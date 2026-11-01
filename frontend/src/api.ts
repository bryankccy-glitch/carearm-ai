// FastAPI 後端呼叫與型別

export type Period = "morning" | "noon" | "evening";
export const PERIODS: Period[] = ["morning", "noon", "evening"];
export const PERIOD_ZH: Record<string, string> = { morning: "早上", noon: "中午", evening: "晚上" };
export const DECISION_ZH: Record<string, string> = {
  EXECUTE: "執行",
  REQUEST_CONFIRMATION: "請求確認",
  STOP: "停止",
};

export interface BoxStatus {
  box_id: string;
  label: string;
  period_zh: string;
  sim_position: number[];
  in_delivery: boolean;
  in_collection: boolean;
  taken_today: boolean;
  planned_pills: number;
}

export interface DoseState {
  retrieved: boolean;
  taken: boolean;
  verdict: "verified_taken" | "pills_remaining" | "unverified" | "caregiver_confirmed" | null;
  pills_remaining: number | null;
  claims_with_pills: number;
  last_ts: string | null;
}

export interface PillInspection {
  count: number;
  empty: boolean;
  uncertain: boolean;
  reasons: string[];
  blobs: { center_px: number[]; area_mm2: number; estimated_pills: number; height_mm?: number }[];
  box_xy: [number, number] | null;
  yaw_deg: number | null;
  dot_count: number;
  outside_objects: number;
  depth_used: boolean;
}

export interface Adherence {
  verdict: "verified_taken" | "pills_remaining" | "unverified";
  verdict_zh: string;
  period: string | null;
  count: number;
  looks: number;
  reasons: string[];
  inspections: PillInspection[];
  claims_with_pills: number;
  alert: boolean;
}

export interface ComplianceResult {
  period: Period;
  status: "WAITING" | "NOT_TAKEN" | "PARTIAL" | "TAKEN" | "POSSIBLE_DROP" | "UNVERIFIED";
  status_zh: string;
  expected_count: number;
  removed_count: number;
  remaining_count: number;
  dropped_regions: number;
  confidence: number | null;      // 只有 RGB 差異法有；RGB-D 計數看 uncertain
  changed_pixels: number;
  message: string;
  method?: "rgbd_count" | "rgb_diff";
  uncertain?: boolean;
  reasons?: string[] | null;
}

export interface Execution {
  requested_period: string;
  lifted_box: string | null;
  delivered_box: string | null;
  grasp_success: boolean;
  placed_in_delivery: boolean;
  other_boxes_disturbed: boolean;
  success: boolean;
  failure_stage: string | null;
  error: string | null;
  sim_steps: number;
  sim_seconds?: number;
  stages: string[];
  stage_times?: [string, number][];
  place_area?: "delivery" | "collection";
  placement_error_mm?: number | null;
  max_lift_mm?: number | null;
}

export interface AsrStatus {
  installed: boolean;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  compute_type?: string | null;
}

export interface Status {
  robot: string;
  busy: boolean;
  paused: boolean;
  stage: string | null;
  tcp?: number[];
  gripper?: number;
  boxes: Record<string, BoxStatus>;
  taken_today: string[];
  retrieved_today: string[];
  collected: string[];
  viewer: boolean;
  risk_model_loaded: boolean;
  sim_time: number;
  stage_total: number;
  stage_index?: number;
  conditions: Record<string, unknown> | null;
  last_execution: Execution | null;
  schedule: Record<string, [string, string]>;
  asr?: AsrStatus;
  compliance: Partial<Record<Period, ComplianceResult>>;
  last_delivered_period: Period | null;
  dose_state?: Record<string, DoseState>;
  last_adherence?: Adherence | null;
}

export interface Detection {
  predicted_period: string;
  dot_count: number;
  world_xy: [number, number];
  calibrated_probs: Record<string, number>;
  raw_scores: Record<string, number>;
  area_ratio: number;
  zone: "table" | "delivery" | "collection";
}

export interface Rule {
  rule: string;
  passed: boolean;
  message: string;
}

export interface Analysis {
  source: string;
  asr: { engine: string; raw_confidence?: number | null } | null;
  time: string;
  intent: {
    transcript: string;
    intent: string;
    raw_confidence: number;
    confidence: number;
    margin: number;
    keyword_period: string | null;
    period: string | null;
  };
  schedule: {
    time: string;
    plausible: string[];
    conflict: boolean;
    needs_clarification: boolean;
    duplicate: boolean;
    target: string | null;
  };
  detections: Detection[];
  decision: {
    decision: string;
    target_period: string | null;
    target_index: number | null;
    risk: number | null;
    rules: Rule[];
    reasons: string[];
    features: Record<string, number>;
    action: "retrieve" | "collect";
  };
  target_box_id: string | null;
  taken_today: string[];
  risk_model_loaded: boolean;
  timing_ms?: { vision: number; speech: number; decision: number };
  alerts?: AlertItem[];
  adherence?: Adherence | null;
  all_zones?: Record<string, number>;
  thresholds: { execute_below: number; stop_above: number } | null;
}

export interface AlertItem {
  id: number | null;
  ts: string;
  level: "URGENT" | "WARN" | "BEHAVIOR";
  level_zh: string;
  category: string;
  message: string;
  transcript: string;
  status: "open" | "acknowledged";
  notify_status: string;
  notify_detail: string;
  ack_ts: string | null;
}

export interface AlertsInfo {
  alerts: AlertItem[];
  open: number;
  urgent_open: number;
  webhook: string;
  headquarters: string;
}

export interface HqMessage {
  received_ts: string;
  level: string;
  level_zh: string;
  category: string;
  resident: string;
  transcript: string;
  context?: Record<string, unknown>;
}

export interface EventRow {
  ts: string;
  source: string;
  level: string;
  message: string;
}

export interface Metric {
  strategy: string;
  策略: string;
  n_test: number;
  executed: number;
  confirm: number;
  stop: number;
  correct_retrieval_rate: number;
  wrong_box_rate: number;
  human_confirmation_rate: number;
  overall_task_error_rate: number;
  error_rate_among_executed: number | null;
  unsafe_action_interception_rate: number | null;
  hazard_cases: number;
}

export interface Run {
  run_id: string;
  meta: { n_scenarios?: number; physics_executions?: number; finished_at?: string; wall_seconds?: number };
  metrics: Metric[];
  intent_accuracy_test: number;
  risk_ece_test: Record<string, number>;
  figures: string[];
}

export interface ModelsInfo {
  intent: { raw: { accuracy: number; ece: number }; calibrated: { accuracy: number; ece: number }; n_eval: number; temperature: number } | null;
  vision: {
    raw_hue_softmax: { accuracy: number; ece: number };
    calibrated: { accuracy: number; ece: number };
    dot_id_accuracy_all_splits: number;
    localization_error_mm: { mean: number; p95: number };
    n_labeled: Record<string, number>;
  } | null;
  risk: { execute_below: number; stop_above: number; train_n: number; train_positive_rate: number; calibration_n: number; platt_applied?: boolean } | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* 非 JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  req<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  status: () => req<Status>("/api/status"),
  events: () => req<EventRow[]>("/api/events?limit=300"),
  experiments: () => req<{ runs: Run[] }>("/api/experiments"),
  models: () => req<ModelsInfo>("/api/models"),
  reset: () => post("/api/reset"),
  randomize: () => post("/api/randomize"),
  conditions: (c: Record<string, unknown>) => post("/api/conditions", c),
  command: (text: string, time: string, extra?: { source: string; asr_engine?: string; asr_confidence?: number }) =>
    post<Analysis>("/api/command", { text, time, ...(extra ?? {}) }),
  selectPeriod: (period: Period, time: string) => post<Analysis>("/api/select_period", { period, time }),
  execute: (caregiver_confirmed: boolean) => post("/api/execute", { caregiver_confirmed }),
  stop: () => post("/api/stop"),
  pause: () => post("/api/pause"),
  resume: () => post("/api/resume"),
  taken: (period: string, taken: boolean) => post("/api/taken", { period, taken }),
  resetDay: () => post("/api/day/reset"),
  alerts: () => req<AlertsInfo>("/api/alerts"),
  ackAlert: (id: number) => post(`/api/alerts/${id}/ack`),
  hqInbox: () => req<HqMessage[]>("/api/hq/inbox"),
  inspectCompliance: (period?: Period | null) => post<ComplianceResult>("/api/compliance/inspect", { period: period ?? null }),
  simulateIntake: (period: Period | null, count: number, drop_on_table = false) =>
    post<ComplianceResult>("/api/simulation/intake", { period, count, drop_on_table }),
  clientLog: (message: string, level = "INFO", detail?: Record<string, unknown>) =>
    post("/api/client_log", { message, level, detail: { ...(detail ?? {}), ua: navigator.userAgent } }).catch(() => undefined),
  audio: async (blob: Blob, time: string) => {
    const form = new FormData();
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    form.append("file", blob, `speech.${ext}`);
    form.append("time", time);
    return req<Analysis>("/api/audio", { method: "POST", body: form });
  },
};

export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;
