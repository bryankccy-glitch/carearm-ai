"""硬性安全限制、錯盒拒絕、重複取藥阻擋、風險門檻行為。"""
import numpy as np
import pytest

from carearm.decision.engine import (
    CONFIRM, EXECUTE, STOP, CalibratedRisk, FixedThreshold, Observation, RiskModel, RuleBased, VisionOnly,
)
from carearm.decision.schedule import MedicationSchedule, to_minutes
from carearm.perception.detector import LabelDetection
from carearm.speech.intent import IntentResult


def det(period, dots, xy, conf=0.99):
    probs = {p: (conf if p == period else (1 - conf) / 2) for p in ("morning", "noon", "evening")}
    return LabelDetection(bbox=(0, 0, 10, 10), center_px=(0, 0), world_xy=xy, area_px=900, expected_area_px=900,
                          area_ratio=1.0, rectangularity=0.95, mean_hue_deg=0, mean_sat=150, mean_val=200,
                          dot_count=dots, raw_scores=probs, calibrated_probs=probs, predicted_period=period)


def intent(name, period=None, conf=0.99):
    return IntentResult(transcript="t", intent=name, raw_confidence=conf, confidence=conf, margin=conf - 0.01,
                        probs={}, keyword_period=period, period=period, oov_ratio=0.0)


def obs(cfg, it, time, dets, taken=()):
    return Observation(it, MedicationSchedule.from_config(cfg, taken).check(to_minutes(time), it.period), dets)


GOOD = [lambda: det("morning", 1, (0.5, -0.12)), lambda: det("noon", 2, (0.5, 0.0)), lambda: det("evening", 3, (0.5, 0.12))]


class AlwaysLow(RiskModel):
    """假的「永遠極低風險」模型：用來證明低風險無法覆蓋硬性限制。"""

    def __init__(self):
        super().__init__()
        self.execute_below, self.stop_above = 0.05, 0.5

    def risk(self, rows):
        return np.zeros(len(rows))


def test_rule_based_executes_clean_case(cfg):
    d = RuleBased(cfg).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", [f() for f in GOOD]))
    assert d.decision == EXECUTE and d.target_period == "noon"


def test_wrong_box_identity_rejected(cfg):
    # 藍色標籤但識別碼是 1 點（早上盒的碼）→ 錯盒，必須停止
    dets = [det("morning", 1, (0.5, -0.12)), det("noon", 1, (0.5, 0.0)), det("evening", 3, (0.5, 0.12))]
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", dets))
    assert d.decision == STOP
    assert any(r.rule == "box_identity" and not r.passed for r in d.rules)


def test_duplicate_retrieval_blocked_even_with_low_risk(cfg):
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", [f() for f in GOOD], taken={"noon"}))
    assert d.decision == STOP and d.risk is None


def test_schedule_conflict_blocked(cfg):
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("SPECIFY_EVENING", "evening"), "08:00", [f() for f in GOOD]))
    assert d.decision == STOP
    assert any(r.rule == "schedule_consistency" and not r.passed for r in d.rules)


def test_indistinguishable_targets_blocked(cfg):
    dets = [f() for f in GOOD] + [det("noon", 2, (0.56, -0.06))]
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", dets))
    assert d.decision == STOP
    assert any(r.rule == "unique_target" and not r.passed for r in d.rules)


def test_no_target_blocked(cfg):
    dets = [GOOD[0](), GOOD[2]()]
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", dets))
    assert d.decision == STOP


def test_ambiguous_request_asks_confirmation(cfg):
    d = CalibratedRisk(cfg, AlwaysLow()).decide(obs(cfg, intent("REQUEST_MEDICATION"), "10:40", [f() for f in GOOD]))
    assert d.decision == CONFIRM and d.risk is None


def test_already_taken_never_executes(cfg):
    for strat in (RuleBased(cfg), FixedThreshold(cfg), CalibratedRisk(cfg, AlwaysLow()), VisionOnly(cfg)):
        d = strat.decide(obs(cfg, intent("MEDICATION_ALREADY_TAKEN"), "12:00", [f() for f in GOOD]))
        assert d.decision != EXECUTE


def test_risk_threshold_behaviour():
    m = RiskModel()
    m.execute_below, m.stop_above = 0.05, 0.5
    m.degenerate = False
    assert m.decide(0.01) == EXECUTE
    assert m.decide(0.05) == EXECUTE
    assert m.decide(0.2) == CONFIRM
    assert m.decide(0.51) == STOP


def test_low_confidence_triggers_confirmation_in_baseline_c(cfg):
    dets = [GOOD[0](), det("noon", 2, (0.5, 0.0), conf=0.6), GOOD[2]()]
    d = FixedThreshold(cfg).decide(obs(cfg, intent("SPECIFY_NOON", "noon"), "12:00", dets))
    assert d.decision == CONFIRM


def test_risk_model_fit_and_calibration():
    rng = np.random.default_rng(0)
    rows, y = [], []
    from carearm.decision.engine import ALL_FEATURES
    for _ in range(300):
        bad = rng.random() < 0.2
        r = {f: float(rng.normal()) for f in ALL_FEATURES}
        r["intent_conf"] = float(rng.uniform(0.3, 0.7) if bad else rng.uniform(0.85, 1.0))
        rows.append(r)
        y.append(int(bad))
    m = RiskModel()
    m.fit(rows[:200], y[:200])
    m.fit_calibration(rows[200:], y[200:], max_auto_error=0.02)
    risk = m.risk(rows[200:])
    executed = risk <= m.execute_below
    assert executed.any()
    assert np.asarray(y[200:])[executed].mean() <= 0.02


def test_calibration_never_inverts_risk_ranking():
    """校正集太小、唯一的錯誤樣本風險很低時，Platt 可能學到負斜率；必須拒絕反轉排序。"""
    from carearm.decision.engine import ALL_FEATURES
    rng = np.random.default_rng(1)
    rows = [{f: float(rng.normal()) for f in ALL_FEATURES} for _ in range(80)]
    y = [1 if r["intent_conf"] < -1.0 else 0 for r in rows]
    m = RiskModel()
    m.fit(rows[:60], y[:60])
    cal = rows[60:]
    raw = m.raw_risk(cal)
    cal_y = [0] * len(cal)
    cal_y[int(np.argmin(raw))] = 1          # 唯一錯誤樣本剛好是原始風險最低者
    m.fit_calibration(cal, cal_y)
    order_raw = np.argsort(m.raw_risk(cal))
    order_cal = np.argsort(m.risk(cal))
    assert np.array_equal(order_raw, order_cal)


def test_finish_command_detection():
    from carearm.decision.engine import is_finish_command
    assert is_finish_command(intent("MEDICATION_ALREADY_TAKEN"))
    for text in ("幫我把空盒收走", "吃完藥了幫我收一下", "把藥盒收回去"):
        it = IntentResult(transcript=text, intent="UNKNOWN", raw_confidence=0.5, confidence=0.5, margin=0.1,
                          probs={}, keyword_period=None, period=None, oov_ratio=0.0)
        assert is_finish_command(it)
    it = IntentResult(transcript="我要吃藥了", intent="REQUEST_MEDICATION", raw_confidence=0.9, confidence=0.99,
                      margin=0.9, probs={}, keyword_period=None, period=None, oov_ratio=0.0)
    assert not is_finish_command(it)


def test_collect_decision_rules(cfg):
    from carearm.decision.engine import collect_decision
    assert collect_decision([], cfg).decision == STOP                                     # 遞送區沒盒子
    assert collect_decision([det("noon", 2, (0.3, -0.2)), det("morning", 1, (0.32, -0.18))], cfg).decision == CONFIRM
    assert collect_decision([det("noon", 1, (0.3, -0.2))], cfg).decision == CONFIRM       # 顏色與識別碼不一致
    two = [det("noon", 2, (0.3, -0.2)), det("morning", 1, (0.22, -0.2))]
    picked = collect_decision(two, cfg, preferred_period="morning")
    assert picked.decision == EXECUTE and picked.target_period == "morning" and picked.target_xy == (0.22, -0.2)
    ok = collect_decision([det("evening", 3, (0.3, -0.2))], cfg)
    assert ok.decision == EXECUTE and ok.target_period == "evening" and ok.action == "collect"
