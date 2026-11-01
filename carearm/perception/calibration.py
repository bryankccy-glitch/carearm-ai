"""視覺顏色分類信心校正。

流程：
1. 以獨立 seed 範圍（不與實驗情境重疊）產生渲染影像資料集，切成 train / calibration / eval。
2. Ground truth（模擬器藥盒位置）只用來替偵測結果「標註」正確類別，推論時不使用。
3. 在 train 上訓練多類 Logistic Regression（特徵：色相 cos/sin、飽和度、亮度）。
4. 在 calibration 上做 temperature scaling。
5. 在 eval 上比較：原始色相距離分數 vs 校正後機率 的準確率與 ECE。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

import joblib
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression

from carearm.perception.detector import LabelDetection, LabelDetector
from carearm.settings import MODELS_DIR, PERIODS, load_config
from carearm.speech.intent import expected_calibration_error

VISION_SEED_BASE = 900_000      # 與實驗情境 seed（20261019 起）不重疊
MATCH_RADIUS_M = 0.03


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class ColorCalibrator:
    def __init__(self) -> None:
        self.lr = LogisticRegression(C=1.0, max_iter=3000)
        self.temperature = 1.0
        self.report: dict = {}

    def fit(self, X: np.ndarray, y: list[str]) -> None:
        self.lr.fit(X, y)

    def logits(self, X: np.ndarray) -> np.ndarray:
        return self.lr.decision_function(X)

    def calibrate(self, X: np.ndarray, y: list[str]) -> None:
        z = self.logits(X)
        idx = np.asarray([list(self.lr.classes_).index(c) for c in y])

        def nll(t: float) -> float:
            p = _softmax(z / t)
            return float(-np.log(p[np.arange(len(idx)), idx] + 1e-12).mean())

        self.temperature = float(minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded").x)

    def probs(self, X: np.ndarray) -> np.ndarray:
        p = _softmax(self.logits(X) / self.temperature)
        order = [list(self.lr.classes_).index(c) for c in PERIODS]
        return p[:, order]

    def predict_probs(self, det: LabelDetection) -> dict[str, float]:
        p = self.probs(det.features().reshape(1, -1))[0]
        return {c: float(p[i]) for i, c in enumerate(PERIODS)}

    def save(self) -> None:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({"lr": self.lr, "temperature": self.temperature, "report": self.report}, MODELS_DIR / "color_calibrator.joblib")

    @classmethod
    def load(cls) -> "ColorCalibrator":
        payload = joblib.load(MODELS_DIR / "color_calibrator.joblib")
        obj = cls()
        obj.lr, obj.temperature, obj.report = payload["lr"], payload["temperature"], payload.get("report", {})
        return obj


def load_detector(cfg: dict | None = None) -> LabelDetector:
    cfg = cfg or load_config()
    detector = LabelDetector(cfg)
    path = MODELS_DIR / "color_calibrator.joblib"
    if path.exists():
        detector.calibrator = ColorCalibrator.load()
    return detector


@dataclass
class VisionSample:
    split: str
    image_index: int
    tags: list[str]
    features: list[float]
    raw_scores: dict
    label: str | None           # ground truth 類別（None = 誤偵測）
    dot_count: int
    expected_dots: int | None
    world_error_m: float | None


def build_vision_dataset(n_images: int = 240, cfg: dict | None = None) -> tuple[list[VisionSample], dict]:
    from carearm.experiments.scenarios import sample_scene_conditions
    from carearm.robot.executor import RetrievalExecutor
    from carearm.settings import box_specs
    from carearm.simulation.scene import make_carearm_env

    cfg = cfg or load_config()
    specs = box_specs(cfg)
    env = make_carearm_env(cfg)
    sim = env.unwrapped
    detector = LabelDetector(cfg)
    samples: list[VisionSample] = []
    missed = 0
    started = time.time()
    for i in range(n_images):
        split = "train" if i < n_images * 0.5 else ("calibration" if i < n_images * 0.75 else "eval")
        rng = random.Random(VISION_SEED_BASE + i)
        cond, tags = sample_scene_conditions(rng, cfg, rng.choice(PERIODS))
        env.reset(seed=VISION_SEED_BASE + i, options={"conditions": cond})
        RetrievalExecutor(env, cfg).move_to_observation_pose()
        detections = detector.detect(sim.render_overhead(), sim.camera_geometry())
        truth = {p: sim.box_position(p) for p in PERIODS}
        if cond.extra_box:
            truth["extra"] = sim.box_position("extra")
        matched = set()
        for det in detections:
            best, dist = None, 1e9
            for name, pos in truth.items():
                d = float(np.hypot(det.world_xy[0] - pos[0], det.world_xy[1] - pos[1]))
                if d < dist:
                    best, dist = name, d
            if dist <= MATCH_RADIUS_M:
                matched.add(best)
                label = cond.extra_box["copy_of"] if best == "extra" else best
                samples.append(VisionSample(split, i, tags, det.features().tolist(), det.raw_scores, label,
                                            det.dot_count, specs[label].id_dots, dist))
            else:
                samples.append(VisionSample(split, i, tags, det.features().tolist(), det.raw_scores, None,
                                            det.dot_count, None, None))
        missed += len(truth) - len(matched)
    env.close()
    meta = {"n_images": n_images, "missed_boxes": missed, "seconds": round(time.time() - started, 1)}
    return samples, meta


def train_color_calibrator(n_images: int = 240) -> ColorCalibrator:
    samples, meta = build_vision_dataset(n_images)
    labeled = [s for s in samples if s.label is not None]
    by = {sp: [s for s in labeled if s.split == sp] for sp in ("train", "calibration", "eval")}
    calib = ColorCalibrator()
    calib.fit(np.asarray([s.features for s in by["train"]]), [s.label for s in by["train"]])
    calib.calibrate(np.asarray([s.features for s in by["calibration"]]), [s.label for s in by["calibration"]])

    ev = by["eval"]
    y = np.asarray([s.label for s in ev])
    raw = np.asarray([[s.raw_scores[p] for p in PERIODS] for s in ev])
    cal = calib.probs(np.asarray([s.features for s in ev]))
    report: dict = {"dataset": meta, "n_labeled": {k: len(v) for k, v in by.items()},
                    "false_detections": sum(1 for s in samples if s.label is None),
                    "temperature": calib.temperature}
    reliability = {}
    for name, probs in (("raw_hue_softmax", raw), ("calibrated", cal)):
        pred = np.asarray(PERIODS)[probs.argmax(1)]
        correct = pred == y
        report[name] = {"accuracy": float(correct.mean()), "ece": expected_calibration_error(probs.max(1), correct),
                        "mean_confidence": float(probs.max(1).mean())}
        reliability[name] = {"confidence": probs.max(1).tolist(), "correct": correct.astype(int).tolist()}
    dots_ok = [s.dot_count == s.expected_dots for s in labeled]
    report["dot_id_accuracy_all_splits"] = float(np.mean(dots_ok))
    errors = [s.world_error_m for s in labeled if s.world_error_m is not None]
    report["localization_error_mm"] = {"mean": float(np.mean(errors) * 1000), "p95": float(np.percentile(errors, 95) * 1000)}
    per_tag: dict = {}
    for s, ok in zip(ev, (np.asarray(PERIODS)[cal.argmax(1)] == y)):
        for t in s.tags or ["clean"]:
            per_tag.setdefault(t, []).append(bool(ok))
    report["eval_accuracy_by_condition"] = {t: {"n": len(v), "accuracy": float(np.mean(v))} for t, v in sorted(per_tag.items())}
    calib.report = report
    calib.save()
    (MODELS_DIR / "vision_calibration_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (MODELS_DIR / "vision_reliability.json").write_text(json.dumps(reliability), encoding="utf-8")
    return calib


if __name__ == "__main__":
    model = train_color_calibrator()
    print(json.dumps(model.report, indent=2, ensure_ascii=False))
