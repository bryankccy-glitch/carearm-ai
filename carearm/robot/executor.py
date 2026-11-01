"""取藥盒執行器：沿用上層 `panda_pick_place.PandaPickPlaceController` 的閉迴路控制。

控制方式（與上層已驗證版本相同）：gym-hil 末端 mocap + 操作空間控制（opspace），
本模組只以 TCP 回授產生 waypoint 序列，不使用任何預錄動畫。

成功判定只看物理結果（模擬 ground truth，只用於驗證）：
1. 哪一個實體藥盒被抬高超過 8 cm（夾取成功、且知道抓到哪一盒）；
2. 該盒最終位於遞送區內且平放；
3. 其他藥盒未被撞動（< 1.5 cm）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from carearm.settings import PERIODS, ensure_legacy_on_path

ensure_legacy_on_path()

from panda_pick_place import (  # noqa: E402  上層既有、已驗證的控制器
    GRIPPER_CLOSE,
    GRIPPER_HOLD,
    GRIPPER_OPEN,
    PandaPickPlaceController,
    PickPlaceConfig,
    PickPlaceError,
)

OBSERVE_POSITION = (0.24, 0.24, 0.30)   # 手臂讓開相機視野的觀測姿態
SAFE_HOME = (0.40, 0.00, 0.20)          # 同上層 PHASE3_SAFE_HOME
LIFT_SUCCESS_METERS = 0.08
DISTURB_LIMIT_METERS = 0.015


class ExecutionStopped(RuntimeError):
    """使用者按下停止 / 緊急停止。"""


@dataclass
class ExecutionResult:
    requested_period: str
    commanded_xy: tuple[float, float]
    lifted_box: str | None = None          # 實際被抬起的盒子（物理驗證）
    delivered_box: str | None = None       # 實際落在遞送區的盒子
    grasp_success: bool = False
    placed_in_delivery: bool = False
    other_boxes_disturbed: bool = False
    success: bool = False                  # 正確盒子被送達、且未撞動其他盒
    failure_stage: str | None = None
    error: str | None = None
    duration_s: float = 0.0
    sim_steps: int = 0
    stages: list[str] = field(default_factory=list)
    place_area: str = "delivery"
    place_target_xy: tuple[float, float] | None = None
    placement_error_mm: float | None = None   # 被放下的盒子最終中心與目標格位的距離
    max_lift_mm: float | None = None          # 抬升後盒子高出原位的高度
    sim_seconds: float = 0.0                  # 模擬時間（控制步數 × control_dt）
    stage_times: list = field(default_factory=list)   # [[階段, 開始的模擬秒數], ...]

    def to_dict(self) -> dict:
        return asdict(self)


class ExecutionControl:
    """暫停 / 停止旗標（執行緒安全）。"""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def check(self) -> None:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.05)
        if self.stop_event.is_set():
            raise ExecutionStopped("執行已被停止")

    def reset(self) -> None:
        self.stop_event.clear()
        self.pause_event.clear()


class _Controller(PandaPickPlaceController):
    """只新增步數計數；move_to / hold 完全使用上層實作。"""

    steps = 0

    def _step(self, xyz_action, gripper):
        self.steps += 1
        return super()._step(xyz_action, gripper)


class RetrievalExecutor:
    def __init__(
        self,
        env,
        cfg: dict,
        *,
        control: ExecutionControl | None = None,
        step_hook: Callable[[], None] | None = None,
        realtime: bool = False,
        status_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.env = env
        self.sim = env.unwrapped
        self.cfg = cfg
        self.control = control or ExecutionControl()
        self.extra_hook = step_hook
        self.realtime = realtime
        self.status_cb = status_cb or (lambda message: None)

    def _hook(self) -> None:
        if self.extra_hook is not None:
            self.extra_hook()
        self.control.check()

    def _controller(self, sensor: str) -> _Controller:
        return _Controller(
            self.env,
            PickPlaceConfig(
                object_position=(0.5, 0.0, 0.02),
                target_position=tuple(self.cfg["delivery_area"]["center"]),
                travel_height=0.16,
                approach_height=0.12,
            ),
            object_sensor_name=sensor,
            return_home_position=SAFE_HOME,
            step_hook=self._hook,
            realtime=self.realtime,
        )

    def move_to_observation_pose(self) -> None:
        """把手臂移出俯視相機視野，之後才拍照做視覺辨識。"""

        ctrl = self._controller(f"{PERIODS[0]}_box_pos")
        tcp = ctrl.tcp_position
        ctrl.move_to((tcp[0], tcp[1], 0.30), GRIPPER_OPEN)
        ctrl.move_to(OBSERVE_POSITION, GRIPPER_OPEN)

    def move_wrist_camera_over(self, xy: tuple[float, float], offset: tuple[float, float] = (0.0, 0.0)) -> None:
        """把手腕相機移到藥盒正上方（服藥驗證設定的高度），夾爪張開、不碰盒子。

        依實際量到的相機—TCP 位移補償；TCP X 下限 0.20 m（最靠近底座的格位會碰到工作範圍下限，
        此時相機會略偏，但藥盒仍在視野內，計數依相機幾何投影，不要求正上方）。
        """

        ctrl = self._controller(f"{PERIODS[0]}_box_pos")
        height = float(self.cfg.get("adherence", {}).get("inspect_camera_height", 0.24))
        tcp = ctrl.tcp_position
        off = self.sim.wrist_offset()
        goal = np.array([max(0.20, xy[0] + offset[0] - off[0]), xy[1] + offset[1] - off[1], height - off[2]])
        if np.linalg.norm(tcp - goal) > 0.01:          # 已在附近就不必先抬高（例如剛建立基準後再檢查）
            ctrl.move_to((tcp[0], tcp[1], max(float(tcp[2]), 0.20)), GRIPPER_OPEN)
        for _ in range(2):          # 相機位移在姿態微動時會略變，對準兩次
            off = self.sim.wrist_offset()
            target = (max(0.20, xy[0] + offset[0] - off[0]), xy[1] + offset[1] - off[1], height - off[2])
            ctrl.move_to((target[0], target[1], max(target[2], 0.08)), GRIPPER_OPEN)
        ctrl.hold(GRIPPER_OPEN, ctrl.config.settle_steps)

    def move_to_inspection_pose(self, target_xy: tuple[float, float]) -> None:
        """把手腕 RGB 相機移到遞送藥杯正上方，供服藥前後影像比較。

        handcam_rgb 在目前固定腕姿下位於 TCP 的世界 X +5 cm、Y +1.5 cm，
        因此 TCP 反向補償。最左格位會碰到工作範圍下限，故以 0.20 m 截斷；
        其誤差仍在 42.5° 視野內。
        """

        ctrl = self._controller(f"{PERIODS[0]}_box_pos")
        tcp = ctrl.tcp_position
        ctrl.move_to((tcp[0], tcp[1], 0.24), GRIPPER_OPEN)
        x = max(0.20, float(target_xy[0]) - 0.05)
        y = float(target_xy[1]) - 0.015
        ctrl.move_to((x, y, 0.13), GRIPPER_OPEN)
        ctrl.hold(GRIPPER_OPEN, ctrl.config.settle_steps)

    def execute(
        self,
        requested_period: str,
        target_xy: tuple[float, float],
        *,
        place_area: str = "delivery",
        place_xy: tuple[float, float] | None = None,
    ) -> ExecutionResult:
        """依視覺估計的 XY 夾取，放到遞送區（取藥）或回收區（吃完藥後收空盒）。

        成功與否由物理結果判定：被抬起的盒子必須「新進入」目標區域。
        """

        area_cfg = self.cfg["delivery_area" if place_area == "delivery" else "collection_area"]
        self._dest_center = np.asarray(area_cfg["center"][:2], dtype=float)
        self._dest_half = np.asarray(area_cfg["half_size"], dtype=float)
        area_zh = "遞送區" if place_area == "delivery" else "回收區"

        started = time.perf_counter()
        result = ExecutionResult(requested_period=requested_period, commanded_xy=(float(target_xy[0]), float(target_xy[1])),
                                 place_area=place_area)
        t0 = float(self.sim.data.time)
        names = list(PERIODS) + (["extra"] if self.sim.conditions.extra_box else [])
        before = {name: self.sim.box_position(name) for name in names}
        ctrl = self._controller(f"{requested_period}_box_pos")
        box_z = self.sim.box_center_z
        grasp_z = box_z + ctrl.config.grasp_offset_z
        delivery = np.asarray(place_xy if place_xy is not None else self._dest_center, dtype=float)
        result.place_target_xy = (float(delivery[0]), float(delivery[1]))
        x, y = result.commanded_xy

        def stage(name: str) -> None:
            result.stages.append(name)
            result.stage_times.append([name, round(float(self.sim.data.time) - t0, 2)])
            self.status_cb(name)

        try:
            stage("張開夾爪")
            ctrl.hold(GRIPPER_OPEN, ctrl.config.open_steps)
            stage("移動到藥盒上方")
            ctrl.move_to((x, y, ctrl.config.travel_height), GRIPPER_OPEN)
            ctrl.move_to((x, y, ctrl.config.approach_height), GRIPPER_OPEN)
            stage("下降到夾取位置")
            ctrl.move_to((x, y, grasp_z), GRIPPER_OPEN)
            stage("閉合夾爪")
            ctrl.hold(GRIPPER_CLOSE, ctrl.config.close_steps)
            stage("抬起藥盒")
            ctrl.move_to((x, y, ctrl.config.travel_height), GRIPPER_HOLD)

            lifted = [n for n in names if self.sim.box_position(n)[2] > before[n][2] + LIFT_SUCCESS_METERS]
            result.lifted_box = lifted[0] if len(lifted) == 1 else None
            if result.lifted_box:
                result.max_lift_mm = round(float(self.sim.box_position(result.lifted_box)[2] - before[result.lifted_box][2]) * 1000, 1)
            result.grasp_success = len(lifted) == 1
            if not result.grasp_success:
                result.failure_stage = "grasp"
                result.error = "夾取驗證失敗：沒有任何藥盒被抬高 8 cm" if not lifted else "同時抬起多個藥盒"
                stage("夾取失敗，張開夾爪並返回")
                ctrl.hold(GRIPPER_OPEN, ctrl.config.open_steps)
                ctrl.move_to((x, y, ctrl.config.travel_height), GRIPPER_OPEN)
                ctrl.move_to(SAFE_HOME, GRIPPER_OPEN)
                return self._finish(result, before, started, ctrl)

            stage(f"移動到{area_zh}上方")
            ctrl.move_to((delivery[0], delivery[1], ctrl.config.travel_height), GRIPPER_HOLD)
            stage("下降並放下")
            ctrl.move_to((delivery[0], delivery[1], grasp_z), GRIPPER_HOLD)
            ctrl.hold(GRIPPER_OPEN, ctrl.config.open_steps)
            ctrl.hold(GRIPPER_OPEN, ctrl.config.settle_steps)
            stage("垂直退出")
            ctrl.move_to((delivery[0], delivery[1], ctrl.config.travel_height), GRIPPER_OPEN)
            stage("回到安全姿態")
            ctrl.move_to(SAFE_HOME, GRIPPER_OPEN)
            ctrl.hold(GRIPPER_HOLD, ctrl.config.settle_steps)
        except ExecutionStopped as error:
            result.failure_stage = "stopped"
            result.error = str(error)
            return self._finish(result, before, started, ctrl)
        except PickPlaceError as error:
            result.failure_stage = "motion"
            result.error = str(error)
            return self._finish(result, before, started, ctrl)

        return self._finish(result, before, started, ctrl)

    def _finish(self, result: ExecutionResult, before: dict, started: float, ctrl: _Controller) -> ExecutionResult:
        center, half = self._dest_center, self._dest_half

        def inside(pos) -> bool:
            return bool(np.all(np.abs(pos[:2] - center) <= half) and abs(pos[2] - self.sim.box_center_z) < 0.015)

        # 「新進入」目標區域的盒子（之前已在區內的盒子不算），才算這次送達的
        delivered = [name for name, start in before.items() if inside(self.sim.box_position(name)) and not inside(start)]
        result.delivered_box = delivered[0] if len(delivered) == 1 else None
        result.placed_in_delivery = result.delivered_box is not None and result.delivered_box == result.lifted_box
        moved_others = [
            name
            for name, start in before.items()
            if name != result.lifted_box and np.linalg.norm(self.sim.box_position(name)[:2] - start[:2]) > DISTURB_LIMIT_METERS
        ]
        result.other_boxes_disturbed = bool(moved_others)
        result.success = bool(
            result.grasp_success
            and result.placed_in_delivery
            and result.delivered_box == result.requested_period
            and not result.other_boxes_disturbed
        )
        if result.failure_stage is None and not result.success:
            if result.delivered_box != result.requested_period and result.placed_in_delivery:
                result.failure_stage = "wrong_box"
            elif not result.placed_in_delivery:
                result.failure_stage = "place"
            else:
                result.failure_stage = "disturbed_others"
        result.duration_s = time.perf_counter() - started
        result.sim_steps = ctrl.steps
        result.sim_seconds = round(ctrl.steps * float(self.sim.control_dt), 2)
        if result.lifted_box and result.place_target_xy is not None:
            final = self.sim.box_position(result.lifted_box)[:2]
            result.placement_error_mm = round(float(np.hypot(*(final - np.asarray(result.place_target_xy)))) * 1000, 1)
        return result
