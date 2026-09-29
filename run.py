"""
TG Account Manager — GUI Launcher
Ikki marta bosing — o'z oynasida panel ochiladi.
Brauzer kerak emas!
"""

import os
import sys
import time
import socket
import threading
import subprocess

# Loyiha papkasini aniqlash
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

# .env ni yuklash
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


def is_port_free(port):
    """Port bo'shligini tekshirish."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_for_server(port, timeout=30):
    """Server tayyor bo'lguncha kutish."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_server():
    """Uvicorn serverni fon threadda ishga tushirish."""
    # Venv ichidagi python'ni ishlatish
    if sys.platform == "win32":
        python = os.path.join(DIR, "venv", "Scripts", "python.exe")
    else:
        python = os.path.join(DIR, "venv", "bin", "python3")

    if not os.path.exists(python):
        python = sys.executable

    cmd = [
        python, "-m", "uvicorn", "api:root",
        "--host", "127.0.0.1",
        "--port", str(PORT),
        "--timeout-graceful-shutdown", "5",
    ]

    # Server jarayonini boshlash
    kwargs = {"cwd": DIR, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **kwargs)
    return proc


def main():
    """Asosiy: server + GUI oyna."""

    # 1. Avval port tekshirish
    server_proc = None
    if is_port_free(PORT):
        print(f"🚀 Server ishga tushirilmoqda (port {PORT})...")
        server_proc = start_server()
    else:
        print(f"✅ Server allaqachon port {PORT} da ishlayapti")

    # 2. Server tayyorligini kutish
    print("⏳ Server tayyorlanmoqda...")
    if not wait_for_server(PORT, timeout=30):
        print("❌ Server ishga tushmadi!")
        if server_proc:
            server_proc.kill()
        sys.exit(1)

    print(f"✅ Server tayyor: {URL}")

    # 3. GUI oynani ochish
    try:
        import webview
        print("🖥️ Oyna ochilmoqda...")

        window = webview.create_window(
            "TG Account Manager",
            URL,
            width=1100,
            height=750,
            min_size=(800, 500),
            confirm_close=True,
            text_select=True,
        )

        # Oyna yopilganda serverni to'xtatish
        def on_closed():
            if server_proc:
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except Exception:
                    server_proc.kill()

        window.events.closed += on_closed
        webview.start()

    except ImportError:
        # pywebview o'rnatilmagan — oddiy brauzerda ochish
        print("⚠️ pywebview o'rnatilmagan — brauzerda ochilmoqda...")
        print(f"   pip install pywebview")
        import webbrowser
        webbrowser.open(URL)
        print("\n🛑 Yopish uchun Ctrl+C bosing")
        try:
            if server_proc:
                server_proc.wait()
        except KeyboardInterrupt:
            if server_proc:
                server_proc.terminate()

    except Exception as e:
        # GUI xato bo'lsa — brauzerda ochish
        print(f"⚠️ GUI xato: {e} — brauzerda ochilmoqda...")
        import webbrowser
        webbrowser.open(URL)
        print("\n🛑 Yopish uchun Ctrl+C bosing")
        try:
            if server_proc:
                server_proc.wait()
        except KeyboardInterrupt:
            if server_proc:
                server_proc.terminate()


if __name__ == "__main__":
    main()
