"""CHATKOL settings: everything that differs between one install and another lives in config.json next to this file
(or the file named by CHATKOL_CONFIG): who sends, which mailbox and Feishu table, the product the replies talk about,
the add-ons in the reply box, where the templates are. Relative paths in it are relative to the config file.

Secrets never go in config.json: the Feishu app ID and secret are read from the credentials file it names (or from
FEISHU_APP_ID / FEISHU_APP_SECRET), the Claude login from the token file it names, the mailbox login from lark-cli.
Works on the macOS system Python (3.9) as well as newer ones.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _config_file():
    if os.environ.get('CHATKOL_CONFIG'):
        return Path(os.environ['CHATKOL_CONFIG']).expanduser()
    for candidate in (HERE / 'config.json', HERE.parent / 'config.json'):  # next to the code, or one level up (the package)
        if candidate.exists():
            return candidate
    return HERE.parent / 'config.json'


CONFIG_FILE = _config_file()


class ConfigError(Exception):
    pass


def _load():
    if not CONFIG_FILE.exists():
        raise ConfigError(f'没有找到 {CONFIG_FILE}。按 SETUP.md 从 config.example.json 复制一份，填好自己的信息。')
    try:
        return json.loads(CONFIG_FILE.read_text())
    except ValueError as e:
        raise ConfigError(f'{CONFIG_FILE} 不是有效的 JSON：{e}')


CFG = _load()
BASE = CONFIG_FILE.parent


def path(value):
    """A path from the config: ~ expanded, relative ones taken from the config file's folder."""
    if not value:
        return None
    p = Path(os.path.expanduser(str(value)))
    return p if p.is_absolute() else (BASE / p).resolve()


def get(*keys, default=None):
    node = CFG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


NAME = get('name', default='CHATKOL')
SENDER = get('sender', default={}) or {}
PRODUCT = get('product', default={}) or {}


def feishu_app():
    """Feishu app ID and secret, from the environment or the credentials file (never from config.json itself)."""
    if os.environ.get('FEISHU_APP_ID') and os.environ.get('FEISHU_APP_SECRET'):
        return os.environ['FEISHU_APP_ID'], os.environ['FEISHU_APP_SECRET']
    f = path(get('feishu', 'credentials_file'))
    if not f or not f.exists():
        raise ConfigError(f'没有找到飞书应用凭证文件 {f}：运行 scripts/set_credentials.py 填写 App ID 和 Secret。')
    d = json.loads(f.read_text())
    app_id = d.get('feishu_app_id') or d.get('app_id')
    secret = d.get('feishu_app_secret') or d.get('app_secret')
    if not app_id or not secret:
        raise ConfigError(f'{f} 里缺 app_id 或 app_secret。')
    return app_id, secret


def feishu_table():
    """(app_token, table_id) of the creator table: from config.json, or from a state file another tool keeps up to date."""
    app_token, table_id = get('feishu', 'app_token'), get('feishu', 'table_id')
    if app_token and table_id:
        return app_token, table_id
    state = path(get('feishu', 'table_state_file'))
    if state and state.exists():
        d = json.loads(state.read_text())
        return d['app_token'], d.get('qualified_table_id') or d.get('table_id')
    raise ConfigError('config.json 里没有飞书表：填 feishu.app_token 和 feishu.table_id（表格链接里 /base/ 后面和 table= 后面那两段）。')


def addons():
    """[(key, label, on by default, languages where it is on by default)] in the order the reply box shows them."""
    out = []
    for key, spec in (get('addons', default={}) or {}).items():
        if key.startswith('_') or not isinstance(spec, dict):  # notes like "_说明"
            continue
        out.append((key, spec.get('label') or key, bool(spec.get('on')), list(spec.get('on_for') or [])))
    return out


def prompt(text):
    """Fill the product and sender into an AI prompt written with 【...】 tokens."""
    never = (PRODUCT.get('never_mention') or '').strip()
    off = [label for _, label, on, _ in addons() if not on]
    tokens = {
        '【发件人】': SENDER.get('name') or '我',
        '【职位】': SENDER.get('title') or '',
        '【公司】': SENDER.get('company') or PRODUCT.get('name') or '',
        '【邮箱】': SENDER.get('email') or '',
        '【产品】': PRODUCT.get('name') or '我们的产品',
        '【博主】': PRODUCT.get('creators') or '博主',
        '【观众】': PRODUCT.get('audience') or '目标观众',
        '【观众判断】': PRODUCT.get('audience_rule') or '',
        '【画像例子】': PRODUCT.get('portrait_example') or '',
        '【标签例子】': PRODUCT.get('tags_example') or '',
        '【关于我们】': PRODUCT.get('about') or '',
        '【合作方式】': PRODUCT.get('collab') or '',
        '【合作条件】': PRODUCT.get('terms') or '',
        '【不要提】': (f'不要提{never}。' if never else ''),
        '【默认不提】': '、'.join(off) or '没有',
        '【模板已讲】': '、'.join(['视频长度', '发在主账号', '脚本他写我们只核对一次产品信息', '付款时间']
                              + [label for _, label, _, _ in addons()]),
    }
    for k, v in tokens.items():
        text = text.replace(k, v)
    return text
