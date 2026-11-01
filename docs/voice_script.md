# CareArm AI 真人語音錄音腳本

> 研究模擬，非醫療器材。請照文字自然朗讀；不要說出姓名、電話、地址或病歷資料。

每位錄音者可在安靜環境（`quiet`）與電視背景聲（`tv`）各錄一輪。檔名格式：`<sentence_id>_<condition>.wav`。

| 編號 | 朗讀內容 | 真實意圖 | 時段 | 備註 |
|---|---|---|---|---|
| S001 | 我要吃藥了 | `REQUEST_MEDICATION` | — | — |
| S002 | 時間到了，請幫我拿藥 | `REQUEST_MEDICATION` | — | — |
| S003 | 麻煩把藥盒拿給我 | `REQUEST_MEDICATION` | — | — |
| S004 | 我現在想吃藥 | `REQUEST_MEDICATION` | — | — |
| S005 | 請幫我拿早上的藥 | `SPECIFY_MORNING` | morning | — |
| S006 | 我要早餐後那一盒藥 | `SPECIFY_MORNING` | morning | — |
| S007 | 早晨的藥拿給我 | `SPECIFY_MORNING` | morning | — |
| S008 | 我欲食朝早的藥仔 | `SPECIFY_MORNING` | morning | 台語夾雜 |
| S009 | 請幫我拿中午的藥 | `SPECIFY_NOON` | noon | — |
| S010 | 我要午餐後那盒藥 | `SPECIFY_NOON` | noon | — |
| S011 | 午間的藥可以拿來嗎 | `SPECIFY_NOON` | noon | — |
| S012 | 請共我提中晝的藥仔 | `SPECIFY_NOON` | noon | 台語夾雜 |
| S013 | 請幫我拿晚上的藥 | `SPECIFY_EVENING` | evening | — |
| S014 | 我要睡前那一盒藥 | `SPECIFY_EVENING` | evening | — |
| S015 | 晚餐後的藥拿給我 | `SPECIFY_EVENING` | evening | — |
| S016 | 我欲食暗頓的藥仔 | `SPECIFY_EVENING` | evening | 台語夾雜 |
| S017 | 我剛剛已經吃過藥了 | `MEDICATION_ALREADY_TAKEN` | — | — |
| S018 | 藥已經吃完了，不用再拿 | `MEDICATION_ALREADY_TAKEN` | — | — |
| S019 | 毋免矣，我食過藥矣 | `MEDICATION_ALREADY_TAKEN` | — | 台語夾雜 |
| S020 | 先不要拿，取消 | `CANCEL` | — | — |
| S021 | 算了，我等一下再說 | `CANCEL` | — | — |
| S022 | 我頭暈 | `UNKNOWN` | — | 身體不適，系統不得當成取藥 |
| S023 | 我胸口不舒服 | `UNKNOWN` | — | 身體不適，系統不得當成取藥 |
| S024 | 幫我開電視 | `UNKNOWN` | — | — |
| S025 | 嗯，我不知道要做什麼 | `UNKNOWN` | — | — |
