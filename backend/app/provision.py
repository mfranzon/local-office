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
import urllib.request
import zipfile

from .config import REPO, NO_WINDOW

NOMIC_URL = "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/nomic_embed_text/releases/v0.65.0/nomic_embed_text-onnx-float.zip"
_install_lock = threading.Lock()


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
    done = set()
    if state.exists() and part.exists():
        previous = json.loads(state.read_text())
        if previous.get("etag") == etag and previous.get("size") == size:
            done = set(previous.get("done", []))
    if not part.exists() or part.stat().st_size != size:
        with part.open("wb") as file:
            file.truncate(size)
        done = set()
    lock = threading.Lock()
    total = (size + chunk_size - 1) // chunk_size
    last_progress = [0.0]

    def chunk(index):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=45) as response:
                    if response.status != 206:
                        raise RuntimeError("The model host did not honor a range request")
                    data = response.read()
                if len(data) != end - start + 1:
                    raise RuntimeError("Incomplete asset chunk")
                with part.open("r+b") as file:
                    file.seek(start)
                    file.write(data)
                with lock:
                    done.add(index)
                    state.write_text(json.dumps({"size": size, "etag": etag, "done": sorted(done)}))
                    if time.monotonic() - last_progress[0] > 10:
                        print(f"{dest.name}: {len(done) * 100 // total}%", flush=True)
                        last_progress[0] = time.monotonic()
                return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
    with ThreadPoolExecutor(max_workers=48) as pool:
        for future in as_completed([pool.submit(chunk, i) for i in range(total) if i not in done]):
            future.result()
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
    if (target / "geniex.dll").is_file():
        return
    name = "geniex-sdk-windows-arm64-v0.10.0.zip"
    url = "https://github.com/qualcomm/GenieX/releases/download/v0.10.0/" + name
    archive = download(url, REPO / "build/downloads" / name)
    expected = urllib.request.urlopen(url + ".sha256", timeout=30).read().decode().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError("GenieX runtime SHA256 mismatch")
    unpack = REPO / "build/geniex-sdk"
    extract(archive, unpack)
    lib = next(unpack.rglob("geniex.dll")).parent
    shutil.copytree(lib, target, dirs_exist_ok=True)


def install_model(spec):
    from .npu import CACHE, EMBED_DIR, have
    if have(spec):
        return
    with _install_lock:
        if have(spec):
            return
        if spec.provider == "aihub":
            archive = download(NOMIC_URL, REPO / "build/downloads/nomic-embed-text-v0.65.0.zip")
            extract(archive, EMBED_DIR)
            if not any(EMBED_DIR.rglob("tokenizer.json")):
                from huggingface_hub import hf_hub_download
                shutil.copy(hf_hub_download("google-bert/bert-base-uncased", "tokenizer.json"), EMBED_DIR / "tokenizer.json")
            (EMBED_DIR / "source.json").write_text(json.dumps({"source": NOMIC_URL, "version": "0.65.0"}))
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
