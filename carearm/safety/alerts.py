"""異常狀況偵測與回報總部。

偵測用透明的關鍵字與行為規則（不重新訓練意圖分類器，避免改變實驗所用的模型）：
- 緊急 URGENT：胸痛、呼吸困難、跌倒、昏倒、求救、輕生念頭 → 停止取藥、立即回報
- 身體不適 WARN：頭暈、頭痛、噁心、發燒… → 取藥改為照護者確認、回報
- 異常行為 BEHAVIOR：要求額外劑量／別人的藥、一次全部吃完、拒絕服藥、短時間內多次被擋的請求 → 回報

這是研究模擬的通報流程，不是醫療判斷；關鍵字規則必然有漏報與誤報。
"""

from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

URGENT = "URGENT"
WARN = "WARN"
BEHAVIOR = "BEHAVIOR"
LEVEL_ZH = {URGENT: "緊急", WARN: "身體不適", BEHAVIOR: "異常行為"}

RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (URGENT, "情緒危機", ("不想活", "想死", "活不下去", "自殺", "結束生命", "想不開")),
    (URGENT, "胸痛／心臟", ("胸痛", "胸口痛", "胸口悶", "心臟痛", "心臟很痛")),
    (URGENT, "呼吸困難", ("喘不過氣", "呼吸困難", "不能呼吸", "吸不到氣", "透不過氣")),
    (URGENT, "跌倒", ("跌倒", "摔倒", "跌了一跤", "站不起來", "爬不起來")),
    (URGENT, "意識／求救", ("救命", "昏倒", "快昏倒", "暈倒", "叫救護車", "快不行了")),
    (WARN, "身體不適", ("不舒服", "頭暈", "頭痛", "想吐", "噁心", "嘔吐", "發燒", "肚子痛", "胃痛",
                       "心悸", "沒力氣", "全身無力", "過敏", "起疹子", "好癢", "很痛", "腳痛", "看不清楚")),
    (BEHAVIOR, "要求額外劑量", ("多給我", "多吃", "多拿", "加倍", "吃兩次", "再給我一盒", "再拿一盒",
                             "全部的藥", "所有的藥", "一次吃完", "都給我")),
    (BEHAVIOR, "取用他人藥物", ("別人的藥", "他的藥", "她的藥", "隔壁的藥", "老伴的藥")),
    (BEHAVIOR, "拒絕服藥", ("不想吃藥", "不要吃藥", "不吃了", "藥丟掉", "把藥丟了")),
]

BLOCKED_WINDOW = timedelta(minutes=30)
BLOCKED_LIMIT = 2          # 30 分鐘內第 2 次被安全規則擋下就回報


@dataclass
class Alert:
    level: str
    category: str
    message: str
    transcript: str
    matched: list[str] = field(default_factory=list)
    id: int | None = None
    ts: str = ""
    status: str = "open"                 # open / acknowledged
    notify_status: str = "pending"       # pending / sending / delivered / failed / disabled
    notify_detail: str = ""
    ack_by: str | None = None
    ack_ts: str | None = None
    notify_attempts: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["level_zh"] = LEVEL_ZH.get(self.level, self.level)
        return data


def detect_alerts(text: str) -> list[Alert]:
    """依關鍵字偵測。每個類別最多一筆，依嚴重度排序。"""

    found: dict[str, Alert] = {}
    for level, category, words in RULES:
        hits = [w for w in words if w in text]
        if hits and category not in found:
            found[category] = Alert(level=level, category=category, transcript=text, matched=hits,
                                    message=f"{LEVEL_ZH[level]}：{category}（「{'、'.join(hits)}」）")
    order = {URGENT: 0, WARN: 1, BEHAVIOR: 2}
    return sorted(found.values(), key=lambda a: order[a.level])


class AlertCenter:
    """保存通報（SQLite）並送到總部：webhook 或內建的模擬總部收件匣。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, category TEXT, message TEXT,
        transcript TEXT, matched TEXT, status TEXT, notify_status TEXT, notify_detail TEXT,
        ack_by TEXT, ack_ts TEXT, notify_attempts INTEGER NOT NULL DEFAULT 0,
        notify_payload TEXT
    );
    CREATE TABLE IF NOT EXISTS hq_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, payload TEXT
    );
    """

    def __init__(self, store, cfg: dict) -> None:
        self.store = store
        acfg = cfg.get("alerts", {})
        self.webhook_url = str(acfg.get("webhook_url", "local") or "").strip()
        self.hq_name = acfg.get("headquarters_name", "照護總部")
        self.resident = acfg.get("resident_name", "住民 A")
        self.max_attempts = max(1, int(acfg.get("max_attempts", 3)))
        self.blocked_times: list[datetime] = []
        with store._lock:
            store.conn.executescript(self.SCHEMA)
            columns = {row[1] for row in store.conn.execute("PRAGMA table_info(alerts)")}
            if "notify_attempts" not in columns:
                store.conn.execute("ALTER TABLE alerts ADD COLUMN notify_attempts INTEGER NOT NULL DEFAULT 0")
                store.conn.execute("UPDATE alerts SET notify_attempts = 1 WHERE notify_status IN ('failed', 'delivered')")
            if "notify_payload" not in columns:
                store.conn.execute("ALTER TABLE alerts ADD COLUMN notify_payload TEXT")
            # 上次送到一半就中斷的通報不算成功，改回失敗才會再送
            store.conn.execute("UPDATE alerts SET notify_status = 'failed' WHERE notify_status = 'sending'")
            store.conn.commit()
        # 總部連不上時不可拖住服務啟動
        threading.Thread(target=self.retry_failed, daemon=True).start()

    # ---------------------------------------------------------- behavior rule
    def note_blocked_request(self, reason: str, transcript: str) -> Alert | None:
        now = datetime.now()
        self.blocked_times = [t for t in self.blocked_times if now - t < BLOCKED_WINDOW] + [now]
        if len(self.blocked_times) >= BLOCKED_LIMIT:
            n = len(self.blocked_times)
            return Alert(level=BEHAVIOR, category="重複被擋的取藥請求", transcript=transcript, matched=[reason],
                         message=f"異常行為：30 分鐘內第 {n} 次取藥請求被安全規則擋下（最近一次：{reason}）")
        return None

    # ---------------------------------------------------------- record + notify
    def raise_alert(self, alert: Alert, context: dict | None = None) -> Alert:
        alert.ts = datetime.now().isoformat(timespec="seconds")
        if not self.webhook_url:
            alert.notify_status, alert.notify_detail = "disabled", "未設定總部 webhook，只記錄在本機"
        with self.store._lock:
            cur = self.store.conn.execute(
                "INSERT INTO alerts (ts, level, category, message, transcript, matched, status, notify_status, notify_detail) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (alert.ts, alert.level, alert.category, alert.message, alert.transcript, json.dumps(alert.matched, ensure_ascii=False),
                 alert.status, alert.notify_status, alert.notify_detail),
            )
            self.store.conn.commit()
            alert.id = int(cur.lastrowid)
        if self.webhook_url:
            payload = {
                "type": "carearm_alert",
                "alert_id": alert.id,
                "time": alert.ts,
                "level": alert.level,
                "level_zh": LEVEL_ZH[alert.level],
                "category": alert.category,
                "message": alert.message,
                "resident": self.resident,
                "transcript": alert.transcript,
                "context": context or {},
                "headquarters": self.hq_name,
                "disclaimer": "研究模擬通報，非醫療診斷",
            }
            self._update(alert.id, notify_payload=json.dumps(payload, ensure_ascii=False))
            threading.Thread(target=self._deliver, args=(alert.id, payload), daemon=True).start()
        return alert

    def retry_failed(self) -> int:
        """同步重送一輪失敗通報，回傳實際嘗試筆數；次數包含首次送出。"""
        if not self.webhook_url:
            return 0
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT id, notify_payload, ts, level, category, message, transcript "
                "FROM alerts WHERE notify_status = 'failed' AND notify_attempts < ? ORDER BY id",
                (self.max_attempts,),
            ).fetchall()
        attempted = 0
        for row in rows:
            # 舊版沒有保存 payload，使用原通報資料補建。
            payload = json.loads(row[1]) if row[1] else {
                "type": "carearm_alert", "alert_id": row[0], "time": row[2],
                "level": row[3], "level_zh": LEVEL_ZH.get(row[3], row[3]),
                "category": row[4], "message": row[5], "transcript": row[6],
                "resident": self.resident, "headquarters": self.hq_name,
                "context": {}, "disclaimer": "研究模擬通報，非醫療診斷",
            }
            attempted += self._deliver(row[0], payload, retry=True)
        return attempted

    def _deliver(self, alert_id: int, payload: dict, *, retry: bool = False) -> bool:
        # 送出前先原子登記次數，避免併發呼叫重複取得同一筆通報。
        with self.store._lock:
            cur = self.store.conn.execute(
                "UPDATE alerts SET notify_attempts = notify_attempts + 1, notify_status = 'sending' "
                "WHERE id = ? AND notify_status = ? AND notify_attempts < ?",
                (alert_id, "failed" if retry else "pending", self.max_attempts),
            )
            self.store.conn.commit()
            if not cur.rowcount:
                return False
        try:
            if self.webhook_url == "local":
                self.receive_hq(payload)
                status, detail = "delivered", f"已送達{self.hq_name}（內建模擬收件匣）"
            else:
                req = urllib.request.Request(self.webhook_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    status, detail = "delivered", f"已送達總部 webhook（HTTP {resp.status}）"
        except Exception as error:  # noqa: BLE001
            status, detail = "failed", f"送出失敗：{error}"
        self._update(alert_id, notify_status=status, notify_detail=detail)
        return True

    def receive_hq(self, payload: dict) -> None:
        """內建的模擬總部收件匣（webhook_url = "local"）。"""

        with self.store._lock:
            self.store.conn.execute("INSERT INTO hq_inbox (received_ts, payload) VALUES (?, ?)",
                                    (datetime.now().isoformat(timespec="seconds"), json.dumps(payload, ensure_ascii=False)))
            self.store.conn.commit()

    def _update(self, alert_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.store._lock:
            self.store.conn.execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*fields.values(), alert_id))
            self.store.conn.commit()

    def acknowledge(self, alert_id: int, by: str = "照護者") -> None:
        self._update(alert_id, status="acknowledged", ack_by=by, ack_ts=datetime.now().isoformat(timespec="seconds"))

    def list(self, limit: int = 100) -> list[dict]:
        with self.store._lock:
            rows = self.store.conn.execute(
                "SELECT id, ts, level, category, message, transcript, matched, status, notify_status, notify_detail, ack_by, ack_ts, notify_attempts "
                "FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            a = Alert(id=r[0], ts=r[1], level=r[2], category=r[3], message=r[4], transcript=r[5], matched=json.loads(r[6] or "[]"),
                      status=r[7], notify_status=r[8], notify_detail=r[9] or "", ack_by=r[10], ack_ts=r[11], notify_attempts=r[12])
            out.append(a.to_dict())
        return out

    def hq_inbox(self, limit: int = 100) -> list[dict]:
        with self.store._lock:
            rows = self.store.conn.execute("SELECT received_ts, payload FROM hq_inbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"received_ts": r[0], **json.loads(r[1])} for r in rows]
