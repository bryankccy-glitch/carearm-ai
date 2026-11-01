"""Conformal 風險控制與分類預測集合測試。"""

import numpy as np

from carearm.decision.conformal import (
    ConformalRisk,
    ConformalRiskController,
    fit_prediction_sets,
    predict_set,
)
from carearm.decision.engine import CONFIRM, EXECUTE, STOP, Observation, RiskModel
from carearm.decision.schedule import MedicationSchedule, to_minutes
from carearm.perception.detector import LabelDetection
from carearm.speech.intent import IntentResult


def _detection(period, dots, xy):
    probs = {p: (0.98 if p == period else 0.01) for p in ("morning", "noon", "evening")}
    return LabelDetection(
        bbox=(0, 0, 10, 10), center_px=(0, 0), world_xy=xy, area_px=900,
        expected_area_px=900, area_ratio=1.0, rectangularity=0.95, mean_hue_deg=0,
        mean_sat=150, mean_val=200, dot_count=dots, raw_scores=probs,
        calibrated_probs=probs, predicted_period=period,
    )


def _observation(cfg, *, taken=()):
    intent = IntentResult(
        transcript="幫我拿中午的藥", intent="SPECIFY_NOON", raw_confidence=0.99,
        confidence=0.99, margin=0.98, probs={}, keyword_period="noon", period="noon",
        oov_ratio=0.0,
    )
    detections = [
        _detection("morning", 1, (0.5, -0.12)),
        _detection("noon", 2, (0.5, 0.0)),
        _detection("evening", 3, (0.5, 0.12)),
    ]
    schedule = MedicationSchedule.from_config(cfg, taken).check(to_minutes("12:00"), "noon")
    return Observation(intent, schedule, detections)


class _FixedRawRisk(RiskModel):
    def __init__(self, value):
        super().__init__(calibrate=False)
        self.value = value

    def raw_risk(self, rows):
        return np.full(len(rows), self.value)


def test_conformal_risk_repeated_splits_controls_average_risk():
    """在交換且風險隨 score 上升的合成資料上，重複切分的平均損失受控。"""

    rng = np.random.default_rng(20261023)
    alpha = 0.10
    risks, coverages = [], []
    for _ in range(200):
        cal_scores = rng.random(199)
        cal_losses = rng.binomial(1, 0.25 * cal_scores)
        controller = ConformalRiskController().fit(cal_scores, cal_losses, alpha)

        test_scores = rng.random(1000)
        test_losses = rng.binomial(1, 0.25 * test_scores)
        selected = test_scores <= controller.threshold
        risks.append(float((test_losses * selected).mean()))
        coverages.append(float(selected.mean()))

    # 有限次 Monte Carlo 允許 0.01 的數值波動；覆蓋率另外輸出便於研究紀錄。
    assert np.mean(risks) <= alpha + 0.01
    print(f"平均測試風險={np.mean(risks):.4f}，平均覆蓋率={np.mean(coverages):.4f}")


def test_threshold_is_monotone_in_alpha():
    scores = np.linspace(0.01, 0.99, 100)
    losses = (scores > 0.55).astype(float)
    thresholds = [ConformalRiskController().fit(scores, losses, alpha).threshold for alpha in (0.03, 0.08, 0.15)]
    assert thresholds == sorted(thresholds)


def test_no_feasible_threshold_never_executes():
    controller = ConformalRiskController().fit([0.1, 0.2, 0.3], [0, 0, 0], alpha=0.01)
    assert controller.threshold == float("-inf")
    assert controller.coverage == 0.0
    assert not controller.decide(-100.0)


def test_prediction_sets_repeated_splits_have_marginal_coverage():
    rng = np.random.default_rng(709)
    alpha = 0.10
    coverages = []
    for _ in range(200):
        cal_probs = rng.dirichlet([1.2, 1.2, 1.2], size=199)
        cal_labels = np.asarray([rng.choice(3, p=p) for p in cal_probs])
        q_hat = fit_prediction_sets(cal_probs, cal_labels, alpha)

        test_probs = rng.dirichlet([1.2, 1.2, 1.2], size=500)
        test_labels = np.asarray([rng.choice(3, p=p) for p in test_probs])
        covered = [label in predict_set(probs, q_hat) for probs, label in zip(test_probs, test_labels)]
        coverages.append(float(np.mean(covered)))

    assert np.mean(coverages) >= 1 - alpha
    print(f"預測集合平均覆蓋率={np.mean(coverages):.4f}")


def test_prediction_sets_support_named_classes():
    cal_probs = [
        {"morning": 0.8, "noon": 0.1, "evening": 0.1},
        {"morning": 0.1, "noon": 0.8, "evening": 0.1},
        {"morning": 0.1, "noon": 0.1, "evening": 0.8},
    ]
    q_hat = fit_prediction_sets(cal_probs, ["morning", "noon", "evening"], alpha=0.5)
    assert predict_set({"morning": 0.45, "noon": 0.45, "evening": 0.10}, q_hat) == []


def test_conformal_strategy_uses_raw_risk_after_same_layer1(cfg):
    controller = ConformalRiskController().fit([0.05, 0.10, 0.20, 0.40], [0, 0, 0, 1], alpha=0.25)
    assert ConformalRisk(cfg, _FixedRawRisk(0.10), controller).decide(_observation(cfg)).decision == EXECUTE
    assert ConformalRisk(cfg, _FixedRawRisk(0.30), controller).decide(_observation(cfg)).decision == CONFIRM
    assert ConformalRisk(cfg, _FixedRawRisk(0.60), controller).decide(_observation(cfg)).decision == STOP

    duplicate = ConformalRisk(cfg, _FixedRawRisk(0.0), controller).decide(_observation(cfg, taken={"noon"}))
    assert duplicate.decision == STOP
    assert duplicate.risk is None
