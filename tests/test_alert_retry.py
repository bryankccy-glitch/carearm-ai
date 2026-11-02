"""通報重送與舊版 SQLite 遷移；所有送出皆使用無網路替身。"""

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from carearm.safety.alerts import Alert, AlertCenter, WARN


@pytest.fixture
def sender(monkeypatch):
    fake = SimpleNamespace(calls=[], fail=True)

    def send(request, timeout):
        fake.calls.append(json.loads(request.data))
        if fake.fail:
            raise OSError("模擬斷線")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        send(request, timeout)
        return Response()

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=False):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("carearm.safety.alerts.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("carearm.safety.alerts.threading.Thread", ImmediateThread)
    return fake


def open_store(path):
    return SimpleNamespace(conn=sqlite3.connect(path), _lock=threading.Lock())


def center(store, **settings):
    return AlertCenter(store, {"alerts": {"webhook_url": "https://example.invalid", **settings}})


def raise_one(ac):
    return ac.raise_alert(Alert(WARN, "身體不適", "身體不適：頭暈", "我頭暈"), {"來源": "測試"})


def test_manual_retry_preserves_payload_and_stops_after_success(tmp_path, sender):
    store = open_store(tmp_path / "alerts.db")
    ac = center(store)
    raise_one(ac)
    assert ac.list()[0]["notify_status"] == "failed"
    assert ac.list()[0]["notify_attempts"] == 1
    sender.fail = False
    assert ac.retry_failed() == 1
    assert ac.list()[0]["notify_status"] == "delivered"
    assert ac.list()[0]["notify_attempts"] == 2
    assert sender.calls[0] == sender.calls[1]
    assert ac.retry_failed() == 0
    center(store)
    assert len(sender.calls) == 2
    store.conn.close()


def test_startup_retry_and_limit_survive_restarts(tmp_path, sender):
    path = tmp_path / "alerts.db"
    store = open_store(path)
    raise_one(center(store))
    store.conn.close()
    for expected in (2, 3, 3):
        store = open_store(path)
        ac = center(store)
        assert ac.list()[0]["notify_attempts"] == expected
        assert ac.list()[0]["notify_status"] == "failed"
        store.conn.close()
    assert len(sender.calls) == 3
    store = open_store(path)
    assert center(store).retry_failed() == 0
    store.conn.close()


def test_legacy_database_migration_and_startup_retry(tmp_path, sender):
    store = open_store(tmp_path / "legacy.db")
    store.conn.execute(
        "CREATE TABLE alerts (id INTEGER PRIMARY KEY, ts TEXT, level TEXT, category TEXT, "
        "message TEXT, transcript TEXT, matched TEXT, status TEXT, notify_status TEXT, "
        "notify_detail TEXT, ack_by TEXT, ack_ts TEXT)"
    )
    for alert_id, status in enumerate(("failed", "delivered", "disabled"), 1):
        store.conn.execute(
            "INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, "2026-01-01", WARN, "身體不適", "頭暈", "我頭暈", '["頭暈"]',
             "acknowledged", status, "既有紀錄", "照護者", "2026-01-02"),
        )
    store.conn.commit()
    sender.fail = False
    ac = center(store)
    rows = {row["id"]: row for row in ac.list()}
    assert len(sender.calls) == 1
    assert sender.calls[0]["transcript"] == "我頭暈"
    assert rows[1]["notify_attempts"] == 2
    assert rows[1]["notify_status"] == "delivered"
    assert rows[1]["status"] == "acknowledged"
    assert rows[1]["ack_by"] == "照護者"
    assert rows[1]["matched"] == ["頭暈"]
    assert rows[2]["notify_attempts"] == 1
    assert rows[3]["notify_attempts"] == 0
    center(store)
    assert len(sender.calls) == 1
    store.conn.close()


def test_disabled_sender_does_not_retry_failed_alert(tmp_path, sender):
    store = open_store(tmp_path / "alerts.db")
    raise_one(center(store))
    ac = center(store, webhook_url="")
    assert ac.retry_failed() == 0
    assert ac.list()[0]["notify_attempts"] == 1
    assert len(sender.calls) == 1
    store.conn.close()


def test_interrupted_delivery_is_sent_again_after_restart(tmp_path, sender):
    path = tmp_path / "alerts.db"
    store = open_store(path)
    raise_one(center(store))
    store.conn.execute("UPDATE alerts SET notify_status = 'sending'")
    store.conn.commit()
    sender.fail = False
    ac = center(store)
    assert ac.list()[0]["notify_status"] == "delivered"
    store.conn.close()


def test_startup_does_not_wait_for_headquarters(tmp_path, monkeypatch):
    started = []

    class RecordingThread:
        def __init__(self, target, args=(), daemon=False):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr("carearm.safety.alerts.threading.Thread", RecordingThread)
    store = open_store(tmp_path / "alerts.db")
    ac = center(store)
    assert started == [ac.retry_failed]
    store.conn.close()
