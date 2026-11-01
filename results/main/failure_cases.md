# 失敗案例摘要（測試集，自動執行但結果不正確）

| 策略 | 情境 | 應執行 | 應取 | 實際送達 | 失敗階段 | 條件 | 決策理由 |
|---|---|---|---|---|---|---|---|
| A 純視覺 | 214 | 否 | — | morning | 錯誤決策 | random_positions|noise_0.1|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 219 | 否 | — | evening | 錯誤決策 | light_high|camera_tilt_15|occlusion|noise_0.3|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 222 | 否 | — | morning | 錯誤決策 | random_positions|light_low|similar_color|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 228 | 否 | — | noon | 錯誤決策 | light_high|noise_0.1|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 231 | 否 | — | noon | 錯誤決策 | random_positions|occlusion|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 238 | 否 | — | morning | 錯誤決策 | shadow|camera_tilt_8|noise_0.2|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 240 | 否 | — | evening | 錯誤決策 | duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 241 | 否 | — | evening | 錯誤決策 | random_positions|camera_tilt_15|similar_color|noise_0.3|schedule_conflict | 偵測到目標顏色，直接執行 |
| B 規則式多模態 | 241 | 否 | — | evening | 錯誤決策 | random_positions|camera_tilt_15|similar_color|noise_0.3|schedule_conflict | 未指定時段，依排程（17:32）推定為晚上 / 所有規則通過，執行 |
| A 純視覺 | 242 | 否 | — | morning | 錯誤決策 | random_positions|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 245 | 否 | — | noon | 錯誤決策 | random_positions|noise_0.3|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 247 | 否 | — | morning | 錯誤決策 | random_positions|light_low|occlusion|noise_0.2|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 252 | 否 | — | evening | 錯誤決策 | random_positions|camera_tilt_8|noise_0.2|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 253 | 否 | — | morning | 錯誤決策 | random_positions|light_high|camera_tilt_8|duplicate_box|noise_0.1 | 偵測到目標顏色，直接執行 |
| A 純視覺 | 254 | 否 | — | noon | 錯誤決策 | random_positions|light_low|camera_tilt_8|occlusion|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 264 | 否 | — | evening | 錯誤決策 | random_positions|camera_tilt_8|duplicate_box | 偵測到目標顏色，直接執行 |
| A 純視覺 | 266 | 否 | — | morning | 錯誤決策 | random_positions|camera_tilt_15|noise_0.2|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 268 | 否 | — | evening | 錯誤決策 | random_positions|shadow|noise_0.3|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 269 | 否 | — | morning | 錯誤決策 | random_positions|camera_tilt_8|similar_color|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 274 | 否 | — | noon | 錯誤決策 | light_low|noise_0.1|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 276 | 否 | — | extra | wrong_box | random_positions|light_low|occlusion|duplicate_box|noise_0.2 | 偵測到目標顏色，直接執行 |
| A 純視覺 | 281 | 否 | — | morning | 錯誤決策 | random_positions|occlusion|noise_0.3|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 286 | 否 | — | evening | 錯誤決策 | random_positions|similar_color|occlusion|noise_0.1|duplicate_retrieval | 偵測到目標顏色，直接執行 |
| A 純視覺 | 290 | 否 | — | evening | 錯誤決策 | random_positions|light_high|camera_tilt_15|duplicate_box|noise_0.1|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 293 | 否 | — | evening | 錯誤決策 | random_positions|light_high|schedule_conflict | 偵測到目標顏色，直接執行 |
| A 純視覺 | 299 | 否 | — | evening | 錯誤決策 | occlusion|noise_0.2|schedule_conflict | 偵測到目標顏色，直接執行 |