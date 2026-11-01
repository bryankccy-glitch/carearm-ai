import { AlertItem, AlertsInfo, HqMessage } from "../api";

const NOTIFY_ZH: Record<string, string> = {
  pending: "傳送中",
  delivered: "已送達總部",
  failed: "送出失敗",
  disabled: "未設定總部（只記錄本機）",
};

export function AlertBanner({ info, onAck, onOpen }: { info: AlertsInfo | null; onAck: (id: number) => void; onOpen: () => void }) {
  const open = info?.alerts.filter((a) => a.status === "open") ?? [];
  if (open.length === 0) return null;
  const top = open[0];
  return (
    <div className={`alert-banner ${top.level.toLowerCase()}`} role="alert">
      <strong>{top.level_zh}</strong>
      <span className="msg">{top.message}・{NOTIFY_ZH[top.notify_status] ?? top.notify_status}</span>
      {open.length > 1 && <span className="muted small">另有 {open.length - 1} 筆未處理</span>}
      <button onClick={() => top.id != null && onAck(top.id)}>已處理</button>
      <button className="link" onClick={onOpen}>查看通報</button>
    </div>
  );
}

export function AlertsTab({ info, inbox, onAck }: { info: AlertsInfo | null; inbox: HqMessage[]; onAck: (id: number) => void }) {
  return (
    <div className="exp-grid">
      <section className="panel">
        <header className="panel-head">
          <h2>異常通報</h2>
          <span className="muted small">回報對象：{info?.headquarters ?? "—"}・方式：{info?.webhook ?? "—"}</span>
        </header>
        <p className="muted small">
          說話內容出現身體不適、緊急狀況或異常的取藥要求，或短時間內多次被安全規則擋下時，系統會自動回報。緊急狀況會立刻停止取藥。這是研究模擬的通報流程，不是醫療判斷。
        </p>
        <table className="alerts-table">
          <thead><tr><th>時間</th><th>等級</th><th>內容</th><th>說的話</th><th>回報</th><th></th></tr></thead>
          <tbody>
            {(info?.alerts ?? []).map((a: AlertItem) => (
              <tr key={a.id} className={a.status === "open" ? "open" : "done"}>
                <td className="num">{a.ts.slice(5, 16).replace("T", " ")}</td>
                <td><span className={`chip lvl-${a.level.toLowerCase()}`}>{a.level_zh}</span></td>
                <td>{a.category}</td>
                <td className="muted">「{a.transcript}」</td>
                <td className="small">{NOTIFY_ZH[a.notify_status] ?? a.notify_status}</td>
                <td>
                  {a.status === "open"
                    ? <button onClick={() => a.id != null && onAck(a.id)}>已處理</button>
                    : <span className="muted small">已處理 {a.ack_ts?.slice(11, 16)}</span>}
                </td>
              </tr>
            ))}
            {(info?.alerts.length ?? 0) === 0 && <tr><td colSpan={6} className="muted">目前沒有通報</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <header className="panel-head"><h2>總部收件匣（模擬）</h2></header>
        <p className="muted small">設定檔 alerts.webhook_url 為 "local" 時，通報會送到這裡；改成真正的網址就會以 JSON POST 送出。</p>
        <ol className="inbox">
          {inbox.map((m, i) => (
            <li key={i} className={m.level.toLowerCase()}>
              <div className="row"><span className={`chip lvl-${m.level.toLowerCase()}`}>{m.level_zh}</span><span className="num muted">{m.received_ts.slice(11, 19)}</span></div>
              <strong>{m.resident}：{m.category}</strong>
              <span className="muted small">「{m.transcript}」・決策 {String(m.context?.decision ?? "—")}</span>
            </li>
          ))}
          {inbox.length === 0 && <li className="muted">尚未收到通報</li>}
        </ol>
      </section>
    </div>
  );
}
