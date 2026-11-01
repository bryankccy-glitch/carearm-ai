"""Conformal 風險控制與分類預測集合。

本模組只處理決策的不確定性，不改動 Layer 1 硬性安全限制。
風險控制器使用 calibration split 選門檻；test split 只能用於最後評估。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Hashable, Mapping, Sequence

import numpy as np

from carearm.decision.engine import (
    CONFIRM,
    EXECUTE,
    STOP,
    CalibratedRisk,
    Decision,
    Observation,
    RiskModel,
    build_features,
)


@dataclass
class ConformalRiskController:
    """以有限校正集選擇可自動執行的最大風險門檻。

    ``scores`` 越大代表越危險，``losses`` 為自動執行是否出錯（0/1）。
    門檻的有限樣本上界為 ``n/(n+1) * R_hat + B/(n+1)``。
    """

    threshold: float = float("-inf")
    n: int = 0
    alpha: float = 0.0
    coverage: float = 0.0
    empirical_risk: float = 0.0
    upper_bound: float = 0.0
    B: float = 1.0

    def fit(
        self,
        scores: Sequence[float] | np.ndarray,
        losses: Sequence[float] | np.ndarray,
        alpha: float,
        B: float = 1.0,
    ) -> "ConformalRiskController":
        scores_arr = np.asarray(scores, dtype=float).reshape(-1)
        losses_arr = np.asarray(losses, dtype=float).reshape(-1)
        if scores_arr.size != losses_arr.size:
            raise ValueError("scores 與 losses 的長度必須相同")
        if scores_arr.size == 0:
            raise ValueError("校正資料不可為空")
        if not np.isfinite(scores_arr).all():
            raise ValueError("scores 必須全部是有限數值")
        if not np.isfinite(losses_arr).all() or np.any((losses_arr < 0) | (losses_arr > B)):
            raise ValueError("losses 必須位於 0 到 B 之間")
        if not 0 < alpha < 1:
            raise ValueError("alpha 必須介於 0 與 1 之間")
        if B <= 0:
            raise ValueError("B 必須大於 0")

        self.n = int(scores_arr.size)
        self.alpha = float(alpha)
        self.B = float(B)
        self.threshold = float("-inf")

        # 決策只會在 score 跨過觀測值時改變，所以只需檢查唯一分數。
        for candidate in np.unique(scores_arr):
            selected_loss = losses_arr * (scores_arr <= candidate)
            empirical = float(selected_loss.mean())
            bound = (self.n / (self.n + 1)) * empirical + self.B / (self.n + 1)
            if bound <= self.alpha:
                self.threshold = float(candidate)

        selected = scores_arr <= self.threshold
        self.coverage = float(selected.mean())
        self.empirical_risk = float((losses_arr * selected).mean())
        self.upper_bound = (
            (self.n / (self.n + 1)) * self.empirical_risk + self.B / (self.n + 1)
        )
        return self

    def decide(self, score: float) -> bool:
        """回傳是否允許自動執行。尚未擬合時會保守地回傳 False。"""

        return bool(np.isfinite(score) and score <= self.threshold)

    @property
    def info(self) -> dict:
        return {
            "lambda": self.threshold,
            "threshold": self.threshold,
            "n": self.n,
            "alpha": self.alpha,
            "calibration_coverage": self.coverage,
            "calibration_empirical_risk": self.empirical_risk,
            "risk_upper_bound": self.upper_bound,
            "B": self.B,
        }

    @property
    def lambda_(self) -> float:
        """Python 不能把 ``lambda`` 當屬性名稱使用，因此提供慣用的 ``lambda_``。"""

        return self.threshold


def _probability_of_label(probs, label: Hashable) -> float:
    if isinstance(probs, Mapping):
        if label not in probs:
            raise ValueError(f"機率資料中找不到真實類別：{label}")
        return float(probs[label])
    values = np.asarray(probs, dtype=float).reshape(-1)
    try:
        return float(values[int(label)])
    except (TypeError, ValueError, IndexError) as error:
        raise ValueError("陣列形式的 cal_probs 需要整數類別索引") from error


def fit_prediction_sets(cal_probs, cal_labels, alpha: float) -> float:
    """以 split conformal 校正分類預測集合，回傳非一致性分位數 q_hat。"""

    if not 0 < alpha < 1:
        raise ValueError("alpha 必須介於 0 與 1 之間")
    if len(cal_probs) != len(cal_labels) or len(cal_labels) == 0:
        raise ValueError("cal_probs 與 cal_labels 必須同長度且不可為空")
    scores = np.asarray(
        [1.0 - _probability_of_label(probs, label) for probs, label in zip(cal_probs, cal_labels)],
        dtype=float,
    )
    if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
        raise ValueError("校正機率必須位於 0 到 1 之間")
    n = len(scores)
    rank = ceil((n + 1) * (1 - alpha))
    if rank > n:
        return float("inf")
    return float(np.sort(scores)[rank - 1])


def predict_set(probs, q_hat: float) -> list:
    """回傳所有 ``1 - p(class) <= q_hat`` 的類別。"""

    if isinstance(probs, Mapping):
        if any(not 0 <= float(probability) <= 1 for probability in probs.values()):
            raise ValueError("機率必須位於 0 到 1 之間")
        return [label for label, probability in probs.items() if 1.0 - float(probability) <= q_hat]
    values = np.asarray(probs, dtype=float).reshape(-1)
    if not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError("機率必須位於 0 到 1 之間")
    return [int(index) for index, probability in enumerate(values) if 1.0 - float(probability) <= q_hat]


class ConformalRisk(CalibratedRisk):
    """策略 E：Layer 1 硬性限制 + raw risk 的 conformal 風險控制。"""

    name = "E_conformal"

    def __init__(
        self,
        cfg: dict,
        model: RiskModel | None = None,
        controller: ConformalRiskController | None = None,
    ) -> None:
        super().__init__(cfg, model=model, name=self.name)
        self.controller = controller

    def decide(self, obs: Observation) -> Decision:
        blocked, period, index, cands, rules, reasons = self.layer1(obs)
        if blocked:
            # 父類建立的封鎖決策名稱是動態的 self.name，因此仍會是 E_conformal。
            return blocked
        feats = build_features(obs, period, index, cands)
        det = obs.detections[index]
        if self.model is None or self.controller is None or self.controller.n == 0:
            return Decision(
                self.name,
                CONFIRM,
                period,
                index,
                det.world_xy,
                rules=rules,
                features=feats,
                reasons=reasons + ["Conformal 風險控制器尚未擬合：一律請求確認"],
            )

        raw_risk = float(self.model.raw_risk([feats])[0])
        if raw_risk > 0.5:
            decision = STOP
            explanation = f"未校正風險 {raw_risk:.3f} > 0.50，停止"
        elif self.controller.decide(raw_risk):
            decision = EXECUTE
            explanation = (
                f"未校正風險 {raw_risk:.3f} ≤ conformal 門檻 "
                f"{self.controller.threshold:.3f}，自動執行"
            )
        else:
            decision = CONFIRM
            explanation = (
                f"未校正風險 {raw_risk:.3f} > conformal 門檻 "
                f"{self.controller.threshold:.3f}，請照護者確認"
            )
        return Decision(
            self.name,
            decision,
            period,
            index,
            det.world_xy,
            risk=raw_risk,
            rules=rules,
            features=feats,
            reasons=reasons + ["硬性安全限制全部通過", explanation],
        )
