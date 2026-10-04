#!/usr/bin/env bash
# 每日備份（PostgreSQL 資料＋上傳檔打包成 zip，存在 instance/backups/，自動保留最近 14 份）
# crontab 範例：15 2 * * * /opt/teacher-showcase/deploy/backup.sh
# 建議另外把 instance/backups/ 同步到別台主機或雲端硬碟。
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/teacher-showcase}"
cd "$APP_DIR"
# 設定由 flask 啟動時自行讀取 env.json
"$APP_DIR/.venv/bin/flask" --app wsgi backup
