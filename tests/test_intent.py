"""藥物時段抽取與意圖分類。"""
import random

import pytest

from carearm.speech.intent import (
    CONFUSIONS,
    PERIOD_HOMOPHONES,
    IntentClassifier,
    IntentResult,
    homophone_period,
    keyword_period,
    simulate_asr,
)


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


@pytest.mark.parametrize("text,period", [
    ("幫我拿五藥", "noon"),
    ("幫我拿找上的藥", "morning"),
    ("我要吃碗上的藥", "evening"),
    ("幫我拿中午的藥", None),
    ("幫我拿中午的五藥", None),
    ("我要吃藥了", None),
    ("找五藥", None),
    ("早上還是晚上的五藥", None),
    ("終五舞藥", "noon"),
    ("", None),
])
def test_homophone_period(text, period):
    assert homophone_period(text) == period


def test_period_homophones_cover_period_defining_confusions():
    expected = {
        homophone: period
        for char, period in [
            ("早", "morning"), ("午", "noon"), ("中", "noon"),
            ("晚", "evening"), ("睡", "evening"),
        ]
        for homophone in CONFUSIONS[char]
    }
    assert PERIOD_HOMOPHONES == expected


def test_intent_result_without_homophone_period():
    result = IntentResult("我要吃藥了", "REQUEST_MEDICATION", 0.8, 0.9, 0.7,
                          {"REQUEST_MEDICATION": 0.9}, None, None, 0.0)
    assert result.homophone_period is None
    old_data = result.to_dict()
    del old_data["homophone_period"]
    assert IntentResult(**old_data).homophone_period is None


def test_predict_sets_homophone_period(clf):
    assert clf.predict("幫我拿五藥").homophone_period == "noon"
    assert clf.predict("幫我拿中午的藥").homophone_period is None
