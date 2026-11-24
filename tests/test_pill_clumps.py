"""藥丸計數：大顆膠囊算一顆；擠在一起時要標記不確定，不能很有把握地回報錯的數字。"""
from carearm.perception.pills import PillCounter
from carearm.robot.executor import RetrievalExecutor
from carearm.simulation.scene import SceneConditions

POSITIONS = {"morning": (0.46, -0.15, 8), "noon": (0.55, 0.02, -6), "evening": (0.47, 0.155, 3)}


def _inspect(env, cfg, box):
    sim = env.unwrapped
    xy = sim.box_position(box)[:2]
    RetrievalExecutor(env, cfg).move_wrist_camera_over((float(xy[0]), float(xy[1])))
    return PillCounter(cfg).inspect(sim.render_wrist(), sim.wrist_camera_geometry(), sim.render_wrist_depth(seed=1))


def test_large_capsule_counts_as_one(env, cfg):
    # 第 0 顆是圓錠（放左格）、第 1 顆是最大的膠囊（放右格），兩顆分開
    pills = {"noon": [{"x": -0.0195, "y": 0.0, "r": 0.0032, "color": 0}, {"x": 0.0195, "y": 0.0, "r": 0.0048, "color": 1}]}
    env.reset(seed=0, options={"conditions": SceneConditions(positions=POSITIONS, pills=pills)})
    assert env.unwrapped.pills_in_box("noon") == 2
    res = _inspect(env, cfg, "noon")
    assert res.count == 2 and not res.uncertain, res.reasons


def test_clumped_pills_are_flagged_uncertain(env, cfg):
    # 五顆擠在同一格：數不清時必須標記不確定，而不是很有把握地回報錯的數字
    spots = [(0.016, -0.006), (0.024, -0.006), (0.016, 0.003), (0.024, 0.003), (0.020, 0.012)]
    pills = {"evening": [{"x": x, "y": y, "r": 0.0040, "color": i} for i, (x, y) in enumerate(spots)]}
    env.reset(seed=0, options={"conditions": SceneConditions(positions=POSITIONS, pills=pills)})
    truth = env.unwrapped.pills_in_box("evening")
    res = _inspect(env, cfg, "evening")
    assert res.count == truth or res.uncertain, (truth, res.count, res.reasons)
    assert not res.empty
