"""Install pinned Qualcomm assets; no document data is sent to download services."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import NamedTuple
import urllib.request
import zipfile

from .config import REPO, NO_WINDOW


class Asset(NamedTuple):
    url: str
    sha256: str
    file: str


# Reviewed pins: change a URL and its digest together. The runtime digest is the
# GitHub release asset digest; the Nomic digest covers the AI Hub v0.65.0 archive.
ASSETS = {
    "runtime": Asset("https://github.com/qualcomm/GenieX/releases/download/v0.10.0/geniex-sdk-windows-arm64-v0.10.0.zip",
                     "410dc39e79a6d0b7e8991bd4267285f7c73de7c46511a1fe0549b83dc4a26e4e",
                     "geniex-sdk-windows-arm64-v0.10.0.zip"),
    "nomic": Asset("https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/nomic_embed_text/releases/v0.65.0/nomic_embed_text-onnx-float.zip",
                   "67d7efd4841602a4a4d51339c71d8cfd6bff12da69fafa4b086836092458b1f0",
                   "nomic-embed-text-v0.65.0.zip"),
}
_install_lock = threading.Lock()


def fetch(name):
    """Download a pinned asset; a cached archive that fails the check is discarded."""
    asset = ASSETS[name]
    archive = download(asset.url, REPO / "build/downloads" / asset.file)
    with archive.open("rb") as file:
        actual = hashlib.file_digest(file, "sha256").hexdigest()
    if actual != asset.sha256:
        archive.unlink()
        raise RuntimeError(f"SHA256 mismatch for {asset.file}; the download was discarded, retry setup")
    return archive


def installed(directory, name):
    """True when the directory was populated from the currently pinned asset."""
    try:
        return json.loads((Path(directory) / "source.json").read_text()).get("sha256") == ASSETS[name].sha256
    except (OSError, ValueError, AttributeError):
        return False


def mark_installed(directory, name):
    asset = ASSETS[name]
    (Path(directory) / "source.json").write_text(json.dumps({"source": asset.url, "sha256": asset.sha256}))


def download(url, dest):
    """Bounded parallel range transfer with resumable, independently checked chunks."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        size = int(response.headers["Content-Length"])
        etag = response.headers.get("ETag", "")
    if dest.exists() and dest.stat().st_size == size:
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    state = dest.with_suffix(dest.suffix + ".progress.json")
    chunk_size = 1024 * 1024
    total = (size + chunk_size - 1) // chunk_size
    done = set()
    if state.exists() and part.exists():
        try:
            previous = json.loads(state.read_text())
            completed = previous.get("done", [])
            if (previous.get("etag") == etag and previous.get("size") == size
                    and isinstance(completed, list)
                    and all(type(i) is int and 0 <= i < total for i in completed)):
                done = set(completed)
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # An interrupted checkpoint is safely replaced by a fresh download.
    if not part.exists() or part.stat().st_size != size:
        with part.open("wb") as file:
            file.truncate(size)
        done = set()
    lock = threading.Lock()
    last_progress = [0.0]
    cancelled = threading.Event()

    def chunk(index):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        for attempt in range(4):
            if cancelled.is_set():
                return
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=45) as response:
                    if response.status != 206:
                        raise RuntimeError("The model host did not honor a range request")
                    data = response.read()
                if cancelled.is_set():
                    return
                if len(data) != end - start + 1:
                    raise RuntimeError("Incomplete asset chunk")
                with part.open("r+b") as file:
                    file.seek(start)
                    file.write(data)
                with lock:
                    done.add(index)
                    checkpoint = state.with_suffix(state.suffix + ".tmp")
                    checkpoint.write_text(json.dumps({"size": size, "etag": etag, "done": sorted(done)}))
                    checkpoint.replace(state)
                    if time.monotonic() - last_progress[0] > 10:
                        print(f"{dest.name}: {len(done) * 100 // total}%", flush=True)
                        last_progress[0] = time.monotonic()
                return
            except Exception:
                if attempt == 3 or cancelled.is_set():
                    raise
                if cancelled.wait(attempt + 1):
                    return
    pool = ThreadPoolExecutor(max_workers=48)
    futures = [pool.submit(chunk, i) for i in range(total) if i not in done]
    try:
        for future in as_completed(futures):
            future.result()
    except BaseException:
        cancelled.set()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    part.replace(dest)
    state.unlink(missing_ok=True)
    return dest


def extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as zipped:
        # CRC verification also detects damaged resumed chunks before activation.
        if bad := zipped.testzip():
            raise RuntimeError(f"Corrupt model archive: {bad}")
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Unsafe archive member")
        zipped.extractall(destination)


def install_runtime():
    target = REPO / "build/geniex/lib"
    if (target / "geniex.dll").is_file() and installed(target.parent, "runtime"):
        return
    archive = fetch("runtime")
    unpack = REPO / "build/geniex-sdk"
    extract(archive, unpack)
    lib = next(unpack.rglob("geniex.dll")).parent
    shutil.copytree(lib, target, dirs_exist_ok=True)
    mark_installed(target.parent, "runtime")


def install_model(spec):
    from .npu import CACHE, EMBED_DIR, have
    if have(spec):
        return
    with _install_lock:
        if have(spec):
            return
        if spec.provider == "aihub":
            extract(fetch("nomic"), EMBED_DIR)
            if not any(EMBED_DIR.rglob("tokenizer.json")):
                from huggingface_hub import hf_hub_download
                shutil.copy(hf_hub_download("google-bert/bert-base-uncased", "tokenizer.json"), EMBED_DIR / "tokenizer.json")
            mark_installed(EMBED_DIR, "nomic")
        else:
            # A subprocess keeps native SDK initialization out of the API server.
            env = os.environ.copy()
            env["GENIEX_LIB_PATH"] = str(REPO / "build/geniex/lib")
            env["GENIEX_PLUGIN_PATH"] = env["GENIEX_LIB_PATH"]
            name = spec.repo + (":Q8_0" if spec.role == "ocr" else ":Q4_0" if spec.provider == "geniex-gguf" else "")
            cmd = [sys.executable, "-m", "geniex.cli", "pull", name]
            subprocess.run(cmd, env=env, check=True, stdin=subprocess.DEVNULL, **NO_WINDOW)
            if spec.mmproj and not (CACHE / spec.repo / spec.mmproj).exists():
                from huggingface_hub import hf_hub_download
                shutil.copyfile(hf_hub_download(spec.repo, spec.mmproj), CACHE / spec.repo / spec.mmproj)
        if not have(spec):
            raise RuntimeError(f"Downloaded files for {spec.name} are incomplete")


if __name__ == "__main__":
    from .models import CATALOG
    install_runtime()
    roles = sys.argv[1:] or [s.role for s in CATALOG]
    for spec in CATALOG:
        if spec.role in roles:
            print(f"Preparing {spec.name}", flush=True)
            install_model(spec)
