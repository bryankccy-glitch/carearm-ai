import { useCallback, useEffect, useState } from "react";
import { AlertsInfo, Analysis, api, EventRow, HqMessage, ModelsInfo, Period, PERIOD_ZH, PERIODS, Run, Status } from "./api";
import { AlertBanner, AlertsTab } from "./components/Alerts";
import { DecisionPanel } from "./components/DecisionPanel";
import { EventLog } from "./components/EventLog";
import { Experiments } from "./components/Experiments";
import { BoxesCard, ComplianceCard, ExecutionCard, TodayCard } from "./components/StatusCards";
import { VoiceInput } from "./components/VoiceInput";

const nowHHMM = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const QUICK = ["我要吃藥了", "幫我拿早上的藥", "幫我拿中午的藥", "幫我拿晚上的藥", "我吃完藥了", "我覺得不舒服"];
type Tab = "console" | "alerts" | "experiments" | "events";
type View = "annotated" | "front" | "overhead" | "wrist" | "inspection";
const VIEW_HUD: Record<View, string> = {
  annotated: "OVERHEAD・偵測",
  front: "FRONT・即時",
  overhead: "OVERHEAD・即時",
  wrist: "WRIST RGB・即時",
  inspection: "WRIST RGB-D・盒內計數",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("console");
  const [status, setStatus] = useState<Status | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [alerts, setAlerts] = useState<AlertsInfo | null>(null);
  const [inbox, setInbox] = useState<HqMessage[]>([]);
  const [text, setText] = useState("");
  const [clock, setClock] = useState(nowHHMM());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [view, setView] = useState<View>("annotated");
  const [showScene, setShowScene] = useState(false);
  const [cond, setCond] = useState({ lighting: "normal", shadow: false, occlusion: 0, occlusion_target: "noon", camera_tilt_deg: 0, similar_color: false });

  const refresh = useCallback(async () => {
    try {
      const [s, e, a, h] = await Promise.all([api.status(), api.events(), api.alerts(), api.hqInbox()]);
      setStatus(s);
      setEvents(e);
      setAlerts(a);
      setInbox(h);
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    refresh();
    api.experiments().then((r) => setRuns(r.runs)).catch(() => undefined);
    api.models().then(setModels).catch(() => undefined);
    const id = window.setInterval(refresh, 600);
    return () => window.clearInterval(id);
  }, [refresh]);

  // 執行中自動切到側視即時；結束後保留畫面
  useEffect(() => {
    if (status?.busy) setView((v) => (v === "annotated" ? "front" : v));
  }, [status?.busy]);

  const run = async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(label);
    setError(null);
    try {
      const out = await fn();
      setFrameKey((k) => k + 1);
      return out;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const showAnalysis = (a: Analysis) => {
    setAnalysis(a);
    setView(a.adherence ? "inspection" : "annotated");
    setFrameKey((k) => k + 1);
    refresh();
  };
  const analyze = async (fn: () => Promise<Analysis>) => {
    const out = await run("分析指令", fn);
    if (out) showAnalysis(out);
  };
  const execute = (confirmed: boolean) => {
    setView("front");
    run("執行", () => api.execute(confirmed));
  };
  const inspectCompliance = () => {
    setView("inspection");
    run("手腕相機檢查", () => api.inspectCompliance(status?.last_delivered_period));
  };
  const simulateIntake = (count: number, dropped: boolean) => {
    setView("inspection");
    run(dropped ? "模擬藥丸掉落" : "模擬服藥", () => api.simulateIntake(status?.last_delivered_period ?? null, count, dropped));
  };

  const offline = status === null;
  const d = analysis?.decision;
  const canExecute = d?.decision === "EXECUTE" && !status?.busy;
  // 照護者只能覆蓋「相機無法確認藥盒是否已空」；其他硬性規則不能覆蓋（後端同樣檢查）
  const canConfirm = d?.decision === "REQUEST_CONFIRMATION" && !status?.busy && d.target_index !== null &&
    d.rules.every((r) => r.passed || r.rule === "adherence_verified");
  const taken = status ? PERIODS.filter((p) => status.taken_today.includes(p)).length : 0;
  const stageIndex = status?.stage_index ?? 0;
  const stageTotal = status?.stage_total ?? 9;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>CareArm AI</h1>
            <p>長照取藥手臂・研究模擬</p>
          </div>
        </div>

        <div className="telemetry" aria-live="polite">
          <div className="state">
            <span className={`dot ${offline ? "off" : status?.busy ? "run" : "idle"}`} />
            <div>
              <strong>{offline ? "後端未連線" : status?.paused ? "已暫停" : status?.robot}</strong>
              <span>{offline ? "請執行 start.sh" : status?.busy ? `階段 ${stageIndex}/${stageTotal}・${status.stage ?? "準備中"}` : "可接受指令"}</span>
            </div>
          </div>
          <dl className="tele">
            <div><dt>TCP (m)</dt><dd className="num">{status?.tcp ? status.tcp.map((v) => v.toFixed(3)).join(" / ") : "—"}</dd></div>
            <div><dt>夾爪</dt><dd className="num">{status?.gripper != null ? `${Math.round(status.gripper * 100)}%` : "—"}</dd></div>
            <div><dt>模擬時間</dt><dd className="num">{status ? `${status.sim_time.toFixed(1)} s` : "—"}</dd></div>
            <div><dt>今日服藥</dt><dd className="num">{taken}/3</dd></div>
          </dl>
        </div>

        <div className="top-actions">
          {status?.busy && (
            <button onClick={() => run("暫停", status.paused ? api.resume : api.pause)}>{status.paused ? "繼續" : "暫停"}</button>
          )}
          {!!alerts?.open && (
            <button className={`alert-badge ${alerts.urgent_open ? "urgent" : ""}`} onClick={() => setTab("alerts")}>
              通報 {alerts.open}
            </button>
          )}
          <button className="estop" onClick={() => run("停止", api.stop)} disabled={offline}>緊急停止</button>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {([["console", "操作台"], ["alerts", "通報"], ["experiments", "實驗與模型"], ["events", "事件紀錄"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
        <span className="disclaimer">研究模擬，非醫療器材；顏色與時段對應為可設定的假設</span>
      </nav>

      {error && (
        <div className="error" role="alert">
          <span>{error}</span>
          <button className="link" onClick={() => setError(null)}>關閉</button>
        </div>
      )}

      {tab !== "alerts" && <AlertBanner info={alerts} onAck={(id) => run("通報", () => api.ackAlert(id))} onOpen={() => setTab("alerts")} />}

      {tab === "alerts" && <AlertsTab info={alerts} inbox={inbox} onAck={(id) => run("通報", () => api.ackAlert(id))} />}

      {tab === "console" && (
        <main className="console">
          <section className="panel camera">
            <header className="panel-head">
              <h2>相機</h2>
              <div className="seg" role="tablist" aria-label="相機畫面">
                {([["annotated", "偵測結果"], ["front", "側視"], ["overhead", "俯視"], ["wrist", "手腕"], ["inspection", "盒內計數"]] as const).map(([v, label]) => (
                  <button key={v} role="tab" aria-selected={view === v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{label}</button>
                ))}
              </div>
            </header>
            <div className="frame">
              {!offline && (
                <img
                  src={view === "annotated" ? `/api/camera.png?annotated=true&k=${frameKey}`
                    : view === "inspection" ? `/api/inspection.png?k=${frameKey}`
                    : `/api/camera.png?live=true&view=${view}&k=${frameKey}`}
                  alt={view === "annotated" ? "俯視相機影像與 OpenCV 偵測框" : view === "inspection" ? "手腕 RGB-D 數藥盒內藥丸的結果" : "MuJoCo 即時渲染畫面"}
                  onLoad={() => view !== "annotated" && view !== "inspection" && window.setTimeout(() => setFrameKey((k) => k + 1), status?.busy ? 150 : 900)}
                  onError={() => view !== "annotated" && window.setTimeout(() => setFrameKey((k) => k + 1), 1000)}
                />
              )}
              <div className="hud">
                <span>{VIEW_HUD[view]}</span>
                {status?.busy && <span className="live">{stageIndex}/{stageTotal}・{status.stage}</span>}
              </div>
              {status?.busy && (
                <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={stageTotal} aria-valuenow={stageIndex}>
                  <span style={{ width: `${(stageIndex / stageTotal) * 100}%` }} />
                </div>
              )}
            </div>
            <div className="scene-row">
              <button onClick={() => run("重置", api.reset)} disabled={!!busy || status?.busy}>重置場景</button>
              <button onClick={() => run("隨機", api.randomize)} disabled={!!busy || status?.busy}>隨機擺放</button>
              <button className="link" onClick={() => setShowScene((v) => !v)} aria-expanded={showScene}>{showScene ? "收起實驗條件" : "實驗條件"}</button>
            </div>
            {showScene && (
              <div className="cond-grid">
                <label>光線
                  <select value={cond.lighting} onChange={(e) => setCond({ ...cond, lighting: e.target.value })}>
                    <option value="normal">正常</option><option value="low">昏暗</option><option value="high">過亮</option>
                  </select>
                </label>
                <label>相機傾斜 {cond.camera_tilt_deg}°
                  <input type="range" min={0} max={15} value={cond.camera_tilt_deg} onChange={(e) => setCond({ ...cond, camera_tilt_deg: Number(e.target.value) })} />
                </label>
                <label>遮擋 {Math.round(cond.occlusion * 100)}%
                  <input type="range" min={0} max={0.8} step={0.05} value={cond.occlusion} onChange={(e) => setCond({ ...cond, occlusion: Number(e.target.value) })} />
                </label>
                <label>遮擋目標
                  <select value={cond.occlusion_target} onChange={(e) => setCond({ ...cond, occlusion_target: e.target.value })}>
                    {PERIODS.map((p) => <option key={p} value={p}>{PERIOD_ZH[p]}藥盒</option>)}
                  </select>
                </label>
                <label className="check"><input type="checkbox" checked={cond.shadow} onChange={(e) => setCond({ ...cond, shadow: e.target.checked })} />側光陰影</label>
                <label className="check"><input type="checkbox" checked={cond.similar_color} onChange={(e) => setCond({ ...cond, similar_color: e.target.checked })} />相似顏色</label>
                <div className="row">
                  <button onClick={() => run("套用", () => api.conditions({ ...cond, randomize: false }))} disabled={!!busy || status?.busy}>套用</button>
                  <button onClick={() => run("套用", () => api.conditions({ ...cond, randomize: true }))} disabled={!!busy || status?.busy}>套用並隨機擺放</button>
                </div>
              </div>
            )}
          </section>

          <section className="panel command">
            <div className="cmd-row">
              <VoiceInput clock={clock} disabled={!!busy || offline || !!status?.busy} asr={status?.asr} onResult={showAnalysis} onBusy={setBusy} />
              <form className="text-cmd" onSubmit={(e) => { e.preventDefault(); if (text.trim()) analyze(() => api.command(text.trim(), clock)); }}>
                <input value={text} placeholder="或輸入指令，例如：幫我拿晚上的藥" onChange={(e) => setText(e.target.value)} aria-label="文字指令" />
                <button type="submit" disabled={!!busy || offline || !text.trim()}>送出</button>
              </form>
              <label className="clock">時間<input type="time" value={clock} onChange={(e) => setClock(e.target.value)} /></label>
            </div>
            <div className="chips">
              {QUICK.map((c) => (
                <button key={c} className="chip-btn" onClick={() => { setText(c); analyze(() => api.command(c, clock)); }} disabled={!!busy || offline || !!status?.busy}>{c}</button>
              ))}
              {busy && <span className="muted small">{busy}…</span>}
            </div>
          </section>

          <DecisionPanel a={analysis} busy={!!busy} canExecute={!!canExecute} canConfirm={!!canConfirm} onExecute={execute} />

          {status && (
            <div className="cards">
              <TodayCard s={status} clock={clock} onToggle={(p, v) => run("排程", () => api.taken(p, v))} onResetDay={() => run("新的一天", api.resetDay)} />
              <ExecutionCard s={status} />
              <BoxesCard s={status} />
              <ComplianceCard s={status} disabled={!!busy || status.busy} onInspect={inspectCompliance} onSimulate={simulateIntake} />
            </div>
          )}
          <EventLog events={events} compact />
        </main>
      )}

      {tab === "experiments" && <Experiments runs={runs} models={models} />}
      {tab === "events" && <EventLog events={events} />}
    </div>
  );
}

export type { Period };
