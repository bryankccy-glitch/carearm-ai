"""手臂執行的併發、急停、失敗處理與跨日。全部走真實模擬。"""
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


def prepare(client, service, text="我要吃中午的藥", hhmm="12:00"):
    idle(client)
    client.post("/api/day/reset")
    client.post("/api/reset")
    decision = client.post("/api/command", json={"text": text, "time": hhmm}).json()["decision"]
    return decision["decision"] != "EXECUTE"


def test_second_execute_is_refused_and_a_decision_runs_once(setup):
    client, service = setup
    confirm = prepare(client, service)
    assert client.post("/api/execute", json={"caregiver_confirmed": confirm}).status_code == 200
    second = client.post("/api/execute", json={"caregiver_confirmed": confirm})
    assert second.status_code == 400
    idle(client)
    third = client.post("/api/execute", json={"caregiver_confirmed": confirm})
    assert third.status_code == 400 and "請先送出指令" in third.json()["detail"]


def test_job_uses_the_approved_decision_even_if_a_new_analysis_replaces_it(setup):
    client, service = setup
    confirm = prepare(client, service)
    assert client.post("/api/execute", json={"caregiver_confirmed": confirm}).status_code == 200
    client.post("/api/command", json={"text": "幫我拿晚上的藥", "time": "18:00"})
    state = idle(client)
    assert state["last_execution"]["requested_period"] == "noon"


def test_failed_job_does_not_leave_busy_and_stays_blocked(setup, monkeypatch):
    client, service = setup
    confirm = prepare(client, service)
    from carearm.robot.executor import RetrievalExecutor

    def boom(*args, **kwargs):
        raise RuntimeError("模擬硬體錯誤")

    monkeypatch.setattr(RetrievalExecutor, "execute", boom)
    assert client.post("/api/execute", json={"caregiver_confirmed": confirm}).status_code == 200
    state = idle(client, 30)
    assert state["last_execution"]["success"] is False
    assert "noon" in service.retrieved_today


def test_confirmed_failure_releases_the_reservation(setup, monkeypatch):
    client, service = setup
    confirm = prepare(client, service)
    from types import SimpleNamespace

    from carearm.robot.executor import RetrievalExecutor

    failed = SimpleNamespace(success=False, failure_stage="夾取", error=None, to_dict=lambda: {"success": False})
    monkeypatch.setattr(RetrievalExecutor, "execute", lambda *a, **k: failed)
    assert client.post("/api/execute", json={"caregiver_confirmed": confirm}).status_code == 200
    idle(client, 30)
    assert "noon" not in service.retrieved_today
    assert service.store.dose_state().get("noon", {}).get("retrieved") is False


def test_reservation_survives_a_restart(setup):
    client, service = setup
    prepare(client, service)
    service.store.log_dose("evening", "dispensing", None, None)
    assert service.store.dose_state()["evening"]["retrieved"] is True


def test_urgent_text_stops_a_moving_arm(setup):
    client, service = setup
    idle(client)
    assert service._urgent_stop("我胸口好痛快不能呼吸") is False  # 手臂沒在動時不用停
    service.control.reset()
    service._set(busy=True)
    try:
        assert service._urgent_stop("我胸口好痛快不能呼吸") is True
        assert service.control.stop_event.is_set()
    finally:
        service._set(busy=False)
        service.control.reset()


def test_stop_reaches_perception_moves(setup):
    client, service = setup
    prepare(client, service)
    from carearm.robot.executor import ExecutionStopped, RetrievalExecutor

    seen = []
    original = RetrievalExecutor.__init__

    def spy(self, *args, **kwargs):
        seen.append(kwargs.get("control"))
        original(self, *args, **kwargs)

    RetrievalExecutor.__init__ = spy
    try:
        client.post("/api/reset")
        client.post("/api/command", json={"text": "我要吃中午的藥", "time": "12:00"})
    finally:
        RetrievalExecutor.__init__ = original
    assert seen and all(item is service.control for item in seen)
    assert ExecutionStopped


def test_midnight_clears_yesterday(setup):
    client, service = setup
    idle(client)
    client.post("/api/day/reset")
    client.post("/api/reset")
    service.retrieved_today.add("noon")
    service._last_decision = object()
    service._day = "2000-01-01"
    service._roll_day()
    assert "noon" not in service.retrieved_today
    assert service._last_decision is None
