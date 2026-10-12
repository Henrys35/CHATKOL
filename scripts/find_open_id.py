#!/usr/bin/env python3
"""Look up your open_id for this app from your Feishu (or Lark) login email, so CHATKOL can message you when something
breaks. Needs the app permission contact:user.id:readonly (and a bot, for the messages themselves).
    python3 scripts/find_open_id.py you@company.com
Put the printed open_id into config.json → feishu.alert_open_id.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
try:
    import core  # noqa: E402
except Exception as e:  # noqa: BLE001 - config.json missing or not filled in
    sys.exit(str(e))


def main():
    if len(sys.argv) != 2 or '@' not in sys.argv[1]:
        sys.exit('用法：python3 scripts/find_open_id.py 你登录飞书用的邮箱')
    fs = core.Feishu()
    try:
        d = fs.call('POST', '/contact/v3/users/batch_get_id', {'user_id_type': 'open_id'}, {'emails': [sys.argv[1]]})
    except core.FeishuError as e:
        sys.exit(f'查不到：{e}\n应用需要开通 contact:user.id:readonly 权限并发布，你也要在应用的可用范围里。')
    users = [u for u in d.get('user_list') or [] if u.get('user_id')]
    if not users:
        sys.exit('这个邮箱在这个企业里没找到用户：换成你登录飞书用的邮箱试试。')
    print(users[0]['user_id'])


if __name__ == '__main__':
    main()
