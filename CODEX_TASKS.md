# Codex 任務單（CareArm AI）

你是協助開發 CareArm AI 研究模擬系統的工程師。先讀 README.md、HANDOFF.md、RESEARCH.md 了解專案。
所有使用者可見文字、文件與註解用**繁體中文（台灣）**；程式識別字用英文。

## 環境
```bash
cd ~/Desktop/ml/carearm-ai
source .venv/bin/activate          # Python 3.12，已安裝 requirements.txt
python -m pytest -q tests          # 開始前先確認現有測試狀態
```
- 不要新增需要 NVIDIA/CUDA 的套件；新套件要有 Apple Silicon wheel，並寫進 requirements.txt。
- 不要捏造任何實驗數字；報告裡的數字只能來自實際執行的程式。

## 檔案分工（非常重要）
另一位開發者（Claude）正在同時修改下列檔案，**你不可以修改它們**，只能 import 使用：
- carearm/simulation/scene.py、carearm/perception/detector.py、carearm/robot/executor.py
- carearm/decision/engine.py、carearm/decision/schedule.py
- carearm/api/service.py、carearm/api/app.py、carearm/safety/*
- frontend/ 底下所有檔案、configs/default.yaml
- README.md、HANDOFF.md、ARCHITECTURE.md

你**可以新增或修改**：
- carearm/decision/conformal.py（新）
- carearm/experiments/stats.py（新）、carearm/experiments/report.py、carearm/experiments/runner.py
- carearm/speech/corpus.py（新）、carearm/speech/evaluate_real.py（新）
- tests/ 底下新增的測試檔（不要改既有測試）
- docs/ 底下新增的文件

每完成一個任務，在本檔最下方「完成紀錄」寫：改了哪些檔案、測試結果（實際輸出）、還有什麼沒做。

---

## 任務 1：Conformal 風險控制模組（carearm/decision/conformal.py）

目的：取代目前「Platt 校正 + 經驗門檻」，讓自動執行的錯誤率有有限樣本保證。

1. `ConformalRiskController`
   - `fit(scores, losses, alpha, B=1.0)`：scores 是每個校正樣本的風險分數（越高越危險），losses 是「若自動執行會不會出錯」（0/1）。
     決策規則：score ≤ λ 就自動執行，否則不執行（損失 0）。
     選出**最大的 λ** 使 `(n/(n+1)) * R̂(λ) + B/(n+1) ≤ alpha`，其中 R̂(λ) = 平均 [1(score ≤ λ) * loss]。
     沒有任何 λ 滿足時，λ = -inf（一律不自動執行）。
   - `decide(score) -> bool`、`info -> dict`（λ、n、alpha、校正集覆蓋率）。
2. 分類用的 split conformal 預測集合：
   - `fit_prediction_sets(cal_probs, cal_labels, alpha)` → 回傳 q̂（非一致性分數 = 1 − p(真實類別)）。
   - `predict_set(probs, q̂) -> list[類別]`。集合大小 > 1 代表要問使用者。
3. 策略類別 `ConformalRisk`：繼承 `carearm.decision.engine.CalibratedRisk`，Layer 1 硬性限制完全相同；
   Layer 2 用 RiskModel 的**未校正分數**（`raw_risk`）加 `ConformalRiskController` 決定執行或請求確認（> 0.5 仍停止）。
   名稱 `E_conformal`。
4. 在 `carearm/experiments/runner.py` 的 `train_risk_models` / `evaluate_test` 加入 `E_conformal`（alpha 預設 0.02，可用 `--alpha` 參數），
   用 **calibration split** 擬合，絕不使用 test。
5. 測試 `tests/test_conformal.py`：
   - 合成資料重複 200 次隨機切分，實際測試風險平均 ≤ alpha（允許小誤差），並報告平均覆蓋率。
   - λ 隨 alpha 單調；沒有可行 λ 時一律不執行；預測集合覆蓋率 ≥ 1 − alpha（重複切分平均）。

## 任務 2：統計檢定（carearm/experiments/stats.py）

1. `bootstrap_ci(df, strategy, metric_fn, n_boot=2000, seed=0)`：以「情境」為單位重抽樣，回傳點估計與 95% 信賴區間。
   對 metrics.csv 裡的主要指標（正確取藥率、錯盒率、執行中錯誤率、人工確認率、危險攔截率）都算。
2. `mcnemar_exact(df, a, b)`：以「自動且正確完成」做配對，回傳兩邊不一致數與精確 p 值（用 scipy.stats.binomtest）。
   比較 D 對 A/B/C、E_conformal 對 D。
3. 在 `report.py` 的 `build_report` 中呼叫，輸出 `stats.csv`、`pairwise_tests.csv`，並在 `strategy_comparison.png` 加上信賴區間誤差線。
4. 測試 `tests/test_stats.py`（用小型假資料驗證區間包含點估計、p 值在已知情況正確）。
5. 用既有結果驗證：`python -m carearm.experiments.report results/exp1200`，把實際輸出貼進完成紀錄。

## 任務 3：真人語音評估工具（不含錄音介面）

1. `carearm/speech/corpus.py`：25 句腳本，每句有 `id、text、true_intent、period`，涵蓋取藥、完成服藥、取消／模糊、身體不適（例如「我頭暈」），
   以及 3–4 句台語或台語夾雜（例如「我欲食暗頓的藥仔」）。輸出 `docs/voice_script.md`（給錄音者看的表）。
2. 資料夾格式：`data/voice/<speaker_id>/<sentence_id>_<condition>.wav` ＋ `data/voice/manifest.csv`
   （speaker_id、age_group、gender、condition[quiet/tv]、sentence_id、file）。**不可存姓名**。
3. `carearm/speech/evaluate_real.py`：
   - 用 `carearm.speech.asr.WhisperASR`（可選 small / medium）轉錄全部音檔。
   - 計算字錯誤率 CER（自己實作 Levenshtein，簡繁先統一）、意圖準確率、校正後信心的 ECE、平均延遲；依 condition、age_group 分組。
   - 輸出 `results/voice_eval/<model>/summary.json`、`per_utterance.csv`。
   - 可以用 `--fake-transcriber` 參數讓測試不需要模型。
4. `docs/voice_consent.md`：錄音同意書範本（研究目的、資料只存本機、可隨時退出、不記錄姓名）。
5. 測試 `tests/test_voice_eval.py`：CER 計算、manifest 讀取、fake transcriber 端到端。

---

## 完成紀錄
（Codex 每完成一項在此追加）

### 2026-10-23：任務 1 完成 — Conformal 風險控制模組

修改檔案：

- 新增 `carearm/decision/conformal.py`
  - `ConformalRiskController.fit/decide/info` 依指定有限樣本上界選最大門檻；無可行門檻時為 `-inf`，一律不自動執行。
  - 新增 split conformal 分類預測集合 `fit_prediction_sets`、`predict_set`，支援陣列類別索引與具名類別字典。
  - 新增策略 `ConformalRisk`（`E_conformal`）：完全沿用 D 的 Layer 1；Layer 2 使用 `RiskModel.raw_risk`，由 conformal 控制器決定執行／確認，raw risk > 0.5 時停止。
- 修改 `carearm/experiments/runner.py`
  - Logistic Regression 只用 train split 擬合；conformal 門檻只用 calibration split 的 raw risk 與物理 loss 擬合。
  - test split 只做最後評估，沒有參與模型或門檻擬合。
  - `E_conformal` 加入 `evaluate_test`；`risk_training.json` 記錄 alpha、lambda、校正集覆蓋率與有限樣本上界。
  - 新增 CLI 參數 `--alpha`（預設 0.02），並在 `run_meta.json` 記錄實際 alpha。
- 修改 `carearm/experiments/report.py`
  - 主要策略表、比較圖、決策矩陣、失敗案例與風險—覆蓋曲線加入 E。
  - 保持向後相容：舊結果沒有 E 時仍可正常重建報告。
- 新增 `tests/test_conformal.py`
  - 200 次隨機切分檢查平均測試風險。
  - 檢查門檻對 alpha 單調、無可行門檻不執行、預測集合覆蓋率與具名類別。
  - 檢查 E 使用 raw risk 做執行／確認／停止，且不能覆蓋重複取藥的 Layer 1 阻擋。

實際驗證：

- `.venv/bin/python -m pytest -q -s tests/test_conformal.py tests/test_decision.py tests/test_database.py tests/test_intent.py tests/test_schedule.py`
  - `35 passed in 2.11s`
  - 200 次切分平均測試風險：`0.0957`（測試 alpha `0.10`）
  - 平均自動執行覆蓋率：`0.8720`
  - split conformal 預測集合平均覆蓋率：`0.9030`（要求至少 `0.9000`）
- `py_compile`：`conformal.py`、`runner.py`、`report.py`、`test_conformal.py` 全部通過。
- 以 80 筆合成 train、60 筆合成 calibration 執行 `train_risk_models` 純邏輯整合測試：成功產生 `E_conformal` 與 `risk_training.json`；控制器記錄 `n=60`，且有限樣本上界 `0.09836 ≤ alpha 0.10`。
- 用 `results/exp1200/test_decisions.csv` 的暫存副本執行 `python -m carearm.experiments.report` 成功，確認舊結果沒有 E 時仍能產生報告；沒有改寫原本的 `results/exp1200`。
- 開始前已嘗試 `.venv/bin/python -m pytest -q tests`；目前 Codex 受限程序在 MuJoCo CGL 建立時出現 `invalid CoreGraphics connection`，62.60 秒後中止，當時尚未進入任何測試（`no tests ran`）。

尚未完成／驗證邊界：

- 沒有捏造 E 的正式實驗數字。完整 300／1200 情境 E 實驗需要可建立 MuJoCo renderer 的一般 Terminal，並會實際執行物理模擬；本次未在受限 CGL 程序內執行。
- 依檔案分工，未修改 `engine.py`、模擬、感知、機械手臂、API、safety、frontend、設定檔、README、HANDOFF 或 ARCHITECTURE。

### 2026-10-23：任務 2 完成 — Bootstrap 信賴區間與 McNemar 檢定

修改檔案：

- 新增 `carearm/experiments/stats.py`
  - `bootstrap_ci` 以 `scenario_id` 為單位做 2,000 次重抽樣，回傳點估計、95% 下界與上界。
  - 五個主要指標：正確取藥率、錯盒率、自動執行中的錯誤率、人工確認率、危險動作攔截率。
  - `mcnemar_exact` 以同一情境的「自動且正確完成」做配對，使用 `scipy.stats.binomtest` 回傳兩側不一致數與雙尾精確 p 值。
- 修改 `carearm/experiments/report.py`
  - 每次 `build_report` 輸出 `stats.csv` 與 `pairwise_tests.csv`，並寫進 `summary.json`。
  - `strategy_comparison.png` 的五個主要指標加入非對稱 bootstrap 95% CI 誤差線；整體任務錯誤率維持原始點估計圖。
  - 配對比較 D 對 A/B/C；資料含 E 時再比較 E 對 D。
- 新增 `tests/test_stats.py`
  - 驗證 CI 包含點估計、只抽指定策略的情境、已知平衡與單側不一致案例的 exact p 值。
- 更新既有 `results/exp1200/metrics.csv`、`summary.json`、`failure_cases.md` 與圖表，並新增 `stats.csv`、`pairwise_tests.csv`。這些輸出只由既有實驗資料重新計算，沒有重跑或捏造 MuJoCo 結果。

實際驗證：

- `.venv/bin/python -m pytest -q tests/test_stats.py tests/test_conformal.py tests/test_database.py tests/test_decision.py tests/test_intent.py tests/test_schedule.py` → `39 passed in 2.33s`。
- `.venv/bin/python -m pytest -q tests/test_stats.py tests/test_conformal.py tests/test_decision.py` → `24 passed in 2.20s`。
- `MPLCONFIGDIR=/tmp/carearm-matplotlib .venv/bin/python -m carearm.experiments.report results/exp1200` 成功。
- `results/exp1200/stats.csv` 共有 40 列（8 策略 × 5 指標）；例如 D 正確取藥率 `0.3917`，95% CI `[0.3389, 0.4417]`，D 自動執行中的錯誤率 `0.0070`，95% CI `[0.0000, 0.0224]`。
- `results/exp1200/pairwise_tests.csv`：D vs A 為 D-only `2`、A-only `23`、p=`1.943111e-05`；D vs B 為 `2`、`0`、p=`0.5`；D vs C 為 `42`、`0`、p=`4.547474e-13`。
- 已實際開圖檢查 `strategy_comparison.png`：五個主要面板都有可見誤差線，標籤與原數值正常。

尚未完成／驗證邊界：

- 舊的 exp1200 沒有策略 E 資料，所以本次 `pairwise_tests.csv` 不會偽造 E vs D；重跑含 E 的實驗後，報告程式會自動新增該列。
- 完整 pytest 仍受目前 Codex 程序的 macOS CGL 限制；沒有修改任何禁止檔案。

### 2026-10-23：任務 3 完成 — 真人語音評估工具

修改檔案：

- 新增 `carearm/speech/corpus.py`
  - 固定 25 句匿名錄音腳本，涵蓋一般取藥、早／中／晚指定、已服藥、取消、模糊、頭暈與胸口不適。
  - 包含 4 句台語或台語夾雜；每句都有 `id`、`text`、`true_intent`、`period`。
  - 可重新產生 `docs/voice_script.md`。
- 新增 `carearm/speech/evaluate_real.py`
  - 驗證匿名 manifest 與 `<speaker_id>/<sentence_id>_<condition>.wav` 格式，拒絕姓名欄位與不合法路徑。
  - 支援 Faster-Whisper small／medium；`--fake-transcriber` 不載入模型，可先驗證完整流程。
  - 自行實作 Levenshtein 與 CER；計算意圖準確率、校正後信心 ECE、平均轉錄延遲，並依 `condition`、`age_group` 分組。
  - 輸出 `results/voice_eval/<model>/summary.json` 與 `per_utterance.csv`。
- 新增 `docs/voice_script.md`、`docs/voice_consent.md`、`docs/voice_evaluation_guide.md`。
- 新增 `data/voice/manifest.csv`（只有匿名欄位表頭）與 `data/voice/README.md`（資料夾格式與範例）。
- 新增 `tests/test_voice_eval.py`：CER 簡繁／標點正規化、25 句內容、4 句台語、manifest 驗證、姓名欄位拒絕、fake transcriber 端到端輸出。

實際驗證：

- `.venv/bin/python -m pytest -q tests/test_voice_eval.py tests/test_stats.py tests/test_conformal.py tests/test_database.py tests/test_decision.py tests/test_intent.py tests/test_schedule.py` → `45 passed in 2.52s`。
- `py_compile`：本次三項任務新增／修改的 Python 模組全部通過。
- 將 `write_voice_script` 輸出到暫存檔後以 `cmp` 比較，與 `docs/voice_script.md` 完全一致。
- `python -m carearm.speech.evaluate_real --help` 正常列出 manifest、small／medium、output root 與 fake transcriber 參數。
- fake transcriber 端到端測試實際建立匿名 WAV 路徑與 manifest，成功輸出兩筆 `per_utterance.csv` 和分組 `summary.json`，平均 CER 為 0、意圖準確率為 1（只代表 fake 流程測試，不是真人語音成績）。

尚未完成／驗證邊界：

- 尚未錄製真人音檔，因此沒有捏造 Whisper small／medium 的 CER、意圖準確率、ECE 或延遲。
- 未下載或執行 Whisper 模型；取得同意並把匿名 WAV 與 manifest 放好後，才能產生正式真人語音結果。
- `docs/voice_consent.md` 是研究範本；正式收案前仍需由研究主持人、學校或研究倫理審查單位確認。
- 依分工未修改 ASR、意圖分類器、API、模擬、感知、機械手臂、safety、frontend、設定檔、README、HANDOFF 或 ARCHITECTURE。

### 2026-10-24：使用者解除原檔案分工，接手 Claude 未收尾項目

使用者明確要求「把 Claude 還沒做的也先做」，因此本節之後的工作不再受本檔開頭的 Claude 禁改清單限制；仍以目前 Mac 已同步內容為基礎，沒有重寫或覆蓋已完成的開口藥杯與通報功能。

完成內容：

- 補上開口藥杯／藥丸物理測試。
- 完成 Panda `handcam_rgb` 檢查姿態、wrist 即時畫面、服藥前基準與前後影像差異判讀。
- 完成未取、部分、全部取走、疑似掉落狀態，以及部分／掉落的總部通報。
- 新增 compliance API、研究用服藥／掉落模擬 API、React 驗證卡與操作按鈕。
- 更新 README、ARCHITECTURE、HANDOFF、CHANGELOG 與完全初學者講義。

實際驗證：

- `.venv/bin/mjpython -m pytest -q tests` → `88 passed, 10 warnings in 50.37s`。
- `npm ci`、`npm run build` 成功；Vite 顯示 34 modules transformed。
- 實際瀏覽器操作 12:00 中午取藥：Panda 物理驗證通過；wrist 卡顯示部分 1/2（90%），再取完顯示 2/2（99%）。
- 端到端掉落測試確認 `POSSIBLE_DROP` 並建立「服藥依從性異常」通報。

仍然不能宣稱：

- 尚未以真實 Panda、真實手腕相機或真人服藥驗證。
- 尚未錄製真人語音，也未重跑包含策略 E 的正式大型實驗。
