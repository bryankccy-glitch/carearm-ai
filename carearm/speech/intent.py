"""繁體中文取藥指令：意圖分類、時段抽取與 ASR 雜訊模擬。

- 意圖分類器：字元 n-gram TF-IDF + Logistic Regression（透明基線）。
- 分類器原始 predict_proba 不視為已校正機率；另以校正集做 temperature scaling。
- 部分句型（HELD_OUT）完全不進入分類器訓練，只出現在實驗情境，用來檢驗泛化。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from carearm.settings import MODELS_DIR

INTENTS = (
    "REQUEST_MEDICATION",
    "SPECIFY_MORNING",
    "SPECIFY_NOON",
    "SPECIFY_EVENING",
    "MEDICATION_ALREADY_TAKEN",
    "CANCEL",
    "UNKNOWN",
)
INTENT_TO_PERIOD = {"SPECIFY_MORNING": "morning", "SPECIFY_NOON": "noon", "SPECIFY_EVENING": "evening"}

_PERIOD_WORDS = {
    "morning": ["早上", "早餐後", "早晨", "上午"],
    "noon": ["中午", "午餐後", "午間"],
    "evening": ["晚上", "晚餐後", "睡前", "傍晚"],
}

TEMPLATES: dict[str, list[str]] = {
    "REQUEST_MEDICATION": [
        "我要吃藥了", "該吃藥了", "幫我拿藥", "我想吃藥", "請幫我拿一下藥",
        "可以幫我拿藥嗎", "藥拿給我", "時間到了我要吃藥", "麻煩拿藥給我", "我現在要吃藥",
    ],
    "SPECIFY": [
        "幫我拿{p}的藥", "我要吃{p}的藥", "請拿{p}那盒藥", "{p}的藥給我", "我要{p}的藥盒",
        "麻煩拿{p}的藥過來", "可以拿{p}的藥給我嗎", "我現在要吃{p}的藥",
    ],
    "MEDICATION_ALREADY_TAKEN": [
        "我剛剛已經吃過藥了", "我吃過藥了", "藥已經吃了", "剛才吃過了", "我早就吃過藥了", "不用拿我吃過了",
    ],
    "CANCEL": ["不用了", "取消", "先不要拿", "等一下再說", "算了不要了", "停止"],
    "UNKNOWN": [
        "今天天氣怎麼樣", "幫我開電視", "我想喝水", "現在幾點", "我有點累", "幫我叫家人", "把燈打開", "嗯",
    ],
}
# 不進入分類器訓練、只用於實驗的句型索引（泛化測試）
HELD_OUT = {
    "REQUEST_MEDICATION": {8, 9},
    "SPECIFY": {6, 7},
    "MEDICATION_ALREADY_TAKEN": {5},
    "CANCEL": {5},
    "UNKNOWN": {6, 7},
}

# ASR 同音 / 近音錯字模型（模擬中文語音辨識常見錯誤）
CONFUSIONS = {
    "早": ["找", "棗", "澡"], "上": ["尚", "賞"], "午": ["五", "舞", "武"], "中": ["終", "鐘", "忠"],
    "晚": ["碗", "完", "挽"], "藥": ["要", "耀", "樂"], "吃": ["痴", "持"], "拿": ["那", "哪"],
    "幫": ["邦", "綁"], "我": ["喔", "窩"], "了": ["啦", "樂"], "過": ["果", "國"], "睡": ["水", "稅"],
    "前": ["錢", "潛"], "餐": ["參", "慘"], "後": ["厚", "候"], "盒": ["和", "合"], "給": ["幾"],
}
FILLERS = ["嗯", "那個", "呃", "就是"]


@dataclass(frozen=True)
class IntentResult:
    transcript: str
    intent: str
    raw_confidence: float          # 分類器原始 max proba（未校正）
    confidence: float              # temperature scaling 後的 max proba
    margin: float                  # 校正後 top1 - top2
    probs: dict[str, float]
    keyword_period: str | None     # 規則式關鍵字抽出的時段（交叉驗證用）
    period: str | None             # 意圖對應的時段（SPECIFY_* 才有）
    oov_ratio: float               # 轉錄文字中不在詞彙表的字比例（雜訊代理特徵）

    def to_dict(self) -> dict:
        return asdict(self)


def render_command(intent: str, rng: random.Random, *, allow_held_out: bool = True) -> str:
    key = "SPECIFY" if intent in INTENT_TO_PERIOD else intent
    choices = [
        (i, t) for i, t in enumerate(TEMPLATES[key]) if allow_held_out or i not in HELD_OUT.get(key, set())
    ]
    _, template = rng.choice(choices)
    if key == "SPECIFY":
        word = rng.choice(_PERIOD_WORDS[INTENT_TO_PERIOD[intent]])
        return template.format(p=word)
    return template


def simulate_asr(text: str, noise: float, rng: random.Random) -> str:
    """依雜訊程度模擬 ASR 錯誤：同音字替換、漏字、插入贅詞。noise=0 時原文輸出。"""

    if noise <= 0:
        return text
    out: list[str] = []
    for char in text:
        roll = rng.random()
        if roll < noise * 0.55 and char in CONFUSIONS:
            out.append(rng.choice(CONFUSIONS[char]))
        elif roll < noise * 0.55 + noise * 0.15:
            continue  # 漏字
        else:
            out.append(char)
    if rng.random() < noise:
        out.insert(rng.randrange(len(out) + 1), rng.choice(FILLERS))
    return "".join(out)


def keyword_period(text: str) -> str | None:
    """透明規則式時段抽取；若同時出現多個時段詞則回傳 None。"""

    found = {p for p, words in _PERIOD_WORDS.items() for w in words if w in text}
    return found.pop() if len(found) == 1 else None


def _vocabulary() -> set[str]:
    chars: set[str] = set()
    for templates in TEMPLATES.values():
        for t in templates:
            chars.update(re.sub(r"\{p\}", "", t))
    for words in _PERIOD_WORDS.values():
        for w in words:
            chars.update(w)
    return chars


VOCAB = _vocabulary()


def build_corpus(n_per_intent: int, seed: int, *, noise_levels=(0.0, 0.1, 0.2, 0.3, 0.4)) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    texts, labels = [], []
    for intent in INTENTS:
        for _ in range(n_per_intent):
            clean = render_command(intent, rng, allow_held_out=False)
            texts.append(simulate_asr(clean, rng.choice(noise_levels), rng))
            labels.append(intent)
    return texts, labels


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class IntentClassifier:
    def __init__(self) -> None:
        self.pipeline = Pipeline(
            [
                ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(1, 3), sublinear_tf=True)),
                ("lr", LogisticRegression(C=4.0, max_iter=2000)),
            ]
        )
        self.temperature = 1.0
        self.report: dict = {}

    @property
    def classes(self) -> list[str]:
        return list(self.pipeline.classes_)

    def fit(self, texts, labels) -> None:
        self.pipeline.fit(texts, labels)

    def _logits(self, texts) -> np.ndarray:
        return self.pipeline.decision_function(texts)

    def calibrate(self, texts, labels) -> None:
        """在校正集上以 NLL 最小化求 temperature（不碰測試資料）。"""

        logits = self._logits(texts)
        idx = np.asarray([self.classes.index(l) for l in labels])

        def nll(t: float) -> float:
            p = _softmax(logits / t)
            return float(-np.log(p[np.arange(len(idx)), idx] + 1e-12).mean())

        self.temperature = float(minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded").x)

    def predict(self, text: str) -> IntentResult:
        raw = self.pipeline.predict_proba([text])[0]
        cal = _softmax(self._logits([text]) / self.temperature)[0]
        order = np.argsort(cal)[::-1]
        intent = self.classes[order[0]]
        chars = [c for c in text if not c.isspace()]
        oov = sum(1 for c in chars if c not in VOCAB) / max(len(chars), 1)
        return IntentResult(
            transcript=text,
            intent=intent,
            raw_confidence=float(raw.max()),
            confidence=float(cal[order[0]]),
            margin=float(cal[order[0]] - cal[order[1]]),
            probs={c: float(cal[i]) for i, c in enumerate(self.classes)},
            keyword_period=keyword_period(text),
            period=INTENT_TO_PERIOD.get(intent),
            oov_ratio=float(oov),
        )

    def probs_matrix(self, texts, *, calibrated: bool = True) -> np.ndarray:
        if calibrated:
            return _softmax(self._logits(texts) / self.temperature)
        return self.pipeline.predict_proba(texts)

    def save(self, path: Path | None = None) -> Path:
        path = path or MODELS_DIR / "intent_classifier.joblib"
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self.pipeline, "temperature": self.temperature, "report": self.report}, path)
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> "IntentClassifier":
        path = path or MODELS_DIR / "intent_classifier.joblib"
        payload = joblib.load(path)
        obj = cls()
        obj.pipeline = payload["pipeline"]
        obj.temperature = payload["temperature"]
        obj.report = payload.get("report", {})
        return obj


def expected_calibration_error(confidences: np.ndarray, correct: np.ndarray, n_bins: int = 10) -> float:
    confidences = np.asarray(confidences, dtype=float)
    correct = np.asarray(correct, dtype=float)
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (confidences > lo) & (confidences <= hi)
        if mask.any():
            ece += mask.mean() * abs(confidences[mask].mean() - correct[mask].mean())
    return float(ece)


def train_intent_model(seed: int = 7001, n_train: int = 220, n_cal: int = 80) -> IntentClassifier:
    """訓練 + 校正 + 在獨立（含 held-out 句型）的評估集上報告 ECE。"""

    train_x, train_y = build_corpus(n_train, seed)
    cal_x, cal_y = build_corpus(n_cal, seed + 1)
    clf = IntentClassifier()
    clf.fit(train_x, train_y)
    clf.calibrate(cal_x, cal_y)

    # 評估集：包含 held-out 句型（分類器從未見過）
    rng = random.Random(seed + 2)
    eval_x, eval_y = [], []
    for intent in INTENTS:
        for _ in range(60):
            clean = render_command(intent, rng, allow_held_out=True)
            eval_x.append(simulate_asr(clean, rng.choice((0.0, 0.1, 0.2, 0.3, 0.4)), rng))
            eval_y.append(intent)
    report = {}
    for name, calibrated in (("raw", False), ("calibrated", True)):
        probs = clf.probs_matrix(eval_x, calibrated=calibrated)
        pred = np.asarray(clf.classes)[probs.argmax(1)]
        correct = pred == np.asarray(eval_y)
        report[name] = {
            "accuracy": float(correct.mean()),
            "ece": expected_calibration_error(probs.max(1), correct),
            "mean_confidence": float(probs.max(1).mean()),
        }
    report["temperature"] = clf.temperature
    report["n_train"] = len(train_x)
    report["n_calibration"] = len(cal_x)
    report["n_eval"] = len(eval_x)
    clf.report = report
    return clf


if __name__ == "__main__":
    model = train_intent_model()
    path = model.save()
    (MODELS_DIR / "intent_report.json").write_text(json.dumps(model.report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(model.report, indent=2, ensure_ascii=False))
    print(f"已儲存：{path}")
