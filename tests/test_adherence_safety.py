"""服藥記錄不能建立在猜測上：懷疑掉落或認不出藥盒時，都不可記成已服用。"""
import time

import pytest
from fastapi.testclient import TestClient

from carearm.api.app import create_app
from carearm.api.service import SimulationService


@pytest.fixture(scope="module")
def setup():
    service = SimulationService(realtime=False)
    with TestClient(create_app(service, realtime=False)) as client:
        yield client, service


def idle(client, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = client.get("/api/status").json()
        if not state["busy"]:
            return state
        time.sleep(0.2)
    raise TimeoutError


def deliver(client):
    idle(client)
    client.post("/api/day/reset")
    client.post("/api/reset")
    decision = client.post("/api/command", json={"text": "幫我拿晚上的藥", "time": "18:00"}).json()["decision"]
    client.post("/api/execute", json={"caregiver_confirmed": decision["decision"] != "EXECUTE"})
    time.sleep(0.5)
    assert idle(client)["boxes"]["evening"]["in_delivery"]


def test_dropped_pills_are_not_recorded_as_taken(setup):
    client, service = setup
    deliver(client)
    assert client.post("/api/simulation/intake", json={"period": "evening", "count": 99, "drop_on_table": True}).status_code == 200
    analysis = client.post("/api/command", json={"text": "我吃完藥了", "time": "18:10"}).json()
    assert analysis["adherence"]["verdict"] != "verified_taken"
    assert "evening" not in service.taken_today


def test_unidentified_box_is_not_recorded_from_speech(setup, monkeypatch):
    client, service = setup
    deliver(client)
    import carearm.api.service as module

    original = module.collect_decision

    def unknown(*args, **kwargs):
        decision = original(*args, **kwargs)
        decision.target_index = decision.target_xy = decision.target_period = None
        return decision

    monkeypatch.setattr(module, "collect_decision", unknown)
    analysis = client.post("/api/command", json={"text": "我吃完藥了", "time": "18:10"}).json()
    assert "evening" not in service.taken_today
    assert any("無法確認身分" in reason for reason in analysis["decision"]["reasons"])


def test_speech_alone_needs_an_identified_period(setup):
    client, service = setup
    idle(client)
    client.post("/api/day/reset")
    client.post("/api/reset")
    client.post("/api/command", json={"text": "我吃完藥了", "time": "18:10"})
    assert service.taken_today == set()
