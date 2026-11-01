"""CareArm MuJoCo 場景：沿用 gym-hil Franka Panda + Robotiq 2F-85，加入三個藥盒。

設計原則（與上層 shape_sorting_env.py 相同）：
- 以 gym-hil 官方 scene.xml 為基礎，執行期用 MjSpec 增減物件，不改動機器人模型。
- 每個藥盒是獨立 free-joint 剛體，具碰撞與質量；標籤與識別黑點是貼在盒頂的
  純視覺 geom（不參與碰撞）。
- 場景 sensor（藥盒位置）屬於模擬 ground truth，只能用於「評估與成功驗證」，
  視覺推論不得讀取。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from carearm.settings import PERIODS, box_specs, ensure_legacy_on_path, load_config

ensure_legacy_on_path()

import gym_hil  # noqa: E402
from config import CARTESIAN_BOUNDS, PANDA_HOME_JOINTS  # noqa: E402  上層已驗證的設定
from gym_hil.mujoco_gym_env import FrankaGymEnv, GymRenderingSpec  # noqa: E402

HIDDEN_POSITION = (1.2, 1.2, 0.02)
EXTRA_BOX = "extra"          # 用於「多個無法區分的目標盒」測試的第四個盒子
LIGHTING_SCALES = {"normal": 0.55, "low": 0.22, "high": 1.0}
TABLE_TOP_Z = 0.001          # 桌面頂面略高於地板，避免與地板平面重疊閃爍


@dataclass
class SceneConditions:
    """單一實驗情境的場景條件（不含語音）。"""

    positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # period -> (x, y, yaw_deg)
    lighting: str = "normal"            # normal / low / high
    shadow: bool = False
    occlusion: float = 0.0              # 0~1，遮住目標盒（occlusion_target）的比例
    occlusion_target: str | None = None
    camera_tilt_deg: float = 0.0        # 相機繞世界 X 軸傾斜角
    similar_color: bool = False         # 中午盒改成藍紫色
    extra_box: dict | None = None       # {"copy_of": period, "position": (x, y, yaw)}
    # 每盒藥丸：{period: [{"x": dx, "y": dy, "r": 半徑, "color": 顏色索引}, ...]}（盒子座標）
    # None = 每盒放 default_per_box 顆的預設配置；空清單 = 該盒沒有藥
    pills: dict | None = None

    def to_dict(self) -> dict:
        return {
            "positions": {k: list(v) for k, v in self.positions.items()},
            "lighting": self.lighting,
            "shadow": self.shadow,
            "occlusion": self.occlusion,
            "occlusion_target": self.occlusion_target,
            "camera_tilt_deg": self.camera_tilt_deg,
            "similar_color": self.similar_color,
            "extra_box": self.extra_box,
            "pills": self.pills,
        }


def _assets() -> Path:
    return Path(gym_hil.__file__).resolve().parent / "assets"


def _dot_offsets(n: int, spacing: float = 0.014) -> list[float]:
    return [(i - (n - 1) / 2.0) * spacing for i in range(n)]


def box_floor_top(cfg: dict) -> float:
    """標籤平面（世界座標 z）：開口藥杯是中央標籤橋頂面（與牆同高），實心盒是盒頂。"""

    hz = cfg["box"]["half_size"][2]
    if cfg["box"].get("open_top"):
        return TABLE_TOP_Z + 2 * hz + 0.0003
    return TABLE_TOP_Z + 2 * hz + 0.0012


def well_spans(cfg: dict) -> list[tuple[float, float]]:
    """兩格藥丸區沿盒子長軸的範圍（盒子座標）。"""

    hx, w, bx = cfg["box"]["half_size"][0], cfg["box"]["wall"], cfg["box"]["bridge_half_x"]
    return [(-hx + w, -bx), (bx, hx - w)]


def _pill_hidden(name: str, index: int) -> tuple[float, float, float]:
    base = {"morning": 0.0, "noon": 0.1, "evening": 0.2, "extra": 0.3}.get(name, 0.4)
    return (1.4 + base, 1.4 + index * 0.03, 0.01)


def _add_box(spec: mujoco.MjSpec, name: str, label_rgba, n_dots: int, cfg: dict) -> None:
    hx, hy, hz = cfg["box"]["half_size"]
    bcfg = cfg["box"]
    body = spec.worldbody.add_body(name=f"{name}_box", pos=HIDDEN_POSITION)
    body.add_freejoint(name=f"{name}_box_joint")
    common = {"rgba": bcfg["body_rgba"], "friction": (0.9, 0.05, 0.001), "type": mujoco.mjtGeom.mjGEOM_BOX}
    if bcfg.get("open_top"):
        w, f = bcfg["wall"], bcfg["floor"]
        mass = bcfg["mass"]
        # 底板 + 四面牆（皆有碰撞），夾爪夾的是左右兩面長牆
        body.add_geom(name=f"{name}_box_geom", pos=(0, 0, -hz + f / 2), size=(hx, hy, f / 2), mass=mass * 0.4, **common)
        body.add_geom(name=f"{name}_wall_py", pos=(0, hy - w / 2, f / 2), size=(hx, w / 2, hz - f / 2), mass=mass * 0.2, **common)
        body.add_geom(name=f"{name}_wall_ny", pos=(0, -hy + w / 2, f / 2), size=(hx, w / 2, hz - f / 2), mass=mass * 0.2, **common)
        body.add_geom(name=f"{name}_wall_px", pos=(hx - w / 2, 0, f / 2), size=(w / 2, hy - w, hz - f / 2), mass=mass * 0.1, **common)
        body.add_geom(name=f"{name}_wall_nx", pos=(-hx + w / 2, 0, f / 2), size=(w / 2, hy - w, hz - f / 2), mass=mass * 0.1, **common)
        # 中央標籤橋：與牆同高的實心塊（有碰撞），兩側各一格藥丸區
        bx = bcfg["bridge_half_x"]
        body.add_geom(name=f"{name}_bridge", pos=(0, 0, f / 2), size=(bx, hy - w, hz - f / 2), mass=mass * 0.1, **common)
        for side, (a, b) in zip(("n", "p"), well_spans(cfg)):
            body.add_geom(name=f"{name}_well_{side}", type=mujoco.mjtGeom.mjGEOM_BOX, pos=((a + b) / 2, 0, -hz + f + 0.0006),
                          size=((b - a) / 2, hy - w, 0.0006), rgba=bcfg["well_rgba"], material="matte_well", contype=0, conaffinity=0, mass=0.0)
        label_z = hz + 0.0003
        label_half = (bx, hy - w)
        dot_z = hz + 0.0007
        dot_xy = [(0.0, off) for off in _dot_offsets(3, bcfg["dot_spacing"])]
        dot_r = bcfg["dot_radius"]
    else:
        body.add_geom(name=f"{name}_box_geom", size=(hx, hy, hz), mass=bcfg["mass"], **common)
        label_z = hz + 0.0006
        label_half = (hx - 0.004, hy - 0.003)
        dot_z = hz + 0.0014
        dot_xy = [(off, 0.0) for off in _dot_offsets(3)]
        dot_r = 0.0042
    # 彩色標籤（純視覺）：開口杯在中央標籤橋頂面，實心盒在盒頂
    body.add_geom(
        name=f"{name}_label",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        pos=(0.0, 0.0, label_z),
        size=(label_half[0], label_half[1], 0.0003),
        rgba=label_rgba,
        contype=0,
        conaffinity=0,
        mass=0.0,
    )
    # 識別黑點：數量即盒子識別碼（旋轉不變，可由影像計數）
    for index, (dx, dy) in enumerate(dot_xy):
        body.add_geom(
            name=f"{name}_dot{index}",
            type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            pos=(dx, dy, dot_z),
            size=(dot_r, 0.0002, 0.0),
            rgba=(0.05, 0.05, 0.05, 1.0),
            contype=0,
            conaffinity=0,
            mass=0.0,
        )
    # 藥丸：每盒預先建立 max_per_box 顆獨立剛體，不用的放在遠處
    if bcfg.get("open_top") and "pills" in cfg:
        pc = cfg["pills"]
        for index in range(int(pc["max_per_box"])):
            pill = spec.worldbody.add_body(name=f"{name}_pill{index}", pos=_pill_hidden(name, index))
            pill.add_freejoint(name=f"{name}_pill{index}_joint")
            pill.add_geom(
                name=f"{name}_pill{index}_geom",
                type=mujoco.mjtGeom.mjGEOM_SPHERE if index % 2 == 0 else mujoco.mjtGeom.mjGEOM_CAPSULE,
                size=(pc["radius_range"][1], pc["radius_range"][1] * 1.2, 0.0) if index % 2 else (pc["radius_range"][1], 0.0, 0.0),
                mass=pc["mass"],
                rgba=pc["colors"][index % len(pc["colors"])],
                friction=(0.6, 0.02, 0.0005),
                condim=4,
            )
            spec.add_sensor(
                name=f"{name}_pill{index}_pos",
                type=mujoco.mjtSensor.mjSENS_FRAMEPOS,
                objtype=mujoco.mjtObj.mjOBJ_BODY,
                objname=f"{name}_pill{index}",
            )
    spec.add_sensor(
        name=f"{name}_box_pos",
        type=mujoco.mjtSensor.mjSENS_FRAMEPOS,
        objtype=mujoco.mjtObj.mjOBJ_BODY,
        objname=f"{name}_box",
    )


def build_carearm_spec(cfg: dict | None = None) -> mujoco.MjSpec:
    cfg = cfg or load_config()
    specs = box_specs(cfg)
    spec = mujoco.MjSpec.from_file(str(_assets() / "scene.xml"))
    # 開口藥盒 + 藥丸會產生較多接觸，預設 arena 不夠
    spec.memory = 64 * 1024 * 1024
    spec.delete(spec.sensor("block_pos"))
    spec.delete(spec.sensor("block_quat"))
    spec.delete(spec.body("block"))
    for light in spec.lights:
        if light.targetbody == "block":
            light.targetbody = "world"
            light.mode = mujoco.mjtCamLight.mjCAMLIGHT_FIXED
            light.dir = (0.0, 0.6, -0.8)

    # 桌面（視覺；地板平面負責支撐碰撞，桌面頂面與地板同高）
    spec.worldbody.add_geom(
        name="table_top",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        pos=(0.40, 0.0, TABLE_TOP_Z - 0.010),
        size=(0.36, 0.40, 0.010),
        rgba=(0.60, 0.60, 0.58, 1.0),
        friction=(1.0, 0.05, 0.001),
    )

    # 霧面內底：避免頂光在灰底上形成鏡面反光（反光會被誤認成白色藥丸）
    spec.add_material(name="matte_well", specular=0.0, shininess=0.0, reflectance=0.0)
    for period in PERIODS:
        _add_box(spec, period, specs[period].label_rgba, specs[period].id_dots, cfg)
    _add_box(spec, EXTRA_BOX, specs["morning"].label_rgba, 3, cfg)

    # 固定遞送區（淺色底板，無碰撞）
    center = cfg["delivery_area"]["center"]
    half = cfg["delivery_area"]["half_size"]
    area = spec.worldbody.add_body(name="delivery_area", pos=(center[0], center[1], 0.0))
    area.add_geom(
        name="delivery_floor",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        pos=(0, 0, TABLE_TOP_Z + 0.0008),
        size=(half[0], half[1], 0.0008),
        rgba=(0.30, 0.85, 0.40, 0.55),
        contype=0,
        conaffinity=0,
    )

    # 空盒回收區（灰色，低飽和度，不會被當成標籤偵測；無碰撞）
    if "collection_area" in cfg:
        c_center = cfg["collection_area"]["center"]
        c_half = cfg["collection_area"]["half_size"]
        coll = spec.worldbody.add_body(name="collection_area", pos=(c_center[0], c_center[1], 0.0))
        coll.add_geom(
            name="collection_floor",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=(0, 0, TABLE_TOP_Z + 0.0008),
            size=(c_half[0], c_half[1], 0.0008),
            rgba=(0.42, 0.42, 0.45, 0.85),
            contype=0,
            conaffinity=0,
        )

    # 陰影實驗用側光（預設關閉：diffuse=0）
    spec.worldbody.add_light(
        name="shadow_light",
        pos=(0.75, 0.55, 0.55),
        dir=(-0.55, -0.45, -0.70),
        diffuse=(0.0, 0.0, 0.0),
        specular=(0.0, 0.0, 0.0),
        castshadow=True,
    )

    # 手腕相機旁的補光燈（環形 LED）：只在服藥驗證拍照時打開，不影響俯視相機的實驗
    spec.body("attachment").add_light(
        name="wrist_led",
        pos=(-0.05, 0.0, 0.0),
        dir=(0.0, 0.0, 1.0),
        diffuse=(0.75, 0.75, 0.72),
        specular=(0.05, 0.05, 0.05),
        cutoff=40.0,
        exponent=2.0,
        castshadow=False,
        active=False,
    )

    # 部分遮擋板（純視覺，模擬照護者手臂或毛巾擋住鏡頭）
    occ = spec.worldbody.add_body(name="occluder", pos=HIDDEN_POSITION)
    occ.add_geom(
        name="occluder_geom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=(0.05, 0.028, 0.002),
        rgba=(0.45, 0.45, 0.47, 1.0),
        contype=0,
        conaffinity=0,
    )

    cam = cfg["camera"]
    spec.worldbody.add_camera(
        name=cam["name"],
        pos=tuple(cam["pos"]),
        xyaxes=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
        fovy=float(cam["fovy"]),
    )
    spec.meshdir = str(_assets())
    return spec


def _quat_mul(a, b) -> tuple:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def _yaw_quat(yaw_deg: float) -> np.ndarray:
    half = np.deg2rad(yaw_deg) / 2.0
    return np.asarray([np.cos(half), 0.0, 0.0, np.sin(half)])


def camera_pose_for_tilt(cfg: dict, tilt_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """回傳相機位置與旋轉矩陣（欄向量為相機 x、y、z 軸，世界座標）。

    傾斜時相機沿 YZ 平面繞注視點旋轉，使畫面中心仍對準桌面工作區。
    """

    base = np.asarray(cfg["camera"]["pos"], dtype=float)
    look_at = np.asarray([base[0], base[1], 0.0])
    distance = base[2]
    a = np.deg2rad(tilt_deg)
    pos = look_at + distance * np.asarray([0.0, -np.sin(a), np.cos(a)])
    z_axis = (pos - look_at) / np.linalg.norm(pos - look_at)
    x_axis = np.asarray([1.0, 0.0, 0.0])
    y_axis = np.cross(z_axis, x_axis)
    rot = np.column_stack([x_axis, y_axis, z_axis])
    return pos, rot


class CareArmEnv(FrankaGymEnv):
    """三藥盒取藥模擬環境。動作介面與上層 gym-hil 相同（7 維，經 wrapper 變 4 維）。"""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or load_config()
        self.specs = box_specs(self.cfg)
        self._tmp = tempfile.TemporaryDirectory(prefix="carearm-")
        scene_path = Path(self._tmp.name) / "carearm_scene.xml"
        scene_path.write_text(build_carearm_spec(self.cfg).to_xml(), encoding="utf-8")
        cam = self.cfg["camera"]
        try:
            super().__init__(
                xml_path=scene_path,
                control_dt=0.1,
                physics_dt=0.002,
                render_spec=GymRenderingSpec(width=cam["width"], height=cam["height"]),
                image_obs=False,
                home_position=PANDA_HOME_JOINTS,
                cartesian_bounds=CARTESIAN_BOUNDS,
            )
        except Exception:
            self._tmp.cleanup()
            raise
        self.overhead_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, cam["name"])
        self.wrist_cam_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, "handcam_rgb")
        self._base_light_diffuse = self.model.light_diffuse.copy()
        self._base_head_diffuse = self.model.vis.headlight.diffuse.copy()
        self._base_head_ambient = self.model.vis.headlight.ambient.copy()
        self._shadow_light = self.model.light("shadow_light").id
        self._noon_label_geom = self.model.geom("noon_label").id
        self._extra_label_geom = self.model.geom("extra_label").id
        self.conditions = SceneConditions()
        self.box_center_z = TABLE_TOP_Z + float(self.cfg["box"]["half_size"][2])

    # ------------------------------------------------------------------ reset
    def reset(self, seed=None, options: dict | None = None, **kwargs):
        """options={"conditions": SceneConditions} 可設定情境；可經 gym wrapper 傳入。"""

        super().reset(seed=seed)
        mujoco.mj_resetData(self.model, self.data)
        self.reset_robot()
        cond = (options or {}).get("conditions") or SceneConditions()
        if not cond.positions:
            cond.positions = {p: (*self.specs[p].default_position[:2], 0.0) for p in PERIODS}
        self.conditions = cond
        for period in PERIODS:
            x, y, yaw = cond.positions[period]
            self._place(period, (x, y, self.box_center_z), yaw)
        self._apply_extra_box(cond.extra_box)
        self._apply_visual_conditions(cond)
        self._place_pills(cond)
        mujoco.mj_forward(self.model, self.data)
        return self._obs(), {}

    # ------------------------------------------------------------- pills
    @property
    def has_pills(self) -> bool:
        return bool(self.cfg["box"].get("open_top")) and "pills" in self.cfg

    def default_pills(self) -> dict:
        n = int(self.cfg["pills"]["default_per_box"])
        spots = [(-0.019, -0.006), (0.019, 0.006), (-0.019, 0.007), (0.019, -0.007), (-0.020, 0.000)]  # 左右兩格內
        r = float(np.mean(self.cfg["pills"]["radius_range"]))
        return {p: [{"x": spots[i][0], "y": spots[i][1], "r": r, "color": i} for i in range(n)] for p in PERIODS}

    def _pill_names(self, box: str) -> list[str]:
        return [f"{box}_pill{i}" for i in range(int(self.cfg["pills"]["max_per_box"]))]

    def _place_pills(self, cond: SceneConditions) -> None:
        if not self.has_pills:
            return
        layout = cond.pills if cond.pills is not None else self.default_pills()
        floor_z = TABLE_TOP_Z + self.cfg["box"]["floor"]
        colors = self.cfg["pills"]["colors"]
        for box in list(PERIODS) + [EXTRA_BOX]:
            pills = layout.get(box, []) if box in PERIODS else []
            pose = cond.positions.get(box) if box in PERIODS else (cond.extra_box or {}).get("position")
            for index, name in enumerate(self._pill_names(box)):
                joint = self.data.jnt(f"{name}_joint")
                gid = self.model.geom(f"{name}_geom").id
                if index < len(pills) and pose is not None:
                    p = dict(pills[index])
                    r = float(p["r"])
                    # 藥丸只能在左右兩格內：落在標籤橋或牆上的座標推回最近的格子
                    spans = well_spans(self.cfg)
                    span = min(spans, key=lambda ab: 0 if ab[0] + r <= p["x"] <= ab[1] - r else min(abs(p["x"] - ab[0]), abs(p["x"] - ab[1])))
                    p["x"] = float(np.clip(p["x"], span[0] + r + 0.0005, span[1] - r - 0.0005))
                    lim = self.cfg["box"]["half_size"][1] - self.cfg["box"]["wall"] - r * 1.25 - 0.0005
                    p["y"] = float(np.clip(p["y"], -lim, lim))
                    yaw = np.deg2rad(pose[2])
                    c, s = np.cos(yaw), np.sin(yaw)
                    wx = pose[0] + c * p["x"] - s * p["y"]
                    wy = pose[1] + s * p["x"] + c * p["y"]
                    self.model.geom_size[gid, 0] = r
                    if self.model.geom_type[gid] == mujoco.mjtGeom.mjGEOM_CAPSULE:
                        self.model.geom_size[gid, 1] = r * 1.2
                    self.model.geom_rgba[gid] = colors[int(p.get("color", index)) % len(colors)]
                    joint.qpos[:3] = (wx, wy, floor_z + r + 0.002)
                    # 膠囊躺平（繞 X 軸 90°），並依盒子方向轉動
                    joint.qpos[3:7] = (np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)) if self.model.geom_type[gid] != mujoco.mjtGeom.mjGEOM_CAPSULE else \
                        _quat_mul((np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)), (np.cos(np.pi / 4), np.sin(np.pi / 4), 0, 0))
                else:
                    joint.qpos[:3] = _pill_hidden(box, index)
                    joint.qpos[3:7] = (1, 0, 0, 0)
                joint.qvel[:] = 0.0

    def pill_positions(self, box: str) -> list[np.ndarray]:
        """模擬 ground truth：該盒子專屬藥丸的位置（僅評估用）。"""

        return [self.data.sensor(f"{name}_pos").data.copy() for name in self._pill_names(box)]

    def pills_in_box(self, box_name: str) -> int:
        """模擬 ground truth：目前位在某個盒子內的藥丸數（任何盒子的藥丸都算；僅評估用）。"""

        if not self.has_pills:
            return 0
        pos = self.box_position(box_name)
        quat = self.data.jnt(f"{box_name}_box_joint").qpos[3:7]
        yaw = 2 * np.arctan2(quat[3], quat[0])
        c, s = np.cos(-yaw), np.sin(-yaw)
        hx, hy, hz = self.cfg["box"]["half_size"]
        count = 0
        for box in list(PERIODS) + [EXTRA_BOX]:
            for p in self.pill_positions(box):
                dx, dy = p[0] - pos[0], p[1] - pos[1]
                lx, ly = c * dx - s * dy, s * dx + c * dy
                if abs(lx) < hx and abs(ly) < hy and pos[2] - hz < p[2] < pos[2] + hz + 0.01:
                    count += 1
        return count

    def take_pills(self, box: str, n: int, *, drop_on_table: bool = False) -> int:
        """模擬長輩行為：從盒中拿走 n 顆（吃掉 → 移到遠處；掉落 → 放到盒子旁的桌面）。"""

        moved = 0
        pos = self.box_position(box)
        for index, name in enumerate(self._pill_names(box)):
            if moved >= n:
                break
            joint = self.data.jnt(f"{name}_joint")
            p = joint.qpos[:3]
            if abs(p[0] - pos[0]) < 0.04 and abs(p[1] - pos[1]) < 0.03:
                if drop_on_table:
                    # 掉在杯子長邊旁 1.5 cm 左右的桌面（手腕相機視野內）
                    joint.qpos[:3] = (pos[0] - 0.012 + 0.012 * moved, pos[1] + 0.034, TABLE_TOP_Z + 0.006)
                else:
                    joint.qpos[:3] = _pill_hidden(box, index)
                joint.qvel[:] = 0.0
                moved += 1
        mujoco.mj_forward(self.model, self.data)
        return moved

    def _place(self, name: str, xyz, yaw_deg: float) -> None:
        joint = self.data.jnt(f"{name}_box_joint")
        joint.qpos[:3] = xyz
        joint.qpos[3:7] = _yaw_quat(yaw_deg)
        joint.qvel[:] = 0.0

    def _apply_extra_box(self, extra: dict | None) -> None:
        if not extra:
            self._place(EXTRA_BOX, HIDDEN_POSITION, 0.0)
            return
        copy_of = extra["copy_of"]
        self._color_label(EXTRA_BOX, self._label_rgba(copy_of))
        n = self.specs[copy_of].id_dots
        for index in range(3):
            gid = self.model.geom(f"{EXTRA_BOX}_dot{index}").id
            self.model.geom_rgba[gid, 3] = 1.0 if index < n else 0.0
        x, y, yaw = extra["position"]
        self._place(EXTRA_BOX, (x, y, self.box_center_z), yaw)

    def _color_label(self, name: str, rgba) -> None:
        self.model.geom_rgba[self.model.geom(f"{name}_label").id] = rgba

    def _set_dots(self, name: str, n: int) -> None:
        open_top = bool(self.cfg["box"].get("open_top"))
        offsets = _dot_offsets(n, self.cfg["box"]["dot_spacing"]) if open_top else _dot_offsets(n)
        for index in range(3):
            gid = self.model.geom(f"{name}_dot{index}").id
            if index < n:
                if open_top:
                    self.model.geom_pos[gid, 0] = 0.0
                    self.model.geom_pos[gid, 1] = offsets[index]
                else:
                    self.model.geom_pos[gid, 0] = offsets[index]
                self.model.geom_rgba[gid, 3] = 1.0
            else:
                self.model.geom_rgba[gid, 3] = 0.0

    def _label_rgba(self, period: str) -> np.ndarray:
        if period == "noon" and self.conditions.similar_color:
            return np.asarray(self.cfg["similar_color_rgba"]["noon"])
        return np.asarray(self.specs[period].label_rgba)

    def _apply_visual_conditions(self, cond: SceneConditions) -> None:
        # 識別黑點：依設定顯示 n 個
        for period in PERIODS:
            self._set_dots(period, self.specs[period].id_dots)
        if cond.extra_box:
            self._set_dots(EXTRA_BOX, self.specs[cond.extra_box["copy_of"]].id_dots)
        self._color_label("noon", self._label_rgba("noon"))

        scale = LIGHTING_SCALES[cond.lighting]
        self.model.light_diffuse[:] = self._base_light_diffuse * scale
        self.model.vis.headlight.diffuse[:] = self._base_head_diffuse * scale
        self.model.vis.headlight.ambient[:] = self._base_head_ambient * scale
        if cond.shadow:
            self.model.light_diffuse[self._shadow_light] = (1.0, 1.0, 1.0)
            # 陰影條件：降低頂光，使側光造成明顯陰影
            for lid in range(self.model.nlight):
                if lid != self._shadow_light:
                    self.model.light_diffuse[lid] *= 0.35
            self.model.vis.headlight.ambient[:] *= 0.5
        else:
            self.model.light_diffuse[self._shadow_light] = (0.0, 0.0, 0.0)
        # 手腕補光燈是受控光源，不隨環境光變化
        led = self.model.light("wrist_led").id
        self.model.light_diffuse[led] = self._base_light_diffuse[led]

        pos, rot = camera_pose_for_tilt(self.cfg, cond.camera_tilt_deg)
        self.model.cam_pos[self.overhead_id] = pos
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, rot.flatten())
        self.model.cam_quat[self.overhead_id] = quat

        occ_body = self.model.body("occluder").id
        if cond.occlusion > 0 and cond.occlusion_target:
            x, y, _ = cond.positions[cond.occlusion_target]
            b = self.cfg["box"]
            hx = b["bridge_half_x"] if b.get("open_top") else b["half_size"][0]
            # 遮擋板（半長 0.05）從 -X 側覆蓋目標「標籤」的 occlusion 比例
            cover_edge = x - hx + 2 * hx * cond.occlusion
            self.model.body_pos[occ_body] = (cover_edge - 0.05, y, 0.075)
        else:
            self.model.body_pos[occ_body] = HIDDEN_POSITION

    # ------------------------------------------------------------- stepping
    def step(self, action: np.ndarray):
        self.apply_action(action)
        return self._obs(), 0.0, False, False, {}

    def _obs(self) -> dict:
        return {"agent_pos": self.get_robot_state().astype(np.float32)}

    # --------------------------------------------------------- ground truth
    def box_position(self, name: str) -> np.ndarray:
        """模擬 ground truth：僅供成功驗證與評估，不得用於視覺推論。"""

        return self.data.sensor(f"{name}_box_pos").data.copy()

    def all_box_positions(self) -> dict[str, np.ndarray]:
        return {p: self.box_position(p) for p in PERIODS}

    @property
    def tcp_position(self) -> np.ndarray:
        return self.data.sensor("2f85/pinch_pos").data.copy()

    # ------------------------------------------------------------- camera
    def render_overhead(self) -> np.ndarray:
        return self.render_camera(self.overhead_id)

    def render_camera(self, camera_id: int) -> np.ndarray:
        """用指定 MuJoCo camera id 渲染 RGB 影像。"""

        self._viewer.update_scene(self.data, camera=camera_id)
        return self._viewer.render().copy()

    def camera_geometry(self) -> dict:
        """相機內外參（校正資訊，屬於感測器幾何，不含任何物體資訊）。"""

        cam_id = self.overhead_id
        return {
            "pos": self.data.cam_xpos[cam_id].copy(),
            "rot": self.data.cam_xmat[cam_id].reshape(3, 3).copy(),
            "fovy": float(self.model.cam_fovy[cam_id]),
            "width": int(self.cfg["camera"]["width"]),
            "height": int(self.cfg["camera"]["height"]),
        }

    # ------------------------------------------------------------- wrist camera
    def render_wrist(self, led: bool = True) -> np.ndarray:
        """手腕相機（夾爪上方，鏡頭朝下）；預設拍照時打開相機旁補光燈。"""

        lid = self.model.light("wrist_led").id
        self.model.light_active[lid] = 1 if led else 0
        try:
            self._viewer.update_scene(self.data, camera=self.wrist_cam_id)
            return self._viewer.render().copy()
        finally:
            self.model.light_active[lid] = 0

    def render_wrist_depth(self, noise_mm: float | None = None, seed: int | None = None) -> np.ndarray:
        """手腕 RGB-D 相機的深度圖（公尺，沿光軸的距離），與 render_wrist() 像素對齊。

        加入感測器雜訊（高斯，標準差 noise_mm，另有少量無效像素 = 0），模擬短距離結構光／立體深度相機。
        """

        noise_mm = float(self.cfg.get("adherence", {}).get("depth_noise_mm", 0.8)) if noise_mm is None else noise_mm
        self._viewer.enable_depth_rendering()
        try:
            self._viewer.update_scene(self.data, camera=self.wrist_cam_id)
            depth = self._viewer.render().copy()
        finally:
            self._viewer.disable_depth_rendering()
        rng = np.random.default_rng(seed)
        depth = depth + rng.normal(0.0, noise_mm / 1000.0, depth.shape)
        depth[rng.random(depth.shape) < 0.01] = 0.0          # 約 1% 無效像素
        return depth.astype(np.float32)

    def wrist_camera_geometry(self) -> dict:
        """手腕相機目前的內外參（由手臂運動學決定，不含任何物體資訊）。"""

        cam_id = self.wrist_cam_id
        return {
            "pos": self.data.cam_xpos[cam_id].copy(),
            "rot": self.data.cam_xmat[cam_id].reshape(3, 3).copy(),
            "fovy": float(self.model.cam_fovy[cam_id]),
            "width": int(self.cfg["camera"]["width"]),
            "height": int(self.cfg["camera"]["height"]),
        }

    def wrist_offset(self) -> np.ndarray:
        """手腕相機相對夾爪中心（TCP）的位移（世界座標，手臂姿態固定朝下時為常數）。"""

        return self.data.cam_xpos[self.wrist_cam_id] - self.tcp_position

    def close(self) -> None:
        super().close()
        self._tmp.cleanup()


def make_carearm_env(cfg: dict | None = None):
    """以上層相同的 gym-hil wrapper 包裝（4 維動作：x, y, z, gripper）。"""

    from gym_hil.wrappers.factory import wrap_env
    from panda_pick_place import CONTROL_STEP_METERS

    return wrap_env(
        CareArmEnv(cfg),
        ee_step_size={"x": CONTROL_STEP_METERS, "y": CONTROL_STEP_METERS, "z": CONTROL_STEP_METERS},
        use_viewer=False,
        use_inputs_control=False,
        reset_delay_seconds=0.0,
    )
