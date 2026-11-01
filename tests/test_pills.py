"""開口藥杯與藥丸的 MuJoCo 物理測試。"""

from carearm.robot.executor import RetrievalExecutor


def test_default_open_cups_contain_planned_pills(env, cfg):
    env.reset(seed=0)
    sim = env.unwrapped
    assert sim.has_pills
    for period in ("morning", "noon", "evening"):
        assert sim.pills_in_box(period) == cfg["pills"]["default_per_box"]


def test_pills_travel_with_cup_then_can_be_taken(env, cfg):
    env.reset(seed=0)
    sim = env.unwrapped
    executor = RetrievalExecutor(env, cfg)
    executor.move_to_observation_pose()
    p = sim.box_position("noon")
    result = executor.execute("noon", (p[0], p[1]))
    assert result.success
    assert sim.pills_in_box("noon") == cfg["pills"]["default_per_box"]
    assert sim.take_pills("noon", 1) == 1
    assert sim.pills_in_box("noon") == cfg["pills"]["default_per_box"] - 1


def test_dropped_pill_is_no_longer_inside_cup(env, cfg):
    env.reset(seed=0)
    sim = env.unwrapped
    assert sim.take_pills("morning", 1, drop_on_table=True) == 1
    assert sim.pills_in_box("morning") == cfg["pills"]["default_per_box"] - 1
