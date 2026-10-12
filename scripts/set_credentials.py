#!/usr/bin/env python3
"""Save the Feishu (or Lark) app's ID and secret for CHATKOL's table access, in the file config.json names
(feishu.credentials_file, normally ~/.config/chatkol/feishu.json), readable only by you.

Run it yourself in Terminal: the secret is typed with nothing echoed, never printed, never written into this folder.
    python3 scripts/set_credentials.py
"""
import getpass
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import settings  # noqa: E402


def main():
    if not sys.stdin.isatty():
        sys.exit('请在「终端」里自己运行这个脚本（要输入密钥，不能由 agent 代填）。')
    target = settings.path(settings.get('feishu', 'credentials_file', default='~/.config/chatkol/feishu.json'))
    api = settings.get('feishu', 'api', default='https://open.feishu.cn/open-apis')
    print(f'表格所在平台的开放接口：{api}')
    print('在开发者后台「凭证与基础信息」里能看到 App ID 和 App Secret。')
    app_id = input('App ID（cli_ 开头）：').strip()
    secret = getpass.getpass('App Secret（输入时不显示）：').strip()
    if not app_id or not secret:
        sys.exit('没有填完整，什么都没保存。')
    req = urllib.request.Request(api + '/auth/v3/tenant_access_token/internal',
                                 data=json.dumps({'app_id': app_id, 'app_secret': secret}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:  # noqa: BLE001
        sys.exit(f'连不上 {api}：{e}。什么都没保存。')
    if d.get('code') != 0:
        sys.exit(f"App ID 或 Secret 不对（{d.get('code')} {d.get('msg')}）。什么都没保存。")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    old = os.umask(0o077)
    try:
        target.write_text(json.dumps({'app_id': app_id, 'app_secret': secret}) + '\n')
    finally:
        os.umask(old)
    os.chmod(target, 0o600)
    print(f'已保存到 {target}（只有你这个 Mac 用户能读）。验证通过，可以回到 agent 那边继续了。')


if __name__ == '__main__':
    main()
