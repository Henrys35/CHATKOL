"""CHATKOL（KOL 合作看板）：本机网页（只在 127.0.0.1 上监听）+ 后台同步 + Claude 读信和起草 + 跟进发送队列。
谁发信、哪个邮箱、哪张飞书表、产品和模板都在 config.json（见 settings.py）。

用法：
  python3 board.py serve                       # 常驻服务（scripts/install_service.sh 装成 launchd 服务），打开 http://127.0.0.1:<port>
  python3 board.py sync [--deep] [--no-write]  # 只同步一次，打印结果
"""
import argparse
import json
import secrets
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ai  # noqa: E402
import compose  # noqa: E402
import core  # noqa: E402

PORT = int(core.settings.get('port', default=8765))
ALERT_OPEN_ID = core.settings.get('feishu', 'alert_open_id')  # who hears about outages on Feishu; empty = log only
NAME = core.settings.NAME
SENDER_NAME = core.settings.SENDER.get('name') or '我们'  # how our side is labelled in the thread the AI reads
HEARTBEAT = core.DATA / 'heartbeat.json'
ALERT_AFTER = {'mail': 15 * 60, 'ai': 30 * 60, 'send': 10 * 60}  # seconds a problem must last before the user hears about it
PROBLEM_NAMES = {'mail': ('邮箱同步', '读不到 Lark 邮箱'), 'ai': ('AI 读信', 'AI 读信和起草停了'), 'send': ('跟进发信', '跟进发不出去')}
FIXES = {
    'keychain': '修法：在终端运行 security unlock-keychain ~/Library/Keychains/login.keychain-db，输入 Mac 开机密码。'
                '以后不想再遇到：运行一次 lark-cli config keychain-downgrade 和 setup_claude_token.sh（看板程序所在的文件夹里）。',
    'login': '修法：Lark 登录过期，在终端运行 lark-cli auth login 重新扫码。',
    'ai': '修法：Claude 命令行登录失效，在终端运行 setup_claude_token.sh（看板程序所在的文件夹里），浏览器里点授权。',
}
SYNC_SECONDS = 300
FOLLOWUP_GAP_SECONDS = 40
STATIC = {'app.js': 'application/javascript; charset=utf-8', 'app.css': 'text/css; charset=utf-8'}


def esc_html(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def say(*parts):
    print(time.strftime('%m-%d %H:%M:%S'), *parts, flush=True)


class App:
    def __init__(self):
        self.board = core.Board()
        self.token = secrets.token_urlsafe(24)
        self.ai_error = None
        self.ai_lock = threading.Lock()
        self.problems = {}  # kind -> {'since': t, 'alerted': t, 'text': str}

    def watch(self, kind, error):
        """Tell the user on Feishu when the mail sync or the AI has been broken for a while, and again when it recovers."""
        now = time.time()
        p = self.problems.get(kind)
        if not error:
            if p and p.get('alerted'):
                self.alert(f"{NAME}：{PROBLEM_NAMES[kind][0]}已恢复。")
            self.problems.pop(kind, None)
            return
        p = self.problems.setdefault(kind, {'since': now, 'alerted': 0})
        if now - p['since'] >= ALERT_AFTER[kind] and now - p['alerted'] >= 6 * 3600:
            fix = FIXES['keychain'] if '钥匙串' in error or 'keychain' in error.lower() else \
                FIXES['login'] if kind == 'mail' and ('token' in error.lower() or 'auth' in error.lower()) else \
                FIXES['ai'] if kind == 'ai' and '登录' in error else ''
            minutes = int((now - p['since']) / 60)
            self.alert(f"{NAME}：{PROBLEM_NAMES[kind][1]}已经 {minutes} 分钟。原因：{error[:200]}。{fix}")
            p['alerted'] = now

    def alert(self, text):
        if not ALERT_OPEN_ID:
            say('alert (no Feishu recipient set)', text[:80])
            return
        try:
            self.board.fs.send_text(ALERT_OPEN_ID, text)
            say('alert sent', text[:80])
        except Exception as e:  # noqa: BLE001 - an alert failing must never stop the board
            say('alert failed', e)

    # ---- background loops
    def sync_loop(self):
        first = True
        while True:
            try:
                deep = first and not core.meta_get(self.board.db, 'last_sync')
                result = self.board.sync(deep=deep)
                say('sync', json.dumps(result, ensure_ascii=False))
                first = False
                self.watch('mail', self.board.sync_error)
                self.ai_pass()
                self.watch('ai', None if self.ai_paused() else self.ai_error)
            except Exception as e:  # noqa: BLE001 - keep the loop alive, show the error on the board
                self.board.sync_error = f'{type(e).__name__}: {e}'[:300]
                say('sync error', self.board.sync_error)
                traceback.print_exc()
                self.watch('mail', self.board.sync_error)
            try:
                HEARTBEAT.write_text(json.dumps({'at': core.iso(), 'mail_error': self.board.sync_error, 'ai_error': self.ai_error},
                                                ensure_ascii=False))
            except OSError:
                pass
            self.board.wake.wait(SYNC_SECONDS)
            self.board.wake.clear()

    def ai_paused(self):
        try:
            until = float((core.DATA / 'ai_paused_until').read_text())
        except (OSError, ValueError):
            return None
        return until if time.time() < until else None

    def ai_pass(self):
        until = self.ai_paused()
        if until:
            self.ai_error = f"AI 暂停到 {time.strftime('%H:%M', time.localtime(until))}，之后自动恢复"
            return
        if not ai.available():
            self.ai_error = '这台 Mac 上没有找到 claude 命令行'
            return
        b = self.board
        with self.ai_lock:
            # attachments first: what a media kit says goes into the reading of the reply it came with
            for a in b.db.execute("SELECT key, message_id, path FROM attachments WHERE status='downloaded' AND data IS NULL").fetchall():
                try:
                    data = ai.read_attachment(a['path'])
                    b.db.execute("UPDATE attachments SET data=?, status='read' WHERE key=?", (json.dumps(data, ensure_ascii=False), a['key']))
                    b.db.execute('DELETE FROM ai_reads WHERE message_id=?', (a['message_id'],))
                    handles = json.loads((b.db.execute('SELECT handles FROM messages WHERE id=?', (a['message_id'],)).fetchone() or ['[]'])[0])
                    b.db.executemany('DELETE FROM suggestions WHERE handle=?', [(x,) for x in handles])  # redraft with the kit in view
                except ai.AIUnavailable:
                    raise
                except Exception as e:  # noqa: BLE001 - an unreadable file is marked, not retried forever
                    b.db.execute("UPDATE attachments SET status=? WHERE key=?", (f'unreadable: {str(e)[:120]}', a['key']))
                b.db.commit()
            done = {r[0] for r in b.db.execute("SELECT message_id FROM ai_reads WHERE data LIKE '%\"v\": 2%'")}
            todo = [m for m in b.db.execute("SELECT id, handles, subject, body, thread_id FROM messages WHERE kind='in' ORDER BY at")
                    if m['id'] not in done]
            try:
                for i in range(0, len(todo), 8):
                    items = []
                    for m in todo[i:i + 8]:
                        prev = b.db.execute("SELECT body FROM messages WHERE kind='out' AND thread_id=? ORDER BY at DESC LIMIT 1",
                                            (m['thread_id'],)).fetchone()
                        atts = [{'filename': x['filename'], **json.loads(x['data'])} for x in b.db.execute(
                            "SELECT filename, data FROM attachments WHERE message_id=? AND data IS NOT NULL", (m['id'],))]
                        items.append({'id': m['id'], 'creator': ', '.join(json.loads(m['handles'])), 'subject': m['subject'],
                                      'reply': core.strip_quoted(m['body'])[:3000],
                                      'our_last': (prev['body'] if prev else '')[:600], 'attachments': atts})
                    for mid, data in ai.read_replies(items).items():
                        data['v'] = 2  # quotes carry their kind (one video, bundle, add-on)
                        b.db.execute('INSERT OR REPLACE INTO ai_reads VALUES(?,?,?,?)',
                                     (mid, json.dumps(data, ensure_ascii=False), core.iso(), ai.MODEL))
                    b.db.commit()
                if todo:
                    with b.lock:
                        b.recompute()
                        b.write_back()
                    say('ai read', len(todo))
                # replies are built from the template; the AI prepares each one's opening and answers
                waiting = [h for h, st in b.states.items() if st['waiting']]
                stale = [h for h in waiting if self.suggestion_basis(h, 'parts') != self.parts_basis(h)]
                with ThreadPoolExecutor(3) as pool:
                    list(pool.map(self.make_parts, stale[:40]))
                if stale:
                    say('ai reply openings', min(len(stale), 40))
                self.profile_pass()
                self.ai_error = None
            except ai.AIUnavailable as e:
                self.ai_error = str(e)
            except Exception as e:  # noqa: BLE001
                self.ai_error = f'{type(e).__name__}: {e}'[:300]
                traceback.print_exc()

    def profile_pass(self, limit=100):
        """One-line portraits, people on today's lists first."""
        b = self.board
        have = {r[0]: json.loads(r[1]) for r in b.db.execute('SELECT handle, data FROM profiles')}
        q = b.queues()
        first = [r['handle'] for k in ('reply', 'quote', 'followup_due', 'bounce', 'followup_soon', 'dm') for r in q[k]]
        rest = sorted(b.states, key=lambda h: -(b.states[h]['last_activity'] or 0))
        # People you are deciding on get the student-audience check now; everyone else only needs a portrait.
        todo = [h for h in dict.fromkeys(first) if 'student_audience' not in have.get(h, {})]
        todo += [h for h in rest if h not in have and h not in todo]
        todo = todo[:limit]
        if not todo:
            return
        batches = [[core.profile_material(b.creators[h]) for h in todo[i:i + 10]] for i in range(0, len(todo), 10)]
        db = b.db
        with ThreadPoolExecutor(3) as pool:
            for out in pool.map(ai.profiles, batches):
                for h, data in out.items():
                    if h in b.creators:
                        db.execute('INSERT OR REPLACE INTO profiles VALUES(?,?,?)', (h, json.dumps(data, ensure_ascii=False), core.iso()))
                db.commit()
        with b.lock:
            b.recompute()
        say('ai profiles', len(todo))

    def suggestion_basis(self, h, mode):
        row = self.board.db.execute('SELECT basis FROM suggestions WHERE handle=? AND mode=?', (h, mode)).fetchone()
        return row['basis'] if row else None

    def parts_basis(self, h):
        return f"{self.board.states[h]['last_in_id']}|{ai.PARTS_VERSION}"

    def thread_for_ai(self, detail, last=6):
        return [{'who': SENDER_NAME if m['kind'] == 'out' else '对方', 'from': m['from'], 'date': core.md(m['at']),
                 'text': m['body'][:1500 if m['kind'] == 'out' else 3000],
                 'attachments': [{'filename': a['filename'], **(a['read'] or {})} for a in m.get('attachments') or [] if a.get('read')]}
                for m in detail['messages'] if m['kind'] in ('out', 'in')][-last:]

    def make_parts(self, h):
        """The AI's share of a reply: who to greet, an opening line, answers to questions the template does not cover."""
        b = self.board
        c, st = b.creators[h], b.states[h]
        detail = b.detail(h)
        prof = b.profiles.get(h) or {}
        mat = core.profile_material(c)
        context = {'creator': h, 'our_greeting_name': b.fu_greeting(h) if st['lang'] == '英文' else st['greeting'],
                   'reply_language': st['lang'], 'platform': c['platform'], 'country': c['country'],
                   'days_since_their_email': round((core.now_ms() - st['waiting_since']) / core.DAY, 1) if st['waiting_since'] else None,
                   'sender_has_answered_before': bool(st['answered_at']),
                   'latest_reply_category': (st['read'] or {}).get('category'), 'latest_reply_summary': (st['read'] or {}).get('summary'),
                   'creator_profile': {'portrait': prof.get('portrait') or '', 'display_name': mat.get('display_name') or '',
                                       'bio': mat.get('bio') or '', 'recent_captions': (mat.get('recent_captions') or [])[:5]},
                   'thread': self.thread_for_ai(detail, 4)}
        try:
            out = ai.reply_parts(context)
        except ai.AIUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - one creator failing must not stop the rest
            say('reply parts failed', h, str(e)[:160])
            return None
        b.db.execute('INSERT OR REPLACE INTO suggestions(handle, mode, basis, text, note, at, zh, pv) VALUES(?,?,?,?,?,?,?,?)',
                     (h, 'parts', self.parts_basis(h), json.dumps(out, ensure_ascii=False), out.get('note') or '', core.iso(), '',
                      ai.PARTS_VERSION))
        b.db.commit()
        return out

    def composer(self, h):
        """What the reply box needs: the AI's opening (if ready), the starting controls, and the creator's own quote."""
        b = self.board
        c, st = b.creators[h], b.states[h]
        row = b.db.execute("SELECT basis, text, note FROM suggestions WHERE handle=? AND mode='parts'", (h,)).fetchone()
        parts = json.loads(row['text']) if row else {}
        fresh = bool(row) and row['basis'] == self.parts_basis(h)
        lang = parts.get('language') or st['lang']
        if not (parts.get('addressee') or '').strip():
            parts['addressee'] = b.fu_greeting(h) if lang == '英文' else (st['greeting'] or h)
        quote = core.main_quote(st['quotes'], c['median'])
        category = (st['read'] or {}).get('category')
        d = compose.defaults(lang, quote, c['platform'], parts.get('recipient'), bool(st['answered_at']), category,
                             core.number(c['board'].get('心理价位')))
        if fresh and parts.get('scenario') in ('offer', 'askrate', 'thanks') and not (parts['scenario'] == 'askrate' and quote):
            d['scenario'] = parts['scenario']
        return {'parts': parts, 'fresh': fresh, 'note': row['note'] if row else '', 'defaults': d, 'lang': lang,
                'addons': compose.addon_labels(),
                'quoted': bool(st['quotes']),
                'quote': {k: quote.get(k) for k in ('item', 'amount', 'currency', 'tax')} if quote else None}

    def draft(self, h, mode, intent=None):
        b = self.board
        c, st = b.creators[h], b.states[h]
        detail = b.detail(h)
        thread = self.thread_for_ai(detail)
        context = {'creator': h, 'name_we_greeted_them_with': st['greeting'], 'platform': c['platform'], 'country': c['country'],
                   'followers': c['followers'], 'median_views_last10': c['median'],
                   'sender_mind_price_usd': core.number(c['board'].get('心理价位')),
                   'status': st['status'] or None, 'quotes_so_far': st['quotes'], 'reply_language': st['lang'],
                   'latest_reply_category': (st['read'] or {}).get('category'), 'latest_reply_summary': (st['read'] or {}).get('summary'),
                   'thread': thread}
        out = ai.draft_reply(context, mode, intent)
        b.db.execute('INSERT OR REPLACE INTO suggestions(handle, mode, basis, text, note, at, zh, pv) VALUES(?,?,?,?,?,?,?,?)',
                     (h, mode, b.basis(h), out['text'], out['note'], core.iso(), out.get('zh') or '', ai.DRAFT_VERSION))
        b.db.commit()
        return out

    def auto_followups(self):
        """With automatic follow-ups on, due ones (both rounds) go out by themselves. They are put in the same queue as the ones
        sent by hand, so the page counts them down and 暂停发送 can stop them; each is re-checked right before it goes out."""
        b = self.board
        if not core.auto_followup(b.db) or not b.states:
            return 0
        failed = {(r['handle'], r['kind']) for r in b.db.execute(  # a send that failed today is not retried by itself
            "SELECT handle, kind FROM sends WHERE status='failed' AND kind LIKE 'followup%' AND created_at>=?",
            (core.iso(core.now_ms() - core.DAY),))}
        with b.lock:
            items = [(r['handle'], r['due']['step'],
                      core.followup_text(b.states[r['handle']]['lang'], r['due']['step'], b.fu_greeting(r['handle']), r['handle']))
                     for r in b.queues()['followup_due'] if (r['handle'], f"followup{r['due']['step']}") not in failed]
        for h, step, text in items:
            b.db.execute('INSERT INTO sends(created_at, handle, kind, body, status, opts) VALUES(?,?,?,?,?,?)',
                         (core.iso(), h, f'followup{step}', text, 'queued', json.dumps({'auto': True})))
        b.db.commit()
        if items:
            say('auto followups queued', len(items))
        return len(items)

    def send_loop(self):
        db = core.connect()
        # a follow-up that was going out when the board stopped: it may or may not have left, so it is not sent again; the next
        # mail sync shows whether it went, and if it is still due the day after, the automatic follow-up queues it again
        stuck = db.execute("UPDATE sends SET status='failed', result=?, done_at=? WHERE status='sending'",
                           (json.dumps({'error': '看板重启时这封正在发，不确定发出没有；下一轮同步会看到'}, ensure_ascii=False), core.iso())).rowcount
        db.commit()
        if stuck:
            say('sends interrupted by a restart', stuck)
        checked = 0
        while True:
            if time.time() - checked > 60:
                checked = time.time()
                try:
                    self.auto_followups()
                except Exception as e:  # noqa: BLE001 - keep the sender alive
                    say('auto followups error', e)
                    traceback.print_exc()
            if self.board.sync_error or not self.board.states:  # mailbox unreachable or still loading: hold everything
                time.sleep(30)
                continue
            row = db.execute("SELECT * FROM sends WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                time.sleep(5)
                continue
            opts = json.loads(row['opts'] or '{}')
            db.execute("UPDATE sends SET status='sending' WHERE id=?", (row['id'],))
            db.commit()
            try:
                result = self.board.send_followup(row['handle'], int(row['kind'][-1]), row['body'], early=bool(opts.get('early')))
                status = 'skipped' if 'skipped' in result else 'sent'
                to = result.get('to') or []
                self.watch('send', None)
            except core.SendUncertain as e:  # the send itself failed: may have gone out, so not retried; the next sync shows which
                status, result, to = 'failed', {'error': str(e)[:300]}, []
                self.watch('send', result['error'])
            except (core.LarkError, subprocess.TimeoutExpired) as e:  # the re-check failed, or Lark turned the send away: nothing went out
                error = f'{type(e).__name__}: {e}'[:300]
                self.watch('send', error)
                opts['attempts'] = opts.get('attempts', 0) + 1
                if opts['attempts'] < 3:  # a mailbox hiccup: same mail again in five minutes
                    db.execute("UPDATE sends SET status='queued', opts=?, result=? WHERE id=?",
                               (json.dumps(opts), json.dumps({'error': error}, ensure_ascii=False), row['id']))
                    db.commit()
                    say('followup', row['handle'], 'retry later', error[:120])
                    time.sleep(300)
                    continue
                status, result, to = 'failed', {'error': error}, []
            except Exception as e:  # noqa: BLE001
                status, result, to = 'failed', {'error': f'{type(e).__name__}: {e}'[:300]}, []
                self.watch('send', result['error'])
            db.execute('UPDATE sends SET status=?, result=?, to_addrs=?, done_at=?, opts=? WHERE id=?',
                       (status, json.dumps(result, ensure_ascii=False)[:2000], json.dumps(to), core.iso(), json.dumps(opts), row['id']))
            db.commit()
            say('followup', row['handle'], status)
            if status == 'sent':
                time.sleep(FOLLOWUP_GAP_SECONDS)

    # ---- API
    def list_rows(self, view):
        b = self.board
        if view == 'all':
            return [b.row(h) for h in b.states]
        q = b.queues()
        if view == 'followup':
            rows = []
            for r in q['followup_due'] + q['followup_soon']:
                st = b.states[r['handle']]
                rows.append({**r, 'due_now': r['due']['at'] <= core.now_ms(),
                             'text': core.followup_text(st['lang'], r['due']['step'], b.fu_greeting(r['handle']), r['handle']),
                             'to': st['to_addrs'], 'easykol': r['due'].get('easykol')})
            return rows
        if view == 'dm':
            return [{**r, 'dm': core.dm_text(r['lang'])} for r in q['dm']]
        if view == 'reply':
            return [{**r, 'has_draft': self.suggestion_basis(r['handle'], 'parts') == self.parts_basis(r['handle'])} for r in q['reply']]
        return q.get(view, [])

    def jobs(self):
        db = self.board.db
        counts = {r['status']: r['n'] for r in db.execute(
            "SELECT status, COUNT(*) n FROM sends WHERE kind LIKE 'followup%' AND (created_at>=? OR status IN ('queued', 'sending')) "
            "GROUP BY status", (core.iso(core.now_ms() - core.DAY),))}
        recent = [dict(r) for r in db.execute(
            "SELECT id, handle, kind, status, done_at, result FROM sends ORDER BY id DESC LIMIT 15")]
        return {'counts': counts, 'recent': recent}

    def get(self, path, qs):
        b = self.board
        if path == '/api/overview':
            o = b.overview() if b.states else {'loading': True, 'sync_error': b.sync_error}
            o['mail_synced_at'] = b.synced_at or core.meta_get(b.db, 'mail_synced_at')
            o.update(ai={'ok': ai.available() and not self.ai_error, 'error': self.ai_error, 'model': ai.MODEL}, jobs=self.jobs())
            return o
        if path == '/api/list':
            return self.list_rows(qs.get('view', ['reply'])[0])
        if path == '/api/creator':
            h = qs['h'][0]
            d = b.detail(h)
            if b.states[h]['last_in_id']:
                d['composer'] = self.composer(h)
            d['ai_ready'] = ai.available() and not self.ai_error and not self.ai_paused()
            return d
        if path == '/api/jobs':
            return self.jobs()
        raise KeyError(path)

    def post(self, path, body):
        b = self.board
        h = body.get('h')
        if h is not None and h not in b.states:
            raise ValueError('找不到这个博主')
        if path == '/api/sync':
            b.wake.set()
            return {'ok': True}
        if path == '/api/suggest':
            if self.ai_paused():
                raise ai.AIUnavailable(self.ai_error or 'AI 暂停中')
            intent = (body.get('intent') or '').strip()[:2000] or None
            return self.draft(h, body.get('mode', 'reply'), intent)
        if path == '/api/compose':  # rebuild the reply from the template with the current controls (no AI call)
            cm = self.composer(h)
            return compose.compose(cm['lang'], cm['parts'], body.get('ctl') or {}, cm['quoted'])
        if path == '/api/parts':  # write the opening again now
            if self.ai_paused():
                raise ai.AIUnavailable(self.ai_error or 'AI 暂停中')
            if not self.make_parts(h):
                raise ValueError('AI 这次没写出来，再点一次试试')
            return self.composer(h)
        if path == '/api/revise':  # the user describes a change in Chinese; the email in the box is edited, the rest kept as is
            if self.ai_paused():
                raise ai.AIUnavailable(self.ai_error or 'AI 暂停中')
            gist, current = (body.get('gist') or '').strip()[:1000], (body.get('current') or '').strip()[:6000]
            if not gist:
                raise ValueError('先写要改什么')
            if len(current) < 10:
                raise ValueError('信是空的，先选好这封做什么，或者用「整封重写」')
            cm, st = self.composer(h), b.states[h]
            return ai.revise_reply({'language': cm['lang'], 'recipient': cm['parts'].get('recipient') or 'creator',
                                    'creator_ref': cm['parts'].get('creator_ref') or '',
                                    'their_latest_email_summary': (st['read'] or {}).get('summary') or '',
                                    'current_email': current}, gist)
        if path == '/api/custom':  # one line the user wrote in Chinese, in the creator's language
            if self.ai_paused():
                raise ai.AIUnavailable(self.ai_error or 'AI 暂停中')
            gist = (body.get('gist') or '').strip()[:600]
            if not gist:
                raise ValueError('先写一句')
            cm = self.composer(h)
            return ai.custom_line({'language': cm['lang'], 'recipient': cm['parts'].get('recipient') or 'creator',
                                   'creator_ref': cm['parts'].get('creator_ref') or '', 'current_email': (body.get('current') or '')[:3000]},
                                  gist)
        if path == '/api/send':
            text = (body.get('body') or '').strip()
            if len(text) < 10:
                raise ValueError('回复内容太短')
            if compose.MISSING in text:
                raise ValueError('信里还有【价格】没填')
            if body.get('mode') == 'nudge':
                return b.send_nudge(h, text)
            result = b.send_reply(h, text, close=body.get('mode') == 'close')
            if body.get('ctl'):  # which template, layout and add-ons the user actually sends, to tune the defaults later
                core.log_action(b.db, h, 'reply_ctl', body['ctl'])
            return result
        if path == '/api/draft':
            return b.save_draft(h, (body.get('body') or '').strip())
        if path == '/api/fields':
            b.set_fields(h, body.get('changes') or {})
            return {'ok': True}
        if path == '/api/close':
            b.close(h)
            return {'ok': True}
        if path == '/api/reopen':
            b.reopen(h)
            return {'ok': True}
        if path == '/api/dismiss':
            b.dismiss(h)
            return {'ok': True}
        if path == '/api/dm':
            b.mark_dm(h)
            return {'ok': True}
        if path == '/api/bounce_done':
            b.bounce_done(h)
            return {'ok': True}
        if path == '/api/resend':
            return b.resend_bounced(h, body.get('email') or '')
        if path == '/api/followups':  # sending now by hand, including ones not due yet
            items = body.get('items') or []
            for it in items:
                if it.get('h') not in b.states or it.get('step') not in (1, 2) or len((it.get('body') or '').strip()) < 10:
                    raise ValueError('跟进内容不完整')
            pending = {r[0] for r in b.db.execute("SELECT handle FROM sends WHERE status IN ('queued', 'sending')")}
            fresh = [it for it in items if it['h'] not in pending]
            for it in fresh:
                b.db.execute('INSERT INTO sends(created_at, handle, kind, body, status, opts) VALUES(?,?,?,?,?,?)',
                             (core.iso(), it['h'], f"followup{it['step']}", it['body'].strip(), 'queued',
                              json.dumps({'early': bool(it.get('early')), 'manual': True})))
            b.db.commit()
            return {'queued': len(fresh), 'skipped': len(items) - len(fresh)}
        if path == '/api/auto_followup':
            on = bool(body.get('on'))
            core.meta_set(b.db, 'auto_followup', {'on': on, 'at': core.iso()})
            cancelled = 0
            if not on:  # switching off also takes back the automatic ones still waiting in the queue
                cancelled = b.db.execute("UPDATE sends SET status='cancelled', result=? WHERE status='queued' AND opts LIKE '%\"auto\": true%'",
                                         (json.dumps({'skipped': '自动跟进已关'}, ensure_ascii=False),)).rowcount
                b.db.commit()
            core.log_action(b.db, None, 'auto_followup', {'on': on, 'cancelled': cancelled})
            return {'on': on, 'cancelled': cancelled}
        if path == '/api/followups/stop':  # the brake: nothing more goes out until the user turns it back on
            core.meta_set(b.db, 'auto_followup', {'on': False, 'at': core.iso()})
            cancelled = b.db.execute("UPDATE sends SET status='cancelled', result=? WHERE status='queued'",
                                     (json.dumps({'skipped': '已暂停发送'}, ensure_ascii=False),)).rowcount
            b.db.commit()
            core.log_action(b.db, None, 'followups_stop', {'cancelled': cancelled})
            return {'cancelled': cancelled}
        raise KeyError(path)


def make_handler(app):
    allowed_hosts = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'KOLBoard'

        def log_message(self, fmt, *args):
            pass

        def send_json(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def guard(self, write=False):
            # Only this Mac, only the page this server handed out (blocks DNS rebinding and cross-site requests).
            if self.headers.get('Host') not in allowed_hosts:
                return False
            origin = self.headers.get('Origin')
            if write and origin and origin not in {f'http://{x}' for x in allowed_hosts}:
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return self.send_json(403, {'error': 'forbidden'})
            url = urlsplit(self.path)
            if url.path in ('/', '/index.html'):
                html = (HERE / 'static/index.html').read_text().replace('{{TOKEN}}', app.token).replace('{{NAME}}', esc_html(NAME))
                html = html.replace('{{MARK}}', esc_html(NAME[:1].upper() or 'C')).replace(
                    '{{AUDIENCE}}', esc_html(core.settings.PRODUCT.get('audience_short') or '目标观众')).replace(
                    '{{PRODUCT}}', esc_html(core.settings.PRODUCT.get('name') or '')).replace(
                    '{{PRIORITY}}', esc_html(json.dumps(core.settings.get('priority', default={}) or {}))).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                return self.wfile.write(html)
            if url.path == '/att':
                # opened in a new tab, so the page token comes as a query parameter here
                qs = parse_qs(url.query)
                if qs.get('t', [''])[0] != app.token:
                    return self.send_json(403, {'error': '页面已过期，请刷新'})
                row = app.board.db.execute('SELECT filename, path FROM attachments WHERE key=?', (qs.get('k', [''])[0],)).fetchone()
                if not row or not row['path'] or not Path(row['path']).is_file() or not str(Path(row['path']).resolve()).startswith(str(core.ATT_DIR)):
                    return self.send_json(404, {'error': '附件还没下载'})
                data = Path(row['path']).read_bytes()
                ext = Path(row['path']).suffix.lstrip('.')
                ctype = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif'}.get(ext, 'application/octet-stream')
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Disposition', 'inline')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; plugin-types application/pdf")
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if url.path.startswith('/static/') and url.path[8:] in STATIC:
                data = (HERE / 'static' / url.path[8:]).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', STATIC[url.path[8:]])
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if self.headers.get('X-Board-Token') != app.token:
                return self.send_json(403, {'error': '页面已过期，请刷新'})
            try:
                return self.send_json(200, app.get(url.path, parse_qs(url.query)))
            except KeyError as e:
                return self.send_json(404, {'error': f'not found: {e}'})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self.send_json(500, {'error': f'{type(e).__name__}: {e}'[:400]})

        def do_POST(self):
            if not self.guard(write=True) or self.headers.get('X-Board-Token') != app.token:
                return self.send_json(403, {'error': '页面已过期，请刷新'})
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                return self.send_json(200, app.post(urlsplit(self.path).path, body))
            except (ValueError, core.LarkError, core.FeishuError) as e:
                return self.send_json(400, {'error': str(e)[:400]})
            except ai.AIUnavailable as e:
                return self.send_json(400, {'error': f'AI 暂不可用：{e}'})
            except KeyError as e:
                return self.send_json(404, {'error': f'not found: {e}'})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self.send_json(500, {'error': f'{type(e).__name__}: {e}'[:400]})

    return Handler


def serve():
    app = App()
    threading.Thread(target=app.sync_loop, daemon=True, name='sync').start()
    threading.Thread(target=app.send_loop, daemon=True, name='send').start()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(app))
    say(f'KOL board on http://127.0.0.1:{PORT}')
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('cmd', choices=['serve', 'sync'])
    parser.add_argument('--deep', action='store_true')
    parser.add_argument('--no-write', action='store_true')
    args = parser.parse_args()
    if args.cmd == 'serve':
        return serve()
    b = core.Board()
    print(json.dumps(b.sync(deep=args.deep, write_back=not args.no_write), ensure_ascii=False))
    q = b.queues()
    print(json.dumps({k: len(v) for k, v in q.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
