# CareArm AI 變更紀錄

本檔只記錄實際完成的修改。每筆必須包含修改內容、驗證證據與尚未完成事項，不能把計畫寫成已完成。



## 2026-11-24 手腕旋轉在藥盒靠近時的比較實驗（Claude）

- 檔案：新增 `scripts/bench_grasp_yaw.py`、`results/grasp_yaw_tight/grid.json`；程式行為未改。
- 設計：目標盒放 (0.50, 0)，夾爪開合方向（y）兩側各放一個不旋轉的鄰盒；中心距 56／60／65／70／80／100 mm × 目標盒角度 0／15／30／40／45°，各跑一次 align_yaw=true／false。真正 MuJoCo 物理（只停用 OpenGL），成功判定沿用 executor（含鄰盒位移 ≤ 1.5 cm）。
- 結果：對齊 20/30、固定 14/30。差異全部出現在鄰盒很近且角度大時（60 mm 的 40°／45°、65 mm 的 30°／40°／45°、70 mm 的 40°）：對齊成功、固定失敗，共 6 組；沒有任何一組是固定成功而對齊失敗。56 mm 兩者全失敗；60–65 mm 且角度 ≤ 15–30° 兩者也都失敗（原因皆為「沒有藥盒被抬高 8 cm」）；≥ 80 mm 兩者全成功。
- 限制：每個條件只跑一次（模擬為確定性、同一 seed），鄰盒固定不旋轉且只放在 y 方向，不能當成機率估計；80 mm／0°／對齊那一組顯示 612 秒，已查明是電腦在 12:24:20 因閒置睡眠 612 秒（pmset 紀錄），不是模擬變慢；單獨重跑與依原順序重跑都約 1.8 秒、結果完全相同。腳本已改為同時記錄不含睡眠的 cpu_sec，長時間執行請加 caffeinate。結論只能說「在這個網格中，鄰盒靠近且角度大時，對齊讓 6 組由失敗變成功」，要宣稱提升成功率需預先登記並在隨機情境中重複。

## 2026-11-24 藥丸計數：擠在一起時標記不確定、辨認大顆膠囊（Claude）

- 檔案：`carearm/perception/pills.py`、`configs/default.yaml`（新增 `adherence.max_capsule_mm2: 200`）、新增 `tests/test_pill_clumps.py`、新增 `scripts/bench_pill_count.py`（基準測驗）。
- 問題（實測）：在真實 MuJoCo 手腕 RGB-D 影像上隨機擺放 0–5 顆藥丸，原版完全算對 65–67%，且**算錯的 40 次全部回報為確定**（uncertain=False），決策層無從得知要再看一次。原因：(1) 模擬最大膠囊約 180 mm²，超過單顆上限 135 mm² 而被算成 2 顆；(2) 相距約 7 mm 的兩顆在深度影像黏成一塊。
- 行為：凸、只有一個高度峰、面積 ≤ 200 mm² 的區塊算一顆大膠囊；判定為「靠在一起」的區塊一律 `uncertain=True` 並附原因，交由上層再看一次或請照護者確認。空盒與單顆判定不變。
- 驗證：`.venv/bin/mjpython scripts/bench_pill_count.py --n 40 --seed 7／11`（各 120 情境）：完全算對 67.5%／72.5%；確定但算錯 6／4（原版 40／120）；標記不確定 55／54；空盒誤判 0。新測試在舊程式上 2 項皆失敗、新程式通過。全套 `mjpython -m pytest -q tests` → 209 passed。
- 代價與限制：約 45% 情境需再看一次或照護者確認（集中在 4–5 顆擠在一起）；仍有少數「兩顆黏成一塊且面積小於 135 mm²」被確定地算成 1 顆。基準情境為隨機緊密擺放，比實際使用嚴苛。試過但未採用：一律找高度峰、降低峰值門檻（多算，完全算對降到 50%）、降低相機高度（手腕相機在 TCP 上方 14.8 cm，無法再降）。

## 2026-11-24 Claude 審查更正（優先於下方 Codex 紀錄）

- **相鄰藥丸分水嶺計數已撤回**：`pills.py` 退回原版、刪除 `tests/test_pills_touching.py`。原因：新演算法在真實 MuJoCo wrist 深度影像上過度分割（`test_adherence.py` evening 3 顆算成 5、noon 2 顆算成 3），只在合成影像測試通過。「靠在一起差 1 顆」仍為已知限制。
- 保留手腕 yaw 對齊（`detector.py`、`executor.py`、`grasp.align_yaw`）。注意：固定腕姿在 ±40° 也成功，目前沒有證據顯示對齊提升成功率。
- Claude 在沙盒外以 `.venv/bin/mjpython -m pytest -q tests` 實跑：**189 passed**。渲染 yaw 偵測誤差仍未量測。

## 2026-11-24 手腕旋轉夾取（Codex 原始紀錄；藥丸部分已撤回）

- 修改檔案：`carearm/perception/detector.py`、`carearm/perception/pills.py`、`carearm/robot/executor.py`、`configs/default.yaml`（只新增 `grasp.align_yaw: true`）；新增 `tests/test_grasp_yaw.py`、`tests/test_pills_touching.py`；本條紀錄。
- 行為：`LabelDetection.yaw_deg` 將最小外接矩形的邊投影到世界平面，再取 `[-45°, 45°)` 的 90° 等價角。執行器在高處以既有 opspace 控制漸進旋轉腕姿，抬升後回正再搬運。既有只傳 XY 的呼叫端會先到觀測姿態重新偵測；也可明確傳入 `yaw_deg`。不改物理成功判定或既有情境分佈。
- 行為：深度分割後，以距離轉換顯著峰值作種子，於前景內分水嶺分開接觸藥丸，依各分區面積排除雜訊；無法拆分的大型或非凸區塊標記不確定，交由再次拍攝。保留無深度時一律不確定的規則，未新增套件。

實際驗證（Python 皆使用主專案 `.venv`，設定 `PYTHONPATH=/Users/chengchenyou/Desktop/ml`）：

- 真正 MuJoCo 物理測試只替換 OpenGL renderer 初始化，不替換模型、接觸、控制器、時間積分或成功判定；明確輸入測試角度，不能當成視覺端到端驗證。各角度各執行一次，結果如下：

  | yaw | 對齊後夾取並送達 | 抬升 mm | 放置誤差 mm |
  |---|---|---|---|
  | -40° | 成功 | 136.6 | 0.1 |
  | -25° | 成功 | 136.7 | 0.2 |
  | 0° | 成功 | 136.5 | 0.2 |
  | 25° | 成功 | 136.5 | 0.2 |
  | 40° | 成功 | 136.6 | 0.4 |

- `align_yaw=false` 比較：-40° 與 +40° 均夾取並送達成功（各一次）；未為了製造基線失敗修改判定，這組結果不能宣稱對齊提升成功率。
- 相鄰計數：2／3／4 顆接觸圓錠遮罩皆正確、單顆膠囊未被多切。解析射線—球面 RGB-D 的同格相切場景（2 顆成列、3 顆 L 形、4 顆方形），在 0 與 0.8 mm 深度雜訊、1% 無效像素下皆正確；這是合成幾何影像，並非 MuJoCo 渲染。另有 10 個投影合成標籤測試（5 角度 × 2 相機傾角）通過，不能代替真實渲染誤差量測。
- 指令與實際輸出：

  ```text
  PYTHONPATH=/Users/chengchenyou/Desktop/ml /Users/chengchenyou/Desktop/ml/carearm-ai/.venv/bin/python -m pytest -q -s tests/test_grasp_yaw.py tests/test_pills_touching.py -k 'not rendered and not wrist'
  27 passed, 8 deselected, 2 warnings in 18.65s
  ```

- 依要求最後執行完整 mjpython 指令；stdout／stderr 為空（0 bytes），外層 shell 取得退出碼 134，尚未進入 pytest。連 `mjpython -c 'print("mjpython ready")'` 亦為 134：

  ```text
  PYTHONPATH=/Users/chengchenyou/Desktop/ml /Users/chengchenyou/Desktop/ml/carearm-ai/.venv/bin/mjpython -m pytest -q tests
  （沒有輸出；exit_code=134）
  ```

- 另以一般 Python 診斷完整測試，實際輸出摘要如下。2 個失敗與 51 個錯誤均為 renderer／服務初始化的 `invalid CoreGraphics connection`；不是完整測試全過：

  ```text
  PYTHONPATH=/Users/chengchenyou/Desktop/ml /Users/chengchenyou/Desktop/ml/carearm-ai/.venv/bin/python -m pytest -q tests
  2 failed, 149 passed, 6 warnings, 51 errors in 23.57s
  ```

- `py_compile`（三個修改模組、兩個新增測試）與 `git diff --check` 通過。未修改 `results/`、`models/`、`RESEARCH.md`、實驗程式、`settings.py`、既有測試、HANDOFF 或 CODEX_TASKS（依本次明確檔案限制）。

未完成／未驗證：

- 本次實際 MuJoCo 渲染影像數為 **0**；yaw 偵測的渲染影像 MAE／P95 **未量測**，不能提供 240 張的誤差數字。5 個真實渲染＋自動角度夾取整合測試與 3 個接觸藥丸 wrist RGB-D 測試已新增，但環境無法初始化，尚未通過。
- 既有 `test_pills.py`、`test_adherence*.py` 的物理／相機部分亦受 CGL 阻擋，不能宣稱全過。需在可正常啟動 mjpython／CGL 的環境重新執行完整指令並完成渲染誤差量測；本次屬部分完成。
- 未驗證真實 Panda、真實 RGB-D 相機或真人服藥；未重跑已發表大型實驗。
- **尚未建立 commit**：已實際嘗試明確列檔的 `git add` 與 `git commit -m "feat: 新增手腕角度對齊與相鄰藥丸分水嶺計數"`，兩者皆退出 128：

  ```text
  fatal: Unable to create '/Users/chengchenyou/Desktop/ml/carearm-ai/.git/worktrees/codex-grasp/index.lock': Operation not permitted
  ```

  worktree 的 Git metadata 位於目前可寫範圍以外；本環境不允許提權。修改保留在分支 `codex/grasp-yaw-pills` 的工作目錄，未暫存、未 commit、未 push、未 merge。需具備該 Git 目錄寫入權限後再提交上述七個檔案。


## 2026-11-24 Claude 審查（照護者確認模擬）

- Claude 在沙盒外以 `.venv/bin/mjpython -m pytest -q tests` 實跑：**185 passed**（含停用時輸出不變的回歸測試）。60 情境試跑尚未執行；正式實驗需先在 RESEARCH.md 預先登記。

## 2026-11-24 照護者確認後執行的可選實驗統計

- 檔案：修改 `carearm/experiments/runner.py`、`report.py`、`stats.py`；新增 `carearm/experiments/caregiver.py`、`tests/test_caregiver.py`、`tests/fixtures/caregiver_legacy_*`、`docs/caregiver_simulation.md`；新增 `results/caregiver_smoke/` 的失敗試跑紀錄。本條依本次任務明確要求更新；未修改 CODEX_TASKS、README、HANDOFF 或 RESEARCH。
- 行為：`--caregiver-error 0/0.05` 啟用依真值批准／拒絕的模擬照護者，錯誤率為翻轉機率；`--caregiver-seed` 固定情境層級抽樣。批准沿用原決策時段及座標，經原 execute 共用物理快取；STOP 不送審，缺目標及硬性停止規則不以真值覆蓋，另列批准未執行數。
- 統計：新增確認後正確完成率、執行錯誤數／率、每百次請求審查負擔與端到端正確取藥率，寫入獨立 caregiver_metrics.csv／caregiver_report.md 及 summary。原 executed／correct 及原統計仍只算自動執行；未啟用時不新增欄位或輸出。啟用時訓練產物保存在本次 run，避免覆寫既有模型；沒有修改訓練演算法、門檻、決策或情境分佈。
- 實際驗證：同一虛擬環境 `python -m pytest -q tests/test_caregiver.py tests/test_stats.py tests/test_decision.py tests/test_conformal.py` → `45 passed, 2 warnings`。新增測試涵蓋錯誤率邊界、固定種子、批准／拒絕、錯盒及不應取藥、缺目標、硬性停止、分母、報告 CSV 讀回與 execute 快取共用。原版提交 a1296d5 生成的固定合成樣本用於停用功能回歸：決策列與 summary 一致，metrics／stats／pairwise CSV 位元組一致；圖檔未做跨平台位元組比較。
- 完整測試實際輸出：`PYTHONPATH=~/Desktop/ml .../.venv/bin/mjpython -m pytest -q tests` → exit 134、無輸出；同環境改用 Python → `2 failed, 140 passed, 5 warnings, 43 errors in 6.26s`，失敗／錯誤均涉及 MuJoCo renderer／服務啟動的 `invalid CoreGraphics connection`。未宣稱完整驗收通過。
- 小型試跑：已嘗試 `--run-id caregiver_smoke --n 60 --seed 20261124 --caregiver-error 0.05 --smoke`；mjpython exit 134，Python 在初始化 renderer 時 exit 1（同上 CGL 錯誤）。`results/caregiver_smoke/caregiver_report.md` 明列「小樣本試跑，非正式結果」及未完成狀態；沒有物理結果與指標，沒有捏造數字。既有 results 未改。
- 提交限制：目前分支 `codex/caregiver-confirm`；`git add` 回報無法建立主 repo 的 `.git/worktrees/codex-confirm/index.lock`（`Operation not permitted`），該 metadata 位於本工作目錄可寫範圍外，故尚未 commit，沒有 push 或 merge。
- 未驗證：成功的 60 情境 MuJoCo 試跑、指定 mjpython 全部測試通過、真實照護者／語音／相機／Panda。正式實驗前應先在 RESEARCH.md 登記固定樣本與 seed、照護者能力及缺目標處理、錯誤率比較、四指標分母、配對分析／區間及多重比較規則；具體建議與重跑指令見 `docs/caregiver_simulation.md`。本次未自行登記或執行正式大型實驗。

## 2026-11-14 第三階段 stress3600 重複實驗

- 檔案：`RESEARCH.md` §9–10、`results/stress3600/`、`HANDOFF.md`。
- 行為：程式未改；依預先登記指令執行 3,600 情境壓力實驗（實際物理執行 1,499 次）。
- 驗證：H9 成立（D vs B，exact McNemar p = 0.031）；H10 不成立（E 錯誤率上界 6.07%）。
- 未驗證：真人語音、真實 RGB-D 與 Panda；結果僅限模擬。

## 2026-11-03 第二階段預先登記實驗

- 檔案：`RESEARCH.md` §7、`models/exp1200_v2_D_*`、`models/risk_model.{joblib,json}`、`results/exp1200_v2/`、`results/stress1200/`（後兩者於 2026-11-12 補 commit）。
- 行為：新增遮擋重複盒規則、時段近音字特徵與 stress 情境分佈（預先登記 commit `745c17b`）；儀表板改用 exp1200_v2 的 D 校正風險模型（執行門檻 0.344）。
- 驗證：兩次 1,200 情境實際 MuJoCo 物理執行（exp1200_v2 576 次、stress1200 498 次）；H5／H6／H7 不成立，H8 只依登記的 bootstrap 標準成立，不宣稱 ≤2%。
- 未驗證：擴大樣本後的 H5、真人語音、真實 RGB-D 與 Panda。

## 2026-11-02 通報失敗會持久重試

- 檔案：`carearm/safety/alerts.py`、`tests/test_alert_retry.py`、`HANDOFF.md`。
- 行為：送失敗的通報保存內容與嘗試次數，最多 3 次（含首次），`alerts.max_attempts` 可調；啟動時在背景重送，也可呼叫 `retry_failed()`；中斷在送出途中的通報重啟後會再送。舊資料庫自動加欄。
- 驗證：`.venv/bin/mjpython -m pytest -q tests` 149 passed，送出都用替身，沒有連真實 webhook。
- 未驗證：真實 webhook 斷線情境；目前只有啟動時會自動重送，執行中沒有定時重送。

## 2026-11-02 status 改讀一致快照

- 檔案：`carearm/api/service.py`、`tests/test_status_snapshot.py`、`HANDOFF.md`。
- 行為：worker 執行緒發布模擬快照，`status` 不再從其他執行緒讀 MuJoCo，模擬忙碌時也能立刻回應；執行中位置約每 0.1 秒更新。決策、模型與實驗結果不變。
- 驗證：`.venv/bin/mjpython -m pytest -q tests` 143 passed；新測試在舊程式上失敗。
- 未驗證：瀏覽器實際操作中的即時位置顯示。

## 2026-11-01 啟動與語音（Claude）

- 模擬環境啟動失敗立刻回報，工作執行緒停止時拒絕新工作。
- 語音來源在沒有明確時段、卻出現時段同音字時，改由照護者確認；只影響實際運作，不改實驗程式與結果。
- 已知限制：被遮住的重複藥盒仍無法由影像發現；`status` 仍在主執行緒讀取模擬資料；通報沒有持久重試；D 策略門檻在錯誤樣本少時僅為經驗值。

## 2026-11-01 服藥判定、API 與前端（Claude、Codex）

- 服藥（Claude）：懷疑掉落時不記成已服用；遞送區有盒子但認不出來時，不靠口述記錄；口述只接受明確時段。
- API（Codex，任務 20261101-215908-303411 逾時後由 Claude 檢查並完成）：條件與時間驗證、音檔大小與類型限制、語音辨識移到工作執行緒並清暫存檔、圖表只提供 run 的 figures 下 PNG。
- 前端（Codex）：即時時鐘、執行中停用指令、清除舊決策、載入失敗可重試、輪詢不重疊、語音元件卸載清理、頁籤鍵盤操作、緊急通報優先。
- 啟動：檢查 `.venv` 與 `frontend/dist`，後端就緒才開瀏覽器。
- 驗證：`mjpython -m pytest -q tests`、WebKit 文字指令流程。
- 未驗證：瀏覽器裡的完整取藥執行（API 已驗證）、真實麥克風。

## 2026-11-01 執行安全（Claude）

- 修改：`carearm/api/service.py`、`carearm/database/store.py`、`tests/test_execution_safety.py`。
- 行為：一份決策只能執行一次，工作帶著批准時的快照；准許執行與停止互斥；重設與感知的手臂動作也受急停控制；緊急指令會立刻停止正在動的手臂；整段執行工作失敗都會解除 busy 並回報；動手前先寫入 `dispensing`，中途當機重啟後仍擋重複取藥，確定沒送達才解除；過午夜自動換日並作廢舊決策。
- 驗證：`mjpython -m pytest -q tests`。
- 未驗證：真實硬體；並發情境只測了指令順序，沒有壓力測試。

## 2026-10-28

### 國科會大專學生研究計畫 C802 報告

- 新增 `scripts/build_undergraduate_research_report.py`，從目前 CareArm 架構、現有 300／1,200 情境實驗、RGB-D 模擬量測、已知盲點與後續計畫產生 C802 研究計畫書。
- 新增 `reports/undergraduate_research/CareArm_AI_大專學生研究計畫_C802.docx` 與同名 PDF。
- 報告依 C802 指定順序包含摘要、研究動機與研究問題、文獻回顧與探討、研究方法及步驟、預期結果、需要指導教授指導內容及參考文獻。
- 版面為 A4、中文標楷體、英文 Times New Roman、正文以 12 pt 為主；正文第 1–10 頁，參考文獻第 11 頁。
- 清楚區分模擬證據與待完成工作：未把策略 E 大型實驗、真人語音、真實 RGB-D 或真實 Panda 寫成已驗證成果。

### 驗證與未完成事項

- 文件結構檢查：124 個段落、7 個表格、2 張內嵌圖；七個 C802 必要章節全部存在，未發現缺字替代符號。
- PDF 驗證：A4、11 頁；以 macOS BiauKai 字型渲染並逐頁檢查，正文在第 10 頁結束，參考文獻自第 11 頁開始，未見文字截斷、表格溢位或圖片遮蔽。
- 產生腳本通過 Python 語法編譯檢查；DOCX 與 PDF 均已計算 SHA-256 供版本比對。
- 尚未完成：申請人需補第一頁的學校、系所、年級、指導教授；再由指導教授審閱研究範圍、倫理程序與年度資料，並備齊 C801、C803、C804、歷年成績及至少 6 小時學術倫理教育證明。
- 本次未修改 CareArm 執行邏輯、模型、模擬場景或既有實驗資料，因此沒有重跑 94 個功能測試。

## 2026-10-26

### Claude：合併服藥驗證，改用手腕 RGB-D 計數

- 以 Codex 2026-10-24 的版本為主線合併，不覆蓋其 API、儀表板與 `retrieved_today`／`taken_today` 語意。
- 新增 `carearm/perception/pills.py`：手腕 RGB-D 直接數杯內藥丸（3D 點落在格子內、高出內底 2.5–14 mm），並偵測杯外 3 cm 內桌面上的疑似掉落物；不讀藥丸座標。
- 修改 `carearm/perception/compliance.py`：`start`／`evaluate` 接受 RGB-D 量測（`measured`），新增 `UNVERIFIED`（看不清楚）；沒有深度時維持原本 RGB 前後差異法。
- 新增 `carearm/decision/adherence.py`：住民說「我吃完了」時先檢查，杯內有藥就不回收並提醒，同一盒當天第 2 次即通報總部；看不清楚由照護者確認。
- `carearm/database/store.py` 新增 `dose_log` 與 `dose_state()`；服務啟動時還原今日紀錄。`CAREARM_DB` 環境變數供測試使用暫存資料庫。
- 場景：手腕補光燈 `wrist_led`、深度渲染 `render_wrist_depth`（0.8 mm 雜訊）、霧面內底材質、`take_pills` 掉落位置改到杯旁；執行器新增 `move_wrist_camera_over`。
- API：`/api/inspection.png`、`/api/doses`；前端新增「盒內計數」頁籤、決策卡「盒內檢查」列、今日服藥顯示驗證結果。

### 驗證與未完成事項

- 雲端 Linux：`python -m pytest -q` → `94 passed`（含 Codex 的 compliance／pills 測試；`test_compliance_api.py` 每個測試先重設當日紀錄）。
- 計數評估（MuJoCo，120 盒，光線／陰影隨機，0–5 顆）：是否為空判讀錯誤 0/120；顆數完全正確 110/120。只代表模擬結果。
- 實際操作儀表板：中午送藥 → 模擬取走 1 顆 → 說「我吃完藥了」→ 停止、不回收、盒內 1 顆，第 2 次說吃完時建立總部通報。
- 尚未完成：真實相機驗證；藥丸靠在一起時顆數可能差 1；Mac 上的完整測試需在一般 Terminal 重跑。

## 2026-10-24

### 接手 Claude 未收尾功能

- 保留並驗證已同步的開口藥杯、獨立藥丸與異常通報；新增物理測試，確認藥丸會隨杯搬運、可被取走及掉在桌面。
- 新增 `carearm/perception/compliance.py`：保存送達時的 wrist RGB 基準影像，以影像對齊、像素差異與連通元件判讀等待、未取藥、部分取走、完成及疑似掉落；不讀 MuJoCo 即時藥丸位置做判斷。
- `scene.py` 公開 `handcam_rgb` 與 wrist renderer；`executor.py` 新增手腕相機檢查姿態。
- 後端新增服藥基準、重新檢查、研究用取藥／掉落模擬、依從性異常通報及 wrist 即時串流；API 新增 `/api/compliance`、`/api/compliance/inspect`、`/api/simulation/intake`。
- 儀表板新增手腕相機頁籤、預裝藥丸數量、服藥驗證卡與四個操作按鈕，並重新建置 `frontend/dist`。
- 修正「送達就算已服藥」的語意錯誤：後端分開保存 `retrieved_today`（今天已取過，防止重複拿藥）與 `taken_today`（手腕影像或人工確認已取走）。藥盒剛送達時今日服藥仍為 0/3；只有 wrist 判讀為 `TAKEN` 或使用者明確說已吃完，才更新已服用狀態。
- 當杯中估計剩餘 0 顆後，儀表板會停用「模擬取走 1 顆／全部服用／掉落 1 顆」，但保留重新拍照，避免重複改動已完成的場景。
- 新增 `tests/test_compliance.py`、`tests/test_compliance_api.py`、`tests/test_pills.py`。端到端測試實際執行 Panda 送藥，驗證取 1 顆、全部取完與掉落。

### 同步補完的研究工具

- 新增 conformal 策略 E、bootstrap 95% CI、McNemar 配對檢定，以及真人語音匿名錄音／評估工具；逐項檔案與證據見 `CODEX_TASKS.md`。
- 既有 `results/exp1200` 只由真實 CSV 重建信賴區間與配對檢定，沒有捏造舊實驗不存在的策略 E 數字。

### 驗證與未完成事項

- `mjpython -m pytest -q tests`：`88 passed, 10 warnings in 51.54s`。warnings 是 Gymnasium float32 精度提示。
- `npm ci` 後 `npm run build` 成功：TypeScript、Vite、34 modules transformed。
- 實際操作 `http://127.0.0.1:8000`：12:00 中午取藥成功；送達後仍顯示今日服藥 0/3、中午「已送達」、wrist 0/2。模擬全部取走後才更新為 1/3、中午「已服用」、wrist 2/2（99%），三個改變藥丸的按鈕同步停用。
- npm 回報 1 個 moderate、1 個 high vulnerability；未使用可能破壞版本的 `npm audit fix --force`。
- macOS 顯示 OpenCV 與 PyAV 內含 AVFoundation class 的重複警告，但完整測試完成；目前未觀察到功能失敗。
- 尚未完成：真人語音正式錄音／數據、包含策略 E 的 300／1200 情境重跑、真實長者與真實 wrist camera 驗證。

## 2026-10-22

### 初學者講義擴充

- 大幅擴充 `BEGINNER_GUIDE.md`，新增 Python 變數、容器、條件、迴圈、函式、dataclass 與 import 的 CareArm 專案例子。
- 新增 YAML 設定、MuJoCo 元件、像素／世界座標、HSV 與輪廓、兩層安全決策、Panda waypoint 與物理成功判定教學。
- 新增四個可沿資料流追蹤的案例，內容與 `tests/test_schedule.py`、`tests/test_decision.py` 的現有行為對齊。
- 新增 FastAPI／JSON／SQLite／pytest 入門、六個縮小版實作里程碑、常見錯誤排查、建議讀碼順序與自我檢查答案。
- 更新 `HANDOFF.md`，登記本次變動與範圍。

### 驗證

- 文件中的行為與 `configs/default.yaml`、排程、決策、偵測器、執行器、API、資料庫及既有測試逐項校對。
- 本次沒有修改 Python 執行邏輯；仍重跑不需 MuJoCo renderer 的核心測試：`.venv/bin/python -m pytest -q tests/test_database.py tests/test_decision.py tests/test_intent.py tests/test_schedule.py`，結果為 `29 passed in 3.62s`。
- 尚未完成：本次文件工作不會解除 Codex 受限程序的 macOS CGL 限制；完整圖形測試仍須在一般 Terminal 執行。

## 2026-10-21

### 專案主線與文件

- 使用者指定 `carearm-ai/` 為後續主要專案；上層 `ml/` 只保留為 Panda 控制元件來源與歷史參考。
- 新增 `BEGINNER_GUIDE.md`，提供完全初學者的專案導讀與實作課程地圖。
- 更新 `README.md`，加入初學者講義、handoff 與變更紀錄入口。
- 更新 `HANDOFF.md`，加入主線規則、本機驗證狀況與本變更紀錄。

### 驗證

- 指令：`.venv/bin/python -m pytest -q tests/test_database.py tests/test_decision.py tests/test_intent.py tests/test_schedule.py`
- 結果：`29 passed in 0.84s`。
- 排除 API 的整體測試在目前 Codex 受限程序中得到 `32 passed, 13 errors`；13 個錯誤皆在建立 MuJoCo CGL renderer 時出現 `invalid CoreGraphics connection`。
- 尚未完成：在使用者一般 macOS Terminal 中執行完整 52 個測試、headless demo 與 `mjpython --viewer` demo。

