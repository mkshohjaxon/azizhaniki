"""
TG Account Manager — Launcher
  win.bat  → Windows
  start.sh → Linux
"""

import os
import sys
import time
import signal
import socket
import atexit
import subprocess

DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

# .env yuklash
env_path = os.path.join(DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

PORT = 8010
BASE_PATH = os.environ.get("BASE_PATH", "").strip("/")
URL = f"http://127.0.0.1:{PORT}/{BASE_PATH}/" if BASE_PATH else f"http://127.0.0.1:{PORT}/"

_server_proc = None


def kill_server():
    """Serverni to'xtatish — X bosilganda, Ctrl+C da, chiqishda."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=3)
        except Exception:
            _server_proc.kill()
        _server_proc = None


# Har qanday holatda serverni to'xtatish
atexit.register(kill_server)


def wait_for_server(timeout=25):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                if s.connect_ex(("127.0.0.1", PORT)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def is_port_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def find_python():
    if sys.platform == "win32":
        p = os.path.join(DIR, "venv", "Scripts", "python.exe")
    else:
        p = os.path.join(DIR, "venv", "bin", "python3")
    return p if os.path.exists(p) else sys.executable


def start_server():
    global _server_proc
    cmd = [find_python(), "-m", "uvicorn", "api:root",
           "--host", "127.0.0.1", "--port", str(PORT),
           "--timeout-graceful-shutdown", "3"]
    kwargs = {"cwd": DIR, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    _server_proc = subprocess.Popen(cmd, **kwargs)
    return _server_proc


def has_display():
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main():
    # 1. Server
    if is_port_busy():
        print(f"  Server allaqachon port {PORT} da ishlayapti")
    else:
        print(f"  Server ishga tushirilmoqda...")
        start_server()

    # 2. Kutish
    if not wait_for_server():
        print(f"  Server ishga tushmadi!")
        kill_server()
        sys.exit(1)

    print(f"  Server tayyor: {URL}")

    # 3. GUI oyna
    if has_display():
        try:
            import webview

            window = webview.create_window(
                "TG Account Manager",
                URL,
                width=1100,
                height=750,
                min_size=(800, 500),
                text_select=True,
            )
            # X bosilganda server to'xtaydi
            window.events.closed += kill_server
            webview.start()
            # Oyna yopildi — chiqish
            kill_server()
            return

        except ImportError:
            print("  pywebview yo'q — brauzerda ochilmoqda")
            print("  O'rnatish: pip install pywebview")
        except Exception as e:
            print(f"  GUI xato: {e}")

        import webbrowser
        webbrowser.open(URL)

    # Server rejimi
    print(f"\n  Manzil: {URL}")
    print(f"  Yopish: Ctrl+C\n")
    try:
        if _server_proc:
            _server_proc.wait()
        else:
            while True:
                time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        kill_server()


if __name__ == "__main__":
    main()
