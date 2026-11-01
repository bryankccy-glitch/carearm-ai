"""SQLite 實驗與事件紀錄。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from carearm.settings import RESULTS_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, created_at TEXT, n_scenarios INTEGER, base_seed INTEGER, config_json TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS scenarios (
    run_id TEXT, scenario_id INTEGER, split TEXT, seed INTEGER, payload_json TEXT,
    PRIMARY KEY (run_id, scenario_id)
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT, scenario_id INTEGER, strategy TEXT, decision TEXT, target_period TEXT, risk REAL,
    payload_json TEXT, PRIMARY KEY (run_id, scenario_id, strategy)
);
CREATE TABLE IF NOT EXISTS executions (
    run_id TEXT, scenario_id INTEGER, cache_key TEXT, success INTEGER, lifted_box TEXT, delivered_box TEXT,
    failure_stage TEXT, sim_steps INTEGER, payload_json TEXT, PRIMARY KEY (run_id, scenario_id, cache_key)
);
CREATE TABLE IF NOT EXISTS dose_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts TEXT, period TEXT, event TEXT,
    pills_remaining INTEGER, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, level TEXT, message TEXT, payload_json TEXT
);
"""


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        # CAREARM_DB 可指定資料庫位置（測試用暫存檔，避免污染真正的服藥紀錄）
        self.path = Path(path) if path else Path(os.environ.get("CAREARM_DB") or RESULTS_DIR / "carearm.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _exec(self, sql: str, params=()) -> None:
        with self._lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def add_run(self, run_id: str, n: int, base_seed: int, cfg: dict, notes: str = "") -> None:
        self._exec("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                   (run_id, datetime.now().isoformat(timespec="seconds"), n, base_seed, json.dumps(cfg, ensure_ascii=False), notes))

    def add_scenario(self, run_id: str, scenario) -> None:
        self._exec("INSERT OR REPLACE INTO scenarios VALUES (?,?,?,?,?)",
                   (run_id, scenario.scenario_id, scenario.split, scenario.seed, json.dumps(scenario.to_dict(), ensure_ascii=False)))

    def add_decision(self, run_id: str, scenario_id: int, decision, extra: dict | None = None) -> None:
        payload = decision.to_dict()
        payload.update(extra or {})
        self._exec("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?)",
                   (run_id, scenario_id, decision.strategy, decision.decision, decision.target_period, decision.risk,
                    json.dumps(payload, ensure_ascii=False, default=float)))

    def add_execution(self, run_id: str, scenario_id: int, key: str, result) -> None:
        self._exec("INSERT OR REPLACE INTO executions VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, scenario_id, key, int(result.success), result.lifted_box, result.delivered_box,
                    result.failure_stage, result.sim_steps, json.dumps(result.to_dict(), ensure_ascii=False, default=float)))

    def log_event(self, source: str, message: str, level: str = "INFO", payload: dict | None = None) -> dict:
        ts = datetime.now().isoformat(timespec="milliseconds")
        self._exec("INSERT INTO events (ts, source, level, message, payload_json) VALUES (?,?,?,?,?)",
                   (ts, source, level, message, json.dumps(payload or {}, ensure_ascii=False, default=str)))
        return {"ts": ts, "source": source, "level": level, "message": message, "payload": payload or {}}

    def recent_events(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, source, level, message, payload_json FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"ts": r[0], "source": r[1], "level": r[2], "message": r[3], "payload": json.loads(r[4])} for r in rows]

    def query(self, sql: str, params=()) -> list[tuple]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ 服藥紀錄（重啟後仍保留）
    # event：dispensed 已送藥／verified_taken 相機確認已空／pills_remaining 盒內仍有藥／unverified 無法確認／
    #        caregiver_confirmed 照護者確認／marked_taken、marked_not_taken 手動標記／day_reset 重設當日
    RETRIEVED_EVENTS = ("dispensing", "dispensed", "verified_taken", "caregiver_confirmed", "marked_taken")
    TAKEN_EVENTS = ("verified_taken", "caregiver_confirmed", "marked_taken")

    def log_dose(self, period: str | None, event: str, pills_remaining: int | None = None,
                 detail: dict | None = None, when: datetime | None = None) -> None:
        when = when or datetime.now()
        self._exec("INSERT INTO dose_log (date, ts, period, event, pills_remaining, detail_json) VALUES (?,?,?,?,?,?)",
                   (when.date().isoformat(), when.isoformat(timespec="seconds"), period, event, pills_remaining,
                    json.dumps(detail or {}, ensure_ascii=False, default=str)))

    def dose_rows(self, date: str | None = None, limit: int = 500) -> list[dict]:
        with self._lock:
            if date:
                rows = self.conn.execute("SELECT date, ts, period, event, pills_remaining, detail_json FROM dose_log "
                                         "WHERE date = ? ORDER BY id", (date,)).fetchall()
            else:
                rows = self.conn.execute("SELECT date, ts, period, event, pills_remaining, detail_json FROM dose_log "
                                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"date": r[0], "ts": r[1], "period": r[2], "event": r[3], "pills_remaining": r[4],
                 "detail": json.loads(r[5] or "{}")} for r in rows]

    def dose_state(self, date: str | None = None) -> dict:
        """某一天各時段的狀態。

        retrieved：已送藥（算入重複取藥限制）；taken：相機驗證／照護者／手動確認已服用；
        verdict：最後一次驗證結果；claims_with_pills：說吃完但盒內仍有藥的次數。
        """

        date = date or datetime.now().date().isoformat()
        state: dict[str, dict] = {}
        for row in self.dose_rows(date):
            if row["event"] == "day_reset":
                state = {}
                continue
            p = row["period"]
            if not p:
                continue
            st = state.setdefault(p, {"retrieved": False, "taken": False, "verdict": None, "pills_remaining": None,
                                      "claims_with_pills": 0, "last_ts": None})
            st["last_ts"] = row["ts"]
            if row["event"] in self.RETRIEVED_EVENTS:
                st["retrieved"] = True
            if row["event"] in self.TAKEN_EVENTS:
                st["taken"] = True
            if row["event"] == "dispense_failed":
                st["retrieved"] = False
            if row["event"] == "marked_not_taken":
                st["taken"] = False
            if row["event"] in ("verified_taken", "pills_remaining", "unverified", "caregiver_confirmed"):
                st["verdict"] = row["event"]
                st["pills_remaining"] = row["pills_remaining"]
            if row["event"] == "pills_remaining":
                st["claims_with_pills"] += 1
        return state

