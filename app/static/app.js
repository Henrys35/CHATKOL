'use strict';
const TOKEN = document.querySelector('meta[name="board-token"]').content;
const metaTag = n => (document.querySelector(`meta[name="${n}"]`) || {}).content || '';
const AUD = metaTag('board-audience') || '目标观众';  // who the creators' audience should be (config: product.audience_short)
const PRODUCT = metaTag('board-product');
const PRIO = (() => { try { return JSON.parse(metaTag('board-priority') || '{}'); } catch (e) { return {}; } })();
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { view: 'today', queue: 'reply', ov: null, lists: {}, all: null, search: '', chip: '全部',
  filters: { market: '', progress: '', status: '' }, drawer: null, fuEdits: {}, fuOpen: new Set(), mode: 'reply' };

async function api(path, body) {
  const r = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'X-Board-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  let d = {};
  try { d = await r.json(); } catch (e) { /* empty body */ }
  if (r.status === 403) {
    // the board service restarted, so this page's token is stale: reload once to pick up the new one
    let last = 0;
    try { last = Number(sessionStorage.getItem('kb.reloaded') || 0); sessionStorage.setItem('kb.reloaded', String(Date.now())); } catch (e) { /* ok */ }
    if (Date.now() - last > 30000) location.reload();
  }
  if (!r.ok) throw new Error(d.error || `请求失败（${r.status}）`);
  return d;
}
let toastTimer;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast on' + (bad ? ' bad' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'toast' + (bad ? ' bad' : ''); }, bad ? 5200 : 2600);
}
const pad = n => String(n).padStart(2, '0');
function fmt(ms) { if (!ms) return ''; const d = new Date(ms); return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function day(ms) { if (!ms) return ''; const d = new Date(ms); return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`; }
function ago(ms) {
  const m = Math.max(0, (Date.now() - ms) / 60000);
  if (m < 60) return `${Math.round(m)} 分钟`;
  if (m < 60 * 24) return `${Math.round(m / 60)} 小时`;
  return `${Math.round(m / 1440)} 天`;
}
function wan(n) { if (n == null) return '—'; return n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : Math.round(n).toLocaleString(); }
const money = q => `${esc(q.item)} ${esc(q.currency)} ${Math.round(q.amount).toLocaleString()}`;
const MARKET = { '北美': 'blue', '其他英语国家': 'green', '台湾': 'red', '韩国': 'violet', '日本': 'amber', '其他地区': '' };
function avatar(r) {
  const tone = MARKET[r.market] || '';
  const bg = tone ? `var(--${tone}-weak)` : 'var(--gray-weak)';
  const fg = tone ? `var(--${tone})` : 'var(--muted)';
  const letter = (r.handle || '?').replace(/[^A-Za-z0-9぀-ヿ一-鿿가-힣]/g, '').slice(0, 1).toUpperCase() || '?';
  return `<div class="avatar" style="background:${bg};color:${fg}">${esc(letter)}</div>`;
}
function meta(r) {
  const bits = [r.platform, r.followers ? `${wan(r.followers)} 粉` : '', r.median ? `中位 ${wan(r.median)}` : '',
    r.ratio ? `播粉比 ${r.ratio.toFixed(2)}` : ''];
  return bits.filter(Boolean).map(esc).join(' · ');
}
function whoCell(r, extra) {
  return `<div class="who"><div class="h"><span class="flag">${r.flag || ''}</span>${esc(r.handle)}${r.own_pick ? '<span class="chip violet tiny">你收藏的</span>' : ''}</div>
    <div class="pt" title="${esc(r.portrait)}">${esc(r.portrait || '画像生成中…')}</div>
    <div class="m">${extra ? esc(extra) : meta(r)}</div></div>`;
}
const tagChips = (tags, n = 4) => (tags || []).slice(0, n).map(t => `<span class="tag">${esc(t)}</span>`).join('');
const PROG = { '对方已回复': 'accent', '已发首封': '', '已跟进一次': 'blue', '已跟进两次': 'blue', '退信': 'red', '已发私信': 'violet', '未联系': '' };
const STAT = { '谈价中': 'amber', '待档期': 'blue', '已签约': 'green', '已发布': 'green', '已付款': 'green', '已放弃': '', '不再联系': '' };
const chip = (text, tone) => text ? `<span class="chip ${tone || ''}">${esc(text)}</span>` : '';
const safeUrl = u => /^https?:\/\//i.test(u || '') ? u : '';

// ------------------------------------------------------------------ top bar and navigation
function renderTop() {
  const o = S.ov || {};
  const sync = $('#sync-pill');
  if (o.sync_error) { sync.className = 'pill bad'; sync.textContent = `邮箱同步停了${o.mail_synced_at ? '（' + fmt(o.mail_synced_at) + '）' : ''}`; sync.title = o.sync_error; }
  else if (!o.synced_at) { sync.className = 'pill warn'; sync.textContent = '第一次同步中…'; sync.title = ''; }
  else { sync.className = 'pill ok'; sync.textContent = `已同步 ${fmt(o.synced_at).slice(6)}`; sync.title = '每 5 分钟从 Lark 和飞书自动同步'; }
  if (o.auth && o.auth.ok === false) { sync.className = 'pill bad'; sync.textContent = 'Lark 登录失效'; sync.title = o.auth.error || '需要重新扫码登录 lark-cli'; }
  const aiPill = $('#ai-pill');
  const a = o.ai || {};
  aiPill.className = 'pill ' + (a.ok ? 'ok' : 'warn');
  aiPill.textContent = a.ok ? 'AI 读信 · Sonnet 5.5' : 'AI 未连接';
  aiPill.title = a.ok ? '' : (a.error || '');
  const c = o.counts || {};
  $('#nav-today').textContent = (c.reply || 0) + (autoOn() ? 0 : (c.followup_due || 0)) + (c.bounce || 0) || '';
  $('#nav-all').textContent = o.funnel && o.funnel[0] ? o.funnel[0].total : '';
}
function setView(v) {
  S.view = v;
  $$('.nav').forEach(a => a.classList.toggle('active', a.dataset.view === v));
  render();
}
$$('.nav').forEach(a => a.addEventListener('click', () => setView(a.dataset.view)));
$('#refresh').addEventListener('click', async () => {
  try { await api('/api/sync', {}); toast('已开始同步，几十秒后自动刷新'); setTimeout(loadOverview, 15000); setTimeout(loadOverview, 45000); }
  catch (e) { toast(e.message, true); }
});
$('#search').addEventListener('input', e => {
  S.search = e.target.value.trim().toLowerCase();
  if (S.view !== 'all') setView('all'); else render();
});

// ------------------------------------------------------------------ today
const QUEUES = [
  { k: 'hopeful', label: '有希望！', color: 'var(--green)', hot: c => c.hopeful_waiting > 0,
    hint: c => !c.hopeful ? '还没有' : [c.hopeful_waiting ? `${c.hopeful_waiting} 个轮到你回` : '', c.hopeful_old ? `${c.hopeful_old} 个超过 3 天没动静` : '']
      .filter(Boolean).join(' · ') || '都在等对方回，3 天内' },
  { k: 'reply', label: '待你回复', color: 'var(--red)', hot: c => c.reply_old > 0,
    hint: c => [c.reply - c.reply_old ? `${c.reply - c.reply_old} 封 24 小时内新到` : '', c.reply_old ? `${c.reply_old} 个超过 24 小时` : '']
      .filter(Boolean).join(' · ') || '没有在等你回的' },
  { k: 'quote', label: '报价待你定价', color: 'var(--amber)', hint: () => '点开填价格，信就拼好了' },
  { k: 'followup', label: '该跟进', color: 'var(--blue)', hint: followupHint },
  { k: 'bounce', label: '退信', color: 'var(--faint)', hint: () => '换邮箱重发或改私信' },
  { k: 'dm', label: '私信待发', color: 'var(--violet)', hint: c => Object.entries(c.dm_langs || {}).filter(([, n]) => n).map(([l, n]) => `${l} ${n}`).join(' · ') || '没有' },
];
const QUEUE_SUB = {
  hopeful: '你发过触达、对方回了、你也回过、还在往来的博主，不含不合作和对方拒绝的。轮到你回的标红排最前；在等对方的，超过 3 天没动静标黄，可以点开写一封轻松的提醒。',
  reply: '对方说了话、你还没回的。点开看 AI 读信、定价参考和起草好的回复。',
  quote: '已经报价、你还没回的。点开看内容、国家和数据，在回复框上面填价格，回信按模板拼好，看一眼再发；也可以直接在这里填出价点「出稿」。',
  followup: '到期的跟进会在原邮件线程里发出。可以改内容、取消勾选；发送前会再查一遍，期间回了信的自动跳过。',
  bounce: '退回来的信。地址前多了符号的可以改好重发；对方服务器临时故障的可以原地址重发。',
  dm: '没有公开邮箱的博主。复制对应语言的私信模板，去 TikTok 或 Instagram 发出后点「已发私信」。',
};
// with automatic follow-ups on, due ones go out by themselves (both rounds); everything can still be sent at once by hand
const autoOn = () => !!(S.ov && S.ov.auto_followup);
const FU_SUB_AUTO = '到期自动在原邮件线程里发出：首封后第 3 天跟进一，再隔 7 天跟进二，每 40 秒一封。发前逐封再查，回了信、退信或点了不合作的自动跳过。下面是 3 天内要发的，想提前发点「现在发」。';
const FU_SUB_MANUAL = '自动跟进关着：到期的不会自己发出。点「现在全部发」或单行的「现在发」，也可以把自动跟进打开。';
function followupHint(c) {
  if (!autoOn()) return c.followup_soon ? `另有 ${c.followup_soon} 封 3 天内到期` : '3 天内没有到期的';
  if (c.followup_queued) return `自动发送中 · 还剩 ${c.followup_queued} 封`;
  const next = (c.followup_days || []).find(d => d.day !== 'now');
  return next ? `自动发 · ${next.day} ${next.n} 封` : '自动发 · 3 天内没有到期的';
}
function cardCount(k, c) {
  if (k !== 'followup') return c[k];
  return autoOn() ? (c.followup_queued || 0) + (c.followup_due || 0) + (c.followup_soon || 0) : c.followup_due;
}
function bannersHtml() {
  const jobs = S.ov && S.ov.jobs ? S.ov.jobs.counts : {};
  const banners = [];
  if (S.ov && S.ov.sync_error) {
    const keychain = S.ov.sync_error.includes('钥匙串');
    banners.push(`<div class="banner bad"><div><b>邮箱同步停了：</b>${esc(S.ov.sync_error)}。看板上的邮件停在 ${esc(fmt(S.ov.mail_synced_at) || '上次同步')}，之后的新回信和发出的信都还没进来，跟进也先停着。
      ${keychain ? '<br>解决：在终端运行 <code>security unlock-keychain ~/Library/Keychains/login.keychain-db</code>，输入 Mac 开机密码，解锁后几分钟内自动恢复。' : ''}</div></div>`);
  }
  if (S.ov && S.ov.auth && S.ov.auth.ok === false) banners.push(`<div class="banner bad">Lark 邮箱登录失效，看板读不到新邮件、也发不了信。需要在终端重新扫码登录 lark-cli。</div>`);
  if (S.ov && S.ov.ai && !S.ov.ai.ok) banners.push(`<div class="banner">AI 还没连上（${esc(S.ov.ai.error || '')}），读信摘要和起草回复暂时没有，其他功能正常，回复可以直接手写。</div>`);
  const left = (jobs.queued || 0) + (jobs.sending || 0);
  const tail = `24 小时内已发 ${jobs.sent || 0} 封${jobs.skipped ? `，跳过 ${jobs.skipped} 封（回了信、退信或点了不合作）` : ''}${jobs.failed ? `，没发出去 ${jobs.failed} 封` : ''}`;
  if (left > 0) banners.push(`<div class="banner info"><span class="grow">跟进发送中：还剩 ${left} 封，每 40 秒一封，约 ${Math.max(1, Math.round(left * 40 / 60))} 分钟发完；${tail}。</span>
    <button class="btn small" id="fu-stop">暂停发送</button></div>`);
  else if (jobs.failed) banners.push(`<div class="banner bad">${tail}。没发出去的${autoOn() ? ' 24 小时后自动再试，' : ''}也可以在「该跟进」里点「现在发」。</div>`);
  return banners.join('');
}
function renderBanners() {
  const el = $('#banners');
  if (!el) return;
  el.innerHTML = bannersHtml();
  const stop = $('#fu-stop', el);
  if (stop) stop.addEventListener('click', stopFollowups);
}
async function stopFollowups() {
  const jobs = S.ov && S.ov.jobs ? S.ov.jobs.counts : {};
  const ok = await confirmBox({ title: '暂停发送跟进', ok: '暂停',
    kv: [['排队中的', `还没发出的 ${jobs.queued || 0} 封全部取消（正在发的那一封除外）`], ['自动跟进', '同时关掉；要恢复，在「该跟进」里打开']] });
  if (!ok) return;
  try { const d = await api('/api/followups/stop', {}); toast(`已暂停，取消了 ${d.cancelled} 封`); refreshInPlace(null); }
  catch (e) { toast(e.message, true); }
}
async function renderToday(main) {
  const c = (S.ov && S.ov.counts) || {};
  const now = new Date();
  main.innerHTML = `
    <div class="page-head"><div><h1>今天要处理</h1><div class="sub">${now.getMonth() + 1} 月 ${now.getDate()} 日 · 从左到右清完就行</div></div></div>
    <div id="banners"></div>
    <div class="cards">${QUEUES.map(q => `
      <div class="card ${S.queue === q.k ? 'sel' : ''}" data-q="${q.k}">
        <div class="label"><span class="dot" style="background:${q.color}"></span>${q.label}</div>
        <div class="num">${cardCount(q.k, c) ?? 0}</div>
        <div class="hint ${q.hot && q.hot(c) ? 'hot' : ''}">${esc(q.hint(c))}</div>
      </div>`).join('')}</div>
    <div class="panel" id="queue"><div class="empty">正在加载…</div></div>`;
  renderBanners();
  $$('.card', main).forEach(el => el.addEventListener('click', () => { S.queue = el.dataset.q; S.chip = '全部'; render(); }));
  await renderQueue();
}
// time order per list, remembered in this browser (default: replies oldest first, everything else newest first)
// Default order: how well the creator fits (the audience the config asks for first), not price.
const SORT_DEFAULT = { hopeful: 'fit', reply: 'fit', quote: 'fit', followup: 'fit', bounce: 'fit', dm: 'fit' };
function loadSorts() { try { return JSON.parse(localStorage.getItem('board-sorts-v2') || '{}'); } catch (e) { return {}; } }
S.sorts = loadSorts();
try { S.allSort = localStorage.getItem('board-all-sort-v2') || 'fit'; } catch (e) { S.allSort = 'fit'; }
function sortDir(view) { return S.sorts[view] || SORT_DEFAULT[view] || 'fit'; }
function setSort(view, dir) { S.sorts[view] = dir; try { localStorage.setItem('board-sorts-v2', JSON.stringify(S.sorts)); } catch (e) { /* private mode */ } }
// audience-fit tier (AI portrait), then the priority line from the config (views/followers and median views), then views/followers
const FIT_TIER = { '是': 2, '可能': 1, '不是': 0 };
function fitCompare(a, b) {
  const ta = FIT_TIER[a.fit] ?? 1, tb = FIT_TIER[b.fit] ?? 1;
  if (ta !== tb) return tb - ta;
  const line = r => (PRIO.min_ratio != null && PRIO.min_median != null && r.ratio >= PRIO.min_ratio && r.median >= PRIO.min_median) ? 1 : 0;
  const pa = line(a), pb = line(b);
  if (pa !== pb) return pb - pa;
  return (b.ratio || 0) - (a.ratio || 0);
}
function timeOf(view, r) {
  if (view === 'reply') return r.waiting_since || 0;
  if (view === 'followup') return r.due ? r.due.at : 0;
  if (view === 'bounce') return r.bounced ? r.bounced.at : 0;
  if (view === 'hopeful') return Math.max(r.answered_at || 0, r.waiting_since || 0);
  if (view === 'first') return r.first_at || 0;
  if (view === 'dm') return r.added_at || 0;
  return r.last_activity || r.first_at || 0;
}
const SORT_LABEL = { hopeful: '最近往来', reply: '收到回信', quote: '最近动态', followup: '到期日', bounce: '退信时间', dm: '入表时间' };
function sortSelect(view) {
  const d = sortDir(view), label = SORT_LABEL[view] || '时间';
  return `<select class="sort" data-sort="${view}" title="排序"><option value="fit" ${d === 'fit' ? 'selected' : ''}>画像最合适的在前</option>
    <option value="new" ${d === 'new' ? 'selected' : ''}>${label}：从新到旧</option>
    <option value="old" ${d === 'old' ? 'selected' : ''}>${label}：从旧到新</option></select>`;
}
async function getList(view) {
  const rows = await api('/api/list?view=' + view);
  S.lists[view] = rows;
  return rows;
}
function marketChips(rows) {
  const markets = ['全部', ...new Set(rows.map(r => r.market))];
  return `<div class="chips-filter">${markets.map(m => `<button class="fchip ${S.chip === m ? 'on' : ''}" data-m="${esc(m)}">${esc(m)}${m === '全部' ? ` ${rows.length}` : ` ${rows.filter(r => r.market === m).length}`}</button>`).join('')}</div>`;
}
async function renderQueue() {
  const box = $('#queue');
  if (!box) return;
  const view = S.queue;
  let rows;
  try { rows = await getList(view); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (S.view !== 'today' || S.queue !== view) return;
  const dir = sortDir(view);
  const shown = rows.filter(r => !isHidden(r.handle)).filter(r => S.chip === '全部' || r.market === S.chip)
    .map((r, i) => [r, i]).sort((a, b) => (dir === 'fit' ? fitCompare(a[0], b[0])
      : (dir === 'new' ? -1 : 1) * (timeOf(view, a[0]) - timeOf(view, b[0]))) || a[1] - b[1]).map(x => x[0]);
  const title = QUEUES.find(q => q.k === view).label;
  let body;
  if (!shown.length) body = `<div class="empty">${view === 'reply' ? '没有在等你回复的，干得漂亮。' : view === 'followup' ? '3 天内没有要发的跟进。' : '这里暂时没有要处理的。'}</div>`;
  // a time order is followed strictly; the groups (and 有希望！'s "your turn first") only shape the default order
  else if (view === 'reply') body = dir === 'fit' ? groupedReplies(shown) : shown.map(replyRow).join('');
  else if (view === 'quote') body = shown.map(quoteRow).join('');
  else if (view === 'followup') body = followupRows(shown);
  else if (view === 'bounce') body = shown.map(bounceRow).join('');
  else if (view === 'hopeful') body = (dir === 'fit' ? [...shown.filter(r => r.waiting), ...shown.filter(r => !r.waiting)] : shown).map(hopefulRow).join('');
  else body = shown.map(dmRow).join('');
  const auto = autoOn();
  const fuHead = view === 'followup' ? `<button class="switch ${auto ? 'on' : ''}" id="fu-auto" title="${auto ? '点一下关掉' : '点一下打开'}">自动跟进 <b>${auto ? '开' : '关'}</b></button>
    <button class="btn primary" id="fu-now" ${shown.length ? '' : 'disabled'}>现在全部发 ${shown.length} 封</button>` : '';
  const sub = view === 'followup' ? (auto ? FU_SUB_AUTO : FU_SUB_MANUAL) : QUEUE_SUB[view];
  box.innerHTML = `<div class="panel-head"><h2>${title}</h2><span class="sub grow">${esc(sub)}</span>${sortSelect(view)}${marketChips(rows.filter(r => !isHidden(r.handle)))}${fuHead}</div>
    <div class="rows">${body}</div>`;
  $$('.fchip[data-m]', box).forEach(b => b.addEventListener('click', () => { S.chip = b.dataset.m; renderQueue(); }));
  $$('select.sort', box).forEach(sel => sel.addEventListener('change', () => { setSort(view, sel.value); renderQueue(); }));
  $$('.row[data-h]', box).forEach(el => el.addEventListener('click', e => {
    if (e.target.closest('button, a, input, textarea, label')) return;
    openDrawer(el.dataset.h);
  }));
  $$('.row-close', box).forEach(b => b.addEventListener('click', () => closeCreator(b.dataset.h)));
  $$('.q-go', box).forEach(b => b.addEventListener('click', () => {
    const input = $(`.q-price[data-h="${CSS.escape(b.dataset.h)}"]`, box);
    const v = input ? Number(input.value) : 0;
    openDrawer(b.dataset.h, v > 0 ? { amount: v } : {});
  }));
  if (view === 'followup') bindFollowups(box, shown);
  if (view === 'dm') bindDm(box, shown);
  if (view === 'bounce') bindBounce(box, shown);
}
function quoteChip(q) {
  if (!q) return '';
  const tone = q.x == null ? 'amber' : q.x <= 1.5 ? 'green' : q.x <= 3 ? 'amber' : 'red';
  const raw = `${q.item || ''} ${q.currency} ${Math.round(q.amount).toLocaleString()}${q.tax && q.tax !== '未说明' ? '（' + q.tax + '）' : ''}`;
  return `<span class="chip ${tone}" title="${esc(raw)}">≈$${q.usd.toLocaleString()}${q.cpm != null ? ` · CPM $${q.cpm} · 参考价 ${q.x}×` : ''}</span>`;
}
function fitChip(r) {
  if (r.fit !== '不是' && r.fit !== '可能') return '';
  return `<span class="chip ${r.fit === '不是' ? 'red' : 'amber'}" title="${esc(r.fit_note)}">${r.fit === '不是' ? `观众不是${AUD}` : `观众未必是${AUD}`}</span>`;
}
const closeBtn = r => `<button class="btn small ghost row-close" data-h="${esc(r.handle)}" title="这个人不合作了，从看板移除">不合作</button>`;
function replyRow(r) {
  const wait = r.waiting_since ? ago(r.waiting_since) : '';
  const old = r.waiting_since && Date.now() - r.waiting_since > 86400000;
  return `<div class="row" data-h="${esc(r.handle)}">${avatar(r)}
    ${whoCell(r)}
    <div class="what">${r.category ? `<b>${esc(r.category)}</b> · ` : ''}${esc(r.summary || '对方回信了，AI 还没读，点开看原文')}</div>
    <div class="right">${r.came_back ? chip(`标了${r.status}后又来信`, 'red') : ''}${r.offtable ? chip('不在博主表里', 'amber') : ''}${quoteChip(r.quote_main)}${fitChip(r)}${chip(r.status, STAT[r.status])}
      ${r.has_draft ? chip('信已备好', 'accent') : ''}${chip(`等了 ${wait}`, old ? 'red' : '')}${closeBtn(r)}</div></div>`;
}
function hopefulRow(r) {
  const quiet = r.answered_at ? Date.now() - r.answered_at : 0;
  const turn = r.waiting ? chip(`轮到你回 · ${ago(r.waiting_since)}`, 'red') : chip(`等对方 · ${ago(r.answered_at)}`, quiet > 3 * 86400000 ? 'amber' : '');
  return `<div class="row" data-h="${esc(r.handle)}">${avatar(r)}
    ${whoCell(r)}
    <div class="what">${r.category ? `<b>${esc(r.category)}</b> · ` : ''}${esc(r.summary || '')}${r.answer_snippet ? `<div class="m">你 ${day(r.answered_at)}：${esc(r.answer_snippet)}</div>` : ''}</div>
    <div class="right">${turn}${chip(`来回 ${r.n_in + r.n_answers} 封`)}${quoteChip(r.quote_main)}${r.price ? chip(`心理价 $${Number(r.price).toLocaleString()}`, 'green') : ''}${fitChip(r)}${chip(r.status, STAT[r.status])}${closeBtn(r)}</div></div>`;
}
function quoteRow(r) {
  const q = r.quote_main;
  return `<div class="row" data-h="${esc(r.handle)}">${avatar(r)}
    ${whoCell(r)}
    <div class="what">${r.quotes.map(money).join('；')}${q && q.ref ? `<div class="m">参考价 $${q.ref.toLocaleString()}（中位播放 ÷ ${q.per}）</div>` : ''}</div>
    <div class="right">${quoteChip(q)}${fitChip(r)}
      <input type="number" min="0" step="any" class="q-price" data-h="${esc(r.handle)}" placeholder="出价 ${esc((q && q.currency) || 'USD')}">
      <button class="btn small primary q-go" data-h="${esc(r.handle)}">出稿</button>${closeBtn(r)}</div></div>`;
}
const REPLY_GROUPS = [
  ['要你定价', '对方报了价：点开填个价格，信就拼好了，看一眼再发；或者点不合作', r => !!r.quote_main || r.category === '报价'],
  ['要你回复', '问预算、有兴趣、问细节：信已按模板拼好、请他先报价，看一眼再发', r => !['拒绝', '自动回复'].includes(r.category)],
  ['对方拒绝', '可以礼貌结束、点不用回，或者不合作', r => true],
];
function groupedReplies(rows) {
  // mail from the last 24 hours on top, newest first, so a reply that just came in is never buried; the rest by group
  const fresh = rows.filter(r => r.waiting_since && Date.now() - r.waiting_since <= 86400000).sort((a, b) => b.waiting_since - a.waiting_since);
  const left = rows.filter(r => !fresh.includes(r));
  const head = fresh.length ? `<div class="group-h"><b>最近 24 小时收到</b><span class="count">${fresh.length}</span><span class="sub">刚来的信放最上面，从新到旧</span></div>${fresh.map(replyRow).join('')}` : '';
  return head + REPLY_GROUPS.map(([name, sub, test]) => {
    const mine = left.filter(test);
    mine.forEach(r => left.splice(left.indexOf(r), 1));
    return mine.length ? `<div class="group-h"><b>${name}</b><span class="count">${mine.length}</span><span class="sub">${sub}</span></div>${mine.map(replyRow).join('')}` : '';
  }).join('');
}
function followupRows(rows) {
  const auto = autoOn();
  return rows.map(r => {
    const open = S.fuOpen.has(r.handle);
    const txt = S.fuEdits[r.handle] ?? r.text;
    const when = r.due_now ? (auto ? '马上自动发' : '已到期') : `${day(r.due.at)} ${auto ? '自动发' : '到期'}`;
    return `<div class="row fu" data-h="${esc(r.handle)}">
      ${avatar(r)}
      ${whoCell(r, '发给 ' + (r.to || []).join(', '))}
      <div class="what">${esc(txt.split('\n').filter(Boolean).slice(1, 3).join(' '))}</div>
      <div class="right">${chip(r.due.step === 1 ? '跟进一' : '跟进二', 'blue')}${chip(r.lang)}${chip(when, r.due_now ? 'amber' : '')}
        <button class="btn small ghost fu-toggle" data-h="${esc(r.handle)}">${open ? '收起' : '看内容'}</button>
        <button class="btn small fu-one-row" data-h="${esc(r.handle)}">现在发</button>${closeBtn(r)}</div>
      ${open ? `<div class="fu-text"><textarea data-h="${esc(r.handle)}" class="fu-edit">${esc(txt)}</textarea>
        <div class="note">${auto ? '自动发用的是模板原文；这里改过的内容只在点「现在发」时用上。' : '这里改过的内容在点「现在发」时用上。'}${r.easykol ? ' 这封首封是 EasyKOL 发的；EasyKOL 的自动跟进 10/12 已全部取消，由看板跟进。' : ''}</div></div>` : ''}
    </div>`;
  }).join('');
}
function bindFollowups(box, rows) {
  $$('.fu-toggle', box).forEach(b => b.addEventListener('click', () => {
    S.fuOpen.has(b.dataset.h) ? S.fuOpen.delete(b.dataset.h) : S.fuOpen.add(b.dataset.h); renderQueue();
  }));
  $$('.fu-edit', box).forEach(t => t.addEventListener('input', () => { S.fuEdits[t.dataset.h] = t.value; }));
  const now = $('#fu-now', box), auto = $('#fu-auto', box);
  if (now) now.addEventListener('click', () => sendFollowups(rows));
  if (auto) auto.addEventListener('click', toggleAuto);
  $$('.fu-one-row', box).forEach(b => b.addEventListener('click', () => sendFollowups(rows.filter(r => r.handle === b.dataset.h))));
}
async function toggleAuto() {
  const on = !autoOn();
  const c = (S.ov && S.ov.counts) || {};
  const ok = await confirmBox(on
    ? { title: '打开自动跟进', ok: '打开',
        kv: [['发什么', '跟进一（首封后第 3 天）和跟进二（再隔 7 天），用各语言模板，在原邮件线程里发'],
          ['什么时候', `现在已到期的 ${c.followup_due || 0} 封一分钟内开始发，之后每封到期就发，每 40 秒一封`],
          ['检查', '每封发之前再查一次，回了信、退信或点了不合作的自动跳过']] }
    : { title: '关掉自动跟进', ok: '关掉',
        kv: [['之后', '到期的跟进不再自己发出，留在「该跟进」里等你点'], ['排队中的', `还没发出的 ${c.followup_queued || 0} 封自动跟进一起取消`]] });
  if (!ok) return;
  try { const d = await api('/api/auto_followup', { on }); toast(on ? '自动跟进已打开' : `自动跟进已关掉${d.cancelled ? `，取消了 ${d.cancelled} 封` : ''}`); refreshInPlace(null); }
  catch (e) { toast(e.message, true); }
}
// by hand, now: every row given, due or not; each is still re-checked right before it goes out
async function sendFollowups(picked) {
  if (!picked.length) return;
  const langs = {}, samples = {};
  picked.forEach(r => { langs[r.lang] = (langs[r.lang] || 0) + 1; if (!samples[r.lang]) samples[r.lang] = r; });
  const preview = Object.entries(samples).map(([l, r]) => `【${l}示例 · ${r.handle} · 跟进${r.due.step === 1 ? '一' : '二'}】\n${S.fuEdits[r.handle] ?? r.text}`).join('\n\n');
  const early = picked.filter(r => !r.due_now);
  const mins = Math.max(1, Math.round(picked.length * 40 / 60));
  const kv = [['语言', Object.entries(langs).map(([l, n]) => `${l} ${n}`).join('、')],
    ['方式', `在各自原邮件线程里回复，每 40 秒一封${picked.length > 1 ? `，约 ${mins} 分钟发完` : ''}`],
    ['检查', '每封发出前再查一次，期间回信、退信或点了不合作的自动跳过']];
  if (early.length) kv.splice(1, 0, ['提前', `${early.length} 封还没到日子，会比定好的时间早发`]);
  const ok = await confirmBox({ title: picked.length === 1 ? `现在跟进 ${picked[0].handle}` : `现在发 ${picked.length} 封跟进`, ok: `发送 ${picked.length} 封`, kv, preview });
  if (!ok) return;
  try {
    const d = await api('/api/followups', { items: picked.map(r => ({ h: r.handle, step: r.due.step, body: S.fuEdits[r.handle] ?? r.text, early: !r.due_now })) });
    toast(`已排队 ${d.queued} 封，开始发送${d.skipped ? `（${d.skipped} 封已经在排队）` : ''}`);
    picked.forEach(r => { delete S.fuEdits[r.handle]; S.fuOpen.delete(r.handle); });
    refreshInPlace(picked.map(r => r.handle));
  } catch (e) { toast(e.message, true); }
}
function bounceRow(r) {
  const b = r.bounced || {};
  return `<div class="row" data-h="${esc(r.handle)}">${avatar(r)}
    ${whoCell(r)}
    <div class="what"><b>${esc(b.reason)}</b> · ${esc(b.addr)} · ${day(b.at)}</div>
    <div class="right"><button class="btn small b-resend" data-h="${esc(r.handle)}" data-addr="${esc((b.addr || '').replace(/^[^A-Za-z0-9]+/, ''))}">改地址重发</button>
      <button class="btn small ghost b-done" data-h="${esc(r.handle)}">处理完了</button>${closeBtn(r)}</div></div>`;
}
function bindBounce(box) {
  $$('.b-done', box).forEach(b => b.addEventListener('click', async () => {
    try { await api('/api/bounce_done', { h: b.dataset.h }); toast('已移出退信列表'); refreshInPlace(b.dataset.h); } catch (e) { toast(e.message, true); }
  }));
  $$('.b-resend', box).forEach(b => b.addEventListener('click', async () => {
    const email = await promptBox({ title: `重发给 ${b.dataset.h}`, label: '新的邮箱地址（会把原来那封首封原样重发）', value: b.dataset.addr });
    if (!email) return;
    const ok = await confirmBox({ title: '确认重发首封', ok: '重发', kv: [['收件人', email], ['内容', '和原来那封首封一样']] });
    if (!ok) return;
    try { await api('/api/resend', { h: b.dataset.h, email }); toast('已重发'); refreshInPlace(b.dataset.h); } catch (e) { toast(e.message, true); }
  }));
}
function dmRow(r) {
  const link = safeUrl(r.profile);
  return `<div class="row" data-h="${esc(r.handle)}">${avatar(r)}
    ${whoCell(r)}
    <div class="what">${esc(r.lang)}私信模板 · ${esc((r.dm || '').split('\n')[0])}</div>
    <div class="right">${link ? `<a class="btn small" href="${esc(link)}" target="_blank" rel="noopener">打开主页</a>` : ''}
      <button class="btn small dm-copy" data-h="${esc(r.handle)}">复制私信</button><button class="btn small primary dm-done" data-h="${esc(r.handle)}">已发私信</button>${closeBtn(r)}</div></div>`;
}
function bindDm(box, rows) {
  $$('.dm-copy', box).forEach(b => b.addEventListener('click', async () => {
    const r = rows.find(x => x.handle === b.dataset.h);
    try { await navigator.clipboard.writeText(r.dm || ''); toast('私信已复制'); } catch (e) { toast('复制失败，请在详情里手动复制', true); }
  }));
  $$('.dm-done', box).forEach(b => b.addEventListener('click', async () => {
    try { await api('/api/dm', { h: b.dataset.h }); toast('已记为「已发私信」，飞书表同步更新'); refreshInPlace(b.dataset.h); } catch (e) { toast(e.message, true); }
  }));
}

async function closeCreator(h) {
  const ok = await confirmBox({ title: `不合作了：${h}`, ok: '移除',
    kv: [['之后', '从看板所有清单里移除，不再跟进，不发任何邮件'], ['飞书', '这一行保留，合作状态标「不再联系」，以后不会被再联系'],
      ['找回', '在「全部博主」把合作状态选「不再联系」能找到，点开可以恢复']] });
  if (!ok) return false;
  try { await api('/api/close', { h }); toast(`${h} 已移除`); if (S.drawer === h) closeDrawer(); refreshInPlace(h); return true; }
  catch (e) { toast(e.message, true); return false; }
}

// ------------------------------------------------------------------ all creators
async function renderAll(main) {
  if (!S.all) {
    main.innerHTML = '<div class="empty">正在加载…</div>';
    try { S.all = await getList('all'); } catch (e) { main.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  }
  const f = S.filters, q = S.search;
  const CLOSED = ['已放弃', '不再联系'];
  const hidden = f.status ? 0 : S.all.filter(r => CLOSED.includes(r.status)).length;
  const rows = S.all.filter(r => (f.status || !CLOSED.includes(r.status)) && (!f.market || r.market === f.market) && (!f.progress || r.progress === f.progress)
    && (!f.status || (f.status === '（未设置）' ? !r.status : r.status === f.status))
    && (!q || r.handle.toLowerCase().includes(q) || (r.emails || []).some(e => e.includes(q))
    || (r.portrait || '').toLowerCase().includes(q) || (r.tags || []).some(t => t.toLowerCase().includes(q))))
    .sort((a, b) => {
      if ((S.allSort || 'fit') === 'fit') return fitCompare(a, b) || (b.last_activity || 0) - (a.last_activity || 0);
      const [key, dir] = S.allSort.split('-');
      const ta = key === 'first' ? (a.first_at || 0) : (a.last_activity || 0), tb = key === 'first' ? (b.first_at || 0) : (b.last_activity || 0);
      if (!ta !== !tb) return ta ? -1 : 1;  // people with no date always go last
      return dir === 'new' ? tb - ta : ta - tb;
    });
  const opts = (key, values, label) => `<select data-f="${key}"><option value="">${label}</option>${values.map(v => `<option ${f[key] === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
  const uniq = k => [...new Set(S.all.map(r => r[k]).filter(Boolean))];
  main.innerHTML = `<div class="page-head"><div><h1>全部博主</h1><div class="sub">共 ${S.all.length} 人，筛出 ${rows.length} 人${hidden ? `；已关闭的 ${hidden} 人没显示，合作状态选「不再联系」或「已放弃」能看到` : ''}</div></div>
    <div class="filters">${opts('market', uniq('market'), '全部市场')}${opts('progress', ['未联系', '已发首封', '已跟进一次', '已跟进两次', '对方已回复', '退信', '已发私信'], '全部进度')}
    ${opts('status', ['（未设置）', '谈价中', '待档期', '已签约', '已发布', '已付款', '已放弃', '不再联系'], '全部合作状态')}
    <select id="all-sort">${[['fit', '画像最合适的在前'], ['act-new', '最近动态：从新到旧'], ['act-old', '最近动态：从旧到新'], ['first-new', '首封日期：从新到旧'], ['first-old', '首封日期：从旧到新']]
      .map(([v, l]) => `<option value="${v}" ${(S.allSort || 'fit') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div></div>
    <div class="panel"><table class="grid"><thead><tr><th>博主</th><th>市场</th><th class="num">粉丝</th><th class="num">中位播放</th><th>进度</th><th>合作状态</th><th>下一步</th><th>最后动态</th></tr></thead>
    <tbody>${rows.slice(0, 400).map(r => `<tr class="click" data-h="${esc(r.handle)}"><td style="max-width:340px"><b><span class="flag">${r.flag || ''}</span>${esc(r.handle)}</b>${r.offtable ? ' ' + chip('不在博主表里', 'amber') : ''}
      <div class="pt">${esc(r.portrait || '')}</div><div class="who"><div class="m">${esc(r.platform)}</div></div></td>
      <td>${esc(r.market)}</td><td class="num">${wan(r.followers)}</td><td class="num">${wan(r.median)}</td><td>${chip(r.progress, PROG[r.progress])}</td>
      <td>${chip(r.status, STAT[r.status])}</td><td>${esc(r.next_step)}</td><td style="max-width:280px">${esc(r.last_event)}</td></tr>`).join('')}</tbody></table>
    ${rows.length > 400 ? '<div class="empty">只显示前 400 个，用上面的筛选或搜索缩小范围</div>' : ''}</div>`;
  $$('select[data-f]', main).forEach(s => s.addEventListener('change', () => { S.filters[s.dataset.f] = s.value; renderAll(main); }));
  $('#all-sort', main).addEventListener('change', e => { S.allSort = e.target.value; try { localStorage.setItem('board-all-sort-v2', S.allSort); } catch (x) { /* ok */ } renderAll(main); });
  $$('tr[data-h]', main).forEach(tr => tr.addEventListener('click', () => openDrawer(tr.dataset.h)));
}

// ------------------------------------------------------------------ progress
function pct(a, b) { return b ? `${Math.round(a / b * 100)}%` : '—'; }
function renderProgress(main) {
  const o = S.ov;
  if (!o || !o.funnel) { main.innerHTML = '<div class="empty">正在加载…</div>'; return; }
  const all = o.funnel[0];
  const steps = [['名单', 'total'], ['已联系', 'contacted'], ['已回复', 'replied'], ['已报价', 'quoted'], ['谈价及以后', 'talking'], ['已签约', 'signed'], ['已发布', 'posted']];
  const bar = (f, k, prev) => `<div class="fstep"><span>${steps.find(s => s[1] === k)[0]}</span><div class="fbar"><i style="width:${f.total ? Math.max(0.5, f[k] / f.total * 100) : 0}%"></i></div>
    <span class="n"><b>${f[k]}</b>${prev ? ` · ${pct(f[k], f[prev])}` : ''}</span></div>`;
  const budget = o.budget;
  main.innerHTML = `<div class="page-head"><div><h1>进度</h1><div class="sub">每一步后面的百分比是从上一步走到这一步的比例。你定的规划假设：联系 → 回复 10%，回复 → 签约 10%。</div></div></div>
    <div class="grid2"><div class="panel"><div class="panel-head"><h2>全部市场</h2></div><div class="funnel">
      ${steps.map(([, k], i) => bar(all, k, i ? steps[i - 1][1] : null)).join('')}</div></div>
      <div class="panel"><div class="panel-head"><h2>10 月预算</h2></div><div class="mini" style="padding-top:14px">
        <div style="font-size:26px;font-weight:650">$${Math.round(budget.signed).toLocaleString()} <span style="font-size:14px;color:var(--faint);font-weight:400">/ $${budget.october.toLocaleString()}</span></div>
        <div class="meter"><i style="width:${Math.min(100, budget.signed / budget.october * 100)}%"></i></div>
        <div class="note">已签约、已发布、已付款的博主的心理价位（成交价）合计。签约后在博主详情里把心理价位改成成交价。</div>
        <table style="margin-top:14px"><tr><th>转化</th><th>实际</th><th>假设</th></tr>
          <tr><td>联系 → 回复</td><td>${pct(all.replied, all.contacted)}</td><td>10%</td></tr>
          <tr><td>回复 → 签约</td><td>${pct(all.signed, all.replied)}</td><td>10%</td></tr></table></div></div></div>
    <div class="panel" style="margin-top:16px"><div class="panel-head"><h2>分市场</h2></div><div class="mini"><table>
      <tr><th>市场</th>${steps.map(s => `<th>${s[0]}</th>`).join('')}<th>回复率</th></tr>
      ${o.funnel.slice(1).map(f => `<tr><td>${esc(f.market)}</td>${steps.map(s => `<td>${f[s[1]]}</td>`).join('')}<td>${pct(f.replied, f.contacted)}</td></tr>`).join('')}
    </table></div></div>`;
}

// ------------------------------------------------------------------ health
function renderHealth(main) {
  const o = S.ov;
  if (!o || !o.health) { main.innerHTML = '<div class="empty">正在加载…</div>'; return; }
  const h = o.health;
  const max = Math.max(1, ...h.days.map(d => d.first + d.reply_in + d.bounce));
  const rate = b => `${b.sent} 封 · 回复 ${b.replied}（${pct(b.replied, b.sent)}）· 退信 ${b.bounced}`;
  main.innerHTML = `<div class="page-head"><div><h1>发信情况</h1><div class="sub">最近 14 天，按首封发出日、回信收到日统计</div></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>首封</span><span><i style="background:#1fae76"></i>收到回信</span><span><i style="background:#e5484d"></i>退信</span></div></div>
    <div class="panel"><div class="bars">${h.days.map(d => `<div class="bar" title="${d.day} 首封 ${d.first} · 回信 ${d.reply_in} · 退信 ${d.bounce}">
      <span class="a" style="height:${d.first / max * 100}%"></span><span class="b" style="height:${d.reply_in / max * 100}%"></span><span class="c" style="height:${d.bounce / max * 100}%"></span></div>`).join('')}</div>
      <div class="bar-labels">${h.days.map(d => `<span>${d.day.slice(3)}</span>`).join('')}</div></div>
    <div class="grid2" style="margin-top:16px">
      <div class="panel"><div class="panel-head"><h2>按语言</h2></div><div class="mini"><table><tr><th>语言</th><th>首封</th><th>回复</th><th>回复率</th><th>退信</th></tr>
        ${Object.entries(h.by_lang).map(([k, b]) => `<tr><td>${esc(k)}</td><td>${b.sent}</td><td>${b.replied}</td><td>${pct(b.replied, b.sent)}</td><td>${b.bounced}</td></tr>`).join('')}</table></div></div>
      <div class="panel"><div class="panel-head"><h2>按发信方式</h2></div><div class="mini"><table><tr><th>方式</th><th>首封</th><th>回复</th><th>回复率</th><th>退信</th></tr>
        ${Object.entries(h.by_route).map(([k, b]) => `<tr><td>${esc(k)}</td><td>${b.sent}</td><td>${b.replied}</td><td>${pct(b.replied, b.sent)}</td><td>${b.bounced}</td></tr>`).join('')}</table>
        <div class="note">Lark 直接发的这批多数还不满 3 天，回复率要过几天才有意义。</div></div></div></div>
    <div class="panel" style="margin-top:16px"><div class="panel-head"><h2>没对上人的相关邮件</h2><span class="sub">主题里提到 ${esc(PRODUCT || '我们')} 或合作、但对不上表里任何人的来信，偶尔看一眼，防止漏掉换了邮箱的博主</span></div>
      <div class="mini"><table><tr><th>时间</th><th>发件人</th><th>主题</th></tr>${(h.unmatched || []).map(u => `<tr><td>${fmt(u.at)}</td><td>${esc(u.from)}</td><td style="text-align:left">${esc(u.subject)}</td></tr>`).join('') || '<tr><td colspan="3">没有</td></tr>'}</table></div></div>`;
}

// ------------------------------------------------------------------ drawer
async function openDrawer(h, opts = {}) {
  S.drawer = h;
  Object.assign(S, { cmp: null, cmpFor: null, cmpCustom: null, cmpDirty: false, cmpUndo: null, cmpOffer: '', cmpLast: '',
    cmpPrefill: opts.amount || null });
  const dr = $('#drawer');
  dr.innerHTML = '<div class="empty">正在加载…</div>';
  dr.classList.add('on'); $('#drawer-mask').classList.add('on');
  try { renderDrawer(await api('/api/creator?h=' + encodeURIComponent(h))); }
  catch (e) { dr.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function closeDrawer() { S.drawer = null; $('#drawer').classList.remove('on'); $('#drawer-mask').classList.remove('on'); }
$('#drawer-mask').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#modal').classList.contains('on')) closeDrawer(); });

function renderDrawer(d) {
  const r = d.row, b = d.board || {};
  const link = safeUrl(r.profile);
  const lastIn = [...d.messages].reverse().find(m => m.kind === 'in');
  const ai = d.ai_ready !== false && S.ov && S.ov.ai && S.ov.ai.ok;
  if (d.composer && S.cmpFor !== r.handle) {
    S.cmp = JSON.parse(JSON.stringify(d.composer.defaults)); S.cmpFor = r.handle;
    if (S.cmpPrefill) { S.cmp.amount = S.cmpPrefill; if (S.cmp.scenario === 'askrate') S.cmp.scenario = 'offer'; S.cmpPrefill = null; }
  }
  const statuses = ['', '谈价中', '待档期', '已签约', '已发布', '已付款', '已放弃', '不再联系'];
  const dr = $('#drawer');
  dr.innerHTML = `
    <div class="d-head">${avatar(r)}<div class="d-title">
      <div class="h"><span class="flag">${r.flag || ''}</span>${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(r.handle)}</a>` : esc(r.handle)}
        ${d.display_name && d.display_name !== r.handle ? `<span class="dname">${esc(d.display_name)}</span>` : ''}</div>
      <div class="m">${esc(r.country)} · ${esc(r.market)}${r.emails && r.emails.length ? ' · ' + esc(r.emails.join(', ')) : ''}</div>
      <div class="d-chips">${chip(r.progress, PROG[r.progress])}${chip(r.status, STAT[r.status])}${chip(r.next_step)}${r.came_back ? chip(`标了${r.status}后又来信`, 'red') : ''}${r.offtable ? chip('不在博主表里：只记在看板上，不写飞书', 'amber') : ''}</div></div>
      <div style="display:flex;gap:6px;align-items:center">${['不再联系', '已放弃'].includes(r.status) ? '<button class="btn small" id="d-reopen">恢复合作</button>'
        : '<button class="btn small danger" id="d-dropit">不合作，移除</button>'}<button class="btn ghost" id="d-close" aria-label="收起">✕</button></div></div>
    <div class="d-grid"><div class="d-left">
    <div class="d-profile">
      <div class="portrait">${esc(r.portrait || '画像生成中，几分钟后刷新')}</div>
      ${r.fit ? `<div class="fit fit-${r.fit === '是' ? 'ok' : r.fit === '可能' ? 'mid' : 'bad'}">${r.fit === '是' ? `观众是${AUD}` : r.fit === '可能' ? `观众未必是${AUD}` : `观众不是${AUD}`}${r.fit_note ? ' · ' + esc(r.fit_note) : ''}</div>` : ''}
      ${r.tags && r.tags.length ? `<div class="tags">${tagChips(r.tags, 6)}</div>` : ''}
      <div class="stats">
        <div><b>${wan(r.followers)}</b><span>粉丝</span></div><div><b>${wan(r.median)}</b><span>最近 10 条中位</span></div>
        <div><b>${r.ratio ? r.ratio.toFixed(2) : '—'}</b><span>播粉比</span></div><div><b>${esc(r.platform)}</b><span>平台</span></div></div>
      ${d.bio ? `<div class="bio">${esc(d.bio)}</div>` : ''}
    </div>
    <div class="d-sec"><h3>定价参考</h3><div class="brief">${d.brief.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v ${k === '参考' ? 'ref' : ''}">${esc(v)}</span>`).join('')}</div></div>
    <div class="d-sec"><h3>你的判断<span class="note" id="save-note"></span></h3><div class="form">
      <label>心理价位（美元）<span class="price-in"><input type="number" min="0" step="10" id="f-price" value="${b['心理价位'] ?? ''}" placeholder="例如 300"></span>
        ${quickPrices(r.quote_main)}</label>
      <label>合作状态<select id="f-status">${statuses.map(s => `<option value="${s}" ${(b['合作状态'] || '') === s ? 'selected' : ''}>${s || '（未设置）'}</option>`).join('')}</select></label>
      <label class="wide">对方报价<input type="text" id="f-quote" value="${esc(textOf(b['对方报价']))}" placeholder="AI 会从回信里读出来，也可以自己改"></label>
      <label class="wide">备注<textarea id="f-notes" rows="2" placeholder="谈到哪了、对方的特殊要求">${esc(textOf(b['备注']))}</textarea></label></div></div>
    </div><div class="d-right">
    ${lastIn && d.composer ? `<div class="d-sec"><h3>回复 ${d.reply_to ? `<span class="note">发给 ${esc(d.reply_to)}</span>` : ''}</h3>
      ${lastIn.read ? `<div class="read"><b>${esc(lastIn.read.category)}</b> · ${esc(lastIn.read.summary)}</div>` : ''}
      ${attList(lastIn, false)}
      <div id="cmp">${composerHtml(d.composer, ai)}</div>
      <textarea id="reply" rows="14" style="margin-top:10px" placeholder="正在按模板拼回信…"></textarea>
      <div class="cmp-under"><span class="note grow" id="c-note"></span><a id="c-undo" hidden>撤销重排</a></div>
      <div class="zh" id="c-zh" hidden><div class="zh-h">中文意思<span>按模板拼的版本；你在上面手改的地方，这里不跟着变</span></div><div class="zh-b"></div></div>
      <div class="actions"><button class="btn" id="c-regen" ${ai ? '' : 'disabled'} title="让 AI 重新写开头那一两句">重写开头</button>
        <button class="btn" id="draft">存为 Lark 草稿</button><span class="grow"></span>
        ${r.progress === '对方已回复' ? '<button class="btn ghost" id="dismiss">不用回</button>' : ''}
        <button class="btn primary" id="send">发送</button></div></div>` : ''}
    ${d.can_nudge ? `<div class="d-sec"><h3>写一封 <span class="note">对方还没回，在原邮件线程里再写一封，发给 ${esc((d.to_addrs || []).join(', '))}</span></h3>
      ${intentBox(ai, 'nudge')}
      <textarea id="nudge" rows="7" style="margin-top:10px" placeholder="写好大意点「按大意起草」，或者直接在这里写">${esc((d.suggestions.nudge || {}).text || '')}</textarea>
      ${(d.suggestions.nudge || {}).zh ? `<div class="zh"><div class="zh-h">中文意思</div><div class="zh-b">${esc(d.suggestions.nudge.zh)}</div></div>` : ''}
      <div class="actions"><span class="grow"></span><button class="btn primary" id="nudge-send">发送</button></div></div>` : ''}
    ${d.followup ? `<div class="d-sec"><h3>跟进${d.followup.step === 1 ? '一' : '二'} <span class="note">${day(d.followup.at)} 到期</span></h3>
      <textarea id="fu" rows="8">${esc(d.followup.text)}</textarea>
      <div class="actions"><span class="grow note">在原邮件线程里发出，发送前会再查一次有没有新回信。</span><button class="btn primary" id="fu-one" ${d.followup.at <= Date.now() ? '' : 'disabled'}>发送这封跟进</button></div></div>` : ''}
    ${d.dm ? `<div class="d-sec"><h3>私信模板</h3><textarea rows="7" readonly id="dm">${esc(d.dm)}</textarea>
      <div class="actions"><button class="btn" id="dm-copy">复制</button>${r.progress === '未联系' ? '<button class="btn primary" id="dm-done">已发私信</button>' : ''}</div></div>` : ''}
    <div class="d-sec"><h3>往来记录 <span class="note">${d.messages.length} 封</span></h3><div class="timeline">
      ${d.messages.length ? [...d.messages].reverse().map(msgCard).join('') : '<div class="empty">还没有邮件往来</div>'}</div></div>
    </div></div>`;
  $('#d-close').addEventListener('click', closeDrawer);
  const drop = $('#d-dropit');
  if (drop) drop.addEventListener('click', () => closeCreator(d.row.handle));
  const reopen = $('#d-reopen');
  if (reopen) reopen.addEventListener('click', async () => {
    try { await api('/api/reopen', { h: d.row.handle }); toast('已恢复，回到正常清单'); delete S.hidden[d.row.handle]; refreshInPlace(null); openDrawer(d.row.handle); } catch (e) { toast(e.message, true); }
  });
  bindDrawer(d);
}
// ------------------------------------------------------------------ reply composer
// The email is built from the reply template; the user picks what this email does, the layout, the price and the add-ons.
const SCN = [['offer', '出价'], ['accept', '接受报价'], ['askrate', '请他先报价'], ['decline', '婉拒'], ['thanks', '回个谢']];
const LAY = [['prose', '段落'], ['bullets', '要点'], ['short', '简短']];
const PF = [['tt', 'TikTok'], ['ig', 'Instagram'], ['both', 'TikTok + IG'], ['three', '三平台'], ['yt', 'YouTube Shorts'], ['any', '不指定平台']];
const CUR = ['USD', 'GBP', 'EUR', 'CAD', 'AUD', 'NZD', 'SGD', 'JPY', 'KRW', 'TWD'];
const TAXED = ['JPY', 'KRW', 'TWD'];
const ADD_DEFAULT = [['promo', '投流加推'], ['pro', '赠送会员'], ['revshare', '折扣码 50% 分成'], ['excl', '7 天排他'], ['label', '标注广告']];
const SYM = { USD: '$', GBP: '£', EUR: '€', CAD: 'CA$', AUD: 'A$', NZD: 'NZ$', SGD: 'S$', JPY: '¥', KRW: '₩', TWD: 'NT$' };
const cash = (a, c) => `${SYM[c] || c + ' '}${Number(a).toLocaleString()}`;
function composerHtml(cm, ai) {
  const c = S.cmp, priced = ['offer', 'accept'].includes(c.scenario), terms = !['decline', 'thanks'].includes(c.scenario);
  const q = cm.quote;
  const seg = (id, opts, v) => `<div class="seg" id="${id}">${opts.map(([k, l]) => `<button data-v="${k}" class="${v === k ? 'on' : ''}">${l}</button>`).join('')}</div>`;
  const chips = (c.chips || []).map(([l, v]) => `<button class="fchip c-qp ${Number(c.amount) === Number(v) ? 'on' : ''}" data-v="${v}">${l} ${cash(v, c.currency)}</button>`).join('');
  return `<div class="cmp">
    <div class="cmp-row"><span class="cmp-l">这封做什么</span>${seg('c-scn', SCN, c.scenario)}</div>
    ${c.scenario === 'thanks' ? '' : `<div class="cmp-row"><span class="cmp-l">样子</span>${seg('c-lay', LAY, c.layout)}
      <span class="note">三种随时切，看哪个顺眼</span></div>`}
    ${priced ? `<div class="cmp-row"><span class="cmp-l">价格</span>
      <select id="c-n">${[1, 2, 3, 4, 5, 6].map(n => `<option value="${n}" ${Number(c.n) === n ? 'selected' : ''}>${n} 条</option>`).join('')}</select>
      <select id="c-pf">${PF.map(([k, l]) => `<option value="${k}" ${c.platform === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <input type="number" id="c-amt" min="0" step="any" value="${c.amount ?? ''}" placeholder="总价">
      <select id="c-cur">${CUR.concat(CUR.includes(c.currency) ? [] : [c.currency]).map(x => `<option ${c.currency === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
      ${TAXED.includes(c.currency) ? `<button class="fchip ${c.tax ? 'on' : ''}" id="c-tax">含税</button>` : ''}</div>
      <div class="cmp-row"><span class="cmp-l"></span><span class="note">${q ? `对方报价 ${esc(cash(q.amount, q.currency))}${q.item ? '：' + esc(q.item) : ''}${q.tax && q.tax !== '未说明' ? '（' + esc(q.tax) + '）' : ''}` : '对方还没报价'}</span>${chips}</div>` : ''}
    ${terms ? `<div class="cmp-row"><span class="cmp-l">附加</span><div class="chips-filter" id="c-add">${(cm.addons || ADD_DEFAULT).map(([k, l]) =>
      `<button class="fchip ${c.addons.includes(k) ? 'on' : ''}" data-v="${k}">${l}</button>`).join('')}</div></div>` : ''}
    <div class="cmp-row cmp-gist"><span class="cmp-l">写大意</span><textarea id="c-gist" rows="2" placeholder="中文就行，要改什么、加什么都写这里。例如：改成两条一共 $500；加一句 11 月初也可以；去掉 Pro 那句；语气再热情一点"></textarea>
      <div class="cmp-btns"><button class="btn small primary" id="c-revise" ${ai ? '' : 'disabled'} title="照你写的改下面这封信，其他句子不动">按大意改这封</button>
      <button class="btn small" id="c-line" ${ai ? '' : 'disabled'} title="把这句写成对方的语言，加在结尾前">只加一句</button>
      <button class="btn small ghost" id="c-whole" ${ai ? '' : 'disabled'} title="不用模板，整封按你写的意思让 AI 写">整封重写</button></div></div>
    ${S.cmpCustom ? `<div class="cmp-row"><span class="cmp-l"></span><span class="note">已加进信里：${esc(S.cmpCustom.text)}</span><a id="c-line-x">去掉</a></div>` : ''}
  </div>`;
}
function setZh(zh) {
  const box = $('#c-zh');
  if (!box) return;
  box.hidden = !zh; $('.zh-b', box).textContent = zh || '';
}
async function composeNow(d, why) {
  const h = d.row.handle, ta = $('#reply');
  if (!ta || !S.cmp) return;
  let out;
  try { out = await api('/api/compose', { h, ctl: { ...S.cmp, chips: undefined, custom: S.cmpCustom } }); }
  catch (e) { toast(e.message, true); return; }
  if (S.drawer !== h || !$('#reply')) return;
  const undo = $('#c-undo');
  if (S.cmpDirty && why === 'price' && S.cmpOffer && out.offer && ta.value.includes(S.cmpOffer)) {
    ta.value = ta.value.replace(S.cmpOffer, out.offer);  // keep the user's own edits, swap only the price sentence
  } else {
    if (S.cmpDirty) { S.cmpUndo = ta.value; if (undo) undo.hidden = false; }
    ta.value = out.text; S.cmpDirty = false; S.cmpLast = out.text;
  }
  S.cmpOffer = out.offer;
  setZh(out.zh);
}
function rerenderComposer(d) {
  const box = $('#cmp');
  if (!box) return;
  box.innerHTML = composerHtml(d.composer, d.ai_ready !== false && S.ov && S.ov.ai && S.ov.ai.ok);
  bindComposerControls(d);
}
function bindComposerControls(d) {
  const h = d.row.handle, c = S.cmp;
  const again = (why, rerender) => { if (rerender) rerenderComposer(d); composeNow(d, why); };
  $$('#c-scn button').forEach(b => b.addEventListener('click', () => {
    c.scenario = b.dataset.v;
    if (c.scenario === 'accept' && d.composer.quote) {  // accepting means their own price, in their currency
      c.amount = d.composer.quote.amount; c.currency = d.composer.quote.currency; c.tax = TAXED.includes(c.currency);
    }
    again('structure', true);
  }));
  $$('#c-lay button').forEach(b => b.addEventListener('click', () => { c.layout = b.dataset.v; again('structure', true); }));
  const n = $('#c-n'), pf = $('#c-pf'), amt = $('#c-amt'), cur = $('#c-cur'), tax = $('#c-tax');
  if (n) n.addEventListener('change', () => { c.n = Number(n.value); again('price'); });
  if (pf) pf.addEventListener('change', () => { c.platform = pf.value; again('price'); });
  let timer;
  if (amt) amt.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { c.amount = amt.value === '' ? null : Number(amt.value); $$('.c-qp').forEach(x => x.classList.toggle('on', Number(x.dataset.v) === c.amount)); again('price'); }, 250);
  });
  if (cur) cur.addEventListener('change', () => { c.currency = cur.value; c.tax = TAXED.includes(cur.value); again('price', true); });
  if (tax) tax.addEventListener('click', () => { c.tax = !c.tax; tax.classList.toggle('on', c.tax); again('price'); });
  $$('.c-qp').forEach(b => b.addEventListener('click', () => { c.amount = Number(b.dataset.v); again('price', true); }));
  $$('#c-add .fchip').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.v;
    c.addons = c.addons.includes(k) ? c.addons.filter(x => x !== k) : c.addons.concat(k);
    b.classList.toggle('on', c.addons.includes(k));
    again('structure');
  }));
  const line = $('#c-line'), whole = $('#c-whole'), gist = $('#c-gist'), lineX = $('#c-line-x'), revise = $('#c-revise');
  if (revise) revise.addEventListener('click', async () => {
    const g = gist.value.trim(), ta = $('#reply');
    if (!g) { toast('先写要改什么', true); gist.focus(); return; }
    revise.disabled = true; revise.innerHTML = '<span class="spin"></span> 改信中…';
    try {
      const before = ta.value;
      const out = await api('/api/revise', { h, gist: g, current: before });
      if (S.drawer !== h) return;
      S.cmpUndo = before; $('#c-undo').hidden = false;
      ta.value = out.text; S.cmpDirty = true;
      if (S.cmpOffer && !out.text.includes(S.cmpOffer)) S.cmpOffer = '';  // the price line was rewritten: the price box stops steering it
      if (out.zh || !$('#c-zh').hidden) setZh(out.zh);
      $('#c-note').textContent = '按大意改了：' + (out.note || '');
      toast('已按大意改好，不满意点「撤销重排」退回去');
    } catch (e) { toast(e.message, true); }
    revise.disabled = false; revise.textContent = '按大意改这封';
  });
  if (line) line.addEventListener('click', async () => {
    const g = gist.value.trim();
    if (!g) { toast('先写一句', true); gist.focus(); return; }
    line.disabled = true; line.innerHTML = '<span class="spin"></span> 写这句…';  // re-render restores the button
    try {
      S.cmpCustom = await api('/api/custom', { h, gist: g, current: $('#reply').value });
      if (S.cmpDirty) {  // the email was already edited by hand: put the line in before the closing, keep those edits
        const ta = $('#reply'), paras = ta.value.split('\n\n');
        paras.splice(Math.max(1, paras.length - 2), 0, S.cmpCustom.text);
        ta.value = paras.join('\n\n');
        rerenderComposer(d);
      } else again('structure', true);
      toast('已加进信里');
    } catch (e) { toast(e.message, true); line.disabled = false; line.textContent = '只加一句'; }
  });
  if (lineX) lineX.addEventListener('click', () => { S.cmpCustom = null; again('structure', true); });
  if (whole) whole.addEventListener('click', async () => {
    const g = gist.value.trim();
    if (!g) { toast('先写这封信的大意', true); gist.focus(); return; }
    whole.disabled = true; whole.innerHTML = '<span class="spin"></span> 写整封…';
    try {
      const out = await api('/api/suggest', { h, mode: 'reply', intent: g });
      const ta = $('#reply');
      S.cmpUndo = ta.value; $('#c-undo').hidden = false;
      ta.value = out.text; S.cmpDirty = true; S.cmpOffer = '';
      setZh(out.zh); $('#c-note').textContent = out.note || '';
      toast('已按大意写好整封，模板的开关对这封不再起作用');
    } catch (e) { toast(e.message, true); }
    whole.disabled = false; whole.textContent = '整封重写';
  });
}
function bindComposer(d) {
  const h = d.row.handle, ta = $('#reply'), note = $('#c-note'), undo = $('#c-undo'), regen = $('#c-regen');
  const showNote = () => { note.textContent = d.composer.fresh ? (d.composer.note || '') : '开头正在写，先用了一句通用的，写好会自动换上…'; };
  showNote();
  ta.addEventListener('input', () => { S.cmpDirty = ta.value !== S.cmpLast; });
  undo.addEventListener('click', () => {
    if (S.cmpUndo == null) return;
    ta.value = S.cmpUndo; S.cmpUndo = null; S.cmpDirty = true; undo.hidden = true;
  });
  const refreshParts = async (manual) => {
    if (regen) { regen.disabled = true; regen.innerHTML = '<span class="spin"></span> 写开头…'; }
    try {
      const cm = await api('/api/parts', { h });
      if (S.drawer !== h) return;
      d.composer = { ...cm, defaults: d.composer.defaults };
      showNote();
      if (manual || !S.cmpDirty) composeNow(d, 'structure');  // a click rebuilds (with 撤销重排 to get edits back); the background never overwrites edits
    } catch (e) { if (manual) toast(e.message, true); }
    if (regen) { regen.disabled = false; regen.textContent = '重写开头'; }
  };
  if (regen) regen.addEventListener('click', () => refreshParts(true));
  bindComposerControls(d);
  composeNow(d, 'structure');
  if (!d.composer.fresh && regen && !regen.disabled) refreshParts(false);
}
function quickPrices(q) {
  if (!q) return '';
  const opts = [];
  if (q.ref) opts.push(['参考价', q.ref]);
  opts.push(['对方报价', q.usd]);
  opts.push(['报价七成', Math.round(q.usd * 0.7 / 10) * 10]);
  if (q.ref) opts.push(['参考价 1.5 倍', Math.round(q.ref * 1.5 / 10) * 10]);
  return `<span class="qp">${opts.map(([l, v]) => `<button type="button" class="fchip qp-btn" data-v="${v}">${l} $${v.toLocaleString()}</button>`).join('')}</span>`;
}
function intentBox(ai, mode = '') {
  return `<div class="intent"><textarea class="intent-in" data-mode="${mode}" rows="2" placeholder="写大意，中文就行。比如：我们最多 $300，可以送一年会员，问他能不能 11 月中发"></textarea>
    <button class="btn small intent-go" data-mode="${mode}" ${ai ? '' : 'disabled'}>按大意起草</button></div>`;
}
function textOf(v) {
  if (v == null) return '';
  if (Array.isArray(v)) return v.map(x => (typeof x === 'object' ? (x.text || '') : String(x))).join('');
  if (typeof v === 'object') return v.text || '';
  return String(v);
}
function attList(m, open) {
  const atts = (m.attachments || []);
  if (!atts.length) return '';
  return `<div class="atts">${atts.map(a => {
    const ok = a.status === 'read' || a.status === 'downloaded';
    const r = a.read || {};
    const link = ok ? `<a href="/att?k=${encodeURIComponent(a.key)}&t=${encodeURIComponent(TOKEN)}" target="_blank" rel="noopener">打开</a>` : '';
    const state = a.status === 'downloaded' ? '<span class="att-state">AI 读取中…</span>' : !ok ? `<span class="att-state">${a.status && a.status.startsWith('unreadable') ? '读不了这个文件' : '下载失败，稍后会重试'}</span>` : '';
    return `<div class="att"><div class="att-h"><span class="att-ic">附件</span><b>${esc(a.filename)}</b>${link}${state}</div>
      ${r.summary ? `<div class="att-b">${esc(r.summary)}</div>` : ''}
      ${open && r.quotes && r.quotes.length ? `<div class="att-b"><span class="k">报价</span>${r.quotes.map(q => `${esc(q.item)} ${esc(q.currency)} ${Math.round(q.amount).toLocaleString()}${q.tax && q.tax !== '未说明' ? '（' + esc(q.tax) + '）' : ''}`).join('；')}</div>` : ''}
      ${open && r.audience ? `<div class="att-b"><span class="k">观众</span>${esc(r.audience)}</div>` : ''}
      ${open && r.notes ? `<div class="att-b"><span class="k">条件</span>${esc(r.notes)}</div>` : ''}</div>`;
  }).join('')}</div>`;
}
function msgCard(m) {
  const who = m.kind === 'out' ? '你' : m.kind === 'bounce' ? '退信' : esc(m.from);
  const tr = m.kind === 'in' && m.read && m.read.translation && m.read.translation.trim() !== (m.body || '').trim() ? m.read.translation : '';
  return `<div class="msg ${m.kind === 'in' ? 'in' : ''}"><div class="mh"><b>${who}</b><span>${fmt(m.at)}</span>${m.spam ? chip('在垃圾箱', 'red') : ''}
    ${m.read ? chip(m.read.category, 'accent') : ''}${m.bounce ? chip(m.bounce, 'red') : ''}${tr ? chip('中文译文', 'violet') : ''}<span>${esc(m.subject)}</span></div>
    <div class="mb">${esc(tr || m.body)}</div>${tr ? `<div class="mb orig" hidden>${esc(m.body)}</div>` : ''}
    ${attList(m, true)}
    <div class="msg-links"><a class="more">展开</a>${tr ? '<a class="orig-toggle">看原文</a>' : ''}</div></div>`;
}
function bindDrawer(d) {
  const h = d.row.handle;
  $$('.msg .more').forEach(a => a.addEventListener('click', () => {
    const box = a.closest('.msg'); box.classList.toggle('open'); a.textContent = box.classList.contains('open') ? '收起' : '展开';
  }));
  $$('.msg .orig-toggle').forEach(a => a.addEventListener('click', () => {
    const box = a.closest('.msg'); const [tr, orig] = $$('.mb', box);
    const showOrig = orig.hidden; orig.hidden = !showOrig; tr.hidden = showOrig; a.textContent = showOrig ? '看中文' : '看原文';
  }));
  const save = async changes => {
    try { await api('/api/fields', { h, changes }); $('#save-note').textContent = '已保存，飞书表已同步'; S.all = null; loadOverview(); }
    catch (e) { toast(e.message, true); }
  };
  $('#f-price').addEventListener('change', e => save({ '心理价位': e.target.value }));
  $$('.qp-btn').forEach(bt => bt.addEventListener('click', () => { $('#f-price').value = bt.dataset.v; save({ '心理价位': bt.dataset.v }); }));
  $('#f-status').addEventListener('change', e => save({ '合作状态': e.target.value }));
  $('#f-quote').addEventListener('change', e => save({ '对方报价': e.target.value }));
  $('#f-notes').addEventListener('change', e => save({ '备注': e.target.value }));
  if (d.composer && $('#reply')) bindComposer(d);
  $$('.intent-go').forEach(bt => bt.addEventListener('click', async () => {
    const mode = bt.dataset.mode || 'reply';
    const intent = $(`.intent-in[data-mode="${bt.dataset.mode}"]`).value.trim();
    if (!intent) return toast('先写一句大意', true);
    const target = mode === 'nudge' ? $('#nudge') : $('#reply');
    bt.disabled = true; bt.innerHTML = '<span class="spin"></span> 起草中…';
    try {
      const out = await api('/api/suggest', { h, mode, intent });
      target.value = out.text;
      const box = target.parentElement.querySelector('.zh');
      const zhHtml = out.zh ? `<div class="zh-h">中文意思<span>按你这次的大意起草</span></div><div class="zh-b">${esc(out.zh)}</div>` : '';
      if (box) box.innerHTML = zhHtml; else if (zhHtml) target.insertAdjacentHTML('afterend', `<div class="zh">${zhHtml}</div>`);
      toast(out.note || '已按大意起草');
    } catch (e) { toast(e.message, true); }
    bt.disabled = false; bt.textContent = '按大意起草';
  }));
  const nudgeSend = $('#nudge-send');
  if (nudgeSend) nudgeSend.addEventListener('click', async () => {
    const body = $('#nudge').value.trim();
    if (body.length < 10) return toast('内容太短', true);
    const ok = await confirmBox({ title: '确认发送', ok: '发送', kv: [['收件人', (d.to_addrs || []).join(', ')], ['方式', '在原邮件线程里回复']], preview: body });
    if (!ok) return;
    nudgeSend.disabled = true; nudgeSend.innerHTML = '<span class="spin"></span> 发送中…';
    try { await api('/api/send', { h, body, mode: 'nudge' }); toast('已发送'); closeDrawer(); refreshInPlace(h); }
    catch (e) { toast(e.message, true); nudgeSend.disabled = false; nudgeSend.textContent = '发送'; }
  });
  const draft = $('#draft');
  if (draft) draft.addEventListener('click', async () => {
    const body = $('#reply').value.trim();
    if (body.length < 10) return toast('先写好回复内容', true);
    try { await api('/api/draft', { h, body }); toast('已存到 Lark 草稿箱'); } catch (e) { toast(e.message, true); }
  });
  const dismiss = $('#dismiss');
  if (dismiss) dismiss.addEventListener('click', async () => {
    try { await api('/api/dismiss', { h }); toast('已从待回复里移走，对方再来信会重新出现'); closeDrawer(); refreshInPlace(h); } catch (e) { toast(e.message, true); }
  });
  const send = $('#send');
  if (send) send.addEventListener('click', async () => {
    const body = $('#reply').value.trim();
    if (body.length < 10) return toast('回复内容太短', true);
    if (body.includes('【价格】')) { toast('先填价格', true); const a = $('#c-amt'); if (a) a.focus(); return; }
    const close = S.cmp && S.cmp.scenario === 'decline';
    const last = [...d.messages].reverse().find(m => m.kind === 'in');
    const ok = await confirmBox({ title: close ? '发送婉拒' : '确认发送回复', ok: '发送',
      kv: [['收件人', d.reply_to || ''], ['主题', 'Re: ' + (last ? last.subject : '')]].concat(close ? [['之后', '合作状态改为「已放弃」']] : []),
      preview: body });
    if (!ok) return;
    send.disabled = true; send.innerHTML = '<span class="spin"></span> 发送中…';
    const ctl = S.cmp ? { ...S.cmp, chips: undefined, custom: !!S.cmpCustom, edited: S.cmpDirty } : null;
    try { await api('/api/send', { h, body, mode: close ? 'close' : 'reply', ctl }); toast('已发送'); closeDrawer(); refreshInPlace(h); }
    catch (e) { toast(e.message, true); send.disabled = false; send.textContent = '发送'; }
  });
  const fu = $('#fu-one');
  if (fu) fu.addEventListener('click', async () => {
    const body = $('#fu').value.trim();
    const ok = await confirmBox({ title: '发送跟进', ok: '发送', kv: [['收件人', (d.row.emails || []).join(', ')], ['方式', '在原邮件线程里回复']], preview: body });
    if (!ok) return;
    try { await api('/api/followups', { items: [{ h, step: d.followup.step, body }] }); toast('已排队，马上发出'); closeDrawer(); refreshInPlace(h); }
    catch (e) { toast(e.message, true); }
  });
  const dmCopy = $('#dm-copy');
  if (dmCopy) dmCopy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#dm').value); toast('私信已复制'); } catch (e) { $('#dm').select(); toast('请按 ⌘C 复制', true); }
  });
  const dmDone = $('#dm-done');
  if (dmDone) dmDone.addEventListener('click', async () => {
    try { await api('/api/dm', { h }); toast('已记为「已发私信」'); closeDrawer(); refreshInPlace(h); } catch (e) { toast(e.message, true); }
  });
}

// ------------------------------------------------------------------ modal helpers
function modal(html) {
  const wrap = $('#modal');
  wrap.innerHTML = `<div class="modal">${html}</div>`;
  wrap.classList.add('on');
  return wrap;
}
function closeModal() { const w = $('#modal'); w.classList.remove('on'); w.innerHTML = ''; }
function confirmBox({ title, ok, kv = [], preview = '' }) {
  return new Promise(resolve => {
    const w = modal(`<h2>${esc(title)}</h2><div class="body"><div class="kv">${kv.map(([k, v]) => `<span class="k">${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>
      ${preview ? `<pre>${esc(preview)}</pre>` : ''}</div><div class="foot"><button class="btn" data-a="no">取消</button><button class="btn primary" data-a="yes">${esc(ok)}</button></div>`);
    const done = v => { closeModal(); resolve(v); };
    $('[data-a=no]', w).addEventListener('click', () => done(false));
    $('[data-a=yes]', w).addEventListener('click', () => done(true));
    w.addEventListener('click', e => { if (e.target === w) done(false); }, { once: true });
  });
}
function promptBox({ title, label, value = '' }) {
  return new Promise(resolve => {
    const w = modal(`<h2>${esc(title)}</h2><div class="body"><label style="display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:13px">${esc(label)}
      <input type="email" id="pb" value="${esc(value)}" style="width:100%"></label></div>
      <div class="foot"><button class="btn" data-a="no">取消</button><button class="btn primary" data-a="yes">下一步</button></div>`);
    const done = v => { closeModal(); resolve(v); };
    $('[data-a=no]', w).addEventListener('click', () => done(null));
    $('[data-a=yes]', w).addEventListener('click', () => done($('#pb').value.trim() || null));
    setTimeout(() => $('#pb').focus(), 30);
  });
}

// ------------------------------------------------------------------ loading
async function render() {
  const main = $('#main');
  renderTop();
  if (S.view === 'today') return renderToday(main);
  if (S.view === 'all') return renderAll(main);
  if (S.view === 'progress') return renderProgress(main);
  return renderHealth(main);
}
let lastSynced = null;
async function loadOverview() {
  try {
    S.ov = await api('/api/overview');
    renderTop();
    if (S.view === 'today') { renderBanners(); updateCards(); }
    const changed = S.ov.synced_at !== lastSynced;
    lastSynced = S.ov.synced_at;
    const editing = document.activeElement && ['TEXTAREA', 'INPUT', 'SELECT'].includes(document.activeElement.tagName);
    if (changed && !editing && !$('#modal').classList.contains('on') && !S.drawer) refreshInPlace(null);
  } catch (e) {
    $('#sync-pill').className = 'pill bad'; $('#sync-pill').textContent = '连不上看板服务';
  }
}
// In-place refresh: the row you acted on fades out, the numbers update, the list keeps its scroll position.
S.hidden = {};  // people acted on in this page, kept off the lists until the server has caught up (10 minutes at most)
const isHidden = h => S.hidden[h] && Date.now() - S.hidden[h] < 600000;
function fadeOut(h) {
  $$(`.row[data-h="${CSS.escape(h)}"], tr[data-h="${CSS.escape(h)}"]`).forEach(el => {
    el.style.transition = 'opacity .18s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 180);
  });
}
function updateCards() {
  const c = (S.ov && S.ov.counts) || {};
  $$('.card[data-q]').forEach(el => {
    const q = QUEUES.find(x => x.k === el.dataset.q);
    if (!q) return;
    $('.num', el).textContent = cardCount(q.k, c) ?? 0;
    const hint = $('.hint', el);
    hint.textContent = q.hint(c); hint.classList.toggle('hot', !!(q.hot && q.hot(c)));
  });
}
async function refreshInPlace(gone) {
  (Array.isArray(gone) ? gone : gone ? [gone] : []).forEach(h => { S.hidden[h] = Date.now(); fadeOut(h); });
  const y = window.scrollY;
  S.all = null;
  try { S.ov = await api('/api/overview'); lastSynced = S.ov.synced_at; } catch (e) { /* keep what is on screen */ }
  renderTop();
  if (S.view === 'today') { updateCards(); await renderQueue(); }
  else await render();
  window.scrollTo(0, y);
}
function refreshAll() { refreshInPlace(null); }
lastSynced = undefined;
(async () => { try { S.ov = await api('/api/overview'); lastSynced = S.ov.synced_at; } catch (e) { /* render shows the state */ } render(); })();
setInterval(loadOverview, 30000);
