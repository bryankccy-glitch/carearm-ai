"""僅用於實驗的照護者批准／拒絕模擬，不提供真值座標。"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from carearm.decision.engine import CONFIRM, STOP


@dataclass(frozen=True)
class SimulatedCaregiver:
    error_rate: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.error_rate) or not 0 <= self.error_rate <= 1:
            raise ValueError("照護者錯誤率必須介於 0 與 1 之間")

    def approves(self, scenario_id: int, should_execute: bool) -> bool:
        """同一情境跨策略共用抽樣，與策略順序及其他亂數來源無關。"""
        digest = hashlib.sha256(f"{self.seed}:{scenario_id}".encode()).digest()
        draw = int.from_bytes(digest[:8], "big") / 2**64
        return bool(should_execute) != (draw < self.error_rate)

    def evaluate(self, scenario, decision, execute, correct) -> dict:
        """批准只沿用原提案；停止規則、缺目標均不以真值覆蓋。"""
        requested = decision.decision == CONFIRM
        approved = self.approves(scenario.scenario_id, scenario.should_execute) if requested else None
        row = {
            "caregiver_requested": requested,
            "caregiver_approved": approved,
            "caregiver_decision_error": requested and approved != bool(scenario.should_execute),
            "caregiver_executed": False,
            "caregiver_correct": None,
            "caregiver_status": "未請求" if not requested else "拒絕",
            "caregiver_exec_success": None,
            "caregiver_delivered_box": None,
            "caregiver_failure_stage": None,
            "caregiver_robot_sim_seconds": 0.0,
        }
        if not approved:
            return row
        if any(not rule.passed and rule.blocking_decision == STOP for rule in decision.rules):
            row["caregiver_status"] = "硬性停止限制"
            return row
        if decision.target_period is None or decision.target_xy is None:
            row["caregiver_status"] = "缺少執行目標"
            return row
        result = execute(scenario, decision.target_period, decision.target_xy)
        row.update({
            "caregiver_executed": True,
            "caregiver_correct": correct(scenario, result),
            "caregiver_status": "已執行",
            "caregiver_exec_success": result["success"],
            "caregiver_delivered_box": result["delivered_box"],
            "caregiver_failure_stage": result["failure_stage"],
            "caregiver_robot_sim_seconds": result["sim_seconds"],
        })
        return row
