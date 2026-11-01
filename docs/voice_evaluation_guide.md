# 真人語音評估操作指南

本工具只用於 CareArm AI 研究模擬，不會判斷真實藥物或劑量。錄音前先取得參與者同意，並使用匿名代碼。

## 1. 準備錄音

1. 閱讀 `docs/voice_consent.md`，依研究單位要求完成同意程序。
2. 讓錄音者依 `docs/voice_script.md` 自然朗讀。
3. 建議錄成單聲道 PCM WAV；每個檔案只放一句話。
4. 可分成 `quiet`（安靜）與 `tv`（電視背景聲）兩種條件。

例如匿名受試者 `spk001` 的第 9 句：

```text
data/voice/spk001/S009_quiet.wav
data/voice/spk001/S009_tv.wav
```

請勿把姓名放進資料夾、檔名或 manifest。

## 2. 填寫 manifest

在 `data/voice/manifest.csv` 每個音檔填一列：

```csv
speaker_id,age_group,gender,condition,sentence_id,file
spk001,65+,undisclosed,quiet,S009,spk001/S009_quiet.wav
spk001,65+,undisclosed,tv,S009,spk001/S009_tv.wav
```

程式會檢查檔名是否符合匿名代碼、句子編號與錄音條件；欄位出現 `name` 或「姓名」會直接拒絕。

## 3. 先用假轉錄器檢查流程

假轉錄器不會載入 Whisper，也不會聽音檔內容；它直接用腳本原文確認資料夾、manifest、意圖分類器與輸出流程能跑通：

```bash
cd /Users/chengchenyou/Desktop/ml/carearm-ai
source .venv/bin/activate
python -m carearm.speech.evaluate_real --fake-transcriber
```

輸出位置：

```text
results/voice_eval/fake/summary.json
results/voice_eval/fake/per_utterance.csv
```

## 4. 執行真正 Whisper 評估

先安裝選用語音套件：

```bash
pip install -r requirements-speech.txt
```

small 模型：

```bash
python -m carearm.speech.evaluate_real --model small
```

medium 模型：

```bash
python -m carearm.speech.evaluate_real --model medium
```

第一次執行可能需要下載模型。正式錄音資料不應上傳到公開儲存空間。

## 5. 看懂輸出

- `CER`：字元錯誤率；0 代表逐字相同，越低越好。計算前會先簡轉繁並移除空白、標點。
- `intent_accuracy`：意圖分類正確比例。
- `ECE`：分類信心和實際正確率的差距，越低越好；樣本少時不穩定。
- `mean_latency_s`：每句 Whisper 轉錄平均耗時（秒）。
- `by_condition`：分別比較安靜與電視背景聲。
- `by_age_group`：依 manifest 的年齡組比較。

`per_utterance.csv` 保留每句參考文字、轉錄、意圖、信心、CER 與延遲，方便查哪一句失敗。不要只報整體平均；台語句、身體不適句與背景聲條件應另外檢查。
