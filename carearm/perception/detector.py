"""OpenCV 藥盒標籤偵測、識別碼讀取與像素→世界座標。

推論時只使用：RGB 影像 + 相機幾何（內外參校正資訊）+ 已知桌面/藥盒高度。
絕不讀取 MuJoCo 物體位置或標籤 metadata。
"""

from __future__ import annotations

import colorsys
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from carearm.settings import PERIODS, box_specs

LABEL_TOP_Z = 0.001 + 0.040 + 0.0012     # 桌面 + 盒高 + 標籤厚度（已知幾何）
MIN_SATURATION = 70                       # OpenCV HSV, 0-255
MIN_VALUE = 40
MIN_AREA_PX = 50          # 開口藥杯的標籤橋約 400 px；部分遮擋時仍要能偵測到殘片
MAX_AREA_PX = 4000
DELIVERY_GREEN_HUE = (40, 85)             # OpenCV hue 0-179：遞送區綠色不是藥盒
WORKSPACE_X = (0.20, 0.62)                # 只接受落在桌面工作區內的標籤（相機校正後座標）
WORKSPACE_Y = (-0.30, 0.30)
AREA_RATIO_RANGE = (0.12, 1.6)


@dataclass
class LabelDetection:
    bbox: tuple[int, int, int, int]             # x, y, w, h
    center_px: tuple[float, float]
    world_xy: tuple[float, float]
    area_px: float
    expected_area_px: float
    area_ratio: float                           # 可見面積 / 預期面積（遮擋時下降）
    rectangularity: float                       # contour 面積 / 最小外接矩形面積
    mean_hue_deg: float
    mean_sat: float
    mean_val: float
    dot_count: int
    raw_scores: dict[str, float] = field(default_factory=dict)      # 未校正（色相距離 softmax）
    calibrated_probs: dict[str, float] = field(default_factory=dict)  # 校正後機率
    predicted_period: str | None = None
    zone: str = "table"                         # table / delivery / collection

    def features(self) -> np.ndarray:
        h = np.deg2rad(self.mean_hue_deg)
        return np.asarray([np.cos(h), np.sin(h), self.mean_sat / 255.0, self.mean_val / 255.0])

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        return data


def rgba_to_hue_deg(rgba) -> float:
    h, _, _ = colorsys.rgb_to_hsv(*rgba[:3])
    return h * 360.0


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


class PixelToWorld:
    """以相機幾何把像素射線與已知水平面相交。"""

    def __init__(self, geometry: dict, plane_z: float = LABEL_TOP_Z) -> None:
        self.pos = np.asarray(geometry["pos"], dtype=float)
        self.rot = np.asarray(geometry["rot"], dtype=float)
        self.width = int(geometry["width"])
        self.height = int(geometry["height"])
        self.focal = (self.height / 2.0) / np.tan(np.deg2rad(geometry["fovy"]) / 2.0)
        self.plane_z = plane_z

    def ray(self, u: float, v: float) -> np.ndarray:
        d_cam = np.asarray([(u - self.width / 2.0) / self.focal, -(v - self.height / 2.0) / self.focal, -1.0])
        return self.rot @ d_cam

    def to_world(self, u: float, v: float) -> tuple[float, float]:
        direction = self.ray(u, v)
        if abs(direction[2]) < 1e-9:
            raise ValueError("相機射線與桌面平行")
        t = (self.plane_z - self.pos[2]) / direction[2]
        point = self.pos + t * direction
        return float(point[0]), float(point[1])

    def to_world_many(self, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
        d_cam = np.stack([(us - self.width / 2.0) / self.focal, -(vs - self.height / 2.0) / self.focal, -np.ones_like(us)])
        d = self.rot @ d_cam
        t = (self.plane_z - self.pos[2]) / d[2]
        return (self.pos[:2, None] + t * d[:2]).T

    def to_pixel(self, x: float, y: float, z: float | None = None) -> tuple[float, float]:
        z = self.plane_z if z is None else z
        d_world = np.asarray([x, y, z]) - self.pos
        d_cam = self.rot.T @ d_world
        u = self.width / 2.0 + self.focal * d_cam[0] / -d_cam[2]
        v = self.height / 2.0 - self.focal * d_cam[1] / -d_cam[2]
        return float(u), float(v)

    def expected_label_area(self, x: float, y: float, half_x: float, half_y: float) -> float:
        corners = [self.to_pixel(x + sx * half_x, y + sy * half_y) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return float(cv2.contourArea(np.asarray(corners, dtype=np.float32)))


class LabelDetector:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.specs = box_specs(cfg)
        self.prototype_hues = {p: rgba_to_hue_deg(self.specs[p].label_rgba) for p in PERIODS}
        hx, hy, _ = cfg["box"]["half_size"]
        if cfg["box"].get("open_top"):
            w = cfg["box"]["wall"]
            self.label_half = (cfg["box"]["bridge_half_x"], hy - w)   # 中央標籤橋頂面
        else:
            self.label_half = (hx - 0.004, hy - 0.003)
        from carearm.simulation.scene import box_floor_top

        self.plane_z = box_floor_top(cfg)       # 標籤所在平面高度（已知幾何）
        self.calibrator = None    # 由 perception.calibration 設定
        self.zones = {"delivery": (cfg["delivery_area"]["center"][:2], cfg["delivery_area"]["half_size"])}
        if "collection_area" in cfg:
            self.zones["collection"] = (cfg["collection_area"]["center"][:2], cfg["collection_area"]["half_size"])

    def zone_of(self, x: float, y: float) -> str:
        """依校正後的世界座標判斷標籤位於桌面、遞送區或回收區。"""

        for name, (center, half) in self.zones.items():
            if abs(x - center[0]) <= half[0] and abs(y - center[1]) <= half[1]:
                return name
        return "table"

    def detect(self, rgb: np.ndarray, geometry: dict) -> list[LabelDetection]:
        """桌面上的取藥候選（不含已在遞送區、回收區的盒子）。"""

        return [d for d in self.detect_all(rgb, geometry) if d.zone == "table"]

    def detect_all(self, rgb: np.ndarray, geometry: dict) -> list[LabelDetection]:
        projector = PixelToWorld(geometry, self.plane_z)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        mask = ((sat >= MIN_SATURATION) & (val >= MIN_VALUE)).astype(np.uint8) * 255
        green = (hue >= DELIVERY_GREEN_HUE[0]) & (hue <= DELIVERY_GREEN_HUE[1])
        mask[green] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # 藥丸或牆的陰影可能把同一個內底切成兩塊：先用閉運算（約 1 cm）把鄰近色塊分成同一組找輪廓，
        # 但顏色與黑點仍從原始 mask 讀取（閉運算會把黑點填掉）
        group = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
        contours, _ = cv2.findContours(group, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[LabelDetection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not (MIN_AREA_PX <= area <= MAX_AREA_PX):
                continue
            hull = cv2.convexHull(contour)
            region = np.zeros(mask.shape, np.uint8)
            cv2.drawContours(region, [hull], -1, 255, thickness=-1)
            label_pixels = (region > 0) & (mask > 0)
            if label_pixels.sum() < MIN_AREA_PX * 0.8:
                continue
            (cx, cy), (rw, rh), _ = cv2.minAreaRect(hull)
            rect_area = max(rw * rh, 1.0)
            hull_area = cv2.contourArea(hull)
            hues = hue[label_pixels].astype(float) * 2.0
            angles = np.deg2rad(hues)
            mean_hue = float(np.rad2deg(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) % 360.0)
            mean_sat = float(sat[label_pixels].mean())
            mean_val = float(val[label_pixels].mean())
            dots = self._count_dots(val, region, mask, mean_val)
            M = cv2.moments(hull)
            if M["m00"] <= 0:
                continue
            u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
            wx, wy = projector.to_world(u, v)
            if not (WORKSPACE_X[0] <= wx <= WORKSPACE_X[1] and WORKSPACE_Y[0] <= wy <= WORKSPACE_Y[1]):
                continue
            zone = self.zone_of(wx, wy)
            expected = projector.expected_label_area(wx, wy, *self.label_half)
            if not (AREA_RATIO_RANGE[0] <= hull_area / max(expected, 1.0) <= AREA_RATIO_RANGE[1]):
                continue
            x, y, w, h = cv2.boundingRect(hull)
            det = LabelDetection(
                bbox=(int(x), int(y), int(w), int(h)),
                center_px=(float(u), float(v)),
                world_xy=(wx, wy),
                area_px=float(hull_area),
                expected_area_px=expected,
                area_ratio=float(hull_area / max(expected, 1.0)),
                rectangularity=float(hull_area / rect_area),
                mean_hue_deg=mean_hue,
                mean_sat=mean_sat,
                mean_val=mean_val,
                dot_count=dots,
                zone=zone,
            )
            det.raw_scores = self.raw_color_scores(det)
            if self.calibrator is not None:
                det.calibrated_probs = self.calibrator.predict_probs(det)
                det.predicted_period = max(det.calibrated_probs, key=det.calibrated_probs.get)
            else:
                det.predicted_period = max(det.raw_scores, key=det.raw_scores.get)
            detections.append(det)
        return detections

    def raw_color_scores(self, det: LabelDetection, temperature: float = 15.0) -> dict[str, float]:
        """未校正分數：依色相距離做 softmax。這不是機率，只用於 Baseline 與校正前比較。"""

        distances = np.asarray([hue_distance(det.mean_hue_deg, self.prototype_hues[p]) for p in PERIODS])
        logits = -distances / temperature
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        return {p: float(probs[i]) for i, p in enumerate(PERIODS)}

    @staticmethod
    def _count_dots(val: np.ndarray, region: np.ndarray, mask: np.ndarray, label_val: float) -> int:
        inner = cv2.erode(region, np.ones((3, 3), np.uint8))
        dark = ((inner > 0) & (mask == 0) & (val < max(25.0, 0.55 * label_val))).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
        return int(sum(1 for i in range(1, n) if 4 <= stats[i, cv2.CC_STAT_AREA] <= 120))


def annotate(rgb: np.ndarray, detections: list[LabelDetection], target: LabelDetection | None = None) -> np.ndarray:
    """畫出偵測框、顏色、信心與目標（供儀表板顯示）。"""

    out = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    for det in detections:
        x, y, w, h = det.bbox
        color = (0, 140, 255) if det is target else (230, 230, 230)
        cv2.rectangle(out, (x - 3, y - 3), (x + w + 3, y + h + 3), color, 2)
        probs = det.calibrated_probs or det.raw_scores
        conf = probs.get(det.predicted_period, 0.0) if det.predicted_period else 0.0
        text = f"{det.predicted_period} {conf:.2f} id:{det.dot_count}"
        cv2.putText(out, text, (x - 4, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.putText(out, text, (x - 4, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
