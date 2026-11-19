"""Which model serves each role, and switching it safely from the Settings page.

A switch is: download (Hugging Face) -> start it next to the current one -> test it on its actual job ->
swap. Nothing changes for the user unless every step succeeds. Switching the embedding model also
rebuilds the search index from the stored page text (no OCR needed).
"""
import asyncio
import io
import json
import re
import shutil
from pathlib import Path
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, replace

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import engines, runtime
from .config import HOST_CHIP, HOST_RAM_GB, HOST_VRAM_MB, MAC, MODELS_DIR, ROLES, SNAPDRAGON
from .db import db, get_setting, recreate_vec_table, set_setting, vec

_http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5))


@dataclass(frozen=True)
class Spec:
    role: str
    repo: str
    file: str
    mmproj: str = ""            # vision projector, for models that read images
    name: str = ""
    size_mb: int = 0
    tags: tuple[str, ...] = ()
    context: int = 8192
    flags: tuple[str, ...] = ()
    query_prefix: str = ""      # embed only
    doc_prefix: str = ""        # embed only
    prompt: str = ""            # ocr only
    min_ram_gb: int = 8         # Mac RAM needed with the other recommended models loaded
    provider: str = "llama"
    model: str = ""             # server only: which of the server's models answers (LocalAI, llama-server router)
    key: str = ""               # server only: API key
    engine: str = ""            # server only: engines.ENGINES id; how its requests are written
    think_off: str = ""         # server only: the engines.THINK_OFF switch this server was tested with
    caps: tuple[str, ...] = ()  # server only: the engines.CAPS the server passed when probed

    def can(self, cap: str) -> bool:
        """Whether the model has an engines.CAPS capability. The bundled models' are known: they have them all."""
        return self.provider != "server" or cap in self.caps

    @property
    def ref(self) -> str:
        if self.provider == "server":
            return self.repo + (f"#{self.model}" if self.model else "")
        return f"{self.repo}/{self.file}".rstrip("/")

    @property
    def files(self) -> list[str]:
        return [self.file, *([self.mmproj] if self.mmproj else [])]

    @property
    def args(self) -> list[str]:
        # 2 slots: a chat answer and background work (OCR / wiki) can run together. The prompt cache is capped:
        # llama-server's default (8 GB of RAM) would make a 4B model use more memory than the weights themselves.
        # On a Mac all layers go to the GPU (unified memory). A discrete GPU has its own, smaller memory: leave the
        # layer count to llama-server's --fit, which offloads what fits in free VRAM and keeps the rest on the CPU.
        args = ["-m", str(runtime.model_path(self.repo, self.file)), "-c", str(self.context),
                *(["-ngl", "999"] if MAC else []), "-fa", "on", "-np", "2", "--cache-ram", "512", *self.flags]
        if self.mmproj:
            args += ["--mmproj", str(runtime.model_path(self.repo, self.mmproj))]
        if self.role != "assistant" and 0 < HOST_VRAM_MB < sum(s.vram_mb for s in _active.values()):
            # The GPU can't hold all three models, and --fit gives VRAM to whichever server starts first. Keep it for
            # the assistant: on an RTX 3060 (6 GB) chat runs at ~80 tokens/s instead of ~8, ingestion ~35% slower.
            args += ["-ngl", "0", "--no-mmproj-offload"]
        return args

    @property
    def vram_mb(self) -> int:
        """Rough GPU memory in use: weights plus context and compute buffers (measured on CUDA, within ~10%)."""
        return int(self.size_mb * 1.3) + 500

    @property
    def downloaded(self) -> bool:
        if self.provider == "server":
            return True
        if self.provider != "llama":
            from . import npu
            return npu.have(self)
        return runtime.have(self.repo, self.files)


_EMBED_FLAGS = ("--embeddings", "--ubatch-size", "2048")
ROLE_DEFAULTS = {
    "assistant": dict(context=16384, flags=("--jinja",)),
    "ocr": dict(context=8192, prompt="Convert this document page to markdown. Keep tables as markdown tables."),
    "embed": dict(context=4096, flags=_EMBED_FLAGS),
}

# Curated choices, each tested on this llama.cpp build. Per role, best first: the recommended model is the first that
# fits the Mac's RAM. Measured on an M5: 9B uses 7.5 GB at ~22 tokens/s, 4B 4.9 GB at ~38. Sizes include the projector.
CATALOG = [
    Spec("assistant", "unsloth/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf", "mmproj-F16.gguf", "Qwen3.5 9B", 6600, ("quality",), min_ram_gb=24, **ROLE_DEFAULTS["assistant"]),
    Spec("assistant", "unsloth/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf", "mmproj-F16.gguf", "Qwen3.5 4B", 3400, ("balanced",), min_ram_gb=16, **ROLE_DEFAULTS["assistant"]),
    Spec("assistant", "unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf", "mmproj-F16.gguf", "Qwen3.5 2B", 1950, ("light",), **ROLE_DEFAULTS["assistant"]),
    Spec("ocr", "ggml-org/LightOnOCR-2-1B-GGUF", "LightOnOCR-2-1B-Q8_0.gguf", "mmproj-LightOnOCR-2-1B-Q8_0.gguf", "LightOnOCR 2 (1B)", 1100, ("quality",), **ROLE_DEFAULTS["ocr"]),
    Spec("embed", "Qwen/Qwen3-Embedding-0.6B-GGUF", "Qwen3-Embedding-0.6B-Q8_0.gguf", "", "Qwen3 Embedding 0.6B", 640, ("multilingual", "quality"),
         min_ram_gb=16, context=4096, flags=(*_EMBED_FLAGS, "--pooling", "last"),
         query_prefix="Instruct: Given a question, retrieve relevant passages that answer it\nQuery: "),
    Spec("embed", "ggml-org/embeddinggemma-300M-GGUF", "embeddinggemma-300M-Q8_0.gguf", "", "EmbeddingGemma 300M", 330, ("light",),
         context=2048, flags=_EMBED_FLAGS, query_prefix="task: search result | query: ", doc_prefix="title: none | text: "),
]

LLAMA_CATALOG = CATALOG
if SNAPDRAGON:
    CATALOG = [
        Spec("assistant", "qualcomm/Qwen3-4B-Instruct-2507", "", name="Qwen3 4B Instruct · GenieX NPU",
             size_mb=3200, context=4096, tags=("balanced",), provider="geniex-qairt"),
        Spec("ocr", "ggml-org/LightOnOCR-2-1B-GGUF", "LightOnOCR-2-1B-Q8_0.gguf",
             "mmproj-LightOnOCR-2-1B-f16.gguf", name="LightOnOCR-2 1B · GenieX NPU", size_mb=1460,
             context=4096, prompt="Convert this document page to markdown.", tags=("quality",), provider="geniex-gguf"),
        Spec("embed", "qualcomm/Nomic-Embed-Text", "", name="Nomic Embed v1.5 · AI Hub NPU", size_mb=523,
             context=128, tags=("quality",), provider="aihub"),
        Spec("vision", "unsloth/Qwen3-VL-4B-Instruct-GGUF", "Qwen3-VL-4B-Instruct-Q4_0.gguf", "mmproj-F16.gguf",
             name="Qwen3 VL 4B · GenieX NPU", size_mb=3200, context=2048, tags=("quality",), provider="geniex-gguf"),
    ]


def recommended(role: str, ram_gb: int = HOST_RAM_GB) -> Spec:
    """The best model of the role that fits this Mac (unknown RAM counts as 16 GB)."""
    choices = [s for s in CATALOG if s.role == role]
    return next((s for s in choices if s.min_ram_gb <= (ram_gb or 16)), choices[-1])


def free_disk_gb() -> int:
    path = MODELS_DIR
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free // 2**30


def _reason(text: str) -> str:
    if "image input is not supported" in text:
        return "this model can't read images"
    if "unknown model architecture" in text:
        return "this model type is not supported by the bundled llama.cpp"
    return text.strip().splitlines()[0][:200] if text.strip() else "unknown error"


def norm(ref: str) -> str:
    return ref.strip().lower()


def from_catalog(role: str, ref: str) -> Spec | None:
    return next((s for s in CATALOG if s.role == role and norm(s.ref) == norm(ref)), None)


async def resolve_custom(role: str, text: str) -> Spec:
    """'user/Repo-GGUF' or 'user/Repo-GGUF:Q5_K_M' or 'user/Repo-GGUF/file.gguf' -> Spec."""
    text = re.sub(r"^(https?://)?(hf\.co|huggingface\.co)/", "", text.strip()).strip("/")
    if text.endswith(".gguf"):
        repo, file = text.rsplit("/", 1)
        quant = ""
    else:
        repo, _, quant = text.partition(":")
        file = ""
    files = [f for f in await runtime.repo_files(repo) if f["path"].endswith(".gguf")]
    weights = [f for f in files if "mmproj" not in f["path"].lower()]
    projs = [f for f in files if "mmproj" in f["path"].lower()]
    if not file:
        want = (quant or "Q4_K_M").lower()
        match = [f for f in weights if want in f["path"].lower()] or ([] if quant else weights[:1])
        if not match:
            raise RuntimeError(f"no {quant or 'GGUF'} file in {repo}")
        file = min(match, key=lambda f: len(f["path"]))["path"]
    mmproj = ""
    if role in ("assistant", "ocr") and projs:
        mmproj = next((f["path"] for f in projs if "f16" in f["path"].lower()), projs[0]["path"])
    size = sum(f.get("size", 0) for f in files if f["path"] in (file, mmproj)) // 10**6
    defaults = {k: v for k, v in ROLE_DEFAULTS[role].items()}
    return Spec(role, repo, file, mmproj, name=file.removesuffix(".gguf"), size_mb=size, **defaults)


def is_server(ref: str) -> bool:
    return bool(re.match(r"https?://(?!(hf\.co|huggingface\.co)/)", ref.strip(), re.I))


async def external(role: str, text: str) -> Spec:
    """'http://127.0.0.1:8080' -> Spec for an OpenAI-compatible server already running on this computer (Lucebox,
    LocalAI, vLLM, another llama-server). Local Office neither starts nor stops it. A server with several models
    needs one picked as 'http://127.0.0.1:8080#model'; an API key goes before the host: 'http://KEY@127.0.0.1:8080'."""
    if role != "assistant":
        raise RuntimeError("only the assistant can use an external server")
    u = httpx.URL(text.strip())
    model, key = u.fragment, u.password or u.username
    base = str(u.copy_with(fragment=None, username=None, password=None)).rstrip("/")
    base = base if base.endswith("/v1") else base + "/v1"
    if u.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("the server must run on this computer (127.0.0.1 or localhost), so documents never leave it")
    spec = Spec(role, base, "", provider="server", model=model, key=key)
    try:
        r = await _http.get(f"{base}/models", headers=engines.headers(spec))
    except httpx.TransportError:
        raise RuntimeError(f"no server answers at {base}: start it, then try again") from None
    if r.status_code in (401, 403):
        raise RuntimeError(f"the server wants an API key: write the address as {u.scheme}://KEY@{u.netloc.decode()}")
    try:
        ids = [str(m["id"]) for m in r.json()["data"]]
    except (ValueError, LookupError, TypeError):
        ids = []
    if model and ids and model not in ids:
        raise RuntimeError(f"the server has no model {model!r}; it has: {', '.join(ids[:12])}")
    if not model and len(ids) > 1:
        raise RuntimeError(f"the server has several models: add the one to use, e.g. {base.removesuffix('/v1')}#{ids[0]} "
                           f"(it has: {', '.join(ids[:12])})")
    model = model or (ids[0] if ids else "")
    name = Path(model).name   # servers that load one file report its path
    return Spec(role, base, "", name=name or base, provider="server", model=model, key=key, engine="generic")


_active: dict[str, Spec] = {}


def load():
    with db() as conn:
        for role in ROLES:
            saved = get_setting(conn, f"model.{role}")
            if isinstance(saved, dict):
                saved = _migrate(saved)
                spec = Spec(**{**saved, **{k: tuple(saved.get(k, ())) for k in ("tags", "flags", "caps")}})
            else:   # first run: pick for this Mac once, so later catalog updates never switch models silently
                spec = recommended(role)
                set_setting(conn, f"model.{role}", asdict(spec))
            _active[role] = spec


def _migrate(saved: dict) -> dict:
    """Settings saved by older versions: external servers had effort_none (or nothing) instead of an engine, and
    weren't probed. They passed the old image test and were used for everything, so they keep every capability."""
    saved = dict(saved)
    effort_none = saved.pop("effort_none", False)
    if saved.get("provider") == "server" and not saved.get("engine"):
        saved |= {"engine": "generic", "think_off": "reasoning_effort" if effort_none else "chat_template_kwargs"}
    if saved.get("provider") == "server" and "caps" not in saved:
        saved["caps"] = engines.CAPS
    return saved


def active(role: str) -> Spec:
    return _active[role]


async def url(role: str) -> str:
    spec = active(role)
    if not spec.downloaded:
        raise RuntimeError(f"the {role} model is not downloaded yet")
    if spec.provider == "server":
        return spec.repo
    return await runtime.ensure(role, spec.ref, spec.args)


# --- role tests: each runs the model on a tiny version of its real job ---

def _test_image(text: str) -> bytes:
    img = Image.new("RGB", (640, 200), "white")
    ImageDraw.Draw(img).text((40, 70), text, fill="black", font=ImageFont.load_default(size=44))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _test_vision_image() -> bytes:
    image = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(image).rectangle((50, 100, 460, 400), fill="blue")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


async def test(spec: Spec, base: str) -> dict:
    """Returns facts about the model (e.g. embedding size); raises with a readable reason if unusable."""
    from .llm import NO_THINK, image_part
    if spec.role == "embed":
        body, headers = engines.prepare(spec, {"input": ["prova di ricerca"]})
        r = await _http.post(f"{base}/embeddings", json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(_reason(r.text))
        return {"dim": len(r.json()["data"][0]["embedding"])}
    if spec.role in ("assistant", "ocr"):
        prompt = spec.prompt or "What is the number written in the image? Reply with the number only."
        content = [image_part(_test_image("Codice 47815")), {"type": "text", "text": prompt}]
    else:
        content = "Reply with the single word: OK"
    body, headers = engines.prepare(spec, {
        "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 256, **NO_THINK})
    r = await _http.post(f"{base}/chat/completions", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(_reason(r.text))
    out = r.json()["choices"][0]["message"]["content"] or ""
    if spec.role in ("assistant", "ocr") and "47815" not in out.replace(" ", ""):
        raise RuntimeError(f"the model could not read a test image (answered: {out[:80]!r})")
    if not out.strip():
        raise RuntimeError("the model returned an empty answer")
    return {}


# --- search index rebuild (embedding model change) ---

index_lock = asyncio.Lock()


async def reindex(spec: Spec, dim: int) -> AsyncIterator[dict]:
    from .llm import embed
    async with index_lock:
        with db() as conn:
            rows = conn.execute("SELECT c.id, d.name, c.text FROM chunks c JOIN documents d ON d.id = c.doc_id").fetchall()
            recreate_vec_table(conn, dim)
            set_setting(conn, "embed_dim", dim)
        for i in range(0, len(rows), 32):
            batch = rows[i:i + 32]
            vectors = await embed([f"{r['name']}\n{r['text']}" for r in batch])
            with db() as conn:
                conn.executemany("INSERT INTO vec_chunks(rowid, embedding) VALUES (?,?)",
                                 [(r["id"], vec(v)) for r, v in zip(batch, vectors)])
            yield {"type": "progress", "step": "reindex", "percent": int(100 * (i + len(batch)) / len(rows))}


# --- the switch itself ---

switch_lock = asyncio.Lock()


async def switch(role: str, ref: str) -> AsyncIterator[dict]:
    yield {"type": "progress", "step": "pull", "percent": 0}
    spec = from_catalog(role, ref)
    if SNAPDRAGON:
        if spec is None:
            raise ValueError("Choose a validated Snapdragon model from the catalog")
        from . import npu, provision
        await asyncio.to_thread(provision.install_model, spec)
        yield {"type": "progress", "step": "test"}
        if role == "embed":
            vectors = await npu.embed(spec, ["search test"])
            facts = {"dim": len(vectors[0])}
        else:
            from .llm import image_part
            messages = [{"role": "user", "content": "Reply with OK only."}]
            if role in ("ocr", "vision"):
                messages[0]["content"] = [image_part(_test_image("Codice 47815")),
                                          {"type": "text", "text": spec.prompt or "Read the number."}]
            if role == "vision":
                messages[0]["content"] = [image_part(_test_vision_image(), "image/png"),
                                          {"type": "text", "text": "What color is the rectangle? Reply in English."}]
            reply = await npu.complete(spec, messages, max_tokens=256)
            if not reply.get("content") or (role == "ocr" and "47815" not in reply["content"]) or (role == "vision" and "blue" not in reply["content"].lower()):
                raise ValueError("The model did not pass its role test")
            facts = {}
        with db() as conn:
            set_setting(conn, f"model.{role}", asdict(spec))
        _active[role] = spec
        if role == "embed":
            async for ev in reindex(spec, facts["dim"]):
                yield ev
        yield {"type": "switched", "role": role, "model": asdict(spec)}
        return
    if is_server(ref):
        spec = await external(role, ref)
        async for ev in probe(spec):
            if ev["type"] == "probed":
                spec = ev["spec"]
            else:
                yield ev
        with db() as conn:
            set_setting(conn, f"model.{role}", asdict(spec))
        _active[role] = spec
        runtime.release(role)
        yield {"type": "switched", "role": role, "model": _view(spec)}
        return
    spec = spec or await resolve_custom(role, ref)
    async for pct in runtime.download(spec.repo, spec.files):
        yield {"type": "progress", "step": "pull", "percent": pct}
    yield {"type": "progress", "step": "configure"}
    srv = await runtime.start(spec.ref, spec.args, f"{role}-candidate")
    try:
        yield {"type": "progress", "step": "test"}
        facts = await test(spec, srv.url)
    except Exception:
        runtime.stop(srv)
        raise
    # Saved before any rebuild: the model is proven to work; an interrupted rebuild is redone by switching again.
    with db() as conn:
        set_setting(conn, f"model.{role}", asdict(spec))
    _active[role] = spec
    runtime.adopt(role, srv)
    if role == "embed":
        async for ev in reindex(spec, facts["dim"]):
            yield ev
    yield {"type": "switched", "role": role, "model": asdict(spec)}


# --- external servers: what can this one do ---

PROBE_BUDGET = 240   # seconds for the optional probes together; one that doesn't fit counts as missing (#20)
_PROBE_SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
_PROBE_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Current weather in a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}


async def probe(spec: Spec) -> AsyncIterator[dict]:
    """Runs a tiny request per engines.CAPS on a server, yielding a progress event before each, then
    {"type": "probed", "spec": spec} with caps, think_off and context filled in. Raises with a readable reason if
    the server can't chat or stream; the other capabilities are optional and just left out of caps."""
    yield {"type": "progress", "step": "probe", "cap": "chat"}
    spec, thinks = await _probe_chat(spec)
    yield {"type": "progress", "step": "probe", "cap": "stream"}
    await _probe_stream(spec)
    caps = ["chat", "stream", *([] if thinks else ["think_off"])]
    deadline = asyncio.get_running_loop().time() + PROBE_BUDGET
    for cap, check in (("vision", _probe_vision), ("json_schema", _probe_json_schema), ("tools", _probe_tools)):
        yield {"type": "progress", "step": "probe", "cap": cap}
        try:
            async with asyncio.timeout_at(deadline):
                if await check(spec):
                    caps.append(cap)
        except (TimeoutError, RuntimeError, httpx.HTTPError, ValueError, LookupError, TypeError, AttributeError):
            pass
    context = await _probe_context(spec)
    yield {"type": "probed", "spec": replace(spec, caps=tuple(caps), **({"context": context} if context else {}))}


async def _ask(spec: Spec, content, **params) -> dict:
    from .llm import NO_THINK
    body, headers = engines.prepare(spec, {"messages": [{"role": "user", "content": content}], "temperature": 0,
                                           "max_tokens": 256, **NO_THINK, **params})
    r = await _http.post(f"{spec.repo}/chat/completions", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(_reason(r.text))
    return r.json()["choices"][0]["message"]


async def _probe_chat(spec: Spec) -> tuple[Spec, bool]:
    """spec with the first of its engine's thinking switches after which the model answers without thinking, and
    whether it still thinks (no switch works: the first one the server accepts is kept). A server that refuses a
    switch names the field in its error; any other error is the model's and ends the probe."""
    accepted, error = None, None
    for mode in engines.engine(spec).think_off:
        candidate = replace(spec, think_off=mode)
        try:
            msg = await _ask(candidate, "Reply with the single word: OK")
        except RuntimeError as e:
            if mode not in str(e):
                raise
            error = e
            continue
        thinks = bool(msg.get("reasoning_content") or msg.get("reasoning"))
        if (msg.get("content") or "").strip():
            if not thinks:
                return candidate, False
            accepted = accepted or candidate
        elif not thinks:
            error = RuntimeError("the model returned an empty answer")
        else:   # all of max_tokens spent thinking
            error = RuntimeError("the model kept thinking and never answered")
    if accepted:
        return accepted, True
    raise error


async def _probe_stream(spec: Spec) -> None:
    from .llm import NO_THINK
    body, headers = engines.prepare(spec, {"messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                                           "temperature": 0, "max_tokens": 256, "stream": True, **NO_THINK})
    text = ""
    async with _http.stream("POST", f"{spec.repo}/chat/completions", json=body, headers=headers) as r:
        if r.is_error:
            raise RuntimeError(_reason((await r.aread()).decode(errors="replace")))
        async for line in r.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                # servers may end with a usage-only chunk that has no choices
                text += ((json.loads(line[6:]).get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
    if not text.strip():
        raise RuntimeError("the server did not stream an answer")


async def _probe_vision(spec: Spec) -> bool:
    from .llm import image_part
    msg = await _ask(spec, [image_part(_test_image("Codice 47815")),
                            {"type": "text", "text": "What is the number written in the image? Reply with the number only."}])
    return "47815" in (msg.get("content") or "").replace(" ", "")


async def _probe_json_schema(spec: Spec) -> bool:
    msg = await _ask(spec, "What is 2 + 3? Reply in JSON.",
                     response_format={"type": "json_schema", "json_schema": {"name": "probe", "schema": _PROBE_SCHEMA}})
    return json.loads(msg.get("content") or "")["answer"] == 5   # parsed as strictly as wiki and skill routing do


async def _probe_tools(spec: Spec) -> bool:
    msg = await _ask(spec, "What is the weather in Rome right now? Use the tool.", tools=[_PROBE_TOOL])
    return any(c["function"]["name"] == "get_weather" and isinstance(json.loads(c["function"]["arguments"] or "{}"), dict)
               for c in msg.get("tool_calls") or [])


async def _probe_context(spec: Spec) -> int:
    """The server's context size, or 0 if it doesn't say: llama-server's /props, or vLLM's max_model_len."""
    headers = engines.headers(spec)
    try:
        r = await _http.get(f"{spec.repo.removesuffix('/v1')}/props", headers=headers)
        if r.status_code == 200:
            return int(r.json()["default_generation_settings"]["n_ctx"])
    except (httpx.HTTPError, ValueError, LookupError, TypeError):
        pass
    try:
        r = await _http.get(f"{spec.repo}/models", headers=headers)
        entry = next(m for m in r.json()["data"] if not spec.model or m["id"] == spec.model)
        return int(entry.get("max_model_len") or 0)
    except (httpx.HTTPError, ValueError, LookupError, TypeError, StopIteration):
        return 0


def _view(s: Spec) -> dict:
    return {**{k: v for k, v in asdict(s).items() if k != "key"}, "ref": s.ref, "downloaded": s.downloaded}


def overview() -> dict:
    return {"ram_gb": HOST_RAM_GB, "chip": HOST_CHIP, "disk_free_gb": free_disk_gb(),
            **{role: {"active": _view(active(role)), "recommended": norm(recommended(role).ref),
                                             "choices": [_view(s) for s in CATALOG if s.role == role]}
                                      for role in ROLES}}


# --- first run ---

def missing() -> list[Spec]:
    return [active(r) for r in ROLES if not active(r).downloaded]


async def download_missing() -> AsyncIterator[dict]:
    todo = missing()
    total = sum(s.size_mb for s in todo) or 1
    done = 0
    for spec in todo:
        if spec.provider != "llama":
            from .provision import install_model
            yield {"type": "progress", "step": "pull", "model": spec.name, "percent": int(100 * done / total)}
            await asyncio.to_thread(install_model, spec)
            done += spec.size_mb
            continue
        async for pct in runtime.download(spec.repo, spec.files):
            yield {"type": "progress", "step": "pull", "model": spec.name,
                   "percent": min(99, int(100 * (done + spec.size_mb * pct / 100) / total))}
        done += spec.size_mb
    yield {"type": "progress", "step": "pull", "percent": 100}
