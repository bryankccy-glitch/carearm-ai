"""完整實驗執行器。

步驟：
1. 產生 N 個可重現情境（train / calibration / test）。
2. 每個情境：重置 MuJoCo → 手臂讓出視野 → 渲染 → 視覺偵測；轉錄文字 → 意圖；排程檢查。
3. 風險標籤：train + calibration 中通過硬性限制者，實際以物理模擬執行 D 選定的目標，
   以物理結果（是否正確送達應取的盒子）作為「執行會出錯」標籤。
4. 訓練風險模型（D 與 4 種消融），只用 train；Platt 與門檻只用 calibration。
5. test：A/B/C/D/消融 做決策；每個 EXECUTE 都實際跑物理模擬；另外為了評估
   風險—覆蓋曲線與校正誤差，也對 D 通過硬性限制但未執行的案例跑反事實物理（不影響決策）。

所有物理執行都是真實 MuJoCo 模擬；同一情境 + 同一指令座標的結果在 MuJoCo 中是確定性的，
因此以 (scenario, period, x, y) 為鍵快取（tests/test_reproducibility.py 驗證確定性）。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from carearm.database.store import Store
from carearm.decision.conformal import ConformalRisk, ConformalRiskController
from carearm.decision.engine import (
    ALL_FEATURES,
    EXECUTE,
    FEATURE_GROUPS,
    CalibratedRisk,
    FixedThreshold,
    RiskModel,
    RuleBased,
    VisionOnly,
    build_features,
)
from carearm.experiments.scenarios import Scenario, generate_suite
from carearm.perception.calibration import load_detector
from carearm.pipeline import interpret, observation_from_dict, observation_to_dict, perceive
from carearm.robot.executor import ExecutionResult, RetrievalExecutor
from carearm.settings import PERIODS, RESULTS_DIR, load_config
from carearm.simulation.scene import make_carearm_env
from carearm.speech.intent import IntentClassifier

ABLATIONS = {
    "D_no_speech_conf": [f for f in ALL_FEATURES if f not in FEATURE_GROUPS["speech"]],
    "D_no_vision_conf": [f for f in ALL_FEATURES if f not in FEATURE_GROUPS["vision"]],
    "D_no_schedule_feat": [f for f in ALL_FEATURES if f not in FEATURE_GROUPS["schedule"]],
}


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class ExperimentRunner:
    def __init__(
        self,
        run_id: str,
        n: int | None = None,
        base_seed: int | None = None,
        cfg: dict | None = None,
        alpha: float = 0.02,
    ) -> None:
        if not 0 < alpha < 1:
            raise ValueError("alpha 必須介於 0 與 1 之間")
        self.cfg = cfg or load_config()
        self.n = n or int(self.cfg["experiments"]["n_scenarios"])
        self.base_seed = base_seed if base_seed is not None else int(self.cfg["experiments"]["base_seed"])
        self.run_id = run_id
        self.alpha = float(alpha)
        self.dir = RESULTS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.dir / "carearm_experiment.sqlite")
        self.env = make_carearm_env(self.cfg)
        self.detector = load_detector(self.cfg)
        self.intent_clf = IntentClassifier.load()
        self.scenarios: list[Scenario] = generate_suite(self.n, self.base_seed, self.cfg)
        self.perception: dict[int, dict] = {}
        self.exec_cache: dict[str, dict] = {}
        self._load_checkpoints()

    # ------------------------------------------------------------ checkpoint
    def _load_checkpoints(self) -> None:
        p = self.dir / "perception.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                self.perception[rec["scenario_id"]] = rec
        e = self.dir / "executions.json"
        if e.exists():
            self.exec_cache = json.loads(e.read_text(encoding="utf-8"))

    def _save_exec_cache(self) -> None:
        (self.dir / "executions.json").write_text(json.dumps(self.exec_cache, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------ perception
    def perceive_all(self) -> None:
        self.store.add_run(self.run_id, self.n, self.base_seed, self.cfg)
        with open(self.dir / "perception.jsonl", "a", encoding="utf-8") as fh:
            for sc in self.scenarios:
                self.store.add_scenario(self.run_id, sc)
                if sc.scenario_id in self.perception:
                    continue
                t0 = time.perf_counter()
                self.env.reset(seed=sc.seed, options={"conditions": sc.scene_conditions()})
                gt = {p: self.env.unwrapped.box_position(p).tolist() for p in PERIODS}
                if sc.conditions["extra_box"]:
                    gt["extra"] = self.env.unwrapped.box_position("extra").tolist()
                _, detections = perceive(self.env, self.detector)
                t1 = time.perf_counter()
                intent, sched = interpret(sc.transcript, sc.time_minutes, sc.taken_today, self.intent_clf, self.cfg)
                t2 = time.perf_counter()
                from carearm.decision.engine import Observation

                obs = Observation(intent, sched, detections)
                rec = {
                    "scenario_id": sc.scenario_id,
                    "observation": observation_to_dict(obs),
                    "ground_truth_positions": gt,      # 僅評估用
                    "vision_seconds": t1 - t0,
                    "speech_seconds": t2 - t1,
                }
                self.perception[sc.scenario_id] = rec
                fh.write(json.dumps(rec, ensure_ascii=False, default=float) + "\n")
                fh.flush()
                if sc.scenario_id % 25 == 0:
                    _log(f"感知 {sc.scenario_id + 1}/{self.n}")

    def observation(self, sid: int):
        return observation_from_dict(self.perception[sid]["observation"])

    # ------------------------------------------------------------ physics
    def execute(self, sc: Scenario, period: str, xy) -> dict:
        key = f"{sc.scenario_id}|{period}|{xy[0]:.4f}|{xy[1]:.4f}"
        if key in self.exec_cache:
            return self.exec_cache[key]
        self.env.reset(seed=sc.seed, options={"conditions": sc.scene_conditions()})
        executor = RetrievalExecutor(self.env, self.cfg)
        executor.move_to_observation_pose()
        result: ExecutionResult = executor.execute(period, xy)
        data = result.to_dict()
        data["sim_seconds"] = result.sim_steps * float(self.env.unwrapped.control_dt)
        self.exec_cache[key] = data
        self.store.add_execution(self.run_id, sc.scenario_id, key, result)
        self._save_exec_cache()
        return data

    @staticmethod
    def correct(sc: Scenario, result: dict) -> bool:
        return bool(sc.should_execute and result["success"] and result["delivered_box"] == sc.expected_period)

    # ------------------------------------------------------------ risk data
    def layer1_passed(self, sc: Scenario):
        obs = self.observation(sc.scenario_id)
        blocked, period, index, cands, _, _ = CalibratedRisk(self.cfg).layer1(obs)
        if blocked:
            return None
        return obs, period, index, cands

    def risk_dataset(self, split: str) -> tuple[list[dict], list[int], list[int]]:
        rows, labels, ids = [], [], []
        todo = [sc for sc in self.scenarios if sc.split == split]
        for k, sc in enumerate(todo):
            passed = self.layer1_passed(sc)
            if passed is None:
                continue
            obs, period, index, cands = passed
            result = self.execute(sc, period, obs.detections[index].world_xy)
            rows.append(build_features(obs, period, index, cands))
            labels.append(0 if self.correct(sc, result) else 1)
            ids.append(sc.scenario_id)
            if k % 20 == 0:
                _log(f"{split} 風險標籤 {k + 1}/{len(todo)}")
        return rows, labels, ids

    def train_risk_models(self) -> dict[str, RiskModel | ConformalRisk]:
        tr_rows, tr_y, _ = self.risk_dataset("train")
        ca_rows, ca_y, _ = self.risk_dataset("calibration")
        dcfg = self.cfg["decision"]
        models: dict[str, RiskModel | ConformalRisk] = {}
        specs = {"D_calibrated_risk": (ALL_FEATURES, True), "D_no_calibration": (ALL_FEATURES, False)}
        specs.update({name: (feats, True) for name, feats in ABLATIONS.items()})
        for name, (feats, calibrate) in specs.items():
            model = RiskModel(feats, calibrate=calibrate)
            model.fit(tr_rows, tr_y)
            model.fit_calibration(ca_rows, ca_y, default_execute_below=dcfg["risk_execute_below"],
                                  default_stop_above=dcfg["risk_stop_above"])
            model.info.update({"train_n": len(tr_y), "train_positive_rate": float(np.mean(tr_y))})
            models[name] = model
            model.save(f"{self.run_id}_{name}" if name != "D_calibrated_risk" else "risk_model")
            model.save(f"{self.run_id}_{name}")

        # 策略 E：模型只用 train 擬合；門檻只看 calibration 的 raw risk 與物理 loss。
        conformal_model = RiskModel(ALL_FEATURES, calibrate=False)
        conformal_model.fit(tr_rows, tr_y)
        calibration_scores = conformal_model.raw_risk(ca_rows)
        controller = ConformalRiskController().fit(calibration_scores, ca_y, self.alpha)
        conformal = ConformalRisk(self.cfg, conformal_model, controller)
        models[conformal.name] = conformal

        model_records = {}
        for name, model in models.items():
            if isinstance(model, ConformalRisk):
                model_records[name] = {
                    "info": model.controller.info,
                    "coefficients": model.model.coefficients(),
                    "score": "raw_risk",
                }
            else:
                model_records[name] = {"info": model.info, "coefficients": model.coefficients()}
        (self.dir / "risk_training.json").write_text(json.dumps({
            "train": {"n": len(tr_y), "positives": int(sum(tr_y))},
            "calibration": {"n": len(ca_y), "positives": int(sum(ca_y))},
            "models": model_records,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return models

    # ------------------------------------------------------------ evaluation
    def evaluate_test(self, models: dict[str, RiskModel | ConformalRisk]) -> list[dict]:
        strategies = [VisionOnly(self.cfg), RuleBased(self.cfg), FixedThreshold(self.cfg)]
        strategies += [
            model if isinstance(model, ConformalRisk) else CalibratedRisk(self.cfg, model, name=name)
            for name, model in models.items()
        ]
        rows: list[dict] = []
        test = [sc for sc in self.scenarios if sc.split == "test"]
        for k, sc in enumerate(test):
            obs = self.observation(sc.scenario_id)
            per = self.perception[sc.scenario_id]
            gt = per["ground_truth_positions"]
            # 反事實：D 通過硬性限制者，無論決策都跑物理（只用於風險評估）
            counterfactual = None
            passed = self.layer1_passed(sc)
            if passed is not None:
                o, period, index, _ = passed
                counterfactual = self.execute(sc, period, o.detections[index].world_xy)
            for strat in strategies:
                dec = strat.decide(obs)
                row = {
                    "scenario_id": sc.scenario_id,
                    "strategy": strat.name,
                    "decision": dec.decision,
                    "target_period": dec.target_period,
                    "risk": dec.risk,
                    "should_execute": sc.should_execute,
                    "expected_period": sc.expected_period,
                    "true_intent": sc.true_intent,
                    "pred_intent": obs.intent.intent,
                    "tags": "|".join(sc.tags),
                    "audio_noise": sc.audio_noise,
                    "lighting": sc.conditions["lighting"],
                    "occlusion": sc.conditions["occlusion"],
                    "camera_tilt": sc.conditions["camera_tilt_deg"],
                    "similar_color": sc.conditions["similar_color"],
                    "speech_raw_conf": obs.intent.raw_confidence,
                    "speech_cal_conf": obs.intent.confidence,
                    "perception_seconds": per["vision_seconds"] + per["speech_seconds"],
                    "layer1_passed": passed is not None,
                    "counterfactual_correct": None if counterfactual is None else self.correct(sc, counterfactual),
                }
                chosen_truth = None
                if dec.target_index is not None:
                    det = obs.detections[dec.target_index]
                    row["vision_raw_conf"] = det.raw_scores.get(dec.target_period) if dec.target_period else None
                    row["vision_cal_conf"] = det.calibrated_probs.get(dec.target_period) if dec.target_period else None
                    dists = {name: float(np.hypot(det.world_xy[0] - p[0], det.world_xy[1] - p[1])) for name, p in gt.items()}
                    nearest = min(dists, key=dists.get)
                    chosen_truth = nearest if dists[nearest] < 0.03 else None
                    if chosen_truth == "extra":
                        chosen_truth = "extra:" + sc.conditions["extra_box"]["copy_of"]
                row["chosen_detection_truth"] = chosen_truth
                if dec.decision == EXECUTE:
                    result = self.execute(sc, dec.target_period, dec.target_xy)
                    row.update({
                        "executed": True,
                        "exec_success": result["success"],
                        "grasp_success": result["grasp_success"],
                        "delivered_box": result["delivered_box"],
                        "failure_stage": result["failure_stage"],
                        "robot_sim_seconds": result["sim_seconds"],
                        "correct": self.correct(sc, result),
                    })
                else:
                    row.update({"executed": False, "exec_success": None, "grasp_success": None, "delivered_box": None,
                                "failure_stage": None, "robot_sim_seconds": 0.0, "correct": None})
                row["reasons"] = " / ".join(dec.reasons)
                self.store.add_decision(self.run_id, sc.scenario_id, dec, {"executed": row["executed"], "correct": row["correct"]})
                rows.append(row)
            if k % 15 == 0:
                _log(f"測試 {k + 1}/{len(test)}")
        return rows

    def run(self) -> Path:
        t0 = time.time()
        _log(f"實驗 {self.run_id}：{self.n} 個情境")
        self.perceive_all()
        _log("訓練風險模型（train）與校正（calibration）")
        models = self.train_risk_models()
        _log("測試集評估")
        rows = self.evaluate_test(models)
        import pandas as pd

        df = pd.DataFrame(rows)
        out = self.dir / "test_decisions.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        pd.DataFrame([s.to_dict() for s in self.scenarios]).to_csv(self.dir / "scenarios.csv", index=False, encoding="utf-8-sig")
        (self.dir / "run_meta.json").write_text(json.dumps({
            "run_id": self.run_id, "n_scenarios": self.n, "base_seed": self.base_seed,
            "conformal_alpha": self.alpha,
            "physics_executions": len(self.exec_cache), "wall_seconds": round(time.time() - t0, 1),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
        _log(f"完成：{out}（實際物理執行 {len(self.exec_cache)} 次）")
        self.env.close()
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description="CareArm AI 完整實驗")
    parser.add_argument("--run-id", default="main")
    parser.add_argument("--n", type=int, default=None, help="情境數（預設讀 config，300）")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--alpha", type=float, default=0.02, help="策略 E 的 conformal 風險上限（預設 0.02）")
    args = parser.parse_args()
    out = ExperimentRunner(args.run_id, args.n, args.seed, alpha=args.alpha).run()
    from carearm.experiments.report import build_report

    build_report(out.parent)


if __name__ == "__main__":
    main()
