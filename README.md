# CareArm AI：長照取藥機械手臂研究模擬系統

**研究主題**：不確定性感知的多模態感知與風險敏感決策，應用於長照情境的機械手臂取藥盒任務
（Uncertainty-Aware Multimodal Perception and Risk-Sensitive Decision-Making for Robotic Medication Box Retrieval in Elderly Care）

> ⚠️ 本系統是**研究模擬**，不是醫療器材。黃／藍／紫對應早／中／晚只是可設定的研究假設，不是醫療標準。系統不做任何真實藥物或劑量決策；不安全的比較基線只允許在模擬中評估。

完全初學者請先讀 [`BEGINNER_GUIDE.md`](BEGINNER_GUIDE.md)。目前狀態與接手方式看 [`HANDOFF.md`](HANDOFF.md)，每次實際修改看 [`CHANGELOG.md`](CHANGELOG.md)。

本專案建立在上層 `ml/` 既有的形狀分類研究之上，**直接沿用**其已驗證的模擬元件：

| 沿用的上層元件 | 在 CareArm 中的用途 |
|---|---|
| gym-hil Franka Panda + Robotiq 2F-85 場景（`scene.xml` + MjSpec 執行期擴充，同 `shape_sorting_env.py` 作法） | 加入三個藥盒、遞送區、桌面、俯視相機 |
| `panda_pick_place.PandaPickPlaceController`（TCP 回授閉迴路 + opspace 控制） | 夾取、抬升、搬運、放下的 `move_to` / `hold` |
| `config.py` 的 `PANDA_HOME_JOINTS`、`CARTESIAN_BOUNDS` | 手臂初始姿態與安全工作範圍 |
| 俯視相機位置（0.42, 0, 0.82）、fovy 52° | 同一套相機幾何，改用射線—平面交點做像素→世界座標 |

上層原有程式一行都沒有修改。

---

## 1. 環境需求

- macOS（Apple Silicon，M4 已列為目標）或 Linux
- Python **3.12**
- 不需要 NVIDIA GPU / CUDA
- Node.js 18 以上（只有要重新建置儀表板時才需要；已附建置好的 `frontend/dist`）

## 2. 安裝（專用虛擬環境，不動到 lerobot-env）

```bash
cd ~/Desktop/ml/carearm-ai
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 選用：真實麥克風／音檔語音辨識
pip install -r requirements-speech.txt
```

> 若系統沒有 `python3.12` 指令：可用既有 lerobot-env 的 Python 3.12 建立**新的**虛擬環境（不會改動 lerobot-env 本身）：
> `/Users/chengchenyou/lerobot-env/bin/python -m venv .venv`，或 `brew install python@3.12`。

> **MuJoCo 版本很重要**：需要 ≥3.3.3（`MjSpec.delete`）且 <3.5（gym-hil 0.1.x 使用 `MjData.qM`，3.5 之後被移除）。`requirements.txt` 固定為 3.4.0。

## 3. 指令一覽

| 目的 | 指令 |
|---|---|
| 第一個里程碑：Panda 夾一個藥盒放到遞送區（含 3D 視窗） | `mjpython -m carearm.demo --period noon --viewer --confirm` |
| 同上，無視窗 | `python -m carearm.demo --period noon --confirm` |
| 完整語音文字 → 決策 → 執行 | `python -m carearm.demo --text "我要吃藥了" --time 18:30` |
| 隨機擺放 | `python -m carearm.demo --period evening --randomize 7 --confirm` |
| 取藥後再模擬「我吃完藥了」把空盒收到回收區 | `mjpython -m carearm.demo --period noon --viewer --confirm --then-finish` |
| 一鍵啟動儀表板（自動切資料夾、啟動 .venv、開瀏覽器） | `~/Desktop/ml/carearm-ai/start.sh` |
| 儀表板（瀏覽器） | `python -m carearm.api.app` → 開 http://127.0.0.1:8000 |
| 儀表板 + MuJoCo 原生 3D 視窗 | `mjpython -m carearm.api.app --viewer` |
| 訓練意圖分類器（已附模型） | `python -m carearm.speech.intent` |
| 產生視覺校正資料集並校正（已附模型） | `python -m carearm.perception.calibration` |
| 完整 300 情境實驗 + 圖表 | `python -m carearm.experiments.runner --run-id main` |
| 只重畫圖表 | `python -m carearm.experiments.report results/main` |
| 自動化測試（macOS，包含相機 renderer） | `mjpython -m pytest -q tests` |
| 重新建置儀表板 | `cd frontend && npm install && npm run build` |

`--confirm` 代表「若系統請求確認，以照護者身分確認」。硬性安全限制失敗（例如重複取藥、排程衝突、識別碼不符）時，確認也不能覆蓋。

## 4. 儀表板使用方式

啟動：`~/Desktop/ml/carearm-ai/start.sh`（或 `python -m carearm.api.app`），瀏覽器開 http://127.0.0.1:8000。

**頂列**：手臂狀態與執行階段（n/9）、TCP 座標、夾爪閉合度、模擬時間、今日服藥數，以及暫停與緊急停止。

**操作台**
1. **相機**：「偵測結果」（俯視影像 + OpenCV 偵測框、顏色、校正後信心、識別碼點數）、「側視即時」「俯視即時」和「手腕相機」。按執行時會自動切到側視；藥盒送達後可切到 wrist RGB 查看服藥驗證。
2. **指令列**：
   - 「說話」按鈕預設用**瀏覽器語音辨識**（Safari／Chrome 內建，zh-TW，不需下載模型）。按一下開始，說完再按一次（最長 8 秒自動停止），辨識中的文字會即時顯示。
   - 下拉選單可改用 **Whisper（本機）**：需要 `pip install -r requirements-speech.txt`，第一次會下載約 460 MB 模型。
   - 也可以直接輸入文字或點範例句；右側可設定模擬時間。
3. **決策卡**：大字顯示執行／請求確認／停止，下面五列是語音、視覺、排程、安全規則、風險的數值；「決策細節」展開每條規則、理由、門檻與處理時間。
4. **今日服藥**：三個時段的狀態（未到時段／服藥時段／已送達／已服用／已回收），可手動標記；「開始新的一天」清除今日紀錄。
5. **最近一次執行**：抬升高度、放置誤差、模擬時間、各階段時間條，以及是否撞動其他盒（全部來自物理模擬）。
6. **吃完藥回收空盒**：說「我吃完藥了」（也接受「吃完了」「幫我把空盒收走」），按「執行回收空盒」就把空盒移到灰色回收區並記錄已服藥。遞送區有兩盒以上時，請說「早上的空盒收走」這類句子指定。
7. 「實驗條件」可設定光線、陰影、遮擋、相機傾斜、相似顏色並重新擺放。
8. **手腕相機服藥驗證**：送達後自動保存「藥丸還在杯中」的基準影像。此時只標示「已送達」，不會立刻算成已服用；wrist 判讀預期藥丸都已取走後，才更新今日服藥。可重新拍照，或用研究模擬按鈕測試取走 1 顆、全部服用、掉落 1 顆。按鈕只移動 MuJoCo 藥丸，判讀仍由 wrist RGB 前後影像差異完成；部分服用或疑似掉落會回報總部。剩餘 0 顆時，三個搬動藥丸的按鈕會停用。

> 「今日服藥」是排程／流程狀態；「手腕相機服藥驗證」才是影像證據。研究模擬只能證明藥丸是否從杯中消失或疑似掉落，不能證明真人已吞服。

**實驗與模型**：各策略指標表、五張研究圖表、意圖／顏色模型的準確率與校正誤差、風險模型門檻與樣本數。

**事件紀錄**：可依來源篩選，「匯出 CSV」下載完整紀錄。

### 語音疑難排解

| 狀況 | 處理 |
|---|---|
| 瀏覽器辨識顯示「沒有麥克風權限」 | 點網址列左側的權限圖示，允許麥克風；Safari 在「設定 → 網站 → 麥克風」 |
| Safari 顯示「系統不允許語音辨識」 | macOS「系統設定 → 鍵盤 → 聽寫」開啟聽寫，或改用 Chrome／Whisper |
| Chrome 顯示需要網路 | Chrome 的語音辨識會送到 Google 伺服器；離線時改用 Whisper |
| Whisper 一直「下載／載入中」或失敗 | 在終端機執行 `python -m carearm.speech.asr` 看完整訊息（會顯示下載進度與 compute type） |

## 5. 專案結構

```
carearm-ai/
├── carearm/
│   ├── settings.py            設定載入；把上層 ml/ 加入 import 路徑
│   ├── simulation/scene.py    MuJoCo 場景（沿用 gym-hil Panda）
│   ├── robot/executor.py      取藥執行器（沿用 PandaPickPlaceController）＋物理成功驗證
│   ├── perception/            OpenCV 偵測、像素→世界、顏色信心校正
│   ├── speech/                意圖分類（含 temperature scaling）、ASR 雜訊模型、Faster-Whisper
│   ├── decision/              排程、硬性安全限制、風險模型、A/B/C/D 策略
│   ├── experiments/           情境產生器、實驗執行器、指標與圖表
│   ├── database/store.py      SQLite 紀錄
│   ├── api/                   FastAPI + 單執行緒模擬服務
│   ├── pipeline.py            感知—決策共用管線
│   └── demo.py                命令列示範
├── configs/default.yaml       藥盒、顏色、排程、門檻、實驗參數
├── models/                    訓練好的意圖／顏色／風險模型與報告
├── results/                   實驗結果（SQLite、CSV、圖表）
├── frontend/                  React + TypeScript + Vite 儀表板
├── tests/                     pytest
└── docs: README / HANDOFF / RESEARCH / ARCHITECTURE / EXPERIMENTS
```

## 6. 疑難排解

| 症狀 | 處理 |
|---|---|
| `AttributeError: 'MjData' object has no attribute 'qM'` | MuJoCo 太新；`pip install mujoco==3.4.0` |
| `'MjSpec' object has no attribute 'delete'` | MuJoCo 太舊；同上 |
| macOS 開 `--viewer` 出現 `launch_passive requires mjpython` | 改用 `mjpython`（在 `.venv/bin/` 內） |
| 原生視窗無法與儀表板同時開 | 先只用 `python -m carearm.api.app`；網頁的相機影像與遙測一樣來自同一個物理模擬 |
| 麥克風按鈕無法按、下方顯示「尚未安裝語音辨識」 | 在 `.venv` 啟動狀態下執行 `pip install -r requirements-speech.txt`，再重新啟動 `python -m carearm.api.app`。啟動後會在背景下載 Whisper small 模型（約 460 MB，只有第一次）；下載完成前錄音會等比較久 |
| 錄音後顯示「語音辨識失敗」 | 看提示訊息；常見原因是瀏覽器沒有允許麥克風（網址列左邊的權限設定），或模型還沒下載完 |
| 執行時想看手臂動作 | 相機面板切到「側視即時」（按「執行取藥」時會自動切換）；要看可旋轉的 3D 畫面請用 `mjpython ... --viewer` |
| Linux 無螢幕伺服器渲染失敗 | 設 `MUJOCO_GL=egl`（需 libEGL）或 `osmesa` |
| 儀表板顯示「後端未連線」 | 確認 `python -m carearm.api.app` 正在執行、port 8000 未被占用 |
| 決策一直是「請求確認」，風險 0.500 | `models/risk_model.joblib` 不存在或訓練資料無錯誤樣本；重新跑實驗 |
