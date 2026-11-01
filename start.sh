#!/bin/zsh
# 一鍵啟動 CareArm AI 儀表板：自動切到專案資料夾、啟動 .venv、開瀏覽器
cd "$(dirname "$0")"
source .venv/bin/activate
(sleep 25 && open http://127.0.0.1:8000) &
if [[ "$1" == "--viewer" ]]; then
  mjpython -m carearm.api.app --viewer
else
  python -m carearm.api.app
fi
