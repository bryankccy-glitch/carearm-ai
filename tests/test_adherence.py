"""服藥依從性驗證：手腕 RGB-D 數藥丸、決策規則、服藥紀錄持久化。"""
from datetime import datetime

import numpy as np
import pytest

from carearm.database.store import Store
from carearm.decision.adherence import REMAINING, TAKEN, UNVERIFIED, AdherenceResult, apply_adherence, verdict_of
from carearm.decision.engine import CONFIRM, EXECUTE, STOP, Decision
from carearm.perception.pills import PillCounter, PillInspection
from carearm.robot.executor import RetrievalExecutor
from carearm.simulation.scene import SceneConditions

POSITIONS = {"morning": (0.46, -0.15, 8), "noon": (0.55, 0.02, -6), "evening": (0.47, 0.155, 3)}


def _pills(n, side=1):
    spots = [(side * 0.0195, -0.008), (-side * 0.0195, 0.008), (side * 0.0195, 0.008), (-side * 0.0195, -0.008)]
    return [{"x": x, "y": y, "r": 0.0038, "color": i} for i, (x, y) in enumerate(spots[:n])]


def _inspect(env, cfg, box):
    sim = env.unwrapped
    xy = sim.box_position(box)[:2]          # 測試只用真值決定相機要移去哪；計數本身只看影像
    RetrievalExecutor(env, cfg).move_wrist_camera_over((float(xy[0]), float(xy[1])))
    return PillCounter(cfg).inspect(sim.render_wrist(), sim.wrist_camera_geometry(), sim.render_wrist_depth(seed=1))


@pytest.mark.parametrize("lighting", ["normal", "low"])
def test_counts_pills_with_wrist_rgbd(env, cfg, lighting):
    pills = {"morning": [], "noon": _pills(1), "evening": _pills(3)}
    env.reset(seed=0, options={"conditions": SceneConditions(positions=POSITIONS, pills=pills, lighting=lighting)})
    sim = env.unwrapped
    for box, expected in (("morning", 0), ("noon", 1), ("evening", 3)):
        res = _inspect(env, cfg, box)
        assert sim.pills_in_box(box) == expected
        assert res.count == expected, (box, res.reasons)
        assert res.empty == (expected == 0) and not res.uncertain
        assert res.dot_count == cfg["medication_boxes"][box]["id_dots"]


def test_eaten_pills_are_noticed(env, cfg):
    env.reset(seed=0, options={"conditions": SceneConditions(positions=POSITIONS, pills={"noon": _pills(2)})})
    assert _inspect(env, cfg, "noon").count == 2
    env.unwrapped.take_pills("noon", 1)
    assert _inspect(env, cfg, "noon").count == 1
    env.unwrapped.take_pills("noon", 1)
    assert _inspect(env, cfg, "noon").empty


def _insp(count, uncertain=False):
    return PillInspection(count=count, empty=count == 0, uncertain=uncertain)


def test_verdict_rules():
    assert verdict_of([_insp(0)]) == (TAKEN, 0)
    assert verdict_of([_insp(2)]) == (REMAINING, 2)
    assert verdict_of([_insp(0, True), _insp(0, True)]) == (UNVERIFIED, 0)
    assert verdict_of([_insp(0, True), _insp(1)]) == (REMAINING, 1)      # 任何一次確定看到藥 → 仍有藥


def _collect():
    return Decision("collect", EXECUTE, "noon", 0, (0.3, -0.2), reasons=["把 MED-NOON-02 移到回收區"], action="collect")


def test_apply_adherence(cfg):
    d = apply_adherence(_collect(), AdherenceResult(TAKEN, "noon", 0, 1), cfg)
    assert d.decision == EXECUTE and d.target_xy is not None
    d = apply_adherence(_collect(), AdherenceResult(REMAINING, "noon", 2, 1), cfg)
    assert d.decision == STOP and d.target_xy is None and not any(r.startswith("把 ") for r in d.reasons)
    d = apply_adherence(_collect(), AdherenceResult(UNVERIFIED, "noon", 0, 2), cfg)
    assert d.decision == CONFIRM and [r.rule for r in d.rules if not r.passed] == ["adherence_verified"]


def test_dose_state_persists(tmp_path):
    path = tmp_path / "dose.sqlite"
    s = Store(path)
    s.log_dose("morning", "dispensed")
    s.log_dose("morning", "pills_remaining", 2)
    s.log_dose("noon", "marked_taken")
    s.log_dose("noon", "marked_not_taken")
    again = Store(path).dose_state()                       # 模擬服務重啟
    # 送達 ≠ 服用：送達後算入重複取藥限制（retrieved），但要驗證或確認才算已服用（taken）
    assert again["morning"]["retrieved"] and not again["morning"]["taken"]
    assert again["morning"]["claims_with_pills"] == 1
    assert again["noon"]["retrieved"] and not again["noon"]["taken"]
    s.log_dose(None, "day_reset")
    assert Store(path).dose_state() == {}
    s.log_dose("evening", "dispensed", when=datetime(2020, 1, 1, 18, 0))
    assert "evening" not in Store(path).dose_state()        # 其他日期不算今天
