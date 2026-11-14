"""`clipforge` command: start the server and open the dashboard."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from . import __version__
from .config import Settings


def _is_clipforge(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/api/health", timeout=1.5)
        return r.status_code == 200 and r.json().get("app") == "clipforge"
    except (httpx.HTTPError, ValueError):
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _open_when_ready(url: str) -> None:
    for _ in range(100):
        if _is_clipforge(url):
            webbrowser.open(url)
            return
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipforge", description="Start ClipForge AI.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", default=None, help="keep the database and working files here")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard")
    parser.add_argument("--version", action="version", version=f"ClipForge AI {__version__}")
    args = parser.parse_args(argv)

    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}"

    if _is_clipforge(url):
        print(f"ClipForge AI is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if _port_in_use(host, port):
        print(f"Port {port} is used by another program. Try: clipforge --port {port + 1}", file=sys.stderr)
        return 1

    if not (settings.frontend_dist / "index.html").is_file():
        print("Note: the dashboard is not built yet (run 'npm run build' in the frontend folder).")

    from .app import create_app  # imported late so --version and the checks above stay instant

    app = create_app(settings)
    print(f"ClipForge AI {__version__} -> {url}   (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
