"""API 輸入與檔案邊界；不啟動研究模擬。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    import carearm.api.app as module
    from carearm.speech.asr import WhisperASR

    monkeypatch.setattr(WhisperASR, "warmup_async", lambda: None)
    monkeypatch.setattr(module, "RESULTS_DIR", tmp_path)
    service = Mock()
    service.reset.return_value = {"ok": True}
    service.randomize.return_value = {"ok": True}
    service.analyze.return_value = {"ok": True}
    app = module.create_app(service=service)
    with TestClient(app) as c:
        yield c, service, tmp_path


@pytest.mark.parametrize("body", [
    {"lighting": "other"}, {"occlusion_target": "other"},
    {"occlusion": -0.1}, {"occlusion": 1.1},
    {"camera_tilt_deg": 16}, {"seed": -1}, {"seed": 2**32},
])
def test_conditions_bounds(client, body):
    c, svc, _ = client
    assert c.post("/api/conditions", json=body).status_code == 422
    svc.reset.assert_not_called()


@pytest.mark.parametrize("route,body", [
    ("/api/reset", {}), ("/api/conditions", {}),
    ("/api/conditions", {"randomize": True}),
])
@pytest.mark.parametrize("error,status", [(RuntimeError("執行中"), 400), (PermissionError("禁止"), 403)])
def test_reset_guard(client, route, body, error, status):
    c, svc, _ = client
    svc.reset.side_effect = svc.randomize.side_effect = error
    assert c.post(route, json=body).status_code == status


@pytest.mark.parametrize("value", ["07:90", "24:00", "7:00", "12:3", "-1:00", "12:00:00"])
def test_invalid_time(client, value):
    c, svc, _ = client
    assert c.post("/api/command", json={"text": "取藥", "time": value}).status_code == 400
    assert c.post("/api/select_period", json={"period": "morning", "time": value}).status_code == 400
    assert c.post("/api/audio", data={"time": value}, files={"file": ("a.webm", b"audio", "audio/webm")}).status_code == 400
    svc.analyze.assert_not_called()


def test_valid_conditions_and_time(client):
    c, svc, _ = client
    assert c.post("/api/conditions", json={"lighting": "low", "occlusion": 1, "occlusion_target": "noon"}).status_code == 200
    assert svc.reset.call_args.args[0]["lighting"] == "low"
    assert c.post("/api/command", json={"text": "取藥", "time": "23:59"}).status_code == 200


@pytest.mark.parametrize("mode,status", [("success", 200), ("empty", 422), ("unavailable", 501), ("decode", 500), ("analysis", 400)])
def test_audio_worker_and_cleanup(client, monkeypatch, mode, status):
    import carearm.api.app as module
    from carearm.speech.asr import ASRUnavailable, WhisperASR

    c, svc, tmp = client
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp))
    paths = []
    threads = []

    def transcribe(self, path):
        paths.append(Path(path))
        threads.append(threading.current_thread().name)
        assert Path(path).read_bytes() == b"audio"
        if mode == "unavailable":
            raise ASRUnavailable("無法使用")
        if mode == "decode":
            raise ValueError("解碼失敗")
        return SimpleNamespace(text="" if mode == "empty" else "取藥", engine="test", language="zh", avg_logprob=0)

    def analyze(*args):
        assert threading.current_thread().name == threads[0]
        if mode == "analysis":
            raise RuntimeError("執行中")
        return {"ok": True}

    svc.analyze.side_effect = analyze
    monkeypatch.setattr(WhisperASR, "transcribe", transcribe)
    response = c.post("/api/audio", files={"file": ("a.webm", b"audio", "audio/webm;codecs=opus")})
    assert response.status_code == status
    assert threads and "worker" in threads[0].lower()
    assert all(not p.exists() for p in paths)


def test_audio_type_and_size(client, monkeypatch):
    import carearm.api.app as module
    c, svc, tmp = client
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(module, "MAX_AUDIO_BYTES", 3)
    assert c.post("/api/audio", files={"file": ("a.txt", b"a", "text/plain")}).status_code == 415
    assert c.post("/api/audio", files={"file": ("a.webm", b"1234", "audio/webm")}).status_code == 413
    assert not list(tmp.iterdir())
    svc.analyze.assert_not_called()


def test_audio_stops_reading_at_limit(client, monkeypatch):
    import carearm.api.app as module
    from fastapi import HTTPException

    c, _, tmp = client
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(module, "MAX_AUDIO_BYTES", 65536)

    class Upload:
        content_type = "audio/webm"
        reads = 0
        closed = False

        async def read(self, size):
            assert size == 65536
            self.reads += 1
            assert self.reads <= 2
            return b"x" * size

        async def close(self):
            self.closed = True

    upload = Upload()
    endpoint = next(route.endpoint for route in c.app.routes if route.path == "/api/audio")
    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint(file=upload, time=None))
    assert error.value.status_code == 413
    assert upload.reads == 2 and upload.closed
    assert not list(tmp.iterdir())


def test_figures_confined_to_regular_png(client):
    c, _, tmp = client
    figures = tmp / "run" / "figures"
    figures.mkdir(parents=True)
    (figures / "ok.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (figures / "secret.json").write_text("{}")
    (figures / "directory.png").mkdir()
    (tmp / "outside.png").write_bytes(b"outside")
    (figures / "link.png").symlink_to(tmp / "outside.png")
    assert c.get("/api/experiments/run/figures/ok.png").status_code == 200
    for name in ("secret.json", "directory.png", "link.png", "missing.png", "..%2Foutside.png"):
        assert c.get(f"/api/experiments/run/figures/{name}").status_code == 404
    (tmp / "alias").symlink_to(tmp / "run", target_is_directory=True)
    assert c.get("/api/experiments/alias/figures/ok.png").status_code == 404
    other = tmp / "other"
    other.mkdir()
    (other / "figures").symlink_to(figures, target_is_directory=True)
    assert c.get("/api/experiments/other/figures/ok.png").status_code == 404
