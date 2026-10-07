"""离线测试服务器，fake_runner 不启动 ChatGPT、不点击桌面。"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from server import make_app, validate_config
from chatgpt_image_ui import load_config

_png = io.BytesIO()
Image.new('RGB', (2, 2), 'red').save(_png, format='PNG')
PNG = _png.getvalue()


def wait_done(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get("/jobs/" + job_id).get_json()
        if data["status"] in {"done", "error"}:
            return data
        time.sleep(.01)
    raise AssertionError("fake_runner 未在 5 秒内结束")


def main():
    with tempfile.TemporaryDirectory(prefix="server-test-") as temporary:
        tmp = Path(temporary)
        config_path = tmp / "config.json"
        image_path = tmp / "result.png"
        image_path.write_bytes(PNG)
        cfg = {
            "points": {"new_chat": [10, 10], "input": [30, 30], "send": [50, 50]},
            "client_size": [800, 600], "timeout": 321,
            "template": "done_marker.png", "output_dir": str(tmp),
        }
        config_path.write_text(json.dumps(cfg), encoding="utf-8")
        (tmp / "done_marker.png").write_bytes(PNG)
        try:
            validate_config(load_config(config_path), config_path)
        except RuntimeError as exc:
            assert "send_marker.png" in str(exc), str(exc)
        else:
            raise AssertionError("缺少发送按钮标记时应拒绝配置")
        (tmp / "send_marker.png").write_bytes(PNG)
        (tmp / "download_menu.png").write_bytes(PNG)
        validate_config(load_config(config_path), config_path)

        first_entered = threading.Event()
        release = threading.Event()
        counter_lock = threading.Lock()
        state = {"active": 0, "peak": 0, "calls": []}

        def fake_runner(prompt, current_cfg, current_path, log, reference=None, review=None):
            assert current_path == config_path
            with counter_lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                state["calls"].append((prompt, current_cfg["timeout"]))
            try:
                log("模拟阶段：" + prompt)
                if prompt == "第一只猫":
                    first_entered.set()
                    assert release.wait(5), "测试释放事件超时"
                if prompt == "失败":
                    raise RuntimeError("模拟生成失败")
                time.sleep(.02)
                return image_path
            finally:
                with counter_lock:
                    state["active"] -= 1

        app = make_app(config_path, runner=fake_runner, validate_startup=False, max_queue=2, max_history=3)
        app.config["TESTING"] = True
        manager = app.extensions["job_manager"]
        client = app.test_client()
        try:
            assert client.get("/health").get_json()["worker_alive"]
            assert client.post("/jobs", data="not json").status_code == 400
            assert client.post("/jobs", json={"prompt": " "}).status_code == 400
            assert client.post("/jobs", json={"prompt": 123}).status_code == 400
            assert client.post("/jobs", json={"prompt": "x" * 10001}).status_code == 400
            assert client.post("/jobs", json={"prompt": "猫", "request_id": []}).status_code == 400
            assert client.get("/jobs/missing").status_code == 404
            assert client.get("/jobs/missing/image").status_code == 404

            response = client.post("/jobs", json={"prompt": "第一只猫", "request_id": "retry-1"})
            assert response.status_code == 202, response.get_data(as_text=True)
            first = response.get_json()["id"]
            assert first_entered.wait(3)
            assert client.get("/jobs/" + first).get_json()["status"] == "running"
            assert client.get("/jobs/" + first + "/image").status_code == 409
            duplicate = client.post("/jobs", json={"prompt": "第一只猫", "request_id": "retry-1"})
            assert duplicate.status_code == 200 and duplicate.get_json()["id"] == first
            assert client.post("/jobs", json={"prompt": "另一只猫", "request_id": "retry-1"}).status_code == 409

            second = client.post("/jobs", json={"prompt": "第二只猫"}).get_json()["id"]
            third = client.post("/jobs", json={"prompt": "第三只猫"}).get_json()["id"]
            assert client.get("/jobs/" + second).get_json()["queue_position"] == 1
            assert client.get("/jobs/" + third).get_json()["queue_position"] == 2
            full = client.post("/jobs", json={"prompt": "队列满"})
            assert full.status_code == 503 and full.headers["Retry-After"] == "10"
            cfg["timeout"] = 432
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            release.set()

            for job_id in (first, second, third):
                result = wait_done(client, job_id)
                assert result["status"] == "done", result
                image = client.get(result["image_url"])
                assert image.status_code == 200 and image.mimetype == "image/png"
                assert image.get_data() == PNG
                image.close()
            assert state["peak"] == 1, state
            assert state["calls"] == [("第一只猫", 321), ("第二只猫", 432), ("第三只猫", 432)], state
            repeated = client.post("/jobs", json={"prompt": "第一只猫", "request_id": "retry-1"})
            assert repeated.status_code == 200 and repeated.get_json()["status"] == "done"
            assert len(state["calls"]) == 3

            failed_id = client.post("/jobs", json={"prompt": "失败"}).get_json()["id"]
            failed = wait_done(client, failed_id)
            assert failed["status"] == "error" and failed["error"] == "模拟生成失败", failed
            assert client.get("/jobs/" + failed_id + "/image").status_code == 409
            assert len(manager.jobs) == 3
            assert client.get("/jobs/" + first).status_code == 404
            image_path.unlink()
            assert client.get("/jobs/" + second + "/image").status_code == 404
            assert client.post("/jobs", data=b"x" * 65537, content_type="application/json").status_code == 413
        finally:
            release.set()
            manager.close(timeout=5)
        assert not manager.worker.is_alive()
    print("PASS: config validation, JSON errors, serial queue, queue capacity, request idempotency, config reload, PNG response, failure reporting, history pruning, shutdown")


if __name__ == "__main__":
    main()
