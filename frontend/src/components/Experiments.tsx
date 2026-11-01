import { useState } from "react";
import { Metric, ModelsInfo, pct, Run } from "../api";

const MAIN = ["A_vision_only", "B_rule_based", "C_fixed_threshold", "D_calibrated_risk"];
const COLS: { key: keyof Metric; label: string }[] = [
  { key: "correct_retrieval_rate", label: "正確取藥率" },
  { key: "wrong_box_rate", label: "錯盒率" },
  { key: "error_rate_among_executed", label: "執行中錯誤率" },
  { key: "human_confirmation_rate", label: "人工確認率" },
  { key: "unsafe_action_interception_rate", label: "危險攔截率" },
];
const FIG_ZH: Record<string, string> = {
  "strategy_comparison.png": "策略比較",
  "risk_coverage.png": "風險—覆蓋曲線",
  "calibration.png": "信心校正",
  "confusion_matrices.png": "混淆矩陣",
  "ablation.png": "消融實驗",
};

export function Experiments({ runs, models }: { runs: Run[]; models: ModelsInfo | null }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [fig, setFig] = useState("strategy_comparison.png");
  const largest = [...runs].sort((a, b) => (b.meta.n_scenarios ?? 0) - (a.meta.n_scenarios ?? 0))[0];
  const run = runs.find((r) => r.run_id === runId) ?? largest;

  const m = (s: string) => run?.metrics.find((x) => x.strategy === s);
  const D = m("D_calibrated_risk");
  const A = m("A_vision_only");
  const C = m("C_fixed_threshold");

  return (
    <div className="exp-grid">
      <section className="panel">
        <header className="panel-head">
          <h2>實驗結果</h2>
          {run && (
            <select value={run.run_id} onChange={(e) => setRunId(e.target.value)} aria-label="選擇實驗">
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id}（{r.meta.n_scenarios} 情境）</option>)}
            </select>
          )}
        </header>
        {!run ? (
          <p className="empty">還沒有完成的實驗。執行 <code>python -m carearm.experiments.runner --run-id main</code> 後重新整理。</p>
        ) : (
          <>
            <dl className="kpis wide">
              <div><dt>測試情境</dt><dd className="num">{D?.n_test}</dd></div>
              <div><dt>實際物理執行</dt><dd className="num">{run.meta.physics_executions}</dd></div>
              <div><dt>D 正確取藥率</dt><dd className="num">{pct(D?.correct_retrieval_rate)}</dd></div>
              <div><dt>D 錯盒率</dt><dd className="num">{pct(D?.wrong_box_rate)}</dd></div>
              <div><dt>A 純視覺錯盒率</dt><dd className="num">{pct(A?.wrong_box_rate)}</dd></div>
              <div><dt>C 人工確認率</dt><dd className="num">{pct(C?.human_confirmation_rate)}</dd></div>
            </dl>
            <div className="table-wrap">
              <table className="metrics">
                <thead>
                  <tr>
                    <th>策略</th>
                    <th className="num">執行/確認/停止</th>
                    {COLS.map((c) => <th key={c.key} className="num">{c.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {run.metrics.map((row) => (
                    <tr key={row.strategy} className={row.strategy === "D_calibrated_risk" ? "ours" : MAIN.includes(row.strategy) ? "" : "abl"}>
                      <td>{row.策略}</td>
                      <td className="num">{row.executed}/{row.confirm}/{row.stop}</td>
                      {COLS.map((c) => <td key={c.key} className="num">{pct(row[c.key] as number | null)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="seg" role="tablist">
              {run.figures.map((f) => (
                <button key={f} role="tab" aria-selected={f === fig} className={f === fig ? "on" : ""} onClick={() => setFig(f)}>
                  {FIG_ZH[f] ?? f}
                </button>
              ))}
            </div>
            {run.figures.includes(fig) && <img className="figure" src={`/api/experiments/${run.run_id}/figures/${fig}`} alt={FIG_ZH[fig] ?? fig} />}
          </>
        )}
      </section>

      <section className="panel">
        <header className="panel-head"><h2>模型與校正</h2></header>
        {!models ? <p className="empty small">讀取中…</p> : (
          <table className="model-table">
            <thead><tr><th>模型</th><th className="num">準確率</th><th className="num">ECE 原始</th><th className="num">ECE 校正</th></tr></thead>
            <tbody>
              {models.intent && (
                <tr>
                  <td>意圖分類<small>（{models.intent.n_eval} 句，含未見句型）</small></td>
                  <td className="num">{pct(models.intent.calibrated.accuracy)}</td>
                  <td className="num">{models.intent.raw.ece.toFixed(3)}</td>
                  <td className="num">{models.intent.calibrated.ece.toFixed(3)}</td>
                </tr>
              )}
              {models.vision && (
                <tr>
                  <td>標籤顏色<small>（{models.vision.n_labeled?.eval} 個標籤）</small></td>
                  <td className="num">{pct(models.vision.calibrated.accuracy)}</td>
                  <td className="num">{models.vision.raw_hue_softmax.ece.toFixed(3)}</td>
                  <td className="num">{models.vision.calibrated.ece.toFixed(3)}</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
        {models?.vision && (
          <dl className="kpis">
            <div><dt>識別碼讀取</dt><dd className="num">{pct(models.vision.dot_id_accuracy_all_splits)}</dd></div>
            <div><dt>定位誤差 平均</dt><dd className="num">{models.vision.localization_error_mm.mean.toFixed(1)} mm</dd></div>
            <div><dt>定位誤差 P95</dt><dd className="num">{models.vision.localization_error_mm.p95.toFixed(1)} mm</dd></div>
          </dl>
        )}
        {models?.risk && (
          <dl className="kpis">
            <div><dt>風險執行門檻</dt><dd className="num">≤ {models.risk.execute_below.toFixed(3)}</dd></div>
            <div><dt>訓練樣本</dt><dd className="num">{models.risk.train_n}</dd></div>
            <div><dt>訓練錯誤率</dt><dd className="num">{pct(models.risk.train_positive_rate)}</dd></div>
            <div><dt>校正樣本</dt><dd className="num">{models.risk.calibration_n}</dd></div>
          </dl>
        )}
        <p className="muted small">所有數字都來自實際完成的 MuJoCo 模擬與獨立評估資料，沒有手動填入。詳細解讀見 HANDOFF.md §4。</p>
      </section>
    </div>
  );
}
