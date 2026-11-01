#!/bin/zsh
# 一鍵啟動 CareArm AI 儀表板：自動切到專案資料夾、啟動 .venv、開瀏覽器
cd "$(dirname "$0")"
if [[ ! -f .venv/bin/activate ]]; then
  print -u2 "無法啟動：找不到 .venv 虛擬環境，請先完成環境安裝。"
  exit 1
fi
if [[ ! -d frontend/dist ]]; then
  print -u2 "無法啟動：找不到 frontend/dist，請先建置前端。"
  exit 1
fi
source .venv/bin/activate
if [[ "$1" == "--viewer" ]]; then
  mjpython -m carearm.api.app --viewer &
else
  python -m carearm.api.app &
fi
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null; kill "$browser_pid" 2>/dev/null' EXIT INT TERM
(
  for attempt in {1..180}; do
    kill -0 "$server_pid" 2>/dev/null || exit 1
    if curl --fail --silent --max-time 1 http://127.0.0.1:8000/api/health >/dev/null; then
      open http://127.0.0.1:8000
      exit
    fi
    sleep 1
  done
  print -u2 "後端尚未就緒，請查看終端機錯誤訊息後重試。"
) &
browser_pid=$!
wait "$server_pid"
