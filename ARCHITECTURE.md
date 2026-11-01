# 系統架構

## 1. 資料流

```
  麥克風／音檔 ──► Faster-Whisper（選用）──┐
  文字輸入（除錯）─────────────────────────┤
                                          ▼
                                  轉錄文字（繁中）
                                          │
                     ┌────────────────────┴────────────────────┐
                     ▼                                         ▼
          意圖分類器（char n-gram LR）               關鍵字時段抽取（規則）
          + temperature scaling                              │
                     └──────────────┬──────────────────────────┘
                                    ▼
                 排程檢查（模擬時鐘、時段視窗、今日已服用）
                                    │
  MuJoCo 場景 ──► 手臂移出視野 ──► 俯視相機 RGB 影像
                                    │
                    OpenCV：HSV 分割 → 輪廓 → 凸包 → 黑點識別碼
                    → 射線—平面交點（相機內外參）→ 世界座標
                    → 顏色分類（LR + temperature scaling）
                                    │
                                    ▼
          ┌──────────── 決策引擎（Proposed D）────────────┐
          │ Layer 1 硬性安全限制（任一失敗 → 停止或詢問）  │
          │ Layer 2 校正後風險 → 執行 / 請求確認 / 停止   │
          └───────────────────────┬───────────────────────┘
                                  ▼
          取藥執行器（上層 PandaPickPlaceController 的閉迴路 move_to / hold）
                                  │
                                  ▼
          物理驗證：哪個盒子被抬起 8 cm？哪個盒子落在遞送區？其他盒子是否被撞動？
                                  │
                                  ▼
                       SQLite 事件／決策／執行紀錄
```

## 2. 模組

| 模組 | 職責 | 不可以做的事 |
|---|---|---|
| `simulation/scene.py` | 以 gym-hil `scene.xml` 為基礎，用 MjSpec 增加三個 free-joint 藥盒（含碰撞、質量）、視覺標籤與識別黑點、遞送區、桌面、陰影燈、遮擋板、俯視相機；可設定光線、陰影、遮擋、相機傾斜、相似顏色、第四個重複盒 | — |
| `robot/executor.py` | 繼承上層 `PandaPickPlaceController`，只加步數計數；以 waypoint 序列呼叫原本的 `move_to`／`hold`；用 ground truth 驗證成功 | 不使用預錄動畫；不以「到達姿態」當作成功 |
| `perception/detector.py` | 只吃影像 + 相機幾何 | **不得讀取**任何 MuJoCo 物體位置或標籤資訊（測試 `test_detector_does_not_read_simulator_objects` 會檢查原始碼） |
| `perception/calibration.py` | 以獨立 seed 範圍產生影像資料集；ground truth 只用於標註；訓練 + temperature scaling + ECE 報告 | 不使用實驗情境的 seed |
| `speech/intent.py` | 意圖分類、校正、ASR 雜訊模擬、held-out 句型 | Whisper 原始 log-prob 不當作機率 |
| `decision/schedule.py` | 時段視窗、寬限期、衝突、需澄清、重複取藥 | — |
| `decision/engine.py` | Layer 1 規則、特徵、`RiskModel`、策略 A/B/C/D | 低風險分數不能覆蓋 Layer 1 |
| `experiments/` | 情境產生、跑實驗、計算指標、畫圖 | 測試集標籤不進訓練、校正或門檻選擇 |
| `api/service.py` | 單一 worker 執行緒擁有 MuJoCo 與 OpenGL renderer；API 執行緒只排工作 | 不跨執行緒呼叫 renderer |
| `api/app.py` | REST 端點 + 提供 `frontend/dist` | — |

## 3. 安全邊界

**Layer 1：硬性安全限制**（`hard_constraints`）。只要任一條失敗就不會自動執行，也不計算風險：

| 規則 | 失敗時決策 |
|---|---|
| `no_retrieval_requested`：意圖是「已吃過」／取消 | 停止（已吃過只做記錄） |
| `no_retrieval_requested`：無法辨識意圖 | 請求確認 |
| `schedule_consistency`：指定時段不在合理時段內 | 停止 |
| `required_information`：未指定時段且排程無法唯一推定 | 請求確認（澄清） |
| `duplicate_retrieval`：今天已取過該時段 | 停止 |
| `target_detected`：影像中找不到該顏色 | 停止 |
| `box_identity`：標籤黑點數 ≠ 該時段盒子的識別碼 | 停止 |
| `unique_target`：有多個同色且同識別碼的盒子 | 停止 |

照護者「確認後執行」只適用於 **Layer 1 全部通過、由 Layer 2 判為不確定** 的情況；`SimulationService.execute` 會再檢查一次規則。

**Layer 2：風險估計**。Logistic Regression（13 個特徵：語音 4、視覺 6、排程 3）；在校正集做 Platt scaling，並選出最大的執行門檻，使校正集「自動執行者的錯誤率 ≤ 2%」。風險高於 0.5 為停止，介於中間為請求確認。

## 4. API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/health` | 健康檢查 + 免責聲明 |
| GET | `/api/status` | 手臂狀態、執行階段、TCP、藥盒（模擬器狀態，僅顯示用）、今日已服用 |
| POST | `/api/reset` | 重置場景（預設位置） |
| POST | `/api/randomize` | 隨機擺放 |
| POST | `/api/conditions` | 設定光線／陰影／遮擋／傾斜／相似色，可同時隨機擺放 |
| POST | `/api/command` | `{text, time}` → 完整分析（不執行） |
| POST | `/api/select_period` | `{period, time}` → 等同說「幫我拿 X 的藥」 |
| POST | `/api/audio` | multipart 音檔 → Faster-Whisper → 分析（未安裝時回 501） |
| POST | `/api/execute` | `{caregiver_confirmed}`；決策為停止或規則未通過時回 403 |
| GET | `/api/compliance` | 各藥盒目前的手腕相機驗證狀態 |
| POST | `/api/compliance/inspect` | 移到 wrist 檢查姿態並比較基準／目前影像 |
| POST | `/api/simulation/intake` | 研究模擬專用：移動藥丸後立即做影像驗證 |
| GET/POST | `/api/alerts`、`/api/alerts/{id}/ack` | 查詢與確認異常通報 |
| GET | `/api/hq/inbox` | 內建模擬總部收件匣 |
| POST | `/api/stop` `/api/pause` `/api/resume` | 緊急停止、暫停、繼續 |
| POST | `/api/taken` | 手動標記某時段已服用／未服用 |
| GET | `/api/camera.png?annotated=&live=` | 最新相機影像（含／不含標註） |
| GET | `/api/events` | 事件紀錄 |
| GET | `/api/experiments` | 所有已完成實驗的指標與圖表清單 |
| GET | `/api/experiments/{run}/figures/{png}` | 圖表檔案 |

## 5. 為什麼網頁沒有 3D 物理畫面

在瀏覽器裡重做 3D 會跟真正的物理引擎脫節，所以網頁只顯示 MuJoCo renderer 實際渲染的相機影像與遙測資料。要看 3D，請用 MuJoCo 原生視窗（`mjpython ... --viewer`）。兩者共用同一份 `MjData`。
