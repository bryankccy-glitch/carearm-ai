"""CareArm 實驗的 bootstrap 信賴區間與配對統計檢定。"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from scipy.stats import binomtest


MetricFn = Callable[[pd.DataFrame], float]


def _bool(series: pd.Series) -> pd.Series:
    """把 CSV 讀回的布林／空值欄位安全轉成 bool。"""

    if series.dtype == bool:
        return series.fillna(False)
    return series.fillna(False).astype(bool)


def correct_retrieval_rate(g: pd.DataFrame) -> float:
    return float((_bool(g["executed"]) & _bool(g["correct"])).mean())


def wrong_box_rate(g: pd.DataFrame) -> float:
    wrong = g["delivered_box"].notna() & (g["delivered_box"] != g["expected_period"])
    return float(wrong.mean())


def error_rate_among_executed(g: pd.DataFrame) -> float:
    executed = _bool(g["executed"])
    if not executed.any():
        return float("nan")
    return float((~_bool(g.loc[executed, "correct"])).mean())


def human_confirmation_rate(g: pd.DataFrame) -> float:
    return float((g["decision"] == "REQUEST_CONFIRMATION").mean())


def unsafe_action_interception_rate(g: pd.DataFrame) -> float:
    hazards = (~_bool(g["should_execute"])) | (
        _bool(g["layer1_passed"]) & g["counterfactual_correct"].eq(False)  # noqa: E712
    )
    if not hazards.any():
        return float("nan")
    return float((~_bool(g.loc[hazards, "executed"])).mean())


MAIN_METRICS: dict[str, MetricFn] = {
    "correct_retrieval_rate": correct_retrieval_rate,
    "wrong_box_rate": wrong_box_rate,
    "error_rate_among_executed": error_rate_among_executed,
    "human_confirmation_rate": human_confirmation_rate,
    "unsafe_action_interception_rate": unsafe_action_interception_rate,
}


def bootstrap_ci(
    df: pd.DataFrame,
    strategy: str,
    metric_fn: MetricFn,
    n_boot: int = 2000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """以情境為抽樣單位，回傳（點估計、95% 下界、95% 上界）。"""

    if n_boot <= 0:
        raise ValueError("n_boot 必須大於 0")
    group = df[df["strategy"] == strategy].copy()
    if group.empty:
        raise ValueError(f"找不到策略：{strategy}")
    if "scenario_id" not in group:
        raise ValueError("資料缺少 scenario_id 欄位")

    scenario_ids = group["scenario_id"].drop_duplicates().to_numpy()
    blocks = {sid: rows for sid, rows in group.groupby("scenario_id", sort=False)}
    one_row_per_scenario = len(group) == len(scenario_ids)
    if one_row_per_scenario:
        ordered = group.drop_duplicates("scenario_id").set_index("scenario_id").loc[scenario_ids].reset_index()

    rng = np.random.default_rng(seed)
    estimates = np.empty(n_boot, dtype=float)
    for index in range(n_boot):
        sampled = rng.choice(scenario_ids, size=len(scenario_ids), replace=True)
        if one_row_per_scenario:
            positions = pd.Index(scenario_ids).get_indexer(sampled)
            sample = ordered.iloc[positions]
        else:
            sample = pd.concat([blocks[sid] for sid in sampled], ignore_index=True)
        estimates[index] = float(metric_fn(sample))

    finite = estimates[np.isfinite(estimates)]
    point = float(metric_fn(group))
    if finite.size == 0:
        return point, float("nan"), float("nan")
    low, high = np.percentile(finite, [2.5, 97.5])
    return point, float(low), float(high)


def mcnemar_exact(df: pd.DataFrame, a: str, b: str) -> dict[str, int | float | str]:
    """比較兩策略「自動且正確完成」的配對結果，回傳 exact McNemar 檢定。"""

    def outcomes(strategy: str) -> pd.DataFrame:
        group = df[df["strategy"] == strategy].copy()
        if group.empty:
            raise ValueError(f"找不到策略：{strategy}")
        group["auto_correct"] = _bool(group["executed"]) & _bool(group["correct"])
        return group[["scenario_id", "auto_correct"]].drop_duplicates("scenario_id")

    paired = outcomes(a).merge(outcomes(b), on="scenario_id", suffixes=("_a", "_b"), validate="one_to_one")
    a_only = int((paired.auto_correct_a & ~paired.auto_correct_b).sum())
    b_only = int((~paired.auto_correct_a & paired.auto_correct_b).sum())
    discordant = a_only + b_only
    p_value = 1.0 if discordant == 0 else float(binomtest(min(a_only, b_only), discordant, 0.5).pvalue)
    return {
        "strategy_a": a,
        "strategy_b": b,
        "a_only": a_only,
        "b_only": b_only,
        "discordant": discordant,
        "p_value": p_value,
        "n_paired": int(len(paired)),
    }
