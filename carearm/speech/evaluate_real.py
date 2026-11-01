"""真人語音離線評估：CER、意圖準確率、ECE 與轉錄延遲。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from carearm.settings import PROJECT_ROOT, RESULTS_DIR
from carearm.speech.asr import Transcription, WhisperASR, to_traditional
from carearm.speech.corpus import sentence_by_id
from carearm.speech.intent import IntentClassifier, expected_calibration_error

REQUIRED_COLUMNS = ("speaker_id", "age_group", "gender", "condition", "sentence_id", "file")
CONDITIONS = {"quiet", "tv"}


def normalize_text(text: str) -> str:
    """簡轉繁、移除空白與標點，保留中文、英數字供 CER 比較。"""

    traditional = to_traditional(str(text)).lower()
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", traditional))


def levenshtein_distance(reference: str, hypothesis: str) -> int:
    """使用兩列動態規劃計算字元編輯距離。"""

    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ref_char != hyp_char),
            ))
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference_norm = normalize_text(reference)
    hypothesis_norm = normalize_text(hypothesis)
    if not reference_norm:
        return 0.0 if not hypothesis_norm else 1.0
    return levenshtein_distance(reference_norm, hypothesis_norm) / len(reference_norm)


def load_manifest(path: Path | str) -> list[dict]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in fields]
        if missing:
            raise ValueError(f"manifest 缺少欄位：{', '.join(missing)}")
        if any("name" in field.lower() or "姓名" in field for field in fields):
            raise ValueError("manifest 不可包含姓名欄位")
        rows = list(reader)

    if not rows:
        raise ValueError("manifest 目前沒有錄音資料")

    for row_number, row in enumerate(rows, start=2):
        speaker_id = row["speaker_id"].strip()
        sentence_id = row["sentence_id"].strip()
        condition = row["condition"].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", speaker_id):
            raise ValueError(f"第 {row_number} 列 speaker_id 格式錯誤")
        if condition not in CONDITIONS:
            raise ValueError(f"第 {row_number} 列 condition 必須是 quiet 或 tv")
        sentence_by_id(sentence_id)
        expected = Path(speaker_id) / f"{sentence_id}_{condition}.wav"
        if Path(row["file"]) != expected:
            raise ValueError(f"第 {row_number} 列 file 應為 {expected}")
        if not row["age_group"].strip() or not row["gender"].strip():
            raise ValueError(f"第 {row_number} 列 age_group 與 gender 不可空白")
    return rows


class _ReferenceTranscriber:
    """測試用：直接回傳腳本原文，不載入 Whisper。"""

    engine = "fake-reference"

    def transcribe_reference(self, reference: str) -> Transcription:
        return Transcription(reference, self.engine, "zh", 0.0)


def _group_metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0, "mean_cer": None, "intent_accuracy": None, "ece": None, "mean_latency_s": None}
    correct = frame["intent_correct"].astype(bool).to_numpy()
    confidence = frame["intent_confidence"].to_numpy(float)
    return {
        "n": int(len(frame)),
        "mean_cer": float(frame["cer"].mean()),
        "intent_accuracy": float(correct.mean()),
        "ece": float(expected_calibration_error(confidence, correct)),
        "mean_latency_s": float(frame["latency_s"].mean()),
    }


def evaluate_manifest(
    manifest_path: Path | str,
    *,
    model_size: str = "small",
    output_root: Path | str | None = None,
    fake_transcriber: bool = False,
    intent_classifier=None,
) -> Path:
    manifest_path = Path(manifest_path)
    rows = load_manifest(manifest_path)
    classifier = intent_classifier or IntentClassifier.load()
    transcriber = _ReferenceTranscriber() if fake_transcriber else WhisperASR(model_size)
    results = []

    for row in rows:
        sentence = sentence_by_id(row["sentence_id"])
        audio_path = manifest_path.parent / row["file"]
        if not audio_path.is_file():
            raise FileNotFoundError(f"找不到音檔：{audio_path}")
        started = time.perf_counter()
        if fake_transcriber:
            transcription = transcriber.transcribe_reference(sentence.text)
        else:
            transcription = transcriber.transcribe(str(audio_path))
        latency = time.perf_counter() - started
        intent = classifier.predict(transcription.text)
        results.append({
            **{column: row[column] for column in REQUIRED_COLUMNS},
            "reference": sentence.text,
            "transcript": transcription.text,
            "true_intent": sentence.true_intent,
            "period": sentence.period,
            "pred_intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_correct": intent.intent == sentence.true_intent,
            "cer": character_error_rate(sentence.text, transcription.text),
            "latency_s": latency,
            "asr_engine": transcription.engine,
            "asr_language": transcription.language,
            "asr_avg_logprob": transcription.avg_logprob,
        })

    frame = pd.DataFrame(results)
    model_dir = "fake" if fake_transcriber else model_size
    root = Path(output_root) if output_root else RESULTS_DIR / "voice_eval"
    output_dir = root / model_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "per_utterance.csv", index=False, encoding="utf-8-sig")
    summary = {
        "model": model_dir,
        "manifest": str(manifest_path),
        "overall": _group_metrics(frame),
        "by_condition": {str(key): _group_metrics(group) for key, group in frame.groupby("condition")},
        "by_age_group": {str(key): _group_metrics(group) for key, group in frame.groupby("age_group")},
        "privacy": "manifest 僅使用匿名 speaker_id，不儲存姓名",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="CareArm AI 真人語音離線評估")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "voice" / "manifest.csv")
    parser.add_argument("--model", choices=("small", "medium"), default="small")
    parser.add_argument("--output-root", type=Path, default=RESULTS_DIR / "voice_eval")
    parser.add_argument("--fake-transcriber", action="store_true", help="測試用：回傳腳本原文，不載入 Whisper")
    args = parser.parse_args()
    output = evaluate_manifest(
        args.manifest,
        model_size=args.model,
        output_root=args.output_root,
        fake_transcriber=args.fake_transcriber,
    )
    print(f"完成：{output / 'summary.json'}")
    print(f"逐句結果：{output / 'per_utterance.csv'}")


if __name__ == "__main__":
    main()
