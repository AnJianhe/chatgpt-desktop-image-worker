"""Cloudflare Quick Tunnel watchdog. Python standard library only, VM only."""
from __future__ import annotations

import argparse
import ctypes
from http.client import HTTPException
import json
import logging
import math
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
# Provisioning errors contain api.trycloudflare.com/tunnel. That control-plane
# host must never become a visitor address, even when parsing the final error.
URL_RE = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b(?![\w.:-])")
RATE_LIMIT_RE = re.compile(
    r"(?:quick tunnel provisioning failed|failed to request quick tunnel).*\b(?:status(?: code)?\s*[:=]?\s*429|429 Too Many Requests)\b", re.I
)
CONNECTION_RE = re.compile(
    r"^cloudflared_tunnel_ha_connections(?:\{[^\n]*\})?\s+([\d.eE+-]+)(?:\s|$)", re.M
)
DIRECT_HTTP = build_opener(ProxyHandler({}))
PUBLIC_HTTP = build_opener()
NETWORK_ERRORS = (OSError, HTTPException, ValueError, TypeError)


def require_vm():
    if sys.platform != "win32":
        raise RuntimeError("此脚本只在 Windows 虚拟机内运行。")
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                       r"HARDWARE\DESCRIPTION\System\BIOS") as key:
        model = winreg.QueryValueEx(key, "SystemProductName")[0]
    if "virtual machine" not in model.lower():
        raise RuntimeError("请在 Hyper-V 虚拟机内运行，不要在本机运行。")


def acquire_mutex():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, r"Local\ChatGPTImageTunnelWatchdog")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError("隧道看门狗已经在运行，无需重复启动。")
    return kernel, handle


def get_text(url, timeout=2):
    request = Request(url, headers={"User-Agent": "FabricTunnelWatchdog/10", "Cache-Control": "no-cache"})
    # Loopback always bypasses proxies; public checks respect the VM's configured
    # proxy, as its browser does. Never route local metrics through an HTTP proxy.
    opener = DIRECT_HTTP if urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"} else PUBLIC_HTTP
    with opener.open(request, timeout=timeout) as response:
        return response.read(1024 * 1024).decode("utf-8", errors="replace")


def active_connections(metrics_text):
    values = CONNECTION_RE.findall(metrics_text)
    if not values:
        return None
    counts = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0 for value in counts):
        return None
    total = sum(counts)
    return total if math.isfinite(total) else None


def local_health(origin):
    try:
        return json.loads(get_text(origin.rstrip("/") + "/health")).get("status") == "ok"
    except NETWORK_ERRORS:
        return False


def local_heartbeat(origin, url):
    query = urlencode({"host": urlsplit(url).hostname or ""})
    try:
        data = json.loads(get_text(origin.rstrip("/") + "/api/tunnel-heartbeat?" + query))
        if isinstance(data, dict) and data.get("status") == "ok" and data.get("worker_alive") is True and data.get("server_id"):
            return data
    except NETWORK_ERRORS:
        pass
    return None


def probe_public(url, server_id):
    """Verify a fresh packet travelled through this URL to the current server."""
    nonce = uuid.uuid4().hex
    try:
        data = json.loads(get_text(url.rstrip("/") + "/api/tunnel-heartbeat?" + urlencode({"nonce": nonce}), timeout=3))
        if isinstance(data, dict) and data.get("status") == "ok" and data.get("server_id") == server_id and data.get("nonce") == nonce:
            return True, "公网心跳正常"
        return False, "公网未返回当前服务的有效心跳"
    except HTTPError as error:
        # Exceptions raised while reading an HTTPError body need their own catch.
        try:
            body = error.read(65536).decode("utf-8", errors="replace")
            code = re.search(r"\b(1016|1033)\b", body)
            return False, f"Cloudflare {code.group(1)}" if code else f"公网 HTTP {error.code}"
        except NETWORK_ERRORS:
            return False, "公网错误页读取超时或中断"
        finally:
            try:
                error.close()
            except NETWORK_ERRORS:
                pass
    except NETWORK_ERRORS as error:
        return False, f"公网心跳失败：{type(error).__name__}：{str(error)[:200]}"


class ConnectionPolicy:
    """Pure decision logic, separate from subprocess/network operations."""
    def __init__(self, started_at, startup_grace=180, disconnect_seconds=30):
        self.started_at = started_at
        self.startup_grace = startup_grace
        self.disconnect_seconds = disconnect_seconds
        self.ever_connected = False
        self.failed_at = None

    def observe(self, now, connected):
        if connected is None:
            # A failed/missing metrics check is unknown, not proof of an outage.
            # Require uninterrupted confirmed failures for the disconnect timer.
            self.failed_at = None
            return False
        if connected:
            self.ever_connected = True
            self.failed_at = None
            return False
        if not self.ever_connected:
            return now - self.started_at >= self.startup_grace
        if self.failed_at is None:
            self.failed_at = now
        return now - self.failed_at >= self.disconnect_seconds


class HeartbeatPolicy:
    """Track missing success packets for diagnosis, never as a restart trigger."""
    def __init__(self, started_at, grace_seconds=30):
        self.started_at = started_at
        self.grace_seconds = grace_seconds
        self.last_success = None

    def observe(self, now, probe_ok=False, browser_age=None):
        if probe_ok:
            self.last_success = now
        if isinstance(browser_age, (int, float)) and not isinstance(browser_age, bool) and math.isfinite(browser_age) and 0 <= browser_age <= 15:
            timestamp = now - browser_age
            self.last_success = max(self.last_success if self.last_success is not None else timestamp, timestamp)
        reference = self.last_success if self.last_success is not None else self.started_at
        return now - reference >= self.grace_seconds

    def age(self, now):
        return None if self.last_success is None else max(0, now - self.last_success)

    def healthy(self, now):
        age = self.age(now)
        return age is not None and age <= 15


class RestartTimer:
    def __init__(self, started_at, seconds=0):
        self.seconds = seconds
        self.deadline = started_at + seconds if seconds > 0 else None

    def due(self, now):
        return self.deadline is not None and now >= self.deadline

    def remaining(self, now):
        return None if self.deadline is None else max(0, self.deadline - now)

    def pause(self, seconds):
        remaining = self.remaining(time.monotonic())
        time.sleep(seconds if remaining is None else min(seconds, remaining))


class RateLimitCooldown:
    """Persist provisioning cooldown across scheduled and manual restarts."""
    def __init__(self, path):
        self.path = path
        self.until, self.delay = 0.0, 0
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            until, delay = data["retry_at"], data["delay_seconds"]
            if type(until) not in (int, float) or type(delay) not in (int, float) or not math.isfinite(until) or not math.isfinite(delay) or until < 0 or delay < 0:
                raise ValueError("Invalid cooldown")
            self.until = min(until, time.time() + 1800)
            self.delay = min(1800, int(delay))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            logging.warning("无法读取限流冷却记录，将在下次 429 后重新建立记录。")

    def remaining(self):
        return max(0.0, self.until - time.time())

    def record(self):
        self.delay = min(1800, max(300, self.delay * 2))
        self.until = time.time() + self.delay
        try:
            write_json(self.path, {"retry_at": self.until, "delay_seconds": self.delay})
        except OSError as error:
            logging.warning("限流冷却记录写入失败，本进程仍会等待：%s", error)

    def clear(self):
        self.until, self.delay = 0.0, 0
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            logging.warning("限流冷却记录清理失败：%s", error)


def write_json(path, value):
    # Unique temp files prevent collisions. Keep atomic replacement; never truncate
    # the live JSON while the link publisher is reading it.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
    try:
        delays = (0.05, 0.1, 0.2, 0.3, 0.5)
        for attempt in range(len(delays) + 1):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == len(delays):
                    raise
                time.sleep(delays[attempt])
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def pump_output(pipe, lines):
    try:
        for line in iter(pipe.readline, ""):
            # Drop overflow rather than allowing logs to consume unbounded RAM.
            try:
                lines.put_nowait(line.rstrip())
            except queue.Full:
                pass
    finally:
        pipe.close()


def stop_child(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def create_child_job():
    """Windows closes child processes even if the watchdog window is closed."""
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling_class", wintypes.DWORD)]

    class IOCounts(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IOCounts),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel.CreateJobObjectW.restype = ctypes.c_void_p
    kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    return kernel, handle


def configure_startup(remove=False):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if remove:
            try:
                winreg.DeleteValue(key, "ChatGPTTunnelWatchdog")
            except FileNotFoundError:
                pass
            print("已移除虚拟机内隧道看门狗的登录自启动。")
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            if not pythonw.is_file():
                raise RuntimeError("没有找到 pythonw.exe。")
            command = f'"{pythonw}" "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, "ChatGPTTunnelWatchdog", 0, winreg.REG_SZ, command)
            print("已设置虚拟机用户登录自启动；本次未启动隧道。")


def run_watchdog(args):
    restart_timer = RestartTimer(time.monotonic(), getattr(args, "self_restart_seconds", 0))
    protocol = getattr(args, "protocol", "auto")
    scheduled_restart = False
    executable = Path(args.executable).resolve()
    if not executable.is_file():
        raise RuntimeError(f"没有找到 cloudflared：{executable}")
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    handlers = [RotatingFileHandler(logs / "tunnel-watchdog.log", maxBytes=2_000_000,
                                   backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
    cooldown = RateLimitCooldown(logs / "tunnel-rate-limit.json")
    desktop = Path.home() / "Desktop"
    # Use Windows' configured Desktop path, including redirected/OneDrive desktops.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            import os
            desktop = Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
    except OSError:
        pass
    address_files = [ROOT / "公网地址.txt"]
    if desktop.is_dir():
        address_files.append(desktop / "画图公网地址.txt")
    restart_count, retry_delay = 0, 1
    process = None
    registration_confirmed = False
    job_kernel, job_handle = create_child_job()

    def status(state, url="", detail="", heartbeat_age=None,
               connection_confirmed=None, metrics_connections=None, local_service_ready=None):
        data = {"state": state, "url": url, "detail": detail,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "watchdog_pid": __import__("os").getpid(),
                "cloudflared_pid": process.pid if process and process.poll() is None else None,
                "restart_count": restart_count, "watchdog_version": 10,
                "protocol": protocol, "registration_confirmed": registration_confirmed,
                "rate_limit_remaining": math.ceil(cooldown.remaining()),
                "rate_limit_retry_at": cooldown.until,
                "heartbeat_age": heartbeat_age,
                "connection_confirmed": connection_confirmed,
                "metrics_connections": metrics_connections,
                "local_service_ready": local_service_ready,
                "self_restart_seconds": restart_timer.seconds,
                "self_restart_remaining": restart_timer.remaining(time.monotonic())}
        try:
            write_json(logs / "tunnel-status.json", data)
        except OSError as error:
            logging.warning("状态文件写入失败，继续监控隧道：%s", error)
        message = (url + "\n" if url else "当前无可用公网地址。\n") + f"状态：{state}\n{detail}\n"
        for path in address_files:
            try:
                path.write_text(message, encoding="utf-8-sig")
            except OSError as error:
                logging.warning("地址文件写入失败，继续监控隧道：%s", error)

    try:
        if restart_timer.seconds:
            logging.info("看门狗定时重启间隔：%s 秒", restart_timer.seconds)
        else:
            logging.info("看门狗定时重启已关闭；连接正常时持续保留原隧道。")
        while True:
            if restart_timer.due(time.monotonic()):
                scheduled_restart = True
                return True
            remaining = cooldown.remaining()
            if remaining > 0:
                status("启动重试中", detail=f"Cloudflare 429 限流，冷却剩余 {math.ceil(remaining)} 秒，届时自动重试。")
                restart_timer.pause(min(5, remaining))
                continue
            # A fixed metrics port also prevents accidental takeover of another tunnel.
            with socket.socket() as port_check:
                try:
                    port_check.bind(("127.0.0.1", args.metrics_port))
                except OSError as error:
                    raise RuntimeError(f"监控端口 {args.metrics_port} 已被占用，请关闭重复看门狗。") from error
            registration_confirmed = False
            command = [str(executable), "--no-autoupdate", "tunnel", "--url", args.origin,
                       "--protocol", protocol, "--edge-ip-version", "4",
                       "--metrics", f"127.0.0.1:{args.metrics_port}"]
            status("连接中", detail="正在申请新的临时公网地址。")
            logging.info("启动隧道，累计重启 %s 次", restart_count)
            try:
                process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                           errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as error:
                logging.error("隧道启动失败，将继续重试：%s", error)
                status("启动重试中", detail=str(error))
                restart_timer.pause(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                restart_count += 1
                continue
            if not job_kernel.AssignProcessToJobObject(job_handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            lines = queue.Queue(maxsize=2000)
            reader = threading.Thread(target=pump_output, args=(process.stdout, lines), daemon=True)
            reader.start()
            policy = ConnectionPolicy(time.monotonic(),
                                      startup_grace=getattr(args, "startup_grace", 180),
                                      disconnect_seconds=getattr(args, "disconnect_grace", 30))
            public_policy = None
            server_id = None
            public_detail = "正在验证公网心跳"
            url, connected_at = "", None
            next_probe = next_public_probe = 0.0
            reason = ""
            rate_limited = False
            last_diagnostic = None
            while True:
                if restart_timer.due(time.monotonic()):
                    scheduled_restart = True
                    return True
                # Check process exit before network probes (which have bounded timeouts).
                exit_code = process.poll()
                if exit_code is not None:
                    # Flush final error lines before announcing process exit.
                    reader.join(timeout=0.5)
                for _ in range(200):
                    try:
                        line = lines.get_nowait()
                    except queue.Empty:
                        break
                    logging.info("cloudflared: %s", line)
                    if RATE_LIMIT_RE.search(line):
                        rate_limited = True
                    if exit_code is None and "INF Registered tunnel connection" in line:
                        registration_confirmed = True
                    match = URL_RE.search(line)
                    if match and not url and exit_code is None:
                        url = match.group(0)
                        cooldown.clear()
                        logging.info("新的公网地址：%s", url)
                        try:
                            with (logs / "tunnel-url-history.jsonl").open("a", encoding="utf-8") as history:
                                history.write(json.dumps({"url": url, "restart_count": restart_count,
                                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
                        except OSError as error:
                            logging.warning("无法记录地址历史：%s", error)
                if rate_limited:
                    cooldown.record()
                    reason = f"Cloudflare 429 限流，等待 {cooldown.delay} 秒后重新申请"
                    break
                if exit_code is not None:
                    reason = f"隧道进程退出，退出码 {exit_code}"
                    break
                now = time.monotonic()
                if now >= next_probe:
                    try:
                        connections = active_connections(get_text(
                            f"http://127.0.0.1:{args.metrics_port}/metrics", timeout=0.5))
                        # An in-flight handshake must not consume the first-connection
                        # grace. Require a completed registration or a public packet.
                        connected = None if connections is None or (connections > 0 and not registration_confirmed) else connections > 0
                    except NETWORK_ERRORS:
                        connections, connected = None, None
                    now = time.monotonic()
                    local = local_heartbeat(args.origin, url)
                    now = local_at = time.monotonic()
                    probe_ok = False
                    heartbeat_missing = False
                    if local and url:
                        if server_id != local["server_id"]:
                            server_id = local["server_id"]
                            public_policy = None
                            next_public_probe = 0
                        if now >= next_public_probe:
                            probe_ok, public_detail = probe_public(url, server_id)
                            now = time.monotonic()
                            next_public_probe = now + 5
                        browser_age = local.get("browser_heartbeat_age")
                        if isinstance(browser_age, (int, float)) and not isinstance(browser_age, bool):
                            browser_age += now - local_at
                        recent_browser = isinstance(browser_age, (int, float)) and not isinstance(browser_age, bool) and math.isfinite(browser_age) and 0 <= browser_age <= 15
                        if public_policy is None and (connected or probe_ok or recent_browser):
                            public_policy = HeartbeatPolicy(now, policy.disconnect_seconds)
                        if public_policy is not None:
                            heartbeat_missing = public_policy.observe(now, probe_ok, browser_age)
                    else:
                        # Restarting cloudflared cannot fix a stopped/old local server.
                        public_policy = None
                        server_id = None
                    public_healthy = public_policy is not None and public_policy.healthy(now)
                    if policy.observe(now, True if public_healthy else connected):
                        reason = (f"隧道连接持续中断 {policy.disconnect_seconds} 秒" if policy.ever_connected
                                  else f"{policy.startup_grace} 秒内未建立隧道连接")
                        break
                    if connected or public_healthy:
                        if connected_at is None:
                            connected_at = now
                        if now - connected_at >= 30:
                            retry_delay = 1
                    else:
                        connected_at = None
                    if public_healthy:
                        state, detail = "可访问", "公网心跳正常"
                    elif not local:
                        state, detail = "本地服务未就绪", "本地画图服务无有效回包，请检查画图服务；缺少心跳不会触发隧道重启。"
                    elif connected is None:
                        state, detail = "原地址重连中", f"连接指标无法确认（读取失败、缺失或尚未完成注册），状态待确认；保留原隧道继续检查。{public_detail}"
                    elif connected:
                        state = "原地址重连中" if url else "等待地址"
                        detail = (f"连续 {policy.disconnect_seconds} 秒无成功心跳，" if heartbeat_missing else "") + f"隧道连接仍在；保留原地址，继续检查本地服务及网络/代理。{public_detail}"
                    else:
                        state, detail = "原地址重连中", "隧道尚未连接或连接已断开；保留原地址等待重连，只有确认持续断开才重建。"
                    diagnostic = (connected, bool(local), heartbeat_missing) if not public_healthy else None
                    if diagnostic is not None and diagnostic != last_diagnostic:
                        logging.warning("%s", detail)
                    last_diagnostic = diagnostic
                    status(state, url, detail, public_policy.age(now) if public_policy else None,
                           connection_confirmed=connected, metrics_connections=connections,
                           local_service_ready=bool(local))
                    next_probe = time.monotonic() + 2
                restart_timer.pause(0.25)
            if rate_limited:
                logging.warning("%s；冷却会保留到看门狗重启之后。", reason)
                status("启动重试中", detail=reason)
            else:
                logging.warning("%s；%s 秒后重启", reason, retry_delay)
                status("重启中", detail=reason)
            stop_child(process)
            reader.join(timeout=2)
            process = None
            restart_count += 1
            if not rate_limited:
                restart_timer.pause(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
    finally:
        try:
            if process is not None:
                stop_child(process)
            if scheduled_restart:
                logging.info("已运行 %s 秒，关闭旧隧道并完整重启看门狗。", restart_timer.seconds)
                status("重启中", detail=f"定时重启看门狗，间隔 {restart_timer.seconds} 秒。")
            else:
                status("已停止", detail="看门狗已停止。")
        finally:
            job_kernel.CloseHandle(job_handle)
            for handler in handlers:
                logging.getLogger().removeHandler(handler)
                handler.close()


def relaunch_watchdog():
    """Called only after the old child job, log files and singleton mutex close."""
    time.sleep(1)
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                            cwd=str(ROOT)).pid


def main():
    parser = argparse.ArgumentParser(description="虚拟机 Cloudflare 隧道自动重启")
    parser.add_argument("--executable", default=str(ROOT.parent / "tools" / "cloudflared-windows-amd64.exe"))
    parser.add_argument("--origin", default="http://127.0.0.1:8765")
    parser.add_argument("--metrics-port", type=int, default=20246)
    parser.add_argument("--protocol", choices=("auto", "quic", "http2"), default="auto",
                        help="隧道协议，默认 auto：优先 QUIC，必要时回退 HTTP/2")
    parser.add_argument("--startup-grace", type=float, default=180,
                        help="初次连接的等待秒数，默认 180")
    parser.add_argument("--disconnect-grace", type=float, default=30,
                        help="在原进程中等待重连的秒数，默认 30")
    parser.add_argument("--self-restart-seconds", type=int, default=0,
                        help="完整重启看门狗的间隔秒数，默认 0（关闭）；正数仅显式启用")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install-startup", action="store_true")
    group.add_argument("--remove-startup", action="store_true")
    args = parser.parse_args()
    if args.self_restart_seconds < 0:
        parser.error("看门狗重启间隔不能小于 0。")
    require_vm()
    if args.install_startup or args.remove_startup:
        configure_startup(args.remove_startup)
        return
    kernel, mutex = acquire_mutex()
    try:
        return run_watchdog(args)
    finally:
        kernel.CloseHandle(mutex)


def run_program():
    if main():
        return relaunch_watchdog()


if __name__ == "__main__":
    try:
        run_program()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        try:
            (ROOT / "tunnel-watchdog-error.txt").write_text(str(error), encoding="utf-8-sig")
        except OSError:
            pass
        if sys.stderr is not None:
            print(f"启动失败：{error}", file=sys.stderr)
        sys.exit(1)
