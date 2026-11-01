"""手腕相機服藥驗證的純影像測試（不需要 MuJoCo renderer）。"""

import cv2
import numpy as np
import pytest

from carearm.perception.compliance import NOT_TAKEN, PARTIAL, POSSIBLE_DROP, TAKEN, WristComplianceMonitor


def frames(removed: int = 0, dropped: bool = False):
    base = np.full((200, 200, 3), 110, np.uint8)
    cv2.circle(base, (90, 90), 7, (250, 230, 220), -1)
    cv2.circle(base, (110, 110), 7, (220, 245, 250), -1)
    now = base.copy()
    if removed >= 1:
        cv2.circle(now, (90, 90), 7, (110, 110, 110), -1)
    if removed >= 2:
        cv2.circle(now, (110, 110), 7, (110, 110, 110), -1)
    if dropped:
        cv2.circle(now, (145, 100), 7, (250, 230, 220), -1)
    return base, now


@pytest.mark.parametrize("removed,status,remaining", [
    (0, NOT_TAKEN, 2),
    (1, PARTIAL, 1),
    (2, TAKEN, 0),
])
def test_wrist_difference_classifies_intake(removed, status, remaining):
    before, after = frames(removed)
    monitor = WristComplianceMonitor({"diff_threshold": 15, "min_component_area": 20})
    waiting = monitor.start("noon", before, 2)
    assert waiting.remaining_count == 2
    result, annotated = monitor.evaluate("noon", after)
    assert result.status == status
    assert result.removed_count == removed
    assert result.remaining_count == remaining
    assert annotated.shape == before.shape


def test_change_beside_cup_is_flagged_as_possible_drop():
    before, after = frames(1, dropped=True)
    monitor = WristComplianceMonitor({"diff_threshold": 15, "min_component_area": 20})
    monitor.start("evening", before, 2)
    result, _ = monitor.evaluate("evening", after)
    assert result.status == POSSIBLE_DROP
    assert result.dropped_regions == 1


def test_requires_a_baseline_and_matching_rgb_shape():
    monitor = WristComplianceMonitor()
    image = np.zeros((100, 100, 3), np.uint8)
    with pytest.raises(ValueError, match="基準"):
        monitor.evaluate("morning", image)
    monitor.start("morning", image, 2)
    with pytest.raises(ValueError, match="尺寸"):
        monitor.evaluate("morning", np.zeros((80, 80, 3), np.uint8))
