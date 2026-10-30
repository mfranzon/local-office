"""Runs the models: downloads GGUF files from Hugging Face and supervises one llama-server per role.

A server starts on the first request for its role and stops after IDLE_SECONDS without use, so memory
is only taken while a model is actually needed.
"""
import asyncio
import contextlib
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import LLAMA_SERVER, LOGS_DIR, MODELS_DIR

IDLE_SECONDS = 15 * 60
START_TIMEOUT = 180
HF = "https://huggingface.co"
_http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=True)


# --- files ---

def model_path(repo: str, file: str) -> Path:
    return MODELS_DIR / repo / file


def have(repo: str, files: list[str]) -> bool:
    return all(model_path(repo, f).is_file() for f in files)


async def repo_files(repo: str) -> list[dict]:
    r = await _http.get(f"{HF}/api/models/{repo}/tree/main")
    if r.status_code in (401, 404):   # Hugging Face answers 401 for repos that don't exist or are private
        raise RuntimeError("model not found on Hugging Face (check the name, or the repo may be private)")
    r.raise_for_status()
    return [f for f in r.json() if f["type"] == "file"]


async def download(repo: str, files: list[str]) -> AsyncIterator[int]:
    """Downloads missing files (resuming partial ones); yields overall percent."""
    missing = [f for f in files if not model_path(repo, f).is_file()]
    if not missing:
        yield 100
        return
    sizes = {f["path"]: f.get("size", 0) for f in await repo_files(repo)}
    unknown = [f for f in missing if f not in sizes]
    if unknown:
        raise RuntimeError(f"file not found in {repo}: {unknown[0]}")
    total = sum(sizes[f] for f in missing) or 1
    done, last = 0, -1
    for name in missing:
        dest = model_path(repo, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        async with _http.stream("GET", f"{HF}/{repo}/resolve/main/{name}", headers=headers,
                                timeout=httpx.Timeout(None, connect=10)) as r:
            if r.status_code not in (200, 206):
                raise RuntimeError(f"download failed ({r.status_code}) for {name}")
            if r.status_code == 200:
                offset = 0       # server ignored the range: start over
            with part.open("ab" if offset else "wb") as out:
                done += offset
                async for chunk in r.aiter_bytes(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    pct = min(99, int(100 * done / total))
                    if pct != last:
                        last = pct
                        yield pct
        if part.stat().st_size != sizes[name]:
            raise RuntimeError(f"incomplete download for {name}, try again")
        part.rename(dest)
    yield 100


# --- servers ---

@dataclass
class Server:
    key: str                     # identifies the model + runtime config
    args: list[str]
    port: int = 0
    proc: subprocess.Popen | None = None
    last_used: float = field(default_factory=time.monotonic)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start(key: str, args: list[str], log_name: str) -> Server:
    srv = Server(key, args, port=_free_port())
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOGS_DIR / f"{log_name}.log").open("w")
    srv.proc = subprocess.Popen([LLAMA_SERVER, *args, "--host", "127.0.0.1", "--port", str(srv.port)],
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + START_TIMEOUT
    async with httpx.AsyncClient(timeout=2) as client:
        while time.monotonic() < deadline:
            if srv.proc.poll() is not None:
                tail = (LOGS_DIR / f"{log_name}.log").read_text(errors="replace").strip().splitlines()[-3:]
                raise RuntimeError("the model failed to start: " + " | ".join(tail))
            with contextlib.suppress(httpx.HTTPError):
                if (await client.get(f"http://127.0.0.1:{srv.port}/health")).status_code == 200:
                    return srv
            await asyncio.sleep(0.5)
    stop(srv)
    raise RuntimeError("the model took too long to start")


def stop(srv: Server | None):
    if srv and srv.proc and srv.proc.poll() is None:
        srv.proc.terminate()
        try:
            srv.proc.wait(10)
        except subprocess.TimeoutExpired:
            srv.proc.kill()


_servers: dict[str, Server] = {}
_locks: dict[str, asyncio.Lock] = {}


async def ensure(role: str, key: str, args: list[str]) -> str:
    """Returns the URL of the role's server, starting (or replacing) it when needed."""
    async with _locks.setdefault(role, asyncio.Lock()):
        srv = _servers.get(role)
        if srv and (srv.key != key or srv.proc.poll() is not None):
            stop(srv)
            srv = None
        if srv is None:
            srv = _servers[role] = await start(key, args, role)
        srv.last_used = time.monotonic()
        return srv.url


def adopt(role: str, srv: Server):
    """Makes an already started (and tested) server the role's server."""
    old = _servers.get(role)
    _servers[role] = srv
    if old is not srv:
        stop(old)


def running() -> dict[str, bool]:
    return {role: s.proc.poll() is None for role, s in _servers.items()}


async def reaper():
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for role, srv in list(_servers.items()):
            if now - srv.last_used > IDLE_SECONDS:
                stop(_servers.pop(role))


def shutdown():
    for srv in _servers.values():
        stop(srv)
    _servers.clear()
