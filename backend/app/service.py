"""Loopback service owned by the desktop host, including crash cleanup."""
import argparse
import ctypes
from ctypes import wintypes
import os
import threading

import uvicorn


def watch_desktop(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        os._exit(0)
    kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
    kernel.CloseHandle(handle)
    # Closing this process's Windows job handle ends any native inference worker.
    os._exit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    options = parser.parse_args()
    if os.name == "nt" and (pid := os.environ.get("LOCAL_OFFICE_PARENT_PID")):
        threading.Thread(target=watch_desktop, args=(int(pid),), daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()
