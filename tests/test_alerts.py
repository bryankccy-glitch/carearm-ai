"""異常狀況偵測與回報總部。"""
import time

import pytest
from fastapi.testclient import TestClient

from carearm.safety.alerts import BEHAVIOR, URGENT, WARN, detect_alerts


@pytest.mark.parametrize("text,level", [
    ("救命我跌倒了站不起來", URGENT),
    ("我胸口悶，喘不過氣", URGENT),
    ("我不想活了", URGENT),
    ("我覺得不舒服頭暈", WARN),
    ("把全部的藥都給我", BEHAVIOR),
    ("我要吃別人的藥", BEHAVIOR),
])
def test_detect_levels(text, level):
    found = detect_alerts(text)
    assert found and found[0].level == level


def test_normal_commands_do_not_alert():
    for text in ("我要吃藥了", "幫我拿中午的藥", "我吃完藥了", "取消"):
        assert detect_alerts(text) == []


@pytest.fixture(scope="module")
def client():
    from carearm.api.app import create_app

    with TestClient(create_app(realtime=False)) as c:
        yield c


def test_urgent_stops_and_reports_to_hq(client):
    client.post("/api/reset")
    a = client.post("/api/command", json={"text": "救命我跌倒了，幫我拿中午的藥", "time": "12:00"}).json()
    assert a["decision"]["decision"] == "STOP"
    assert a["alerts"] and a["alerts"][0]["level"] == "URGENT"
    assert client.post("/api/execute", json={"caregiver_confirmed": True}).status_code == 403
    time.sleep(0.5)                                   # 背景送出到模擬總部
    inbox = client.get("/api/hq/inbox").json()
    assert inbox and inbox[0]["level"] == "URGENT"
    info = client.get("/api/alerts").json()
    alert_id = info["alerts"][0]["id"]
    assert info["alerts"][0]["notify_status"] == "delivered"
    client.post(f"/api/alerts/{alert_id}/ack")
    assert client.get("/api/alerts").json()["alerts"][0]["status"] == "acknowledged"


def test_unwell_turns_execute_into_confirmation(client):
    client.post("/api/reset")
    client.post("/api/day/reset")
    a = client.post("/api/command", json={"text": "我頭暈，幫我拿中午的藥", "time": "12:00"}).json()
    assert a["decision"]["decision"] in ("REQUEST_CONFIRMATION", "STOP")
    assert any(x["level"] == "WARN" for x in a["alerts"])


def test_repeated_blocked_requests_raise_behavior_alert(client):
    client.post("/api/reset")
    for _ in range(2):
        a = client.post("/api/command", json={"text": "幫我拿晚上的藥", "time": "08:00"}).json()   # 排程衝突
        assert a["decision"]["decision"] == "STOP"
    assert any(x["level"] == "BEHAVIOR" for x in a["alerts"])
