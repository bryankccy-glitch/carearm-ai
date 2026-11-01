"""由實際完成的模擬結果計算指標並產生圖表。不產生任何未經模擬的數字。"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from carearm.settings import MODELS_DIR  # noqa: E402
from carearm.speech.intent import INTENTS, expected_calibration_error  # noqa: E402
from carearm.experiments.stats import MAIN_METRICS, bootstrap_ci, mcnemar_exact  # noqa: E402

STRATEGY_ZH = {
    "A_vision_only": "A 純視覺",
    "B_rule_based": "B 規則式多模態",
    "C_fixed_threshold": "C 固定信心門檻",
    "D_calibrated_risk": "D 校正風險感知（本研究）",
    "D_no_speech_conf": "D − 語音信心",
    "D_no_vision_conf": "D − 視覺信心",
    "D_no_schedule_feat": "D − 排程特徵",
    "D_no_calibration": "D − 風險校正",
    "E_conformal": "E Conformal 風險控制",
}
MAIN = ["A_vision_only", "B_rule_based", "C_fixed_threshold", "D_calibrated_risk", "E_conformal"]
ABL = ["D_calibrated_risk", "D_no_speech_conf", "D_no_vision_conf", "D_no_schedule_feat", "D_no_calibration"]
ORANGE, CHARCOAL, GREY = "#F07B1A", "#2B2B2B", "#9A9A9A"


def _setup_font() -> None:
    for name in ("PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans TC", "Arial Unicode MS"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def strategy_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strat, g in df.groupby("strategy", sort=False):
        n = len(g)
        ex = g[g.executed]
        n_ex = len(ex)
        correct = int(ex.correct.sum())
        unsafe_decision = int((ex.should_execute == False).sum())  # noqa: E712
        delivered_wrong = int(((ex.delivered_box.notna()) & (ex.delivered_box != ex.expected_period)).sum())
        grasp_fail = int((ex.grasp_success == False).sum())  # noqa: E712
        # 感知錯誤：在應執行的任務中，選到的偵測實際不是目標盒
        should_ex = ex[ex.should_execute == True]  # noqa: E712
        perception_err = int((should_ex.chosen_detection_truth != should_ex.expected_period).sum())
        robot_fail = int(((should_ex.chosen_detection_truth == should_ex.expected_period) & (should_ex.exec_success == False)).sum())  # noqa: E712
        hazards = g[(g.should_execute == False) | ((g.layer1_passed) & (g.counterfactual_correct == False))]  # noqa: E712
        intercepted = int((~hazards.executed).sum())
        rows.append({
            "strategy": strat,
            "策略": STRATEGY_ZH.get(strat, strat),
            "n_test": n,
            "executed": n_ex,
            "confirm": int((g.decision == "REQUEST_CONFIRMATION").sum()),
            "stop": int((g.decision == "STOP").sum()),
            "correct_retrieval_rate": correct / n,
            "automatic_completion_rate": correct / n,
            "wrong_box_rate": delivered_wrong / n,
            "human_confirmation_rate": (g.decision == "REQUEST_CONFIRMATION").mean(),
            "overall_task_error_rate": (n_ex - correct) / n,
            "error_rate_among_executed": _rate(n_ex - correct, n_ex),
            "decision_error_rate": unsafe_decision / n,
            "perception_error_rate_among_executed": _rate(perception_err, len(should_ex)),
            "robot_grasp_failure_rate_among_executed": _rate(grasp_fail, n_ex),
            "robot_failure_correct_target": robot_fail,
            "missed_auto_rate": int(((g.should_execute == True) & (~g.executed)).sum()) / n,  # noqa: E712
            "hazard_cases": len(hazards),
            "unsafe_action_interception_rate": _rate(intercepted, len(hazards)),
            "mean_task_time_s": float((g.perception_seconds + g.robot_sim_seconds).mean()),
            "mean_robot_sim_time_executed_s": float(ex.robot_sim_seconds.mean()) if n_ex else None,
        })
    return pd.DataFrame(rows)


def risk_coverage(df: pd.DataFrame, strategy: str, score_col: str, higher_is_riskier: bool = True):
    g = df[(df.strategy == strategy) & (df.layer1_passed) & df.counterfactual_correct.notna()].copy()
    if g.empty or g[score_col].isna().all():
        return None
    g = g.dropna(subset=[score_col])
    scores = g[score_col].to_numpy(float)
    if not higher_is_riskier:
        scores = -scores
    order = np.argsort(scores, kind="stable")
    errors = (~g.counterfactual_correct.astype(bool)).to_numpy()[order]
    k = np.arange(1, len(order) + 1)
    return k / len(order), np.cumsum(errors) / k


def _bar(ax, labels, values, title, color=ORANGE, pct=True, errors=None):
    vals = [0 if v is None or (isinstance(v, float) and np.isnan(v)) else v for v in values]
    bars = ax.bar(range(len(vals)), vals, color=color, edgecolor=CHARCOAL, yerr=errors, capsize=3 if errors is not None else 0)
    ax.set_xticks(range(len(vals)), labels, rotation=20, ha="right", fontsize=8)
    ax.set_title(title, fontsize=10)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:.1%}" if pct else f"{v:.2f}", ha="center", va="bottom", fontsize=7)
    ax.spines[["top", "right"]].set_visible(False)


def reliability_plot(ax, conf, correct, title):
    conf, correct = np.asarray(conf, float), np.asarray(correct, float)
    bins = np.linspace(0, 1, 11)
    xs, ys, ns = [], [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            xs.append(conf[m].mean())
            ys.append(correct[m].mean())
            ns.append(int(m.sum()))
    ax.plot([0, 1], [0, 1], "--", color=GREY, lw=1)
    ax.plot(xs, ys, "o-", color=ORANGE)
    for x, y, n in zip(xs, ys, ns):
        ax.annotate(str(n), (x, y), fontsize=6, xytext=(2, 2), textcoords="offset points")
    ax.set_title(f"{title}\nECE={expected_calibration_error(conf, correct):.3f}", fontsize=9)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("預測信心")
    ax.set_ylabel("實際正確率")


def build_report(run_dir: Path) -> dict:
    _setup_font()
    run_dir = Path(run_dir)
    fig_dir = run_dir / "figures"
    fig_dir.mkdir(exist_ok=True)
    df = pd.read_csv(run_dir / "test_decisions.csv")
    for col in ("executed", "should_execute", "layer1_passed", "similar_color"):
        df[col] = df[col].astype(bool)
    metrics = strategy_metrics(df)
    metrics.to_csv(run_dir / "metrics.csv", index=False, encoding="utf-8-sig")

    # 每個策略、每個主要指標都以情境為單位做 bootstrap 95% 信賴區間。
    stat_rows = []
    for strategy in metrics["strategy"]:
        for metric, metric_fn in MAIN_METRICS.items():
            estimate, low, high = bootstrap_ci(df, strategy, metric_fn, n_boot=2000, seed=0)
            stat_rows.append({
                "strategy": strategy,
                "策略": STRATEGY_ZH.get(strategy, strategy),
                "metric": metric,
                "estimate": estimate,
                "ci_low": low,
                "ci_high": high,
                "n_boot": 2000,
            })
    stats_df = pd.DataFrame(stat_rows)
    stats_df.to_csv(run_dir / "stats.csv", index=False, encoding="utf-8-sig")

    pairs = [("D_calibrated_risk", strategy) for strategy in ("A_vision_only", "B_rule_based", "C_fixed_threshold")]
    pairs.append(("E_conformal", "D_calibrated_risk"))
    available = set(df["strategy"])
    pairwise = pd.DataFrame([mcnemar_exact(df, a, b) for a, b in pairs if a in available and b in available])
    pairwise.to_csv(run_dir / "pairwise_tests.csv", index=False, encoding="utf-8-sig")

    # 1. 主要策略比較
    m = metrics.set_index("strategy")
    main = [strategy for strategy in MAIN if strategy in m.index]
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    labels = [STRATEGY_ZH[s] for s in main]
    ci_lookup = stats_df.set_index(["strategy", "metric"])
    for ax, (col, title) in zip(axes.flat, [
        ("correct_retrieval_rate", "正確取藥率（自動完成）"),
        ("wrong_box_rate", "錯盒率"),
        ("error_rate_among_executed", "自動執行中的錯誤率"),
        ("human_confirmation_rate", "請求人工確認率"),
        ("unsafe_action_interception_rate", "危險動作攔截率"),
        ("overall_task_error_rate", "整體任務錯誤率"),
    ]):
        values = [m.loc[s, col] for s in main]
        errors = None
        if col in MAIN_METRICS:
            lower = [max(0.0, value - ci_lookup.loc[(s, col), "ci_low"]) for s, value in zip(main, values)]
            upper = [max(0.0, ci_lookup.loc[(s, col), "ci_high"] - value) for s, value in zip(main, values)]
            errors = np.asarray([lower, upper])
        _bar(ax, labels, values, title, errors=errors)
    fig.suptitle(f"策略比較（測試集 n={int(m.loc['A_vision_only', 'n_test'])}，實際 MuJoCo 模擬）", fontsize=12)
    fig.tight_layout()
    fig.savefig(fig_dir / "strategy_comparison.png", dpi=150)
    plt.close(fig)

    # 2. 消融
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    labels = [STRATEGY_ZH[s] for s in ABL]
    for ax, (col, title) in zip(axes, [
        ("correct_retrieval_rate", "自動完成率"),
        ("error_rate_among_executed", "自動執行中的錯誤率"),
        ("human_confirmation_rate", "人工確認率"),
    ]):
        _bar(ax, labels, [m.loc[s, col] for s in ABL], title, color=CHARCOAL)
    fig.suptitle("消融實驗（移除單一輸入群組）", fontsize=12)
    fig.tight_layout()
    fig.savefig(fig_dir / "ablation.png", dpi=150)
    plt.close(fig)

    # 3. 風險—覆蓋曲線（通過硬性限制的測試案例；標籤來自反事實物理執行）
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    d0 = df[df.strategy == "C_fixed_threshold"].copy()
    d0["min_raw_conf"] = d0[["speech_raw_conf", "vision_raw_conf"]].min(axis=1)
    curves = {}
    for name, frame, col, risky in [
        ("D_calibrated_risk", df, "risk", True),
        ("E_conformal", df, "risk", True),
        ("D_no_calibration", df, "risk", True),
        ("D_no_vision_conf", df, "risk", True),
        ("D_no_speech_conf", df, "risk", True),
        ("C_fixed_threshold", d0, "min_raw_conf", False),
    ]:
        rc = risk_coverage(frame, name, col, risky)
        if rc is None:
            continue
        cov, risk = rc
        curves[name] = {"coverage": cov.tolist(), "risk": risk.tolist()}
        style = dict(color=ORANGE, lw=2.2) if name == "D_calibrated_risk" else dict(lw=1.2)
        label = STRATEGY_ZH[name] if name != "C_fixed_threshold" else "C 以 min(原始信心) 排序"
        ax.plot(cov, risk, label=label, **style)
    ax.set_xlabel("覆蓋率（自動執行比例）")
    ax.set_ylabel("已執行任務的錯誤率")
    ax.set_title("風險—覆蓋曲線（通過硬性限制的測試案例）")
    ax.legend(fontsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(fig_dir / "risk_coverage.png", dpi=150)
    plt.close(fig)

    # 4. 校正圖：風險模型（測試）、視覺、語音
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    dd = df[(df.strategy == "D_calibrated_risk") & df.layer1_passed & df.counterfactual_correct.notna()]
    dn = df[(df.strategy == "D_no_calibration") & df.layer1_passed & df.counterfactual_correct.notna()]
    risk_ece = {}
    for ax, frame, title in ((axes[0], dd, "D 風險（Platt 校正）"), (axes[1], dn, "D 風險（未校正）")):
        if len(frame):
            conf = 1 - frame.risk.to_numpy(float)          # 「執行正確」的預測機率
            correct = frame.counterfactual_correct.astype(bool).to_numpy()
            reliability_plot(ax, conf, correct, f"{title} 測試 n={len(frame)}")
            risk_ece[title] = expected_calibration_error(conf, correct)
    rel_path = MODELS_DIR / "vision_reliability.json"
    if rel_path.exists():
        rel = json.loads(rel_path.read_text())
        reliability_plot(axes[2], rel["raw_hue_softmax"]["confidence"], rel["raw_hue_softmax"]["correct"], "視覺：原始色相分數")
        reliability_plot(axes[3], rel["calibrated"]["confidence"], rel["calibrated"]["correct"], "視覺：校正後機率")
    fig.tight_layout()
    fig.savefig(fig_dir / "calibration.png", dpi=150)
    plt.close(fig)

    # 5. 混淆矩陣：意圖、決策 vs 正解
    test_d = df[df.strategy == "D_calibrated_risk"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    cm = pd.crosstab(test_d.true_intent, test_d.pred_intent).reindex(index=INTENTS, columns=INTENTS, fill_value=0)
    axes[0].imshow(cm.values, cmap="Oranges")
    axes[0].set_xticks(range(len(INTENTS)), [i.replace("_", "\n") for i in INTENTS], fontsize=6)
    axes[0].set_yticks(range(len(INTENTS)), INTENTS, fontsize=6)
    for (i, j), v in np.ndenumerate(cm.values):
        axes[0].text(j, i, v, ha="center", va="center", fontsize=7)
    axes[0].set_title("意圖混淆矩陣（測試集，含 ASR 雜訊）")
    axes[0].set_xlabel("預測")
    axes[0].set_ylabel("真實")
    tab = df[df.strategy.isin(main)].groupby(["strategy", "should_execute", "decision"]).size().unstack(fill_value=0)
    tab = tab.reindex(columns=["EXECUTE", "REQUEST_CONFIRMATION", "STOP"], fill_value=0)
    ylabels = [f"{STRATEGY_ZH[s].split(' ')[0]} | {'應執行' if se else '不應執行'}" for s, se in tab.index]
    axes[1].imshow(tab.values, cmap="Greys")
    axes[1].set_xticks(range(3), ["執行", "請求確認", "停止"])
    axes[1].set_yticks(range(len(ylabels)), ylabels, fontsize=7)
    for (i, j), v in np.ndenumerate(tab.values):
        axes[1].text(j, i, v, ha="center", va="center", fontsize=8, color=ORANGE)
    axes[1].set_title("決策 × 正確答案")
    fig.tight_layout()
    fig.savefig(fig_dir / "confusion_matrices.png", dpi=150)
    plt.close(fig)

    # 6. 失敗案例摘要
    failures = df[df.strategy.isin(main) & df.executed & (df.correct == False)]  # noqa: E712
    lines = ["# 失敗案例摘要（測試集，自動執行但結果不正確）", ""]
    lines.append("| 策略 | 情境 | 應執行 | 應取 | 實際送達 | 失敗階段 | 條件 | 決策理由 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in failures.iterrows():
        lines.append(f"| {STRATEGY_ZH.get(r.strategy, r.strategy)} | {r.scenario_id} | {'是' if r.should_execute else '否'} | "
                     f"{r.expected_period if isinstance(r.expected_period, str) else '—'} | "
                     f"{r.delivered_box if isinstance(r.delivered_box, str) else '無'} | "
                     f"{r.failure_stage if isinstance(r.failure_stage, str) else ('錯誤決策' if not r.should_execute else '—')} | "
                     f"{r.tags if isinstance(r.tags, str) else ''} | {str(r.reasons)[:80]} |")
    if failures.empty:
        lines.append("| （無） | | | | | | | |")
    (run_dir / "failure_cases.md").write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "metrics": json.loads(metrics.to_json(orient="records", force_ascii=False)),
        "statistics": json.loads(stats_df.to_json(orient="records", force_ascii=False)),
        "pairwise_tests": json.loads(pairwise.to_json(orient="records", force_ascii=False)),
        "risk_ece_test": risk_ece,
        "risk_coverage": curves,
        "intent_accuracy_test": float((test_d.true_intent == test_d.pred_intent).mean()),
        "n_failures_main": int(len(failures)),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import sys

    from carearm.settings import RESULTS_DIR

    print(json.dumps(build_report(Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS_DIR / "main")["metrics"], indent=1, ensure_ascii=False))
