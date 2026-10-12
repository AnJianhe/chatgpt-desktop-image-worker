"""Run the optional, separately installed Uptime Kuma Windows package."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent / "uptime-kuma"


def installed():
    return (ROOT / "node.exe").is_file() and (ROOT / "server/server.js").is_file()


def available():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:3001/api/entry-page", timeout=3) as response:
            return response.status == 200 and isinstance(json.load(response), dict)
    except Exception:
        return False


def main():
    if not installed():
        raise RuntimeError("Uptime Kuma 尚未安装，请先运行安装脚本")
    if available():
        print("Uptime Kuma 已运行，保留原进程", flush=True)
        return
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    env = dict(os.environ, UPTIME_KUMA_HOST="127.0.0.1", UPTIME_KUMA_PORT="3001",
               UPTIME_KUMA_DB_TYPE="sqlite", DATA_DIR=str(data))
    child = subprocess.Popen([str(ROOT / "node.exe"), str(ROOT / "server/server.js")],
                             cwd=ROOT, env=env)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        return child.wait()


if __name__ == "__main__":
    sys.exit(main())
