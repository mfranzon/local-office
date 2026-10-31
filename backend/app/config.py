"""Paths and machine facts. Works both from source (dev) and inside the packaged Local Office.app."""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROLES = ("assistant", "ocr", "embed")
REPO = Path(__file__).resolve().parents[2]
BUNDLE = Path(getattr(sys, "_MEIPASS", REPO))          # PyInstaller unpacks bundled resources here

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"
if MAC:
    _DEFAULT_DATA = Path.home() / "Library/Application Support/Local Office"
elif WINDOWS:
    _DEFAULT_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Local Office"
else:
    _DEFAULT_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "local-office"
# the packaged Windows app has no console: without this, every child process flashes a console window
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
DATA_DIR = Path(os.environ.get("DATA_DIR", _DEFAULT_DATA))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", DATA_DIR / "models"))
LOGS_DIR = DATA_DIR / "logs"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", BUNDLE / "ui" if hasattr(sys, "_MEIPASS") else REPO / "frontend/dist"))
EMBED_DIM = int(os.environ.get("EMBED_DIM", 1024))
BUILTIN_SKILLS = BUNDLE / "skills"                      # shipped with the app; user skills live in DATA_DIR/skills


def _llama_server() -> str:
    exe = "llama-server.exe" if WINDOWS else "llama-server"
    for candidate in (os.environ.get("LLAMA_SERVER"), BUNDLE / "bin" / exe, REPO / "build/bin" / exe):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return shutil.which("llama-server") or "llama-server"


LLAMA_SERVER = _llama_server()


def _ram_gb() -> int:
    try:
        if WINDOWS:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            *((name, ctypes.c_ulonglong) for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                              "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual"))]
            status = MemoryStatus(dwLength=ctypes.sizeof(MemoryStatus))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return -(-status.ullTotalPhys // 2**30)   # round up: the firmware reserves a little
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
        if WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip() or platform.processor()
        else:
            cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name")), "")
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, **NO_WINDOW).stdout.strip().splitlines() if shutil.which("nvidia-smi") else []
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
                                  capture_output=True, text=True, **NO_WINDOW).stdout.split()[0])
    except (OSError, ValueError, IndexError):
        return 0


HOST_VRAM_MB = _vram_mb()
