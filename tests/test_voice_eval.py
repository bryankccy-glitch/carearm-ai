"""真人語音評估工具的純邏輯與 fake transcriber 端到端測試。"""

import csv
import json

import pandas as pd
import pytest

from carearm.speech.corpus import VOICE_SCRIPT
from carearm.speech.evaluate_real import character_error_rate, evaluate_manifest, load_manifest
from carearm.speech.intent import IntentResult


class _FakeIntentClassifier:
    def predict(self, text):
        intent = "SPECIFY_NOON" if "中午" in text else "UNKNOWN"
        return IntentResult(
            transcript=text, intent=intent, raw_confidence=0.9, confidence=0.9,
            margin=0.8, probs={intent: 0.9}, keyword_period="noon" if intent == "SPECIFY_NOON" else None,
            period="noon" if intent == "SPECIFY_NOON" else None, oov_ratio=0.0,
        )


def _write_manifest(root, rows):
    path = root / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["speaker_id", "age_group", "gender", "condition", "sentence_id", "file"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_character_error_rate_normalizes_traditional_and_punctuation():
    assert character_error_rate("請幫我拿藥。", "请帮我拿药") == 0.0
    assert character_error_rate("中午的藥", "中午藥") == pytest.approx(0.25)


def test_voice_script_has_25_unique_sentences_and_four_taiwanese_items():
    assert len(VOICE_SCRIPT) == 25
    assert len({sentence.id for sentence in VOICE_SCRIPT}) == 25
    assert sum("台語" in sentence.note for sentence in VOICE_SCRIPT) == 4
    assert any(sentence.text == "我頭暈" and sentence.true_intent == "UNKNOWN" for sentence in VOICE_SCRIPT)


def test_manifest_loads_anonymous_folder_format(tmp_path):
    rows = [{
        "speaker_id": "spk001", "age_group": "65+", "gender": "undisclosed",
        "condition": "quiet", "sentence_id": "S009", "file": "spk001/S009_quiet.wav",
    }]
    path = _write_manifest(tmp_path, rows)
    assert load_manifest(path) == rows


def test_manifest_rejects_wrong_audio_path(tmp_path):
    rows = [{
        "speaker_id": "spk001", "age_group": "65+", "gender": "undisclosed",
        "condition": "quiet", "sentence_id": "S009", "file": "wrong.wav",
    }]
    with pytest.raises(ValueError, match="file 應為"):
        load_manifest(_write_manifest(tmp_path, rows))


def test_manifest_rejects_name_column(tmp_path):
    path = tmp_path / "manifest.csv"
    path.write_text(
        "speaker_id,age_group,gender,condition,sentence_id,file,name\n"
        "spk001,65+,undisclosed,quiet,S009,spk001/S009_quiet.wav,should-not-be-stored\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="不可包含姓名"):
        load_manifest(path)


def test_fake_transcriber_end_to_end(tmp_path):
    speaker = tmp_path / "spk001"
    speaker.mkdir()
    (speaker / "S009_quiet.wav").touch()
    (speaker / "S022_tv.wav").touch()
    rows = [
        {"speaker_id": "spk001", "age_group": "65+", "gender": "undisclosed", "condition": "quiet",
         "sentence_id": "S009", "file": "spk001/S009_quiet.wav"},
        {"speaker_id": "spk001", "age_group": "65+", "gender": "undisclosed", "condition": "tv",
         "sentence_id": "S022", "file": "spk001/S022_tv.wav"},
    ]
    manifest = _write_manifest(tmp_path, rows)
    output = evaluate_manifest(
        manifest,
        output_root=tmp_path / "results",
        fake_transcriber=True,
        intent_classifier=_FakeIntentClassifier(),
    )
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    utterances = pd.read_csv(output / "per_utterance.csv")
    assert output.name == "fake"
    assert summary["overall"]["n"] == 2
    assert summary["overall"]["mean_cer"] == 0.0
    assert summary["overall"]["intent_accuracy"] == 1.0
    assert set(summary["by_condition"]) == {"quiet", "tv"}
    assert len(utterances) == 2
    assert not any("name" in column.lower() for column in utterances.columns)
