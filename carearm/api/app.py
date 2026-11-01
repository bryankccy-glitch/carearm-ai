"""FastAPI 後端 + 靜態儀表板。

啟動：
    python -m carearm.api.app                 # 只有網頁儀表板（相機影像 + 遙測）
    mjpython -m carearm.api.app --viewer      # macOS：同時開 MuJoCo 原生 3D 視窗
"""

from __future__ import annotations

import argparse
import json
import tempfile
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from carearm.decision.schedule import to_minutes

from carearm.settings import PERIOD_ZH, PERIODS, PROJECT_ROOT, RESULTS_DIR

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class CommandBody(BaseModel):
    text: str
    time: str | None = None
    source: str = "text"                 # text / browser-asr
    asr_engine: str | None = None
    asr_confidence: float | None = None  # 瀏覽器回報的原始值，只記錄，不當作校正機率


class PeriodBody(BaseModel):
    period: str
    time: str | None = None


class Lighting(str, Enum):
    normal = "normal"
    low = "low"
    high = "high"


class Period(str, Enum):
    morning = "morning"
    noon = "noon"
    evening = "evening"


MAX_AUDIO_BYTES = 10 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": ".webm", "audio/mp4": ".m4a", "audio/ogg": ".ogg",
               "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3"}


class ConditionBody(BaseModel):
    lighting: Lighting = Lighting.normal
    shadow: bool = False
    occlusion: float = Field(0.0, ge=0, le=1, allow_inf_nan=False)
    occlusion_target: Period | None = None
    camera_tilt_deg: float = Field(0.0, ge=-15, le=15, allow_inf_nan=False)
    similar_color: bool = False
    randomize: bool = False
    seed: int | None = Field(None, ge=0, le=2**32 - 1)


class ExecuteBody(BaseModel):
    caregiver_confirmed: bool = False


class ClientLogBody(BaseModel):
    level: str = "INFO"
    message: str
    detail: dict | None = None


class TakenBody(BaseModel):
    period: str
    taken: bool = True


class ComplianceBody(BaseModel):
    period: str | None = None


class IntakeBody(BaseModel):
    period: str | None = None
    count: int = 99
    drop_on_table: bool = False


def create_app(service=None, *, viewer: bool = False, realtime: bool = True) -> FastAPI:
    if service is None:
        from carearm.api.service import SimulationService

        service = SimulationService(viewer=viewer, realtime=realtime)
    svc = service
    app = FastAPI(title="CareArm AI 研究模擬 API")
    app.state.service = svc
    from carearm.speech.asr import WhisperASR

    WhisperASR.warmup_async()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path == "/api/conditions":
            return JSONResponse(status_code=422, content={"detail": "實驗條件格式或數值範圍錯誤，請檢查後重試"})
        return await request_validation_exception_handler(request, error)

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/health")
    def health():
        return {"ok": True, "disclaimer": "研究模擬，非醫療器材，不做任何真實藥物或劑量決策。"}

    @app.get("/api/status")
    def status():
        from carearm.speech.asr import WhisperASR

        return {**svc.status(), "asr": dict(WhisperASR.status)}

    @app.post("/api/reset")
    def reset():
        return guard(svc.reset)

    @app.post("/api/conditions")
    def conditions(body: ConditionBody):
        visual = body.model_dump(mode="json", exclude={"randomize", "seed"})
        if body.randomize:
            return guard(svc.randomize, body.seed, **visual)
        return guard(svc.reset, visual)

    @app.post("/api/randomize")
    def randomize():
        return guard(svc.randomize)

    @app.post("/api/command")
    def command(body: CommandBody):
        if body.time is not None:
            guard(to_minutes, body.time)
        if not body.text.strip():
            raise HTTPException(400, "指令不可為空")
        asr = None
        if body.source == "browser-asr":
            asr = {"engine": body.asr_engine or "瀏覽器語音辨識", "raw_confidence": body.asr_confidence}
        return guard(svc.analyze, body.text.strip(), body.time, body.source, asr)

    @app.post("/api/select_period")
    def select_period(body: PeriodBody):
        if body.time is not None:
            guard(to_minutes, body.time)
        if body.period not in PERIODS:
            raise HTTPException(400, "period 必須是 morning / noon / evening")
        return guard(svc.analyze, f"幫我拿{PERIOD_ZH[body.period]}的藥", body.time, "manual")

    @app.post("/api/audio")
    async def audio(file: UploadFile = File(...), time: str | None = Form(None)):
        from carearm.speech.asr import ASRUnavailable, WhisperASR

        path = None
        try:
            if time is not None:
                guard(to_minutes, time)
            content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
            suffix = AUDIO_TYPES.get(content_type)
            if suffix is None:
                raise HTTPException(415, "不支援的音訊格式")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                path = tmp.name
                size = 0
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "音訊檔案不可超過 10 MB")
                    tmp.write(chunk)

            def process():
                try:
                    tr = WhisperASR().transcribe(path)
                except ASRUnavailable as error:
                    svc.log("speech", f"Whisper 無法使用：{error}", "ERROR")
                    raise HTTPException(501, str(error)) from error
                except Exception as error:
                    svc.log("speech", f"Whisper 辨識失敗：{error}", "ERROR")
                    raise HTTPException(500, f"Whisper 辨識失敗：{error}") from error
                if not tr.text:
                    raise HTTPException(422, "沒有辨識到語音內容，請靠近麥克風再說一次")
                return guard(svc.analyze, tr.text, time, "audio", {
                    "engine": tr.engine, "language": tr.language, "avg_logprob_raw": tr.avg_logprob})

            return await run_in_threadpool(process)
        finally:
            if path is not None:
                Path(path).unlink(missing_ok=True)
            await file.close()

    @app.post("/api/execute")
    def execute(body: ExecuteBody):
        return guard(svc.execute, body.caregiver_confirmed)

    @app.post("/api/stop")
    def stop():
        svc.stop()
        return {"stopped": True}

    @app.post("/api/pause")
    def pause():
        svc.pause(True)
        return {"paused": True}

    @app.post("/api/resume")
    def resume():
        svc.pause(False)
        return {"paused": False}

    @app.get("/api/alerts")
    def alerts():
        items = svc.alerts.list()
        return {"alerts": items, "open": sum(1 for a in items if a["status"] == "open"),
                "urgent_open": sum(1 for a in items if a["status"] == "open" and a["level"] == "URGENT"),
                "webhook": "內建模擬總部" if svc.alerts.webhook_url == "local" else ("未設定" if not svc.alerts.webhook_url else "外部 webhook"),
                "headquarters": svc.alerts.hq_name}

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int):
        svc.alerts.acknowledge(alert_id)
        svc.log("alert", f"通報 #{alert_id} 已由照護者確認處理")
        return {"ok": True}

    @app.get("/api/hq/inbox")
    def hq_inbox():
        return svc.alerts.hq_inbox()

    @app.get("/api/compliance")
    def compliance():
        return {"sessions": svc.compliance.info(), "last_delivered_period": svc._last_delivered_period}

    @app.post("/api/compliance/inspect")
    def inspect_compliance(body: ComplianceBody):
        return guard(svc.inspect_compliance, body.period)

    @app.post("/api/simulation/intake")
    def simulation_intake(body: IntakeBody):
        """研究模擬專用：模擬取走／掉落藥丸，再由手腕相機判讀。"""

        return guard(svc.simulate_intake, body.period, body.count, body.drop_on_table)

    @app.post("/api/client_log")
    def client_log(body: ClientLogBody):
        """瀏覽器端（語音辨識、麥克風）回報的診斷訊息，寫入事件紀錄方便遠端除錯。"""

        level = body.level.upper() if body.level.upper() in ("INFO", "WARN", "ERROR") else "INFO"
        svc.log("client", body.message[:300], level, body.detail or {})
        return {"ok": True}

    @app.post("/api/day/reset")
    def day_reset():
        svc.reset_day()
        return {"taken_today": []}

    @app.get("/api/models")
    def models():
        return svc.models_info()

    @app.get("/api/events.csv")
    def events_csv():
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["時間", "來源", "等級", "訊息"])
        for e in reversed(svc.store.recent_events(5000)):
            writer.writerow([e["ts"], e["source"], e["level"], e["message"]])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=carearm_events.csv"})

    @app.post("/api/taken")
    def taken(body: TakenBody):
        if body.period not in PERIODS:
            raise HTTPException(400, "period 錯誤")
        svc.set_taken(body.period, body.taken)
        return {"taken_today": sorted(svc.taken_today)}

    @app.get("/api/inspection.png")
    def inspection_png():
        return Response(svc.inspection_png(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/doses")
    def doses(limit: int = 200):
        """服藥紀錄（SQLite，跨重啟保留）與今日各時段狀態。"""

        return {"today": svc.store.dose_state(), "history": svc.store.dose_rows(limit=limit)}

    @app.get("/api/analysis")
    def analysis():
        return svc.last_analysis or {}

    @app.get("/api/camera.png")
    def camera(annotated: bool = True, live: bool = False, view: str = "front"):
        if live:
            return Response(svc.live_png(view), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return Response(svc.camera_png(annotated), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    def events(limit: int = 200):
        return svc.store.recent_events(limit)

    @app.get("/api/experiments")
    def experiments():
        runs = []
        if RESULTS_DIR.exists():
            for run_dir in sorted(RESULTS_DIR.iterdir()):
                summary = run_dir / "summary.json"
                if summary.exists():
                    data = json.loads(summary.read_text(encoding="utf-8"))
                    meta = run_dir / "run_meta.json"
                    runs.append({
                        "run_id": run_dir.name,
                        "meta": json.loads(meta.read_text()) if meta.exists() else {},
                        "metrics": data["metrics"],
                        "risk_ece_test": data.get("risk_ece_test"),
                        "risk_coverage": data.get("risk_coverage"),
                        "intent_accuracy_test": data.get("intent_accuracy_test"),
                        "figures": sorted(p.name for p in (run_dir / "figures").glob("*.png")),
                    })
        return {"runs": runs, "risk_model": svc.risk_model_info()}

    @app.get("/api/experiments/{run_id}/figures/{name}")
    def figure(run_id: str, name: str):
        root = RESULTS_DIR.resolve()
        run = root / run_id
        figures = run / "figures"
        path = figures / name
        if (run_id in (".", "..") or Path(run_id).name != run_id
                or Path(name).name != name or not name.endswith(".png")
                or run.is_symlink() or figures.is_symlink() or path.is_symlink()
                or run.resolve().parent != root
                or path.resolve().parent != figures.resolve() or not path.is_file()):
            raise HTTPException(404, "找不到圖表")
        return FileResponse(path, media_type="image/png")

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/")
        def no_frontend():
            return JSONResponse({"message": "前端尚未建置：cd frontend && npm install && npm run build"})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="CareArm AI 儀表板後端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--viewer", action="store_true", help="開啟 MuJoCo 原生視窗（macOS 請用 mjpython）")
    parser.add_argument("--fast", action="store_true", help="不以實時速度執行手臂（測試用）")
    args = parser.parse_args()
    import uvicorn

    app = create_app(viewer=args.viewer, realtime=not args.fast)
    print(f"儀表板：http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
