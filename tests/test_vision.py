"""顏色辨識、識別碼讀取、像素↔世界座標（使用實際 MuJoCo 渲染影像）。"""
import numpy as np
import pytest

from carearm.perception.calibration import load_detector
from carearm.perception.detector import PixelToWorld
from carearm.robot.executor import RetrievalExecutor
from carearm.simulation.scene import SceneConditions


@pytest.fixture(scope="module")
def detector(cfg):
    return load_detector(cfg)


def _detect(env, cfg, detector, cond):
    env.reset(seed=0, options={"conditions": cond})
    RetrievalExecutor(env, cfg).move_to_observation_pose()
    sim = env.unwrapped
    return detector.detect(sim.render_overhead(), sim.camera_geometry()), sim


@pytest.mark.parametrize("cond", [
    SceneConditions(),
    SceneConditions(lighting="low"),
    SceneConditions(camera_tilt_deg=12, positions={"morning": (0.46, -0.14, 8), "noon": (0.55, 0.02, -6), "evening": (0.47, 0.15, 3)}),
])
def test_colors_ids_and_localization(env, cfg, detector, cond):
    dets, sim = _detect(env, cfg, detector, cond)
    assert len(dets) == 3
    expected_dots = {"morning": 1, "noon": 2, "evening": 3}
    for d in dets:
        truth = sim.box_position(d.predicted_period)       # ground truth 只用於評估
        assert np.hypot(d.world_xy[0] - truth[0], d.world_xy[1] - truth[1]) < 0.008
        assert d.dot_count == expected_dots[d.predicted_period]
        assert d.calibrated_probs[d.predicted_period] > 0.9


def test_occlusion_breaks_identity_code(env, cfg, detector):
    dets, _ = _detect(env, cfg, detector, SceneConditions(occlusion=0.4, occlusion_target="evening"))
    evening = [d for d in dets if d.predicted_period == "evening"]
    assert evening and evening[0].dot_count != 3 and evening[0].area_ratio < 0.7


def test_pixel_world_roundtrip(env):
    geo = env.unwrapped.camera_geometry()
    proj = PixelToWorld(geo)
    for xy in [(0.5, 0.0), (0.45, -0.15), (0.58, 0.14)]:
        u, v = proj.to_pixel(*xy)
        back = proj.to_world(u, v)
        assert np.allclose(back, xy, atol=1e-6)


def test_detector_does_not_read_simulator_objects():
    import inspect

    from carearm.perception import detector as module

    source = inspect.getsource(module)
    for forbidden in ("box_position", "sensor(", ".data.", "geom_rgba"):
        assert forbidden not in source
