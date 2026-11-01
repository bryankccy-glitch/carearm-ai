"""集中式設定載入與共用路徑。

CareArm AI 位於原本 ml 研究專案的子資料夾，並沿用上層已驗證的
gym-hil Panda 場景與 `panda_pick_place.PandaPickPlaceController`。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]          # .../ml/carearm-ai
LEGACY_ROOT = PROJECT_ROOT.parent                            # .../ml（原本的形狀分類專案）
CONFIG_DIR = PROJECT_ROOT / "configs"
MODELS_DIR = PROJECT_ROOT / "models"
RESULTS_DIR = PROJECT_ROOT / "results"

PERIODS = ("morning", "noon", "evening")
PERIOD_ZH = {"morning": "早上", "noon": "中午", "evening": "晚上"}


def ensure_legacy_on_path() -> None:
    """讓 `import panda_pick_place` / `import config` 指向上層既有程式。"""

    legacy = str(LEGACY_ROOT)
    if legacy not in sys.path:
        sys.path.insert(0, legacy)


@dataclass(frozen=True)
class BoxSpec:
    period: str
    box_id: str
    label_name: str
    label_rgba: tuple[float, float, float, float]
    id_dots: int
    default_position: tuple[float, float, float]


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else CONFIG_DIR / "default.yaml"
    with open(cfg_path, encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    _validate(cfg)
    return cfg


def box_specs(cfg: dict | None = None) -> dict[str, BoxSpec]:
    cfg = cfg or load_config()
    specs = {}
    for period in PERIODS:
        raw = cfg["medication_boxes"][period]
        specs[period] = BoxSpec(
            period=period,
            box_id=raw["box_id"],
            label_name=raw["label_name"],
            label_rgba=tuple(raw["label_rgba"]),
            id_dots=int(raw["id_dots"]),
            default_position=tuple(raw["default_position"]),
        )
    return specs


def _validate(cfg: dict) -> None:
    boxes = cfg["medication_boxes"]
    if set(boxes) != set(PERIODS):
        raise ValueError("medication_boxes 必須剛好包含 morning / noon / evening")
    ids = [boxes[p]["box_id"] for p in PERIODS]
    dots = [boxes[p]["id_dots"] for p in PERIODS]
    if len(set(ids)) != 3 or len(set(dots)) != 3:
        raise ValueError("每個藥盒的 box_id 與 id_dots 必須唯一")
    for period in PERIODS:
        x, y, _ = boxes[period]["default_position"]
        if not (0.2 <= x <= 0.6 and -0.3 <= y <= 0.3):
            raise ValueError(f"{period} 藥盒預設位置超出 Panda 工作範圍")
