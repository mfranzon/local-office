"""Entry point of Local Office.app: runs the backend on a private localhost port inside a native window."""
import atexit
import logging
import socket
import threading
import time
import urllib.request

import uvicorn
import webview

from . import runtime
from .config import DATA_DIR, LOGS_DIR


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOGS_DIR / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .main import app  # after logging is configured

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    atexit.register(runtime.shutdown)   # never leave model processes behind

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base}/api/status", timeout=1)
            break
        except OSError:
            time.sleep(0.1)

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("Local Office", base, width=1360, height=880, min_size=(1000, 680))
    webview.start(private_mode=False, storage_path=str(DATA_DIR / "webview"))
    server.should_exit = True
    runtime.shutdown()


if __name__ == "__main__":
    main()
