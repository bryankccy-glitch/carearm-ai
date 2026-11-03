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


def test_stress_profile_only_changes_the_condition_rates(cfg):
    import copy
    from carearm.experiments.scenarios import generate_suite
    stress = copy.deepcopy(cfg)
    stress["experiments"]["profile"] = "stress"
    a, b = generate_suite(400, 20261019, cfg), generate_suite(400, 20261019, stress)
    share = lambda suite, tag: sum(tag in s.tags for s in suite) / len(suite)
    assert share(b, "occlusion") > share(a, "occlusion") + 0.1
    assert share(b, "duplicate_box") > share(a, "duplicate_box") + 0.04
    assert [s.split for s in a] == [s.split for s in b]   # same split, so train/cal/test never mix


def test_unknown_profile_is_rejected(cfg):
    import copy
    import pytest
    from carearm.experiments.scenarios import generate_suite
    bad = copy.deepcopy(cfg)
    bad["experiments"]["profile"] = "typo"
    with pytest.raises(ValueError):
        generate_suite(3, 1, bad)
