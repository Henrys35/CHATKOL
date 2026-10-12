#!/usr/bin/env python3
"""Check everything CHATKOL needs, one line each, with what to do about anything missing.
    python3 scripts/check_setup.py          # everything except a real AI call
    python3 scripts/check_setup.py --ai     # also one short Claude call to prove the login works
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
sys.path.insert(0, str(APP))
LARK_ENV = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
MAIL_SCOPES = ['mail:user_mailbox.message:readonly', 'mail:user_mailbox.message.address:read', 'mail:user_mailbox.message.subject:read',
               'mail:user_mailbox.message.body:read', 'mail:user_mailbox:readonly', 'mail:user_mailbox.message:send',
               'mail:user_mailbox.message:modify', 'offline_access']
results = []


def ok(item, detail=''):
    results.append(True)
    print(f'  ✓ {item}' + (f'：{detail}' if detail else ''))


def bad(item, fix, critical=True):
    results.append(not critical)
    print(f"  {'✗' if critical else '!'} {item}\n      → {fix}")


def placeholders(node, path=''):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if not k.startswith('_'):
                out += placeholders(v, f'{path}.{k}' if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += placeholders(v, f'{path}[{i}]')
    elif isinstance(node, str) and '例：' in node:
        out.append(path)
    return out


def main():
    print('CHATKOL 安装检查\n')
    print('[环境]')
    if sys.version_info >= (3, 9):
        ok('Python', sys.version.split()[0])
    else:
        bad('Python', '需要 3.9 或更新：macOS 自带的 /usr/bin/python3 就可以')

    print('\n[配置 config.json]')
    try:
        import settings
    except Exception as e:  # noqa: BLE001
        bad('config.json', str(e))
        return
    ok('能读', str(settings.CONFIG_FILE))
    left = placeholders(settings.CFG)
    if left:
        bad('还有没填的「例：」', '这些要换成你自己的：' + '、'.join(left))
    for keys in (('sender', 'name'), ('sender', 'email'), ('product', 'name'), ('product', 'about'), ('product', 'collab'),
                 ('product', 'terms'), ('product', 'creators'), ('product', 'audience')):
        if not settings.get(*keys):
            bad('.'.join(keys), '这一项不能空')
    try:
        settings.feishu_table()
        ok('飞书表', '已填 app_token 和 table_id')
    except settings.ConfigError as e:
        bad('飞书表', str(e))

    print('\n[模板]')
    try:
        import compose
        import core
        lib = compose.templates()
        need = set(lib.get('英文') or next(iter(lib.values()), {}))
        langs = list(core.TEMPLATE_FILES) or ['英文']
        for lang in langs:
            if lang not in lib:
                bad(f'回复模板·{lang}', f'回复模板里没有「## {lang}」这一节：照英文那节写一份，或者把 config.json 里这个语言删掉')
                continue
            missing = sorted(need - set(lib[lang]))
            if missing:
                bad(f'回复模板·{lang}', '缺这些小节：' + '、'.join(missing))
            for step in (1, 2):
                if not core.followup_text(lang, step, 'X', 'x').strip():
                    bad(f'跟进模板·{lang}', f"{core.TEMPLATE_FILES.get(lang)} 里没有「## 跟进{'一' if step == 1 else '二'}」和它下面的代码块")
            if not core.dm_text(lang).strip():
                bad(f'私信模板·{lang}', f'{core.TEMPLATE_FILES.get(lang)} 里没有「## 私信 · 通用版」', critical=False)
        ok('能解析', '、'.join(langs))
        name = settings.PRODUCT.get('name') or ''
        examples = [x for x in ('Acme Fit',) if x not in name]  # the example products the templates ship with
        stale = [f.name for f in core.TPL.glob('*.md') if any(x in f.read_text() for x in examples)]
        if stale:
            bad('模板里还是示例产品', '按 SETUP.md 第 7 步改成你的产品：' + '、'.join(stale))
    except Exception as e:  # noqa: BLE001
        bad('模板', f'{type(e).__name__}: {e}')
        return

    print('\n[飞书表格]')
    try:
        app_id, _ = settings.feishu_app()
        ok('应用凭证', f'App ID {app_id}')
        fs = core.Feishu()
        fields = {f['field_name'] for f in fs.call('GET', fs.path + '/fields', {'page_size': 100}).get('items') or []}
        missing = [f for f in ('账号', '合作邮箱', '发信语言') if f not in fields]
        if missing:
            bad('表格的列', '缺 ' + '、'.join(missing) + '：运行 python3 scripts/prepare_table.py')
        else:
            rows = fs.records()
            ok('能读写表格', f'{len(rows)} 行')
            if not rows:
                bad('表里还没有博主', '先把要联系的博主填进去（至少账号、合作邮箱、发信语言）', critical=False)
    except settings.ConfigError as e:
        bad('应用凭证', str(e))
    except Exception as e:  # noqa: BLE001
        bad('飞书表格', f'{e}；检查应用有没有加进这张表（表格右上角「…」→「添加文档应用」，给可编辑）、有没有开 bitable:app 并发布')
    if not settings.get('feishu', 'alert_open_id'):
        bad('故障提醒', '没填 feishu.alert_open_id，看板出故障只记日志不发消息；要的话用 scripts/find_open_id.py 查', critical=False)

    print('\n[邮箱 lark-cli]')
    lark = shutil.which('lark-cli')
    if not lark:
        bad('lark-cli', '没装：npm install -g @larksuite/cli')
    else:
        try:
            st = json.loads(subprocess.run([lark, 'auth', 'status', '--json'], capture_output=True, text=True, timeout=60,
                                           env=LARK_ENV).stdout or '{}')
            user = (st.get('identities') or {}).get('user') or {}
            scopes = set((user.get('scope') or '').split())
            if user.get('status') != 'ready':
                bad('登录', '还没用自己的账号登录：lark-cli auth login --scope "' + ' '.join(MAIL_SCOPES) + '"')
            else:
                ok('登录', f"{user.get('userName') or ''}（{st.get('brand')}，应用 {st.get('appId')}）")
                lack = [s for s in MAIL_SCOPES if s not in scopes]
                if lack:
                    bad('邮箱权限', '还缺：' + ' '.join(lack) + '。在开发者后台开通、发布，管理员审批后重新 lark-cli auth login')
                data = settings.path(settings.get('data_dir', default='data'))
                data.mkdir(parents=True, exist_ok=True)
                p = subprocess.run([lark, 'mail', '+triage', '--as', 'user', '--folder', 'INBOX', '--max', '1', '--format', 'json'],
                                   capture_output=True, text=True, timeout=120, env=LARK_ENV, cwd=str(data))
                if p.returncode == 0 and '{' in p.stdout:
                    ok('能读收件箱')
                else:
                    bad('读收件箱', (p.stderr or p.stdout)[-300:] or '读不到')
        except Exception as e:  # noqa: BLE001
            bad('lark-cli', f'{type(e).__name__}: {e}')
    sender = (settings.SENDER.get('email') or '').strip()
    if sender and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', sender):
        bad('sender.email', '格式不对')

    print('\n[AI claude]')
    claude = shutil.which('claude') or ('/opt/homebrew/bin/claude' if Path('/opt/homebrew/bin/claude').exists() else None)
    if not claude:
        bad('claude 命令行', '没装：npm install -g @anthropic-ai/claude-code，然后 claude 登录你自己的 Claude 账号')
    else:
        ok('claude 命令行', claude)
        token = settings.path(settings.get('claude', 'token_file', default='~/.config/chatkol/claude_oauth_token'))
        if token and token.exists():
            ok('一年期登录', str(token))
        else:
            bad('一年期登录', '还没有：在终端运行 bash app/setup_claude_token.sh（不做也能用命令行自己的登录，但 Mac 钥匙串锁住时 AI 会停）',
                critical=False)
        if '--ai' in sys.argv:
            import ai
            try:
                ai.ask_json('只按要求回答。', '回答 ok', {'type': 'object', 'additionalProperties': False, 'required': ['a'],
                                                     'properties': {'a': {'type': 'string'}}}, effort='low', timeout=180)
                ok('AI 调用', ai.MODEL)
            except Exception as e:  # noqa: BLE001
                bad('AI 调用', f'{e}；先在终端运行 claude 登录，或运行 bash app/setup_claude_token.sh')

    print('\n[端口]')
    port = int(settings.get('port', default=8765))
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        ok('端口空着', str(port))
    except OSError:
        bad(f'端口 {port} 已被占用', '看板已经在跑就没关系；不是的话在 config.json 改一个 port', critical=False)
    finally:
        s.close()

    print('\n' + ('全部通过，可以试跑了：bash scripts/run.sh' if all(results) else '有 ✗ 的先处理掉；! 的是提醒，不影响运行。'))


if __name__ == '__main__':
    main()
    sys.exit(0 if all(results) else 1)
