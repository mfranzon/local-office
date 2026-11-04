"""Snapdragon assets and isolated workers, with serialized NPU execution.

Keep Nomic resident alongside one generative model, so OCR/indexing and skill
searches do not repeatedly reload weights. Native libraries stay out of the API.
"""
import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .config import LOGS_DIR, MODELS_DIR, NO_WINDOW, REPO

CACHE = Path(os.environ.get("GENIEX_DATADIR", Path.home() / ".cache/geniex")) / "models"
EMBED_DIR = MODELS_DIR / "nomic-embed-text"
_lock = asyncio.Lock()
IDLE_TIMEOUT = 300


@dataclass
class Worker:
    proc: subprocess.Popen
    role: str
    key: str
    last_used: float = 0.0


_workers: dict[str, Worker] = {}


def library_path():
    explicit = os.environ.get("GENIEX_LIB_PATH")
    if explicit:
        return Path(explicit)
    root = REPO / "build/geniex/lib"
    if (root / "geniex.dll").exists():
        return root
    return None  # The regular GenieX wheel can bundle its own libraries.


def model_path(spec):
    if spec.provider == "aihub":
        return EMBED_DIR
    return CACHE / spec.repo / spec.file


def have(spec):
    path = model_path(spec)
    if spec.provider == "aihub":
        from .provision import verified_install, NOMIC_SHA256
        return (verified_install(path / "source.json", NOMIC_SHA256)
                and any(path.rglob("*.onnx")) and any(path.rglob("tokenizer.json")))
    if spec.provider == "geniex-qairt":
        manifest = path / "geniex.json"
        if not manifest.exists():
            return False
        try:
            data = json.loads(manifest.read_text())
            files = list(data.get("ModelFile", {}).values()) + data.get("ExtraFiles", [])
            return (path / "metadata.json").exists() and bool(files) and all(
                f.get("Downloaded") and (path / f["Name"]).is_file()
                and (path / f["Name"]).stat().st_size == f.get("Size", 0) for f in files)
        except (OSError, ValueError, KeyError):
            return False
    return path.is_file() and (not spec.mmproj or (CACHE / spec.repo / spec.mmproj).is_file())


def shutdown(slot=None):
    """Synchronous teardown for process exit; async callers use a thread."""
    for name in list(_workers):
        if slot is not None and name != slot:
            continue
        worker = _workers.pop(name)
        proc = worker.proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdin:
            proc.stdin.close()
        if proc.stdout:
            proc.stdout.close()


async def ashutdown():
    async with _lock:
        await asyncio.to_thread(shutdown)


def running():
    return {w.role: True for w in list(_workers.values()) if w.proc.poll() is None}


def _start(spec):
    slot = "embed" if spec.role == "embed" else "generation"
    key = json.dumps(asdict(spec), sort_keys=True)
    worker = _workers.get(slot)
    if worker and worker.proc.poll() is None and worker.key == key:
        return worker
    shutdown(slot)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GGML_HEXAGON_DEVICES"] = "HTP0"
    env["GENIEX_LOG"] = "warn"
    if lib := library_path():
        env["GENIEX_LIB_PATH"] = str(lib)
        env["GENIEX_PLUGIN_PATH"] = str(lib)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(REPO / "backend")
    with (LOGS_DIR / f"npu-{spec.role}.log").open("a", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-u", "-m", "app.npu_worker"], cwd=REPO / "backend",
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                 text=True, encoding="utf-8", errors="replace", env=env, **NO_WINDOW)
    if sys.platform == "win32":
        from .runtime import _kill_with_app
        _kill_with_app(proc)
    worker = _workers[slot] = Worker(proc, spec.role, key)
    return worker


async def events(spec, request):
    async with _lock:
        if not have(spec):
            raise RuntimeError(f"{spec.name} is not downloaded. Run Setup to install it.")
        starting = asyncio.create_task(asyncio.to_thread(_start, spec))
        try:
            worker = await asyncio.shield(starting)
        except asyncio.CancelledError:
            # Do not release the lock while the startup thread mutates worker slots.
            await starting
            await asyncio.to_thread(shutdown, "embed" if spec.role == "embed" else "generation")
            raise
        proc = worker.proc
        worker.last_used = time.monotonic()
        request_error = None
        try:
            proc.stdin.write(json.dumps({"spec": asdict(spec), **request}) + "\n")
            proc.stdin.flush()
            while True:
                # Reset the idle timer on every event, not a fixed request deadline.
                line = await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), IDLE_TIMEOUT)
                if not line:
                    raise RuntimeError(f"The {spec.role} worker stopped; see npu-{spec.role}.log")
                marker = line.find("LO_EVENT:")
                if marker < 0:
                    continue
                event, _ = json.JSONDecoder().raw_decode(line[marker + len("LO_EVENT:"):])
                if event["type"] == "error":
                    if event.get("fatal", True):
                        raise RuntimeError(event["message"])
                    request_error = event["message"]
                elif event["type"] == "done":
                    break
                elif request_error is None:
                    yield event
            worker.last_used = time.monotonic()
        except BaseException:
            slot = "embed" if spec.role == "embed" else "generation"
            await asyncio.to_thread(shutdown, slot)
            raise
        if request_error is not None:
            # done was consumed: the native worker is safe to reuse after validation errors.
            raise RuntimeError(request_error)


async def complete(spec, messages, **params):
    answer = None
    async for event in events(spec, {"op": "chat", "messages": messages, "params": params}):
        if event["type"] == "message":
            answer = event["message"]
    if answer is None:
        raise RuntimeError("The model returned no response")
    return answer


async def embed(spec, texts, query=False):
    vectors = None
    async for event in events(spec, {"op": "embed", "texts": texts, "query": query}):
        if event["type"] == "embeddings":
            vectors = event["vectors"]
    if vectors is None:
        raise RuntimeError("The embedding model returned no vectors")
    return vectors


async def reap():
    if _lock.locked():
        return
    async with _lock:
        for slot, worker in list(_workers.items()):
            if time.monotonic() - worker.last_used > 180:
                await asyncio.to_thread(shutdown, slot)
