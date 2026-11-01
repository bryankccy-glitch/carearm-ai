"""真人語音評估的固定 25 句匿名錄音腳本。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from carearm.settings import PROJECT_ROOT


@dataclass(frozen=True)
class VoiceSentence:
    id: str
    text: str
    true_intent: str
    period: str | None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


VOICE_SCRIPT = (
    VoiceSentence("S001", "我要吃藥了", "REQUEST_MEDICATION", None),
    VoiceSentence("S002", "時間到了，請幫我拿藥", "REQUEST_MEDICATION", None),
    VoiceSentence("S003", "麻煩把藥盒拿給我", "REQUEST_MEDICATION", None),
    VoiceSentence("S004", "我現在想吃藥", "REQUEST_MEDICATION", None),
    VoiceSentence("S005", "請幫我拿早上的藥", "SPECIFY_MORNING", "morning"),
    VoiceSentence("S006", "我要早餐後那一盒藥", "SPECIFY_MORNING", "morning"),
    VoiceSentence("S007", "早晨的藥拿給我", "SPECIFY_MORNING", "morning"),
    VoiceSentence("S008", "我欲食朝早的藥仔", "SPECIFY_MORNING", "morning", "台語夾雜"),
    VoiceSentence("S009", "請幫我拿中午的藥", "SPECIFY_NOON", "noon"),
    VoiceSentence("S010", "我要午餐後那盒藥", "SPECIFY_NOON", "noon"),
    VoiceSentence("S011", "午間的藥可以拿來嗎", "SPECIFY_NOON", "noon"),
    VoiceSentence("S012", "請共我提中晝的藥仔", "SPECIFY_NOON", "noon", "台語夾雜"),
    VoiceSentence("S013", "請幫我拿晚上的藥", "SPECIFY_EVENING", "evening"),
    VoiceSentence("S014", "我要睡前那一盒藥", "SPECIFY_EVENING", "evening"),
    VoiceSentence("S015", "晚餐後的藥拿給我", "SPECIFY_EVENING", "evening"),
    VoiceSentence("S016", "我欲食暗頓的藥仔", "SPECIFY_EVENING", "evening", "台語夾雜"),
    VoiceSentence("S017", "我剛剛已經吃過藥了", "MEDICATION_ALREADY_TAKEN", None),
    VoiceSentence("S018", "藥已經吃完了，不用再拿", "MEDICATION_ALREADY_TAKEN", None),
    VoiceSentence("S019", "毋免矣，我食過藥矣", "MEDICATION_ALREADY_TAKEN", None, "台語夾雜"),
    VoiceSentence("S020", "先不要拿，取消", "CANCEL", None),
    VoiceSentence("S021", "算了，我等一下再說", "CANCEL", None),
    VoiceSentence("S022", "我頭暈", "UNKNOWN", None, "身體不適，系統不得當成取藥"),
    VoiceSentence("S023", "我胸口不舒服", "UNKNOWN", None, "身體不適，系統不得當成取藥"),
    VoiceSentence("S024", "幫我開電視", "UNKNOWN", None),
    VoiceSentence("S025", "嗯，我不知道要做什麼", "UNKNOWN", None),
)


def sentence_by_id(sentence_id: str) -> VoiceSentence:
    try:
        return next(sentence for sentence in VOICE_SCRIPT if sentence.id == sentence_id)
    except StopIteration as error:
        raise ValueError(f"未知的 sentence_id：{sentence_id}") from error


def write_voice_script(path: Path | None = None) -> Path:
    """重新產生給錄音者閱讀的 Markdown 表格。"""

    path = path or PROJECT_ROOT / "docs" / "voice_script.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# CareArm AI 真人語音錄音腳本",
        "",
        "> 研究模擬，非醫療器材。請照文字自然朗讀；不要說出姓名、電話、地址或病歷資料。",
        "",
        "每位錄音者可在安靜環境（`quiet`）與電視背景聲（`tv`）各錄一輪。檔名格式：`<sentence_id>_<condition>.wav`。",
        "",
        "| 編號 | 朗讀內容 | 真實意圖 | 時段 | 備註 |",
        "|---|---|---|---|---|",
    ]
    for sentence in VOICE_SCRIPT:
        lines.append(
            f"| {sentence.id} | {sentence.text} | `{sentence.true_intent}` | "
            f"{sentence.period or '—'} | {sentence.note or '—'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(write_voice_script())
