# 真人語音資料夾格式

本資料夾只放研究者取得同意後錄製的匿名資料，不可放姓名或其他直接識別資訊。

```text
data/voice/
├── manifest.csv
├── spk001/
│   ├── S001_quiet.wav
│   ├── S001_tv.wav
│   └── ...
└── spk002/
    └── ...
```

`manifest.csv` 的 `file` 使用相對於 `data/voice/` 的路徑，例如：

```csv
speaker_id,age_group,gender,condition,sentence_id,file
spk001,65+,undisclosed,quiet,S001,spk001/S001_quiet.wav
```

- `speaker_id`：匿名代碼，只能使用英數字、底線或連字號。
- `age_group`：年齡組，例如 `18-39`、`40-64`、`65+`。
- `gender`：研究者事先定義的性別選項；可使用 `undisclosed`。
- `condition`：只能是 `quiet` 或 `tv`。
- `sentence_id`：`S001` 到 `S025`。
- `file`：必須符合 `<speaker_id>/<sentence_id>_<condition>.wav`。
