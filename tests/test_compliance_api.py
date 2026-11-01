"""Panda 送藥後，以真實手腕相機畫面驗證服藥的端到端測試。"""

import time

from fastapi.testclient import TestClient

from carearm.api.app import create_app


def wait_idle(client, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        status = client.get("/api/status").json()
        if not status["busy"] and status["last_execution"] is not None:
            return status
        time.sleep(0.2)
    raise TimeoutError("Panda 執行逾時")


def deliver_noon(client):
    client.post("/api/day/reset")      # 服藥紀錄跨重啟保留：每個測試從新的一天開始
    client.post("/api/reset")
    analysis = client.post("/api/command", json={"text": "幫我拿中午的藥", "time": "12:00"}).json()
    response = client.post("/api/execute", json={
        "caregiver_confirmed": analysis["decision"]["decision"] != "EXECUTE",
    })
    assert response.status_code == 200
    status = wait_idle(client)
    assert status["last_execution"]["success"]
    assert status["compliance"]["noon"]["status"] == "WAITING"
    assert "noon" in status["retrieved_today"]
    assert "noon" not in status["taken_today"]
    wrist = client.get("/api/camera.png?live=true&view=wrist")
    assert wrist.status_code == 200 and wrist.content[:2] == b"\xff\xd8"


def test_delivery_intake_and_wrist_compliance_end_to_end():
    with TestClient(create_app(realtime=False)) as client:
        deliver_noon(client)
        partial = client.post("/api/simulation/intake", json={"period": "noon", "count": 1}).json()
        assert partial["status"] == "PARTIAL", partial
        assert partial["remaining_count"] == 1

        complete = client.post("/api/simulation/intake", json={"period": "noon", "count": 99}).json()
        assert complete["status"] == "TAKEN", complete
        assert complete["remaining_count"] == 0
        assert "noon" in client.get("/api/status").json()["taken_today"]


def test_dropped_pill_is_seen_outside_cup_and_reported():
    with TestClient(create_app(realtime=False)) as client:
        deliver_noon(client)
        dropped = client.post("/api/simulation/intake", json={
            "period": "noon", "count": 1, "drop_on_table": True,
        }).json()
        assert dropped["status"] == "POSSIBLE_DROP", dropped
        assert dropped["dropped_regions"] >= 1
        assert dropped["alert"]["category"] == "服藥依從性異常"
