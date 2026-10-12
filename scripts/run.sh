#!/bin/bash
# Run CHATKOL in this Terminal window (for the first try; Ctrl+C stops it). For everyday use install the background service:
#   bash scripts/install_service.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
port="$(cd "$root/app" && python3 -c 'import settings; print(settings.get("port", default=8765))')"
echo "CHATKOL 在 http://127.0.0.1:${port} ，第一次同步要几分钟（会读这一轮的全部邮件）。按 Ctrl+C 停。"
export CHATKOL_CONFIG="$root/config.json" PYTHONUNBUFFERED=1
exec python3 "$root/app/board.py" serve
