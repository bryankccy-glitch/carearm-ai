# CareArm AI 變更紀錄

本檔只記錄實際完成的修改。每筆必須包含修改內容、驗證證據與尚未完成事項，不能把計畫寫成已完成。

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

