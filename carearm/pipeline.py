"""感知 → 決策 → 執行 的共用管線（實驗與儀表板共用同一套程式）。"""

from __future__ import annotations

from dataclasses import asdict

from carearm.decision.engine import Observation
from carearm.decision.schedule import MedicationSchedule, ScheduleCheck
from carearm.perception.detector import LabelDetection, LabelDetector
from carearm.robot.executor import RetrievalExecutor
from carearm.speech.intent import IntentClassifier, IntentResult


def perceive(env, detector: LabelDetector, *, executor: RetrievalExecutor | None = None):
    """手臂移到觀測姿態 → 渲染俯視影像 → OpenCV 偵測。只用影像與相機幾何。"""

    sim = env.unwrapped
    (executor or RetrievalExecutor(env, sim.cfg)).move_to_observation_pose()
    image = sim.render_overhead()
    detections = detector.detect(image, sim.camera_geometry())
    return image, detections


def interpret(text: str, minute: int, taken_today, clf: IntentClassifier, cfg: dict) -> tuple[IntentResult, ScheduleCheck]:
    intent = clf.predict(text)
    schedule = MedicationSchedule.from_config(cfg, taken_today)
    return intent, schedule.check(minute, intent.period)


def observation_to_dict(obs: Observation) -> dict:
    return {
        "intent": obs.intent.to_dict(),
        "schedule": obs.schedule.to_dict(),
        "detections": [d.to_dict() for d in obs.detections],
    }


def observation_from_dict(data: dict) -> Observation:
    dets = []
    for d in data["detections"]:
        d = dict(d)
        d["bbox"] = tuple(d["bbox"])
        d["center_px"] = tuple(d["center_px"])
        d["world_xy"] = tuple(d["world_xy"])
        dets.append(LabelDetection(**d))
    return Observation(
        intent=IntentResult(**data["intent"]),
        schedule=ScheduleCheck(**data["schedule"]),
        detections=dets,
    )


__all__ = ["perceive", "interpret", "observation_to_dict", "observation_from_dict", "asdict"]
