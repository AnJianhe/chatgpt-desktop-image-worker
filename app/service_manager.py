"""Windows VM service launcher. Uses only the Python standard library."""
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request
import webbrowser
import uptime_kuma

BASE = Path(__file__).resolve().parent
SERVICES = {
    "server": ("画图服务器", "server.py", ["--host", "127.0.0.1", "--port", "8765"]),
    "tunnel": ("隧道看门狗", "tunnel_watchdog.py", []),
    "sync": ("链接同步", "link_sync.py", []),
    "kuma": ("Uptime Kuma 监控", "uptime_kuma.py", []),
    "cloud": ("Sites 云端任务同步", "cloud_agent.py", []),
    "monitor_sync": ("监控数据同步", "kuma_sync.py", []),
    "bug_notifier": ("Bug 报告通知", "bug_notifier.py", []),
}
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def process_inventory(strict=True):
    script = ('[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); '
              'ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process '
              '| Where-Object {$_.Name -in @("python.exe","pythonw.exe")} '
              '| Select-Object ProcessId,CommandLine)')
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, timeout=12, creationflags=HIDDEN)
    if result.returncode:
        raise RuntimeError("无法读取进程状态，请以管理员身份打开管理界面")
    rows = json.loads(result.stdout.decode("utf-8-sig"))
    # Unknown Python processes may be elevated services. Never blindly duplicate them.
    if strict and any(not row.get("CommandLine") for row in rows):
        raise RuntimeError("有 Python 进程不可读取，请以管理员身份打开管理界面")
    return rows


def service_pids(rows, script):
    target = str(BASE / script).casefold()
    return [int(row["ProcessId"]) for row in rows
            if target in str(row.get("CommandLine", "")).casefold()]


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def tail(path, size=65536):
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            total = stream.tell()
            stream.seek(max(0, total - size))
            data = stream.read(size)
        if total > size:
            data = data.split(b"\n", 1)[-1]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return "暂无日志。已有服务的输出可能仍在原来的命令窗口中。"


class Controller:
    def __init__(self):
        self.children = {}
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def health(self):
        try:
            with self.http.open("http://127.0.0.1:8765/health", timeout=3) as response:
                data = json.load(response)
            if data.get("status") == "ok":
                return "正常" if data.get("worker_alive") else "桌面执行线程异常"
            return "响应异常"
        except Exception:
            return "未响应"

    def snapshot(self):
        rows = process_inventory(strict=not (BASE / 'cloud_agent_config.json').is_file())
        return {key: service_pids(rows, value[1]) for key, value in SERVICES.items()}

    def launch(self, key):
        title, script, args = SERVICES[key]
        if not (BASE / script).is_file():
            raise RuntimeError(f"缺少文件：{script}")
        python = Path(sys.executable).with_name("python.exe")
        if not python.is_file():
            raise RuntimeError("没有找到 python.exe")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        child = subprocess.Popen([str(python), "-u", str(BASE / script), *args], cwd=BASE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env, creationflags=HIDDEN)
        self.children[key] = child
        return child

    def start_missing(self):
        state = self.snapshot()
        notes = []
        for key, (title, script, args) in SERVICES.items():
            cloud_mode = (BASE / 'cloud_agent_config.json').is_file()
            if (cloud_mode and key in ('tunnel', 'sync')) or (not cloud_mode and key in ('cloud', 'monitor_sync', 'bug_notifier')):
                continue
            if key == "kuma" and not uptime_kuma.installed():
                notes.append("Uptime Kuma 未安装，跳过可选监控")
                continue
            child = self.children.get(key)
            if state.get(key) or (child is not None and child.poll() is None):
                notes.append(title + "已运行，保留原进程")
                continue
            if key == "server" and self.health() != "未响应":
                notes.append("8765 端口已有服务，保留原进程")
                continue
            if key == "kuma" and uptime_kuma.available():
                notes.append("Uptime Kuma 已运行，保留原进程")
                continue
            # Relay owns stdout and disk rotation; GUI does not own the service lifetime.
            python = Path(sys.executable).with_name("python.exe")
            if not (BASE / script).is_file():
                raise RuntimeError(f"缺少文件：{script}")
            relay = subprocess.Popen([str(python), str(Path(__file__).resolve()), "--relay", key],
                                     cwd=BASE, creationflags=HIDDEN,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            self.children[key] = relay
            notes.append(title + "已发起启动")
        return "；".join(notes)


def relay(key):
    logs = BASE / "logs"
    logs.mkdir(exist_ok=True)
    logger = logging.getLogger("relay")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(logs / ("manager-" + key + ".log"),
                                 maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    title, script, _ = SERVICES[key]
    child = None
    try:
        if service_pids(process_inventory(strict=key in ('tunnel','sync')), script):
            logger.info("%s已运行，本次启动已跳过", title)
            return
        controller = Controller()
        if key == "server" and controller.health() != "未响应":
            logger.info("8765 端口已有服务，跳过启动")
            return
        if key == "kuma" and uptime_kuma.available():
            logger.info("Uptime Kuma 已运行，跳过启动")
            return
        child = controller.launch(key)
        logger.info("启动 %s，PID=%s", title, child.pid)
        while True:
            block = child.stdout.readline(16384)
            if not block:
                break
            logger.info("%s", block.decode("utf-8", errors="replace").rstrip())
        code = child.wait()
        logger.warning("%s已退出，退出码=%s。可点击一键启动补启动。", title, code)
    except Exception:
        logger.exception("%s启动失败", title)
    finally:
        handler.close()


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("画图服务管理器 v1.3 · Sites")
        self.geometry("960x660")
        self.minsize(750, 500)
        self.controller = Controller()
        self.events = queue.Queue()
        self.busy = False
        self.labels = {}
        self.note = tk.StringVar(value="关闭此窗口不会关闭后台服务。")
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        self.start_button = ttk.Button(bar, text="一键启动全部服务", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(bar, text="打开本地网页", command=lambda: webbrowser.open("http://127.0.0.1:8765/")).pack(side="left", padx=8)
        ttk.Button(bar, text="打开日志文件夹", command=lambda: os.startfile(str(BASE / "logs"))).pack(side="left")
        ttk.Button(bar, text="打开运行监控", command=lambda: webbrowser.open("http://127.0.0.1:3001/")).pack(side="left", padx=8)
        if (BASE / 'cloud_agent_config.json').is_file():
            ttk.Button(bar, text="打开云端工作台", command=lambda: webbrowser.open(read_json(BASE / 'cloud_agent_config.json')['endpoint'])).pack(side="left", padx=8)
        status = ttk.LabelFrame(self, text="服务状态（每 5 秒刷新）", padding=12)
        status.pack(fill="x", padx=12)
        for key, (title, _, _) in SERVICES.items():
            label = ttk.Label(status, text=title + "：正在读取…")
            label.pack(anchor="w", pady=3)
            self.labels[key] = label
        self.network = ttk.Label(status, text="公网连接：正在读取…", wraplength=890)
        self.network.pack(anchor="w", pady=3)
        ttk.Label(self, textvariable=self.note, wraplength=910, padding=12).pack(fill="x")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.views = []
        for title, filename in [("画图服务器", "manager-server.log"), ("隧道", "tunnel-watchdog.log"),
                                ("链接同步", "link-sync.log"), ("启动错误", "manager-tunnel.log"),
                                ("同步启动错误", "manager-sync.log"), ("运行监控", "manager-kuma.log"), ("云端同步", "cloud-agent.log"), ("监控数据同步", "kuma-sync.log"), ("Bug 报告", "bug-notifier.log")]:
            frame = ttk.Frame(self.tabs)
            self.tabs.add(frame, text=title)
            text = tk.Text(frame, wrap="word", state="disabled", font=("Consolas", 10))
            scroll = ttk.Scrollbar(frame, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            self.views.append((text, BASE / "logs" / filename))
        self.after(100, self.tick)

    def start(self):
        if self.busy:
            return
        self.busy = True
        self.start_button.config(state="disabled")
        self.note.set("正在补启动缺失的服务，现有服务不会被重启…")
        def work():
            try:
                self.events.put(("started", self.controller.start_missing()))
            except Exception as exc:
                self.events.put(("failed", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def inspect(self):
        try:
            self.events.put(("status", (self.controller.snapshot(), self.controller.health())))
        except Exception as exc:
            self.events.put(("inspect_error", str(exc)))

    def tick(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind in ("started", "failed"):
                self.busy = False
                self.start_button.config(state="normal")
                self.note.set(value)
                if kind == "failed":
                    messagebox.showerror("启动失败", value)
            elif kind == "status":
                self.inspecting = False
                pids, health = value
                for key, (title, _, _) in SERVICES.items():
                    info = "运行中，PID " + ", ".join(map(str, pids[key])) if pids[key] else "未运行"
                    if key == "server":
                        if not pids[key] and health == "正常":
                            info = "运行中（本地接口已确认，PID 未关联）"
                        info += "；本地接口：" + health
                    self.labels[key].config(text=title + "：" + info)
            elif kind == "inspect_error":
                self.inspecting = False
                self.note.set(value)
        now = time.monotonic()
        if now >= getattr(self, "next_inspect", 0) and not getattr(self, "inspecting", False):
            self.next_inspect = now + 5
            self.inspecting = True
            threading.Thread(target=self.inspect, daemon=True).start()
        state = read_json(BASE / "logs" / "tunnel-status.json")
        detail = str(state.get("detail") or "")
        try:
            fresh = time.time() - (BASE / "logs" / "tunnel-status.json").stat().st_mtime < 45
        except OSError:
            fresh = False
        self.network.config(text="公网连接：" + (str(state.get("state", "未知")) if fresh else "状态记录已过期或尚未产生") + " " + detail)
        if (BASE / 'cloud_agent_config.json').is_file():
            cloud = read_json(BASE / 'logs' / 'cloud-status.json')
            self.network.config(text='Sites 云端同步：'+('正常' if time.time()-cloud.get('updated',0)<30 else '暂未连接，请检查云端同步日志')+'；云端模式不启动隧道。')
        current = self.tabs.index(self.tabs.select())
        widget, path = self.views[current]
        content = tail(path)
        if content != getattr(widget, "last_content", None):
            at_bottom = widget.yview()[1] >= .99
            position = widget.yview()[0]
            widget.config(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", content)
            widget.config(state="disabled")
            widget.see("end") if at_bottom else widget.yview_moveto(position)
            widget.last_content = content
        self.after(1000, self.tick)


def main():
    if "--relay" in sys.argv:
        relay(sys.argv[sys.argv.index("--relay") + 1])
        return
    if os.name != "nt":
        raise RuntimeError("此管理器用于 Windows 虚拟机")
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
        if "virtual" not in winreg.QueryValueEx(key, "SystemProductName")[0].lower():
            messagebox.showerror("请在虚拟机打开", "请把管理器放在虚拟机的 image-automation 文件夹中运行。")
            return
    (BASE / "logs").mkdir(exist_ok=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\ChatGPTServiceManager")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        messagebox.showinfo("已打开", "管理器已在运行，请从任务栏打开。")
        return
    try:
        Manager().mainloop()
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
