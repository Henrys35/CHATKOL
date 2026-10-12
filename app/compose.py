"""Replies built from the reply template (templates/回复模板.md), so each email needs only a little customising and a price.

The fixed wording is his, from the template file; the AI only writes the opening line and answers to questions the template
does not cover. Price, number of videos, platform, currency and the add-ons come from the controls above the reply box, so
changing any of them rebuilds the email at once, without another AI call. Every email can be shown in three layouts
(prose, bullets, short) so the user can look at the candidates and pick.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402

TPL_FILE = settings.path(settings.get('templates', 'dir', default='templates')) / settings.get('templates', 'reply', default='回复模板.md')
LANGS = ('英文', '繁中', '韩文', '日文')
SCENARIOS = ('offer', 'accept', 'askrate', 'decline', 'thanks')
LAYOUTS = ('prose', 'bullets', 'short')
PLATFORMS = ('tt', 'ig', 'both', 'three', 'yt', 'any')
# the add-on switches; each one's wording is the template section of that name (投流·段落, 投流·要点, ...)
ADDON_KEY = {'promo': '投流', 'revshare': '分成', 'pro': 'Pro', 'excl': '排他', 'label': '标注'}
# which ones the reply box shows, their labels and defaults come from config.json
ADDONS = tuple(k for k, *_ in settings.addons() if k in ADDON_KEY) or tuple(ADDON_KEY)


def addon_labels():
    return [[k, label] for k, label, _, _ in settings.addons() if k in ADDON_KEY]
TAXED = ('JPY', 'KRW', 'TWD')  # quotes there are often given before or after tax, so the price says which
MISSING = '【价格】'
SEP = {'英文': ' ', '韩文': ' ', '繁中': '', '日文': ''}

_cache = {'mtime': None, 'data': None}


def templates():
    mtime = TPL_FILE.stat().st_mtime
    if _cache['mtime'] != mtime:
        _cache['data'] = parse(TPL_FILE.read_text())
        _cache['mtime'] = mtime
    return _cache['data']


def parse(text):
    """{language: {snippet name: (text, Chinese meaning)}}: under each `## language`, every `### name` holds the wording in its
    first code block and, for English, Korean and Japanese, the Chinese meaning in its second."""
    data = {}
    for section in re.split(r'\n## ', '\n' + text)[1:]:
        lang, body = (section.split('\n', 1) + [''])[:2]
        if lang.strip() not in LANGS:
            continue
        snippets = {}
        for sub in re.split(r'\n### ', '\n' + body)[1:]:
            key, rest = (sub.split('\n', 1) + [''])[:2]
            blocks = re.findall(r'```[a-z]*\n(.*?)\n```', rest, re.S)
            if blocks:
                snippets[key.strip()] = (blocks[0].strip('\n'), blocks[1].strip('\n') if len(blocks) > 1 else '')
        data[lang.strip()] = snippets
    return data


# ---------------------------------------------------------------- price and "what we buy", per language

SYMBOL = {'USD': '$', 'GBP': '£', 'EUR': '€', 'CAD': 'CA$', 'AUD': 'A$', 'NZD': 'NZ$', 'SGD': 'S$', 'HKD': 'HK$',
          'JPY': '¥', 'KRW': '₩', 'TWD': 'NT$', 'MYR': 'RM'}
KR_UNIT = {'USD': '달러', 'GBP': '파운드', 'EUR': '유로', 'JPY': '엔', 'CAD': '캐나다 달러', 'AUD': '호주 달러', 'TWD': '대만 달러'}
JP_UNIT = {'USD': '米ドル', 'GBP': 'ポンド', 'EUR': 'ユーロ', 'KRW': 'ウォン', 'TWD': '台湾ドル', 'CAD': 'カナダドル', 'AUD': '豪ドル'}
TW_PREFIX = {'TWD': 'NT$', 'USD': 'US$', 'JPY': '日幣 ', 'KRW': '韓元 '}


def _num(a):
    return f'{a:,.0f}' if float(a).is_integer() else f'{a:,.2f}'


def money(amount, cur, lang, tax):
    """(price as written in the email, part a Korean particle attaches to), Chinese version."""
    a = float(amount)
    taxed = bool(tax) and cur in TAXED
    zh = SYMBOL.get(cur, cur + ' ') + _num(a) + (' 含税' if taxed else '')
    if lang == '韩文':
        base = (f'{int(a) // 10000:,}만 원' if a % 10000 == 0 else f'{_num(a)}원') if cur == 'KRW' else _num(a) + KR_UNIT.get(cur, ' ' + cur)
        return base + ('(부가세 포함)' if taxed else ''), base, zh
    if lang == '日文':
        base = (f'{int(a) // 10000:,}万円' if a % 10000 == 0 else f'{_num(a)}円') if cur == 'JPY' else _num(a) + JP_UNIT.get(cur, cur)
        return base + ('（税込）' if taxed else ''), base, zh
    if lang == '繁中':
        base = TW_PREFIX.get(cur, SYMBOL.get(cur, cur + ' ')) + _num(a)
        return base + (' 含稅' if taxed else ''), base, zh
    base = SYMBOL.get(cur, cur + ' ') + _num(a)
    return base + (' (tax included)' if taxed else ''), base, zh


NUM_EN = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six'}
NUM_TW = {1: '一', 2: '兩', 3: '三', 4: '四', 5: '五', 6: '六'}
NUM_ZH = {1: '一', 2: '两', 3: '三', 4: '四', 5: '五', 6: '六'}


def deal(lang, n, platform):
    if lang == '繁中':
        w = NUM_TW.get(n, str(n)) + '支'
        return {'tt': f'{w} TikTok 影片', 'ig': f'{w} Instagram Reels', 'yt': f'{w} YouTube Shorts', 'any': f'{w}影片',
                'both': f'{w}影片（TikTok 和 Reels 同步發布）', 'three': f'{w}影片（TikTok、Reels、YouTube Shorts 三平台同步發布）'}[platform]
    if lang == '韩文':  # every phrase ends in 편, so the particle after it is always 을
        return {'tt': f'틱톡 영상 {n}편', 'ig': f'인스타그램 릴스 {n}편', 'yt': f'유튜브 쇼츠 {n}편', 'any': f'영상 {n}편',
                'both': f'틱톡·릴스 동시 업로드 영상 {n}편', 'three': f'틱톡·릴스·유튜브 쇼츠 동시 업로드 영상 {n}편'}[platform]
    if lang == '日文':
        return {'tt': f'TikTok動画{n}本', 'ig': f'Instagramリール{n}本', 'yt': f'YouTubeショート{n}本', 'any': f'動画{n}本',
                'both': f'TikTokとInstagramリールに同時投稿する動画{n}本',
                'three': f'TikTok・Instagramリール・YouTubeショートに同時投稿する動画{n}本'}[platform]
    w, one = NUM_EN.get(n, str(n)), n == 1
    return {'tt': f'{w} TikTok' + ('' if one else 's'), 'ig': f'{w} Instagram Reel' + ('' if one else 's'),
            'yt': f'{w} YouTube Short' + ('' if one else 's'), 'any': f'{w} video' + ('' if one else 's'),
            'both': f'{w} video posted to both TikTok and Instagram' if one else f'{w} videos, each posted to both TikTok and Instagram',
            'three': (f'{w} video posted to TikTok, Instagram and YouTube Shorts' if one
                      else f'{w} videos, each posted to TikTok, Instagram and YouTube Shorts')}[platform]


def deal_zh(n, platform):
    w = NUM_ZH.get(n, str(n)) + '条'
    return {'tt': f'{w} TikTok', 'ig': f'{w} Instagram Reels', 'yt': f'{w} YouTube Shorts', 'any': f'{w}视频',
            'both': f'{w}视频（TikTok 和 Instagram 同发）', 'three': f'{w}视频（TikTok、Instagram、YouTube Shorts 三平台同发）'}[platform]


def josa(word, pair):
    """Korean particle after a word: ('을', '를') or ('으로', '로'), chosen by the last syllable's final consonant."""
    last = word.rstrip()[-1:]
    code = ord(last) - 0xAC00 if last else -1
    if not 0 <= code < 11172:
        return pair[1]
    final = code % 28
    if pair[0] == '으로':
        return pair[1] if final in (0, 8) else pair[0]  # no final consonant, or ㄹ, takes 로
    return pair[0] if final else pair[1]


def tokens(lang, recipient, creator_ref, n):
    """Person and number words. A manager or agency hears about the creator in the third person (English only: agencies
    write to us in English)."""
    one = n == 1
    agency = recipient == 'agency' and bool(creator_ref) and lang == '英文'
    c = creator_ref
    t = {'{your}': f"{c}'s" if agency else 'your', '{You}': c if agency else 'You', '{you}': c if agency else 'you',
         "{you're}": f'{c} is' if agency else "you're", "{you'd}": f'{c} would' if agency else "you'd",
         '{them}': 'them' if agency else 'you', '{write}': 'writes' if agency else 'write',
         '{the video}': 'the video' if one else 'each video', '{The video}': 'The video' if one else 'Each video',
         '{it}': 'it' if one else 'each one', '{script}': 'script' if one else 'scripts',
         '{the post}': 'the post' if one else 'each post',
         '{每支}': '' if one else '每支', '{각}': '' if one else '각 ', '{各}': '' if one else '各',
         '{你}': f' {c} ' if agency else '你', '{你的}': f' {c} 的' if agency else '你的', '{视频}': '视频' if one else '每条视频'}
    return t


def tidy_zh(s):
    """A creator's name inside the Chinese meaning gets spaces around it; drop the ones that end up doubled or before punctuation."""
    s = re.sub(r' {2,}', ' ', s)
    s = re.sub(r' ([，。；：！？）、])', r'\1', s)
    return re.sub(r'([（，。；：！？、]) ', r'\1', s)


def fill(text, t):
    for k, v in t.items():
        text = text.replace(k, v)
    return text


# ---------------------------------------------------------------- defaults for the reply box

def guess_platform(item, creator_platform):
    s = (item or '').lower()
    tt = 'tiktok' in s or '抖音' in s
    ig = any(x in s for x in ('instagram', 'reel', 'insta')) or bool(re.search(r'\big\b', s))
    yt = 'short' in s or 'youtube' in s
    if tt and ig and yt:
        return 'three'
    if tt and ig:
        return 'both'
    if tt:
        return 'tt'
    if ig:
        return 'ig'
    if yt:
        return 'yt'
    return {'TikTok': 'tt', 'Instagram': 'ig', 'YouTube': 'yt'}.get(creator_platform, 'any')


def quick_amounts(amount, cur):
    if not amount:
        return []
    step = {'JPY': 5000, 'KRW': 10000, 'TWD': 500}.get(cur) or (50 if amount >= 1000 else 10)
    def r(x):
        return max(step, round(x / step) * step)
    return [['对方报价', amount], ['九折', r(amount * 0.9)], ['八折', r(amount * 0.8)], ['七成', r(amount * 0.7)], ['五成', r(amount * 0.5)]]


def defaults(lang, quote, creator_platform, recipient, answered_before, category, mind_price):
    """What the reply box starts with: the user changes what they need, usually just the price."""
    cur = (quote or {}).get('currency') or {'繁中': 'TWD', '韩文': 'KRW', '日文': 'JPY'}.get(lang, 'USD')
    if category == '拒绝':
        scenario = 'thanks'
    elif quote:
        scenario = 'offer'
    else:
        scenario = 'askrate'
    layout = 'bullets' if recipient == 'agency' else ('short' if answered_before else 'prose')
    chips = quick_amounts((quote or {}).get('amount'), cur)
    if mind_price and cur == 'USD':
        chips.insert(0, ['心理价位', mind_price])
    # on for everyone, or only for some languages (e.g. the ad label in Japanese and Korean, where the law requires paid
    # content to say it is an ad; Japan's 2023 stealth-marketing rules are aimed at the advertiser)
    addons = [k for k, _, on, on_for in settings.addons() if k in ADDON_KEY and (on or lang in on_for)]
    return {'scenario': scenario, 'layout': layout, 'n': 1, 'platform': guess_platform((quote or {}).get('item'), creator_platform),
            'amount': None, 'currency': cur, 'tax': cur in TAXED, 'addons': addons, 'chips': chips}


# ---------------------------------------------------------------- the email

def compose(lang, parts, ctl, quoted):
    """Build the reply. parts: the AI's opening and answers (may be empty); ctl: what the user set in the reply box; quoted:
    whether the creator has named a price (a counter says "could we do", a first offer says "we'd love to offer")."""
    lib = templates()
    if lang not in lib:
        lang = '英文'
    T = lib[lang]
    n = max(1, min(int(ctl.get('n') or 1), 9))
    t = tokens(lang, parts.get('recipient'), (parts.get('creator_ref') or '').strip(), n)
    sep = SEP[lang]

    t['{发件人}'] = settings.SENDER.get('name') or ''

    def get(key):
        text, zh = T.get(key, ('', ''))
        return fill(text, t), fill(zh, t)

    def join(pairs):
        return sep.join(p[0] for p in pairs if p[0]), ''.join(p[1] for p in pairs if p[1])

    scenario = ctl.get('scenario') if ctl.get('scenario') in SCENARIOS else 'askrate'
    layout = ctl.get('layout') if ctl.get('layout') in LAYOUTS else 'prose'
    platform = ctl.get('platform') if ctl.get('platform') in PLATFORMS else 'any'
    on = [a for a in ADDONS if a in (ctl.get('addons') or [])]

    name = (parts.get('addressee') or '').strip() or {'英文': 'there'}.get(lang, '')
    greet = get('问候')
    greet = (greet[0].replace('{称呼}', name).strip(), greet[1].replace('{称呼}', name).strip())
    if not name:
        greet = ({'繁中': '你好！', '韩文': '안녕하세요!', '日文': 'ご担当者様'}.get(lang, 'Hi there,'), '你好，')

    paras, offer_text, missing = [], '', False
    if scenario == 'thanks':
        paras.append(get('回个谢'))
    else:
        opener = (parts.get('opener') or '').strip()
        paras.append((opener, (parts.get('opener_zh') or '').strip()) if opener else get('默认开头'))
        answers = (parts.get('answers') or '').strip()
        if answers and scenario != 'decline':
            paras.append((answers, (parts.get('answers_zh') or '').strip()))
        if scenario == 'decline':
            text, zh = get('婉拒')
            zh_parts = zh.split('\n\n') if zh else []
            for i, p in enumerate(text.split('\n\n')):
                paras.append((p, zh_parts[i] if i < len(zh_parts) else ''))
        else:
            if scenario in ('offer', 'accept'):
                amount = ctl.get('amount')
                if amount not in (None, '') and float(amount) > 0:
                    price, base, price_zh = money(amount, ctl.get('currency') or 'USD', lang, ctl.get('tax'))
                else:
                    price, base, price_zh, missing = MISSING, '', MISSING, True
                key = '接受报价' if scenario == 'accept' else ('出价·对方已报价' if quoted else '出价·对方没报价')
                text, zh = get(key)
                text = (text.replace('{价格}', price).replace('{内容}', deal(lang, n, platform))
                        .replace('{으로}', josa(base, ('으로', '로')) if base else '으로')
                        .replace('{을}', josa(base, ('을', '를')) if base else '을'))
                zh = zh.replace('{价格}', price_zh).replace('{内容}', deal_zh(n, platform))
                offer = (text, zh)
                offer_text = text
            base_lines = get('条件·要点')
            base_t, base_z = base_lines[0].split('\n'), base_lines[1].split('\n') if base_lines[1] else []

            def bullets(lead):
                items = [(base_t[i], base_z[i] if i < len(base_z) else '') for i in range(min(2, len(base_t)))]
                items += [get(ADDON_KEY[a] + '·要点') for a in on if a in ('promo', 'revshare', 'excl', 'label')]
                items += [(base_t[i], base_z[i] if i < len(base_z) else '') for i in range(2, len(base_t))]
                if 'pro' in on:
                    items.append(get('Pro·要点'))
                return (lead[0] + '\n' + '\n'.join('• ' + x[0] for x in items),
                        (lead[1] + '\n' + '\n'.join('• ' + x[1] for x in items if x[1])) if lead[1] else '')

            if scenario in ('offer', 'accept'):
                if layout == 'short':
                    paras.append(join([offer] + [get(ADDON_KEY[a] + '·段落') for a in on]))
                elif layout == 'prose':
                    paras.append(join([offer] + [get(ADDON_KEY[a] + '·段落') for a in on if a in ('promo', 'revshare')]))
                    paras.append(join([join([get('段落开头·出价'), get('条件·段落')])]
                                      + [get(ADDON_KEY[a] + '·段落') for a in on if a in ('pro', 'excl', 'label')]))
                else:
                    paras.append(bullets(join([offer, get('要点开头·出价')])))
                closing = get('结尾·接受' if scenario == 'accept' else '结尾·出价')
            else:  # askrate: how it would work, then ask for their price
                if layout == 'prose':
                    paras.append(join([join([get('段落开头·请他报价'), get('条件·段落')])] + [get(ADDON_KEY[a] + '·段落') for a in on]))
                elif layout == 'bullets':
                    paras.append(bullets(get('要点开头·请他报价')))
                closing = get('请他报价')
            custom = ctl.get('custom') or {}
            if (custom.get('text') or '').strip():
                paras.append((custom['text'].strip(), (custom.get('zh') or '').strip()))
            paras.append(closing)
    sign = get('署名')
    text = '\n\n'.join([greet[0]] + [p[0] for p in paras if p[0]] + [sign[0]])
    zh = '' if lang == '繁中' else tidy_zh('\n\n'.join([greet[1]] + [p[1] for p in paras if p[1]] + [sign[1]]))
    return {'text': text, 'zh': zh, 'offer': offer_text, 'missing_price': missing, 'lang': lang}
