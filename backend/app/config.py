"""Paths and machine facts. Works both from source (dev) and inside the packaged Local Office.app."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROLES = ("assistant", "ocr", "embed")
REPO = Path(__file__).resolve().parents[2]
BUNDLE = Path(getattr(sys, "_MEIPASS", REPO))          # PyInstaller unpacks bundled resources here

MAC = sys.platform == "darwin"
_DEFAULT_DATA = (Path.home() / "Library/Application Support/Local Office" if MAC
                 else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "local-office")
DATA_DIR = Path(os.environ.get("DATA_DIR", _DEFAULT_DATA))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", DATA_DIR / "models"))
LOGS_DIR = DATA_DIR / "logs"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", BUNDLE / "ui" if hasattr(sys, "_MEIPASS") else REPO / "frontend/dist"))
EMBED_DIM = int(os.environ.get("EMBED_DIM", 1024))
BUILTIN_SKILLS = BUNDLE / "skills"                      # shipped with the app; user skills live in DATA_DIR/skills


def _llama_server() -> str:
    for candidate in (os.environ.get("LLAMA_SERVER"), BUNDLE / "bin/llama-server", REPO / "build/bin/llama-server"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return shutil.which("llama-server") or "llama-server"


LLAMA_SERVER = _llama_server()


def _ram_gb() -> int:
    try:
        if MAC:
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) // 2**30
        return -(-os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**30)  # round up: Linux reports a 16 GB machine as ~15.5
    except (OSError, ValueError):
        return 0


HOST_RAM_GB = _ram_gb()


def _chip() -> str:
    try:
        if MAC:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name")), "")
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip().splitlines() if shutil.which("nvidia-smi") else []
        return " + ".join([cpu, *gpu[:1]])
    except OSError:
        return ""


HOST_CHIP = _chip()


def _vram_mb() -> int:
    """Memory of the first NVIDIA GPU, 0 if there is none (or on a Mac, where memory is unified)."""
    if MAC or not shutil.which("nvidia-smi"):
        return 0
    try:
        return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True).stdout.split()[0])
    except (OSError, ValueError, IndexError):
        return 0


HOST_VRAM_MB = _vram_mb()
