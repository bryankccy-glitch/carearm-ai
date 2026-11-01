"""模擬服藥排程與重複取藥紀錄。只是研究用的可設定時段，不是醫療排程。"""

from __future__ import annotations
import re

from dataclasses import asdict, dataclass, field

from carearm.settings import PERIODS

LATE_GRACE_MIN = 60     # 時段結束後 60 分鐘內仍視為「可能是這一餐」
EARLY_GRACE_MIN = 30    # 時段開始前 30 分鐘內也列為候選


def to_minutes(hhmm: str) -> int:
    if not isinstance(hhmm, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", hhmm) is None:
        raise ValueError("時間必須為有效的 HH:MM（00:00 至 23:59）")
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fmt_minutes(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class ScheduleCheck:
    time: str
    in_window: list[str]
    plausible: list[str]
    candidate: str | None                 # 只有剛好一個候選時才有值
    requested: str | None
    conflict: bool                        # 指定時段不在合理候選內
    needs_clarification: bool             # 未指定時段且候選數 != 1
    duplicate: bool                       # 目標時段今天已取過/已服用
    target: str | None                    # 排程驗證後的目標時段
    minutes_from_window: float            # 目標時段距離視窗中心（分鐘）；無目標為 999
    explicit: bool                        # 使用者是否明確指定時段

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MedicationSchedule:
    windows: dict[str, tuple[int, int]]
    taken_today: set[str] = field(default_factory=set)

    @classmethod
    def from_config(cls, cfg: dict, taken_today=()) -> "MedicationSchedule":
        windows = {p: (to_minutes(cfg["schedule"][p][0]), to_minutes(cfg["schedule"][p][1])) for p in PERIODS}
        return cls(windows=windows, taken_today=set(taken_today))

    def plausible_periods(self, minute: int) -> tuple[list[str], list[str]]:
        inside = [p for p, (s, e) in self.windows.items() if s <= minute < e]
        plausible = [
            p for p, (s, e) in self.windows.items() if s - EARLY_GRACE_MIN <= minute < e + LATE_GRACE_MIN
        ]
        return inside, plausible

    def check(self, minute: int, requested: str | None) -> ScheduleCheck:
        inside, plausible = self.plausible_periods(minute)
        candidate = plausible[0] if len(plausible) == 1 else (inside[0] if len(inside) == 1 and len(plausible) == 1 else None)
        if requested is not None:
            conflict = requested not in plausible
            target = None if conflict else requested
            needs = False
        else:
            conflict = False
            needs = candidate is None
            target = candidate
        duplicate = target is not None and target in self.taken_today
        if target is not None:
            s, e = self.windows[target]
            dist = abs(minute - (s + e) / 2.0)
        else:
            dist = 999.0
        return ScheduleCheck(
            time=fmt_minutes(minute),
            in_window=inside,
            plausible=plausible,
            candidate=candidate,
            requested=requested,
            conflict=conflict,
            needs_clarification=needs,
            duplicate=duplicate,
            target=target,
            minutes_from_window=float(dist),
            explicit=requested is not None,
        )

    def mark_taken(self, period: str) -> None:
        self.taken_today.add(period)
