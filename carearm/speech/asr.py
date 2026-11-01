"""語音轉文字（Faster-Whisper，Apple Silicon 以 CPU int8 執行）。

- Whisper 的原始 log-prob 不被當作校正機率；系統只使用轉錄文字，
  信心由 speech.intent 的校正後意圖分類器提供。
- 若未安裝 faster-whisper，會丟出 ASRUnavailable，介面改用文字輸入（除錯模式）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# Whisper 常輸出簡體字；若有 OpenCC 用 s2twp，否則以取藥相關常用字對照表轉換
_S2T = str.maketrans({
    "药": "藥", "帮": "幫", "吃": "吃", "过": "過", "经": "經", "晚": "晚", "饭": "飯", "后": "後", "这": "這",
    "个": "個", "时": "時", "间": "間", "们": "們", "给": "給", "盒": "盒", "还": "還", "没": "沒", "说": "說",
    "开": "開", "关": "關", "灯": "燈", "电": "電", "视": "視", "气": "氣", "样": "樣", "点": "點", "现": "現",
    "该": "該", "请": "請", "麻": "麻", "烦": "煩", "刚": "剛", "才": "才", "里": "裡", "么": "麼", "吗": "嗎",
    "觉": "覺", "睡": "睡", "前": "前", "餐": "餐", "要": "要", "拿": "拿", "早": "早", "午": "午", "中": "中",
})


class ASRUnavailable(RuntimeError):
    pass


@dataclass
class Transcription:
    text: str
    engine: str
    language: str | None
    avg_logprob: float | None      # Whisper 原始值，僅記錄，不當作機率使用


def to_traditional(text: str) -> str:
    try:
        from opencc import OpenCC  # type: ignore

        return OpenCC("s2twp").convert(text)
    except Exception:
        return text.translate(_S2T)


def is_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


class WhisperASR:
    _lock = threading.Lock()
    _model = None
    status = {"installed": False, "loaded": False, "loading": False, "error": None, "compute_type": None, "model": None}

    @classmethod
    def warmup_async(cls, model_size: str = "small") -> None:
        """背景預先載入模型（第一次會從網路下載約 460 MB），避免第一次錄音等很久。"""

        cls.status["installed"] = is_installed()
        if not cls.status["installed"] or cls._model is not None:
            return

        def run() -> None:
            cls.status["loading"] = True
            try:
                cls(model_size)._load()
                cls.status["loaded"] = True
            except Exception as error:  # noqa: BLE001
                cls.status["error"] = str(error)
            finally:
                cls.status["loading"] = False

        threading.Thread(target=run, name="whisper-warmup", daemon=True).start()

    def __init__(self, model_size: str = "small") -> None:
        self.model_size = model_size

    def _load(self):
        with self._lock:
            if WhisperASR._model is None:
                try:
                    from faster_whisper import WhisperModel  # type: ignore
                except ImportError as error:
                    raise ASRUnavailable("未安裝 faster-whisper：pip install faster-whisper（或改用文字輸入）") from error
                last_error = None
                # Apple Silicon 一般支援 int8；若不支援就退回 float32
                for compute_type in ("int8", "float32"):
                    try:
                        WhisperASR._model = WhisperModel(self.model_size, device="cpu", compute_type=compute_type)
                        WhisperASR.status.update(compute_type=compute_type, model=self.model_size, error=None)
                        break
                    except ValueError as error:
                        last_error = error
                if WhisperASR._model is None:
                    raise RuntimeError(f"Whisper 模型載入失敗：{last_error}")
        return WhisperASR._model

    def transcribe(self, audio_path: str) -> Transcription:
        model = self._load()
        segments, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,
            initial_prompt="以下是繁體中文的取藥語音指令，例如：我要吃藥了、幫我拿早上的藥、我剛剛已經吃過藥了。",
        )
        segs = list(segments)
        text = to_traditional("".join(s.text for s in segs).strip().replace(" ", ""))
        logprob = float(sum(s.avg_logprob for s in segs) / len(segs)) if segs else None
        return Transcription(text=text, engine=f"faster-whisper-{self.model_size}", language=info.language, avg_logprob=logprob)


def main() -> None:
    """診斷：python -m carearm.speech.asr [音檔路徑]"""

    import sys
    import time as _t

    print(f"faster-whisper 已安裝：{is_installed()}")
    if not is_installed():
        print("請執行：pip install -r requirements-speech.txt")
        return
    started = _t.time()
    print("載入 Whisper small 模型（第一次會下載約 460 MB）…")
    asr = WhisperASR()
    asr._load()
    print(f"模型載入完成：compute_type={WhisperASR.status['compute_type']}，耗時 {_t.time() - started:.1f} 秒")
    if len(sys.argv) > 1:
        tr = asr.transcribe(sys.argv[1])
        print(f"辨識結果：「{tr.text}」（語言 {tr.language}）")
    else:
        print("可加上音檔路徑測試辨識，例如：python -m carearm.speech.asr test.m4a")


if __name__ == "__main__":
    main()
