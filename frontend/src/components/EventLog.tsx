import { useState } from "react";
import { EventRow } from "../api";

const SOURCES: [string, string][] = [
  ["all", "全部"],
  ["decision", "決策"],
  ["robot", "手臂"],
  ["speech", "語音"],
  ["vision", "視覺"],
  ["schedule", "排程"],
  ["simulation", "場景"],
  ["alert", "通報"],
  ["client", "瀏覽器"],
];

export function EventLog({ events, compact = false }: { events: EventRow[]; compact?: boolean }) {
  const [source, setSource] = useState("all");
  const rows = events.filter((e) => source === "all" || e.source === source);
  return (
    <section className="panel events">
      <header className="panel-head">
        <h2>事件紀錄</h2>
        {!compact && (
          <div className="row">
            <div className="seg">
              {SOURCES.map(([k, label]) => (
                <button key={k} className={source === k ? "on" : ""} onClick={() => setSource(k)}>{label}</button>
              ))}
            </div>
            <a className="btn" href="/api/events.csv">匯出 CSV</a>
          </div>
        )}
      </header>
      <ol className={`log ${compact ? "short" : ""}`}>
        {(compact ? rows.slice(0, 30) : rows).map((e, i) => (
          <li key={i} className={e.level.toLowerCase()}>
            <time className="num">{e.ts.slice(11, 19)}</time>
            <span className="src">{SOURCES.find(([k]) => k === e.source)?.[1] ?? e.source}</span>
            <span className="msg">{e.message}</span>
          </li>
        ))}
        {rows.length === 0 && <li className="muted">尚無紀錄</li>}
      </ol>
    </section>
  );
}
