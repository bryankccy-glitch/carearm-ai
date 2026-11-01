"""藥物時段抽取與意圖分類。"""
import random

import pytest

from carearm.speech.intent import IntentClassifier, keyword_period, simulate_asr


@pytest.fixture(scope="module")
def clf():
    return IntentClassifier.load()


@pytest.mark.parametrize("text,intent,period", [
    ("我要吃藥了", "REQUEST_MEDICATION", None),
    ("幫我拿早上的藥", "SPECIFY_MORNING", "morning"),
    ("我要吃中午的藥", "SPECIFY_NOON", "noon"),
    ("幫我拿晚上的藥", "SPECIFY_EVENING", "evening"),
    ("我剛剛已經吃過藥了", "MEDICATION_ALREADY_TAKEN", None),
    ("取消", "CANCEL", None),
])
def test_example_commands(clf, text, intent, period):
    result = clf.predict(text)
    assert result.intent == intent
    assert result.period == period
    assert 0.0 <= result.confidence <= 1.0


def test_keyword_period_extraction():
    assert keyword_period("幫我拿早上的藥") == "morning"
    assert keyword_period("睡前的藥給我") == "evening"
    assert keyword_period("中午的藥") == "noon"
    assert keyword_period("我要吃藥了") is None
    assert keyword_period("早上還是晚上的藥") is None  # 多個時段 → 不猜


def test_asr_noise_zero_is_identity():
    assert simulate_asr("幫我拿早上的藥", 0.0, random.Random(1)) == "幫我拿早上的藥"


def test_raw_and_calibrated_confidence_are_distinct(clf):
    assert clf.temperature != 1.0
    r = clf.predict("邦我那碗上的要")
    assert r.raw_confidence != r.confidence
