"""情境可重現、資料切分不重疊、物理模擬確定性。"""
from carearm.experiments.scenarios import generate_suite
from carearm.robot.executor import RetrievalExecutor


def test_scenarios_reproducible(cfg):
    a = [s.to_dict() for s in generate_suite(60, 123, cfg)]
    b = [s.to_dict() for s in generate_suite(60, 123, cfg)]
    assert a == b
    c = [s.to_dict() for s in generate_suite(60, 124, cfg)]
    assert a != c


def test_splits_disjoint(cfg):
    suite = generate_suite(300, 1, cfg)
    ids = {sp: {s.scenario_id for s in suite if s.split == sp} for sp in ("train", "calibration", "test")}
    assert not (ids["train"] & ids["test"]) and not (ids["calibration"] & ids["test"]) and not (ids["train"] & ids["calibration"])
    assert sum(len(v) for v in ids.values()) == 300


def test_physics_is_deterministic(env, cfg):
    sc = generate_suite(5, 77, cfg)[3]
    finals = []
    for _ in range(2):
        env.reset(seed=sc.seed, options={"conditions": sc.scene_conditions()})
        ex = RetrievalExecutor(env, cfg)
        ex.move_to_observation_pose()
        p = env.unwrapped.box_position("noon")
        r = ex.execute("noon", (round(p[0], 4), round(p[1], 4)))
        finals.append((r.success, r.sim_steps, tuple(env.unwrapped.box_position("noon").round(6))))
    assert finals[0] == finals[1]
