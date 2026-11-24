"""手腕角度、真正接觸動力學與渲染影像角度驗證。"""
from copy import deepcopy

import cv2
import numpy as np
import pytest

from carearm.perception.detector import LabelDetector, PixelToWorld
from carearm.robot.executor import RetrievalExecutor
from carearm.simulation.scene import SceneConditions, make_carearm_env

YAWS = [-40, -25, 0, 25, 40]


@pytest.fixture(scope="module")
def physics_env(cfg):
    # 只停用這組物理測試的 OpenGL 初始化；模型、控制器、接觸、時間積分均為真正 MuJoCo。
    # 影像端到端測試仍使用原本 env fixture，不能用此替身宣稱已驗證渲染。
    import mujoco
    from unittest.mock import patch

    class NoGraphics:
        def __init__(self, *args, **kwargs):
            pass

        def render(self):
            return None

        def close(self):
            pass

    with patch.object(mujoco, "Renderer", NoGraphics):
        e = make_carearm_env(cfg)
    yield e
    e.close()


def _positions(cfg, yaw):
    return {p: (*spec["default_position"][:2], yaw if p == "noon" else 0)
            for p, spec in cfg["medication_boxes"].items()}


@pytest.mark.parametrize("yaw", YAWS)
def test_aligned_grasp_physics(physics_env, cfg, yaw):
    physics_env.reset(seed=0, options={"conditions": SceneConditions(positions=_positions(cfg, yaw))})
    ex = RetrievalExecutor(physics_env, cfg)
    ex.move_to_observation_pose()
    xy = cfg["medication_boxes"]["noon"]["default_position"][:2]
    result = ex.execute("noon", xy, yaw_deg=yaw)
    print(f"對齊 yaw={yaw}: {result.to_dict()}")
    assert result.success, result.to_dict()


@pytest.mark.parametrize("yaw", [-40, 40])
def test_unaligned_grasp_comparison(physics_env, cfg, yaw):
    comparison = deepcopy(cfg)
    comparison["grasp"]["align_yaw"] = False
    physics_env.reset(seed=0, options={"conditions": SceneConditions(positions=_positions(cfg, yaw))})
    ex = RetrievalExecutor(physics_env, comparison)
    ex.move_to_observation_pose()
    result = ex.execute("noon", cfg["medication_boxes"]["noon"]["default_position"][:2], yaw_deg=yaw)
    # 比較基線允許成功，必須如實輸出，不能為了製造差異修改物理判定。
    print(f"固定腕姿 yaw={yaw}: success={result.success}, grasp={result.grasp_success}, error={result.error}")
    assert result.sim_steps > 0


@pytest.mark.parametrize("yaw", YAWS)
def test_rendered_yaw_and_automatic_grasp(env, cfg, yaw):
    env.reset(seed=0, options={"conditions": SceneConditions(positions=_positions(cfg, yaw))})
    ex = RetrievalExecutor(env, cfg)
    ex.move_to_observation_pose()
    sim = env.unwrapped
    dets = LabelDetector(cfg).detect(sim.render_overhead(), sim.camera_geometry())
    d = next(d for d in dets if d.predicted_period == "noon")
    error = abs((d.yaw_deg - yaw + 45) % 90 - 45)
    print(f"渲染 yaw={yaw}: estimate={d.yaw_deg:.6f}, error={error:.6f}")
    assert error < 5
    # 不傳 yaw，驗證現有 API／demo 僅傳 XY 的呼叫端也會自動對齊。
    result = ex.execute("noon", d.world_xy)
    assert result.success, result.to_dict()


@pytest.mark.parametrize("tilt", [0, 12])
@pytest.mark.parametrize("yaw", YAWS)
def test_synthetic_projected_yaw(cfg, yaw, tilt):
    from carearm.simulation.scene import camera_pose_for_tilt

    pos, rot = camera_pose_for_tilt(cfg, tilt)
    geo = dict(pos=pos, rot=rot, width=640, height=640, fovy=52)
    detector = LabelDetector(cfg)
    proj = PixelToWorld(geo, detector.plane_z)
    image = np.zeros((640, 640, 3), np.uint8)
    angle = np.deg2rad(yaw)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    hx, hy = detector.label_half
    corners = [np.array([.5, 0]) + rotation @ [sx * hx, sy * hy] for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]]
    poly = np.array([proj.to_pixel(*p) for p in corners]).round().astype(np.int32)
    cv2.fillConvexPoly(image, poly, (40, 100, 240))
    d, = detector.detect(image, geo)
    assert abs((d.yaw_deg - yaw + 45) % 90 - 45) < 5
