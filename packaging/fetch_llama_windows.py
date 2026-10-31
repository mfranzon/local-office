"""Puts llama.cpp's official Windows build of llama-server (and its DLLs) in build/bin.

Same llama.cpp tag as build_llama.sh. The default CUDA 12.4 build also carries the CPU backends, which llama-server
loads on its own when there is no NVIDIA GPU. LLAMA_WIN_BACKEND=vulkan (any GPU) or cpu picks a smaller build.
"""
import io
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/bin"
TAG = os.environ.get("LLAMA_TAG") or re.search(r'LLAMA_TAG:-(\w+)', (ROOT / "packaging/build_llama.sh").read_text()).group(1)
BACKEND = os.environ.get("LLAMA_WIN_BACKEND", "cuda-12.4")
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}"

zips = [f"llama-{TAG}-bin-win-{BACKEND}-x64.zip"]
if BACKEND.startswith("cuda-"):
    zips.append(f"cudart-llama-bin-win-{BACKEND}-x64.zip")   # CUDA runtime + cuBLAS: users only need the NVIDIA driver

OUT.mkdir(parents=True, exist_ok=True)
for name in zips:
    print("download", name, flush=True)
    with urllib.request.urlopen(f"{BASE}/{name}") as r, zipfile.ZipFile(io.BytesIO(r.read())) as z:
        for member in z.infolist():
            # only the server and the libraries it loads; the other tools aren't used by the app
            file = Path(member.filename).name
            wanted = file in ("llama-server.exe", "llama-server-impl.dll") or (file.endswith(".dll") and "-impl" not in file)
            if member.is_dir() or not wanted:
                continue
            (OUT / file).write_bytes(z.read(member))
if not (OUT / "llama-server.exe").is_file():
    sys.exit(f"llama-server.exe not found in {zips[0]}")
print(f"llama-server {TAG} ({BACKEND}) in {OUT}")
