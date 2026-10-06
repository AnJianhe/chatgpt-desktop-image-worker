"""Upload watchdog status to the stable entry page. Does not launch a tunnel."""
import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from tunnel_watchdog import write_json

BASE = Path(__file__).resolve().parent
STATES = {"可访问", "原地址重连中", "连接中", "本地服务未就绪", "等待地址", "重启中", "已停止", "启动重试中", "状态已过期"}
URL_PATTERN = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/?\Z")

def read_status(path, now=None):
    now = time.time() if now is None else now
    try:
        # Watchdog atomically replaces this file; stat/open race can only make it older.
        age = max(0, now - path.stat().st_mtime)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        url = str(data.get("url") or "")
        state = data.get("state")
        if state not in STATES or age > 45:
            state = "状态已过期"
        if url and not URL_PATTERN.fullmatch(url):
            url, state = "", "状态已过期"
        return {"url": url.rstrip("/"), "state": state, "source_age": round(age, 1)}
    except (OSError, ValueError, TypeError):
        return {"url": "", "state": "状态已过期", "source_age": 999}

def config(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    endpoint = data["endpoint"]
    if not endpoint.startswith("https://") or not endpoint.endswith("/api/update"):
        raise ValueError("Invalid sync endpoint")
    if len(data.get("token", "")) < 32:
        raise ValueError("Missing sync token")
    return data

def upload(settings, status):
    req = urllib.request.Request(settings["endpoint"], data=json.dumps(status, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings["token"], "User-Agent": "FabricLinkSync/1"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status != 200 or not json.load(response).get("ok"):
            raise RuntimeError("Sync response rejected")

def vm_only():
    if os.name != "nt":
        raise RuntimeError("Run this uploader inside the Windows VM")
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
        product = winreg.QueryValueEx(key, "SystemProductName")[0]
    if "virtual" not in product.lower():
        raise RuntimeError("VM-only startup: physical host was not changed")

def install_startup():
    vm_only()
    import winreg
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        winreg.SetValueEx(key, "ChatGPTLinkSync", 0, winreg.REG_SZ, f'"{pythonw}" "{BASE / "link_sync.py"}"')

def run(once=False):
    vm_only()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\ChatGPTStableLinkPublisher")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        return
    logs = BASE / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[RotatingFileHandler(logs / "link-sync.log", maxBytes=300000, backupCount=2, encoding="utf-8")])
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            startup_installed = str(BASE / "link_sync.py") in winreg.QueryValueEx(key, "ChatGPTLinkSync")[0]
    except OSError:
        startup_installed = False
    startup_installed = startup_installed or (Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/ChatGPTLinkSync.lnk").is_file()
    previous = None
    last_sent = 0
    while True:
        try:
            settings = config(BASE / "link_sync_config.json")
            status = read_status(logs / "tunnel-status.json")
            signature = (status["url"], status["state"], settings["endpoint"])
            if once or signature != previous or time.monotonic() - last_sent >= 10:
                upload(settings, status)
                previous, last_sent = signature, time.monotonic()
                progress = {"ok": True, "state": status["state"], "url": status["url"], "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "startup_installed": startup_installed}
                write_json(logs / "link-sync-status.json", progress)
                if signature != getattr(run, "logged", None):
                    logging.info("Synced: %s %s", status["state"], status["url"])
                    run.logged = signature
            if once:
                return
        except Exception as error:
            logging.warning("Sync failed (%s); retrying", type(error).__name__)
            if once:
                raise
            time.sleep(5)
        time.sleep(2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--setup-and-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.install_startup or args.setup_and_run:
        install_startup()
    if not args.install_startup or args.setup_and_run:
        run(args.once)
