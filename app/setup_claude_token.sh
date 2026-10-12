#!/bin/bash
# One-year Claude login for CHATKOL's background AI, stored as a file only this macOS user can read (the path is
# claude.token_file in config.json), so the board keeps working when the login keychain is locked.
# Run it yourself in Terminal; approve in the browser when it opens. The token goes straight from `claude setup-token` into
# the file; it is never printed back or sent anywhere else.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
target="$(cd "$here" && python3 -c 'import settings; print(settings.path(settings.get("claude", "token_file", default="~/.config/chatkol/claude_oauth_token")))')"
dir="$(dirname "$target")"
mkdir -p "$dir" && chmod 700 "$dir"
work="$(mktemp -d)" && chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

echo "浏览器会打开 Claude 授权页，用你自己的 Claude 账号点授权即可。"
script -q "$work/session" claude setup-token >/dev/null

umask 077
/usr/bin/python3 - "$work/session" "$target" <<'PY'
import re, sys
raw = open(sys.argv[1], 'rb').read().decode('utf-8', 'ignore')
raw = re.sub(r'\x1b\][^\x07\x1b]*(\x07|\x1b\\)', '', raw)        # terminal title and other OSC sequences
raw = re.sub(r'\x1b\[[0-9;?]*[ -/]*[@-~]', '', raw)              # colours and cursor movement
part = re.search(r'valid for 1 year\)?:?(.*?)Store this token', raw, re.S)
joined = re.sub(r'\s+', '', part.group(1)) if part else ''
token = re.search(r'sk-ant-oat[0-9A-Za-z_\-]{20,}', joined)
if not token:
    sys.exit('没有拿到令牌：授权没有完成，或者 claude 命令行的输出格式变了。')
with open(sys.argv[2], 'w') as f:
    f.write(token.group(0) + '\n')
PY
chmod 600 "$target"

model="$(cd "$here" && python3 -c 'import settings; print(settings.get("claude", "model", default="claude-sonnet-5-5"))')"
echo "令牌已保存到 $target，正在测试…"
if CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$target")" claude -p "只回复：可以" --model "$model" --tools "" \
    --no-session-persistence --strict-mcp-config --output-format text 2>/dev/null | grep -q "可以"; then
  echo "完成：看板的 AI 以后不再依赖钥匙串。"
else
  echo "令牌已保存，但测试调用没有成功：把这句话发给帮你安装的 agent。"
fi
