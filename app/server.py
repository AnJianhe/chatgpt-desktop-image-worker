"""ChatGPT 桌面画图中转服务器；请求入队，单个线程执行桌面操作。

运行：python server.py --host 127.0.0.1 --port 8765
首次启动前：python chatgpt_image_ui.py，完成界面校准。
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import io
import logging
import math
import os
from pathlib import Path
import queue
import shutil
import re
import threading
import time
from typing import Callable
import uuid
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

from chatgpt_image_ui import NotGenerated, Automation, DEFAULT_CONFIG, enable_dpi, load_config
from fabric_presets import FABRIC_CATALOG, prepare_prompt
from generation_history import GenerationHistory

BASE = Path(__file__).resolve().parent
LOGGER = logging.getLogger("chatgpt_remote_image")
Runner = Callable[..., Path]
IMAGE_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}


def normalize_entry_origin(value: str) -> str:
    """Normalize an explicitly configured parent-page origin for postMessage."""
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    host = parsed.hostname
    if (parsed.scheme not in {"https", "http"} or not host or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or (parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"})):
        raise ValueError("固定入口应填写 HTTPS 来源地址，本地预览可用 localhost HTTP 地址。")
    port = parsed.port
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


class TunnelHeartbeat:
    """Track successful browser packets for this server instance and hostname."""
    def __init__(self):
        self.server_id = uuid.uuid4().hex
        self.hosts = OrderedDict()
        self.lock = threading.Lock()

    def record(self, host):
        # Local pages and packets for another tunnel must not keep a dead URL alive.
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", host):
            return False
        with self.lock:
            self.hosts[host] = time.monotonic()
            self.hosts.move_to_end(host)
            while len(self.hosts) > 32:
                self.hosts.popitem(last=False)
        return True

    def age(self, host):
        with self.lock:
            timestamp = self.hosts.get(host)
        return None if timestamp is None else max(0, time.monotonic() - timestamp)


def validate_config(cfg: dict, config_path: Path) -> None:
    """只检查配置文件，不激活窗口或移动鼠标。"""
    if not config_path.is_file():
        raise RuntimeError("找不到 config.json。请先运行 python chatgpt_image_ui.py，保存界面校准。")
    size = cfg.get("client_size")
    if not isinstance(size, (list, tuple)) or len(size) != 2 or any(
        isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in size
    ):
        raise RuntimeError("尚未记录 ChatGPT 窗口尺寸。请在本地校准界面记录按钮并截取完成标记。")
    points = cfg.get("points")
    if not isinstance(points, dict):
        raise RuntimeError("校准点配置无效，请在本地校准界面重新保存。")
    for key in ("new_chat", "input", "send"):
        point = points.get(key)
        if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in point
        ) or not (0 <= point[0] < size[0] and 0 <= point[1] < size[1]):
            raise RuntimeError("缺少有效的按钮校准：" + key + "。请先运行 python chatgpt_image_ui.py。")
    marker_value = cfg.get("template")
    if not isinstance(marker_value, str) or not marker_value.strip():
        raise RuntimeError("尚未配置生成完成标记，请在本地校准界面截取完成标记。")
    marker = Path(marker_value).expanduser()
    if not marker.is_absolute():
        marker = config_path.parent / marker
    if not marker.is_file() or marker.stat().st_size == 0:
        raise RuntimeError("找不到生成完成标记图片，请在本地校准界面重新截取并保存。")
    send_marker = config_path.parent / "send_marker.png"
    if not send_marker.is_file() or send_marker.stat().st_size == 0:
        raise RuntimeError("找不到发送按钮标记 send_marker.png，请在本地校准界面重新记录发送按钮并保存。")
    download_marker = config_path.parent / "download_menu.png"
    if not download_marker.is_file() or download_marker.stat().st_size == 0:
        raise RuntimeError("找不到下载菜单标记 download_menu.png，请在本地校准界面截取“下载副本”菜单项。")
    try:
        timeout = float(cfg["timeout"])
        confidence = float(cfg["confidence"])
        stable = float(cfg["stable_seconds"])
        download_timeout = float(cfg.get("download_timeout", 90))
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("超时、匹配阈值或稳定时间配置无效。") from exc
    if not all(math.isfinite(v) for v in (timeout, confidence, stable, download_timeout)) or (
        timeout < 20 or not 0.5 <= confidence < 1 or stable < 1 or not 10 <= download_timeout <= 3600
    ):
        raise RuntimeError("超时至少 20 秒，匹配阈值为 0.5 至小于 1，稳定时间至少 1 秒，下载超时为 10 至 3600 秒。")
    if not isinstance(cfg.get("download_dir", ""), str):
        raise RuntimeError("下载文件夹配置无效，请填写路径或留空使用系统默认目录。")
    title = cfg.get("window_title")
    if not isinstance(title, str) or not title.strip():
        raise RuntimeError("请在本地校准界面设置 ChatGPT 窗口标题。")
    try:
        re.compile(title)
    except re.error as exc:
        raise RuntimeError("ChatGPT 窗口标题匹配表达式无效。") from exc
    if not isinstance(cfg.get("output_dir"), str) or not cfg["output_dir"].strip():
        raise RuntimeError("图片保存目录为空，请在本地校准界面设置并保存。")


def desktop_runner(prompt: str, cfg: dict, config_path: Path, log: Callable[[str], None], image_path: Path | None = None, review=None) -> Path:
    return Automation(cfg, config_path, log=log, review=review).run(prompt, image_paths=image_path if isinstance(image_path, (list, tuple)) else ([image_path] if image_path else []))


@dataclass
class Job:
    id: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    request_id: str | None = None
    upload_id: str | None = None
    reference: Path | None = None
    references: tuple = ()
    upload_ids: tuple = ()
    tool: str = "custom"
    original_prompt: str = ""
    status: str = "queued"
    stage: str = "等待本地电脑执行"
    error: str | None = None
    image: Path | None = None
    image_mimetype: str | None = None
    image_filename: str | None = None
    preview: Path | None = None
    review_version: int = 0
    review_action: str | None = None


class JobManager:
    def __init__(self, config_path: Path, runner: Runner, validate: bool, max_queue: int, max_history: int):
        self.config_path = config_path
        self.runner = runner
        self.validate = validate
        self.max_history = max_history
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.request_ids: dict[str, str] = {}
        self.queue: queue.Queue[str] = queue.Queue(maxsize=max_queue)
        self.lock = threading.RLock()
        self.review_changed = threading.Condition(self.lock)
        self.history = GenerationHistory(config_path.parent / "generation-history")
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._work, name="desktop-image-worker", daemon=True)
        self.worker.start()

    def _trim(self) -> None:
        # 仅移除已结束任务；排队/执行中的任务永远保留。
        excess = len(self.jobs) - self.max_history
        if excess <= 0:
            return
        for job_id, job in list(self.jobs.items()):
            if excess <= 0:
                break
            if job.status not in {"done", "error", "not_generated"}:
                continue
            del self.jobs[job_id]
            if job.request_id:
                self.request_ids.pop(job.request_id, None)
            excess -= 1

    def submit(self, prompt: str, request_id: str | None, upload_id: str | None = None, reference: Path | None = None, *, references=None, upload_ids=None, tool="custom", original_prompt=None) -> tuple[Job, bool]:
        references = tuple(references) if references is not None else ((reference,) if reference else ())
        upload_ids = tuple(upload_ids) if upload_ids is not None else ((upload_id,) if upload_id else ())
        with self.lock:
            if request_id and request_id in self.request_ids:
                job = self.jobs[self.request_ids[request_id]]
                if job.prompt != prompt or job.original_prompt != (prompt if original_prompt is None else original_prompt) or job.upload_ids != upload_ids or job.tool != tool:
                    raise ValueError("同一个 request_id 已用于不同提示词或参考图片。")
                return job, False
            if request_id:
                saved = self.history.get(request_id=request_id)
                if saved:
                    if saved['sent_prompt'] != prompt or saved['prompt'] != (prompt if original_prompt is None else original_prompt) or tuple(saved['referenceImages']) != upload_ids or saved['tool'] != tool:
                        raise ValueError("同一个 request_id 已用于不同提示词或参考图片。")
                    return Job(id=saved['id'], prompt=prompt, status=saved['status']), False
            if self.stop.is_set():
                raise RuntimeError("本地服务器正在停止，请稍后重试。")
            job = Job(id=uuid.uuid4().hex, prompt=prompt, request_id=request_id, upload_id=upload_id, reference=reference)
            job.references = references
            job.upload_ids = upload_ids
            job.tool = tool
            job.original_prompt = prompt if original_prompt is None else original_prompt
            if self.queue.full():
                raise queue.Full()
            # Private immutable copies: composer edits and upload cleanup cannot change queued jobs.
            if references:
                folder = self.config_path.parent / "job-inputs" / job.id
                folder.mkdir(parents=True, exist_ok=True)
                copies = []
                for index, source in enumerate(references):
                    destination = folder / f"{index + 1}.png"
                    shutil.copyfile(source, destination)
                    copies.append(destination)
                job.references = tuple(copies)
                job.reference = copies[0]
            # 持锁入队与登记，worker 获取任务后会在同一把锁处等待。
            self.queue.put_nowait(job.id)
            self.jobs[job.id] = job
            if request_id:
                self.request_ids[request_id] = job.id
            self.record(job)
            self._trim()
            return job, True

    def snapshot(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                saved = self.history.get(job_id=job_id)
                return self.history.public(saved) if saved else None
            data = {"id": job.id, "taskId": job.id, "created_at": job.created_at, "prompt": job.original_prompt, "sent_prompt": job.prompt, "tool": job.tool, "referenceImages": list(job.upload_ids), "status": job.status, "stage": job.stage, "error": job.error, "result": None}
            if job.status == "done":
                data["image_url"] = "/jobs/" + job.id + "/image"
                data["image_filename"] = job.image_filename
                data["result"] = data["image_url"]
            elif job.status == "review":
                data["preview_url"] = "/jobs/" + job.id + "/preview?v=" + str(job.review_version)
                data["review_version"] = job.review_version
            elif job.status == "queued":
                waiting = [j.id for j in self.jobs.values() if j.status == "queued"]
                data["queue_position"] = waiting.index(job.id) + 1
            return data

    def record(self, job):
        data = self.snapshot(job.id)
        data.update(request_id=job.request_id, _image=str(job.image) if job.image else None, _mimetype=job.image_mimetype, _filename=job.image_filename)
        self.history.save(data)

    def _log(self, job_id: str, message: str) -> None:
        stage = str(message).strip()[:500]
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].stage = stage
        LOGGER.info("任务 %s：%s", job_id, stage)

    def request_review(self, job_id: str, preview: Path, message: str) -> str:
        preview = Path(preview).resolve()
        with Image.open(preview) as image:
            if image.format != "PNG":
                raise RuntimeError("远端预览截图格式无效。")
            image.verify()
        with self.review_changed:
            job = self.jobs[job_id]
            job.preview = preview
            job.review_version += 1
            job.review_action = None
            job.status = "review"
            job.stage = message
            self.record(job)
            LOGGER.info("任务 %s 等待远端确认，第 %s 次", job_id, job.review_version)
            while job.review_action is None and not self.stop.is_set():
                self.review_changed.wait(timeout=1)
            if self.stop.is_set():
                raise RuntimeError("服务器已停止，远端确认已取消；当前对话保留。")
            action = job.review_action
            if action == "end":
                raise NotGenerated("用户确认：本次未生成图片。请修改提示词后重新提交。")
            job.status = "running"
            job.stage = "正在重试当前图片下载" if action == "retry" else "继续等待当前图片生成"
            self.record(job)
            return action

    def _work(self) -> None:
        while not self.stop.is_set():
            try:
                job_id = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    job = self.jobs[job_id]
                    job.status = "running"
                    job.stage = "读取本地配置"
                    self.record(job)
                # 每次任务读取配置，校准后无需重启服务器。
                cfg = load_config(self.config_path)
                if self.validate:
                    validate_config(cfg, self.config_path)
                log = lambda msg: self._log(job_id, msg)
                path = Path(self.runner(job.prompt, cfg, self.config_path, log, (job.references if len(job.references) > 1 else job.reference),
                                        lambda preview, message: self.request_review(job_id, preview, message)))
                path = path.expanduser().resolve()
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError("桌面操作结束，但没有找到有效的原图文件。")
                # 根据文件实际内容判定格式；保留下载的原始文件，不转码。
                with Image.open(path) as image:
                    image_format = image.format
                    if image_format not in IMAGE_FORMATS:
                        raise RuntimeError("下载文件必须是 PNG、JPEG 或 WEBP 图片。")
                    image.verify()
                # verify 检查结构，再实际解码一次，拒绝截断的下载文件。
                with Image.open(path) as image:
                    image.load()
                mimetype, extension = IMAGE_FORMATS[image_format]
                valid_suffixes = {".jpg", ".jpeg"} if image_format == "JPEG" else {extension}
                filename = path.name if path.suffix.lower() in valid_suffixes else path.stem + extension
                path = self.history.preserve_image(job.id, path)
                with self.lock:
                    job.image = path
                    job.image_mimetype = mimetype
                    job.image_filename = filename
                    job.status = "done"
                    job.stage = "图片已生成，原图已下载，可查看和保存"
                    self.record(job)
            except NotGenerated as exc:
                with self.lock:
                    job = self.jobs[job_id]
                    job.status = "not_generated"
                    job.stage = str(exc)
                    job.error = None
                    self.record(job)
            except Exception as exc:
                LOGGER.exception("任务 %s 执行失败", job_id)
                with self.lock:
                    job = self.jobs[job_id]
                    job.status = "error"
                    job.error = str(exc) or type(exc).__name__
                    job.stage = "执行失败"
                    self.record(job)
            finally:
                self.queue.task_done()
                with self.lock:
                    self._trim()

    def close(self, timeout: float = 1) -> None:
        """停止本服务的调度线程，不结束 ChatGPT 或其他用户进程。"""
        self.stop.set()
        with self.review_changed:
            self.review_changed.notify_all()
        self.worker.join(timeout=timeout)


def make_app(
    config_path: Path | str = DEFAULT_CONFIG,
    runner: Runner | None = None,
    validate_startup: bool = True,
    max_queue: int = 20,
    max_history: int = 200,
    upload_dir: Path | str | None = None,
    entry_origin: str | None = None,
) -> Flask:
    """测试时注入 fake_runner(prompt, cfg, config_path, log, reference, review)，不触发桌面操作。

    validate_startup=False 同时跳过启动和任务配置校验，便于离线测试。
    每个应用只有一个 worker；不要配置多个 waitress 进程或 Flask reloader。
    """
    config_path = Path(config_path).expanduser().resolve()
    entry_origin = normalize_entry_origin(entry_origin if entry_origin is not None else os.environ.get("FABRIC_ENTRY_ORIGIN", ""))
    if max_queue < 1 or max_history < max_queue + 1:
        raise ValueError("max_queue 至少为 1，max_history 至少为 max_queue + 1。")
    if validate_startup:
        validate_config(load_config(config_path), config_path)
        if not (BASE / "templates" / "index.html").is_file():
            raise RuntimeError("缺少 templates/index.html，请把前端文件放回完整项目目录。")
    app = Flask(__name__, template_folder=str(BASE / "templates"), static_folder=str(BASE / "static"))
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    uploads = Path(upload_dir).resolve() if upload_dir is not None else config_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    manager = JobManager(config_path, runner or desktop_runner, validate_startup, max_queue, max_history)
    app.extensions["job_manager"] = manager
    heartbeat = TunnelHeartbeat()
    app.extensions["tunnel_heartbeat"] = heartbeat

    @app.get("/")
    def index():
        return render_template("index.html", fabric_catalog=FABRIC_CATALOG,
                               heartbeat_server_id=heartbeat.server_id, entry_origin=entry_origin)

    @app.get("/health")
    def health():
        return jsonify(status="ok", worker_alive=manager.worker.is_alive(), server_id=heartbeat.server_id)

    @app.route("/api/tunnel-heartbeat", methods=["GET", "POST"])
    def tunnel_heartbeat():
        if not manager.worker.is_alive():
            return jsonify(status="error", error="画图服务尚未就绪。"), 503
        host = (urlsplit("http://" + request.host).hostname or "").lower()
        if request.method == "POST":
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error="心跳格式错误。"), 400
            if data.get("server_id") != heartbeat.server_id:
                return jsonify(error="服务已更新，请重新获取心跳。"), 409
            nonce = data.get("nonce")
            if not isinstance(nonce, str) or not 1 <= len(nonce) <= 128:
                return jsonify(error="缺少心跳编号。"), 400
            return jsonify(status="ok", server_id=heartbeat.server_id, nonce=nonce,
                           recorded=heartbeat.record(host))
        # Watchdog queries the local server for packets to its current public host.
        if host in {"127.0.0.1", "localhost", "::1"}:
            host = request.args.get("host", host).lower()
        return jsonify(status="ok", worker_alive=True, server_id=heartbeat.server_id,
                       nonce=request.args.get("nonce", "")[:128],
                       browser_heartbeat_age=heartbeat.age(host))

    @app.post("/uploads")
    def upload_image():
        request.max_content_length = 21 * 1024 * 1024
        files = request.files.getlist("image")
        if len(files) != 1 or not files[0].filename:
            return jsonify(error="请选择一张 PNG、JPEG 或 WEBP 参考图片。"), 400
        upload_id = request.form.get("upload_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
            return jsonify(error="上传编号无效，请重新选择图片。"), 400
        data = files[0].read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            return jsonify(error="参考图片最大 20 MB。"), 413
        target = uploads / (upload_id + ".png")
        digest_path = uploads / (upload_id + ".sha256")
        digest = hashlib.sha256(data).hexdigest()
        # 断网重试同一次上传，不重复保存或重复创建画图任务。
        with manager.lock:
            if target.is_file() and digest_path.is_file():
                if digest_path.read_text(encoding="ascii") != digest:
                    return jsonify(error="同一个上传编号已用于另一张图片。"), 409
                return jsonify(upload_id=upload_id)
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in IMAGE_FORMATS:
                        return jsonify(error="仅支持 PNG、JPEG 或 WEBP 图片。"), 400
                    if image.width * image.height > 20_000_000:
                        return jsonify(error="图片过大，请缩小到 2000 万像素以内。"), 400
                    image.load()
                    oriented = ImageOps.exif_transpose(image)
                    oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB").save(target, format="PNG")
                digest_path.write_text(digest, encoding="ascii")
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                target.unlink(missing_ok=True)
                return jsonify(error="无法读取这张图片，请选择完整的 PNG、JPEG 或 WEBP 文件。"), 400
        return jsonify(upload_id=upload_id), 201

    @app.post("/jobs")
    def create_job():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="请使用 JSON 提交 {prompt: 画图提示词}。"), 400
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return jsonify(error="请输入画图提示词。"), 400
        # Custom mode preserves whitespace exactly; strip only for validation.
        if len(prompt) > 10000:
            return jsonify(error="提示词最长 10000 个字符。"), 400
        request_id = body.get("request_id")
        if request_id is not None:
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
                return jsonify(error="request_id 必须是长度 1 至 128 的字符串。"), 400
            request_id = request_id.strip()
        upload_ids = body.get("upload_ids", [body["upload_id"]] if body.get("upload_id") is not None else [])
        if not isinstance(upload_ids, list) or len(upload_ids) > 3:
            return jsonify(error="每个任务最多上传 3 张参考图片。"), 400
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in upload_ids) or len(set(upload_ids)) != len(upload_ids):
            return jsonify(error="参考图片编号无效或重复，请重新上传。"), 400
        references = [uploads / (value + ".png") for value in upload_ids]
        if any(not path.is_file() for path in references):
            return jsonify(error="参考图片不存在，请重新上传。"), 400
        reference = references[0] if references else None
        upload_id = upload_ids[0] if upload_ids else None
        tool_id = body.get("tool", "custom")
        tool = next((item for item in FABRIC_CATALOG["tools"] if item["id"] == tool_id), None)
        if tool is None:
            return jsonify(error="设计工具预设无效。"), 400
        if tool["requires_reference"] and reference is None:
            return jsonify(error="此工具预设需要先上传参考图片。"), 400
        try:
            final_prompt = prepare_prompt(prompt, tool_id, bool(references))
            job, created = manager.submit(final_prompt, request_id, upload_id, reference, references=references, upload_ids=upload_ids, tool=tool_id, original_prompt=prompt)
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        except queue.Full:
            return jsonify(error="当前等待任务已满，请稍后重试。"), 503, {"Retry-After": "10"}
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503
        with manager.lock:
            status = job.status
        return jsonify(id=job.id, status=status), 202 if created else 200

    @app.get("/history")
    def generation_records():
        try:
            offset = max(0, int(request.args.get("offset", "0")))
            limit = min(100, max(1, int(request.args.get("limit", "20"))))
        except ValueError:
            return jsonify(error="分页参数无效。"), 400
        status = request.args.get("status") or None
        if status and status not in {"queued", "running", "review", "done", "not_generated", "error"}:
            return jsonify(error="记录状态无效。"), 400
        with manager.lock:
            return jsonify(manager.history.page(offset, limit, status))

    @app.get("/jobs/<job_id>")
    def job_status(job_id: str):
        data = manager.snapshot(job_id)
        if data is None:
            return jsonify(error="任务不存在或已从历史记录中移除。"), 404
        return jsonify(data)

    @app.get("/jobs/<job_id>/preview")
    def job_preview(job_id: str):
        with manager.lock:
            job = manager.jobs.get(job_id)
            if job is None:
                return jsonify(error="任务不存在。"), 404
            path = job.preview
        if path is None or not path.is_file():
            return jsonify(error="当前任务没有待确认截图。"), 404
        return send_file(path, mimetype="image/png", download_name="当前画面.png", conditional=True)

    @app.post("/jobs/<job_id>/review")
    def review_job(job_id: str):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("action"), str) or body["action"] not in {"retry", "wait", "end"}:
            return jsonify(error="请选择已经生成、继续等待或生成已结束。"), 400
        if body["action"] == "end" and body.get("confirmed") is not True:
            return jsonify(error="结束任务需要二次确认。"), 400
        with manager.review_changed:
            job = manager.jobs.get(job_id)
            if job is None:
                return jsonify(error="任务不存在。"), 404
            if job.status != "review" or job.review_action is not None or type(body.get("version")) is not int or body["version"] != job.review_version:
                return jsonify(error="截图确认已处理或已更新，请等待页面刷新。"), 409
            job.review_action = body["action"]
            job.status = "running"
            manager.review_changed.notify_all()
        return jsonify(id=job_id, status="running"), 202

    @app.get("/jobs/<job_id>/image")
    def job_image(job_id: str):
        with manager.lock:
            job = manager.jobs.get(job_id)
            if job is None:
                saved = manager.history.get(job_id=job_id)
                if saved is None:
                    return jsonify(error="任务不存在。"), 404
                if saved["status"] != "done" or not saved.get("_image"):
                    return jsonify(error="此任务没有生成图片。", status=saved["status"]), 409
                path = Path(saved["_image"])
                mimetype = saved["_mimetype"]
                filename = saved["_filename"]
            else:
                if job.status != "done" or job.image is None:
                    return jsonify(error="图片尚未生成完成。", status=job.status), 409
                path = job.image
                mimetype = job.image_mimetype
                filename = job.image_filename
        if not path.is_file():
            return jsonify(error="原图文件已被移动或删除。"), 404
        return send_file(path, mimetype=mimetype, download_name=filename, conditional=True)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error="请求内容过大，参考图片最大 20 MB，提示词最长 10000 字。"), 413

    @app.after_request
    def response_headers(response):
        # 轮询必须读取当前状态；内网穿透使用同源前端，不需要 CORS。
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatGPT 桌面远程画图服务器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="本地 UI 校准配置文件")
    parser.add_argument("--entry-origin", default=None, help="自己的固定入口来源地址；默认读取 FABRIC_ENTRY_ORIGIN")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1 至 65535。")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    enable_dpi()
    try:
        from waitress import serve
        app = make_app(config_path=args.config, entry_origin=args.entry_origin)
    except Exception as exc:
        LOGGER.error("服务器无法启动：%s", exc)
        return 2
    LOGGER.info("本地页面：http://%s:%s", args.host, args.port)
    LOGGER.info("已就绪，桌面画图任务会逐个执行。按 Ctrl+C 停止服务器。")
    try:
        serve(app, host=args.host, port=args.port, threads=4)
    except KeyboardInterrupt:
        LOGGER.info("正在停止本地服务器。")
    except OSError as exc:
        LOGGER.error("无法监听端口 %s：%s", args.port, exc)
        return 2
    finally:
        app.extensions["job_manager"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
