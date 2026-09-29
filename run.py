"""
TG Account Manager — Launcher
Ikki marta bosing:
  - Windows/Desktop: o'z oynasida panel ochiladi
  - Server: brauzerda yoki faqat server ishlaydi
"""

import os
import sys
import time
import socket
import threading
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


def wait_for_server(port, timeout=30):
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


def is_port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_python():
    if sys.platform == "win32":
        p = os.path.join(DIR, "venv", "Scripts", "python.exe")
    else:
        p = os.path.join(DIR, "venv", "bin", "python3")
    return p if os.path.exists(p) else sys.executable


def start_server():
    cmd = [find_python(), "-m", "uvicorn", "api:root",
           "--host", "127.0.0.1", "--port", str(PORT),
           "--timeout-graceful-shutdown", "5"]
    kwargs = {"cwd": DIR, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)


def has_display():
    """GUI mavjudligini tekshirish."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return True
    # Linux — DISPLAY yoki WAYLAND bor-yo'qligini tekshirish
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def try_webview(server_proc):
    """pywebview bilan GUI oyna ochish."""
    import webview

    window = webview.create_window(
        "TG Account Manager",
        URL,
        width=1100,
        height=750,
        min_size=(800, 500),
        text_select=True,
    )

    def on_closed():
        if server_proc:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except Exception:
                server_proc.kill()

    window.events.closed += on_closed
    webview.start()


def main():
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║    📱 TG Account Manager             ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    # 1. Server
    server_proc = None
    if is_port_busy(PORT):
        print(f"  ✅ Server allaqachon ishlayapti (port {PORT})")
    else:
        print(f"  🚀 Server ishga tushirilmoqda...")
        server_proc = start_server()

    # 2. Kutish
    print(f"  ⏳ Kutilmoqda...")
    if not wait_for_server(PORT, timeout=25):
        print(f"  ❌ Server ishga tushmadi!")
        if server_proc:
            server_proc.kill()
        sys.exit(1)

    print(f"  ✅ Server tayyor: {URL}")

    # 3. GUI yoki brauzer
    if has_display():
        try:
            print("  🖥️  Oyna ochilmoqda...")
            try_webview(server_proc)
            return
        except ImportError:
            print("  ⚠️  pywebview topilmadi — brauzerda ochilmoqda...")
            print("     O'rnatish: pip install pywebview")
        except Exception as e:
            print(f"  ⚠️  GUI xato: {e}")

        # Brauzerda ochish
        import webbrowser
        webbrowser.open(URL)

    # Server rejimida ishlash (yoki brauzer ochilgandan keyin)
    print()
    print(f"  🌐 Manzil: {URL}")
    print(f"  🛑 Yopish: Ctrl+C")
    print()

    try:
        if server_proc:
            server_proc.wait()
        else:
            while True:
                time.sleep(60)
    except KeyboardInterrupt:
        print("\n  🛑 To'xtatilmoqda...")
        if server_proc:
            server_proc.terminate()


if __name__ == "__main__":
    main()
