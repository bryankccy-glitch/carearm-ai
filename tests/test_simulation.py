"""模擬重置、夾取成功偵測、錯盒偵測（真實物理）。"""
import numpy as np

from carearm.robot.executor import RetrievalExecutor
from carearm.settings import PERIODS
from carearm.simulation.scene import SceneConditions


def test_reset_restores_default_positions(env, cfg):
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    p = env.unwrapped.box_position("noon")
    ex.execute("noon", (p[0], p[1]))
    env.reset(seed=0)
    for period in PERIODS:
        assert np.allclose(env.unwrapped.box_position(period)[:2], cfg["medication_boxes"][period]["default_position"][:2], atol=1e-6)


def test_successful_pick_and_place_verified_physically(env, cfg):
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    p = env.unwrapped.box_position("evening")
    r = ex.execute("evening", (p[0], p[1]))
    assert r.success and r.grasp_success and r.lifted_box == "evening" and r.delivered_box == "evening"
    final = env.unwrapped.box_position("evening")
    center = np.asarray(cfg["delivery_area"]["center"])
    assert np.all(np.abs(final[:2] - center[:2]) <= np.asarray(cfg["delivery_area"]["half_size"]))


def test_grasp_failure_detected_when_target_is_empty(env, cfg):
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    r = ex.execute("noon", (0.40, 0.06))      # 空桌面：到達位姿但沒有夾到任何東西
    assert not r.grasp_success and not r.success and r.failure_stage == "grasp"


def test_wrong_box_is_not_counted_as_success(env, cfg):
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    p = env.unwrapped.box_position("morning")
    r = ex.execute("noon", (p[0], p[1]))       # 要中午，卻夾了早上的位置
    assert r.delivered_box == "morning" and not r.success and r.failure_stage == "wrong_box"


def test_stop_flag_interrupts_motion(env, cfg):
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.control.stop_event.set()
    p = env.unwrapped.box_position("noon")
    r = ex.execute("noon", (p[0], p[1]))
    assert r.failure_stage == "stopped" and not r.success


def test_finish_moves_box_from_delivery_to_collection(env, cfg):
    """吃完藥後：從遞送區夾起空盒放到回收區（物理驗證），且不撞動回收區內既有的盒子。"""
    from carearm.decision.engine import EXECUTE, collect_decision
    from carearm.perception.calibration import load_detector

    detector = load_detector(cfg)
    env.reset(seed=0)
    sim = env.unwrapped
    ex = RetrievalExecutor(env, cfg)
    area = cfg["collection_area"]
    for slot, period in enumerate(("noon", "evening")):
        ex.move_to_observation_pose()
        p = sim.box_position(period)
        assert ex.execute(period, (p[0], p[1])).success
        ex.move_to_observation_pose()
        delivered = [d for d in detector.detect_all(sim.render_overhead(), sim.camera_geometry()) if d.zone == "delivery"]
        decision = collect_decision(delivered, cfg)
        assert decision.decision == EXECUTE and decision.target_period == period
        place = (area["center"][0] + area["slot_offsets_x"][slot], area["center"][1])
        r = ex.execute(period, decision.target_xy, place_area="collection", place_xy=place)
        assert r.success and r.delivered_box == period and not r.other_boxes_disturbed
        assert np.allclose(sim.box_position(period)[:2], place, atol=0.02)


def test_second_retrieval_succeeds_while_first_box_still_delivered(env, cfg):
    """遞送區已有一盒時再取另一盒，成功判定只看「新進入」遞送區的盒子。"""
    env.reset(seed=0)
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    area = cfg["delivery_area"]
    for slot, period in enumerate(("morning", "evening")):
        p = env.unwrapped.box_position(period)
        place = (area["center"][0] + area["slot_offsets_x"][slot], area["center"][1])
        r = ex.execute(period, (p[0], p[1]), place_xy=place)
        assert r.success and r.delivered_box == period and not r.other_boxes_disturbed
