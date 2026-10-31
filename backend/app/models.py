"""Which model serves each role, and switching it safely from the Settings page.

A switch is: download (Hugging Face) -> start it next to the current one -> test it on its actual job ->
swap. Nothing changes for the user unless every step succeeds. Switching the embedding model also
rebuilds the search index from the stored page text (no OCR needed).
"""
import asyncio
import io
import re
import shutil
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import runtime
from .config import HOST_CHIP, HOST_RAM_GB, HOST_VRAM_MB, MAC, MODELS_DIR, ROLES
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

    @property
    def ref(self) -> str:
        return f"{self.repo}/{self.file}"

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


_active: dict[str, Spec] = {}


def load():
    with db() as conn:
        for role in ROLES:
            saved = get_setting(conn, f"model.{role}")
            if isinstance(saved, dict):
                spec = Spec(**{**saved, "tags": tuple(saved.get("tags", ())), "flags": tuple(saved.get("flags", ()))})
            else:   # first run: pick for this Mac once, so later catalog updates never switch models silently
                spec = recommended(role)
                set_setting(conn, f"model.{role}", asdict(spec))
            _active[role] = spec


def active(role: str) -> Spec:
    return _active[role]


async def url(role: str) -> str:
    spec = active(role)
    if not spec.downloaded:
        raise RuntimeError(f"the {role} model is not downloaded yet")
    return await runtime.ensure(role, spec.ref, spec.args)


# --- role tests: each runs the model on a tiny version of its real job ---

def _test_image(text: str) -> bytes:
    img = Image.new("RGB", (640, 200), "white")
    ImageDraw.Draw(img).text((40, 70), text, fill="black", font=ImageFont.load_default(size=44))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


async def test(spec: Spec, base: str) -> dict:
    """Returns facts about the model (e.g. embedding size); raises with a readable reason if unusable."""
    from .llm import NO_THINK, image_part
    if spec.role == "embed":
        r = await _http.post(f"{base}/embeddings", json={"input": ["prova di ricerca"]})
        if r.status_code >= 400:
            raise RuntimeError(_reason(r.text))
        return {"dim": len(r.json()["data"][0]["embedding"])}
    if spec.role in ("assistant", "ocr"):
        prompt = spec.prompt or "What is the number written in the image? Reply with the number only."
        content = [image_part(_test_image("Codice 47815")), {"type": "text", "text": prompt}]
    else:
        content = "Reply with the single word: OK"
    r = await _http.post(f"{base}/chat/completions", json={
        "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 256, **NO_THINK})
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
    spec = from_catalog(role, ref) or await resolve_custom(role, ref)
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


def _view(s: Spec) -> dict:
    return {**asdict(s), "ref": s.ref, "downloaded": s.downloaded}


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
        async for pct in runtime.download(spec.repo, spec.files):
            yield {"type": "progress", "step": "pull", "model": spec.name,
                   "percent": min(99, int(100 * (done + spec.size_mb * pct / 100) / total))}
        done += spec.size_mb
    yield {"type": "progress", "step": "pull", "percent": 100}
