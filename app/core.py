"""CHATKOL（KOL 合作看板）的数据层。

每轮同步：飞书博主表 → 本地；发件人的 Lark 邮箱（已发送、收件箱、垃圾箱）→ 本地；把每封信对到表里的人，
算出进度、待回复、该跟进和退信，再把看板列写回飞书。看板上的操作（回信、跟进、改状态、标私信）也在这里执行。
谁发信、哪个邮箱、哪张表、产品和模板都在 config.json（见 settings.py）。除了打开了自动跟进时到期的跟进，
其他邮件都只在用户于看板上点了发送之后才发。
"""
import functools
import hashlib
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402

CFG = settings.CFG
DATA = settings.path(settings.get('data_dir', default='data'))
DB_PATH = DATA / 'board.sqlite3'
PIPE_DB = settings.path(settings.get('sourcing', 'pipeline_db'))  # an optional sourcing pipeline's database
TPL = settings.path(settings.get('templates', 'dir', default='templates'))
CACHE = settings.path(settings.get('cache_dir', default='~/Library/Caches/chatkol'))
# Creators' attachments (media kits, rate cards) are untrusted files: kept outside the project, each in its own folder under
# a fixed name, never executed; Claude reads them there (no project CLAUDE.md above that folder either).
ATT_DIR = CACHE / 'attachments'
ATT_TYPES = {'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}
ATT_MAX = 25 * 1024 * 1024
TZ = timezone(timedelta(hours=settings.get('timezone_hours', default=8)))
# mail before this date is not this round of outreach
SINCE_MS = int(datetime.strptime(settings.get('mail', 'since', default='2026-01-01'), '%Y-%m-%d').replace(tzinfo=TZ).timestamp() * 1000)
ME = (settings.SENDER.get('email') or '').strip().lower()
DAY = 86400 * 1000
# two follow-ups: 3 days after the first email, then 7 days after the first follow-up (configurable)
FOLLOW_GAPS = tuple(settings.get('followup', 'gaps_days', default=[3, 7]))
# extra days before following up a first email sent through EasyKOL, if EasyKOL still sends its own follow-ups (else 0)
EASYKOL_GRACE_DAYS = settings.get('followup', 'easykol_grace_days', default=0)
CLOSED = {'已放弃', '不再联系'}
SIGNED = {'已签约', '已发布', '已付款'}
BUDGET = settings.get('budget_usd', default=0)
PRICING = settings.get('pricing', default=None) or None  # {cpm_usd, median_divisor, max_usd}; None = no reference price
PICK_TOPIC = settings.get('sourcing', 'pick_topic')  # rows whose 内容主题 is this are the sender's own picks
# unmatched mail worth a look on the 发信情况 page: subjects with these words
MAIL_KEYWORDS = list(settings.PRODUCT.get('mail_keywords') or [settings.PRODUCT.get('name') or 'collab', '合作'])
# Rough USD rates for reading quotes at a glance (shown as ≈; not for paying anyone). Untaxed JP/KR quotes add 10%, TW 5%.
FX = {'USD': 1, 'GBP': 1.3, 'EUR': 1.08, 'CAD': 0.73, 'AUD': 0.66, 'NZD': 0.6, 'SGD': 0.75, 'HKD': 0.128, 'MYR': 0.22,
      'TWD': 0.031, 'JPY': 0.0067, 'KRW': 0.00072}
UNTAXED_ADD = {'JPY': 0.10, 'KRW': 0.10, 'TWD': 0.05}


def main_quote(quotes, median):
    """The price of one video (not add-ons or bundles), in USD, with what it means against the configured CPM line."""
    usable = [q for q in quotes if q.get('currency') in FX and q.get('amount')]
    pick = [q for q in usable if q.get('kind') == '单条视频'] or [q for q in usable if q.get('kind') not in ('附加项',)]
    if not pick:
        return None
    def usd(q):
        return q['amount'] * FX[q['currency']] * (1 + (UNTAXED_ADD.get(q['currency'], 0) if q.get('tax') == '不含税' else 0))
    q = min(pick, key=usd)
    out = {'item': q.get('item'), 'amount': q['amount'], 'currency': q['currency'], 'tax': q.get('tax'), 'usd': round(usd(q))}
    if median:
        out['cpm'] = round(usd(q) / median * 1000, 1)
        if PRICING:
            ref = min(PRICING.get('max_usd') or 10 ** 9, median / PRICING['median_divisor'])
            out.update(ref=round(ref), x=round(usd(q) / ref, 1) if ref else None, per=PRICING['median_divisor'])
    return out

BOARD_FIELDS = [
    ('进度', 3, ['未联系', '已发首封', '已跟进一次', '已跟进两次', '对方已回复', '退信', '已发私信']),
    ('合作状态', 3, ['谈价中', '待档期', '已签约', '已发布', '已付款', '已放弃', '不再联系']),
    ('下一步', 1, None), ('最后动态', 1, None), ('对方报价', 1, None),
    ('心理价位', 2, None), ('备注', 1, None), ('首封日期', 5, None),
]
AUTO_FIELDS = ('进度', '下一步', '最后动态', '首封日期')
MANUAL_FIELDS = ('合作状态', '心理价位', '对方报价', '备注')

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
PRODUCT_RE = re.escape(settings.PRODUCT.get('name') or '') or r'(?!)'  # (?!) never matches
# our subject lines read "<product> x @handle" (e.g. "Acme x @someone")
SUBJECT_HANDLE = re.compile(PRODUCT_RE + r'(?: AI)?\s+x\s+@?([A-Za-z0-9._]+)', re.I)
AUTO_SUBJECT = re.compile(r'out of office|automatic reply|auto[- ]?reply|autoreply|自動回覆|自动回复|自動返信|부재중|자동\s*회신|'
                          r'^thank you for your email', re.I)
EASYKOL_MARK = 'To stop receiving emails from us'
# How a template nudge starts, so it is never taken for the sender answering a creator: older wordings listed in config.json,
# plus the first line of every follow-up in the templates (added below, once the template helpers exist).
FOLLOWUP_OPENERS = tuple(settings.get('nudge_openers', default=[]) or [])
SENT_ID = re.compile(r'"message_id":\s*"([^"]+)"')


def automated(m):
    """A template nudge (EasyKOL's or ours), not the sender answering. EasyKOL also stamps its footer on replies written
    in EasyKOL, so the footer alone says nothing."""
    return any(x in (m['body'] or '')[:400] for x in FOLLOWUP_OPENERS)
QUOTE_START = re.compile(
    r'^\s*(On .{3,200}wrote:|在 .{2,120}写道[:：]|.{0,60}於.{0,80}寫道[:：]|-{2,}\s*(Original Message|原始邮件|原始郵件)|'
    r'From:\s|发件人[:：]|寄件者[:：]|보낸 사람[:：]?|差出人[:：]|20\d\d年\d{1,2}月\d{1,2}日.{0,60}[:：]\s*$|'
    r'20\d\d\. ?\d{1,2}\. ?\d{1,2}\..{0,60}작성[:：]?\s*$|>)', re.M)
# the same headers in the middle of a line: some replies arrive with all their line breaks gone, and then the quoted
# original (with the sender's own name in it) was read as part of the creator's reply (2026-10-12: 62 replies)
QUOTE_INLINE = re.compile(
    r'On [^<>]{3,120}?<[^>\s]+@[^>\s]+>\s*wrote\s*:|'                                  # Gmail, English
    r'[^\s<>]{0,40}\s?<[^>\s]+@[^>\s]+>\s*(?:於|于).{0,60}?(?:寫道|写道)\s*[:：]|'       # Gmail, Chinese: "Name <addr>於 …寫道："
    r'在 .{2,80}?<[^>\s]+@[^>\s]+>\s*写道\s*[:：]|'
    r'20\d\d[年./-][^<>]{0,60}?<[^>\s]+@[^>\s]+>\s*(?:님이\s*작성\s*[:：]?|[:：])')     # Gmail, Korean and Japanese


def now_ms():
    return int(time.time() * 1000)


def iso(ms=None):
    return datetime.fromtimestamp((ms or now_ms()) / 1000, TZ).isoformat(timespec='seconds')


def md(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime('%m/%d') if ms else ''


def strip_quoted(body):
    """The new part of a reply: everything before the quoted earlier mail."""
    body = (body or '').replace('\r', '')
    cuts = [m.start() for m in (QUOTE_START.search(body, 1), QUOTE_INLINE.search(body, 1)) if m]
    return (body[:min(cuts)] if cuts else body).strip()


def thread_lang(subject, fallback):
    """Language of a thread, from the first email's subject (each template's subject carries its own marker; a creator's
    CJK display name inside an English subject must not decide it)."""
    s = subject or ''
    if '【PR案件' in s or '【タイアップ' in s or 'ご依頼' in s or s.rstrip().endswith('様'):
        return '日文'
    if '[유료' in s or '[협업' in s or '제안' in s:
        return '韩文'
    if '【付費' in s or '【合作' in s or '付費合作' in s or '合作邀約' in s:
        return '繁中'
    if re.search(r'paid|collab|partnership|' + PRODUCT_RE + r'(?: AI)? x', s, re.I):
        return '英文'
    return fallback


# ---------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, thread_id TEXT, folder TEXT, at INTEGER, from_addr TEXT, from_name TEXT,
  to_addrs TEXT, subject TEXT, body TEXT, handles TEXT DEFAULT '[]', kind TEXT DEFAULT '', bounce_addr TEXT, bounce_reason TEXT);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id);
CREATE TABLE IF NOT EXISTS ai_reads(message_id TEXT PRIMARY KEY, data TEXT, at TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS suggestions(handle TEXT, mode TEXT, basis TEXT, text TEXT, note TEXT, at TEXT, zh TEXT, pv INTEGER,
  PRIMARY KEY(handle, mode));
CREATE TABLE IF NOT EXISTS local(handle TEXT PRIMARY KEY, dismissed TEXT, dm_sent_at INTEGER, bounce_done TEXT);
CREATE TABLE IF NOT EXISTS sends(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, handle TEXT, kind TEXT, target TEXT,
  to_addrs TEXT, body TEXT, status TEXT, result TEXT, done_at TEXT);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, handle TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS written(handle TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS profiles(handle TEXT PRIMARY KEY, data TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS attachments(key TEXT PRIMARY KEY, message_id TEXT, att_id TEXT, filename TEXT, content_type TEXT, path TEXT,
  size INTEGER, status TEXT, data TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=60, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute('PRAGMA table_info(suggestions)')}
    if 'zh' not in cols:
        db.execute('ALTER TABLE suggestions ADD COLUMN zh TEXT')
    if 'pv' not in cols:
        db.execute('ALTER TABLE suggestions ADD COLUMN pv INTEGER')
    if 'attachments' not in {r[1] for r in db.execute('PRAGMA table_info(messages)')}:
        db.execute('ALTER TABLE messages ADD COLUMN attachments TEXT')
    if 'opts' not in {r[1] for r in db.execute('PRAGMA table_info(sends)')}:
        db.execute('ALTER TABLE sends ADD COLUMN opts TEXT')
    local_cols = {r[1] for r in db.execute('PRAGMA table_info(local)')}
    for col, kind in (('closed_seen_at', 'INTEGER'), ('board', 'TEXT')):
        if col not in local_cols:
            db.execute(f'ALTER TABLE local ADD COLUMN {col} {kind}')
    return db


def meta_get(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def meta_set(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
    db.commit()


def auto_followup(db):
    """Whether due follow-ups (both rounds) go out by themselves; switched on and off on the page."""
    return bool((meta_get(db, 'auto_followup') or {}).get('on'))


def log_action(db, handle, kind, detail):
    db.execute('INSERT INTO actions(at, handle, kind, detail) VALUES(?,?,?,?)',
               (iso(), handle, kind, json.dumps(detail, ensure_ascii=False)))
    db.commit()


# ---------------------------------------------------------------- Lark (the sender's mailbox through lark-cli)

class SendUncertain(Exception):
    """The send command itself failed: the mail may or may not have gone out, so it is never retried by itself."""


class LarkError(Exception):
    pass


LARK_ENV = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')


RETRYABLE = ('timeout', 'time out', 'timed out', 'network', 'rate limit', 'too many requests', '502', '503', '504')


def lark(*args, timeout=240, attempts=3):
    """One lark-cli call. Network hiccups and Lark server timeouts are retried with a pause; anything else fails at once."""
    for attempt in range(attempts):
        try:
            p = subprocess.run(['lark-cli', *args], capture_output=True, text=True, env=LARK_ENV, timeout=timeout, cwd=str(DATA))
        except subprocess.TimeoutExpired:
            err = f'lark-cli 超过 {timeout} 秒没有返回'
        else:
            out = p.stdout.strip()
            start = out.find('{')
            try:
                d = json.loads(out[start:] if start > 0 else out)
            except ValueError:
                d, err = None, (p.stderr.strip() or out)[-500:] or f'lark-cli exit {p.returncode}'
            if isinstance(d, dict) and d.get('ok') is False:
                err = json.dumps(d.get('error'), ensure_ascii=False)[:500]
            elif d is not None:
                return d
        if attempt == attempts - 1 or not any(x in err.lower() for x in RETRYABLE):
            raise LarkError(err)
        time.sleep((5, 20, 60)[attempt])


def lark_auth():
    try:
        user = lark('auth', 'status', timeout=60)['identities']['user']
        return {'ok': user.get('tokenStatus') == 'valid', 'refresh_expires': user.get('refreshExpiresAt')}
    except Exception as e:  # noqa: BLE001 - shown on the board, never fatal
        return {'ok': False, 'error': str(e)[:200]}


def triage_new(db, folder, deep):
    """Ids of messages in a folder not seen before, newest first, back to SINCE."""
    known = {r[0] for r in db.execute('SELECT id FROM messages WHERE folder=?', (folder,))}
    ids, token = [], None
    since = datetime.fromtimestamp(SINCE_MS / 1000, TZ).strftime('%Y-%m-%d')
    for _ in range(12):
        args = ['mail', '+triage', '--as', 'user', '--folder', folder, '--max', '400' if deep else '60', '--format', 'json']
        if token:
            args += ['--page-token', token]
        d = lark(*args)
        page = d.get('messages') or []
        fresh = [m for m in page if m['message_id'] not in known]
        ids += [m['message_id'] for m in fresh if (m.get('date') or '') >= since]
        token = d.get('page_token')
        oldest = min((m.get('date') or '' for m in page), default='')
        if not d.get('has_more') or not token or oldest < since or (not fresh and not deep):
            break
    return ids


def fetch_messages(db, ids, folder):
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        d = lark('mail', '+messages', '--as', 'user', '--html=false', '--message-ids', ','.join(chunk))
        for m in (d.get('data') or {}).get('messages') or []:
            head = m.get('head_from') or {}
            to = [x.get('mail_address', '').strip().lower() for x in (m.get('to') or []) + (m.get('cc') or [])]
            db.execute('INSERT OR REPLACE INTO messages(id, thread_id, folder, at, from_addr, from_name, to_addrs, subject, body, attachments) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (m['message_id'], m.get('thread_id'), folder, int(m.get('internal_date') or 0),
                        (head.get('mail_address') or '').strip().lower(), head.get('name') or '',
                        json.dumps([a for a in to if a]), m.get('subject') or '', (m.get('body_plain_text') or '')[:20000],
                        json.dumps(attachment_meta(m), ensure_ascii=False)))
        db.commit()


def attachment_meta(m):
    return [{'id': a.get('id'), 'filename': a.get('filename') or '', 'content_type': a.get('content_type') or '',
             'is_inline': bool(a.get('is_inline'))} for a in (m.get('attachments') or []) if a.get('id')]


def att_ext(a):
    ext = ATT_TYPES.get((a.get('content_type') or '').lower())
    if not ext:
        tail = (a.get('filename') or '').rsplit('.', 1)[-1].lower()
        ext = {'jpeg': 'jpg'}.get(tail, tail) if tail in ('pdf', 'png', 'jpg', 'jpeg', 'webp', 'gif') else None
    return ext


def fill_attachment_meta(db):
    """Replies stored before attachments were tracked get their attachment list once."""
    ids = [r[0] for r in db.execute("SELECT id FROM messages WHERE kind='in' AND attachments IS NULL")]
    for i in range(0, len(ids), 20):
        d = lark('mail', '+messages', '--as', 'user', '--html=false', '--message-ids', ','.join(ids[i:i + 20]))
        for m in (d.get('data') or {}).get('messages') or []:
            db.execute('UPDATE messages SET attachments=? WHERE id=?', (json.dumps(attachment_meta(m), ensure_ascii=False), m['message_id']))
        db.commit()


def download_attachments(db, limit=20):
    """PDFs and images creators attach to their replies (media kits, rate cards); a failed download is retried after 6 hours."""
    retry_before = iso(now_ms() - 6 * 3600 * 1000)
    have = {r[0] for r in db.execute("SELECT key FROM attachments WHERE status!='failed' OR at>?", (retry_before,))}
    todo = []
    for r in db.execute("SELECT id, attachments FROM messages WHERE kind='in' AND attachments IS NOT NULL AND attachments!='[]'"):
        for a in json.loads(r['attachments']):
            ext = att_ext(a)
            if not a.get('is_inline') and ext and f"{r['id']}/{a['id']}" not in have:
                todo.append((r['id'], a, ext))
    for mid, a, ext in todo[:limit]:
        key, path, size = f"{mid}/{a['id']}", None, 0
        try:
            d = lark('mail', 'user_mailbox.message.attachments', 'download_url', '--as', 'user', '--user-mailbox-id', 'me',
                     '--message-id', mid, '--attachment-ids', a['id'])
            url = (((d.get('data') or {}).get('download_urls') or [{}])[0]).get('download_url')
            if not url or not url.startswith('https://'):
                raise LarkError('没有下载地址')
            folder = ATT_DIR / hashlib.sha1(key.encode()).hexdigest()[:16]
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'attachment.{ext}'
            with urllib.request.urlopen(url, timeout=120) as resp, open(path, 'wb') as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > ATT_MAX:
                        raise ValueError('附件超过 25MB')
                    f.write(chunk)
            status = 'downloaded'
        except Exception as e:  # noqa: BLE001 - one bad attachment must not stop the sync
            status = f'failed: {str(e)[:120]}'
            if path and path.exists():
                path.unlink()
            path = None
        db.execute('INSERT OR REPLACE INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (key, mid, a['id'], a.get('filename'), a.get('content_type'), str(path) if path else None, size, status, None, iso()))
        db.commit()


def sync_mail(db, deep=False):
    got = {}
    for folder in ('SENT', 'INBOX', 'SPAM'):
        ids = triage_new(db, folder, deep)
        fetch_messages(db, ids, folder)
        got[folder] = len(ids)
    return got


# ---------------------------------------------------------------- Feishu (the creator table is the list of record)

class FeishuError(Exception):
    pass


class Feishu:
    BASE = settings.get('feishu', 'api', default='https://open.feishu.cn/open-apis')

    def __init__(self):
        app_token, table_id = settings.feishu_table()
        self.path = f"/bitable/v1/apps/{app_token}/tables/{table_id}"
        self._token, self._expires = None, 0

    def token(self):
        if self._token and time.time() < self._expires - 300:
            return self._token
        app_id, secret = settings.feishu_app()
        req = urllib.request.Request(self.BASE + '/auth/v3/tenant_access_token/internal',
                                     data=json.dumps({'app_id': app_id, 'app_secret': secret}).encode(),
                                     headers={'Content-Type': 'application/json'})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        self._token, self._expires = d['tenant_access_token'], time.time() + int(d.get('expire') or 7200)
        return self._token

    def call(self, method, path, params=None, body=None):
        url = self.BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + self.token(),
                                              'Content-Type': 'application/json; charset=utf-8'})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            raise FeishuError(f'{e.code} {e.read().decode()[:300]}')
        if d.get('code') != 0:
            raise FeishuError(f"{d.get('code')} {d.get('msg')}")
        return d.get('data') or {}

    def records(self):
        out, page = [], None
        while True:
            params = {'page_size': 500, 'automatic_fields': 'true'}  # adds created_time: when the row entered the table
            if page:
                params['page_token'] = page
            d = self.call('GET', self.path + '/records', params)
            out += d.get('items') or []
            if not d.get('has_more'):
                return out
            page = d['page_token']

    def ensure_fields(self):
        have = {f['field_name'] for f in self.call('GET', self.path + '/fields', {'page_size': 100}).get('items') or []}
        for name, ftype, options in BOARD_FIELDS:
            if name not in have:
                body = {'field_name': name, 'type': ftype}
                if options:
                    body['property'] = {'options': [{'name': o} for o in options]}
                self.call('POST', self.path + '/fields', body=body)

    def update(self, rows):
        for i in range(0, len(rows), 100):
            self.call('POST', self.path + '/records/batch_update', body={'records': rows[i:i + 100]})

    def send_text(self, open_id, text):
        """A Feishu message to one person through the project's Feishu app (the same channel the sourcing watchdog uses)."""
        return self.call('POST', '/im/v1/messages', {'receive_id_type': 'open_id'},
                         {'receive_id': open_id, 'msg_type': 'text', 'content': json.dumps({'text': text}, ensure_ascii=False)})


def text(v):
    if v is None:
        return ''
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get('text') or v.get('link') or ''
    if isinstance(v, list):
        if all(isinstance(x, str) for x in v):
            return ','.join(v)
        return ''.join(x.get('text') or x.get('link') or '' if isinstance(x, dict) else str(x) for x in v)
    return str(v)


def number(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def creator_from(record):
    f = record['fields']
    emails = [e.lower() for e in EMAIL.findall(text(f.get('合作邮箱')))]
    return {
        'handle': text(f.get('账号')).strip(), 'record_id': record['record_id'],
        'profile': text(f.get('主页链接')), 'country': text(f.get('国家')), 'market': text(f.get('市场')) or '其他地区',
        'platform': text(f.get('平台')) or 'TikTok', 'followers': number(f.get('粉丝数')),
        'median': number(f.get('最近10条中位播放')), 'ratio': number(f.get('播粉比')),
        'latest_post': number(f.get('近期发文日期')), 'emails': emails, 'email_source': text(f.get('邮箱来源')),
        'topic': text(f.get('内容主题')), 'topic_evidence': text(f.get('主题依据')), 'portrait': text(f.get('人群画像')),
        'video_log': text(f.get('10条视频核验记录')), 'send_lang': text(f.get('发信语言')) or '英文',
        'contact': text(f.get('联系方式')) or ('邮件' if emails else '私信'), 'reach': text(f.get('触达情况')),
        'content_lang': text(f.get('内容语言')), 'source_tag': text(f.get('来源标签')),
        'board': {name: f.get(name) for name, _, _ in BOARD_FIELDS}, 'added_at': record.get('created_time'),
    }


LANG_MARKET = {'繁中': '台湾', '韩文': '韩国', '日文': '日本'}
OUTREACH_NAME = re.compile(r'(?:×|\bx\b)\s+(.+?)\s*(?:[｜|:：—]|様|님|$)', re.I)


def offtable_creators(db, creators):
    """People we emailed who are not (or no longer) in the table but wrote back, so their reply is not lost. They live
    only on the board: nothing about them goes to Feishu, and what the user fills in for them is kept in `local`."""
    domain = '@' + ME.split('@')[-1]
    taken = {h.lower() for h in creators}
    kept = {r['handle']: json.loads(r['board']) for r in db.execute('SELECT handle, board FROM local WHERE board IS NOT NULL')}
    out = {}
    for r in db.execute("SELECT thread_id, at, to_addrs, subject FROM messages WHERE folder='SENT' AND kind='other_out' ORDER BY at"):
        to = [a for a in json.loads(r['to_addrs'] or '[]') if a and not a.endswith(domain)]
        if not to or not re.search(PRODUCT_RE, r['subject'] or '', re.I):
            continue
        replied = db.execute("SELECT 1 FROM messages WHERE thread_id=? AND folder!='SENT' AND kind='other' AND from_addr NOT LIKE ? "
                             "AND from_addr NOT LIKE '%mailer-daemon%' AND from_addr NOT LIKE '%postmaster%' LIMIT 1",
                             (r['thread_id'], '%' + domain)).fetchone()
        if not replied or any(set(to) & set(c['emails']) for c in out.values()):
            continue
        m = OUTREACH_NAME.search(r['subject'] or '')
        name = re.sub(r'[\s/\\]+', '', m.group(1))[:40] if m else ''
        name = name or to[0].split('@')[0]
        if name.lower() in taken:
            name += '（表外）'
        taken.add(name.lower())
        lang = thread_lang(r['subject'], '英文')
        board = {k: None for k, _, _ in BOARD_FIELDS}
        board.update(kept.get(name) or {})
        out[name] = {'handle': name, 'record_id': None, 'offtable': True, 'profile': '', 'country': '',
                     'market': LANG_MARKET.get(lang, '其他地区'), 'platform': '', 'followers': None, 'median': None, 'ratio': None,
                     'latest_post': None, 'emails': to, 'email_source': '', 'topic': '', 'topic_evidence': '', 'portrait': '',
                     'video_log': '', 'send_lang': lang, 'contact': '邮件', 'reach': '已触达', 'content_lang': '', 'source_tag': '',
                     'board': board, 'added_at': r['at']}
    return out


# ---------------------------------------------------------------- matching mail to creators

def bounce_info(body):
    m = re.search(r'收件人\s+(\S+@\S+?)\s+原因\s+(\S+)', body or '')
    if m:
        return m.group(1).strip().lower(), m.group(2)
    m = re.search(r"(?:wasn't delivered to|delivery to the following recipient[s]? failed[^:]*:)\s*(\S+@\S+)", body or '', re.I)
    if m:
        return m.group(1).strip('<>.,').lower(), '投递失败'
    return '', ''


def match_all(db, creators):
    by_addr = {}
    for c in creators.values():
        for e in c['emails']:
            by_addr.setdefault(e, set()).add(c['handle'])
    by_lower = {h.lower(): h for h in creators}
    # mail the board itself sent knows its creator, even when it went to a manager's address that is not in the table
    ours = {}
    for r in db.execute("SELECT handle, result FROM sends WHERE result LIKE '%message_id%'"):
        for mid in SENT_ID.findall(r['result'] or ''):
            if r['handle'] in creators:
                ours[mid] = r['handle']
    threads, inbound_threads, unmatched_sent = {}, {}, []
    rows = db.execute('SELECT id, thread_id, folder, from_addr, to_addrs, subject, body FROM messages WHERE at>=? ORDER BY at',
                      (SINCE_MS,)).fetchall()
    updates = []

    def from_subject(subject):
        m = SUBJECT_HANDLE.search(subject or '')
        return {by_lower[m.group(1).lower()]} if m and m.group(1).lower() in by_lower else set()

    for r in rows:
        if r['folder'] != 'SENT':
            continue
        hs = {ours[r['id']]} if r['id'] in ours else set()
        if not hs:
            for a in json.loads(r['to_addrs'] or '[]'):
                hs |= by_addr.get(a, set())
            hs = hs or from_subject(r['subject'])
        if hs:
            threads.setdefault(r['thread_id'], set()).update(hs)
            updates.append((json.dumps(sorted(hs)), 'out', None, None, r['id']))
        else:
            unmatched_sent.append(r)
    for r in rows:
        if r['folder'] == 'SENT':
            continue
        if 'mailer-daemon' in (r['from_addr'] or '') or 'postmaster' in (r['from_addr'] or ''):
            addr, reason = bounce_info(r['body'])
            hs = by_addr.get(addr, set())
            updates.append((json.dumps(sorted(hs)), 'bounce' if hs else 'other', addr, reason, r['id']))
            continue
        hs = set(threads.get(r['thread_id'], set())) or by_addr.get(r['from_addr'], set()) or from_subject(r['subject'])
        if hs:
            inbound_threads.setdefault(r['thread_id'], set()).update(hs)
        updates.append((json.dumps(sorted(hs)), 'in' if hs else 'other', None, None, r['id']))
    # the sender writing back in a thread a creator's manager or agency started, from an address the table does not have
    for r in unmatched_sent:
        hs = inbound_threads.get(r['thread_id'], set())
        updates.append((json.dumps(sorted(hs)), 'out' if hs else 'other_out', None, None, r['id']))
    db.executemany('UPDATE messages SET handles=?, kind=?, bounce_addr=?, bounce_reason=? WHERE id=?', updates)
    db.commit()


# ---------------------------------------------------------------- per-creator state

def first_line(body, n=120):
    return re.sub(r'\s+', ' ', body or '').strip()[:n]


def greeting_name(body, lang):
    head = (body or '').strip()[:240]
    pattern = {'英文': r'^Hi\s+([^,\n]{1,60}),', '繁中': r'^(.{1,40}?)\s*你好', '韩文': r'^안녕하세요,?\s*(.{1,40}?)님',
               '日文': r'^(.{1,40}?)様'}.get(lang, r'^Hi\s+([^,\n]{1,60}),')
    m = re.search(pattern, head)
    return m.group(1).strip() if m else ''


def is_auto(m, reads):
    data = reads.get(m['id']) or {}
    return data.get('category') == '自动回复' or bool(AUTO_SUBJECT.search(m['subject'] or ''))


def derive(c, msgs, reads, local, now):
    outs = [m for m in msgs if m['kind'] == 'out']
    ins = [m for m in msgs if m['kind'] == 'in']
    real_in = [m for m in ins if not is_auto(m, reads)]
    bounces = [m for m in msgs if m['kind'] == 'bounce']
    status = text(c['board'].get('合作状态'))
    first = outs[0] if outs else None
    first_in_at = real_in[0]['at'] if real_in else None
    # a bounced first email sent again to a corrected address starts the follow-up clock over
    resent = [m for m in outs if m['id'] in local.get('resend_ids', ())]
    base = resent[-1] if resent else first
    followups = [m for m in outs[1:] if m not in resent and (not resent or m['at'] > base['at'])
                 and (first_in_at is None or m['at'] < first_in_at)]
    last_out = outs[-1] if outs else None
    last_in = real_in[-1] if real_in else None
    # the sender's own answers: mail sent after the creator first wrote that is not an EasyKOL or template nudge
    answers = [m for m in outs if first_in_at is not None and m['at'] > first_in_at and not automated(m)]
    last_answer = answers[-1] if answers else None
    lang = thread_lang(first['subject'], c['send_lang']) if first else c['send_lang']
    bounced = None
    if bounces and not real_in:
        b = bounces[-1]
        if not first or b['at'] >= first['at']:
            bounced = {'addr': b['bounce_addr'], 'reason': b['bounce_reason'] or '退信', 'at': b['at'], 'id': b['id']}
    if bounced and local.get('bounce_done') == bounced['id']:
        bounced = None
    dm_sent = local.get('dm_sent_at')
    if real_in:
        progress = '对方已回复'
    elif bounced:
        progress = '退信'
    elif len(followups) >= 2:
        progress = '已跟进两次'
    elif followups:
        progress = '已跟进一次'
    elif first:
        progress = '已发首封'
    elif dm_sent or (c['contact'] == '私信' and c['reach']):
        progress = '已发私信'
    else:
        progress = '未联系'
    closed = status in CLOSED
    # marked 已放弃 / 不再联系, and then they wrote again: back on 待你回复 (the status stays, no follow-ups restart)
    came_back = bool(closed and last_in and local.get('closed_at') and last_in['at'] > local['closed_at'])
    waiting = bool(last_in and (not last_answer or last_in['at'] > last_answer['at']) and (not closed or came_back)
                   and local.get('dismissed') != last_in['id'])
    due = None
    if first and not real_in and not bounced and not status:
        if not followups:
            via_easykol = EASYKOL_MARK in (base['body'] or '')
            due = {'step': 1, 'at': base['at'] + (FOLLOW_GAPS[0] + (EASYKOL_GRACE_DAYS if via_easykol else 0)) * DAY,
                   'after': last_out['id'], 'easykol': via_easykol}
        elif len(followups) == 1:
            due = {'step': 2, 'at': followups[0]['at'] + FOLLOW_GAPS[1] * DAY, 'after': last_out['id'], 'easykol': False}
    quotes = []
    for m in real_in:
        for q in (reads.get(m['id']) or {}).get('quotes') or []:
            quotes.append({**q, 'at': m['at']})
    latest_read = (reads.get(last_in['id']) or {}) if last_in else {}
    if waiting:
        next_step = f"待你回复（{md(last_in['at'])} 收到）"
    elif status in SIGNED:
        next_step = {'已签约': '等对方发布', '已发布': '发布后 3 个工作日内付款', '已付款': '记第 7 天、第 30 天数据'}[status]
    elif status in CLOSED:
        next_step = '不再跟进'
    elif bounced:
        next_step = '换邮箱或改走私信'
    elif due:
        next_step = f"{md(due['at'])} 发跟进{'一' if due['step'] == 1 else '二'}"
    elif real_in:
        next_step = '等对方回复'
    elif first:
        next_step = '跟进已发完，等回复'
    elif c['contact'] == '私信' and progress == '未联系':
        next_step = '发私信'
    else:
        next_step = '待发首封' if progress == '未联系' else ''
    events = sorted([(m['at'], 'out', m) for m in outs] + [(m['at'], 'in', m) for m in ins], key=lambda x: x[0])
    last_event = ''
    if events:
        at, direction, m = events[-1]
        if direction == 'in':
            summary = (reads.get(m['id']) or {}).get('summary') or first_line(strip_quoted(m['body']), 60)
            last_event = f"{md(at)} 对方：{summary}"
        else:
            kind = '首封' if first and m['id'] == first['id'] else ('跟进' if m in followups or automated(m) else '我们回复')
            last_event = f"{md(at)} {kind}"
    elif progress == '已发私信':
        last_event = f"{md(dm_sent)} 已发私信" if dm_sent else '已发私信'
    return {
        'handle': c['handle'], 'progress': progress, 'status': status, 'lang': lang, 'waiting': waiting,
        'came_back': came_back and waiting,
        'waiting_since': last_in['at'] if waiting else None, 'last_in_id': last_in['id'] if last_in else None,
        'last_in_from': last_in['from_addr'] if last_in else None, 'last_out_id': last_out['id'] if last_out else None,
        'first_at': first['at'] if first else (dm_sent or None), 'first_id': first['id'] if first else None,
        'first_subject': first['subject'] if first else '', 'followups': len(followups),
        'via_easykol': bool(first and EASYKOL_MARK in (first['body'] or '')), 'replied': bool(real_in),
        'auto_only': bool(ins and not real_in), 'bounced': bounced, 'due': due, 'quotes': quotes,
        'read': latest_read, 'next_step': next_step, 'last_event': last_event,
        'greeting': greeting_name(first['body'], lang) if first else '',
        'last_activity': events[-1][0] if events else (dm_sent or 0),
        'answered_at': last_answer['at'] if last_answer else None, 'n_in': len(real_in), 'n_answers': len(answers),
        'refused': latest_read.get('category') == '拒绝',
        'answer_snippet': first_line(re.sub(r'^\s*(hi|hello|dear|hey)\b[^,\n]*,', '', strip_quoted(last_answer['body']), flags=re.I), 120) if last_answer else '',
        'to_addrs': json.loads(last_out['to_addrs'] or '[]') if last_out else c['emails'],
    }


# ---------------------------------------------------------------- pricing brief (what the sender weighs before naming a price)

VIDEO_LINE = re.compile(r'^\s*\d+\.\s*(\d{4}-\d{2}-\d{2})\s*\|\s*播放\s*([\d,]+)', re.M)
AD = re.compile(r'#ad\b|#ads\b|#sponsored|#paidpartnership|#partner\b|paid partnership|#pr\b|#gifted|#광고|#협찬|#유료광고|'
                r'#業配|#合作|#タイアップ|#プロモーション|#提供', re.I)


def pipeline_videos(handle):
    if not PIPE_DB or not PIPE_DB.exists():
        return []
    try:
        con = sqlite3.connect(f'file:{PIPE_DB}?mode=ro', uri=True, timeout=10)
        row = con.execute('SELECT verify_json FROM creators WHERE username=? AND verify_json IS NOT NULL '
                          'ORDER BY verified_at DESC LIMIT 1', (handle,)).fetchone()
        con.close()
        return (json.loads(row[0]) or {}).get('newest10') or [] if row else []
    except (sqlite3.Error, ValueError):
        return []


def flag(country):
    m = re.search(r'[（(]([A-Z]{2})[)）]', country or '')
    return ''.join(chr(0x1F1E6 + ord(ch) - 65) for ch in m.group(1)) if m else ''


def wan(n):
    if n is None:
        return '—'
    return f'{n / 10000:.1f} 万' if n >= 10000 else f'{int(n):,}'


def brief(c, st):
    videos = pipeline_videos(c['handle'])
    views = sorted(((d, int(v.replace(',', ''))) for d, v in VIDEO_LINE.findall(c['video_log'])), reverse=True)
    if not views and videos:
        views = sorted(((v.get('published_at') or '')[:10], int(v.get('view_count') or 0)) for v in videos)[::-1]
    lines = []  # who the creator is now sits in the portrait above, so the brief starts with where and the numbers
    where = ' · '.join(x for x in (c['country'], c['market'], c['platform'],
                                   f"内容语言 {c['content_lang']}" if c['content_lang'] else '') if x)
    lines.append(['国家', where or '—'])
    data = [f"{wan(c['followers'])} 粉"]
    if c['median']:
        data.append(f"最近 10 条中位 {wan(c['median'])}")
    if c['ratio']:
        data.append(f"播粉比 {c['ratio']:.2f}")
    if views:
        vals = [v for _, v in views]
        data.append(f"区间 {wan(min(vals))}–{wan(max(vals))}")
        hits = sum(v >= 100000 for v in vals)
        if hits:
            data.append(f'{hits} 条过 10 万')
        if len(vals) >= 6:
            recent = statistics.median(vals[:5])
            whole = statistics.median(vals)
            trend = '在涨' if recent > whole * 1.25 else '在掉' if recent < whole * 0.75 else '持平'
            data.append(f'最近 5 条中位 {wan(recent)}（{trend}）')
        try:
            span = (datetime.fromisoformat(views[0][0]) - datetime.fromisoformat(views[-1][0])).days
            data.append(f'{len(vals)} 条用了 {span} 天')
        except ValueError:
            pass
    rates = [v['like_count'] / v['view_count'] for v in videos if v.get('view_count') and v.get('like_count') is not None]
    if rates:
        data.append(f'点赞率 {statistics.median(rates) * 100:.1f}%')
    lines.append(['数据', ' · '.join(data)])
    ads = [v for v in videos if AD.search(v.get('caption') or '')]
    if videos:
        lines.append(['商单', '；'.join(f"{(v.get('published_at') or '')[:10]} 带广告标记的视频 {wan(v.get('view_count'))} 播放" for v in ads[:3])
                      if ads else '最近 10 条里没看到带广告标记的视频'])
    if st['quotes']:
        lines.append(['报价', '；'.join(f"{q.get('item')} {q.get('currency')} {q.get('amount'):,.0f}"
                                       + ('' if q.get('tax') in (None, '未说明') else f"（{q['tax']}）") for q in st['quotes'][-4:])])
        mq = main_quote(st['quotes'], c['median'])
        if mq and mq.get('cpm') is not None:
            lines.append(['折算', f"单条约 ${mq['usd']:,}，相当于 CPM ${mq['cpm']}" + (f"，是参考价的 {mq['x']} 倍" if mq.get('x') else '')])
    if c['median'] and PRICING:
        cap = PRICING.get('max_usd')
        ref = round(c['median'] / PRICING['median_divisor'])
        lines.append(['参考', f"按 CPM ${PRICING.get('cpm_usd')} 折算约 ${min(cap, ref) if cap else ref:,}"
                              f"（中位播放 ÷ {PRICING['median_divisor']}" + (f"，单条不超过 ${cap:,}" if cap else '') + '）'])
    return lines


# ---------------------------------------------------------------- who the creator is (material for the one-line portrait)

# an optional sourcing pipeline may leave creator files around (TikTok embeds, EasyKOL details); most installs have none
EASYKOL_DIR = settings.path(settings.get('sourcing', 'easykol_dir'))
EMBEDS = settings.path(settings.get('sourcing', 'embeds_dir'))


def _json(path):
    if path is None:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _in(folder, name):
    return folder / name if folder else None


def display_name(h):
    """The name on the creator's profile (TikTok embed, else EasyKOL), as profile_material finds it but without its slow
    caption lookups: the follow-up list needs it for hundreds of people at once."""
    prof = (_json(_in(EMBEDS, f'{h}.json')) or {}).get('profile') or {}
    if prof.get('nickname'):
        return prof['nickname']
    ek = (_json(_in(EASYKOL_DIR, f'easykol_detail_{h}.json')) or _json(_in(EASYKOL_DIR, f'easykol_detail_instagram_{h}.json'))
          or {}).get('data') or {}
    return ek.get('nickname') or ''


def profile_material(c):
    h = c['handle']
    m = {'handle': h, 'platform': c['platform'], 'country': c['country'], 'followers': c['followers'],
         'median_views': c['median'], 'our_topic': c['topic'], 'our_evidence': c['topic_evidence'][:400],
         'our_portrait': c['portrait'][:300]}
    captions, bio, name = [], '', ''
    if PIPE_DB and PIPE_DB.exists():
        try:
            con = sqlite3.connect(f'file:{PIPE_DB}?mode=ro', uri=True, timeout=10)
            row = con.execute('SELECT bio, captions FROM creators WHERE username=? ORDER BY updated_at DESC LIMIT 1', (h,)).fetchone()
            con.close()
            if row:
                bio = row[0] or ''
                captions += [x for x in json.loads(row[1] or '[]') if isinstance(x, str)]
        except (sqlite3.Error, ValueError):
            pass
    captions += [v.get('caption') or '' for v in pipeline_videos(h)]
    embed = _json(_in(EMBEDS, f'{h}.json')) or {}
    prof = embed.get('profile') or {}
    name = prof.get('nickname') or name
    bio = bio or prof.get('signature') or ''
    captions += [v.get('description') or '' for v in embed.get('videos') or []]
    ek = (_json(_in(EASYKOL_DIR, f'easykol_detail_{h}.json')) or _json(_in(EASYKOL_DIR, f'easykol_detail_instagram_{h}.json'))
          or {}).get('data') or {}
    if ek:
        name = name or ek.get('nickname') or ''
        bio = bio or ek.get('description') or ''
        m['third_party_portrait'] = ek.get('oneLinePitch') or ''
        m['third_party_tags'] = ek.get('aiTags') or ''
    seen, clean = set(), []
    for cap in captions:
        cap = re.sub(r'\s+', ' ', cap).strip()[:160]
        if cap and cap not in seen:
            seen.add(cap)
            clean.append(cap)
    m.update(display_name=name, bio=bio[:300], recent_captions=clean[:10])
    return m


# ---------------------------------------------------------------- templates (the folder named in config.json)

# follow-ups (## 跟进一 / ## 跟进二) and DM templates (## 私信 · 通用版), one file per language
TEMPLATE_FILES = dict(settings.get('templates', 'by_language', default={}) or {})
ENGLISH_DM = settings.get('templates', 'english_dm')  # optional: the English DM in its own file (heading ## 通用版)
NAME_TOKENS = ('{博主昵称}', '{暱稱}', '{닉네임}', '{名前}', '{name}')


def template_block(filename, heading):
    body = (TPL / filename).read_text()
    i = body.find('\n## ' + heading)
    if i < 0:
        return ''
    m = re.search(r'```[a-z]*\n(.*?)\n```', body[i:], re.S)
    return m.group(1).strip() if m else ''


def fill_name(block, name, lang, handle):
    if not name:
        name = {'英文': 'there', '繁中': '', '韩文': handle, '日文': handle}.get(lang, handle)
    for token in NAME_TOKENS:
        block = block.replace(token, name)
    block = block.replace('{发件人}', settings.SENDER.get('name') or '')  # templates may sign with the configured sender
    return '\n'.join(line.strip() if line.startswith(' ') else line for line in block.splitlines()).strip()


TITLES = {'dr', 'mr', 'ms', 'mrs', 'miss', 'mx', 'prof', 'professor', 'coach', 'nurse', 'teacher', 'tutor', 'doc', 'mister'}
# words that make a display name a channel name rather than a person ("Arab Med Bro", "Study With Jess", "Nursing School Notes")
NOT_NAMES = set("""the a an and of with by for to in on my your our its im official real study studies studying studygram studytok
    student students school college uni university academy class notes note tips tricks hacks daily life vlog vlogs blog asmr
    med meds medic medical medschool premed nurse nurses nursing rn bsn np pa md do dds phd mcat lsat sat act gre gmat nclex usmle
    doc doctor dentist dental pharm pharmacy physio bio chem physics math maths science stem english tutor tutoring teacher
    teach teaches learn learning edu education exam exams revision code coding dev tech ai bro bros sis girl girls boy boys guy
    gal mom dad mama papa team crew club hub lab labs studio media tv yt tiktok ig insta official theofficial channel""".split())


@functools.lru_cache(maxsize=1)
def name_lists():
    def read(path):
        try:
            return {w.strip() for w in open(path, encoding='utf-8', errors='ignore') if w.strip()}
        except OSError:
            return set()
    # macOS ships both: 1,300 common first names, and 236,000 English words to tell "Arab" or "Nursing" from a name
    return read('/usr/share/dict/propernames'), {w.lower() for w in read('/usr/share/dict/words')}


def friendly_name(greet, lang, handle, display_name):
    """How an English follow-up says hello. EasyKOL greeted people by their handle ("Hi jane_d0e92"), so take a first name
    from the display name when it clearly is one, otherwise "Hi there". Other languages keep the name the first email used."""
    if lang != '英文':
        return greet
    if greet and greet.lower() != handle.lower() and not re.search(r'[_.\d@]', greet):
        return greet
    first = re.split(r'\s[|/·•–—-]\s|[|/·•,(（]', display_name or '')[0]
    tokens = [t for t in (re.sub(r"[^A-Za-z'\-]", '', x) for x in first.split()) if t]
    while tokens and tokens[0].lower() in TITLES:
        tokens = tokens[1:]
    if not tokens or len(tokens) > 3 or any(t.lower().strip("'") in NOT_NAMES for t in tokens):
        return 'there'
    cand = tokens[0].strip("'-")
    if not re.fullmatch(r"[A-Za-z][A-Za-z'\-]{2,11}", cand) or cand.lower() == re.sub(r'[^a-z]', '', handle.lower()):
        return 'there'
    name = cand.capitalize() if cand.islower() or cand.isupper() else cand
    proper, words = name_lists()
    return name if name in proper or cand.lower() not in words else 'there'


def followup_text(lang, step, name, handle):
    block = template_block(TEMPLATE_FILES.get(lang) or TEMPLATE_FILES.get('英文') or '', '跟进一' if step == 1 else '跟进二')
    return fill_name(block, name, lang, handle)


def dm_text(lang):
    if lang not in TEMPLATE_FILES:
        lang = '英文'
    if lang == '英文' and ENGLISH_DM:
        return template_block(ENGLISH_DM, '通用版')
    return template_block(TEMPLATE_FILES.get(lang) or '', '私信 · 通用版')


def _template_openers():
    """The first line after the greeting of every follow-up template: how the board tells its own nudges from a reply."""
    out = []
    for f in set(TEMPLATE_FILES.values()):
        for step in ('跟进一', '跟进二'):
            try:
                block = template_block(f, step)
            except OSError:
                continue
            lines = [x.strip() for x in block.splitlines() if x.strip() and not any(t in x for t in NAME_TOKENS)]
            if lines:
                out.append(lines[0][:24])
    return tuple(out)


FOLLOWUP_OPENERS = FOLLOWUP_OPENERS + _template_openers()


# ---------------------------------------------------------------- snapshot (what the board shows)

class Board:
    def __init__(self):
        connect().close()
        self._tl = threading.local()
        self.lock = threading.RLock()
        self.fs = Feishu()
        self.creators, self.states, self.profiles = {}, {}, {}
        self._display_names = {}
        self.synced_at, self.sync_error, self.auth = None, None, {}
        self.mail_checked_at = 0
        self.wake = threading.Event()

    @property
    def db(self):
        """One SQLite connection per thread (the server, the sync loop and the send worker each get their own)."""
        con = getattr(self._tl, 'db', None)
        if con is None:
            con = self._tl.db = connect()
        return con

    # ---- sync
    def sync(self, deep=False, write_back=True):
        started = time.time()
        if write_back:
            self.fs.ensure_fields()
        creators = {}
        for r in self.fs.records():
            c = creator_from(r)
            if c['handle']:
                creators[c['handle']] = c
        mail_error = None
        try:
            got = sync_mail(self.db, deep=deep)
        except (LarkError, subprocess.TimeoutExpired) as e:  # keep showing what we have; say why mail is stale
            got, mail_error = {}, str(e)
        match_all(self.db, creators)
        extra = offtable_creators(self.db, creators)
        if extra:
            creators.update(extra)
            match_all(self.db, creators)
        if not mail_error:
            try:
                fill_attachment_meta(self.db)
                download_attachments(self.db)
            except (LarkError, subprocess.TimeoutExpired) as e:  # attachments are extra; the sync itself still counts
                print('attachments:', str(e)[:200], flush=True)
        with self.lock:
            self.creators = creators
            self.recompute()
            written = self.write_back() if write_back else 0
        if mail_error:
            self.sync_error = ('Mac 钥匙串被锁住了，读不到 Lark 邮箱登录' if 'keychain' in mail_error.lower() else mail_error[:300])
            self.feishu_synced_at = now_ms()
            return {'mail_error': self.sync_error, 'written': written}
        self.synced_at, self.sync_error = now_ms(), None
        self.mail_checked_at = self.synced_at
        meta_set(self.db, 'mail_synced_at', self.synced_at)
        if not self.auth or time.time() - self.auth.get('checked', 0) > 1800:
            self.auth = {**lark_auth(), 'checked': time.time()}
        meta_set(self.db, 'last_sync', {'at': iso(), 'mail': got, 'written': written, 'seconds': round(time.time() - started)})
        return {'mail': got, 'written': written}

    def recompute(self):
        self.profiles = {r['handle']: json.loads(r['data']) for r in self.db.execute('SELECT * FROM profiles')}
        reads = {r['message_id']: json.loads(r['data']) for r in self.db.execute('SELECT * FROM ai_reads')}
        local = {r['handle']: dict(r) for r in self.db.execute('SELECT * FROM local')}
        for r in self.db.execute("SELECT handle, result FROM sends WHERE kind='resend' AND status='sent'"):
            local.setdefault(r['handle'], {}).setdefault('resend_ids', set()).update(SENT_ID.findall(r['result'] or ''))
        per = {h: [] for h in self.creators}
        for m in self.db.execute("SELECT id, thread_id, folder, at, from_addr, to_addrs, subject, body, handles, kind, "
                                 "bounce_addr, bounce_reason FROM messages WHERE kind IN ('out','in','bounce') ORDER BY at"):
            for h in json.loads(m['handles'] or '[]'):
                if h in per:
                    per[h].append(dict(m))
        for h, at in self.closing_times(local).items():
            local.setdefault(h, {})['closed_at'] = at
        now = now_ms()
        self.states = {h: derive(c, per[h], reads, local.get(h, {}), now) for h, c in self.creators.items()}

    def closing_times(self, local):
        """When each creator now marked 已放弃 / 不再联系 got that status: the board's own action if it set it, else the first
        sync that saw it (set straight in Feishu). Mail they send after that time goes back on 待你回复."""
        last = {}
        for a in self.db.execute("SELECT at, handle, kind, detail FROM actions WHERE kind IN ('fields', 'close', 'reopen') ORDER BY id"):
            if a['kind'] == 'fields':
                d = json.loads(a['detail'] or '{}')
                if '合作状态' in d:
                    last[a['handle']] = a['at'] if d['合作状态'] in CLOSED else None
            else:
                last[a['handle']] = a['at'] if a['kind'] == 'close' else None
        out, changed = {}, False
        for h, c in self.creators.items():
            seen = (local.get(h) or {}).get('closed_seen_at')
            if text(c['board'].get('合作状态')) not in CLOSED:
                if seen:
                    self.db.execute('UPDATE local SET closed_seen_at=NULL WHERE handle=?', (h,))
                    changed = True
                continue
            if last.get(h):
                out[h] = int(datetime.fromisoformat(last[h]).timestamp() * 1000)
                continue
            if not seen:
                seen = now_ms()
                self.db.execute('INSERT INTO local(handle, closed_seen_at) VALUES(?,?) '
                                'ON CONFLICT(handle) DO UPDATE SET closed_seen_at=excluded.closed_seen_at', (h, seen))
                changed = True
            out[h] = seen
        if changed:
            self.db.commit()
        return out

    def write_back(self):
        """Board columns in the Feishu table, only for creators we have reached; human-entered fields are never overwritten."""
        rows = []
        written = {r['handle']: json.loads(r['data']) for r in self.db.execute('SELECT * FROM written')}
        for h, st in self.states.items():
            c = self.creators[h]
            if st['progress'] == '未联系' or not c.get('record_id'):  # no row in the table for people we emailed off it
                continue
            want = {'进度': st['progress'], '下一步': st['next_step'], '最后动态': st['last_event']}
            current_quote = text(c['board'].get('对方报价'))
            if st['quotes'] and (not current_quote or current_quote == written.get(h, {}).get('对方报价')):
                want['对方报价'] = '；'.join(f"{q.get('item')} {q.get('currency')} {q.get('amount'):,.0f}" for q in st['quotes'][-3:])
            changed = {k: v for k, v in want.items() if text(c['board'].get(k)) != v}
            first_date = number(c['board'].get('首封日期'))
            if st['first_at'] and (first_date is None or abs(first_date - st['first_at']) > DAY):
                changed['首封日期'] = int(st['first_at'])
            if '已触达' not in c['reach']:
                changed['触达情况'] = ['已触达']
            if changed:
                rows.append({'record_id': c['record_id'], 'fields': changed})
                record = {**written.get(h, {}), **{k: v for k, v in changed.items() if k in ('对方报价',)}}
                self.db.execute('INSERT OR REPLACE INTO written VALUES(?,?)', (h, json.dumps(record, ensure_ascii=False)))
                for k, v in changed.items():
                    if k in c['board']:
                        c['board'][k] = v
                if '触达情况' in changed:
                    c['reach'] = '已触达'
        self.db.commit()
        if rows:
            self.fs.update(rows)
        return len(rows)

    # ---- views
    def row(self, h):
        c, st = self.creators[h], self.states[h]
        price = number(c['board'].get('心理价位'))
        return {
            'handle': h, 'platform': c['platform'], 'market': c['market'], 'country': c['country'], 'lang': st['lang'],
            'followers': c['followers'], 'median': c['median'], 'ratio': c['ratio'], 'profile': c['profile'],
            'progress': st['progress'], 'status': st['status'], 'next_step': st['next_step'], 'last_event': st['last_event'],
            'waiting_since': st['waiting_since'], 'summary': (st['read'] or {}).get('summary') or '',
            'category': (st['read'] or {}).get('category') or '', 'quotes': st['quotes'][-3:], 'price': price,
            'due': st['due'], 'bounced': st['bounced'], 'contact': c['contact'], 'emails': c['emails'],
            'first_at': st['first_at'], 'last_activity': st['last_activity'],
            'own_pick': bool(PICK_TOPIC) and c['topic'] == PICK_TOPIC,
            'portrait': (self.profiles.get(h) or {}).get('portrait') or '', 'tags': (self.profiles.get(h) or {}).get('tags') or [],
            'fit': (self.profiles.get(h) or {}).get('student_audience'), 'fit_note': (self.profiles.get(h) or {}).get('fit_note') or '',
            'quote_main': main_quote(st['quotes'], c['median']),
            'answered_at': st['answered_at'], 'answer_snippet': st['answer_snippet'],
            'waiting': st['waiting'], 'n_in': st['n_in'], 'n_answers': st['n_answers'],
            'flag': flag(c['country']), 'came_back': st['came_back'], 'offtable': bool(c.get('offtable')),
            'added_at': c.get('added_at'),
        }

    def queues(self):
        now = now_ms()
        rows = {h: self.row(h) for h in self.states}
        reply = sorted((r for r in rows.values() if self.states[r['handle']]['waiting']), key=lambda r: r['waiting_since'])
        # a quote the sender has already answered is handled, mental price or not
        quote = [r for r in rows.values() if r['quotes'] and r['price'] is None and r['status'] not in CLOSED | SIGNED
                 and not (r['answered_at'] and r['answered_at'] > max(q['at'] for q in self.states[r['handle']]['quotes']))]
        # follow-ups already queued for sending leave the list; the banner counts them down
        pending = {r['handle'] for r in self.db.execute("SELECT DISTINCT handle FROM sends WHERE status IN ('queued', 'sending')")}
        followup = sorted((r for r in rows.values() if r['due'] and r['handle'] not in pending), key=lambda r: r['due']['at'])
        bounce = [r for r in rows.values() if r['bounced'] and r['status'] not in CLOSED]
        dm = [r for r in rows.values() if r['contact'] == '私信' and r['progress'] == '未联系' and r['status'] not in CLOSED]
        # 「有希望！」: we reached out, they answered, we wrote back, and it is still going
        # (not marked 不合作, and their latest mail is not a no)
        hopeful = [r for r in rows.values() if self.states[r['handle']]['replied'] and r['answered_at'] and r['status'] not in CLOSED
                   and not self.states[r['handle']]['refused']]
        return {'hopeful': hopeful, 'reply': reply, 'quote': sorted(quote, key=lambda r: (r['quote_main'] or {}).get('x') or 999),
                'followup_due': [r for r in followup if r['due']['at'] <= now],
                'followup_soon': [r for r in followup if now < r['due']['at'] <= now + 3 * DAY],
                'bounce': bounce, 'dm': sorted(dm, key=lambda r: (r['lang'], r['market']))}

    def overview(self):
        q = self.queues()
        rows = [self.row(h) for h in self.states]
        order = list(settings.get('markets', default=[]) or [])
        markets = order + sorted({r['market'] for r in rows} - set(order))
        funnel = []
        for mk in ['全部'] + markets:
            sel = [r for r in rows if mk == '全部' or r['market'] == mk]
            if not sel:
                continue
            funnel.append({'market': mk, 'total': len(sel),
                           'contacted': sum(r['progress'] != '未联系' for r in sel),
                           'replied': sum(self.states[r['handle']]['replied'] for r in sel),
                           'quoted': sum(bool(r['quotes']) for r in sel),
                           'talking': sum(r['status'] in ('谈价中', '待档期') or r['status'] in SIGNED for r in sel),
                           'signed': sum(r['status'] in SIGNED for r in sel),
                           'posted': sum(r['status'] in ('已发布', '已付款') for r in sel)})
        signed_amount = sum(r['price'] or 0 for r in rows if r['status'] in SIGNED)
        queued = self.db.execute("SELECT COUNT(*) FROM sends WHERE status IN ('queued', 'sending') AND kind LIKE 'followup%'").fetchone()[0]
        by_day = {}
        for r in q['followup_due'] + q['followup_soon']:
            d = 'now' if r['due']['at'] <= now_ms() else md(r['due']['at'])
            by_day[d] = by_day.get(d, 0) + 1
        return {
            'auto_followup': auto_followup(self.db),
            'synced_at': self.synced_at, 'sync_error': self.sync_error, 'auth': self.auth,
            'counts': {'reply': len(q['reply']), 'reply_old': sum(now_ms() - r['waiting_since'] > DAY for r in q['reply']),
                       'quote': len(q['quote']), 'followup_due': len(q['followup_due']), 'followup_soon': len(q['followup_soon']),
                       'followup_queued': queued, 'followup_days': [{'day': d, 'n': n} for d, n in sorted(by_day.items(), key=lambda x: (x[0] != 'now', x[0]))],
                       'hopeful': len(q['hopeful']), 'hopeful_waiting': sum(r['waiting'] for r in q['hopeful']),
                       'hopeful_old': sum(not r['waiting'] and now_ms() - r['answered_at'] > 3 * DAY for r in q['hopeful']),
                       'bounce': len(q['bounce']), 'dm': len(q['dm']),
                       'dm_langs': {lang: sum(r['lang'] == lang for r in q['dm']) for lang in ('英文', '日文', '韩文', '繁中')}},
            'funnel': funnel, 'budget': {'october': BUDGET, 'signed': signed_amount},
            'health': self.health(),
        }

    def health(self):
        days = [(datetime.now(TZ) - timedelta(days=i)).strftime('%m/%d') for i in range(13, -1, -1)]
        per = {d: {'first': 0, 'reply_in': 0, 'bounce': 0} for d in days}
        for h, st in self.states.items():
            if st['first_at'] and st['first_id'] and md(st['first_at']) in per:
                per[md(st['first_at'])]['first'] += 1
        for m in self.db.execute("SELECT at, kind, handles FROM messages WHERE at>=? AND kind IN ('in','bounce')",
                                 (now_ms() - 15 * DAY,)):
            d = md(m['at'])
            if d in per:
                per[d]['reply_in' if m['kind'] == 'in' else 'bounce'] += 1
        by_lang, by_route = {}, {}
        for h, st in self.states.items():
            if not st['first_id']:
                continue
            for key, bucket in ((st['lang'], by_lang), ('EasyKOL 发的' if st['via_easykol'] else 'Lark 直接发的', by_route)):
                b = bucket.setdefault(key, {'sent': 0, 'replied': 0, 'bounced': 0})
                b['sent'] += 1
                b['replied'] += st['replied']
                b['bounced'] += bool(st['bounced'])
        unmatched = [{'at': m['at'], 'from': m['from_addr'], 'subject': m['subject']} for m in self.db.execute(
            "SELECT at, from_addr, subject FROM messages WHERE kind='other' AND folder!='SENT' AND at>=? AND ("
            + ' OR '.join('subject LIKE ?' for _ in MAIL_KEYWORDS) + ") ORDER BY at DESC LIMIT 20",
            (SINCE_MS, *[f'%{k}%' for k in MAIL_KEYWORDS]))]
        return {'days': [{'day': d, **per[d]} for d in days], 'by_lang': by_lang, 'by_route': by_route, 'unmatched': unmatched}

    def detail(self, h):
        c, st = self.creators[h], self.states[h]
        msgs = []
        for m in self.db.execute("SELECT * FROM messages WHERE kind IN ('out','in','bounce') AND instr(handles, ?) > 0 ORDER BY at",
                                 (json.dumps(h),)):
            if h not in json.loads(m['handles'] or '[]'):
                continue
            read = self.db.execute('SELECT data FROM ai_reads WHERE message_id=?', (m['id'],)).fetchone()
            atts = [{'key': a['key'], 'filename': a['filename'], 'status': a['status'], 'read': json.loads(a['data']) if a['data'] else None}
                    for a in self.db.execute('SELECT * FROM attachments WHERE message_id=? ORDER BY key', (m['id'],))]
            msgs.append({'id': m['id'], 'at': m['at'], 'kind': m['kind'], 'from': m['from_addr'], 'subject': m['subject'], 'attachments': atts,
                         'body': strip_quoted(m['body'])[:4000] if m['kind'] == 'in' else (m['body'] or '')[:4000],
                         'spam': m['folder'] == 'SPAM', 'read': json.loads(read['data']) if read else None,
                         'bounce': m['bounce_reason']})
        sugg = {r['mode']: {'text': r['text'], 'note': r['note'], 'at': r['at'], 'basis': r['basis'], 'zh': r['zh'], 'pv': r['pv'] or 1}
                for r in self.db.execute('SELECT * FROM suggestions WHERE handle=?', (h,))}
        fu = None
        if st['due']:
            fu = {'step': st['due']['step'], 'at': st['due']['at'],
                  'text': followup_text(st['lang'], st['due']['step'], self.fu_greeting(h), h)}
        mat = profile_material(c)
        return {'row': self.row(h), 'brief': brief(c, st), 'messages': msgs, 'suggestions': sugg, 'followup': fu,
                'can_nudge': bool(st['last_out_id']) and not st['last_in_id'], 'to_addrs': st['to_addrs'],
                'display_name': mat.get('display_name') or '', 'bio': mat.get('bio') or '',
                'board': {k: c['board'].get(k) for k in MANUAL_FIELDS}, 'reply_to': st['last_in_from'],
                'dm': dm_text(st['lang']) if c['contact'] == '私信' else None, 'basis': self.basis(h)}

    def fu_greeting(self, h):
        st = self.states[h]
        if st['lang'] != '英文':
            return st['greeting']
        if h not in self._display_names:
            self._display_names[h] = display_name(h)
        return friendly_name(st['greeting'], st['lang'], h, self._display_names[h])

    def basis(self, h):
        st, c = self.states[h], self.creators[h]
        return f"{st['last_in_id']}|{number(c['board'].get('心理价位'))}|{st['status']}"

    # ---- actions (each one comes from a click on the board)
    def set_fields(self, h, changes):
        allowed = {k: v for k, v in changes.items() if k in MANUAL_FIELDS}
        if '心理价位' in allowed:
            allowed['心理价位'] = number(allowed['心理价位'])
        clean = {k: v for k, v in allowed.items() if v not in (None, '')}
        cleared = [k for k, v in allowed.items() if v in (None, '')]
        c = self.creators[h]
        if not c.get('record_id'):  # not in the table: the board keeps these itself
            kept = {**{k: v for k, v in c['board'].items() if k in MANUAL_FIELDS}, **allowed}
            self.db.execute('INSERT INTO local(handle, board) VALUES(?,?) ON CONFLICT(handle) DO UPDATE SET board=excluded.board',
                            (h, json.dumps(kept, ensure_ascii=False)))
            self.db.commit()
        if clean and c.get('record_id'):
            self.fs.update([{'record_id': c['record_id'], 'fields': clean}])
        if cleared and c.get('record_id'):
            self.fs.update([{'record_id': c['record_id'], 'fields': {k: None for k in cleared}}])
        with self.lock:
            c['board'].update(allowed)
            self.recompute()
        log_action(self.db, h, 'fields', allowed)

    def close(self, h):
        """Not working with this creator: off every list, no follow-ups, nothing sent. The Feishu row stays, marked
        不再联系, because sourcing would put a deleted row back on its next publish and the creator could be contacted again."""
        self.set_fields(h, {'合作状态': '不再联系'})
        self.db.execute("UPDATE sends SET status='skipped', result=? WHERE handle=? AND status='queued'",
                        (json.dumps({'skipped': '已关闭，不合作'}, ensure_ascii=False), h))
        self.db.commit()
        log_action(self.db, h, 'close', {})

    def reopen(self, h):
        self.set_fields(h, {'合作状态': None})
        log_action(self.db, h, 'reopen', {})

    def mark_dm(self, h):
        c = self.creators[h]
        self.db.execute('INSERT INTO local(handle, dm_sent_at) VALUES(?,?) ON CONFLICT(handle) DO UPDATE SET dm_sent_at=excluded.dm_sent_at',
                        (h, now_ms()))
        self.db.commit()
        self.fs.update([{'record_id': c['record_id'], 'fields': {'触达情况': ['已触达'], '进度': '已发私信',
                                                                  '首封日期': now_ms(), '下一步': '等对方回复',
                                                                  '最后动态': f'{md(now_ms())} 已发私信'}}])
        with self.lock:
            c['reach'] = '已触达'
            self.recompute()
        log_action(self.db, h, 'dm_sent', {})

    def dismiss(self, h):
        st = self.states[h]
        self.db.execute('INSERT INTO local(handle, dismissed) VALUES(?,?) ON CONFLICT(handle) DO UPDATE SET dismissed=excluded.dismissed',
                        (h, st['last_in_id']))
        self.db.commit()
        with self.lock:
            self.recompute()
        log_action(self.db, h, 'dismiss', {'message': st['last_in_id']})

    def bounce_done(self, h):
        st = self.states[h]
        if st['bounced']:
            self.db.execute('INSERT INTO local(handle, bounce_done) VALUES(?,?) ON CONFLICT(handle) DO UPDATE SET bounce_done=excluded.bounce_done',
                            (h, st['bounced']['id']))
            self.db.commit()
        with self.lock:
            self.recompute()
        log_action(self.db, h, 'bounce_done', {})

    def send_reply(self, h, body, close=False):
        st = self.states[h]
        if not st['last_in_id']:
            raise ValueError('这个人还没有回信，没有可回复的邮件')
        result = lark('mail', '+reply', '--as', 'user', '--message-id', st['last_in_id'], '--body', body,
                      '--plain-text', '--no-signature', '--confirm-send', attempts=1)
        self._record_send(h, 'close' if close else 'reply', st['last_in_id'], [st['last_in_from']], body, result)
        self._ingest_sent(h, result, [st['last_in_from']], body, st['last_in_id'])
        if close:
            self.set_fields(h, {'合作状态': '已放弃'})
        self.wake.set()
        return result

    def save_draft(self, h, body):
        st = self.states[h]
        target = st['last_in_id'] or st['last_out_id']
        if not target:
            raise ValueError('没有可回复的邮件')
        args = ['mail', '+reply' if st['last_in_id'] else '+reply-all', '--as', 'user', '--message-id', target, '--body', body,
                '--plain-text', '--no-signature']
        if not st['last_in_id']:
            args += ['--remove', ME]
        result = lark(*args)
        log_action(self.db, h, 'draft', {'target': target})
        return result

    def send_nudge(self, h, body):
        """A message the user wrote the gist of, to someone who has not answered yet: in our own thread, to the same people."""
        st = self.states[h]
        if not st['last_out_id']:
            raise ValueError('还没给这个人发过邮件')
        result = lark('mail', '+reply-all', '--as', 'user', '--message-id', st['last_out_id'], '--remove', ME, '--body', body,
                      '--plain-text', '--no-signature', '--confirm-send', attempts=1)
        self._record_send(h, 'nudge', st['last_out_id'], st['to_addrs'], body, result)
        self._ingest_sent(h, result, st['to_addrs'], body, st['last_out_id'])
        self.wake.set()
        return result

    def send_followup(self, h, step, body, early=False):
        """Re-checked right before sending: anyone who replied, bounced or moved on since the batch was queued is skipped.
        early: the user chose to send it before its day. The mailbox is re-read at most every 90 seconds: with sends 40
        seconds apart, reading it before every single one ran into Lark's rate limit (2026-10-12)."""
        if now_ms() - (self.mail_checked_at or 0) > 90 * 1000:
            sync_mail(self.db)
            self.mail_checked_at = now_ms()
        match_all(self.db, self.creators)
        with self.lock:
            self.recompute()
        st = self.states.get(h)
        if not st or not st['due'] or st['due']['step'] != step or (not early and st['due']['at'] > now_ms() + 3600 * 1000):
            return {'skipped': '状态已变化（对方回信、退信、已经跟进过、点了不合作或还没到时间）'}
        try:
            result = lark('mail', '+reply-all', '--as', 'user', '--message-id', st['last_out_id'], '--remove', ME, '--body', body,
                          '--plain-text', '--no-signature', '--confirm-send', attempts=1)
        except LarkError as e:
            text = str(e)
            # turned away before anything left (rate limit, or the original message could not even be fetched): safe to retry
            if 'rate limit' in text.lower() or 'failed to fetch original message' in text:
                raise
            raise SendUncertain(f'发送这一步出错，可能没发出去：{e}') from e
        log_action(self.db, h, f'send_followup{step}', {'to': st['to_addrs']})
        self._ingest_sent(h, result, st['to_addrs'], body, st['last_out_id'])
        return {'to': st['to_addrs'], 'result': result}

    def resend_bounced(self, h, email):
        st = self.states[h]
        if not st['first_id']:
            raise ValueError('没有首封可以重发')
        if not EMAIL.fullmatch(email.strip()):
            raise ValueError('邮箱格式不对')
        first = self.db.execute('SELECT subject, body FROM messages WHERE id=?', (st['first_id'],)).fetchone()
        body = '\n'.join(line for line in (first['body'] or '').splitlines() if EASYKOL_MARK not in line).strip()
        subject = re.sub(r'^(RE|Re|回复|回覆)\s*[:：]\s*', '', first['subject'])
        result = lark('mail', '+send', '--as', 'user', '--to', email.strip(), '--subject', subject, '--body', body,
                      '--plain-text', '--no-signature', '--confirm-send', attempts=1)
        self._record_send(h, 'resend', st['first_id'], [email.strip()], body, result)
        self._ingest_sent(h, result, [email.strip()], body, None, subject)
        self.bounce_done(h)
        return result

    def _ingest_sent(self, h, result, to_addrs, body, reply_to, subject=''):
        """Put mail the board just sent straight into its own records and recompute, so the lists and the Feishu columns move
        the moment the user clicks send. The mailbox sync later sees the same message id and keeps this copy."""
        data = (result or {}).get('data') or {}
        if not data.get('message_id'):
            return
        if reply_to and not subject:
            row = self.db.execute('SELECT subject FROM messages WHERE id=?', (reply_to,)).fetchone()
            subject = (row['subject'] if row else '') or ''
            if subject and not re.match(r'^(re|回复|回覆)\s*[:：]', subject, re.I):
                subject = 'Re: ' + subject
        self.db.execute('INSERT OR IGNORE INTO messages(id, thread_id, folder, at, from_addr, from_name, to_addrs, subject, body, '
                        'handles, kind, attachments) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (data['message_id'], data.get('thread_id'), 'SENT', now_ms(), ME, settings.SENDER.get('name') or '', json.dumps(to_addrs), subject,
                         body, json.dumps([h]), 'out', '[]'))
        self.db.commit()
        match_all(self.db, self.creators)
        with self.lock:
            self.recompute()

    def _record_send(self, h, kind, target, to_addrs, body, result):
        self.db.execute('INSERT INTO sends(created_at, handle, kind, target, to_addrs, body, status, result, done_at) '
                        'VALUES(?,?,?,?,?,?,?,?,?)',
                        (iso(), h, kind, target, json.dumps(to_addrs), body, 'sent', json.dumps(result, ensure_ascii=False)[:2000], iso()))
        self.db.commit()
        log_action(self.db, h, 'send_' + kind, {'to': to_addrs})
