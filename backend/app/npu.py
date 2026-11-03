"""Snapdragon model assets and one isolated NPU worker at a time.

Serializing roles prevents GenieX/ONNX Runtime from competing for the single
working Hexagon session. Native libraries never load in the UI/API process.
"""
import asyncio
from dataclasses import asdict
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
_proc = None
_role = None
_key = None
_last_used = 0.0


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
        return any(path.rglob("*.onnx")) and any(path.rglob("tokenizer.json"))
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


def shutdown():
    global _proc, _role, _key
    if _proc is not None:
        if _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
                _proc.wait()
        _proc = None
    _role = _key = None


def running():
    return {_role: True} if _proc and _proc.poll() is None else {}


def _start(spec):
    global _proc, _role, _key
    key = json.dumps(asdict(spec), sort_keys=True)
    if _proc and _proc.poll() is None and _key == key:
        return
    shutdown()
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
        _proc = subprocess.Popen([sys.executable, "-u", "-m", "app.npu_worker"], cwd=REPO / "backend",
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                 text=True, encoding="utf-8", errors="replace", env=env, **NO_WINDOW)
    if sys.platform == "win32":
        from .runtime import _kill_with_app
        _kill_with_app(_proc)
    _role, _key = spec.role, key


async def events(spec, request):
    global _last_used
    async with _lock:
        if not have(spec):
            raise RuntimeError(f"{spec.name} is not downloaded. Run Setup to install it.")
        _start(spec)
        _last_used = time.monotonic()
        try:
            _proc.stdin.write(json.dumps({"spec": asdict(spec), **request}) + "\n")
            _proc.stdin.flush()
            async with asyncio.timeout(300):
                while True:
                    line = await asyncio.to_thread(_proc.stdout.readline)
                    if not line:
                        raise RuntimeError(f"The {spec.role} worker stopped; see npu-{spec.role}.log")
                    if not line.startswith("LO_EVENT:"):
                        continue
                    event = json.loads(line[len("LO_EVENT:"):])
                    if event["type"] == "error":
                        raise RuntimeError(event["message"])
                    if event["type"] == "done":
                        break
                    yield event
            _last_used = time.monotonic()
        except BaseException:
            shutdown()  # Cancellation must stop native generation before releasing the lock.
            raise


async def complete(spec, messages, **params):
    answer = None
    async for event in events(spec, {"op": "chat", "messages": messages, "params": params}):
        if event["type"] == "message":
            answer = event["message"]
    if answer is None:
        raise RuntimeError("The model returned no response")
    return answer


async def embed(spec, texts, query=False):
    async for event in events(spec, {"op": "embed", "texts": texts, "query": query}):
        if event["type"] == "embeddings":
            return event["vectors"]
    raise RuntimeError("The embedding model returned no vectors")


async def reap():
    if not _lock.locked() and time.monotonic() - _last_used > 180:
        shutdown()
