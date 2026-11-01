"""API 端點（真實模擬服務，不使用 mock）。"""
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from carearm.api.app import create_app

    app = create_app(realtime=False)
    with TestClient(app) as c:
        yield c


def wait_idle(client, timeout=90):
    end = time.time() + timeout
    time.sleep(0.5)
    while time.time() < end:
        s = client.get("/api/status").json()
        if not s["busy"] and s["last_execution"] is not None:
            return s
        time.sleep(0.3)
    raise TimeoutError


def test_health_and_status(client):
    assert client.get("/api/health").json()["ok"]
    s = client.get("/api/status").json()
    assert set(s["boxes"]) == {"morning", "noon", "evening"}


def test_camera_png(client):
    r = client.get("/api/camera.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_stop_decision_cannot_execute(client):
    client.post("/api/reset")
    a = client.post("/api/command", json={"text": "幫我拿晚上的藥", "time": "08:00"}).json()
    assert a["decision"]["decision"] == "STOP"
    assert client.post("/api/execute", json={"caregiver_confirmed": True}).status_code == 403


def test_full_retrieval_then_duplicate_blocked(client):
    client.post("/api/reset")
    a = client.post("/api/command", json={"text": "我要吃中午的藥", "time": "12:00"}).json()
    assert a["decision"]["decision"] in ("EXECUTE", "REQUEST_CONFIRMATION")
    r = client.post("/api/execute", json={"caregiver_confirmed": a["decision"]["decision"] != "EXECUTE"})
    assert r.status_code == 200
    s = wait_idle(client)
    assert s["last_execution"]["success"] and s["boxes"]["noon"]["in_delivery"]
    again = client.post("/api/command", json={"text": "我要吃中午的藥", "time": "12:05"}).json()
    assert again["decision"]["decision"] == "STOP"
    assert any(r["rule"] == "duplicate_retrieval" and not r["passed"] for r in again["decision"]["rules"])


def test_finish_collects_empty_box(client):
    client.post("/api/reset")
    a = client.post("/api/command", json={"text": "幫我拿晚上的藥", "time": "18:00"}).json()
    client.post("/api/execute", json={"caregiver_confirmed": a["decision"]["decision"] != "EXECUTE"})
    assert wait_idle(client)["boxes"]["evening"]["in_delivery"]
    # 盒內還有藥（預設每盒 2 顆）：手腕相機看到藥 → 不回收
    early = client.post("/api/command", json={"text": "我吃完藥了", "time": "18:10"}).json()
    assert early["decision"]["decision"] == "STOP" and early["adherence"]["verdict"] == "pills_remaining"
    assert early["adherence"]["count"] == 2
    assert client.get("/api/inspection.png").content[:4] == b"\x89PNG"
    # 模擬住民把藥吃完，再說一次 → 相機確認已空 → 回收
    assert client.post("/api/simulation/intake", json={"period": "evening", "count": 99}).json()["remaining_count"] == 0
    f = client.post("/api/command", json={"text": "我吃完藥了", "time": "18:20"}).json()
    assert f["adherence"]["verdict"] == "verified_taken"
    assert client.get("/api/doses").json()["today"]["evening"]["verdict"] == "verified_taken"
    assert f["decision"]["action"] == "collect" and f["decision"]["decision"] == "EXECUTE"
    assert f["decision"]["target_period"] == "evening"
    assert client.post("/api/execute", json={"caregiver_confirmed": False}).status_code == 200
    end = time.time() + 90
    time.sleep(0.5)
    while time.time() < end:
        s = client.get("/api/status").json()
        if not s["busy"] and s["boxes"]["evening"]["in_collection"]:
            break
        time.sleep(0.3)
    assert s["boxes"]["evening"]["in_collection"] and s["last_execution"]["success"]
    again = client.post("/api/command", json={"text": "吃完了", "time": "18:25"}).json()
    assert again["decision"]["decision"] == "STOP"          # 遞送區已經空了


def test_events_and_experiments(client):
    assert len(client.get("/api/events").json()) > 0
    assert "runs" in client.get("/api/experiments").json()


def test_bad_input_rejected(client):
    assert client.post("/api/command", json={"text": "  "}).status_code == 400
    assert client.post("/api/select_period", json={"period": "midnight"}).status_code == 400
