"""SQLite 紀錄。"""
from carearm.database.store import Store
from carearm.decision.engine import Decision
from carearm.robot.executor import ExecutionResult


def test_logging_roundtrip(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.log_event("vision", "偵測到 3 個標籤", payload={"n": 3})
    s.add_decision("r1", 7, Decision("D", "STOP", "noon", None, None, reasons=["重複取藥"]))
    s.add_execution("r1", 7, "k", ExecutionResult(requested_period="noon", commanded_xy=(0.5, 0.0), success=True))
    events = s.recent_events()
    assert events[0]["message"] == "偵測到 3 個標籤" and events[0]["payload"]["n"] == 3
    assert s.query("SELECT decision FROM decisions WHERE scenario_id=7")[0][0] == "STOP"
    assert s.query("SELECT success FROM executions")[0][0] == 1
