#!/usr/bin/env python3
"""Make sure the creator table has every column CHATKOL reads and writes; missing ones are added, nothing is deleted or
overwritten. Needs config.json (feishu.app_token / table_id) and the app credentials (scripts/set_credentials.py), and the
app added to the table with edit rights (table's「…」→「添加文档应用」).
    python3 scripts/prepare_table.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
try:
    import core  # noqa: E402
except Exception as e:  # noqa: BLE001 - config.json missing or not filled in
    sys.exit(str(e))

# (column, Feishu field type, options): 1 text, 2 number, 3 single select, 4 multi select
CREATOR_FIELDS = [
    ('账号', 1, None),            # required: the creator's handle, one row per creator
    ('平台', 3, ['TikTok', 'Instagram', 'YouTube']),
    ('主页链接', 1, None),
    ('合作邮箱', 1, None),         # required for email outreach; several addresses can go in one cell
    ('发信语言', 3, ['英文', '繁中', '韩文', '日文']),
    ('联系方式', 3, ['邮件', '私信']),
    ('触达情况', 4, ['已触达']),
    ('国家', 1, None), ('市场', 1, None),
    ('粉丝数', 2, None), ('最近10条中位播放', 2, None), ('播粉比', 2, None),
    ('内容主题', 1, None), ('人群画像', 1, None), ('内容语言', 1, None),
]


def main():
    fs = core.Feishu()
    fields = fs.call('GET', fs.path + '/fields', {'page_size': 100}).get('items') or []
    have = {f['field_name'] for f in fields}
    added = []
    if '账号' not in have:
        primary = next((f for f in fields if f.get('is_primary')), None)
        if primary and primary.get('type') == 1:  # a new table's first column ("文本"): make it the handle column
            fs.call('PUT', f"{fs.path}/fields/{primary['field_id']}", body={'field_name': '账号', 'type': 1})
            added.append(f"账号（把第一列「{primary['field_name']}」改了名）")
            have.add('账号')
    for name, ftype, options in CREATOR_FIELDS:
        if name in have:
            continue
        body = {'field_name': name, 'type': ftype}
        if options:
            body['property'] = {'options': [{'name': o} for o in options]}
        fs.call('POST', fs.path + '/fields', body=body)
        added.append(name)
    before = {f['field_name'] for f in fs.call('GET', fs.path + '/fields', {'page_size': 100}).get('items') or []}
    fs.ensure_fields()  # the board's own columns: 进度、合作状态、下一步、最后动态、对方报价、心理价位、备注、首封日期
    after = {f['field_name'] for f in fs.call('GET', fs.path + '/fields', {'page_size': 100}).get('items') or []}
    added += sorted(after - before)
    rows = len(fs.records())
    print('表格可以读写。' + (f"新加的列：{'、'.join(added)}。" if added else '需要的列都已经有了。') + f' 表里现在有 {rows} 行。')
    print('必填：账号、合作邮箱（走邮件的）、发信语言。建议填：平台、主页链接、国家、市场、粉丝数、最近10条中位播放、播粉比、内容主题、人群画像。')


if __name__ == '__main__':
    try:
        main()
    except core.FeishuError as e:
        sys.exit(f'飞书接口报错：{e}\n常见原因：应用没加进这张表（表格右上角「…」→「添加文档应用」，给可编辑）、应用没开 bitable:app 权限或没发布、'
                 'config.json 里的 app_token / table_id 抄错。')
    except core.settings.ConfigError as e:
        sys.exit(str(e))
