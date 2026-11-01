"""啟動失敗要立刻回報；語音裡疑似聽錯時段不能直接執行。"""
from types import SimpleNamespace

import pytest

from carearm.api import service as module
from carearm.api.service import SimulationService

check = SimulationService._period_may_be_misheard
REQUEST = SimpleNamespace(intent="REQUEST_MEDICATION")


@pytest.mark.parametrize("text,source,explicit,intent,expected", [
    ("我要吃五餐的藥", "audio", False, REQUEST, True),
    ("幫我拿碗上的藥", "browser-asr", False, REQUEST, True),
    ("我要吃五餐的藥", "text", False, REQUEST, False),
    ("我要吃藥了", "audio", False, REQUEST, False),
    ("我要吃五餐的藥", "audio", True, REQUEST, False),
    ("我吃完五顆了", "audio", False, SimpleNamespace(intent="MEDICATION_ALREADY_TAKEN"), False),
])
def test_misheard_period(text, source, explicit, intent, expected):
    assert check(text, source, intent, SimpleNamespace(explicit=explicit)) is expected


def test_startup_failure_is_reported_at_once(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("找不到模型")

    monkeypatch.setattr(module, "make_carearm_env", broken)
    with pytest.raises(RuntimeError, match="啟動失敗：找不到模型"):
        SimulationService(realtime=False)
