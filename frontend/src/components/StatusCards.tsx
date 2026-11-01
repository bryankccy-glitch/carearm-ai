import { PERIOD_ZH, PERIODS, Status } from "../api";

const VERDICT_ZH: Record<string, string> = {
  verified_taken: "相機確認已空",
  pills_remaining: "盒內仍有藥",
  unverified: "無法確認",
  caregiver_confirmed: "照護者確認",
};

const STATE_ZH = {
  collected: "已回收",
  delivered: "已送達",
  taken: "已服用",
  due: "服藥時段",
  waiting: "未到時段",
  passed: "已過時段",
} as const;

function periodState(s: Status, p: string, clock: string): keyof typeof STATE_ZH {
  const b = s.boxes[p];
  if (b?.in_collection) return "collected";
  if (s.taken_today.includes(p)) return "taken";
  if (b?.in_delivery) return "delivered";
  const [start, end] = s.schedule[p];
  if (clock >= start && clock < end) return "due";
  return clock < start ? "waiting" : "passed";
}

export function TodayCard({ s, clock, onToggle, onResetDay }: { s: Status; clock: string; onToggle: (p: string, v: boolean) => void; onResetDay: () => void }) {
  const done = PERIODS.filter((p) => s.taken_today.includes(p)).length;
  return (
    <section className="panel card">
      <header className="panel-head">
        <h2>今日服藥</h2>
        <span className="num big-num">{done}<small>/3</small></span>
      </header>
      <ol className="timeline">
        {PERIODS.map((p) => {
          const st = periodState(s, p, clock);
          return (
            <li key={p} className={`tl ${st}`}>
              <span className={`swatch-dot ${p}`} aria-hidden />
              <div>
                <strong>{PERIOD_ZH[p]}</strong>
                <span className="muted small">
                  {s.schedule[p][0]}–{s.schedule[p][1]}・{s.boxes[p]?.box_id}
                  {s.dose_state?.[p]?.verdict && (
                    <>・<span className={`adh ${s.dose_state[p].verdict}`}>{VERDICT_ZH[s.dose_state[p].verdict!]}
                      {s.dose_state[p].verdict === "pills_remaining" ? `（${s.dose_state[p].pills_remaining} 顆）` : ""}</span></>
                  )}
                </span>
              </div>
              <span className={`chip ${st}`}>{STATE_ZH[st]}</span>
              <button className="link small" onClick={() => onToggle(p, !s.taken_today.includes(p))}>
                {s.taken_today.includes(p) ? "取消" : "標記"}
              </button>
            </li>
          );
        })}
      </ol>
      <p className="muted small">送達算入「今天已取過」（防重複給藥）；相機驗證或照護者確認後才算「已服用」。紀錄存在 SQLite，重啟後保留。</p>
      <button className="link small" onClick={onResetDay}>開始新的一天（清除今日紀錄）</button>
    </section>
  );
}

export function ExecutionCard({ s }: { s: Status }) {
  const e = s.last_execution;
  const total = e?.sim_seconds ?? 0;
  return (
    <section className="panel card">
      <header className="panel-head">
        <h2>最近一次執行</h2>
        {e && <span className={`chip ${e.success ? "ok" : "fail"}`}>{e.success ? "物理驗證通過" : `失敗：${e.failure_stage}`}</span>}
      </header>
      {!e ? (
        <p className="empty small">執行後顯示夾取高度、放置誤差與各階段時間。</p>
      ) : (
        <>
          <dl className="kpis">
            <div><dt>動作</dt><dd>{e.place_area === "collection" ? "回收空盒" : "取藥送達"}</dd></div>
            <div><dt>抬升高度</dt><dd className="num">{e.max_lift_mm != null ? `${e.max_lift_mm.toFixed(0)} mm` : "—"}</dd></div>
            <div><dt>放置誤差</dt><dd className="num">{e.placement_error_mm != null ? `${e.placement_error_mm.toFixed(1)} mm` : "—"}</dd></div>
            <div><dt>模擬時間</dt><dd className="num">{total ? `${total.toFixed(1)} s` : "—"}</dd></div>
          </dl>
          {e.stage_times && e.stage_times.length > 0 && total > 0 && (
            <div className="stagebar" aria-label="各階段時間">
              {e.stage_times.map(([name, t], i) => {
                const next = i + 1 < e.stage_times!.length ? e.stage_times![i + 1][1] : total;
                return <span key={i} style={{ flexGrow: Math.max(next - t, 0.05) }} title={`${name}：${(next - t).toFixed(1)} s`} />;
              })}
            </div>
          )}
          <p className="muted small">
            抬起 {e.lifted_box ?? "無"} → {e.place_area === "collection" ? "回收區" : "遞送區"} {e.delivered_box ?? "無"}・{e.sim_steps} 控制步
            {e.other_boxes_disturbed ? "・有撞動其他盒" : "・未撞動其他盒"}
          </p>
        </>
      )}
    </section>
  );
}

export function BoxesCard({ s }: { s: Status }) {
  return (
    <section className="panel card">
      <header className="panel-head"><h2>藥盒</h2><span className="muted small">模擬器狀態，僅顯示</span></header>
      <table className="boxes">
        <tbody>
          {PERIODS.map((p) => {
            const b = s.boxes[p];
            if (!b) return null;
            const where = b.in_delivery ? "遞送區" : b.in_collection ? "回收區" : "桌面";
            return (
              <tr key={p}>
                <td><span className={`swatch-dot ${p}`} aria-hidden /> {b.box_id}</td>
                <td>{b.label}</td>
                <td>{where}・預裝 {b.planned_pills} 顆</td>
                <td className="num">{b.sim_position[0].toFixed(3)}, {b.sim_position[1].toFixed(3)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

export function ComplianceCard({ s, disabled, onInspect, onSimulate }: {
  s: Status;
  disabled: boolean;
  onInspect: () => void;
  onSimulate: (count: number, dropped: boolean) => void;
}) {
  const period = s.last_delivered_period;
  const result = period ? s.compliance?.[period] : undefined;
  const good = result?.status === "TAKEN";
  const warn = result?.status === "PARTIAL" || result?.status === "POSSIBLE_DROP";
  const noPillsLeft = (result?.remaining_count ?? 1) <= 0;
  return (
    <section className="panel card compliance-card">
      <header className="panel-head">
        <h2>手腕相機服藥驗證</h2>
        {result && <span className={`chip ${good ? "ok" : warn ? "warn" : "waiting"}`}>{result.status_zh}</span>}
      </header>
      {!period || !result ? (
        <p className="empty small">藥盒送到遞送區後，系統會自動用手腕相機建立服藥前基準。</p>
      ) : (
        <>
          <dl className="kpis">
            <div><dt>藥盒</dt><dd>{PERIOD_ZH[period]}</dd></div>
            <div><dt>預期數量</dt><dd className="num">{result.expected_count} 顆</dd></div>
            <div><dt>估計取走</dt><dd className="num">{result.removed_count} 顆</dd></div>
            {result.method === "rgbd_count" ? (
              <div><dt>杯內剩餘</dt><dd className="num">{result.uncertain ? "看不清" : `${result.remaining_count} 顆`}</dd></div>
            ) : (
              <div><dt>視覺信心</dt><dd className="num">{result.confidence != null ? `${Math.round(result.confidence * 100)}%` : "—"}</dd></div>
            )}
          </dl>
          <p className="muted small">{result.message}</p>
          <div className="compliance-actions">
            <button onClick={onInspect} disabled={disabled}>重新拍照檢查</button>
            <button onClick={() => onSimulate(1, false)} disabled={disabled || noPillsLeft}>模擬取走 1 顆</button>
            <button className="primary" onClick={() => onSimulate(99, false)} disabled={disabled || noPillsLeft}>模擬全部服用</button>
            <button onClick={() => onSimulate(1, true)} disabled={disabled || noPillsLeft}>模擬掉落 1 顆</button>
          </div>
          <p className="muted small">
            「模擬」按鈕只改變 MuJoCo 藥丸；判讀由手腕 RGB-D 直接數杯內藥丸、偵測杯外掉落，不讀模擬器座標。
            住民說「我吃完了」時，回收前也會自動檢查。
          </p>
        </>
      )}
    </section>
  );
}
