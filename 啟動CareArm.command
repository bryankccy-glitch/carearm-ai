#!/bin/zsh
# 雙擊即可啟動 CareArm AI 儀表板（Finder 會用終端機執行）
cd "$(dirname "$0")"
exec ./start.sh "$@"
