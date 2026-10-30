"""Paths and machine facts. Works both from source (dev) and inside the packaged Local Office.app."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROLES = ("assistant", "ocr", "embed", "agent")
REPO = Path(__file__).resolve().parents[2]
BUNDLE = Path(getattr(sys, "_MEIPASS", REPO))          # PyInstaller unpacks bundled resources here

DATA_DIR = Path(os.environ.get("DATA_DIR", Path.home() / "Library/Application Support/Local Office"))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", DATA_DIR / "models"))
LOGS_DIR = DATA_DIR / "logs"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", BUNDLE / "ui" if hasattr(sys, "_MEIPASS") else REPO / "frontend/dist"))
EMBED_DIM = int(os.environ.get("EMBED_DIM", 1024))


def _llama_server() -> str:
    for candidate in (os.environ.get("LLAMA_SERVER"), BUNDLE / "bin/llama-server", REPO / "build/bin/llama-server"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return shutil.which("llama-server") or "llama-server"


LLAMA_SERVER = _llama_server()


def _ram_gb() -> int:
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) // 2**30
    except (OSError, ValueError):
        return 0


HOST_RAM_GB = _ram_gb()


def _chip() -> str:
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


HOST_CHIP = _chip()
