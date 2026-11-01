"""Bootstrap 信賴區間與 exact McNemar 檢定測試。"""

import pandas as pd
import pytest

from carearm.experiments.stats import bootstrap_ci, correct_retrieval_rate, mcnemar_exact


def _rows(strategy, outcomes):
    rows = []
    for scenario_id, correct in enumerate(outcomes):
        rows.append({
            "scenario_id": scenario_id,
            "strategy": strategy,
            "executed": bool(correct),
            "correct": bool(correct) if correct else None,
        })
    return rows


def test_bootstrap_interval_contains_point_estimate():
    df = pd.DataFrame(_rows("A", [1] * 7 + [0] * 3))
    point, low, high = bootstrap_ci(df, "A", correct_retrieval_rate, n_boot=1000, seed=7)
    assert point == pytest.approx(0.7)
    assert low <= point <= high
    assert 0 <= low <= high <= 1


def test_bootstrap_resamples_scenarios_not_other_strategies():
    df = pd.DataFrame(_rows("A", [1, 1, 1, 1]) + _rows("B", [0, 0, 0, 0]))
    point, low, high = bootstrap_ci(df, "A", correct_retrieval_rate, n_boot=100, seed=1)
    assert (point, low, high) == pytest.approx((1.0, 1.0, 1.0))


def test_mcnemar_exact_known_balanced_case():
    # A-only=2、B-only=2；雙尾 exact binomial p=1。
    a = [1, 1, 0, 0, 1, 0]
    b = [0, 0, 1, 1, 1, 0]
    result = mcnemar_exact(pd.DataFrame(_rows("A", a) + _rows("B", b)), "A", "B")
    assert result["a_only"] == 2
    assert result["b_only"] == 2
    assert result["p_value"] == pytest.approx(1.0)


def test_mcnemar_exact_one_sided_disagreement():
    # 6 個不一致案例全由 A 勝出；雙尾 exact p=2*(0.5**6)=0.03125。
    result = mcnemar_exact(
        pd.DataFrame(_rows("A", [1] * 6 + [0, 0]) + _rows("B", [0] * 8)),
        "A",
        "B",
    )
    assert result["a_only"] == 6
    assert result["b_only"] == 0
    assert result["p_value"] == pytest.approx(0.03125)
