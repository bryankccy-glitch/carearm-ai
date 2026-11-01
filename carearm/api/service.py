"""模擬服務：單一工作執行緒擁有 MuJoCo 環境（含 OpenGL renderer）。

MuJoCo 的資料結構與 OpenGL context 都不是執行緒安全的，所以所有模擬、渲染、
手臂控制都在同一個 worker 執行緒中執行；API 執行緒只送出工作並等待結果。
停止 / 暫停只透過執行緒安全的旗標傳遞。
"""

from __future__ import annotations

import queue
import random
import threading
import time
from concurrent.futures import Future
from datetime import datetime

import cv2
import numpy as np

from carearm.database.store import Store
from carearm.decision.adherence import REMAINING, TAKEN as ADH_TAKEN, UNVERIFIED as ADH_UNVERIFIED, VERDICT_ZH, AdherenceResult, apply_adherence, verdict_of
from carearm.decision.engine import (
    CONFIRM,
    EXECUTE,
    STOP,
    CalibratedRisk,
    Observation,
    RiskModel,
    collect_decision,
    is_finish_command,
)
from carearm.decision.schedule import MedicationSchedule, fmt_minutes, to_minutes
from carearm.experiments.scenarios import sample_positions
from carearm.perception.calibration import load_detector
from carearm.perception.compliance import PARTIAL, POSSIBLE_DROP, TAKEN, WristComplianceMonitor
from carearm.perception.pills import PillCounter, annotate_inspection
from carearm.perception.detector import annotate
from carearm.robot.executor import ExecutionControl, RetrievalExecutor
from carearm.settings import MODELS_DIR, PERIOD_ZH, PERIODS, box_specs, load_config
from carearm.simulation.scene import SceneConditions, make_carearm_env
from carearm.safety.alerts import BEHAVIOR, LEVEL_ZH, URGENT, WARN, Alert, AlertCenter, detect_alerts
from carearm.speech.intent import IntentClassifier


CAREGIVER_OVERRIDABLE = {"adherence_verified"}   # 照護者只能覆蓋「相機無法確認藥盒是否已空」


class SimulationService:
    def __init__(self, *, viewer: bool = False, realtime: bool = True) -> None:
        self.cfg = load_config()
        self.store = Store()
        self.alerts = AlertCenter(self.store, self.cfg)
        self.control = ExecutionControl()
        self.want_viewer = viewer
        self.realtime = realtime
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self.state = {
            "robot": "初始化中",
            "busy": False,
            "paused": False,
            "stage": None,
            "last_execution": None,
            "conditions": None,
            "sim_time": 0.0,
        }
        # 從 SQLite 還原今日紀錄：重啟服務也不會忘記已送過藥（避免重複給藥）
        today = self.store.dose_state()
        self.taken_today: set[str] = {p for p, st in today.items() if st["taken"]}
        self.retrieved_today: set[str] = {p for p, st in today.items() if st["retrieved"]}   # 已送達；仍用來阻擋重複取藥
        self.last_adherence: dict | None = None
        self._inspection_frame = None
        self.collected: list[str] = []          # 已收到回收區的盒子（決定下一個格位）
        self.compliance = WristComplianceMonitor(self.cfg.get("compliance"))
        self._compliance_alerted: set[tuple[str, str]] = set()
        self._last_delivered_period: str | None = None
        self._place_xy = None
        self.last_analysis: dict | None = None
        self._last_obs: Observation | None = None
        self._last_decision = None
        self._frame = None
        self._live: dict[str, np.ndarray | None] = {"overhead": None, "front": None, "wrist": None}
        self._delivered_xy: dict[str, tuple[float, float]] = {}   # 送達時放置的位置（手臂自己放的，不讀模擬真值）
        self._live_lock = threading.Lock()
        self._hook_steps = 0
        self._last_live_request = 0.0     # 網頁最近一次要求即時畫面的時間
        self._last_live_render = 0.0
        self._live_view = "front"          # 只渲染網頁正在看的那個視角
        self._annotated = None
        self.viewer_handle = None
        self.worker = threading.Thread(target=self._run, name="mujoco-worker", daemon=True)
        self.worker.start()
        self._ready.wait(timeout=120)

    # ---------------------------------------------------------------- worker
    def _run(self) -> None:
        self.env = make_carearm_env(self.cfg)
        self.sim = self.env.unwrapped
        self.detector = load_detector(self.cfg)
        self.pill_counter = PillCounter(self.cfg)
        self.intent_clf = IntentClassifier.load()
        try:
            self.risk_model = RiskModel.load()
        except Exception:
            self.risk_model = None
        self.strategy = CalibratedRisk(self.cfg, self.risk_model)
        import mujoco

        self.front_cam_id = mujoco.mj_name2id(self.sim.model, mujoco.mjtObj.mjOBJ_CAMERA, "front")
        if self.want_viewer:
            try:
                import mujoco.viewer

                self.viewer_handle = mujoco.viewer.launch_passive(self.sim.model, self.sim.data)
            except Exception as error:  # macOS 需用 mjpython 啟動
                self.log("viewer", f"無法開啟 MuJoCo 原生視窗：{error}", "WARN")
        self._do_reset(SceneConditions())
        self._set(robot="待命")
        self._ready.set()
        last_idle_render = 0.0
        while True:
            try:
                fn, args, fut = self._jobs.get(timeout=0.05)
            except queue.Empty:
                if self.viewer_handle is not None and self.viewer_handle.is_running():
                    self.viewer_handle.sync()
                now = time.monotonic()
                if now - last_idle_render > 0.5 and now - self._last_live_request < 3.0:  # 有人在看才更新
                    self._render_live()
                    last_idle_render = now
                continue
            try:
                fut.set_result(fn(*args))
            except Exception as error:  # noqa: BLE001
                fut.set_exception(error)

    def submit(self, fn, *args, wait: bool = True, timeout: float = 300.0):
        fut: Future = Future()
        self._jobs.put((fn, args, fut))
        return fut.result(timeout=timeout) if wait else fut

    def _set(self, **kw) -> None:
        with self._state_lock:
            self.state.update(kw)

    def log(self, source: str, message: str, level: str = "INFO", payload: dict | None = None) -> None:
        self.store.log_event(source, message, level, payload)

    def _render_live(self) -> None:
        """在 worker 執行緒內渲染即時畫面（俯視、側視或手腕），供網頁輪詢。"""

        frames = {}
        views = (("overhead", self.sim.overhead_id), ("front", self.front_cam_id), ("wrist", self.sim.wrist_cam_id))
        for view, cam_id in [v for v in views if v[0] == self._live_view] or views:
            if view == "wrist":
                frames[view] = self.sim.render_wrist()        # 含相機旁補光燈
                continue
            self.sim._viewer.update_scene(self.sim.data, camera=cam_id)
            frames[view] = self.sim._viewer.render().copy()
        with self._live_lock:
            self._live.update(frames)

    def _hook(self) -> None:
        if self.viewer_handle is not None and self.viewer_handle.is_running():
            self.viewer_handle.sync()
        self._hook_steps += 1
        now = time.monotonic()
        # 只有網頁正在看即時畫面時才渲染，且最多約每 0.15 秒一次
        if now - self._last_live_request < 3.0 and now - self._last_live_render > 0.15:
            self._render_live()
            self._last_live_render = now
        self._set(sim_time=float(self.sim.data.time))

    # ---------------------------------------------------------------- scene
    def _do_reset(self, cond: SceneConditions) -> dict:
        self.env.reset(seed=0, options={"conditions": cond})
        RetrievalExecutor(self.env, self.cfg, step_hook=self._hook).move_to_observation_pose()
        self._frame = self.sim.render_overhead()
        self._render_live()
        self._annotated = None
        self._last_obs = None
        self._last_decision = None
        self.collected = []
        self.compliance.reset()
        self._compliance_alerted.clear()
        self._last_delivered_period = None
        self._delivered_xy = {}
        self._set(conditions=self.sim.conditions.to_dict(), last_execution=None, stage=None)
        return self.sim.conditions.to_dict()

    def reset(self, conditions: dict | None = None) -> dict:
        cond = SceneConditions(**conditions) if conditions else SceneConditions()
        if cond.positions:
            cond.positions = {k: tuple(v) for k, v in cond.positions.items()}
        out = self.submit(self._do_reset, cond)
        self.log("simulation", "環境已重置", payload=out)
        return out

    def randomize(self, seed: int | None = None, **visual) -> dict:
        seed = seed if seed is not None else random.randrange(1_000_000)
        positions = sample_positions(random.Random(seed), self.cfg, True)
        cond = SceneConditions(positions=positions, **visual)
        out = self.submit(self._do_reset, cond)
        self.log("simulation", f"藥盒位置隨機化（seed={seed}）", payload=out)
        return {"seed": seed, **out}

    # ---------------------------------------------------------------- perception
    def _do_perceive(self) -> list:
        """回傳所有標籤（含遞送區、回收區），各自帶 zone 欄位。"""

        RetrievalExecutor(self.env, self.cfg, step_hook=self._hook).move_to_observation_pose()
        self._frame = self.sim.render_overhead()
        return self.detector.detect_all(self._frame, self.sim.camera_geometry())

    def analyze(self, text: str, time_hhmm: str | None = None, source: str = "text", asr: dict | None = None) -> dict:
        minute = to_minutes(time_hhmm) if time_hhmm else datetime.now().hour * 60 + datetime.now().minute
        t0 = time.perf_counter()
        all_detections = self.submit(self._do_perceive)
        t1 = time.perf_counter()
        intent = self.intent_clf.predict(text)
        t2 = time.perf_counter()
        # 重複取藥應在盒子一送達就阻擋；但 UI 的「已服用」要等 wrist 或住民確認。
        sched = MedicationSchedule.from_config(self.cfg, self.retrieved_today | self.taken_today).check(minute, intent.period)
        adherence = None
        if is_finish_command(intent):
            # 吃完藥：只看遞送區內的盒子；回收前先驗證盒內已空
            detections = [d for d in all_detections if d.zone == "delivery"]
            decision = collect_decision(detections, self.cfg, preferred_period=intent.keyword_period)
            if decision.target_index is not None and decision.target_period:
                # collect_decision 可能只挑其中一盒；讓畫面上的目標索引對到正確的偵測
                chosen_xy = decision.target_xy
                if chosen_xy is not None:
                    decision.target_index = next(i for i, d in enumerate(detections) if d.world_xy == chosen_xy)
            # 回收區格位：手臂觀測姿態會擋住部分回收區，所以以已回收的數量決定格位
            area = self.cfg["collection_area"]
            n_slots = len(area["slot_offsets_x"])
            self._place_xy = None if len(self.collected) >= n_slots else (
                area["center"][0] + area["slot_offsets_x"][len(self.collected)], area["center"][1])
            if decision.decision == EXECUTE and self._place_xy is None:
                decision.decision = STOP
                decision.reasons.append("回收區三個格位都滿了，請照護者清空")
            if decision.target_xy is not None and decision.target_period:
                # 回收前先用手腕 RGB-D 看盒內是否還有藥（服藥依從性驗證）
                adherence = self._verify_adherence(decision.target_period, decision.target_xy, text)
                apply_adherence(decision, adherence, self.cfg)
            else:
                taken = decision.target_period or sched.target or sched.candidate
                if taken:
                    self._mark(taken, "marked_taken", detail={"source": "self_report", "text": text})
                    self.log("schedule", f"遞送區沒有可檢查的藥盒：依住民口述記錄{PERIOD_ZH[taken]}的藥已服用（未經相機驗證）", "WARN")
        else:
            detections = [d for d in all_detections if d.zone == "table"]
            decision = self.strategy.decide(Observation(intent, sched, detections))
            # 遞送區格位：依影像中遞送區已有的盒子選第一個空格；全滿就停止
            occupied = [d.world_xy for d in all_detections if d.zone == "delivery"]
            self._place_xy = self._free_slot("delivery_area", occupied)
            if decision.decision != STOP and self._place_xy is None:
                decision.decision = STOP
                decision.target_xy = None
                decision.reasons.append("遞送區三個格位都有藥盒：請先說「我吃完藥了」回收空盒")
        # ---- 異常狀況：關鍵字（身體不適、緊急、異常要求）與行為規則（短時間內多次被擋）
        raised = []
        found = detect_alerts(text)
        levels = {a.level for a in found}
        if URGENT in levels and decision.decision != STOP:
            decision.decision = STOP
            decision.target_xy = None
            decision.reasons.append("偵測到緊急狀況：停止取藥，已回報總部")
        elif WARN in levels and decision.decision == EXECUTE:
            decision.decision = CONFIRM
            decision.reasons.append("使用者表示身體不適：取藥改由照護者確認，已回報總部")
        if any(a.category in ("要求額外劑量", "取用他人藥物") for a in found) and decision.decision != STOP:
            decision.decision = STOP
            decision.target_xy = None
            decision.reasons.append("異常的取藥要求：停止，已回報總部")
        if decision.action == "retrieve" and decision.decision == STOP and not found and \
                intent.intent not in ("MEDICATION_ALREADY_TAKEN", "CANCEL"):
            blocked = next((r.message for r in decision.rules if not r.passed), decision.reasons[-1] if decision.reasons else "")
            behavior = self.alerts.note_blocked_request(blocked, text)
            if behavior:
                found.append(behavior)
        context = {"time": fmt_minutes(minute), "intent": intent.intent, "decision": decision.decision,
                   "taken_today": sorted(self.taken_today), "retrieved_today": sorted(self.retrieved_today)}
        for alert in found:
            raised.append(self.alerts.raise_alert(alert, context).to_dict())
            self.log("alert", f"回報總部｜{alert.message}", "ERROR" if alert.level == URGENT else "WARN",
                     payload={"alert_id": alert.id})
        t3 = time.perf_counter()
        obs = Observation(intent, sched, detections)
        target = detections[decision.target_index] if decision.target_index is not None else None
        self._annotated = annotate(self._frame, all_detections, target)
        self._last_obs, self._last_decision = obs, decision
        analysis = {
            "source": source,
            "asr": asr,
            "time": fmt_minutes(minute),
            "intent": intent.to_dict(),
            "schedule": sched.to_dict(),
            "taken_today": sorted(self.taken_today),
            "retrieved_today": sorted(self.retrieved_today),
            "adherence": adherence.to_dict() if adherence else None,
            "detections": [d.to_dict() for d in detections],
            "decision": decision.to_dict(),
            "target_box_id": box_specs(self.cfg)[decision.target_period].box_id if decision.target_period else None,
            "risk_model_loaded": self.risk_model is not None,
            "timing_ms": {"vision": round((t1 - t0) * 1000), "speech": round((t2 - t1) * 1000, 1),
                          "decision": round((t3 - t2) * 1000, 1)},
            "alerts": raised,
            "all_zones": {z: sum(1 for d in all_detections if d.zone == z) for z in ("table", "delivery", "collection")},
            "thresholds": None if self.risk_model is None else {
                "execute_below": self.risk_model.execute_below, "stop_above": self.risk_model.stop_above},
        }
        self.last_analysis = analysis
        self.log("speech", f"指令「{text}」→ {intent.intent}（校正信心 {intent.confidence:.2f}）", payload={"source": source})
        self.log("vision", f"偵測到 {len(detections)} 個標籤", payload={"detections": [
            {"period": d.predicted_period, "dots": d.dot_count, "xy": d.world_xy} for d in detections]})
        self.log("decision", f"{decision.decision}：{' / '.join(decision.reasons)}",
                 "WARN" if decision.decision != EXECUTE else "INFO", payload={"risk": decision.risk})
        return analysis

    # ---------------------------------------------------------------- execution
    def _do_execute(self) -> dict:
        decision = self._last_decision
        period = decision.target_period
        collecting = decision.action == "collect"
        self._set(busy=True, stage_index=0, robot=f"執行中：{'回收' if collecting else '取'}{PERIOD_ZH[period]}藥盒")

        def on_stage(name: str) -> None:
            with self._state_lock:
                self.state["stage"] = name
                self.state["stage_index"] = int(self.state.get("stage_index") or 0) + 1

        executor = RetrievalExecutor(self.env, self.cfg, control=self.control, step_hook=self._hook,
                                     realtime=self.realtime, status_cb=on_stage)
        try:
            if collecting:
                result = executor.execute(period, decision.target_xy, place_area="collection", place_xy=self._place_xy)
            else:
                result = executor.execute(period, decision.target_xy, place_xy=self._place_xy)
        except Exception:
            self._set(busy=False, paused=False)
            raise
        self._frame = self.sim.render_overhead()
        self._annotated = None          # 執行後顯示最新畫面，而不是執行前的標註圖
        self._render_live()
        data = result.to_dict()
        if result.success and collecting:
            self.collected.append(period)
            self._set(robot="待命（空盒已回收）")
            self.log("robot", f"成功：{box_specs(self.cfg)[period].box_id} 空盒已移到回收區（物理驗證）", payload=data)
        elif result.success:
            self._mark(period, "dispensed", detail={"box_id": box_specs(self.cfg)[period].box_id})
            self._last_delivered_period = period
            self._delivered_xy[period] = tuple(self._place_xy)
            self._set(robot="手腕相機建立服藥前基準")
            try:
                wrist, inspections = self._capture(period, self._place_xy)
                baseline = self.compliance.start(period, wrist, self._planned_pill_count(period), inspections[-1])
                data["compliance_baseline"] = baseline.to_dict()
                if baseline.method == "rgbd_count" and not baseline.uncertain and inspections[-1].count != self._planned_pill_count(period):
                    self.log("compliance", baseline.message, "WARN")
                self._set(robot="待命（已送達，等待服藥驗證）")
            except Exception as error:  # 送藥成功不應因附加相機檢查失敗而永久卡在 busy
                data["compliance_error"] = str(error)
                self._set(robot="待命（已送達；手腕相機基準失敗）")
                self.log("compliance", f"無法建立服藥前基準：{error}", "ERROR")
            self.log("robot", f"成功：{box_specs(self.cfg)[period].box_id} 已放入遞送區（物理驗證）", payload=data)
        else:
            self._set(robot=f"待命（失敗：{result.failure_stage}）")
            self.log("robot", f"未成功：{result.failure_stage} {result.error or ''}", "ERROR", payload=data)
        self._last_decision = None
        self._set(last_execution=data, busy=False, paused=False)   # 狀態文字更新後才解除 busy
        return data

    def _planned_pill_count(self, period: str) -> int:
        """回傳裝盒計畫的預期數量；不讀取即時 MuJoCo 藥丸座標。"""

        layout = self.sim.conditions.pills
        if layout is None:
            return int(self.cfg.get("pills", {}).get("default_per_box", 0))
        return len(layout.get(period, []))

    # ---------------------------------------------------------------- 手腕相機（服藥驗證）
    def _mark(self, period: str, event: str, pills_remaining: int | None = None, detail: dict | None = None) -> None:
        """寫入服藥紀錄（SQLite），並更新今日「已送藥」／「已服用」集合。"""

        if event in ("dispensed", "verified_taken", "caregiver_confirmed", "marked_taken"):
            self.retrieved_today.add(period)
        if event in ("verified_taken", "caregiver_confirmed", "marked_taken"):
            self.taken_today.add(period)
        self.store.log_dose(period, event, pills_remaining, detail)

    def _capture(self, period: str, xy, relook: bool = True):
        """手腕 RGB-D 拍藥盒（在 worker 執行緒）。看不清楚且沒看到藥時，換個角度再看一次。

        回傳（最後一張 RGB, [PillInspection, ...]）。只用相機與手臂自己知道的放置位置，不讀藥丸真值。
        """

        acfg = self.cfg.get("adherence", {})
        offsets = [(0.0, 0.0)] + ([tuple(acfg.get("relook_offset", (0.012, -0.012)))] if relook else [])
        executor = RetrievalExecutor(self.env, self.cfg, control=self.control, step_hook=self._hook, realtime=self.realtime)
        inspections, rgb = [], None
        for k, offset in enumerate(offsets):
            executor.move_wrist_camera_over((float(xy[0]), float(xy[1])), offset)
            rgb = self.sim.render_wrist()
            depth = self.sim.render_wrist_depth(seed=int(self.sim.data.time * 1000) + k)
            insp = self.pill_counter.inspect(rgb, self.sim.wrist_camera_geometry(), depth)
            inspections.append(insp)
            self._inspection_frame = annotate_inspection(rgb, insp)
            with self._live_lock:
                self._live["wrist"] = rgb
            if not (insp.uncertain and insp.count == 0):
                break
        return rgb, inspections

    def _do_inspect_compliance(self, period: str, *, manage_busy: bool = True) -> dict:
        if period not in self.compliance.baselines or period not in self._delivered_xy:
            raise ValueError(f"{PERIOD_ZH[period]}藥盒尚未送達，沒有服藥前基準影像")
        if manage_busy:
            self._set(busy=True, robot=f"手腕相機檢查{PERIOD_ZH[period]}藥盒", stage="服藥依從性檢查（RGB-D）")
        try:
            frame, inspections = self._capture(period, self._delivered_xy[period])
            result, _ = self.compliance.evaluate(period, frame, inspections[-1])
        except Exception:
            if manage_busy:
                self._set(busy=False, robot="待命（服藥驗證失敗）", stage=None)
            raise
        if manage_busy:
            self._set(busy=False, robot="待命（服藥驗證完成）", stage=None)
        data = result.to_dict()
        data["inspections"] = [i.to_dict() for i in inspections]
        if result.status == TAKEN:
            self._mark(period, "verified_taken", 0, {"source": "wrist_rgbd", "looks": len(inspections)})
            data["taken_today"] = sorted(self.taken_today)
            self.log("schedule", f"手腕相機確認{PERIOD_ZH[period]}藥盒已空")
        self.log("compliance", result.message, "WARN" if result.status != TAKEN else "INFO", payload=data)
        if result.status in (PARTIAL, POSSIBLE_DROP) and (period, result.status) not in self._compliance_alerted:
            self._compliance_alerted.add((period, result.status))
            alert = Alert(
                level=BEHAVIOR,
                category="服藥依從性異常",
                transcript="手腕相機自動檢查",
                matched=[result.status],
                message=f"異常行為：{PERIOD_ZH[period]}藥盒｜{result.message}",
            )
            data["alert"] = self.alerts.raise_alert(alert, {"period": period, "compliance": data}).to_dict()
        return data

    def _verify_adherence(self, period: str, xy, text: str) -> AdherenceResult:
        """住民說「我吃完了」：回收前檢查盒內是否已空。"""

        acfg = self.cfg.get("adherence", {})

        def job():
            self._set(robot=f"檢查{PERIOD_ZH[period]}藥盒內是否還有藥", stage="手腕相機檢查")
            frame, inspections = self._capture(period, self._delivered_xy.get(period, xy))
            comp = None
            if period in self.compliance.baselines:          # 同時更新服藥驗證卡（含杯外掉落偵測）
                comp, _ = self.compliance.evaluate(period, frame, inspections[-1])
            self._set(robot="待命", stage=None)
            return inspections, comp

        inspections, comp = self.submit(job)
        verdict, count = verdict_of(inspections)
        reasons = [r for i in inspections for r in i.reasons]
        if comp is not None and comp.status == POSSIBLE_DROP:
            reasons.append(comp.message)
        prior = self.store.dose_state().get(period, {}).get("claims_with_pills", 0)
        claims = prior + (1 if verdict == REMAINING else 0)
        alert = verdict == REMAINING and claims >= int(acfg.get("repeat_claim_alert", 2))
        result = AdherenceResult(verdict, period, count, len(inspections), reasons,
                                 [i.to_dict() for i in inspections], claims, alert)
        if verdict == ADH_TAKEN:
            self._mark(period, "verified_taken", 0, {"source": "finish_command", "looks": len(inspections), "text": text})
        else:
            event = "pills_remaining" if verdict == REMAINING else "unverified"
            self.store.log_dose(period, event, count, {"looks": len(inspections), "reasons": reasons, "text": text})
        self.log("adherence", f"{PERIOD_ZH[period]}藥盒：{VERDICT_ZH[verdict]}" + (f"（約 {count} 顆）" if count else "")
                 + f"，看了 {len(inspections)} 次", "INFO" if verdict == ADH_TAKEN else "WARN", payload=result.to_dict())
        if comp is not None and comp.status == POSSIBLE_DROP and (period, POSSIBLE_DROP) not in self._compliance_alerted:
            self._compliance_alerted.add((period, POSSIBLE_DROP))
            self.alerts.raise_alert(Alert(level=BEHAVIOR, category="服藥依從性異常", transcript=text, matched=[POSSIBLE_DROP],
                                          message=f"異常行為：{PERIOD_ZH[period]}藥盒｜{comp.message}"), {"period": period})
        if alert:
            a = Alert(level=BEHAVIOR, category="宣稱已服藥但盒內仍有藥", transcript=text, matched=[f"{count} 顆"],
                      message=f"異常行為：今天第 {claims} 次說{PERIOD_ZH[period]}的藥吃完了，但手腕相機看到盒內仍有約 {count} 顆")
            self.alerts.raise_alert(a, {"period": period, "pills_remaining": count, "claims": claims})
            self.log("alert", f"回報總部｜{a.message}", "WARN", payload={"alert_id": a.id})
        self.last_adherence = result.to_dict()
        return result

    def inspection_png(self) -> bytes:
        frame = self._inspection_frame if self._inspection_frame is not None else np.zeros((480, 640, 3), np.uint8)
        ok, buf = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        return buf.tobytes()

    def inspect_compliance(self, period: str | None = None) -> dict:
        period = period or self._last_delivered_period
        if period not in PERIODS:
            raise ValueError("請先送達一個藥盒，或指定 morning / noon / evening")
        if self.state["busy"]:
            raise RuntimeError("手臂正在執行中")
        self.control.reset()
        return self.submit(self._do_inspect_compliance, period)

    def _do_simulate_intake(self, period: str, count: int, drop_on_table: bool) -> dict:
        moved = self.sim.take_pills(period, count, drop_on_table=drop_on_table)
        if moved == 0:
            raise ValueError("藥杯內已沒有可移動的藥丸")
        import mujoco

        mujoco.mj_step(self.sim.model, self.sim.data, 150)       # 讓掉到桌上的藥丸落定（手臂控制指令維持不變）
        self.log("simulation", f"模擬住民{'掉落' if drop_on_table else '取走'} {moved} 顆藥丸",
                 "WARN" if drop_on_table else "INFO", {"period": period, "count": moved})
        return self._do_inspect_compliance(period)

    def simulate_intake(self, period: str | None = None, count: int = 99, drop_on_table: bool = False) -> dict:
        """只供研究模擬 UI：改變藥丸狀態，之後仍由手腕影像做判讀。"""

        period = period or self._last_delivered_period
        if period not in PERIODS:
            raise ValueError("請先送達一個藥盒，或指定 period")
        if count < 1:
            raise ValueError("count 必須至少為 1")
        if self.state["busy"]:
            raise RuntimeError("手臂正在執行中")
        self.control.reset()
        return self.submit(self._do_simulate_intake, period, count, drop_on_table)

    def _free_slot(self, area_name: str, occupied: list) -> tuple[float, float] | None:
        area = self.cfg[area_name]
        for dx in area.get("slot_offsets_x", [0.0]):
            slot = (area["center"][0] + dx, area["center"][1])
            if all(np.hypot(slot[0] - x, slot[1] - y) > 0.045 for x, y in occupied):
                return slot
        return None

    def execute(self, caregiver_confirmed: bool = False) -> dict:
        decision = self._last_decision
        if decision is None:
            raise ValueError("請先送出指令並完成分析")
        if decision.decision == EXECUTE:
            pass
        elif decision.decision == CONFIRM and caregiver_confirmed:
            failed = [r for r in decision.rules if not r.passed]
            if any(r.rule not in CAREGIVER_OVERRIDABLE for r in failed):
                raise PermissionError("硬性安全限制未通過，照護者確認也不能覆蓋；請先解決原因")
            if failed:
                self._mark(decision.target_period, "caregiver_confirmed", detail={"reason": "相機無法確認，照護者確認已服藥"})
            if decision.target_xy is None:
                raise PermissionError("沒有可執行的目標")
            self.log("decision", "照護者已確認，允許執行", "WARN")
        else:
            raise PermissionError(f"決策為 {decision.decision}，不允許執行")
        if self.state["busy"]:
            raise RuntimeError("手臂正在執行中")
        self.control.reset()
        self.submit(self._do_execute, wait=False)
        return {"started": True}

    def stop(self) -> None:
        self.control.stop_event.set()
        self.log("robot", "緊急停止", "WARN")

    def pause(self, paused: bool) -> None:
        if paused:
            self.control.pause_event.set()
        else:
            self.control.pause_event.clear()
        self._set(paused=paused)
        self.log("robot", "暫停" if paused else "繼續")

    # ---------------------------------------------------------------- views
    def camera_png(self, annotated: bool = True) -> bytes:
        frame = self._annotated if (annotated and self._annotated is not None) else self._frame
        if frame is None:
            frame = np.zeros((480, 640, 3), np.uint8)
        ok, buf = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        return buf.tobytes()

    def live_png(self, view: str = "front") -> bytes:
        """即時畫面（JPEG），永不阻塞。"""

        if view not in ("front", "overhead", "wrist"):
            view = "front"
        # 不排入 worker：worker 會在有人觀看時自行更新（執行中約每 0.15 秒、閒置每 0.5 秒），這裡只讀最新一張
        now = time.monotonic()
        stale = now - self._last_live_request > 2.0 or view != self._live_view
        self._live_view = view
        self._last_live_request = now
        if stale and not self.state["busy"] and self._jobs.empty():
            try:                                   # 閒置一段時間後的第一張：先請 worker 重新渲染
                self.submit(self._render_live, timeout=3)
            except Exception:  # noqa: BLE001
                pass
        with self._live_lock:
            frame = self._live.get(view)
        if frame is None:
            frame = np.zeros((480, 640, 3), np.uint8)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def status(self) -> dict:
        with self._state_lock:
            state = dict(self.state)
        boxes = {}
        if hasattr(self, "sim"):
            specs = box_specs(self.cfg)
            for p in PERIODS:
                pos = self.sim.box_position(p)
                boxes[p] = {"box_id": specs[p].box_id, "label": specs[p].label_name, "period_zh": PERIOD_ZH[p],
                            "sim_position": [round(float(v), 4) for v in pos],
                            "in_delivery": bool(np.all(np.abs(pos[:2] - np.asarray(self.cfg["delivery_area"]["center"][:2]))
                                                        <= np.asarray(self.cfg["delivery_area"]["half_size"]))),
                            "in_collection": bool(np.all(np.abs(pos[:2] - np.asarray(self.cfg["collection_area"]["center"][:2]))
                                                          <= np.asarray(self.cfg["collection_area"]["half_size"]))),
                            "taken_today": p in self.taken_today,
                            "planned_pills": self._planned_pill_count(p)}
            state["tcp"] = [round(float(v), 4) for v in self.sim.tcp_position]
            state["gripper"] = round(float(self.sim.get_gripper_pose()[0]) / 255.0, 3)   # 0 張開 → 1 閉合
        state["boxes"] = boxes
        state["taken_today"] = sorted(self.taken_today)
        state["retrieved_today"] = sorted(self.retrieved_today)
        state["viewer"] = self.viewer_handle is not None
        state["risk_model_loaded"] = getattr(self, "risk_model", None) is not None
        state["schedule"] = self.cfg["schedule"]
        state["collected"] = list(self.collected)
        state["stage_total"] = 9
        state["compliance"] = self.compliance.info()
        state["dose_state"] = self.store.dose_state()
        state["last_adherence"] = self.last_adherence
        state["last_delivered_period"] = self._last_delivered_period
        return state

    def reset_day(self) -> None:
        """新的一天：清除今日已服用紀錄（不移動場景中的盒子）。"""

        self.taken_today.clear()
        self.retrieved_today.clear()
        self.store.log_dose(None, "day_reset")
        self.last_adherence = None
        self.log("schedule", "已重設今日服藥紀錄")

    def models_info(self) -> dict:
        import json

        out = {}
        for key, name in (("intent", "intent_report.json"), ("vision", "vision_calibration_report.json"), ("risk", "risk_model.json")):
            path = MODELS_DIR / name
            out[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return out

    def set_taken(self, period: str, taken: bool) -> None:
        if taken:
            self._mark(period, "marked_taken", detail={"source": "dashboard"})
        else:
            self.taken_today.discard(period)
            self.store.log_dose(period, "marked_not_taken", detail={"source": "dashboard"})
        self.log("schedule", f"{PERIOD_ZH[period]}標記為{'已服用' if taken else '未服用'}")

    def risk_model_info(self) -> dict | None:
        path = MODELS_DIR / "risk_model.json"
        if path.exists():
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        return None


def wait_until_idle(service: SimulationService, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    time.sleep(0.2)
    while time.time() < deadline:
        if not service.state["busy"] and service._jobs.empty():
            return service.status()
        time.sleep(0.1)
    raise TimeoutError("執行逾時")
