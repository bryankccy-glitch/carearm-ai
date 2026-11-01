"""可重現的實驗情境產生器。

每個情境由 (base_seed + index) 決定；同一 seed 永遠產生同一情境。
資料切分以情境 index 決定（訓練 / 校正 / 測試互不重疊），測試標籤不參與任何訓練或門檻選擇。
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field

from carearm.decision.schedule import MedicationSchedule, fmt_minutes, to_minutes
from carearm.settings import PERIODS, load_config
from carearm.simulation.scene import SceneConditions
from carearm.speech.intent import INTENT_TO_PERIOD, render_command, simulate_asr

INTENT_WEIGHTS = {
    "SPECIFY_MORNING": 0.15,
    "SPECIFY_NOON": 0.15,
    "SPECIFY_EVENING": 0.15,
    "REQUEST_MEDICATION": 0.30,
    "MEDICATION_ALREADY_TAKEN": 0.10,
    "CANCEL": 0.07,
    "UNKNOWN": 0.08,
}


@dataclass
class Scenario:
    scenario_id: int
    seed: int
    split: str
    true_intent: str
    command_text: str              # 使用者實際說的話（乾淨）
    audio_noise: float
    transcript: str                # 模擬 ASR 輸出（含雜訊）
    time_minutes: int
    taken_today: list[str]
    duplicate_condition: bool
    schedule_conflict: bool        # 真實意圖與排程衝突
    conditions: dict               # SceneConditions.to_dict()
    expected_period: str | None    # 正確應取的時段（若應自動執行）
    should_execute: bool           # 依真實意圖 + 排程 + 場景，正確答案是否為自動取藥
    tags: list[str] = field(default_factory=list)

    @property
    def time_str(self) -> str:
        return fmt_minutes(self.time_minutes)

    def scene_conditions(self) -> SceneConditions:
        c = self.conditions
        return SceneConditions(
            positions={k: tuple(v) for k, v in c["positions"].items()},
            lighting=c["lighting"],
            shadow=c["shadow"],
            occlusion=c["occlusion"],
            occlusion_target=c["occlusion_target"],
            camera_tilt_deg=c["camera_tilt_deg"],
            similar_color=c["similar_color"],
            extra_box=c["extra_box"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _weighted(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def sample_positions(rng: random.Random, cfg: dict, randomize: bool) -> dict[str, tuple[float, float, float]]:
    if not randomize:
        return {p: (*cfg["medication_boxes"][p]["default_position"][:2], 0.0) for p in PERIODS}
    r = cfg["randomization"]
    for _ in range(500):
        pos = {}
        for p in PERIODS:
            pos[p] = (
                round(rng.uniform(*r["x_range"]), 4),
                round(rng.uniform(*r["y_range"]), 4),
                round(rng.uniform(-r["yaw_deg"], r["yaw_deg"]), 2),
            )
        pts = list(pos.values())
        ok = all(
            ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= r["min_separation"]
            for i, a in enumerate(pts) for b in pts[i + 1:]
        )
        if ok:
            return pos
    raise RuntimeError("無法產生不重疊的藥盒位置")


def _free_spot(rng: random.Random, cfg: dict, positions: dict) -> tuple[float, float, float] | None:
    r = cfg["randomization"]
    for _ in range(300):
        cand = (round(rng.uniform(*r["x_range"]), 4), round(rng.uniform(*r["y_range"]), 4), round(rng.uniform(-8, 8), 2))
        if all(((cand[0] - v[0]) ** 2 + (cand[1] - v[1]) ** 2) ** 0.5 >= r["min_separation"] for v in positions.values()):
            return cand
    return None


def sample_scene_conditions(rng: random.Random, cfg: dict, focus_period: str | None) -> tuple[SceneConditions, list[str]]:
    tags: list[str] = []
    randomize = rng.random() < 0.7
    positions = sample_positions(rng, cfg, randomize)
    if randomize:
        tags.append("random_positions")
    lighting = _weighted(rng, {"normal": 0.6, "low": 0.2, "high": 0.2})
    if lighting != "normal":
        tags.append(f"light_{lighting}")
    shadow = rng.random() < 0.2
    if shadow:
        tags.append("shadow")
    tilt = _weighted(rng, {0.0: 0.6, 8.0: 0.2, 15.0: 0.2})
    if tilt:
        tags.append(f"camera_tilt_{int(tilt)}")
    similar = rng.random() < 0.2
    if similar:
        tags.append("similar_color")
    occlusion, occ_target = 0.0, None
    if rng.random() < 0.3:
        occlusion = round(rng.uniform(0.2, 0.75), 2)
        occ_target = focus_period if (focus_period and rng.random() < 0.6) else rng.choice(PERIODS)
        tags.append("occlusion")
    extra = None
    if rng.random() < 0.06:
        spot = _free_spot(rng, cfg, positions)
        if spot is not None:
            copy_of = focus_period if (focus_period and rng.random() < 0.6) else rng.choice(PERIODS)
            extra = {"copy_of": copy_of, "position": list(spot)}
            tags.append("duplicate_box")
    cond = SceneConditions(
        positions=positions,
        lighting=lighting,
        shadow=shadow,
        occlusion=occlusion,
        occlusion_target=occ_target,
        camera_tilt_deg=tilt,
        similar_color=similar,
        extra_box=extra,
    )
    return cond, tags


def _sample_time(rng: random.Random, cfg: dict, period: str | None, *, conflict: bool) -> int:
    windows = {p: (to_minutes(cfg["schedule"][p][0]), to_minutes(cfg["schedule"][p][1])) for p in PERIODS}
    if period and conflict:
        other = rng.choice([p for p in PERIODS if p != period])
        s, e = windows[other]
        return rng.randrange(s, e)
    roll = rng.random()
    if period and roll < 0.7:
        s, e = windows[period]
        return rng.randrange(s, e)
    if roll < 0.85:
        boundary = rng.choice([m for w in windows.values() for m in w])
        return max(0, min(24 * 60 - 1, boundary + rng.randrange(-45, 75)))
    return rng.randrange(5 * 60, 23 * 60)


def split_for(index: int, n: int, split_cfg: dict) -> str:
    train_end = int(round(n * split_cfg["train"]))
    cal_end = train_end + int(round(n * split_cfg["calibration"]))
    if index < train_end:
        return "train"
    if index < cal_end:
        return "calibration"
    return "test"


def generate_scenario(index: int, n: int, base_seed: int, cfg: dict | None = None) -> Scenario:
    cfg = cfg or load_config()
    seed = base_seed + index
    rng = random.Random(seed)
    intent = _weighted(rng, INTENT_WEIGHTS)
    requested = INTENT_TO_PERIOD.get(intent)
    conflict_flag = requested is not None and rng.random() < 0.2
    minute = _sample_time(rng, cfg, requested or rng.choice(PERIODS), conflict=conflict_flag)
    schedule = MedicationSchedule.from_config(cfg)
    check = schedule.check(minute, requested)
    target = check.target

    duplicate = False
    taken: list[str] = []
    if target and rng.random() < 0.15:
        taken.append(target)
        duplicate = True
    for p in PERIODS:   # 其他早於現在的時段，隨機標為已服用（背景狀態）
        if p != target and p not in taken and to_minutes(cfg["schedule"][p][1]) <= minute and rng.random() < 0.5:
            taken.append(p)

    focus = target or requested
    cond, tags = sample_scene_conditions(rng, cfg, focus)
    noise = _weighted(rng, {0.0: 0.4, 0.1: 0.2, 0.2: 0.2, 0.3: 0.2})
    clean = render_command(intent, rng, allow_held_out=True)
    transcript = simulate_asr(clean, noise, rng)
    if noise:
        tags.append(f"noise_{noise}")
    if duplicate:
        tags.append("duplicate_retrieval")
    if check.conflict:
        tags.append("schedule_conflict")
    if check.needs_clarification and intent == "REQUEST_MEDICATION":
        tags.append("ambiguous_period")

    should = (
        intent in ("REQUEST_MEDICATION", "SPECIFY_MORNING", "SPECIFY_NOON", "SPECIFY_EVENING")
        and target is not None
        and not duplicate
        and not (cond.extra_box and cond.extra_box["copy_of"] == target)
    )
    return Scenario(
        scenario_id=index,
        seed=seed,
        split=split_for(index, n, cfg["experiments"]["split"]),
        true_intent=intent,
        command_text=clean,
        audio_noise=noise,
        transcript=transcript,
        time_minutes=minute,
        taken_today=sorted(taken),
        duplicate_condition=duplicate,
        schedule_conflict=bool(check.conflict),
        conditions=cond.to_dict(),
        expected_period=target if should else None,
        should_execute=bool(should),
        tags=tags,
    )


def generate_suite(n: int | None = None, base_seed: int | None = None, cfg: dict | None = None) -> list[Scenario]:
    cfg = cfg or load_config()
    n = n or int(cfg["experiments"]["n_scenarios"])
    base_seed = base_seed if base_seed is not None else int(cfg["experiments"]["base_seed"])
    return [generate_scenario(i, n, base_seed, cfg) for i in range(n)]


if __name__ == "__main__":
    suite = generate_suite()
    from collections import Counter

    print(Counter(s.split for s in suite))
    print(Counter(s.true_intent for s in suite))
    print("should_execute", Counter(s.should_execute for s in suite))
    print(Counter(t for s in suite for t in s.tags))
    print(json.dumps(suite[0].to_dict(), ensure_ascii=False)[:600])
