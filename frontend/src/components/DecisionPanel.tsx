import { useState } from "react";
import { Analysis, DECISION_ZH, PERIOD_ZH, pct } from "../api";

const RULE_ZH: Record<string, string> = {
  no_retrieval_requested: "取藥意圖",
  schedule_consistency: "排程一致",
  required_information: "資訊完整",
  duplicate_retrieval: "重複取藥",
  target_detected: "找到目標",
  box_identity: "盒子識別碼",
  unique_target: "目標唯一",
  delivered_box_present: "遞送區有盒子",
  single_delivered_box: "遞送區單一盒",
  delivered_box_identity: "空盒身分",
  adherence_verified: "服藥驗證",
};

const ADH_CLASS: Record<string, string> = { verified_taken: "ok", pills_remaining: "fail", unverified: "warn" };

function RiskBar({ risk, low, high }: { risk: number; low: number; high: number }) {
  const c = (v: number) => Math.max(0, Math.min(1, v)) * 100;
  return (
    <div className="riskbar" role="img" aria-label={`風險 ${risk.toFixed(3)}`}>
      <div className="zone exec" style={{ width: `${c(low)}%` }} />
      <div className="zone confirm" style={{ left: `${c(low)}%`, width: `${c(high) - c(low)}%` }} />
      <div className="zone stop" style={{ left: `${c(high)}%`, right: 0 }} />
      <div className="needle" style={{ left: `${c(risk)}%` }} />
    </div>
  );
}

interface Props {
  a: Analysis | null;
  busy: boolean;
  canExecute: boolean;
  canConfirm: boolean;
  onExecute: (confirmed: boolean) => void;
}

export function DecisionPanel({ a, busy, canExecute, canConfirm, onExecute }: Props) {
  const [open, setOpen] = useState(false);
  if (!a) {
    return (
      <section className="panel decision">
        <header className="panel-head"><h2>決策</h2></header>
        <p className="empty">說一句指令或點下方的範例。系統會依序檢查語音、視覺、排程、安全規則與風險，再決定執行、請求確認或停止。</p>
      </section>
    );
  }
  const d = a.decision;
  const target = d.target_index !== null ? a.detections[d.target_index] : null;
  const passed = d.rules.filter((r) => r.passed).length;
  const failed = d.rules.find((r) => !r.passed);
  const collect = d.action === "collect";
  const visionConf = target ? (target.calibrated_probs[d.target_period ?? target.predicted_period] ?? null) : null;

  return (
    <section className="panel decision">
      <header className="panel-head">
        <h2>決策</h2>
        <span className="muted small">{collect ? "回收空盒" : "取藥"}・{a.source === "browser-asr" || a.source === "audio" ? "語音" : "文字"}輸入</span>
      </header>

      <div className={`verdict ${d.decision.toLowerCase()}`}>
        <span className="verdict-word">{DECISION_ZH[d.decision]}</span>
        <div className="verdict-meta">
          <strong>{a.target_box_id ? `${a.target_box_id}・${PERIOD_ZH[d.target_period ?? ""]}` : "無目標"}</strong>
          <span>{failed ? failed.message : d.reasons[d.reasons.length - 1]}</span>
        </div>
      </div>

      <table className="signals">
        <tbody>
          <tr>
            <th>語音</th>
            <td className="quote">「{a.intent.transcript}」</td>
            <td className="num">{pct(a.intent.confidence)}</td>
          </tr>
          <tr>
            <th></th>
            <td className="muted small">{a.intent.intent}・原始 {pct(a.intent.raw_confidence)}・差距 {a.intent.margin.toFixed(2)}{a.asr ? `・${a.asr.engine}` : ""}</td>
            <td></td>
          </tr>
          <tr>
            <th>視覺</th>
            <td>
              {collect ? "遞送區" : "桌面"} {a.detections.length} 盒
              {target && <>・目標 {target.dot_count} 點・({target.world_xy[0].toFixed(3)}, {target.world_xy[1].toFixed(3)}) m</>}
            </td>
            <td className="num">{pct(visionConf)}</td>
          </tr>
          <tr>
            <th>排程</th>
            <td>{a.schedule.time}・合理 {a.schedule.plausible.map((p) => PERIOD_ZH[p]).join("、") || "無"}{a.schedule.target ? `・目標${PERIOD_ZH[a.schedule.target]}` : ""}</td>
            <td className="num">{a.schedule.conflict || a.schedule.duplicate ? "衝突" : "一致"}</td>
          </tr>
          <tr>
            <th>安全規則</th>
            <td>{failed ? <span className="bad">阻擋：{RULE_ZH[failed.rule] ?? failed.rule}</span> : "全部通過"}</td>
            <td className="num">{passed}/{d.rules.length}</td>
          </tr>
          {a.adherence && (
            <tr>
              <th>盒內檢查</th>
              <td>
                <span className={`chip ${ADH_CLASS[a.adherence.verdict]}`}>{a.adherence.verdict_zh}</span>
                {" "}手腕 RGB-D・看 {a.adherence.looks} 次
                {a.adherence.claims_with_pills > 0 && <span className="muted small">・今天說吃完但仍有藥 {a.adherence.claims_with_pills} 次</span>}
              </td>
              <td className="num">{a.adherence.verdict === "pills_remaining" ? `${a.adherence.count} 顆` : a.adherence.verdict === "verified_taken" ? "空" : "?"}</td>
            </tr>
          )}
          <tr>
            <th>風險</th>
            <td>
              {d.risk !== null && a.thresholds ? (
                <RiskBar risk={d.risk} low={a.thresholds.execute_below} high={a.thresholds.stop_above} />
              ) : (
                <span className="muted small">{collect ? "回收只看影像身分，不使用風險模型" : "安全規則未通過，不計算"}</span>
              )}
            </td>
            <td className="num">{d.risk !== null ? d.risk.toFixed(3) : "—"}</td>
          </tr>
        </tbody>
      </table>

      <div className="actions">
        <button className="primary" disabled={!canExecute || busy} onClick={() => onExecute(false)}>
          {collect ? "執行回收空盒" : "執行取藥"}
        </button>
        <button disabled={!canConfirm || busy} onClick={() => onExecute(true)}>照護者確認後執行</button>
        <button className="link" onClick={() => setOpen((v) => !v)} aria-expanded={open}>{open ? "收起細節" : "決策細節"}</button>
      </div>

      {open && (
        <div className="details">
          <ul className="rules">
            {d.rules.map((r) => (
              <li key={r.rule} className={r.passed ? "pass" : "fail"}>
                <span className="mark">{r.passed ? "通過" : "阻擋"}</span>
                <span>{RULE_ZH[r.rule] ?? r.rule}：{r.message}</span>
              </li>
            ))}
          </ul>
          <ol className="reasons">{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ol>
          {a.thresholds && !collect && (
            <p className="muted small">門檻：風險 ≤ {a.thresholds.execute_below.toFixed(3)} 執行，&gt; {a.thresholds.stop_above.toFixed(2)} 停止，中間請求確認。</p>
          )}
          {a.adherence && a.adherence.reasons.length > 0 && (
            <p className="muted small">盒內檢查備註：{a.adherence.reasons.join("；")}</p>
          )}
          {a.timing_ms && (
            <p className="muted small">處理時間：視覺 {a.timing_ms.vision} ms（含手臂讓出視野）・意圖 {a.timing_ms.speech} ms・決策 {a.timing_ms.decision} ms</p>
          )}
        </div>
      )}
    </section>
  );
}
