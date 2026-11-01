"""服藥依從性驗證的決策規則。

住民說「我吃完了」→ 手臂先用手腕 RGB-D 相機檢查遞送區的藥盒，再決定要不要回收：

- 盒內是空的（且判斷確定）        → 記錄「已服藥（相機驗證）」，照原計畫把空盒移到回收區
- 盒內還有藥                      → 不回收，提醒住民把藥吃完；同一盒反覆「說吃完但還有藥」就回報總部
- 看不清楚（遮擋、出畫面、雜訊）   → 先換個角度「再看一次」；仍不確定就交給照護者確認

這裡只做決策，不碰模擬器；影像判斷在 carearm.perception.pills。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from carearm.decision.engine import CONFIRM, EXECUTE, STOP, Decision, RuleResult
from carearm.settings import PERIOD_ZH

TAKEN = "verified_taken"          # 相機確認盒內已空
REMAINING = "pills_remaining"     # 盒內仍有藥
UNVERIFIED = "unverified"         # 看不清楚，需照護者確認

VERDICT_ZH = {TAKEN: "已服藥（相機驗證）", REMAINING: "盒內仍有藥", UNVERIFIED: "無法確認"}


@dataclass
class AdherenceResult:
    verdict: str
    period: str | None
    count: int
    looks: int
    reasons: list[str] = field(default_factory=list)
    inspections: list[dict] = field(default_factory=list)
    claims_with_pills: int = 0
    alert: bool = False

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "verdict_zh": VERDICT_ZH[self.verdict],
            "period": self.period,
            "count": self.count,
            "looks": self.looks,
            "reasons": self.reasons,
            "inspections": self.inspections,
            "claims_with_pills": self.claims_with_pills,
            "alert": self.alert,
        }


def verdict_of(inspections: list) -> tuple[str, int]:
    """綜合一次或兩次檢查。只要有一次確定看到藥，就是「仍有藥」；確定為空才算已服藥。"""

    last = inspections[-1]
    seen = [i for i in inspections if i.count > 0]
    if seen:
        return REMAINING, max(i.count for i in seen)
    if not last.uncertain and last.empty:
        return TAKEN, 0
    return UNVERIFIED, 0


def apply_adherence(decision: Decision, result: AdherenceResult, cfg: dict) -> Decision:
    """依驗證結果調整回收決策（就地修改並回傳）。"""

    period_zh = PERIOD_ZH.get(result.period or "", "")
    if result.verdict == TAKEN:
        decision.rules.append(RuleResult("adherence_verified", True, f"手腕相機確認{period_zh}藥盒已空（看了 {result.looks} 次）"))
        decision.reasons.append(f"已確認{period_zh}的藥吃完了，把空盒移到回收區")
        return decision
    if result.verdict == REMAINING:
        msg = f"{period_zh}藥盒裡還有約 {result.count} 顆藥：請先把藥吃完，再說「我吃完了」"
        decision.rules.append(RuleResult("adherence_verified", False, msg, STOP))
        decision.decision = STOP
        decision.target_xy = None
        decision.reasons = [r for r in decision.reasons if not r.startswith("把 ")]   # 不回收了，移除原本的回收說明
        decision.reasons.append(msg)
        if result.alert:
            decision.reasons.append(f"同一盒已 {result.claims_with_pills} 次表示吃完但盒內仍有藥：已回報總部")
        return decision
    msg = f"手腕相機看了 {result.looks} 次仍無法確認{period_zh}藥盒是否已空：請照護者確認後再回收"
    decision.rules.append(RuleResult("adherence_verified", False, msg, CONFIRM))
    if decision.decision == EXECUTE:
        decision.decision = CONFIRM
    decision.reasons.append(msg)
    return decision
