"""服藥依從性驗證：用手腕 RGB-D 相機數藥盒內剩下的藥丸。

只用影像、深度與已知的相機幾何／藥盒設計尺寸（不讀模擬器裡的藥丸或藥盒位置）：
1. RGB：找出中央彩色標籤橋（色相一致的區塊），用 minAreaRect 求藥盒位置與方向，並核對尺寸、黑點；
2. 依藥盒設計圖把左右兩格內底投影回影像，得到兩個多邊形；
3. 深度：把每個像素還原成 3D 點，以格子中央的中位高度當內底平面，高出內底 2.5 mm 以上的區塊是藥丸。
   用高度而不用顏色：淺色藥丸、陰影與側光反光在顏色上會混淆（實測：顏色門檻在像素層級無法分開），
   高度則不受光線影響；
4. 依斑點面積與凸性估計顆數，並回報「不確定」的情況（找不到標籤、格子出畫面、斑點太小、
   深度無效像素太多），上層決策據此「再看一次」或請照護者確認，而不是硬猜。

沒有深度圖時退回顏色法（較不可靠，結果一律標記為不確定）。
限制：掉到桌上的藥丸不在檢查範圍；藥丸疊在一起時數量是估計值（但「是否為空」仍可靠）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from carearm.perception.detector import DELIVERY_GREEN_HUE, MIN_SATURATION, MIN_VALUE, PixelToWorld
from carearm.simulation.scene import TABLE_TOP_Z, box_floor_top, well_spans


@dataclass
class PillInspection:
    count: int
    empty: bool
    uncertain: bool
    reasons: list[str] = field(default_factory=list)
    blobs: list[dict] = field(default_factory=list)
    box_xy: tuple[float, float] | None = None
    yaw_deg: float | None = None
    dot_count: int = 0
    outside_objects: int = 0              # 杯子周圍桌面上的小物體（疑似掉落的藥丸），只有深度可用時計算
    outside_blobs: list[dict] = field(default_factory=list)
    depth_used: bool = False
    wells_px: list[list[list[int]]] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("wells_px", None)
        return data


class PillCounter:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        b = cfg["box"]
        self.hy, self.w = float(b["half_size"][1]), float(b["wall"])
        self.bridge_half = float(b["bridge_half_x"])
        self.top_z = box_floor_top(cfg)
        self.floor_z = TABLE_TOP_Z + float(b["floor"]) + 0.0012     # 內底灰板頂面
        a = cfg.get("adherence", {})
        self.min_area = float(a.get("min_pill_area_mm2", 14))
        self.pill_area = float(a.get("pill_area_mm2", 60))
        self.max_single = float(a.get("max_single_pill_mm2", 135))
        self.min_height = float(a.get("min_pill_height_mm", 2.5)) / 1000.0

    @staticmethod
    def _count_height_peaks(rel: np.ndarray, blob: np.ndarray, h: float = 0.0035, min_px: int = 15) -> int:
        """h-maxima：數突起高度至少 h（公尺）的區域峰值（以形態學重建實作）。"""

        x, y, bw, bh = cv2.boundingRect(blob)
        pad = 3
        x0, y0 = max(x - pad, 0), max(y - pad, 0)
        sub = np.where(blob[y0:y + bh + pad, x0:x + bw + pad] > 0, np.clip(rel[y0:y + bh + pad, x0:x + bw + pad], 0, None), 0)
        f = cv2.GaussianBlur(sub.astype(np.float32), (9, 9), 0)
        marker = np.clip(f - h, 0, None)
        kernel = np.ones((3, 3), np.uint8)
        for _ in range(400):
            nxt = np.minimum(cv2.dilate(marker, kernel), f)
            if np.allclose(nxt, marker, atol=1e-7):
                break
            marker = nxt
        dome = f - marker
        peaks = (dome >= h * 0.9).astype(np.uint8)
        n, _, st, _ = cv2.connectedComponentsWithStats(peaks, connectivity=8)
        return int(sum(1 for k in range(1, n) if st[k, cv2.CC_STAT_AREA] >= min_px))

    @staticmethod
    def _point_map(depth: np.ndarray, geometry: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """深度（沿光軸）→ 每個像素的世界座標 (x, y, z)；無效像素的 z 為 0。"""

        h, w = depth.shape
        focal = (geometry["height"] / 2.0) / np.tan(np.deg2rad(geometry["fovy"]) / 2.0)
        d = cv2.medianBlur(depth.astype(np.float32), 5)
        us, vs = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        rot = np.asarray(geometry["rot"], dtype=np.float32)
        # 相機座標：x 右、y 上、看向 -z；深度為沿光軸距離
        xc, yc = (us - w / 2.0) / focal * d, -(vs - h / 2.0) / focal * d
        pos = np.asarray(geometry["pos"], dtype=np.float32)
        x_world = pos[0] + rot[0, 0] * xc + rot[0, 1] * yc + rot[0, 2] * (-d)
        y_world = pos[1] + rot[1, 0] * xc + rot[1, 1] * yc + rot[1, 2] * (-d)
        z_world = pos[2] + rot[2, 0] * xc + rot[2, 1] * yc + rot[2, 2] * (-d)
        z_world[d <= 0] = 0.0
        return x_world, y_world, z_world

    # ------------------------------------------------------------------ main
    def inspect(self, rgb: np.ndarray, geometry: dict, depth: np.ndarray | None = None) -> PillInspection:
        h, w = rgb.shape[:2]
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        label = ((sat >= MIN_SATURATION) & (val >= MIN_VALUE)).astype(np.uint8)
        label[(hue >= DELIVERY_GREEN_HUE[0]) & (hue <= DELIVERY_GREEN_HUE[1])] = 0
        points = self._point_map(depth, geometry) if depth is not None else None
        if points is not None:
            # 有深度：標籤橋與杯緣同高，藥丸最高不到內底上方 1 cm。只留「夠高」的彩色像素，
            # 不必靠色相（手臂陰影會讓標籤一半偏色）；補光燈下標籤會變淡，所以飽和度門檻放寬到 30
            # （白色杯緣的飽和度約 5，仍會被排除）
            label = ((sat >= 30) & (val >= MIN_VALUE) & (points[2] > self.top_z - 0.008)).astype(np.uint8)
            label[(hue >= DELIVERY_GREEN_HUE[0]) & (hue <= DELIVERY_GREEN_HUE[1])] = 0
        label = cv2.morphologyEx(label, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # 黑點會在標籤上挖洞：閉運算後再找輪廓
        closed = cv2.morphologyEx(label, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) >= 300]
        if not contours:
            return PillInspection(0, False, True, ["手腕相機畫面中找不到藥盒標籤"])
        center = np.array([w / 2, h / 2])
        contour = min(contours, key=lambda c: np.linalg.norm(cv2.minAreaRect(c)[0] - center))
        # 沒有深度時：只保留與標籤同色相、同飽和度的像素（淺橘、淺粉藥丸飽和度較低，不會黏進標籤）
        comp = np.zeros((h, w), np.uint8)
        cv2.drawContours(comp, [contour], -1, 1, thickness=-1)
        sel = (comp > 0) & (label > 0)
        hue_med = float(np.median(hue[sel]))
        sat_med = float(np.median(sat[sel]))
        dh = np.abs(hue.astype(np.int16) - int(round(hue_med)))
        dh = np.minimum(dh, 180 - dh)
        if points is None:
            label = ((dh <= 8) & (sat >= 0.7 * sat_med) & (val >= MIN_VALUE)).astype(np.uint8)
        label = cv2.morphologyEx(label, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        closed = cv2.morphologyEx(label, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) >= 300]
        if not contours:
            return PillInspection(0, False, True, ["手腕相機畫面中找不到藥盒標籤"])
        contour = min(contours, key=lambda c: np.linalg.norm(cv2.minAreaRect(c)[0] - center))
        rect = cv2.minAreaRect(contour)
        corners_px = cv2.boxPoints(rect)

        top = PixelToWorld(geometry, self.top_z)
        corners = np.asarray([top.to_world(float(u), float(v)) for u, v in corners_px])
        c_world = corners.mean(axis=0)
        e1, e2 = corners[1] - corners[0], corners[2] - corners[1]
        long_e, short_e = (e1, e2) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, e1)
        long_len, short_len = float(np.linalg.norm(long_e)), float(np.linalg.norm(short_e))
        x_axis = short_e / max(short_len, 1e-9)                   # 藥盒長軸（標籤橋的短邊方向）
        yaw = float(np.degrees(np.arctan2(x_axis[1], x_axis[0])))
        reasons: list[str] = []
        uncertain = False
        exp_long, exp_short = 2 * (self.hy - self.w), 2 * self.bridge_half
        if not (0.75 <= long_len / exp_long <= 1.3 and 0.7 <= short_len / exp_short <= 1.35):
            uncertain = True
            reasons.append(f"標籤尺寸不符（{long_len * 1000:.0f}×{short_len * 1000:.0f} mm），可能被遮擋或不是藥盒")

        # 識別黑點（核對盒子身分）
        region = np.zeros((h, w), np.uint8)
        cv2.drawContours(region, [contour], -1, 1, thickness=-1)
        inner = cv2.erode(region, np.ones((5, 5), np.uint8))
        label_val = float(val[(region > 0) & (label > 0)].mean()) if np.any((region > 0) & (label > 0)) else 0.0
        dark = ((inner > 0) & (label == 0) & (val < max(25.0, 0.55 * label_val))).astype(np.uint8)
        n_dark, _, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
        dot_count = int(sum(1 for i in range(1, n_dark) if st[i, cv2.CC_STAT_AREA] >= 12))

        # 兩格內底投影回影像
        floor = PixelToWorld(geometry, self.floor_z)
        y_axis = np.array([-x_axis[1], x_axis[0]])
        margin = 0.0003       # 一路到內壁：藥丸常靠著牆
        core = 0.003          # 參考顏色只取格子中央，避開牆邊陰影
        lab_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        height_src = None
        floor_ref = None
        if points is not None:
            x_src, y_src, height_src = points
            # 內底高度 = 實測標籤橋頂面高度 − 已知杯深；標籤橋不會被藥丸蓋住，所以比用內底本身估計可靠
            bridge = (cv2.erode(closed, np.ones((7, 7), np.uint8)) > 0) & (height_src > 0)
            if bridge.sum() >= 50:
                floor_ref = float(np.median(height_src[bridge])) - (self.top_z - self.floor_z)
            else:
                uncertain = True
                reasons.append("深度影像中標籤橋太小，內底高度改用設計值")
                floor_ref = self.floor_z
        else:
            uncertain = True
            reasons.append("沒有深度影像，只能用顏色判斷（較不可靠）")
        blobs: list[dict] = []
        mm2_scales: list[float] = []
        wells_px = []
        count = 0
        for a, b in well_spans(self.cfg):
            local = [(a + margin, -(self.hy - self.w - margin)), (b - margin, -(self.hy - self.w - margin)),
                     (b - margin, self.hy - self.w - margin), (a + margin, self.hy - self.w - margin)]
            world = [c_world + lx * x_axis + ly * y_axis for lx, ly in local]
            poly = np.asarray([floor.to_pixel(float(p[0]), float(p[1])) for p in world], np.float32)
            wells_px.append(poly.round().astype(int).tolist())
            inside = (poly[:, 0] >= 0) & (poly[:, 0] < w) & (poly[:, 1] >= 0) & (poly[:, 1] < h)
            if not inside.all():
                uncertain = True
                reasons.append("有一格藥丸區超出手腕相機畫面")
            mask = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(mask, poly.round().astype(np.int32), 1)
            mask &= (closed == 0).astype(np.uint8)                 # 標籤橋本身不算
            px_area = float(mask.sum())
            world_area_mm2 = (b - a - 2 * margin) * 2 * (self.hy - self.w - margin) * 1e6
            if px_area < 50:
                uncertain = True
                reasons.append("藥丸區在畫面中太小或被遮住")
                continue
            mm2_per_px = world_area_mm2 / max(float(cv2.contourArea(poly)), 1.0)
            mm2_scales.append(mm2_per_px)
            core_world = [c_world + lx * x_axis + ly * y_axis for lx, ly in
                          [(a + core, -(self.hy - self.w - core)), (b - core, -(self.hy - self.w - core)),
                           (b - core, self.hy - self.w - core), (a + core, self.hy - self.w - core)]]
            core_mask = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(core_mask, np.asarray([floor.to_pixel(float(p[0]), float(p[1])) for p in core_world]).round().astype(np.int32), 1)
            core_mask &= mask
            if height_src is not None:
                valid = height_src > 0
                if valid[mask > 0].mean() < 0.85:
                    uncertain = True
                    reasons.append("深度影像無效像素過多")
                rel = height_src - floor_ref
                # 用 3D 點判斷是否真的在格子裡：斜看時牆與標籤橋的側面會投影進格子邊緣，
                # 但它們的 3D 點落在牆面上，離格子內緣 1 mm 以內就排除；藥丸只在牆邊相切一點
                lx = (x_src - c_world[0]) * x_axis[0] + (y_src - c_world[1]) * x_axis[1]
                ly = (x_src - c_world[0]) * y_axis[0] + (y_src - c_world[1]) * y_axis[1]
                m3 = 0.0010
                inside3d = (lx > a + m3) & (lx < b - m3) & (np.abs(ly) < self.hy - self.w - m3)
                search = cv2.dilate(mask, np.ones((9, 9), np.uint8))          # 2D 只當搜尋範圍
                pill = ((search > 0) & valid & inside3d & (rel > self.min_height) & (rel < 0.014)).astype(np.uint8)
            else:
                pix = lab_img[core_mask > 0] if core_mask.sum() >= 30 else lab_img[mask > 0]
                ref = np.median(pix, axis=0)
                diff_l = lab_img[..., 0] - ref[0]
                chroma = np.linalg.norm(lab_img[..., 1:] - ref[1:], axis=-1)
                pill = ((mask > 0) & ((diff_l > 18) | (chroma > 12))).astype(np.uint8)
            pill = cv2.morphologyEx(pill, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            n, labels_img, stats, cents = cv2.connectedComponentsWithStats(pill, connectivity=8)
            for i in range(1, n):
                area_mm2 = float(stats[i, cv2.CC_STAT_AREA]) * mm2_per_px
                if area_mm2 < self.min_area:
                    continue
                blob = (labels_img == i).astype(np.uint8)
                cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                hull_area = float(cv2.contourArea(cv2.convexHull(cnts[0]))) if cnts else 0.0
                solidity = float(stats[i, cv2.CC_STAT_AREA]) / max(hull_area, 1.0)
                if area_mm2 <= self.max_single and solidity >= 0.86:
                    est = 1                      # 單顆圓錠或膠囊：凸、面積不大
                else:
                    # 黏在一起：用高度峰（相鄰藥丸之間有凹谷）估計，至少 2 顆；沒有深度時用面積
                    by_area = max(2, int(round(area_mm2 / self.pill_area)))
                    est = max(2, self._count_height_peaks(rel, blob)) if height_src is not None else by_area
                    reasons.append(f"有藥丸靠在一起（{area_mm2:.0f} mm²），估計 {est} 顆")
                if area_mm2 < self.min_area * 1.6:
                    uncertain = True
                    reasons.append(f"有一個很小的斑點（{area_mm2:.0f} mm²），無法確定是不是藥丸碎片")
                count += est
                info = {"center_px": [round(float(cents[i][0]), 1), round(float(cents[i][1]), 1)],
                        "area_mm2": round(area_mm2, 1), "estimated_pills": est}
                if height_src is not None:
                    sel = blob > 0
                    info["height_mm"] = round(float(np.max(rel[sel])) * 1000, 1)
                    info["box_xy_mm"] = [round(float(np.mean(lx[sel])) * 1000, 1), round(float(np.mean(ly[sel])) * 1000, 1)]
                blobs.append(info)
        outside_objects, outside_blobs = 0, []
        if points is not None and floor_ref is not None:
            # 杯外 3 cm 內的桌面：比桌面高 2.5–14 mm 的小區塊 = 疑似掉落的藥丸（盒子本身與相鄰盒子更高或更遠）
            hx = float(self.cfg["box"]["half_size"][0])
            gx = (x_src - c_world[0]) * x_axis[0] + (y_src - c_world[1]) * x_axis[1]
            gy = (x_src - c_world[0]) * y_axis[0] + (y_src - c_world[1]) * y_axis[1]
            table_ref = floor_ref - (self.floor_z - TABLE_TOP_Z)
            ht = height_src - table_ref
            ring = (np.abs(gx) < hx + 0.030) & (np.abs(gy) < self.hy + 0.030) & \
                   ~((np.abs(gx) < hx + 0.003) & (np.abs(gy) < self.hy + 0.003))
            obj = (ring & (height_src > 0) & (ht > self.min_height) & (ht < 0.014)).astype(np.uint8)
            obj = cv2.morphologyEx(obj, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            n_o, _, st_o, ce_o = cv2.connectedComponentsWithStats(obj, connectivity=8)
            scale = float(np.mean(mm2_scales)) if mm2_scales else 0.05
            for k in range(1, n_o):
                area_mm2 = float(st_o[k, cv2.CC_STAT_AREA]) * scale
                if area_mm2 >= self.min_area:
                    outside_objects += 1
                    outside_blobs.append({"center_px": [round(float(ce_o[k][0]), 1), round(float(ce_o[k][1]), 1)],
                                          "area_mm2": round(area_mm2, 1)})
            if outside_objects:
                reasons.append(f"杯子旁邊的桌面上有 {outside_objects} 個小物體，疑似藥丸掉落")
        return PillInspection(
            count=count,
            empty=count == 0,
            uncertain=uncertain,
            reasons=reasons,
            blobs=blobs,
            box_xy=(float(c_world[0]), float(c_world[1])),
            yaw_deg=round(yaw, 1),
            dot_count=dot_count,
            outside_objects=outside_objects,
            outside_blobs=outside_blobs,
            depth_used=points is not None,
            wells_px=wells_px,
        )


def annotate_inspection(rgb: np.ndarray, result: PillInspection) -> np.ndarray:
    out = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    for poly in result.wells_px:
        cv2.polylines(out, [np.asarray(poly, np.int32)], True, (255, 200, 0), 2)
    for blob in result.blobs:
        x, y = map(int, blob["center_px"])
        cv2.circle(out, (x, y), 14, (0, 140, 255), 2)
    for blob in result.outside_blobs:
        x, y = map(int, blob["center_px"])
        cv2.circle(out, (x, y), 16, (40, 40, 230), 3)
    text = "uncertain" if result.uncertain else ("EMPTY" if result.empty else f"{result.count} pill(s) left")
    color = (0, 200, 255) if result.uncertain else ((80, 200, 80) if result.empty else (40, 40, 230))
    cv2.putText(out, text, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 4, cv2.LINE_AA)
    cv2.putText(out, text, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
