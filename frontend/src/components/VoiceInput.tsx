import { useEffect, useRef, useState } from "react";
import { Analysis, api, AsrStatus } from "../api";

// 瀏覽器內建語音辨識（Safari / Chrome 的 Web Speech API）
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecognition = any;
const SpeechRecognitionCtor: (new () => AnyRecognition) | undefined =
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

type Engine = "browser" | "whisper";

const BROWSER_ERRORS: Record<string, string> = {
  "not-allowed": "瀏覽器沒有麥克風權限：點網址列左側的權限設定，允許麥克風後再試。",
  "service-not-allowed": "系統不允許語音辨識：macOS 請到「系統設定 → 鍵盤 → 聽寫」開啟聽寫，或改用 Whisper。",
  "no-speech": "沒有聽到聲音，請靠近麥克風再說一次。",
  "audio-capture": "找不到麥克風，請確認麥克風已連接。",
  network: "瀏覽器語音辨識需要網路（Chrome 會送到 Google 伺服器）。請檢查網路或改用 Whisper。",
  aborted: "已取消錄音。",
};

interface Props {
  clock: string;
  disabled: boolean;
  asr?: AsrStatus;
  onResult: (a: Analysis) => void;
  onBusy: (label: string | null) => void;
}

export function VoiceInput({ clock, disabled, asr, onResult, onBusy }: Props) {
  const browserOk = Boolean(SpeechRecognitionCtor);
  const [engine, setEngine] = useState<Engine>(browserOk ? "browser" : "whisper");
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [seconds, setSeconds] = useState(0);
  const recRef = useRef<AnyRecognition | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<number | null>(null);
  const MAX_SECONDS = 8;

  useEffect(() => () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
  }, []);

  const send = async (text: string, extra: Parameters<typeof api.command>[2]) => {
    onBusy("分析指令");
    try {
      const out = await api.command(text, clock, extra);
      onResult(out);
      setNote({ text: `辨識結果：「${text}」`, tone: "info" });
    } catch (e) {
      setNote({ text: `分析失敗：${e instanceof Error ? e.message : String(e)}`, tone: "error" });
    } finally {
      onBusy(null);
    }
  };

  const startTimer = (onTimeout: () => void) => {
    setSeconds(0);
    timerRef.current = window.setInterval(() => {
      setSeconds((s) => {
        if (s + 1 >= MAX_SECONDS) onTimeout();
        return s + 1;
      });
    }, 1000);
  };
  const stopTimer = () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
  };

  const startBrowser = () => {
    const rec = new SpeechRecognitionCtor!();
    rec.lang = "zh-TW";
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    let finalText = "";
    let lastText = "";          // Safari 手動停止時常不給 final 結果，所以保留最後的暫時結果
    let finalConf: number | undefined;
    let errorCode: string | null = null;
    rec.onresult = (ev: AnyRecognition) => {
      let text = "";
      for (let i = 0; i < ev.results.length; i += 1) {
        text += ev.results[i][0].transcript;
        if (ev.results[i].isFinal) {
          finalText = text;
          finalConf = ev.results[i][0].confidence;
        }
      }
      lastText = text;
      setInterim(text);
    };
    rec.onerror = (ev: AnyRecognition) => {
      errorCode = ev.error;
      setNote({ text: BROWSER_ERRORS[ev.error] ?? `語音辨識錯誤：${ev.error}`, tone: "error" });
      void api.clientLog(`瀏覽器語音辨識錯誤：${ev.error}`, "ERROR", { engine: "browser", message: ev.message });
    };
    rec.onend = () => {
      stopTimer();
      setListening(false);
      setInterim("");
      const text = (finalText || lastText || "").replace(/\s+/g, "");
      if (text) {
        void api.clientLog(`瀏覽器語音辨識完成：「${text}」`, "INFO", { engine: "browser", final: Boolean(finalText), confidence: finalConf });
        void send(text, { source: "browser-asr", asr_engine: "瀏覽器語音辨識（zh-TW）", asr_confidence: finalConf });
      } else if (!errorCode) {
        setNote({ text: "沒有辨識到內容：請靠近麥克風、說完停一秒再按結束；也可以按「麥克風測試」確認收音。", tone: "error" });
        void api.clientLog("瀏覽器語音辨識結束但沒有結果", "WARN", { engine: "browser" });
      }
    };
    rec.onstart = () => void api.clientLog("瀏覽器語音辨識開始", "INFO", { engine: "browser" });
    recRef.current = rec;
    setNote(null);
    setListening(true);
    try {
      rec.start();
    } catch (e) {
      setListening(false);
      setNote({ text: `無法啟動瀏覽器語音辨識：${String(e)}`, tone: "error" });
      void api.clientLog(`無法啟動瀏覽器語音辨識：${String(e)}`, "ERROR", { engine: "browser" });
      return;
    }
    startTimer(() => rec.stop());
  };

  const startWhisper = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stopTimer();
        stream.getTracks().forEach((t) => t.stop());
        setListening(false);
        const blob = new Blob(chunks, { type: rec.mimeType });
        void api.clientLog(`Whisper 錄音完成（${blob.type || "未知格式"}，${blob.size} bytes）`, "INFO", { engine: "whisper" });
        if (blob.size < 1000) {
          setNote({ text: "沒有錄到聲音，請靠近麥克風再說一次。", tone: "error" });
          return;
        }
        setNote({ text: asr?.loaded ? "Whisper 辨識中…" : "Whisper 辨識中…第一次要下載模型，可能需要幾分鐘。", tone: "info" });
        onBusy("語音辨識中");
        try {
          const out = await api.audio(blob, clock);
          onResult(out);
          setNote({ text: `辨識結果：「${out.intent.transcript}」`, tone: "info" });
        } catch (e) {
          setNote({ text: `Whisper 辨識失敗：${e instanceof Error ? e.message : String(e)}`, tone: "error" });
        } finally {
          onBusy(null);
        }
      };
      mediaRef.current = rec;
      setNote(null);
      setListening(true);
      rec.start();
      startTimer(() => rec.state !== "inactive" && rec.stop());
    } catch (e) {
      setNote({ text: BROWSER_ERRORS["not-allowed"], tone: "error" });
      void api.clientLog(`無法開啟麥克風：${String(e)}`, "ERROR", { engine: "whisper" });
    }
  };

  // 麥克風測試：錄 2 秒，量最大音量，確認麥克風真的有收到聲音
  const micTest = async () => {
    setNote({ text: "麥克風測試中：請說幾個字…", tone: "info" });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Float32Array(analyser.fftSize);
      let peak = 0;
      const end = performance.now() + 2000;
      while (performance.now() < end) {
        analyser.getFloatTimeDomainData(buf);
        for (const v of buf) peak = Math.max(peak, Math.abs(v));
        await new Promise((r) => setTimeout(r, 50));
      }
      stream.getTracks().forEach((t) => t.stop());
      await ctx.close();
      const level = Math.round(peak * 100);
      const ok = level >= 5;
      setNote({ text: ok ? `麥克風正常（最大音量 ${level}%）。` : `麥克風幾乎沒有收到聲音（${level}%）：請檢查系統設定的輸入裝置與音量。`, tone: ok ? "info" : "error" });
      void api.clientLog(`麥克風測試：最大音量 ${level}%`, ok ? "INFO" : "WARN", { browserSR: browserOk });
    } catch (e) {
      setNote({ text: `無法開啟麥克風：${String(e)}`, tone: "error" });
      void api.clientLog(`麥克風測試失敗：${String(e)}`, "ERROR");
    }
  };

  const toggle = () => {
    if (listening) {
      stopTimer();
      if (engine === "browser") recRef.current?.stop();
      else if (mediaRef.current?.state !== "inactive") mediaRef.current?.stop();
      return;
    }
    if (engine === "browser") startBrowser();
    else void startWhisper();
  };

  const whisperHint =
    engine !== "whisper" || !asr
      ? null
      : !asr.installed
        ? "Whisper 未安裝：pip install -r requirements-speech.txt，或改用瀏覽器辨識。"
        : asr.error
          ? `Whisper 載入失敗：${asr.error}`
          : asr.loading
            ? "Whisper 模型下載／載入中（第一次約 460 MB）。"
            : null;
  const whisperBlocked = engine === "whisper" && asr?.installed === false;

  return (
    <div className="voice">
      <button
        className={`mic ${listening ? "on" : ""}`}
        onClick={toggle}
        disabled={(disabled && !listening) || whisperBlocked}
        aria-pressed={listening}
        title="按下開始說話，說完再按一次"
      >
        <span className="mic-dot" aria-hidden />
        {listening ? `聆聽中 ${seconds}s・按此結束` : "說話"}
      </button>
      <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)} aria-label="語音辨識引擎" disabled={listening}>
        <option value="browser" disabled={!browserOk}>瀏覽器辨識{browserOk ? "" : "（不支援）"}</option>
        <option value="whisper">Whisper（本機）</option>
      </select>
      <button className="link small" onClick={micTest} disabled={listening}>麥克風測試</button>
      {(listening && interim) || note || whisperHint ? (
        <p className={`voice-note ${note?.tone === "error" ? "err" : ""}`} role="status">
          {listening && interim ? `「${interim}」` : note?.text ?? whisperHint}
        </p>
      ) : null}
    </div>
  );
}
