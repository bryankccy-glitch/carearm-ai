"""不確定性感知多模態決策引擎（兩層安全架構）＋ 三個比較基線。

Layer 1：硬性安全限制 → 任一違反即不得自動執行（低風險分數不能覆蓋）。
Layer 2：通過硬性限制後，以校正後 Logistic Regression 估計「執行會出錯」的機率。

決策：EXECUTE / REQUEST_CONFIRMATION / STOP
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from carearm.decision.schedule import ScheduleCheck
from carearm.perception.detector import LabelDetection
from carearm.settings import MODELS_DIR, PERIOD_ZH, PERIODS, box_specs
from carearm.speech.intent import IntentResult

EXECUTE = "EXECUTE"
CONFIRM = "REQUEST_CONFIRMATION"
STOP = "STOP"

FEATURE_GROUPS = {
    "speech": ["intent_conf", "intent_margin", "keyword_agrees", "oov_ratio"],
    "vision": ["vision_conf", "vision_margin", "area_ratio", "rectangularity", "label_brightness", "n_color_candidates"],
    "schedule": ["minutes_from_window", "explicit_period", "n_plausible"],
}
ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]


@dataclass
class RuleResult:
    rule: str
    passed: bool
    message: str
    blocking_decision: str = STOP    # 違反時的決策（缺資訊/需澄清 → 詢問；其他 → 停止）


@dataclass
class Observation:
    intent: IntentResult
    schedule: ScheduleCheck
    detections: list[LabelDetection]


@dataclass
class Decision:
    strategy: str
    decision: str
    target_period: str | None
    target_index: int | None
    target_xy: tuple[float, float] | None
    risk: float | None = None
    rules: list[RuleResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    action: str = "retrieve"          # retrieve：取藥送到遞送區；collect：吃完藥後把空盒收到回收區

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


# ----------------------------------------------------------------- 目標選擇
def resolve_target_period(obs: Observation) -> tuple[str | None, list[str]]:
    """依意圖 + 排程決定目標時段；不做安全判斷。"""

    intent = obs.intent
    if intent.intent in ("SPECIFY_MORNING", "SPECIFY_NOON", "SPECIFY_EVENING"):
        return intent.period, [f"使用者指定{PERIOD_ZH[intent.period]}的藥"]
    if intent.intent == "REQUEST_MEDICATION":
        if obs.schedule.target:
            return obs.schedule.target, [f"未指定時段，依排程（{obs.schedule.time}）推定為{PERIOD_ZH[obs.schedule.target]}"]
        return None, ["未指定時段，且排程無法唯一推定"]
    return None, []


def color_candidates(obs: Observation, period: str, *, calibrated: bool) -> list[int]:
    out = []
    for i, det in enumerate(obs.detections):
        probs = det.calibrated_probs if (calibrated and det.calibrated_probs) else det.raw_scores
        if max(probs, key=probs.get) == period:
            out.append(i)
    return out


def select_target(obs: Observation, period: str, cfg: dict, *, use_identity: bool, calibrated: bool = True) -> tuple[int | None, list[int]]:
    """回傳（選定偵測索引, 同色候選列表）。use_identity=True 時優先選識別碼相符者。"""

    expected_dots = box_specs(cfg)[period].id_dots
    cands = color_candidates(obs, period, calibrated=calibrated)
    if not cands:
        return None, cands

    def conf(i: int) -> float:
        det = obs.detections[i]
        probs = det.calibrated_probs if (calibrated and det.calibrated_probs) else det.raw_scores
        return probs[period]

    if use_identity:
        matching = [i for i in cands if obs.detections[i].dot_count == expected_dots]
        pool = matching or cands
    else:
        pool = cands
    return max(pool, key=conf), cands


# --------------------------------------------------------------- Layer 1
def hard_constraints(obs: Observation, cfg: dict, period: str | None, index: int | None, cands: list[int]) -> list[RuleResult]:
    intent = obs.intent.intent
    rules: list[RuleResult] = []
    if intent == "MEDICATION_ALREADY_TAKEN":
        rules.append(RuleResult("no_retrieval_requested", False, "使用者表示已服藥：只記錄，不取藥"))
        return rules
    if intent in ("CANCEL", "UNKNOWN"):
        msg = "使用者取消" if intent == "CANCEL" else "無法辨識取藥意圖"
        rules.append(RuleResult("no_retrieval_requested", False, msg, CONFIRM if intent == "UNKNOWN" else STOP))
        return rules

    sched = obs.schedule
    if sched.explicit:
        rules.append(RuleResult("schedule_consistency", not sched.conflict,
                                "指定時段與排程一致" if not sched.conflict else
                                f"指定的{PERIOD_ZH[sched.requested]}時段與目前時間 {sched.time} 的排程衝突"))
    else:
        rules.append(RuleResult("required_information", not sched.needs_clarification,
                                "排程可唯一推定時段" if not sched.needs_clarification else
                                f"目前時間 {sched.time} 有 {len(sched.plausible)} 個可能時段，需要使用者澄清",
                                CONFIRM))
    if period is None:
        return rules
    rules.append(RuleResult("duplicate_retrieval", not sched.duplicate,
                            "今天尚未取過此時段藥盒" if not sched.duplicate else f"今天已取過{PERIOD_ZH[period]}的藥（重複取藥）"))
    rules.append(RuleResult("target_detected", index is not None,
                            "已偵測到目標藥盒" if index is not None else f"影像中找不到{PERIOD_ZH[period]}標籤的藥盒"))
    if index is not None:
        expected = box_specs(cfg)[period].id_dots
        det = obs.detections[index]
        rules.append(RuleResult("box_identity", det.dot_count == expected,
                                f"識別碼相符（{det.dot_count} 點 = {box_specs(cfg)[period].box_id}）" if det.dot_count == expected else
                                f"識別碼不符：看到 {det.dot_count} 點，預期 {expected} 點（{box_specs(cfg)[period].box_id}）"))
        same = [i for i in cands if obs.detections[i].dot_count == det.dot_count]
        rules.append(RuleResult("unique_target", len(same) <= 1,
                                "目標唯一" if len(same) <= 1 else f"偵測到 {len(same)} 個無法區分的目標藥盒"))
    return rules


def first_violation(rules: list[RuleResult]) -> RuleResult | None:
    for r in rules:
        if not r.passed:
            return r
    return None


# --------------------------------------------------------------- 特徵
def build_features(obs: Observation, period: str, index: int, cands: list[int]) -> dict[str, float]:
    det = obs.detections[index]
    probs = det.calibrated_probs or det.raw_scores
    ordered = sorted(probs.values(), reverse=True)
    intent = obs.intent
    agrees = 1.0 if (intent.keyword_period is not None and intent.keyword_period == period) else (0.5 if intent.keyword_period is None else 0.0)
    return {
        "intent_conf": intent.confidence,
        "intent_margin": intent.margin,
        "keyword_agrees": agrees,
        "oov_ratio": intent.oov_ratio,
        "vision_conf": float(probs[period]),
        "vision_margin": float(ordered[0] - ordered[1]),
        "area_ratio": det.area_ratio,
        "rectangularity": det.rectangularity,
        "label_brightness": det.mean_val / 255.0,
        "n_color_candidates": float(len(cands)),
        "minutes_from_window": min(obs.schedule.minutes_from_window, 400.0) / 100.0,
        "explicit_period": 1.0 if obs.schedule.explicit else 0.0,
        "n_plausible": float(len(obs.schedule.plausible)),
    }


# --------------------------------------------------------------- Layer 2
class RiskModel:
    """Logistic Regression 風險模型 + 校正集 Platt scaling + 校正集門檻選擇。"""

    def __init__(self, features: list[str] | None = None, *, calibrate: bool = True) -> None:
        self.features = list(features or ALL_FEATURES)
        self.calibrate_enabled = calibrate
        self.scaler = StandardScaler()
        self.lr = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")
        self.platt = LogisticRegression(C=1e4, max_iter=2000)
        self.execute_below = 0.05
        self.stop_above = 0.50
        self.info: dict = {}

    def _x(self, rows: list[dict]) -> np.ndarray:
        return np.asarray([[r[f] for f in self.features] for r in rows], dtype=float)

    def fit(self, rows: list[dict], labels: list[int]) -> None:
        y = np.asarray(labels)
        self.degenerate = len(set(y)) < 2
        if self.degenerate:
            # 訓練資料只有單一類別：無法學習風險 → 保守處理，風險固定 0.5（一律請求確認）
            self.info["degenerate"] = True
            return
        self.lr.fit(self.scaler.fit_transform(self._x(rows)), y)

    def raw_risk(self, rows: list[dict]) -> np.ndarray:
        if getattr(self, "degenerate", False):
            return np.full(len(rows), 0.5)
        return self.lr.predict_proba(self.scaler.transform(self._x(rows)))[:, 1]

    def fit_calibration(self, rows: list[dict], labels: list[int], *, max_auto_error: float = 0.02,
                        default_execute_below: float = 0.05, default_stop_above: float = 0.5) -> None:
        y = np.asarray(labels)
        if getattr(self, "degenerate", False):
            self.execute_below, self.stop_above = 0.0, 1.0
            self.info.update({"calibrated": False, "execute_below": 0.0, "note": "訓練集無錯誤樣本，模型退化為一律確認"})
            return
        if not self.calibrate_enabled:
            self.execute_below, self.stop_above = default_execute_below, default_stop_above
            self.info.update({"calibrated": False, "execute_below": self.execute_below, "stop_above": self.stop_above})
            return
        raw = self.raw_risk(rows)
        logit = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / np.clip(1 - raw, 1e-6, 1)).reshape(-1, 1)
        self._platt_ok = False
        if len(set(y)) >= 2:
            self.platt.fit(logit, y)
            # 校正必須保持風險排序（單調遞增）；斜率 ≤ 0 代表校正集太小或無資訊，退回未校正分數
            self._platt_ok = bool(self.platt.coef_[0][0] > 0)
            self.info["platt_slope"] = float(self.platt.coef_[0][0])
        self.info["platt_applied"] = self._platt_ok
        risk = self.risk(rows)
        # 選擇性執行門檻：在校正集上，自動執行者的錯誤率 ≤ max_auto_error 的最大門檻
        candidates = np.unique(np.concatenate([risk, [0.0]]))
        best = 0.0
        for t in candidates:
            executed = risk <= t
            if executed.sum() == 0:
                continue
            if y[executed].mean() <= max_auto_error:
                best = max(best, float(t))
        self.execute_below = float(best) + 1e-9
        self.stop_above = max(default_stop_above, self.execute_below)
        self.info.update({
            "calibrated": True,
            "execute_below": self.execute_below,
            "stop_above": self.stop_above,
            "max_auto_error_target": max_auto_error,
            "calibration_n": int(len(y)),
            "calibration_positive_rate": float(y.mean()),
            "calibration_coverage": float((risk <= self.execute_below).mean()),
        })

    def risk(self, rows: list[dict]) -> np.ndarray:
        raw = self.raw_risk(rows)
        if not self.calibrate_enabled or not getattr(self, "_platt_ok", False):
            return raw
        logit = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / np.clip(1 - raw, 1e-6, 1)).reshape(-1, 1)
        return self.platt.predict_proba(logit)[:, 1]

    def decide(self, risk: float) -> str:
        if getattr(self, "degenerate", False):
            return CONFIRM
        if risk <= self.execute_below:
            return EXECUTE
        if risk > self.stop_above:
            return STOP
        return CONFIRM

    def coefficients(self) -> dict[str, float]:
        if getattr(self, "degenerate", False):
            return {}
        return {f: float(c) for f, c in zip(self.features, self.lr.coef_[0])}

    def save(self, name: str = "risk_model") -> Path:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / f"{name}.joblib"
        joblib.dump(self, path)
        (MODELS_DIR / f"{name}.json").write_text(
            json.dumps({"features": self.features, "coefficients": self.coefficients(), **self.info}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load(name: str = "risk_model") -> "RiskModel":
        return joblib.load(MODELS_DIR / f"{name}.joblib")


# --------------------------------------------------------------- 策略
class Strategy:
    name = "base"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def decide(self, obs: Observation) -> Decision:
        raise NotImplementedError


class VisionOnly(Strategy):
    """Baseline A：只依意圖指出的顏色直接取盒（無排程、信心、識別碼、重複檢查）。"""

    name = "A_vision_only"

    def decide(self, obs: Observation) -> Decision:
        period = obs.intent.period or obs.schedule.candidate or (obs.schedule.in_window[0] if obs.schedule.in_window else None)
        if obs.intent.intent in ("MEDICATION_ALREADY_TAKEN", "CANCEL", "UNKNOWN") or period is None:
            return Decision(self.name, STOP, None, None, None, reasons=["沒有可取的時段"])
        index, _ = select_target(obs, period, self.cfg, use_identity=False, calibrated=False)
        if index is None:
            return Decision(self.name, STOP, period, None, None, reasons=["找不到該顏色的藥盒"])
        return Decision(self.name, EXECUTE, period, index, obs.detections[index].world_xy, reasons=["偵測到目標顏色，直接執行"])


class RuleBased(Strategy):
    """Baseline B：語音 + 視覺 + 排程的確定性規則（含硬性限制，不看信心）。"""

    name = "B_rule_based"

    def _layer1(self, obs: Observation, *, calibrated: bool) -> tuple[Decision | None, str | None, int | None, list[int], list[RuleResult], list[str]]:
        period, reasons = resolve_target_period(obs)
        index, cands = (None, [])
        if period is not None:
            index, cands = select_target(obs, period, self.cfg, use_identity=True, calibrated=calibrated)
        rules = hard_constraints(obs, self.cfg, period, index, cands)
        violation = first_violation(rules)
        if violation is not None:
            dec = Decision(self.name, violation.blocking_decision, period, index, None, rules=rules,
                           reasons=reasons + [f"硬性安全限制：{violation.message}"])
            return dec, period, index, cands, rules, reasons
        return None, period, index, cands, rules, reasons

    def decide(self, obs: Observation) -> Decision:
        blocked, period, index, cands, rules, reasons = self._layer1(obs, calibrated=False)
        if blocked:
            return blocked
        return Decision(self.name, EXECUTE, period, index, obs.detections[index].world_xy, rules=rules,
                        reasons=reasons + ["所有規則通過，執行"])


class FixedThreshold(RuleBased):
    """Baseline C：規則 + 固定未校正信心門檻（語音與視覺原始信心都需 ≥ 門檻）。"""

    name = "C_fixed_threshold"

    def decide(self, obs: Observation) -> Decision:
        blocked, period, index, cands, rules, reasons = self._layer1(obs, calibrated=False)
        if blocked:
            return blocked
        threshold = float(self.cfg["decision"]["fixed_confidence_threshold"])
        det = obs.detections[index]
        speech, vision = obs.intent.raw_confidence, det.raw_scores[period]
        ok = speech >= threshold and vision >= threshold
        decision = EXECUTE if ok else CONFIRM
        return Decision(self.name, decision, period, index, det.world_xy, rules=rules,
                        features={"speech_raw": speech, "vision_raw": vision},
                        reasons=reasons + [f"原始信心 語音 {speech:.2f} / 視覺 {vision:.2f}，門檻 {threshold:.2f}"])


class CalibratedRisk(RuleBased):
    """Proposed D：硬性限制 + 校正後風險估計 + 選擇性執行。"""

    name = "D_calibrated_risk"

    def __init__(self, cfg: dict, model: RiskModel | None = None, name: str | None = None) -> None:
        super().__init__(cfg)
        self.model = model
        if name:
            self.name = name

    def layer1(self, obs: Observation):
        return self._layer1(obs, calibrated=True)

    def decide(self, obs: Observation) -> Decision:
        blocked, period, index, cands, rules, reasons = self.layer1(obs)
        if blocked:
            return blocked
        feats = build_features(obs, period, index, cands)
        det = obs.detections[index]
        if self.model is None:
            return Decision(self.name, CONFIRM, period, index, det.world_xy, rules=rules, features=feats,
                            reasons=reasons + ["風險模型尚未訓練：一律請求確認"])
        risk = float(self.model.risk([feats])[0])
        decision = self.model.decide(risk)
        explain = {
            EXECUTE: f"估計錯誤風險 {risk:.3f} ≤ 執行門檻 {self.model.execute_below:.3f}，自動執行",
            CONFIRM: f"估計錯誤風險 {risk:.3f} 介於門檻之間，請照護者確認",
            STOP: f"估計錯誤風險 {risk:.3f} > 停止門檻 {self.model.stop_above:.2f}，停止",
        }[decision]
        return Decision(self.name, decision, period, index, det.world_xy, risk=risk, rules=rules, features=feats,
                        reasons=reasons + ["硬性安全限制全部通過", explain])


# ----------------------------------------------------------- 吃完藥：回收空盒
# 透明的關鍵字規則（不重新訓練意圖分類器，以免改變實驗所用的模型）
FINISH_KEYWORDS = ("吃完", "吃好了", "空盒", "收走", "收回", "收一下", "拿回去", "拿走")


def is_finish_command(intent: IntentResult) -> bool:
    """使用者表示已吃完藥／要收空盒。分類器判為「已服藥」或文字含回收關鍵字。"""

    if intent.intent == "MEDICATION_ALREADY_TAKEN":
        return True
    return any(word in intent.transcript for word in FINISH_KEYWORDS)


def _identity(det: LabelDetection, specs) -> str | None:
    """識別碼與顏色一致時回傳時段，否則 None。"""

    by_dots = [p for p in PERIODS if specs[p].id_dots == det.dot_count]
    probs = det.calibrated_probs or det.raw_scores
    color = max(probs, key=probs.get)
    return by_dots[0] if by_dots and by_dots[0] == color else None


def collect_decision(delivered: list[LabelDetection], cfg: dict, preferred_period: str | None = None) -> Decision:
    """把遞送區的空盒移到回收區。只看影像中遞送區內的標籤（顏色 + 識別碼）。

    遞送區有多盒時，若使用者有說時段（例如「早上的空盒收走」）就只收那一盒。
    """

    specs = box_specs(cfg)
    rules: list[RuleResult] = []
    reasons = ["使用者表示已吃完藥：回收遞送區的空盒"]
    if len(delivered) > 1 and preferred_period:
        chosen = [d for d in delivered if _identity(d, specs) == preferred_period]
        if len(chosen) == 1:
            reasons.append(f"遞送區有 {len(delivered)} 盒，依指令只收{PERIOD_ZH[preferred_period]}的空盒")
            delivered = chosen
    rules.append(RuleResult("delivered_box_present", len(delivered) >= 1,
                            f"遞送區偵測到 {len(delivered)} 個藥盒" if delivered else "遞送區沒有藥盒：只記錄已服藥，不需回收"))
    if delivered:
        rules.append(RuleResult("single_delivered_box", len(delivered) == 1,
                                "遞送區只有一個藥盒" if len(delivered) == 1 else
                                f"遞送區有 {len(delivered)} 個藥盒：請說明要收哪一盒，例如「早上的空盒收走」", CONFIRM))
    violation = first_violation(rules)
    if violation is not None:
        return Decision("collect", violation.blocking_decision, None, None, None, rules=rules,
                        reasons=reasons + [violation.message], action="collect")

    det = delivered[0]
    by_dots = [p for p in PERIODS if specs[p].id_dots == det.dot_count]
    probs = det.calibrated_probs or det.raw_scores
    color = max(probs, key=probs.get)
    period = by_dots[0] if by_dots else None
    consistent = period is not None and period == color
    rules.append(RuleResult("delivered_box_identity", consistent,
                            f"識別碼 {det.dot_count} 點與{specs[period].label_name}標籤一致（{specs[period].box_id}）" if consistent else
                            f"無法確認遞送區藥盒身分（顏色判為 {PERIOD_ZH.get(color, color)}、識別碼 {det.dot_count} 點）", CONFIRM))
    if not consistent:
        return Decision("collect", CONFIRM, None, 0, None, rules=rules, reasons=reasons + [rules[-1].message], action="collect")
    return Decision("collect", EXECUTE, period, 0, det.world_xy, rules=rules,
                    reasons=reasons + [f"把 {specs[period].box_id}（{PERIOD_ZH[period]}）從遞送區移到回收區"], action="collect")
