#!/bin/bash
# Install CHATKOL as a macOS background service: starts at login, restarts if it crashes, logs to data/logs.
#   bash scripts/install_service.sh              # install or update
#   bash scripts/install_service.sh --uninstall  # remove
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
label="com.chatkol.board"
plist="$HOME/Library/LaunchAgents/$label.plist"
uid="$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "已卸载。数据还在 $root/data，删不删由你。"
  exit 0
fi

python="$(command -v python3)"
port="$(cd "$root/app" && "$python" -c 'import settings; print(settings.get("port", default=8765))')"
data="$(cd "$root/app" && "$python" -c 'import settings; print(settings.path(settings.get("data_dir", default="data")))')"
mkdir -p "$data/logs" "$HOME/Library/LaunchAgents"
# lark-cli and claude are usually under Homebrew or npm's global folder
extra_path="$(dirname "$(command -v lark-cli || echo /opt/homebrew/bin/lark-cli)"):$(dirname "$(command -v claude || echo /opt/homebrew/bin/claude)")"

cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$python</string><string>$root/app/board.py</string><string>serve</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$extra_path:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>CHATKOL_CONFIG</key><string>$root/config.json</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>WorkingDirectory</key><string>$root</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$data/logs/board.log</string>
  <key>StandardErrorPath</key><string>$data/logs/board.err</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$uid/$label" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$plist"
echo "已装好常驻服务 $label：开机自动启动，崩了自动拉起。"
echo "打开 http://127.0.0.1:$port 。日志在 $data/logs/。"
echo "重启：launchctl kickstart -k gui/$uid/$label    卸载：bash scripts/install_service.sh --uninstall"
