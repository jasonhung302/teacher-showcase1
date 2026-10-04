#!/usr/bin/env bash
# 每日備份（資料庫＋上傳檔打包成 zip，存在 instance/backups/，自動保留最近 14 份）
# crontab 範例：15 2 * * * /opt/teacher-showcase/deploy/backup.sh
# 建議另外把 instance/backups/ 同步到別台主機或雲端硬碟。
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/teacher-showcase}"
cd "$APP_DIR"
set -a; [ -f .env ] && . ./.env; set +a
"$APP_DIR/.venv/bin/flask" --app wsgi backup
