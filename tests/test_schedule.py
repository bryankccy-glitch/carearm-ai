"""排程衝突、需澄清與重複取藥。"""
from carearm.decision.schedule import MedicationSchedule, to_minutes


def test_inferred_period_inside_window(cfg):
    c = MedicationSchedule.from_config(cfg).check(to_minutes("12:30"), None)
    assert c.target == "noon" and not c.needs_clarification and not c.conflict


def test_explicit_conflict(cfg):
    c = MedicationSchedule.from_config(cfg).check(to_minutes("08:00"), "evening")
    assert c.conflict and c.target is None


def test_ambiguous_period_needs_clarification(cfg):
    c = MedicationSchedule.from_config(cfg).check(to_minutes("10:40"), None)  # 早上剛結束、中午快開始
    assert c.needs_clarification and set(c.plausible) == {"morning", "noon"}


def test_outside_all_windows_needs_clarification(cfg):
    c = MedicationSchedule.from_config(cfg).check(to_minutes("02:00"), None)
    assert c.needs_clarification and c.target is None


def test_duplicate_detected(cfg):
    s = MedicationSchedule.from_config(cfg, taken_today={"noon"})
    assert s.check(to_minutes("12:00"), "noon").duplicate
    assert not s.check(to_minutes("18:00"), "evening").duplicate
