"""TG Account Manager — Launcher"""
import os, sys, time, socket, subprocess, webbrowser

DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

PORT = 8010
# .env dan BASE_PATH o'qish
bp = ""
env_path = os.path.join(DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.strip().startswith("BASE_PATH="):
                bp = line.strip().split("=",1)[1].strip().strip("/")
URL = f"http://127.0.0.1:{PORT}/{bp}/" if bp else f"http://127.0.0.1:{PORT}/"

def find_python():
    if sys.platform == "win32":
        p = os.path.join(DIR, "venv", "Scripts", "python.exe")
    else:
        p = os.path.join(DIR, "venv", "bin", "python3")
    return p if os.path.exists(p) else sys.executable

# Port band bo'lsa — allaqachon ishlayapti
with socket.socket() as s:
    if s.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"Server allaqachon ishlayapti")
        webbrowser.open(URL)
        sys.exit(0)

# Server ishga tushirish
print(f"Server ishga tushirilmoqda (port {PORT})...")
cmd = [find_python(), "-m", "uvicorn", "api:root", "--host", "127.0.0.1", "--port", str(PORT)]
kwargs = {"cwd": DIR}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

proc = subprocess.Popen(cmd, **kwargs)

# Kutish
for _ in range(30):
    time.sleep(1)
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            break
else:
    print("Server ishga tushmadi!")
    proc.kill()
    sys.exit(1)

print(f"Server tayyor: {URL}")
webbrowser.open(URL)

# Server ishlashda davom etsin
try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
