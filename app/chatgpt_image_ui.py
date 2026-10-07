"""独立的 Windows ChatGPT 桌面图片自动化；Python 3.10+。

首次运行图形界面完成校准；随后可使用 --prompt 从命令行运行。
不依赖 Codex、computer-use、浏览器控制服务或任何网络 API。
"""
from __future__ import annotations

import argparse
import ctypes
import io
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

BASE = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE / "config.json"
DEFAULTS = {
    "launcher": "",
    "window_title": "ChatGPT",
    "timeout": 600,
    "confidence": 0.90,
    "stable_seconds": 1.5,
    "output_dir": str(BASE / "screenshots"),
    "points": {},
    "client_size": None,
    "template": "done_marker.png",
    "download_dir": "",
    "download_timeout": 90,
}


class NotGenerated(Exception):
    """Human confirmed that no image was produced; not an automation failure."""


class Cancelled(Exception):
    pass


class BottomNotStable(RuntimeError):
    pass


class DownloadMenuNotFound(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS, points={})
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def save_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def windows_downloads() -> Path:
    """读取 Windows 实际下载目录，包括被移动到其他磁盘的目录。"""
    if os.name != "nt":
        raise RuntimeError("请在配置中填写 download_dir。")
    class GUID(ctypes.Structure):
        _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                    ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]
    folder_id = GUID.from_buffer_copy(uuid.UUID("374de290-123f-4565-9164-39c4925e467b").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                          wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE: 已初始化。
        raise RuntimeError("无法读取系统下载目录，请手动填写下载文件夹。")
    result = ctypes.c_wchar_p()
    try:
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(result)) != 0:
            raise RuntimeError("无法读取系统下载目录，请手动填写下载文件夹。")
        if not result.value:
            raise RuntimeError("系统下载目录为空，请手动填写下载文件夹。")
        return Path(result.value)
    finally:
        if result:
            ole.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))
        if initialized >= 0:
            ole.CoUninitialize()


def download_folder(cfg, config_path) -> Path:
    value = cfg.get("download_dir", "").strip()
    folder = Path(os.path.expandvars(value)).expanduser() if value else windows_downloads()
    if not folder.is_absolute():
        folder = config_path.parent / folder
    folder = folder.resolve()
    if not folder.is_dir():
        raise RuntimeError("下载文件夹不存在：" + str(folder))
    return folder


def image_files(folder: Path) -> dict:
    result = {}
    try:
        for path in folder.iterdir():
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            try:
                stat = path.stat()
                if path.is_file():
                    result[path] = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                continue
    except OSError as exc:
        raise RuntimeError("无法读取下载文件夹：" + str(folder)) from exc
    return result


def save_point_selection(cfg, config_path, key, screenshot, client_rect, point):
    """从原始全屏截图保存点位；所有坐标均为物理像素。"""
    from PIL import ImageStat
    if key not in {"new_chat", "input", "send", "hover"}:
        raise ValueError("未知校准点。")
    left, top, width, height = client_rect
    if not (width > 0 and height > 0 and 0 <= left and 0 <= top
            and left + width <= screenshot.width and top + height <= screenshot.height):
        raise RuntimeError("ChatGPT 窗口未完整显示在主屏幕内，请重新打开后校准。")
    x, y = point
    if not (left <= x < left + width and top <= y < top + height):
        raise ValueError("请点击 ChatGPT 窗口内的目标位置。")
    expected = cfg.get("client_size")
    if expected and list(expected) != [width, height]:
        raise RuntimeError("窗口尺寸已改变，请先重置校准，再重新记录。")
    if key == "send":
        patch = screenshot.crop((max(left, x - 16), max(top, y - 16),
                                 min(left + width, x + 16), min(top + height, y + 16)))
        if patch.width < 12 or patch.height < 12 or max(ImageStat.Stat(patch.convert("RGB")).stddev) < 8:
            raise ValueError("请选择可用发送按钮的中央，按钮需要有清晰图案。")
        patch.save(config_path.parent / "send_marker.png")
    cfg["client_size"] = [width, height]
    cfg["points"][key] = [x - left, y - top]
    save_config(config_path, cfg)


class PointPicker:
    """点击冻结的屏幕截图，避免校准时触发真实应用按钮。"""

    def __init__(self, parent, screenshot, label, on_select, on_close):
        import tkinter as tk
        from tkinter import messagebox
        from PIL import ImageTk
        self.closed = False
        self.on_close = on_close
        self.overlay = tk.Toplevel(parent)
        self.overlay.overrideredirect(True)
        self.overlay.geometry(f"{screenshot.width}x{screenshot.height}+0+0")
        self.overlay.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.overlay, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.photo = ImageTk.PhotoImage(screenshot)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        banner = tk.Frame(self.overlay, bg="#123c72", padx=14, pady=10)
        banner.place(relx=.5, y=8, anchor="n")
        tk.Label(banner, text=f"在截图上点击“{label}”的位置（不会点击真实界面）",
                 bg="#123c72", fg="white", font=("Microsoft YaHei UI", 12)).pack(side="left")
        tk.Button(banner, text="取消", command=self.close).pack(side="left", padx=(14, 0))

        def select(event):
            try:
                on_select((event.x, event.y))
            except Exception as exc:
                messagebox.showerror("位置未保存", str(exc), parent=self.overlay)
                return
            self.close()

        self.canvas.bind("<ButtonRelease-1>", select)
        self.overlay.bind("<Escape>", lambda _: self.close())
        self.overlay.protocol("WM_DELETE_WINDOW", self.close)
        self.overlay.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.overlay.destroy()
        self.on_close()


def enable_dpi() -> None:
    # 必须先于 Tk / PyAutoGUI 初始化，统一物理像素坐标。
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()


def dependencies():
    try:
        import pyautogui
        import pyperclip
        import cv2  # PyAutoGUI 的 confidence 匹配需要此模块。
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖，请先运行：python -m pip install -r requirements.txt"
        ) from exc
    pyautogui.PAUSE = 0.15
    return pyautogui, pyperclip, Image, ImageChops, ImageStat


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    path: str


class Windows:
    """只用 Windows 标准函数查找/激活窗口，点击和截图由 PyAutoGUI 执行。"""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("此脚本针对 Windows 10/11 的 ChatGPT 桌面端。")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback, wintypes.LPARAM], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
        }
        for name, (args, result) in signatures.items():
            getattr(self.u, name).argtypes = args
            getattr(self.u, name).restype = result
        self.k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k.OpenProcess.restype = wintypes.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k.CloseHandle.restype = wintypes.BOOL

    def process_path(self, hwnd: int) -> str:
        pid = wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            text = ctypes.create_unicode_buffer(size.value)
            return text.value if self.k.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)) else ""
        finally:
            self.k.CloseHandle(handle)

    def find(self, title_pattern: str) -> Window | None:
        pattern = re.compile(title_pattern, re.IGNORECASE)
        found = []

        @self.callback
        def visit(hwnd, _):
            length = self.u.GetWindowTextLengthW(hwnd)
            if not self.u.IsWindowVisible(hwnd) or not length:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            self.u.GetWindowTextW(hwnd, title, length + 1)
            path = self.process_path(hwnd)
            # 安装包目录可能叫 OpenAI.Codex，但实际应用仍显示 ChatGPT。
            # 以可执行文件名和用户配置的标题共同识别，不按包目录名排除。
            if Path(path).name.lower() == "chatgpt.exe" and pattern.search(title.value):
                found.append(Window(hwnd, title.value, path))
            return True

        self.u.EnumWindows(visit, 0)
        foreground = self.u.GetForegroundWindow()
        return next((w for w in found if w.hwnd == foreground), found[0] if found else None)

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect, point = wintypes.RECT(), wintypes.POINT(0, 0)
        if not self.u.GetClientRect(hwnd, ctypes.byref(rect)) or not self.u.ClientToScreen(hwnd, ctypes.byref(point)):
            raise RuntimeError("无法读取 ChatGPT 窗口，窗口可能已关闭。")
        return point.x, point.y, rect.right - rect.left, rect.bottom - rect.top

    def activate(self, hwnd: int, pag) -> None:
        self.u.ShowWindow(hwnd, 9)  # Restore
        width, height = self.u.GetSystemMetrics(0), self.u.GetSystemMetrics(1)
        self.u.SetWindowPos(hwnd, None, 0, 0, width, height, 0x0004)
        self.u.ShowWindow(hwnd, 3)  # Maximize on primary monitor
        self.u.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        if self.u.GetForegroundWindow() != hwnd:
            # 激活已有窗口时，Windows 有时需要一次用户输入事件。
            pag.press("alt")
            self.u.SetForegroundWindow(hwnd)
            time.sleep(0.3)
        self.assert_foreground(hwnd)

    def assert_foreground(self, hwnd: int) -> None:
        if self.u.GetForegroundWindow() != hwnd:
            raise RuntimeError("ChatGPT 未处于前台；请激活 ChatGPT 后重新开始。")

    def save_dialog(self, owner: int):
        hwnd = self.u.GetForegroundWindow()
        if hwnd == owner or not hwnd:
            return None
        class_name, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, class_name, 256)
        self.u.GetWindowTextW(hwnd, title, 256)
        if class_name.value != "#32770" or not re.fullmatch(
                r"另存为|保存为|保存图片|保存图像|保存文件|保存|save(?: as)?", title.value, re.IGNORECASE):
            return None
        current = hwnd
        for _ in range(8):
            current = self.u.GetWindow(current, 4)  # GW_OWNER
            if current == owner:
                return hwnd
            if not current:
                break
        # Electron 未指定父窗口时，保存对话框可能没有 owner 链。
        popup_pid, owner_pid = wintypes.DWORD(), wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(popup_pid))
        self.u.GetWindowThreadProcessId(owner, ctypes.byref(owner_pid))
        if popup_pid.value and popup_pid.value == owner_pid.value:
            return hwnd
        return None

def discover_launcher() -> str:
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "@(Get-StartApps | Where-Object { $_.Name -eq 'ChatGPT' }) "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("无法自动查找 ChatGPT，请在启动路径中填写快捷方式或程序路径。")
    apps = json.loads(result.stdout.strip() or "[]")
    if isinstance(apps, dict):
        apps = [apps]
    if not apps:
        raise RuntimeError("未找到 ChatGPT 启动项，请填写 ChatGPT 的 .lnk / .exe 路径或 AUMID。")
    return "shell:AppsFolder\\" + apps[0]["AppID"]


def launch(target: str) -> None:
    target = target.strip().strip('"') or discover_launcher()
    if target.lower().startswith("shell:appsfolder\\"):
        subprocess.Popen(["explorer.exe", target])
    elif "!" in target and not Path(target).exists():
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])
    elif Path(target).is_file():
        os.startfile(str(Path(target).resolve()))
    else:
        raise RuntimeError("启动路径不存在。请填写实际的 ChatGPT 快捷方式或程序路径。")


class Automation:
    def __init__(self, cfg: dict, config_path: Path, log=print, stop=None, review=None):
        self.cfg, self.config_path, self.log = cfg, config_path, log
        self.stop = stop or threading.Event()
        self.pag, self.clipboard, self.Image, self.ImageChops, self.ImageStat = dependencies()
        self.win = Windows()
        self.window = None
        self.review = review

    def sleep(self, seconds: float) -> None:
        if self.stop.wait(seconds):
            raise Cancelled("任务已停止。")

    def prepare(self) -> None:
        self.window = self.win.find(self.cfg["window_title"])
        if not self.window:
            self.log("启动 ChatGPT……")
            launch(self.cfg["launcher"])
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                self.sleep(0.5)
                self.window = self.win.find(self.cfg["window_title"])
                if self.window:
                    break
            else:
                raise RuntimeError("启动后未找到 ChatGPT 窗口。检查启动路径和窗口标题。")
        self.win.activate(self.window.hwnd, self.pag)
        self.sleep(1)
        self.log("已激活：" + self.window.title)

    def rect(self):
        return self.win.rect(self.window.hwnd)

    def assert_target(self):
        if self.stop.is_set():
            raise Cancelled("任务已停止。")
        self.win.assert_foreground(self.window.hwnd)
        expected = self.cfg.get("client_size")
        if expected and list(self.rect()[2:]) != list(expected):
            raise RuntimeError("ChatGPT 窗口尺寸已改变，请重新校准位置和完成标记。")

    def click(self, key):
        self.assert_target()
        left, top, width, height = self.rect()
        x, y = self.cfg["points"][key]
        if not (0 <= x < width and 0 <= y < height):
            raise RuntimeError("校准点不在 ChatGPT 窗口内，请重新校准。")
        self.pag.click(left + x, top + y)

    def shot(self):
        self.assert_target()
        return self.pag.screenshot(region=self.rect())

    def marker_path(self):
        path = Path(self.cfg["template"])
        return path if path.is_absolute() else self.config_path.parent / path

    def marker_present(self, screenshot, marker) -> bool:
        return self.marker_match(screenshot, marker) is not None

    def marker_match(self, screenshot, marker, grayscale=False):
        try:
            return self.pag.locate(marker, screenshot, confidence=float(self.cfg["confidence"]), grayscale=grayscale)
        except self.pag.ImageNotFoundException:
            return None

    def hover(self):
        point = self.cfg["points"].get("hover")
        if point:
            self.assert_target()
            left, top, _, _ = self.rect()
            self.pag.moveTo(left + point[0], top + point[1], duration=0.15)

    def content_sample(self, screenshot):
        # 排除侧栏、标题、输入框和鼠标所在的右侧空白，比较实际对话内容。
        width, height = screenshot.size
        return screenshot.crop((int(width * .23), int(height * .16),
                                int(width * .83), int(height * .86))).resize((360, 240)).convert("RGB")

    def frame_delta(self, previous, current):
        if previous is None:
            return float("inf")
        return sum(self.ImageStat.Stat(self.ImageChops.difference(previous, current)).mean) / 3

    def scroll_latest(self):
        # 滚轮必须指向正文；输入框有焦点时 Ctrl+End 只会移动输入光标。
        self.assert_target()
        left, top, width, height = self.rect()
        input_y = self.cfg["points"].get("input", [0, height])[1]
        y = max(int(height * .25), min(int(height * .5), input_y - 80))
        self.pag.moveTo(left + int(width * .88), top + y, duration=0.08)
        self.pag.scroll(-36)
        self.sleep(0.15)

    def scroll_to_bottom(self):
        previous, unchanged = None, 0
        for _ in range(12):
            self.scroll_latest()
            screenshot = self.shot()
            sample = self.content_sample(screenshot)
            unchanged = unchanged + 1 if self.frame_delta(previous, sample) < 0.6 else 0
            if unchanged >= 2:
                return screenshot
            previous = sample
        raise BottomNotStable("向下滚动后画面仍在变化，未确认到达对话底部；当前对话已保留。")

    def paste_reference(self, path: Path):
        """将本地参考图作为 Windows 图片剪贴板粘贴，不打开文件选择器。"""
        with self.Image.open(path) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="BMP")
            dib = buffer.getvalue()[14:]
        self.assert_target()
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user.OpenClipboard.argtypes = [wintypes.HWND]
        user.OpenClipboard.restype = wintypes.BOOL
        user.EmptyClipboard.restype = wintypes.BOOL
        user.CloseClipboard.restype = wintypes.BOOL
        user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user.SetClipboardData.restype = wintypes.HANDLE
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = wintypes.HANDLE
        kernel.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel.GlobalFree.argtypes = [wintypes.HANDLE]
        memory = kernel.GlobalAlloc(0x0002, len(dib))  # GMEM_MOVEABLE
        if not memory:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            address = kernel.GlobalLock(memory)
            if not address:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(address, dib, len(dib))
            finally:
                kernel.GlobalUnlock(memory)
            for _ in range(20):
                if user.OpenClipboard(self.window.hwnd):
                    break
                self.sleep(0.1)
            else:
                raise RuntimeError("无法打开图片剪贴板，请稍后重试。")
            try:
                if not user.EmptyClipboard() or not user.SetClipboardData(8, memory):  # CF_DIB
                    raise ctypes.WinError(ctypes.get_last_error())
                memory = None  # Windows 接管内存所有权。
            finally:
                user.CloseClipboard()
        finally:
            if memory:
                kernel.GlobalFree(memory)
        self.click("input")
        before = self.shot()
        self.pag.hotkey("ctrl", "v")
        # 等附件缩略图出现并稳定；未出现时不发送空附件任务。
        deadline = time.monotonic() + 60
        previous, stable_start = None, None
        def composer(frame):
            w, h = frame.size
            return frame.crop((int(w * .20), int(h * .68), int(w * .88), h - 8)).resize((400, 160)).convert("RGB")
        baseline = composer(before)
        while time.monotonic() < deadline:
            self.sleep(0.4)
            current = composer(self.shot())
            if self.frame_delta(baseline, current) > 0.4 and self.frame_delta(previous, current) < 0.4:
                stable_start = stable_start if stable_start is not None else time.monotonic()
                if time.monotonic() - stable_start >= 2:
                    self.log("参考图片已粘贴，继续输入修改要求……")
                    return
            else:
                stable_start = None
            previous = current
        raise RuntimeError("参考图片粘贴后未出现稳定的附件预览，当前对话已保留。")

    def send(self, timeout=8):
        # 输入多行提示词后，输入框高度可能改变；优先按按钮图案定位。
        path = self.config_path.parent / "send_marker.png"
        if not path.is_file():
            raise RuntimeError("请重新校准发送按钮，以保存按钮图案。")
        with self.Image.open(path) as image:
            marker = image.convert("RGB")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screenshot = self.shot()
            try:
                match = self.pag.locate(marker, screenshot, confidence=float(self.cfg["confidence"]), grayscale=False)
            except self.pag.ImageNotFoundException:
                match = None
            if match:
                self.assert_target()
                left, top, _, _ = self.rect()
                x, y = self.pag.center(match)
                self.pag.click(left + x, top + y)
                return
            self.sleep(0.4)
        raise RuntimeError("未找到可用的发送按钮，请在输入框有文字时重新校准发送按钮。")

    def new_chat(self, marker) -> None:
        self.click("new_chat")
        self.sleep(2)
        self.click("input")
        self.pag.hotkey("ctrl", "a")
        self.pag.press("backspace")
        self.sleep(0.5)
        # 此检查也防止旧对话的完成按钮造成下一轮误判。
        if self.marker_present(self.shot(), marker):
            raise RuntimeError("新对话中仍匹配到完成标记：检查新对话位置，或重选更独特的完成标记。")

    def wait_done(self, marker, output=None):
        started = time.monotonic()
        deadline = started + float(self.cfg["timeout"])
        review_at = started + 60
        stable_start, previous = None, None
        while time.monotonic() < deadline:
            if output is not None and self.review is not None and time.monotonic() >= review_at:
                action = self.request_download_review(output, "已等待 60 秒仍未自动识别完成，请查看截图：已生成就直接下载，未生成就继续识别。")
                if action == "retry":
                    self.log("远端确认已生成，直接右击图片下载副本……")
                    self.scroll_latest()
                    self.hover()
                    self.sleep(0.2)
                    return self.shot()
                # 远端查看期间暂停计时；继续等待重新给予完整识别时间。
                continued = time.monotonic()
                deadline = continued + float(self.cfg["timeout"])
                review_at = continued + 60
                stable_start, previous = None, None
                self.log("远端选择未生成，继续识别；60 秒后仍未完成会再次发送截图。")
            self.sleep(0.5)
            self.scroll_latest()
            self.hover()
            self.sleep(0.15)
            screenshot = self.shot()
            present = self.marker_present(screenshot, marker)
            sample = self.content_sample(screenshot)
            delta = self.frame_delta(previous, sample)
            if present and delta < 0.6 and time.monotonic() - started >= 3:
                stable_start = stable_start or time.monotonic()
                if time.monotonic() - stable_start >= float(self.cfg["stable_seconds"]):
                    self.log("图片完成标记已出现；再次滚到最底部并复核……")
                    try:
                        self.scroll_to_bottom()
                    except BottomNotStable as exc:
                        # 仍在加载/滚动时继续等待，不能提前把当前画面当成结果。
                        self.log(str(exc))
                        stable_start, previous = None, None
                        continue
                    self.hover()
                    self.sleep(0.2)
                    fresh = self.shot()
                    if (self.marker_present(fresh, marker)
                            and self.frame_delta(sample, self.content_sample(fresh)) < 0.6):
                        return fresh
                    stable_start = None
            else:
                stable_start = None
            previous = sample
        raise TimeoutError("等待图片生成超时，未确认完成；当前对话已保留。")

    def image_point(self, screenshot, marker):
        point = self.cfg["points"].get("hover")
        if not point:
            match = self.marker_match(screenshot, marker)
            if not match:
                raise RuntimeError("未找到图片完成标记，无法确定右击位置；当前对话已保留。")
            x, y = self.pag.center(match)
            # 默认完成标记为图片左下的“编辑”；向上避开按钮，右击图片本身。
            point = (x, y - 80)
        width, height = screenshot.size
        x, y = point
        if not (0 <= x < width and int(height * .16) <= y < int(height * .88)):
            raise RuntimeError("图片右击位置不在可见正文内，请滚到底部后重新记录“图片位置”。")
        return int(x), int(y)

    def open_image_menu(self, screenshot, marker):
        x, y = self.image_point(screenshot, marker)
        self.assert_target()
        left, top, _, _ = self.rect()
        self.pag.click(left + x, top + y, button="right")
        self.sleep(0.6)
        return x, y

    def click_download_copy(self, point):
        with self.Image.open(self.config_path.parent / "download_menu.png") as image:
            menu_marker = image.convert("RGB")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            screenshot = self.shot()
            x, y = point
            # 只搜索右击位置附近的菜单，允许菜单在窗口底部向上展开。
            box = (max(0, x - 320), max(0, y - 280),
                   min(screenshot.width, x + 340), min(screenshot.height, y + 280))
            match = self.marker_match(screenshot.crop(box), menu_marker, grayscale=True)
            if match:
                self.assert_target()
                left, top, _, _ = self.rect()
                mx, my = self.pag.center(match)
                self.pag.click(left + box[0] + mx, top + box[1] + my)
                return
            self.sleep(0.4)
        self.assert_target()
        self.pag.press("esc")
        raise DownloadMenuNotFound("右键菜单中未找到“下载副本”。")

    def wait_download(self, folder, before, output):
        deadline = time.monotonic() + float(self.cfg.get("download_timeout", 90))
        observed = {}
        dialog_handled = False
        while time.monotonic() < deadline:
            self.sleep(0.5)
            if not dialog_handled:
                dialog_handled = self.save_download_dialog(folder)
            now = time.monotonic()
            changed = {path: state for path, state in image_files(folder).items()
                       if state[0] > 0 and before.get(path) != state}
            if len(changed) > 1:
                raise RuntimeError("下载目录同时出现多张新图片，无法确定本次副本；当前对话已保留。")
            for path, state in changed.items():
                previous, since = observed.get(path, (None, now))
                if previous != state:
                    observed[path] = (state, now)
                    continue
                if now - since < 2 or any(Path(str(path) + suffix).exists()
                                          for suffix in (".crdownload", ".part", ".tmp")):
                    continue
                try:
                    with self.Image.open(path) as image:
                        image.load()
                        image_format = image.format
                    extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image_format)
                    if not extension:
                        continue
                    destination = output / ("chatgpt_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + extension)
                    temporary = destination.with_suffix(destination.suffix + ".part")
                    try:
                        shutil.copyfile(path, temporary)
                        if temporary.stat().st_size != state[0] or path.stat().st_mtime_ns != state[1]:
                            observed.pop(path, None)
                            continue
                        temporary.replace(destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                except OSError:
                    continue
                self.log("原图副本已保存：" + str(destination))
                return destination
            observed = {path: value for path, value in observed.items() if path in changed}
        raise TimeoutError("下载副本超时：下载目录中没有完整的新图片。请确认填写的下载文件夹与 ChatGPT 实际下载位置一致；当前对话已保留。")

    def save_download_dialog(self, folder):
        hwnd = self.win.save_dialog(self.window.hwnd)
        if not hwnd:
            return False
        if self.stop.is_set():
            raise Cancelled("任务已停止。")
        self.win.assert_foreground(hwnd)
        filename = folder / ("chatgpt_download_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png")
        self.log("填写另存为路径：" + str(filename))
        self.clipboard.copy(str(filename))
        self.pag.hotkey("alt", "n")
        self.pag.hotkey("ctrl", "a")
        self.pag.hotkey("ctrl", "v")
        self.sleep(0.3)
        self.win.assert_foreground(hwnd)
        self.pag.press("enter")
        return True

    def download_image(self, screenshot, marker, output):
        folder = download_folder(self.cfg, self.config_path)
        need_wait = False
        while True:
            if need_wait:
                self.log("远端选择继续等待，恢复生成完成识别……")
                try:
                    screenshot = self.wait_done(marker, output=output)
                except TimeoutError:
                    action = self.request_download_review(output, "等待生成超时，请查看当前截图后选择重试下载或继续等待。")
                    need_wait = action == "wait"
                    screenshot = self.shot()
                    continue
                need_wait = False
            before = image_files(folder)
            for attempt in range(2):
                self.log("右击图片，点击“下载副本”……" if attempt == 0 else "未找到下载菜单，重新右击一次……")
                point = self.open_image_menu(screenshot, marker)
                try:
                    self.click_download_copy(point)
                    break
                except DownloadMenuNotFound:
                    screenshot = self.shot()
            else:
                action = self.request_download_review(output, "两次右击都未找到下载菜单，请查看截图：已生成则重试下载，未生成则继续等待。")
                need_wait = action == "wait"
                if not need_wait:
                    self.scroll_to_bottom()
                    self.hover()
                    self.sleep(0.2)
                screenshot = self.shot()
                continue
            self.log("等待原图下载完成：" + str(folder))
            return self.wait_download(folder, before, output)

    def request_download_review(self, output, message):
        self.assert_target()
        self.pag.press("esc")
        self.sleep(0.15)
        preview = output / ("review_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png")
        self.shot().save(preview)
        self.log(message)
        callback = getattr(self, "review", None)
        if callback is None:
            raise DownloadMenuNotFound(message + " 截图已保存：" + str(preview) + "；远端确认功能请通过 server.py 使用。")
        action = callback(preview, message)
        if action == "end":
            raise NotGenerated("用户确认：本次未生成图片。请检查提示词后重新提交。")
        if action not in {"retry", "wait"}:
            raise Cancelled("远端确认已停止。")
        # 暂停期间不操控窗口；收到远端选择后重新激活并读取当前画面。
        self.win.activate(self.window.hwnd, self.pag)
        self.sleep(0.2)
        self.assert_target()
        return action

    def run(self, prompt: str, image_path: Path | None = None, image_paths=None) -> Path:
        references = tuple(image_paths) if image_paths is not None else ((image_path,) if image_path is not None else ())
        if len(references) > 3:
            raise ValueError("每个任务最多 3 张参考图片。")
        if not prompt.strip():
            raise ValueError("请输入画图提示词。")
        for key in ("new_chat", "input", "send"):
            if key not in self.cfg["points"]:
                raise RuntimeError("请先在图形界面校准新对话、输入框和发送按钮。")
        if not self.cfg.get("client_size") or not self.marker_path().is_file():
            raise RuntimeError("请先在图形界面截取完成标记。")
        if not (self.config_path.parent / "download_menu.png").is_file():
            raise RuntimeError("缺少 download_menu.png，请在界面截取“下载菜单”标记。")
        download_folder(self.cfg, self.config_path)
        if not 10 <= float(self.cfg.get("download_timeout", 90)) <= 3600:
            raise ValueError("下载超时应为 10 至 3600 秒。")
        timeout = float(self.cfg["timeout"])
        confidence = float(self.cfg["confidence"])
        if timeout < 20 or not 0.5 <= confidence < 1 or float(self.cfg["stable_seconds"]) < 1:
            raise ValueError("超时应至少 20 秒，匹配阈值为 0.5 至小于 1，稳定时间至少 1 秒。")
        output = Path(self.cfg["output_dir"]).expanduser()
        if not output.is_absolute():
            output = self.config_path.parent / output
        output.mkdir(parents=True, exist_ok=True)
        with self.Image.open(self.marker_path()) as image:
            marker = image.convert("RGB")
        self.prepare()
        self.log("打开新对话……")
        self.new_chat(marker)
        for index, reference in enumerate(references, 1):
            self.log(f"粘贴参考图片 {index}/{len(references)}……")
            self.paste_reference(Path(reference))
        self.log("粘贴提示词并点击发送……")
        self.clipboard.copy(prompt)
        self.click("input")
        self.pag.hotkey("ctrl", "v")
        self.sleep(0.8)
        self.send(timeout=60) if references else self.send()
        self.log("持续滚到最新内容，等待图片专属完成标记并确认画面稳定……")
        try:
            screenshot = self.wait_done(marker, output=output)
        except TimeoutError:
            diagnostic = output / ("timeout_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png")
            self.shot().save(diagnostic)
            self.log("超时截图：" + str(diagnostic))
            raise
        path = output / ("screenshot_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png")
        screenshot.save(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("截图未成功保存。")
        self.log("截图已保存：" + str(path))
        # 网页接收下载的原图；截图仅作为本机操作记录保留。
        path = self.download_image(screenshot, marker, output)
        self.log("打开空白新对话，为下一次操作准备……")
        self.new_chat(marker)
        self.log("本次完成。")
        return path

    def calibration_snapshot(self):
        self.prepare()
        self.assert_target()
        left, top, width, height = self.rect()
        self.pag.moveTo(left + width // 2, top + 4)
        self.sleep(0.4)
        return self.pag.screenshot(), self.rect()


def gui(config_path):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
    from fabric_presets import FABRIC_CATALOG, make_fabric_prompt, prepare_prompt

    cfg = load_config(config_path)
    root = tk.Tk()
    root.title("ChatGPT 图片自动化")
    root.geometry("980x920")
    root.minsize(900, 850)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", padding=(10, 7))
    style.configure("TLabel", font=("Microsoft YaHei UI", 10))
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
    root.configure(bg="#f3f5f8")
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="ChatGPT 图片自动化", style="Title.TLabel").pack(anchor="w")
    ttk.Label(frame, text="输入提示词 → 等待完成 → 滚到底部 → 右击图片下载副本 → 准备下一次").pack(anchor="w", pady=(4, 14))
    settings = ttk.LabelFrame(frame, text="启动与保存", padding=10)
    settings.pack(fill="x")
    launcher_var = tk.StringVar(value=cfg["launcher"])
    output_var = tk.StringVar(value=cfg["output_dir"])
    download_var = tk.StringVar(value=cfg.get("download_dir", ""))
    timeout_var = tk.StringVar(value=str(cfg["timeout"]))
    title_var = tk.StringVar(value=cfg["window_title"])
    confidence_var = tk.StringVar(value=str(round(float(cfg["confidence"]) * 100, 2)))
    stable_var = tk.StringVar(value=str(cfg["stable_seconds"]))
    settings.columnconfigure(1, weight=1)
    ttk.Label(settings, text="ChatGPT 路径").grid(row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Entry(settings, textvariable=launcher_var).grid(row=0, column=1, sticky="ew", pady=3)
    ttk.Button(settings, text="浏览", command=lambda: browse_launcher()).grid(row=0, column=2, padx=(6, 0))
    ttk.Label(settings, text="留空自动查找；也可填 .lnk / .exe 路径或 AUMID").grid(row=1, column=1, sticky="w")
    ttk.Label(settings, text="结果保存文件夹").grid(row=2, column=0, sticky="w")
    ttk.Entry(settings, textvariable=output_var).grid(row=2, column=1, sticky="ew", pady=3)
    ttk.Button(settings, text="浏览", command=lambda: browse_output()).grid(row=2, column=2, padx=(6, 0))
    ttk.Label(settings, text="下载文件夹").grid(row=3, column=0, sticky="w")
    ttk.Entry(settings, textvariable=download_var).grid(row=3, column=1, sticky="ew", pady=3)
    ttk.Button(settings, text="浏览", command=lambda: browse_download()).grid(row=3, column=2, padx=(6, 0))
    ttk.Label(settings, text="留空读取系统下载目录；手填时须与 ChatGPT 实际下载位置一致").grid(row=4, column=1, sticky="w")
    advanced = ttk.Frame(settings)
    advanced.grid(row=5, column=1, sticky="w", pady=(6, 0))
    ttk.Label(advanced, text="超时（秒）").pack(side="left")
    ttk.Entry(advanced, textvariable=timeout_var, width=8).pack(side="left", padx=(4, 18))
    ttk.Label(advanced, text="窗口标题匹配").pack(side="left")
    ttk.Entry(advanced, textvariable=title_var, width=24).pack(side="left", padx=4)
    detection = ttk.Frame(settings)
    detection.grid(row=6, column=1, sticky="w", pady=(6, 0))
    ttk.Label(detection, text="匹配阈值（%）").pack(side="left")
    ttk.Entry(detection, textvariable=confidence_var, width=6).pack(side="left", padx=(4, 18))
    ttk.Label(detection, text="完成稳定时间（秒）").pack(side="left")
    ttk.Entry(detection, textvariable=stable_var, width=6).pack(side="left", padx=4)
    calibration = ttk.LabelFrame(frame, text="首次校准（在截图上点击位置，保存后可重复使用）", padding=10)
    calibration.pack(fill="x", pady=12)
    row = ttk.Frame(calibration)
    row.pack(fill="x")
    buttons = []
    busy, stop, events = [False], threading.Event(), queue.Queue()
    selecting = [False]

    def log(text):
        events.put(("log", text))

    def sync():
        cfg.update(launcher=launcher_var.get().strip(), output_dir=output_var.get().strip(),
                   timeout=float(timeout_var.get()), window_title=title_var.get().strip(),
                   download_dir=download_var.get().strip(), confidence=float(confidence_var.get()) / 100,
                   stable_seconds=float(stable_var.get()))
        if not 0.5 <= cfg["confidence"] < 1 or cfg["stable_seconds"] < 1:
            raise ValueError("匹配阈值应为 50 至小于 100%，完成稳定时间至少 1 秒。")
        if not cfg["output_dir"] or not cfg["window_title"] or cfg["timeout"] < 20:
            raise ValueError("请填写保存目录和窗口标题；超时至少 20 秒。")
        re.compile(cfg["window_title"])
        save_config(config_path, cfg)

    def status():
        names = {"new_chat": "新对话", "input": "输入框", "send": "发送", "hover": "图片位置"}
        recorded = "、".join(names[key] for key in names if key in cfg["points"]) or "无"
        marker = Path(cfg["template"])
        if not marker.is_absolute():
            marker = config_path.parent / marker
        menu = (config_path.parent / "download_menu.png").is_file()
        status_var.set(f"已记录：{recorded}；完成标记：{'已保存' if marker.is_file() else '未设置'}；下载菜单：{'已保存' if menu else '未设置'}")

    def set_busy(value):
        busy[0] = value
        for button in buttons:
            button.configure(state="disabled" if value else "normal")
        stop_button.configure(state="normal" if value else "disabled")

    def start_job(action):
        if busy[0]:
            return
        try:
            sync()
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc), parent=root)
            return
        stop.clear()
        set_busy(True)
        root.iconify()

        def worker():
            try:
                auto = Automation(cfg, config_path, log, stop)
                action(auto)
            except Exception as exc:
                events.put(("error", str(exc)))
            finally:
                events.put(("finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def calibrate(key, label):
        hint = "\n发送按钮校准前，请在空白新对话输入少量文字，使按钮可用。" if key == "send" else ""
        if key == "hover":
            hint = "\n请先打开已完成图片，脚本会滚到底部；点击图片内容内部，避开编辑/分享按钮。"
        if not messagebox.askokcancel("校准 " + label,
                "脚本将显示最大化的 ChatGPT 截图。\n\n在截图上点击“" + label +
                "”的位置即可保存。\n点击不会触发真实 ChatGPT 的按钮。" + hint, parent=root):
            return

        def capture(auto):
            if key == "hover":
                auto.prepare()
                auto.scroll_to_bottom()
                screenshot, rect = auto.pag.screenshot(), auto.rect()
            else:
                screenshot, rect = auto.calibration_snapshot()
            events.put(("point", (screenshot, rect, key, label)))

        start_job(capture)

    def template(kind="done"):
        title = "截取下载菜单" if kind == "menu" else "截取完成标记"
        hint = ("脚本会滚到底部并右击图片，然后显示菜单截图。\n"
                "拖框截取“下载副本”四个字及少量背景，按 Esc 可取消。"
                if kind == "menu" else
                "脚本会先把对话滚到最底部，再显示截图。\n"
                "拖框截取图片左下角“编辑”按钮文字及少量背景。\n"
                "不要选复制/点赞工具栏、居中的滚动箭头、图片内容或加载提示。\n"
                "若按钮需要悬停才出现，先记录“图片位置”。")
        if not messagebox.askokcancel(title,
                "请先在 ChatGPT 中打开一张已经生成完成的图片。\n\n"
                + hint, parent=root):
            return

        def capture(auto):
            auto.prepare()
            auto.assert_target()
            auto.scroll_to_bottom()
            auto.hover()
            auto.sleep(0.8)
            if kind == "menu":
                if not auto.marker_path().is_file() and not cfg["points"].get("hover"):
                    raise RuntimeError("请先截取完成标记，或记录“图片位置”。")
                marker = None
                if auto.marker_path().is_file():
                    with auto.Image.open(auto.marker_path()) as image:
                        marker = image.convert("RGB")
                auto.open_image_menu(auto.shot(), marker)
                screenshot, rect = auto.pag.screenshot(), auto.rect()
                auto.assert_target()
                auto.pag.press("esc")
            else:
                screenshot, rect = auto.pag.screenshot(), auto.rect()
            events.put(("template", (screenshot, rect, kind)))

        start_job(capture)

    for key, label in (("new_chat", "新对话"), ("input", "输入框"), ("send", "发送"), ("hover", "图片位置")):
        button = ttk.Button(row, text=label, command=lambda k=key, n=label: calibrate(k, n))
        button.pack(side="left", padx=(0, 5))
        buttons.append(button)
    button = ttk.Button(row, text="截取完成标记", command=template)
    button.pack(side="left", padx=(0, 5))
    buttons.append(button)
    button = ttk.Button(row, text="下载菜单", command=lambda: template("menu"))
    button.pack(side="left", padx=(0, 5))
    buttons.append(button)

    def reset():
        cfg["points"], cfg["client_size"] = {}, None
        cfg["template"] = "done_marker.png"
        save_config(config_path, cfg)
        status()
        log("坐标已重置；请重新记录坐标并覆盖完成标记。")

    button = ttk.Button(row, text="重置校准", command=reset)
    button.pack(side="left")
    buttons.append(button)
    status_var = tk.StringVar()
    ttk.Label(calibration, textvariable=status_var).pack(anchor="w", pady=(8, 0))
    preset_row = ttk.Frame(frame)
    preset_row.pack(fill="x", pady=(0, 8))
    ttk.Label(preset_row, text="布料预设").pack(side="left", padx=(0, 8))
    tool_select = ttk.Combobox(preset_row, values=[item["label"] for item in FABRIC_CATALOG["tools"]], state="readonly", width=22)
    tool_select.pack(side="left", padx=(0, 8))
    tool_select.current(next(i for i, item in enumerate(FABRIC_CATALOG["tools"]) if item["id"] == "fabric"))
    layout_select = ttk.Combobox(preset_row, values=[item["label"] for item in FABRIC_CATALOG["layouts"]], state="readonly", width=24)
    layout_select.pack(side="left", padx=(0, 8))
    layout_select.current(0)
    theme_select = ttk.Combobox(preset_row, values=[item["label"] for item in FABRIC_CATALOG["themes"]], state="readonly", width=16)
    theme_select.pack(side="left", padx=(0, 8))
    theme_select.current(0)
    def fill_fabric_preset():
        if busy[0]:
            return
        text = make_fabric_prompt(FABRIC_CATALOG["layouts"][layout_select.current()]["id"], FABRIC_CATALOG["themes"][theme_select.current()]["id"], tool=FABRIC_CATALOG["tools"][tool_select.current()]["id"], has_reference=bool(image_var.get().strip()))
        prompt.delete("1.0", "end")
        prompt.insert("1.0", text)
    preset_button = ttk.Button(preset_row, text="填入预设", command=fill_fabric_preset)
    preset_button.pack(side="left")
    buttons.append(preset_button)
    ttk.Label(frame, text="布料花型提示词（可继续修改）").pack(anchor="w")
    prompt = ScrolledText(frame, height=5, wrap="word", font=("Microsoft YaHei UI", 11))
    prompt.pack(fill="x", pady=(4, 10))
    prompt.insert("1.0", make_fabric_prompt())
    reference_row = ttk.Frame(frame)
    reference_row.pack(fill="x", pady=(0, 8))
    image_var = tk.StringVar()
    ttk.Label(reference_row, text="参考图片").pack(side="left", padx=(0, 8))
    ttk.Entry(reference_row, textvariable=image_var).pack(side="left", fill="x", expand=True)
    def browse_reference():
        path = filedialog.askopenfilename(parent=root, filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if path:
            image_var.set(path)
    ttk.Button(reference_row, text="选择图片", command=browse_reference).pack(side="left", padx=6)
    ttk.Button(reference_row, text="清除", command=lambda: image_var.set("")).pack(side="left")
    controls = ttk.Frame(frame)
    controls.pack(fill="x")

    def run():
        text = prompt.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showerror("提示词为空", "请输入画图提示词。", parent=root)
            return
        reference = image_var.get().strip()
        tool = FABRIC_CATALOG["tools"][tool_select.current()]
        if tool["requires_reference"] and not reference:
            messagebox.showerror("需要参考图片", "此工具预设需要先选择参考图片。", parent=root)
            return
        if reference and not Path(reference).is_file():
            messagebox.showerror("图片不存在", "请重新选择参考图片。", parent=root)
            return
        text = prepare_prompt(text, tool["id"], bool(reference))
        start_job(lambda auto: auto.run(text, image_path=Path(reference) if reference else None))

    for text, command in (("开始生成并下载", run), ("打开 ChatGPT", lambda: start_job(lambda a: a.prepare()))):
        button = ttk.Button(controls, text=text, command=command)
        button.pack(side="left", padx=(0, 8))
        buttons.append(button)
    stop_button = ttk.Button(controls, text="停止", command=stop.set, state="disabled")
    stop_button.pack(side="left", padx=(0, 8))
    button = ttk.Button(controls, text="打开结果文件夹", command=lambda: open_output())
    button.pack(side="left")
    buttons.append(button)
    ttk.Label(frame, text="运行记录（任务期间自动收起窗口；停止时可从任务栏恢复此窗口）").pack(anchor="w", pady=(14, 4))
    logs = ScrolledText(frame, height=9, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
    logs.pack(fill="both", expand=True)

    def browse_launcher():
        value = filedialog.askopenfilename(parent=root, filetypes=[("程序或快捷方式", "*.exe *.lnk"), ("所有文件", "*.*")])
        if value:
            launcher_var.set(value)

    def browse_output():
        value = filedialog.askdirectory(parent=root)
        if value:
            output_var.set(value)

    def browse_download():
        value = filedialog.askdirectory(parent=root)
        if value:
            download_var.set(value)

    def open_output():
        try:
            sync()
            path = Path(cfg["output_dir"])
            if not path.is_absolute():
                path = config_path.parent / path
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path.resolve()))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=root)

    def select_template(screenshot, client_rect, kind="done"):
        from PIL import ImageTk, ImageStat
        overlay = tk.Toplevel(root)
        overlay.overrideredirect(True)
        overlay.geometry(f"{screenshot.width}x{screenshot.height}+0+0")
        overlay.attributes("-topmost", True)
        canvas = tk.Canvas(overlay, highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        photo = ImageTk.PhotoImage(screenshot)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image = photo
        canvas.create_text(20, 20, anchor="nw", fill="#ff4545", font=("Microsoft YaHei UI", 14, "bold"),
                           text="拖框选择“下载副本”文字；Esc 取消" if kind == "menu" else "拖框选择图片左下“编辑”文字；Esc 取消")
        origin, selection = [None], [None]

        def begin(event):
            origin[0] = (event.x, event.y)
            if selection[0]:
                canvas.delete(selection[0])
            selection[0] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#ff4545", width=2)

        def drag(event):
            if origin[0]:
                canvas.coords(selection[0], *origin[0], event.x, event.y)

        def finish(event):
            if not origin[0]:
                return
            x0, y0 = origin[0]
            box = (min(x0, event.x), min(y0, event.y), max(x0, event.x), max(y0, event.y))
            left, top, width, height = client_rect
            if not (left <= box[0] < box[2] <= left + width and top <= box[1] < box[3] <= top + height):
                messagebox.showerror("区域错误", "请选择 ChatGPT 窗口内的按钮。", parent=overlay)
                return
            patch = screenshot.crop(box)
            if patch.width < 12 or patch.height < 12 or max(ImageStat.Stat(patch.convert("RGB")).stddev) < 8:
                messagebox.showerror("标记过小或过于单一", "请选择完整且有明显图案的按钮，包含少量周边背景。", parent=overlay)
                return
            path = config_path.parent / ("download_menu.png" if kind == "menu" else "done_marker.png")
            patch.save(path)
            if kind != "menu":
                cfg["template"] = path.name
            cfg["client_size"] = [width, height]
            save_config(config_path, cfg)
            overlay.destroy()
            status()
            log(("下载菜单已保存：" if kind == "menu" else "完成标记已保存：") + str(path))
            end_selection()

        def cancel():
            overlay.destroy()
            end_selection()

        canvas.bind("<ButtonPress-1>", begin)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", finish)
        overlay.bind("<Escape>", lambda _: cancel())
        overlay.protocol("WM_DELETE_WINDOW", cancel)
        cancel_button = tk.Button(overlay, text="取消选择", command=cancel)
        cancel_button.place(relx=1, x=-20, y=15, anchor="ne")
        overlay.focus_force()

    def end_selection():
        selecting[0] = False
        set_busy(False)
        status()
        root.deiconify()

    def select_point(screenshot, client_rect, key, label):
        def save(point):
            save_point_selection(cfg, config_path, key, screenshot, client_rect, point)
            log(f"已记录{label}：{cfg['points'][key]}")
        PointPicker(root, screenshot, label, save, end_selection)

    def drain():
        pending_selection = None
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                logs.configure(state="normal")
                logs.insert("end", datetime.now().strftime("%H:%M:%S ") + value + "\n")
                logs.see("end")
                logs.configure(state="disabled")
            elif kind == "error":
                root.deiconify()
                messagebox.showerror("任务未完成", value, parent=root)
                log(value)
            elif kind in {"template", "point"}:
                selecting[0] = True
                pending_selection = (kind, value)
            elif kind == "finished":
                save_config(config_path, cfg)
                status()
                if not selecting[0]:
                    set_busy(False)
                    root.deiconify()
        if pending_selection:
            try:
                kind, value = pending_selection
                (select_point if kind == "point" else select_template)(*value)
            except Exception as exc:
                end_selection()
                messagebox.showerror("选择界面未打开", str(exc), parent=root)
        root.after(100, drain)

    def close():
        if busy[0]:
            stop.set()
            log("已请求停止；任务结束后可关闭窗口。")
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    status()
    log("首次使用：校准三个位置，截取图片左下“编辑”完成标记；下载菜单已附带，必要时可重选。")
    root.after(100, drain)
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatGPT 桌面图片 UI 自动化（默认打开图形界面）")
    parser.add_argument("--prompt", help="使用已保存的校准配置生成图片并下载副本")
    parser.add_argument("--prompt-file", type=Path, help="读取 UTF-8 提示词文件")
    parser.add_argument("--image", type=Path, help="与提示词一起提交的本地参考图片")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="校准配置路径")
    args = parser.parse_args()
    if args.prompt is not None and args.prompt_file is not None:
        parser.error("--prompt 和 --prompt-file 只能选择一个")
    if args.image is not None and args.prompt is None and args.prompt_file is None:
        parser.error("--image 需要同时提供 --prompt 或 --prompt-file")
    try:
        enable_dpi()
        if args.prompt is not None or args.prompt_file is not None:
            prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8-sig")
            cfg = load_config(args.config.resolve())
            Automation(cfg, args.config.resolve()).run(prompt, image_path=args.image)
        else:
            dependencies()
            gui(args.config.resolve())
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print("运行失败：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
