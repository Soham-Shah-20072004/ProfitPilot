#!/usr/bin/env python3
"""One command to run ProfitPilot on your laptop.

    python run.py              # app + API on http://localhost:8000 (opens your browser)
    python run.py --lan        # also reachable from phones on the same Wi-Fi (prints a QR code)
    python run.py --reset      # start again from fresh demo data
    python run.py --port 9000  # another port

First run creates a virtual environment in .venv and installs requirements.txt
(about a minute). Needs Python 3.9 or newer. Stop the server with Ctrl+C.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQ = ROOT / "requirements.txt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env(use_venv: bool) -> str:
    if sys.version_info < (3, 9):
        sys.exit(f"Python 3.9+ is needed (you have {sys.version.split()[0]}). Install it from python.org.")
    if not use_venv:
        return sys.executable
    py = venv_python()
    if not py.exists():
        print("• Creating a virtual environment in .venv …")
        venv.EnvBuilder(with_pip=True).create(VENV)
    marker = VENV / ".requirements.sha"
    digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text() != digest:
        print("• Installing requirements (first run only, about a minute) …")
        subprocess.check_call([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQ)])
        marker.write_text(digest)
    return str(py)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_healthy(url: str, timeout: float = 40) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url + "/api/v1/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.4)
    return False


def print_qr(py: str, url: str) -> None:
    code = ("import qrcode,sys\nq=qrcode.QRCode(border=1)\nq.add_data(sys.argv[1])\nq.make(fit=True)\nq.print_ascii(invert=True)")
    try:
        subprocess.run([py, "-c", code, url], check=False, timeout=10)
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Run ProfitPilot (API + app) on this machine.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--lan", action="store_true", help="listen on the local network so phones can open it")
    ap.add_argument("--reset", action="store_true", help="delete the local database and start from fresh demo data")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-venv", action="store_true", help="use the current Python instead of .venv")
    ap.add_argument("--install-only", action="store_true")
    a = ap.parse_args()

    py = ensure_env(not a.no_venv)
    if a.install_only:
        print("• Installed. Start with: python run.py")
        return
    if a.reset:
        for f in ("profitpilot.db", "profitpilot.db-journal"):
            (ROOT / f).unlink(missing_ok=True)
        print("• Fresh demo data")
    if not port_free(a.port):
        sys.exit(f"Port {a.port} is busy. Try: python run.py --port {a.port + 1}")

    host = "0.0.0.0" if a.lan else "127.0.0.1"
    local = f"http://localhost:{a.port}"
    cmd = [py, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(a.port), "--log-level", "warning"]
    proc = subprocess.Popen(cmd, cwd=str(ROOT))

    def announce():
        if not wait_healthy(local):
            print("✘ The server did not start. See the error above.")
            return
        print("\n" + "=" * 60)
        print("  ProfitPilot is running on this computer")
        print(f"  App ............ {local}")
        print(f"  API docs ....... {local}/docs")
        print(f"  Health ......... {local}/api/v1/health")
        if a.lan:
            phone = f"http://{lan_ip()}:{a.port}"
            print(f"  On your phone .. {phone}   (same Wi-Fi; allow Python in the firewall if asked)")
        print("  Stop ........... Ctrl+C")
        print("=" * 60)
        if a.lan:
            print_qr(py, f"http://{lan_ip()}:{a.port}")
        if not a.no_browser:
            webbrowser.open(local)

    threading.Thread(target=announce, daemon=True).start()
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n• Stopping …")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
