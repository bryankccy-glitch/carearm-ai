"""手腕相機服藥驗證：藥杯內還剩幾顆、有沒有藥丸掉在杯外。

這個模組刻意不讀 MuJoCo 的藥丸座標。兩種量測來源：

1. RGB-D 絕對計數（carearm.perception.pills.PillCounter，主要方法）：
   每次檢查直接數杯內藥丸，不依賴基準影像；送達時的計數就是「實際裝了幾顆」，
   可以和裝盒計畫比對。看不清楚時回報 UNVERIFIED，不硬猜。
2. RGB 前後差異（本模組原有方法）：比較送達時與現在的影像。
   有深度計數時只用它找「杯外新出現的東西」（疑似掉落）；沒有深度時才用它估計取走數量。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

WAITING = "WAITING"
NOT_TAKEN = "NOT_TAKEN"
PARTIAL = "PARTIAL"
TAKEN = "TAKEN"
POSSIBLE_DROP = "POSSIBLE_DROP"
UNVERIFIED = "UNVERIFIED"

STATUS_ZH = {
    WAITING: "等待服藥",
    NOT_TAKEN: "尚未取藥",
    PARTIAL: "只取走部分藥丸",
    TAKEN: "已取走預期藥丸",
    POSSIBLE_DROP: "疑似有藥丸掉在藥杯外",
    UNVERIFIED: "無法確認（看不清楚）",
}


@dataclass
class ComplianceResult:
    period: str
    status: str
    expected_count: int
    removed_count: int
    remaining_count: int
    dropped_regions: int
    confidence: float | None          # 只有 RGB 差異法有啟發式信心；RGB-D 計數為 None（改看 uncertain）
    changed_pixels: int
    message: str
    method: str = "rgb_diff"              # rgbd_count：RGB-D 絕對計數；rgb_diff：前後影像差異
    uncertain: bool = False
    reasons: list | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status_zh"] = STATUS_ZH[self.status]
        return data


class WristComplianceMonitor:
    """保存送達時的基準影像，並判讀之後的影像變化。"""

    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self.diff_threshold = int(cfg.get("diff_threshold", 22))
        self.min_component_area = int(cfg.get("min_component_area", 35))
        self.cup_width_fraction = float(cfg.get("cup_width_fraction", 0.34))
        self.cup_height_fraction = float(cfg.get("cup_height_fraction", 0.44))
        self.baselines: dict[str, np.ndarray] = {}
        self.expected: dict[str, int] = {}
        self.results: dict[str, ComplianceResult] = {}

    def reset(self) -> None:
        self.baselines.clear()
        self.expected.clear()
        self.results.clear()

    def start(self, period: str, frame_rgb: np.ndarray, expected_count: int, measured=None) -> ComplianceResult:
        """送達時建立基準。measured 是 RGB-D 計數結果（PillInspection）；有的話以實測數量為準。"""

        if frame_rgb.ndim != 3 or frame_rgb.shape[2] != 3:
            raise ValueError("手腕相機影像必須是 RGB 三通道")
        self.baselines[period] = frame_rgb.copy()
        planned = max(0, int(expected_count))
        message = "已用手腕相機記錄送達時影像，等待再次檢查。"
        method, uncertain, reasons = "rgb_diff", False, []
        if measured is not None:
            method, uncertain, reasons = "rgbd_count", bool(measured.uncertain), list(measured.reasons)
            if not measured.uncertain:
                if measured.count != planned:
                    message = f"送達時實測杯內 {measured.count} 顆，與裝盒計畫 {planned} 顆不符，請照護者確認。"
                else:
                    message = f"送達時實測杯內 {measured.count} 顆，與裝盒計畫一致，等待服藥。"
                planned = int(measured.count)
            else:
                message = f"送達時手腕相機看不清楚杯內藥丸，暫以裝盒計畫 {planned} 顆為準。"
        self.expected[period] = planned
        result = ComplianceResult(
            period=period,
            status=WAITING,
            expected_count=self.expected[period],
            removed_count=0,
            remaining_count=self.expected[period],
            dropped_regions=0,
            confidence=1.0,
            changed_pixels=0,
            message=message,
            method=method,
            uncertain=uncertain,
            reasons=reasons,
        )
        self.results[period] = result
        return result

    @staticmethod
    def _rect(shape: tuple[int, ...], wf: float, hf: float) -> tuple[int, int, int, int]:
        h, w = shape[:2]
        rw, rh = int(w * wf), int(h * hf)
        return (w // 2 - rw // 2, h // 2 - rh // 2, w // 2 + rw // 2, h // 2 + rh // 2)

    def _change_components(self, baseline: np.ndarray, current: np.ndarray):
        if current.shape != baseline.shape:
            raise ValueError("目前影像尺寸和基準影像不同")
        # Panda 每次回到同一目標仍可能差幾個像素。先以整張桌面紋理做仿射對齊，
        # 否則整個藥杯邊緣都會被誤認為「藥丸移動」。
        warp = np.eye(2, 3, dtype=np.float32)
        base_gray = cv2.cvtColor(baseline, cv2.COLOR_RGB2GRAY)
        now_gray = cv2.cvtColor(current, cv2.COLOR_RGB2GRAY)
        raw_change_ratio = float(np.mean(np.max(cv2.absdiff(baseline, current), axis=2) >= self.diff_threshold))
        if raw_change_ratio < 0.05:
            aligned = current
        else:
            try:
                cv2.findTransformECC(base_gray, now_gray, warp, cv2.MOTION_AFFINE,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5))
                aligned = cv2.warpAffine(current, warp, (current.shape[1], current.shape[0]),
                                         flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                         borderMode=cv2.BORDER_REFLECT)
            except cv2.error:
                aligned = current
        delta = cv2.absdiff(baseline, aligned)
        strength = np.max(delta, axis=2).astype(np.uint8)
        strength = cv2.GaussianBlur(strength, (5, 5), 0)
        mask = (strength >= self.diff_threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, labels, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
        components = []
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area >= self.min_component_area:
                components.append((area, float(centers[i, 0]), float(centers[i, 1])))
        return aligned, mask, components

    def evaluate(self, period: str, frame_rgb: np.ndarray, measured=None) -> tuple[ComplianceResult, np.ndarray]:
        if period not in self.baselines:
            raise ValueError(f"{period} 尚未建立送達基準影像")
        baseline = self.baselines[period]
        aligned, mask, components = self._change_components(baseline, frame_rgb)
        x1, y1, x2, y2 = self._rect(frame_rgb.shape, self.cup_width_fraction, self.cup_height_fraction)
        # 擴大的環形區用來找「從杯內消失、又在杯旁出現」的變化。
        ox1, oy1, ox2, oy2 = self._rect(frame_rgb.shape, min(0.72, self.cup_width_fraction * 2.0),
                                         min(0.76, self.cup_height_fraction * 1.7))
        inside = [c for c in components if x1 <= c[1] <= x2 and y1 <= c[2] <= y2]
        outside = [c for c in components if ox1 <= c[1] <= ox2 and oy1 <= c[2] <= oy2
                   and not (x1 <= c[1] <= x2 and y1 <= c[2] <= y2)]
        expected = self.expected[period]
        removed = min(expected, len(inside))
        changed_pixels = int(np.count_nonzero(mask[y1:y2, x1:x2]))
        if measured is not None:
            return self._evaluate_measured(period, expected, measured, outside, changed_pixels, aligned)

        if not inside:
            status = NOT_TAKEN
        elif outside:
            status = POSSIBLE_DROP
        elif removed >= expected:
            status = TAKEN
        else:
            status = PARTIAL
        remaining = max(0, expected - removed)
        if status == NOT_TAKEN:
            message = f"手腕相機沒有看到藥杯內明顯變化；預期仍有 {remaining} 顆。"
        elif status == POSSIBLE_DROP:
            message = f"杯內有 {removed} 個取藥變化，但杯外也有 {len(outside)} 個變化區域，請人工確認。"
        elif status == PARTIAL:
            message = f"手腕相機估計取走 {removed} 顆，仍可能剩下 {remaining} 顆。"
        else:
            message = f"手腕相機估計已取走預期的 {expected} 顆藥丸。"
        # 影像差異越明顯，信心越高；上限保留 0.99，避免把啟發式方法說成絕對正確。
        evidence = sum(c[0] for c in inside) / max(1, self.min_component_area * max(1, expected))
        confidence = 0.55 if status == NOT_TAKEN else min(0.99, 0.60 + 0.12 * evidence)
        result = ComplianceResult(period, status, expected, removed, remaining, len(outside),
                                  round(float(confidence), 3), changed_pixels, message)
        self.results[period] = result

        annotated = aligned.copy()
        color = (60, 220, 110) if status == TAKEN else (255, 180, 30) if status in (PARTIAL, POSSIBLE_DROP) else (240, 80, 80)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)
        for _, cx, cy in inside:
            cv2.circle(annotated, (round(cx), round(cy)), 12, color, 3)
        for _, cx, cy in outside:
            cv2.circle(annotated, (round(cx), round(cy)), 14, (255, 60, 60), 3)
        return result, annotated

    def _evaluate_measured(self, period, expected, measured, outside, changed_pixels, aligned):
        """RGB-D 計數為主：杯內剩幾顆直接量；影像差異只用來找杯外的新物體（疑似掉落）。"""

        remaining = int(measured.count)
        removed = max(0, expected - remaining)
        # 有深度時，「杯外的新東西」直接由 RGB-D 量測（不依賴前後影像差異，手臂陰影不會造成誤報）
        if getattr(measured, "depth_used", False):
            outside = list(measured.outside_blobs)
        if measured.uncertain and remaining == 0:
            status = UNVERIFIED
            message = "手腕相機看不清楚杯內是否還有藥：" + "；".join(measured.reasons or ["原因不明"])
        elif outside and remaining < expected:
            status = POSSIBLE_DROP
            message = f"杯內剩 {remaining} 顆，但杯外出現 {len(outside)} 個新變化區域，疑似有藥丸掉落，請人工確認。"
        elif remaining == 0:
            status = TAKEN
            message = f"手腕 RGB-D 確認杯內已空（原有 {expected} 顆）。"
        elif remaining >= expected:
            status = NOT_TAKEN
            message = f"杯內仍有 {remaining} 顆，尚未取藥。"
        else:
            status = PARTIAL
            message = f"杯內還剩 {remaining} 顆（原有 {expected} 顆），只取走部分。"
        result = ComplianceResult(period, status, expected, removed, remaining, len(outside),
                                  None, changed_pixels, message,
                                  method="rgbd_count", uncertain=bool(measured.uncertain), reasons=list(measured.reasons))
        self.results[period] = result
        return result, aligned

    def info(self) -> dict[str, dict]:
        return {period: result.to_dict() for period, result in self.results.items()}
