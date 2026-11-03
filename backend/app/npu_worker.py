"""Native inference subprocess. stdout is a prefixed JSONL protocol."""
import base64
import copy
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import traceback
import uuid

_model = None
_tokenizer = None
_spec = None
_session = None


def emit(kind, **data):
    print("LO_EVENT:" + json.dumps({"type": kind, **data}, ensure_ascii=True), flush=True)


def parse_message(text, tools=None):
    """Translate model-generated calls; only expose functions actually offered."""
    names = {t["function"]["name"] for t in tools or []}
    calls = []
    tagged = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.S)
    if not tagged and names:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
                return {"role": "assistant", "content": parsed["answer"]}
            if isinstance(parsed, dict) and ("name" in parsed or "tool_calls" in parsed):
                tagged = [json.dumps(c) for c in parsed.get("tool_calls", [parsed])]
        except ValueError:
            pass
    for raw in tagged:
        call = json.loads(raw)
        fn = call.get("function", call)
        if fn.get("name") not in names:
            raise ValueError("The model requested an unknown tool")
        arguments = fn.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        calls.append({"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                      "function": {"name": fn["name"], "arguments": json.dumps(arguments)}})
    return {"role": "assistant", "content": "" if calls else text, **({"tool_calls": calls} if calls else {})}


def load(spec):
    global _model, _tokenizer, _spec, _session
    from .npu import CACHE, EMBED_DIR, model_path
    _spec = spec
    from types import SimpleNamespace
    path = model_path(SimpleNamespace(**spec))
    if spec["provider"] == "aihub":
        import numpy as np
        import onnxruntime as ort
        import onnxruntime_qnn as qnn
        from tokenizers import Tokenizer
        if "QNNExecutionProvider" not in ort.get_available_providers():
            ort.register_execution_provider_library("QNNExecutionProvider", qnn.get_library_path())
        options = ort.SessionOptions()
        options.log_severity_level = 2
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        model_file = next(EMBED_DIR.rglob("*.onnx"))
        devices = [d for d in ort.get_ep_devices() if d.ep_name == "QNNExecutionProvider"
                   and d.device.type == ort.OrtHardwareDeviceType.NPU]
        if not devices:
            raise RuntimeError("No Qualcomm NPU device is available to ONNX Runtime")
        options.add_provider_for_devices(devices, {"backend_path": qnn.get_qnn_htp_path(),
                                        "htp_performance_mode": "burst", "enable_htp_fp16_precision": "1"})
        _session = ort.InferenceSession(str(model_file), sess_options=options)
        _session.disable_fallback()
        _tokenizer = Tokenizer.from_file(str(next(EMBED_DIR.rglob("tokenizer.json"))))
        input_spec = _session.get_inputs()[0]
        _tokenizer.enable_padding(length=input_spec.shape[1], pad_id=0, pad_token="[PAD]")
        _tokenizer.enable_truncation(max_length=input_spec.shape[1], stride=16)
        logging.info("AI Hub Nomic ready: providers=%s inputs=%s", _session.get_providers(),
                     [(i.name, i.shape, i.type) for i in _session.get_inputs()])
        return
    from geniex import AutoModelForCausalLM, AutoModelForVision2Seq
    kwargs = {"device_map": "qairt:npu" if spec["provider"] == "geniex-qairt" else "llama_cpp:npu"}
    if spec["provider"] == "geniex-gguf":
        kwargs.update(n_ctx=spec["context"], mmproj_path=str(CACHE / spec["repo"] / spec["mmproj"]),
                      vit_device_id="HTP0")
    cls = AutoModelForVision2Seq if spec["role"] in ("vision", "ocr") else AutoModelForCausalLM
    _model = cls.from_pretrained(str(path), **kwargs)
    token_file = path / "tokenizer.json" if path.is_dir() else None
    if token_file and token_file.exists():
        from tokenizers import Tokenizer
        _tokenizer = Tokenizer.from_file(str(token_file))
    logging.info("GenieX %s ready on NPU: %s", spec["role"], spec["name"])


def embeddings(texts, query):
    import numpy as np
    prefix = "search_query: " if query else "search_document: "
    vectors = []
    for text in texts:
        # The AI Hub export has a fixed 128-token input. Cover the entire chunk
        # with overlapping windows instead of silently discarding its tail.
        encoded = _tokenizer.encode(prefix + text)
        windows = [encoded, *encoded.overflowing]
        pooled = []
        for window in windows:
            inputs = {}
            for inp in _session.get_inputs():
                values = window.attention_mask if "mask" in inp.name else window.ids
                inputs[inp.name] = np.asarray([values], dtype=np.int64 if "int64" in inp.type else np.int32)
            pooled.append(np.asarray(_session.run(None, inputs)[0], dtype=np.float32).reshape(-1))
        vector = np.mean(pooled, axis=0)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        if vector.size != 512 or not np.isfinite(vector).all():
            raise ValueError("Nomic returned an invalid embedding")
        vectors.append(vector.tolist())
    emit("embeddings", vectors=vectors)


def chat(request):
    params = request.get("params", {})
    messages = copy.deepcopy(request["messages"])
    schema = (params.get("response_format") or {}).get("json_schema", {}).get("schema")
    if schema:
        instruction = "\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(schema)
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    tools = params.get("tools")
    template_tools = tools
    if tools and _spec.get("provider") == "geniex-qairt":
        # QAIRT bundles differ in native tool-template support. Use a small,
        # explicit JSON action protocol rather than accepting narrated actions.
        action_instruction = ("\nYou are executing a tool workflow. Respond ONLY with one JSON object. "
            'To call a tool: {"name":"function_name","arguments":{...}}. '
            'After reading actual tool results, to finish: {"answer":"your final answer"}. '
            "Never fabricate documents or pretend a tool ran. Read documents before answering. "
            "To create a requested file you MUST call create_file. Available tools:\n" + json.dumps(tools))
        if messages[0]["role"] == "system":
            messages[0]["content"] += action_instruction
        else:
            messages.insert(0, {"role": "system", "content": action_instruction})
        template_tools = None
    with tempfile.TemporaryDirectory(prefix="local-office-image-") as temp:
        images = []
        for msg in messages:
            if not isinstance(msg.get("content"), list):
                continue
            for part in msg["content"]:
                if part.get("type") == "image_url":
                    uri = part["image_url"]["url"]
                    if not uri.startswith("data:image/") or ";base64," not in uri:
                        raise ValueError("Only inline local images are supported")
                    image = Path(temp) / f"{len(images)}.jpg"
                    image.write_bytes(base64.b64decode(uri.split(",", 1)[1], validate=True))
                    if _spec["role"] == "vision":
                        from PIL import Image
                        with Image.open(image) as source:
                            scaled = source.convert("RGB")
                            scaled.thumbnail((840, 840))
                            scaled.save(image, "JPEG", quality=95)
                    images.append(str(image))
        # Ensure the final template plus output fits the compiled QAIRT context.
        # OCR needs ingestion's full requested budget, not the assistant's cap.
        limit = _spec["context"] - 32 if _spec.get("role") == "ocr" else 1536
        max_tokens = min(params.get("max_tokens", 1024), limit)
        # QAIRT's template is stateful: reset BEFORE formatting so system
        # instructions and tools are included on every independent request.
        _model.reset()
        for _ in range(30):
            prompt = _model.tokenizer.apply_chat_template(messages, tools=template_tools,
                        enable_thinking=False if _model.supports_thinking else None)
            count = len(_tokenizer.encode(prompt).ids) if _tokenizer else len(prompt) // 3
            reserve = min(max_tokens, 768)
            if count < _spec["context"] - reserve - 64 or images:
                break
            # Shorten the largest text while retaining instructions, recent
            # tool history, and the beginning/end of document context.
            candidates = [m for m in messages if isinstance(m.get("content"), str) and len(m["content"]) > 600]
            if not candidates:
                raise ValueError("Conversation exceeds this model's context. Start a new chat.")
            target = max(candidates, key=lambda m: len(m["content"]))
            text = target["content"]
            target["content"] = text[:int(len(text)*.45)] + "\n[excerpt shortened]\n" + text[-int(len(text)*.3):]
        if not images:
            max_tokens = min(max_tokens, _spec["context"] - count - 32)
        if max_tokens < 32:
            raise ValueError("Conversation exceeds the model context")
        _model.reset()
        kwargs = {"max_new_tokens": max_tokens, "temperature": params.get("temperature", 0),
                  "seed": 42, "repetition_penalty": 1.05}
        if images:
            kwargs["images"] = images
        if request.get("stream") and not schema and not tools:
            stream = _model.generate(prompt, stream=True, **kwargs)
            for token in stream:
                emit("token", text=token)
            output = stream.output
        else:
            output = _model.generate(prompt, **kwargs)
        if _spec.get("role") == "ocr" and output.profile.stop_reason in ("length", "context_length"):
            raise RuntimeError("OCR output was truncated by the model's token/context limit. Split the page into smaller images and retry; partial text was not accepted.")
        if output.profile.stop_reason == "context_length":
            raise RuntimeError("The model context is full; shorten the request or start a new chat")
        text = output.text.strip()
        if schema:
            from jsonschema import validate, ValidationError
            for attempt in range(3):
                try:
                    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
                    parsed = json.loads(text)
                    validate(parsed, schema)
                    text = json.dumps(parsed, ensure_ascii=False)
                    break
                except (ValueError, ValidationError) as error:
                    if attempt == 2:
                        raise
                    location = ".".join(map(str, error.absolute_path)) if isinstance(error, ValidationError) else "JSON syntax"
                    detail = error.message if isinstance(error, ValidationError) else str(error)
                    retry = copy.deepcopy(messages)
                    retry[-1]["content"] += f"\nReturn valid JSON only. Fix the previous validation error at {location}: {detail[:300]}"
                    _model.reset()
                    repaired_prompt = _model.tokenizer.apply_chat_template(retry,
                                      enable_thinking=False if _model.supports_thinking else None)
                    text = _model.generate(repaired_prompt, **kwargs).text.strip()
        emit("message", message=parse_message(text, tools))


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if _spec is None:
                load(request["spec"])
            if request["op"] == "embed":
                embeddings(request["texts"], request.get("query", False))
            else:
                chat(request)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            emit("error", message=str(error)[:500])
        finally:
            emit("done")


if __name__ == "__main__":
    main()
