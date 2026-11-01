# 實驗重現指南

## 1. 一行重跑全部

```bash
cd ~/Desktop/ml/carearm-ai && source .venv/bin/activate
python -m carearm.speech.intent             # （可略）重新訓練意圖分類器 → models/intent_*
python -m carearm.perception.calibration    # （可略）重建 240 張視覺資料集並校正 → models/color_* vision_*
python -m carearm.experiments.runner --run-id main
```

- 預設 300 個情境（train 150／calibration 60／test 90），`base_seed=20261019`，都寫在 `configs/default.yaml`。
- 擴充：`--n 600`；換一批情境：`--seed 1234`；另存一份結果：`--run-id exp600`。
- 中斷後用同一個 `--run-id` 重跑會從 `perception.jsonl` 與 `executions.json` 接續。
- 參考時間：Linux 2 核雲端機約 15–20 分鐘；M4 單機預期相近或更快（未實測）。

## 2. 產出檔案（`results/<run-id>/`）

| 檔案 | 內容 |
|---|---|
| `scenarios.csv` | 每個情境的完整參數（seed、意圖、轉錄、時間、條件、正確答案） |
| `perception.jsonl` | 每個情境的視覺偵測、意圖、排程結果，以及只供評估用的 ground truth 位置 |
| `executions.json` | 每一次實際 MuJoCo 物理執行的結果（快取） |
| `risk_training.json` | 風險模型的訓練／校正樣本數、正例數、係數、門檻 |
| `test_decisions.csv` | 測試集 × 每個策略的決策、執行與正確與否 |
| `metrics.csv` | 各策略指標（定義見 RESEARCH.md §5） |
| `summary.json` | 指標、風險 ECE、風險—覆蓋曲線資料 |
| `failure_cases.md` | 自動執行但結果不正確的案例 |
| `carearm_experiment.sqlite` | runs／scenarios／decisions／executions 四張表 |
| `figures/strategy_comparison.png` | 主要策略比較 |
| `figures/ablation.png` | 消融 |
| `figures/risk_coverage.png` | 風險—覆蓋曲線 |
| `figures/calibration.png` | 風險模型（校正前後）與視覺分數的可靠度圖 |
| `figures/confusion_matrices.png` | 意圖混淆矩陣；決策 × 正確答案 |

只重畫圖：`python -m carearm.experiments.report results/main`

## 3. 防止資料洩漏的設計

| 資料 | 用於 | seed 範圍 |
|---|---|---|
| 意圖語料（train／calibration／eval） | 意圖分類器、temperature | 7001–7003（不含 held-out 句型） |
| 視覺影像資料集 | 顏色分類器、temperature | 900000–900239 |
| 實驗 train | 風險模型係數 | 20261019 + 0…149 |
| 實驗 calibration | Platt scaling、執行門檻 | 20261019 + 150…209 |
| 實驗 test | 只算指標 | 20261019 + 210…299 |

## 4. 本次實際結果

已完成兩次：`results/main`（300 情境）與 `results/exp1200`（`--run-id exp1200 --n 1200`）。完整數字與解讀見 HANDOFF.md §4。所有數字都來自實際完成的模擬，沒有手動填入。

注意：兩次實驗的情境 index 0–299 內容相同，但切分不同（main 的測試集 210–299 在 exp1200 是訓練集）。兩次實驗各自內部沒有洩漏，但**不要把兩次的測試結果混在一起統計**。

`models/risk_model.joblib` 是**最後完成**的實驗所存的 D 模型（目前是 exp1200）；儀表板與 demo 都使用它。各實驗的模型另存為 `models/<run-id>_<策略>.joblib`。

配對顯著性檢定（符號檢定）可用：
```bash
python - <<'PY'
import pandas as pd; from scipy.stats import binomtest
df = pd.read_csv("results/exp1200/test_decisions.csv")
ok = lambda s: df[df.strategy == s].set_index("scenario_id").apply(lambda r: bool(r.executed and r.correct == True), axis=1)
d, c = ok("D_calibrated_risk"), ok("C_fixed_threshold")
a, b = int((d & ~c).sum()), int((~d & c).sum()); print(a, b, binomtest(a, a + b).pvalue)
PY
```
