"""Native inference subprocess. stdout is a prefixed JSONL protocol."""
import base64
import copy
import json
import logging
import math
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


class RequestError(ValueError):
    """A completed request failed validation; its loaded model can be reused."""


def emit(kind, **data):
    print("LO_EVENT:" + json.dumps({"type": kind, **data}, ensure_ascii=True), flush=True)


def parse_message(text, tools=None):
    """Translate model-generated calls; only expose functions actually offered."""
    names = {t["function"]["name"] for t in tools or []}
    if not names:
        return {"role": "assistant", "content": text}
    calls = []
    tagged = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.S)
    if not tagged and names:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
                return {"role": "assistant", "content": parsed["answer"]}
            if isinstance(parsed, dict) and (("name" in parsed and "arguments" in parsed) or "tool_calls" in parsed):
                tagged = [json.dumps(c) for c in parsed.get("tool_calls", [parsed])]
        except ValueError:
            pass
    for raw in tagged:
        call = json.loads(raw)
        if not isinstance(call, dict):
            raise ValueError("Tool calls must be JSON objects")
        fn = call.get("function", call)
        if not isinstance(fn, dict):
            raise ValueError("Invalid function object")
        if fn.get("name") not in names:
            raise ValueError("The model requested an unknown tool")
        arguments = fn.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        calls.append({"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                      "function": {"name": fn["name"], "arguments": json.dumps(arguments)}})
    return {"role": "assistant", "content": "" if calls else text, **({"tool_calls": calls} if calls else {})}


def load(spec):
    global _model, _tokenizer, _spec, _session
    from .npu import CACHE, EMBED_DIR, model_path
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
        _spec = spec
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
    _spec = spec


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
            emit("progress")
        vector = np.mean(pooled, axis=0)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        if vector.size != 512 or not np.isfinite(vector).all():
            raise ValueError("Nomic returned an invalid embedding")
        vectors.append(vector.tolist())
    emit("embeddings", vectors=vectors)


def fit_prompt(messages, tools, requested, image_tokens=0):
    """Protect system/protocol text. Budget images and trim prior tool results first."""
    working = copy.deepcopy(messages)
    for _ in range(40):
        _model.reset()  # QAIRT templates retain conversation state otherwise.
        prompt = _model.tokenizer.apply_chat_template(working, tools=tools,
                    enable_thinking=False if _model.supports_thinking else None)
        # SDK 0.10 does not expose GGUF tokenization. UTF-8 bytes are a conservative
        # upper bound; QAIRT uses the exact local tokenizer.
        count = len(_tokenizer.encode(prompt).ids) if _tokenizer else len(prompt.encode("utf-8"))
        available = _spec["context"] - count - image_tokens - 64
        if available >= min(requested, 768):
            return prompt, min(requested, available)
        candidates = [(i, m) for i, m in enumerate(working[:-1])
                      if m["role"] != "system" and isinstance(m.get("content"), str)
                      and len(m["content"]) > 600 and not m.get("tool_calls")]
        if not candidates:
            if available >= 32:
                return prompt, min(requested, available)
            raise RequestError("Conversation exceeds the model context. Shorten the request or start a new chat.")
        _, target = min(candidates, key=lambda pair: (pair[1]["role"] != "tool", pair[0]))
        text = target["content"]
        target["content"] = text[:int(len(text)*.45)] + "\n[excerpt shortened]\n" + text[-int(len(text)*.25):]
    raise RequestError("Conversation exceeds the model context")


def generate(prompt, budget, params, images, stream_response):
    _model.reset()
    kwargs = {"max_new_tokens": budget, "temperature": params.get("temperature", 0),
              "seed": 42, "repetition_penalty": params.get("repetition_penalty", 1.0 if _spec.get("role") == "ocr" else 1.05)}
    if images:
        kwargs["images"] = images
    # Token progress also resets the parent's idle timeout for blocking OCR/JSON.
    stream = _model.generate(prompt, stream=True, **kwargs)
    for token in stream:
        if stream_response:
            emit("token", text=token)
        else:
            emit("progress")
    output = stream.output
    if output is None:
        raise RequestError("The model returned no generation result")
    if output.profile.stop_reason in ("length", "context_length"):
        if _spec.get("role") == "ocr":
            raise RequestError("OCR output was truncated by the model's token/context limit. Split the page into smaller images and retry; partial text was not accepted.")
        raise RequestError("The response reached its token/context limit before completion. Shorten the request or ask for a shorter answer.")
    return output


def chat(request):
    params = request.get("params", {})
    requested = int(params.get("max_tokens", 1024))
    if requested < 1:
        raise RequestError("max_tokens must be positive")
    messages = copy.deepcopy(request["messages"])
    schema = (params.get("response_format") or {}).get("json_schema", {}).get("schema")
    if schema:
        instruction = "\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(schema)
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    tools = request.get("params", {}).get("tools")
    template_tools = tools
    if tools and _spec.get("provider") == "geniex-qairt":
        instruction = ("\nYou are executing a tool workflow. Respond ONLY with one JSON object. "
            '{"name":"function_name","arguments":{...}} calls a tool. '
            '{"answer":"your final answer"} finishes the task. '
            "Never fabricate documents or pretend a tool ran. Read source documents when the task depends on them. "
            "Call create_file when the user or applicable skill step requests an actual downloadable file. "
            "Available tools:\n" + json.dumps(tools))
        if messages[0]["role"] == "system":
            messages[0]["content"] += instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        template_tools = None
    with tempfile.TemporaryDirectory(prefix="local-office-image-") as temp:
        images = []
        image_tokens = 0
        for msg in messages:
            if not isinstance(msg.get("content"), list):
                continue
            for part in msg["content"]:
                if part.get("type") != "image_url":
                    continue
                uri = part["image_url"]["url"]
                if not uri.startswith("data:image/") or ";base64," not in uri:
                    raise RequestError("Only inline local images are supported")
                image = Path(temp) / f"{len(images)}.jpg"
                image.write_bytes(base64.b64decode(uri.split(",", 1)[1], validate=True))
                from PIL import Image
                with Image.open(image) as source:
                    if _spec["role"] == "vision":
                        scaled = source.convert("RGB")
                        scaled.thumbnail((840, 840))
                        scaled.save(image, "JPEG", quality=95)
                        width, height = scaled.size
                    else:
                        width, height = source.size
                # Both shipped projectors merge patches. 28px is a conservative
                # bound (LightOn: 14px patches / 2; Qwen-VL: 16px / 2), plus markers.
                image_tokens += math.ceil(width / 28) * math.ceil(height / 28) + 64
                images.append(str(image))
        streaming = bool(request.get("stream") and not schema and not tools)
        for attempt in range(3):
            prompt, budget = fit_prompt(messages, template_tools, requested, image_tokens)
            output = generate(prompt, budget, params, images, streaming)
            text = output.text.strip()
            try:
                if schema:
                    from jsonschema import validate, ValidationError
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
                    parsed = json.loads(raw)
                    try:
                        validate(parsed, schema)
                    except ValidationError as error:
                        raise ValueError(error.message[:300]) from error
                    text = json.dumps(parsed, ensure_ascii=False)
                if not tools and not schema and text.startswith("<tool_call>"):
                    raise ValueError("Tools are unavailable on this turn; answer directly without tool calls")
                message = parse_message(text, tools)
                message["finish_reason"] = output.profile.stop_reason
                message["max_output_tokens"] = budget
                emit("message", message=message)
                return
            except (ValueError, TypeError, KeyError) as error:
                if attempt == 2:
                    raise RequestError(f"The model could not produce valid JSON/tool calls: {error}") from error
                # Separate turns work for multimodal history too; never concatenate
                # a string onto a list of image/text parts. Refit on every retry.
                messages.append({"role": "assistant", "content": text})
                instruction = ("Return valid JSON using only the offered tools/schema. " if tools or schema
                               else "Answer directly in plain text without tool calls. ")
                messages.append({"role": "user", "content":
                    "Correct the previous response. " + instruction + str(error)[:300]})


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
            fatal = _spec is None or not isinstance(error, ValueError)
            if not fatal and _model is not None:
                try:
                    _model.reset()
                except Exception:
                    fatal = True
            emit("error", message=str(error)[:500], fatal=fatal)
        finally:
            emit("done")


if __name__ == "__main__":
    main()
