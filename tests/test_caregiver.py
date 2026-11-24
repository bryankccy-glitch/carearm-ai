"""照護者決定、物理路由、分母及停用功能的回歸驗證。"""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pandas as pd
import pytest

from carearm.decision.engine import CONFIRM, EXECUTE, STOP, Decision, RuleResult
from carearm.experiments.caregiver import SimulatedCaregiver
from carearm.experiments.runner import ExperimentRunner
from carearm.experiments import runner as module
from carearm.experiments.stats import CAREGIVER_METRICS


def decision(kind=CONFIRM, **kwargs):
    return Decision("測試", kind, "noon", 0, (0.4, 0.0), **kwargs)


@pytest.mark.parametrize("error", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_error(error):
    with pytest.raises(ValueError):
        SimulatedCaregiver(error)


def test_seed_and_truth():
    caregiver = SimulatedCaregiver(0.05, 17)
    forward = {i: caregiver.approves(i, True) for i in range(1000)}
    assert forward == {i: caregiver.approves(i, True) for i in reversed(range(1000))}
    assert any(not value for value in forward.values())
    assert forward != {i: SimulatedCaregiver(0.05, 18).approves(i, True) for i in forward}
    for truth in (False, True):
        assert SimulatedCaregiver(0).approves(1, truth) == truth
        assert SimulatedCaregiver(1).approves(1, truth) != truth


@pytest.mark.parametrize("truth,error,kind,expected", [
    (True, 0, CONFIRM, True), (False, 0, CONFIRM, False),
    (False, 1, CONFIRM, True), (True, 1, CONFIRM, False),
    (True, 0, STOP, False), (True, 0, EXECUTE, False),
])
def test_execution_routes(truth, error, kind, expected):
    execute = Mock(return_value={"success": True, "delivered_box": "evening", "failure_stage": None, "sim_seconds": 4})
    scenario = NS(scenario_id=3, should_execute=truth, expected_period="noon")
    row = SimulatedCaregiver(error).evaluate(scenario, decision(kind), execute, ExperimentRunner.correct)
    assert row["caregiver_executed"] == expected
    assert execute.call_count == int(expected)
    if expected:
        execute.assert_called_once_with(scenario, "noon", (0.4, 0.0))
        assert row["caregiver_correct"] is False  # 批准不會消除錯盒。


def test_unresolved_and_hard_stop():
    execute = Mock()
    sc = NS(scenario_id=1, should_execute=True)
    missing = decision()
    missing.target_xy = None
    blocked = decision(rules=[RuleResult("duplicate", False, "重複取藥", STOP)])
    for dec, status in [(missing, "缺少執行目標"), (blocked, "硬性停止限制")]:
        row = SimulatedCaregiver().evaluate(sc, dec, execute, ExperimentRunner.correct)
        assert row["caregiver_approved"] is True
        assert row["caregiver_status"] == status
    execute.assert_not_called()


def test_metric_denominators():
    df = pd.DataFrame({"executed": [True, False, False, False], "correct": [True, None, None, None],
                       "caregiver_requested": [False, True, True, True],
                       "caregiver_executed": [False, True, True, False],
                       "caregiver_correct": [None, True, False, None]})
    values = {name: fn(df) for name, fn in CAREGIVER_METRICS.items()}
    assert values == {"confirmed_completion_rate": 0.5, "confirmed_error_rate": 0.5,
                      "caregiver_burden_per_100": 75.0, "end_to_end_retrieval_rate": 0.5}
    assert pd.isna(CAREGIVER_METRICS["confirmed_completion_rate"](df.iloc[[0, 3]]))


def fake_runner(monkeypatch, runner_module=module, caregiver=None):
    """固定輸入涵蓋自動、確認與停止，不依賴渲染器。"""
    obj = runner_module.ExperimentRunner.__new__(runner_module.ExperimentRunner)
    obj.run_id = "回歸測試"
    obj.cfg = {}
    obj.caregiver = caregiver
    obj.store = NS(add_decision=Mock())
    obj.scenarios = [NS(scenario_id=i, split="test", should_execute=True, expected_period="noon",
                        true_intent="REQUEST_MEDICATION", tags=[], audio_noise=0,
                        conditions={"lighting": "normal", "occlusion": 0, "camera_tilt_deg": 0, "similar_color": False}) for i in range(3)]
    obs = NS(intent=NS(intent="REQUEST_MEDICATION", raw_confidence=0.8, confidence=0.9),
             detections=[NS(raw_scores={"noon": 0.8}, calibrated_probs={"noon": 0.9}, world_xy=(0.4, 0.0))])
    obj.observation = lambda sid: obs
    obj.perception = {i: {"ground_truth_positions": {"noon": (0.4, 0.0)}, "vision_seconds": 0.1, "speech_seconds": 0.2} for i in range(3)}
    obj.layer1_passed = lambda sc: None
    obj.execute = Mock(return_value={"success": True, "grasp_success": True, "delivered_box": "noon", "failure_stage": None, "sim_seconds": 2.0})
    for factory, name, kind in [("VisionOnly", "A_vision_only", EXECUTE), ("RuleBased", "B_rule_based", CONFIRM), ("FixedThreshold", "C_fixed_threshold", STOP)]:
        dec = decision(kind)
        dec.strategy = name
        strategy = NS(name=name, decide=lambda obs, dec=dec: dec)
        monkeypatch.setattr(runner_module, factory, lambda cfg, strategy=strategy: strategy)
    return obj


def test_disabled_matches_original_rows(monkeypatch):
    rows = fake_runner(monkeypatch).evaluate_test({})
    baseline = json.loads((Path(__file__).parent / "fixtures/caregiver_legacy_rows.json").read_text())
    assert rows == baseline
    assert not any(key.startswith("caregiver_") for key in rows[0])


def test_enabled_preserves_automatic_results(monkeypatch):
    baseline = fake_runner(monkeypatch).evaluate_test({})
    obj = fake_runner(monkeypatch, caregiver=SimulatedCaregiver())
    enabled = obj.evaluate_test({})
    assert [{key: row[key] for key in old} for row, old in zip(enabled, baseline)] == baseline
    assert sum(row["caregiver_executed"] for row in enabled) == 3
    assert obj.execute.call_count == 6


def report_input():
    from carearm.experiments.report import STRATEGY_ZH
    rows = json.loads((Path(__file__).parent / "fixtures/caregiver_legacy_rows.json").read_text())
    template = [rows[0], rows[4], rows[8]]
    template[2]["should_execute"] = False
    return pd.DataFrame([{**row, "strategy": strategy} for strategy in STRATEGY_ZH for row in template])


def test_legacy_report_unchanged(tmp_path, monkeypatch):
    from carearm.experiments import report
    from carearm.experiments.stats import bootstrap_ci
    monkeypatch.setattr(report, "bootstrap_ci", lambda df, strategy, fn, **kw: bootstrap_ci(df, strategy, fn, n_boot=30))
    report_input().to_csv(tmp_path / "test_decisions.csv", index=False)
    actual = report.build_report(tmp_path)
    expected = json.loads((Path(__file__).parent / "fixtures/caregiver_legacy_summary.json").read_text())
    assert actual == expected
    assert not (tmp_path / "caregiver_metrics.csv").exists()
    for name in ("metrics.csv", "stats.csv", "pairwise_tests.csv"):
        assert (tmp_path / name).read_bytes() == (Path(__file__).parent / "fixtures" / ("caregiver_legacy_" + name)).read_bytes()


def test_caregiver_report_roundtrip(tmp_path, monkeypatch):
    from carearm.experiments import report
    from carearm.experiments.stats import bootstrap_ci
    monkeypatch.setattr(report, "bootstrap_ci", lambda df, strategy, fn, **kw: bootstrap_ci(df, strategy, fn, n_boot=30))
    frame = report_input()
    frame["caregiver_requested"] = frame.decision.eq(CONFIRM)
    frame["caregiver_approved"] = frame.caregiver_requested.map({True: True, False: None})
    frame["caregiver_executed"] = frame.caregiver_requested
    frame["caregiver_correct"] = frame.caregiver_requested.map({True: True, False: None})
    frame["caregiver_decision_error"] = False
    frame.to_csv(tmp_path / "test_decisions.csv", index=False)
    (tmp_path / "run_meta.json").write_text(json.dumps({"result_label": "小樣本試跑，非正式結果", "caregiver": {"error_rate": 0.05, "seed": 123}}))
    summary = report.build_report(tmp_path)
    assert summary["result_label"] == "小樣本試跑，非正式結果"
    assert summary["caregiver_settings"]["seed"] == 123
    for row in summary["caregiver_metrics"]:
        assert row["confirmed_executions"] == 1
        assert row["confirmed_correct"] == 1
        assert row["confirmed_execution_errors"] == 0
    assert "小樣本試跑，非正式結果" in (tmp_path / "caregiver_report.md").read_text()


def test_confirmation_reuses_physics_cache(monkeypatch):
    obj = fake_runner(monkeypatch)
    sc = obj.scenarios[0]
    sc.seed = 123
    sc.scene_conditions = lambda: "固定場景"
    obj.env = NS(reset=Mock(), unwrapped=NS(control_dt=0.01))
    obj.exec_cache = {}
    obj.store.add_execution = Mock()
    obj._save_exec_cache = Mock()
    result = NS(sim_steps=200, to_dict=lambda: {"success": True, "delivered_box": "noon", "failure_stage": None})
    executor = NS(move_to_observation_pose=Mock(), execute=Mock(return_value=result))
    monkeypatch.setattr(module, "RetrievalExecutor", lambda env, cfg: executor)
    execute = lambda *args: ExperimentRunner.execute(obj, *args)
    first = execute(sc, "noon", (0.4, 0.0))
    row = SimulatedCaregiver().evaluate(sc, decision(), execute, ExperimentRunner.correct)
    assert row["caregiver_correct"] is True
    assert row["caregiver_robot_sim_seconds"] == first["sim_seconds"]
    assert len(obj.exec_cache) == 1
    executor.execute.assert_called_once()
    obj.store.add_execution.assert_called_once()
