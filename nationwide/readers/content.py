"""HTML の本文の範囲、使い回しの部分、方針の文のふるい、deferred と caveat の見分け方、但し書きの洗い出し
(DESIGN.md §3.2・§2.6・§3.8)

  python nationwide/readers/content.py <HTML のファイル> [--url URL]    # 本文の範囲・文・方針の文の分け方を表示

祝日・年末年始・期間・但し書きの文は、その日程の本文からだけ取る。役所の開庁時間の文 (塩竈のページの足
「閉庁日：…祝日、年末年始（12月29日から1月3日）」、御蔵島のレイアウトの表「開庁時間…※祝休日、12月29日～1月3日を除く」) を
方針と読まないため。ここでは文を切り出して分けるだけで、値 (方針の IR) にするのは後の段 (rules.py・expand.py)。
"""
import hashlib, re, sys, unicodedata, warnings
from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning

import consume

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object", "button", "select", "input", "textarea",
             "map", "area", "link", "meta", "head"}
EXCLUDE_TAGS = {"header", "footer", "nav", "aside", "form"}
# id・class で外すもの (ページの頭・足・横、パンくず、文字サイズ・背景色の切り替え、お問い合わせの欄、SNS、関連リンク)
EXCLUDE_NAME = re.compile(
    r"(?:^|[-_\s])(?:header|head_?wrap|footer|foot|f_?navi|nav|navi|navigation|g_?nav|global(?:_?menu|_?navi|_?info)?|"
    r"menu|menulist|sp_?menu|sidebar\d*|side|side_?menu|sub_?menu|local_?nav|breadcrumbs?|pankuzu(?:_?wrap)?|topic_?path|"
    r"crumbs?|sitemap|copyright|page_?top|banner|sns|share|social|font_?size|text_?size|moji_?size|fontsize|zoom|"
    r"bg_?color|color_?change|search|section_?footer|toiawase|inquiry|contact(?:_?box|_?info)?|author(?:_?info)?|"
    r"related|kanren|recommend|ai_?recommend|questionnaire|enquete|evaluation|feedback|reader|adobe|pdf_?reader|"
    r"skip|hidden|print_?only)(?:$|[-_\s\d])", re.I)
CMS_IDS = ("main_body", "mainbody", "main-body", "main_contents", "maincontents", "main-contents", "contents_body",
           "contents-body", "tmp_contents", "tmp_main", "contents_main", "content_main", "honbun", "article", "entry",
           "detail", "page-body", "page_body", "contents", "content", "main", "main_a", "mymain")
# 本文の中の、お問い合わせの欄などの見出し (その見出しの節を外す) と、1 行だけの飾り (その行だけ外す)
INQUIRY_SECTION = re.compile(r"^(?:このページ(?:に関する|の)?(?:お)?問い?合わ?せ|お問い?合わ?せ先|問合せ先|このページの作成担当|"
                             r"この情報はお役に立ちましたか|このページの情報は役に立ちましたか|このページを見ている人は|関連リンク|関連情報)")
INQUIRY_LINE = re.compile(r"^(?:ページの先頭へ|文字サイズ|背景色|PDF(?:形式の)?ファイルをご覧|Adobe|アドビ)")
INQUIRY = re.compile(f"(?:{INQUIRY_SECTION.pattern})|(?:{INQUIRY_LINE.pattern})")
INQUIRY_BOX_MAX = 600               # 見出しでない札 (p・dt・strong) で始まる欄は、この字数以下の塊の中だけで外す
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BLOCKS = {"p", "div", "li", "dt", "dd", "td", "th", "caption", "tr", "table", "section", "article", "main", "blockquote",
          "pre", "ul", "ol", "dl", "figure", "figcaption", "address", "center", "body", "html"}
UPDATED = re.compile(r"(?:最終)?更新日|掲載日|公開日|閲覧数|アクセス数|ページ番号|印刷用ページ")

OFFICE_WORDS = ("開庁", "閉庁", "業務時間", "窓口", "受付", "休館", "開館", "営業")
GOMI_WORDS = ("収集", "回収", "ごみ", "ゴミ", "資源", "出す", "出せ")
DROPOFF_WORDS = ("持込", "持ち込", "搬入", "受け入れ", "受入")
GOMI_HEADING = re.compile(r"ごみ|ゴミ|資源|収集|回収|分別|リサイクル|粗大")

# §2.6 の見分け方
PERIOD_NAMES = ("年末年始", "ゴールデンウィーク", "GW", "大型連休", "お盆休み", "盆休み", "お盆", "正月", "年末", "年始", "連休")
PERIOD_CANON = {"お盆休み": "お盆", "盆休み": "お盆", "GW": "ゴールデンウィーク"}       # GW は NFKC のあと (「ＧＷ」も)
# 祭典・行事の名前: 漢字・カタカナ (か、ひらがな 2 字以上) のあとの「祭」「まつり」。「祭日」は休日の種類なので入れない
FESTIVAL = re.compile(r"(?:[一-鿿゠-ヿー・]{1,10}|[ぁ-ん]{2,6})(?:祭(?!日)|まつり)|祭典")
MARKERS_MAY = ("ことがあります", "場合があります", "変更あり",
               "ことがございます", "場合がございます")       # 丁寧な言い方 (川口「通常の周期と異なることがございます」)
MARKERS_LATER = ("決まり次第", "後日", "別途", "改めて", "未定", "反映されていません", "反映していません", "広報", "区報", "市報",
                 "町報", "村報", "ニュース", "ホームページ")
HOLIDAY_KINDS_WORDS = ("祝日", "祭日", "振替休日", "休日", "祝休日")
CHANGE_WORDS = ("変更", "変わ", "休", "中止", "日程", "収集日", "異な")

# §3.8 の但し書きの印 (NFKC のあとで探す)
QUALIFIER_MARKS = {
    "count": ("第5", "5番目", "5回目", "5週", "番目", "回目", "当該月", "隔週", "月末"),
    "range": ("除く", "以外", "のみ", "だけ", "夏", "冬"),
    "change": ("休み", "お休み", "休止", "中止", "祝", "祭日", "振替", "臨時", "変更", "変わ", "順延", "天候", "通常どおり", "通常通り",
               "停止", "廃止", "中断", "延期", "見合わせ", "見合せ", "取りやめ", "取り止め", "取止め", "減ります", "減る", "減少"),
    "negative": ("ありません", "しません", "行いません", "行わない", "なし", "無し", "できません", "出せません", "出せない", "出せなく",
                 "受け付けていません", "受け付けません", "受付していません", "受け付けておりません", "できない", "できなく"),
    "period": ("年末", "年始", "正月", "お盆", "盆休み", "連休", "当分の間", "当面の間", "当面", "しばらく", "暫く"),
    "symbol": ("※", "注", "*"),
}
# 期間の名前 → 月 (§3.8 の 4 で、月を名指しした但し書きとして扱う。ここに無い名前は月が決まらない)
PERIOD_MONTHS = {"年末年始": (12, 1), "年末": (12,), "年始": (1,), "正月": (1,), "お盆": (7, 8), "ゴールデンウィーク": (4, 5)}
# 月をまたいで当たりうる但し書き (祝日・休日・第5週)。月と一緒に書いてあっても月を決めない
# (「毎月第1月曜日（祝日の場合はその直後の平日、8月は除く）」を 8 月だけ止めると、ほかの月の祝日を答える)
MONTH_WIDE = re.compile(r"祝|休日|第\s*5|5\s*週|5\s*番目|5\s*回目")

# ---- 月の名指し: 閉じた見本の並び (whitelist) ----
# 文の全体が「閉じた月の並び・範囲」+「決まった述語」にまるごと当たるときだけ月を返す。ほかの言い方 (「から」「より」「以降」
# 「まで」の相手の月が無い・「終了」「開始」「境」「後」「前」「頃」「半ば」「初め」「頭」「翌月」「なったら」「過ぎ」
# 「他」「ほか」「その他」「それ以外」「通常」「最終」「月末の」「第N週」・「のみ」「だけ」「限り」「隔月」・年度や「翌年」を
# 挟んだ範囲・主語の付いた文) はすべて空 (= 月を名指ししていない → 系列ごと止める)。打ち消しの印を探す書き方では、
# 言い換え (「8月を最後に終了」「8月になったら隔週」) で、その月だけ止めてほかの月を答える読みが 2 回とも残った
_NM_N = r"(?:1[0-2]|0?[1-9])"
_NM_SEP = r"(?:・|、|と|及び|および)"
_NM_YE = r"\((?:12月\d{1,2}日[~〜\-]1月\d{1,2}日(?:まで)?|12/\d{1,2}[~〜\-]1/\d{1,2})\)"      # 年末年始の 12 月・1 月の日付だけ
_NM_SEASON = r"(?:夏季|夏期|夏|冬季|冬期|冬)"
_NM_ONE = (r"(?:" + _NM_N + r"(?:" + _NM_SEP + _NM_N + r")*月"                                  # 5月・5・6月・5、6月
           r"|" + _NM_N + r"月?(?:[~〜\-]|から|より)" + _NM_N + r"月(?:まで)?"                    # 5~9月・5月から9月まで
           r"|年末年始(?:" + _NM_YE + r")?|年末|年始|正月|お盆(?:の期間|期間)?|ゴールデンウィーク(?:の期間|期間)?)")
_NM_LIST = _NM_ONE + r"(?:" + _NM_SEP + _NM_ONE + r")*"
_NM_SUBJ = (r"(?P<subj>" + _NM_SEASON + r"\(" + _NM_LIST + r"\)"                                # 夏（7月～9月）
            r"|" + _NM_LIST + r"(?:\(" + _NM_SEASON + r"\))?)(?:中(?=は))?")                     # 7月～8月（夏季）・6月中
_NM_DIFF = r"(?:収集日程|収集日|日程|収集曜日)(?:、時間)?が"
MONTH_TEMPLATES = (
    # 8月は休み / 8月はお休みです / 10月から3月まで休み / 年末年始の収集は休み / お盆の期間は休みます
    ("off", re.compile(_NM_SUBJ + r"(?:は|(?<=まで)|の(?:収集|回収)は)(?:(?:収集|回収)(?:は|を))?(?:お休み|休み|休止)(?:です|します|ます|となります)?")),
    # 8月は収集しません / 8月の収集はありません
    ("none", re.compile(_NM_SUBJ + r"(?:は|の(?:収集|回収)は)(?:収集|回収)?(?:しません|いたしません|はありません|ありません|は行いません|"
                        r"を行いません|行いません|なし|はなし)")),
    # 7・8月は毎週 / 5月〜9月は隔週収集です / 5月〜9月は紙の収集が隔週
    ("weekly_or_biweekly", re.compile(_NM_SUBJ + r"は(?:[^\d月第年、。()]{1,20}の(?:収集|回収)が)?(?:毎週|隔週)(?:の?(?:収集|回収))?(?:です|します)?")),
    # 1月は第2月曜日 / 8月は第2・4水曜日に収集します (第5 は MONTH_WIDE で先に外す)
    ("nth_weekday", re.compile(_NM_SUBJ + r"は第[1-4](?:・第?[1-4])*[月火水木金土日]曜日?(?:に(?:収集|回収)(?:します)?|の(?:収集|回収)(?:です)?|です)?")),
    # 年末及び1月は収集日程、時間が異なります / 年末は別日程
    ("differs", re.compile(_NM_SUBJ + r"は(?:" + _NM_DIFF + r"異な(?:ります|ることがあります)|別日程(?:です|となります)?)")),
    # 年末年始は収集日が変わります (期間の名前だけ。「8月は収集日が変わります」は 8 月から先かもしれないので読まない)
    ("changes_period", re.compile(_NM_SUBJ + r"は" + _NM_DIFF + r"変わります")),
    # 足す収集の日: ペットボトルは5・6・7・8・9・10月のみ第1水曜日でも出せる (毛呂山)。足す日は読まないので、その月だけ止める
    # (「のみ」は足す日の月を限る言葉で、ほかの月は元の規則のまま。足す日は第1〜4・毎週の曜日だけ。「〜のみ収集」は見本の外)
    ("additional", re.compile(r"(?:[^\d月第年、。()]{1,20}は)?(?P<subj>" + _NM_LIST + r")のみ(?:毎週|第[1-4](?:・第?[1-4])*)?"
                              r"[月火水木金土日]曜日?(?:に)?(?:でも|にも)(?:出せる|出せます|出すことができます|収集します)")),
)
# 但し書きの印を探すだけの広い形 (「第1～3月曜日」も印にする。印を減らすと止める方に倒れなくなる)
MONTH_SPAN_MARK = re.compile(r"(\d{1,2})月?\s*[~〜～ー\-－から]+\s*(\d{1,2})月")


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def squash(s):
    """比べるための形: NFKC、空白 (ゼロ幅を含む) を除く"""
    return re.sub(r"[\s​‌‍﻿]+", "", nfkc(s))


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---- DOM ----
def parse(html_text):
    """HTML の文字列 → BeautifulSoup (どこでも同じ結果になるよう、Python 標準の html.parser で読む)"""
    return BeautifulSoup(html_text, "html.parser")


def _names(el):
    v = [el.get("id") or ""] + list(el.get("class") or [])
    return " ".join(x for x in v if x)


def css_path(el):
    """要素の道すじ (body から。tag#id か tag:nth-of-type(n))"""
    parts = []
    while isinstance(el, Tag) and el.name not in ("[document]", "html"):
        if el.get("id"):
            parts.append(f"{el.name}#{el['id']}")
            if el.name != "body":
                el = el.parent
                continue
        else:
            sib = [s for s in el.parent.find_all(el.name, recursive=False)] if el.parent else [el]
            parts.append(el.name if len(sib) <= 1 else f"{el.name}:nth-of-type({sib.index(el) + 1})")
        if el.name == "body":
            break
        el = el.parent
    return " > ".join(reversed(parts))


def _excluded(el):
    if el.name in EXCLUDE_TAGS:
        return True
    if (el.get("role") or "").lower() in ("navigation", "banner", "contentinfo", "complementary", "search"):
        return True
    n = _names(el)
    if n and EXCLUDE_NAME.search(n):
        return True
    st = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in st or "visibility:hidden" in st or el.has_attr("hidden")


def _text_len(el):
    return len(squash(el.get_text(" ")))


def _inside(el, anc):
    """el が anc の中 (anc 自身を含む) か。Tag の == は形が同じなら真になるので、is で比べる"""
    return el is anc or any(p is anc for p in el.parents)


def _heading_level(el):
    """見出しの高さ: h1〜h6 はその高さ、見出しだけを包む要素 (div > h3) は中の見出しの高さ、それ以外 (p・dt・strong) は 7"""
    if el.name in HEADINGS:
        return HEADINGS[el.name]
    hs = el.find_all(list(HEADINGS))
    return min(HEADINGS[h.name] for h in hs) if hs else 7


def _starts_section(el, lv):
    """el が高さ lv と同じか高い見出し (か、それを含む塊) か。lv が 7 (見出しの代わりの札) なら、どの見出しでも、
    dt・【】の行・太字だけの行でも節の始まりとみなす"""
    if not isinstance(el, Tag):
        return False
    if el.name in HEADINGS:
        return HEADINGS[el.name] <= lv
    if any(HEADINGS[h.name] <= lv for h in el.find_all(list(HEADINGS))):
        return True
    if lv >= 7:
        t = _clean(el.get_text(" "))
        return el.name == "dt" or bool(BRACKET_HEAD.match(t)) or _strong_only(el, t)
    return False


def _first_in_box(el):
    """el が親の中で、文字のある最初の子か (欄の札が欄の頭にある)"""
    for sib in el.previous_siblings:
        if isinstance(sib, Comment):
            continue
        if isinstance(sib, NavigableString):
            if str(sib).strip():
                return False
        elif isinstance(sib, Tag) and sib.name not in SKIP_TAGS and squash(sib.get_text(" ")):
            return False
    return True


def text_outside_links(el):
    """リンクの文字を除いた文字 (リンクの並びは方針の文にならない)"""
    out = []
    for s in el.find_all(string=True):
        if isinstance(s, Comment):
            continue
        p, skip = s.parent, False
        while p is not None and p is not el:
            if p.name == "a" or p.name in SKIP_TAGS:
                skip = True
                break
            p = p.parent
        if not skip:
            out.append(str(s))
    return " ".join(out)


def _remove_inquiry(el, keep, root, removed):
    """お問い合わせの欄などの札 el を外す。
    - 1 行だけの飾り (ページの先頭へ・文字サイズ・PDF の案内) は、それだけ外す
    - 見出し (か、見出しだけを包む塊) なら、その見出しと、同じか高い見出しまでの後ろの兄弟を外す
    - 見出しでない札 (p・dt・strong) は、600 字以下の塊の頭にあるときだけ、次の見出し (どの高さでも) までの後ろの兄弟を外す。
      塊の途中にあれば札だけ外す (「ページの先頭へ」のあとの節を消さない)
    どの場合も、日程の表 (keep) と、§2.6 の文・但し書きの文を持つ兄弟の手前で止める (外しすぎると deferred と但し書きが消えて、
    答えない方に倒れなくなる)"""
    t = squash(el.get_text(" "))
    removed.append(css_path(el))
    lv = _heading_level(el)
    alone = bool(INQUIRY_LINE.match(t))
    if not alone and lv >= 7:
        box = el.parent
        alone = not (box is not None and box is not root and box.name not in ("body", "html", "[document]")
                     and _text_len(box) <= INQUIRY_BOX_MAX and _first_in_box(el))
    if not alone:
        for sib in list(el.find_next_siblings()):
            if _starts_section(sib, lv) or any(_inside(k, sib) for k in keep) or policy_like(text_outside_links(sib)):
                break
            sib.decompose()
    el.decompose()


def _strip(root, keep=()):
    """root の中の外すもの (頭・足・横・お問い合わせの欄など) を取り除く。keep (日程の表など) を含むもの・その中のものは外さない"""
    removed = []
    keep = [k for k in keep if isinstance(k, Tag)]
    total = _text_len(root)
    for el in list(root.find_all(True)):
        if getattr(el, "decomposed", False) or el.parent is None:
            continue
        if any(_inside(k, el) or _inside(el, k) for k in keep):
            continue
        if el.name in SKIP_TAGS or (_excluded(el) and _text_len(el) <= 0.5 * total):
            # ページ全体を包む要素 (ASP.NET の <form>、Nuxt の div#pageTop) は名前が当たっても外さない
            removed.append(css_path(el))
            el.decompose()
    for el in list(root.find_all(list(HEADINGS) + ["p", "div", "dt", "strong", "span"])):
        if getattr(el, "decomposed", False) or el.parent is None or el is root:
            continue
        t = squash(el.get_text(" "))
        if not (t and INQUIRY.match(t) and len(t) <= 60) or any(_inside(k, el) or _inside(el, k) for k in keep):
            continue
        if len(lines(el)) > 1:
            continue                # 札と中身を包む塊 (本文の範囲そのものもありうる) は外さない。中の札の行を、その行として扱う
        _remove_inquiry(el, keep, root, removed)
    for c in root.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    return removed


class Region:
    """本文の範囲: root (要素)、method (main / article / role_main / cms:#id / table_block / body)、removed (外したものの道すじ)"""

    def __init__(self, root, method, removed, boilerplate_removed=0):
        self.root, self.method, self.removed, self.boilerplate_removed = root, method, removed, boilerplate_removed

    def lines(self):
        return lines(self.root)

    def sentences(self):
        return sentences(self.lines())

    def text(self):
        return "\n".join(l.text for l in self.lines())

    def __repr__(self):
        return f"Region({self.method}, {len(self.text())} 字, 外した {len(self.removed)})"


def _candidates(soup):
    cands = []
    for el in soup.find_all(True):
        if el.name in ("main", "article"):
            cands.append((el, el.name))
        elif (el.get("role") or "").lower() == "main":
            cands.append((el, "role_main"))
        elif (el.get("id") or "").lower() in CMS_IDS:
            cands.append((el, f"cms:#{el['id']}"))
    return cands


def content_region(html_or_soup, schedule_tables=(), boilerplate=None):
    """HTML → Region。schedule_tables (日程の表の要素か、表の中の文字) を含む所を選ぶ。
    boilerplate (使い回しの塊の sha256 の集まり) があれば、その塊を外す"""
    soup = parse(html_or_soup) if isinstance(html_or_soup, (str, bytes)) else html_or_soup
    body = soup.body or soup
    tables = [t for t in schedule_tables if isinstance(t, Tag)]
    texts = [squash(t) for t in schedule_tables if isinstance(t, str)]
    if texts:
        for tb in body.find_all("table"):
            if any(x and x in squash(tb.get_text(" ")) for x in texts):
                tables.append(tb)
    cands = []
    for el, how in _candidates(body):
        if tables and not all(_inside(t, el) for t in tables):
            continue
        if _excluded(el):
            continue
        cands.append((el, how))
    removed = []
    ok = []
    if cands:
        sizes = []
        for el, how in cands:
            tmp = parse(str(el))
            _strip(tmp)
            sizes.append(_text_len(tmp))
        big = max(sizes)
        # いちばん内側で、いちばん大きい候補の 6 割以上の文字を持つもの (ページ全体を包む #main より #contents を選ぶ)
        ok = [(el, how, n) for (el, how), n in zip(cands, sizes) if big and n >= 0.6 * big]
    if ok:
        el, how, _ = max(ok, key=lambda x: len(list(x[0].parents)))
        root, method = el, how
    elif tables:
        root = tables[0]
        for anc in tables[0].parents:
            if anc.name in ("body", "html", "[document]"):
                break
            if any(h for h in anc.find_all(list(HEADINGS)) if not any(_inside(h, t) for t in tables)):
                root = anc
                break
        method = "table_block"
    else:
        root, method = body, "body"
    removed += _strip(root, keep=tables)
    n_bp = 0
    if boilerplate:
        for ln in lines(root):
            el = ln.element
            if ln.hash in boilerplate and el is not None and not getattr(el, "decomposed", False) and el.parent is not None \
                    and el is not root and not any(_inside(el, t) or _inside(t, el) for t in tables) \
                    and squash(el.get_text(" ")) == squash(ln.text) \
                    and not policy_like(ln.text):          # 塊がほかの行を含むもの・方針や但し書きの文は外さない (古い数え上げでも)
                removed.append(ln.selector)
                el.decompose()
                n_bp += 1
    return Region(root, method, removed, n_bp)


# ---- 行と文 ----
class Line:
    """本文の 1 行 (ブロックの要素の文字)。heading_path は上の見出しの並び"""

    def __init__(self, text, heading_path, selector, tag, element=None, is_heading=False, in_table=False):
        self.text, self.heading_path, self.selector, self.tag = text, heading_path, selector, tag
        self.element, self.is_heading, self.in_table = element, is_heading, in_table
        self.hash = sha256_text(squash(text))

    def __repr__(self):
        return f"Line({self.tag} {self.text[:30]!r} @ {' / '.join(self.heading_path)[-40:]})"


def _clean(s):
    s = s.replace("\xa0", " ").replace("　", " ")
    s = re.sub("[​‌‍﻿]", "", s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


BRACKET_HEAD = re.compile(r"^【[^【】]{1,40}】$")


def _strong_only(el, text):
    """<p><strong>2026年度年末年始のごみ収集について</strong></p> のように、太字だけの短い行 (見出しの代わり)"""
    if not isinstance(el, Tag) or el.name not in ("p", "div", "dt") or len(text) > 40:
        return False
    bold = "".join(b.get_text("") for b in el.find_all(["strong", "b"]))
    return bool(bold) and squash(bold) == squash(text)


def lines(root):
    """要素 → Line のリスト (見出し・ブロックの要素で切る。br は行の中の改行)"""
    out, heads, buf = [], [], []

    def flush(el):
        t = _clean("".join(buf))
        buf.clear()
        if not t:
            return
        if BRACKET_HEAD.match(t) or _strong_only(el, t):     # 【可燃物（燃えるごみ）】・太字だけの行は 7 番目の高さの見出し
            while heads and heads[-1][0] >= 7:
                heads.pop()
            out.append(Line(t, [h for _, h in heads], css_path(el), el.name if el else "", el, True,
                            bool(el and el.find_parent("table"))))
            heads.append((7, t))
            return
        out.append(Line(t, [h for _, h in heads], css_path(el) if el else "", el.name if el else "", el, False,
                        bool(el and (el.name in ("td", "th", "caption") or el.find_parent("table")))))

    def walk(node, block):
        for ch in list(node.children):
            if isinstance(ch, Comment):
                continue
            if isinstance(ch, NavigableString):
                buf.append(str(ch))
                continue
            if not isinstance(ch, Tag) or ch.name in SKIP_TAGS:
                continue
            if ch.name == "br":
                buf.append("\n")
            elif ch.name in HEADINGS:
                flush(block)
                t = _clean(ch.get_text(" "))
                lv = HEADINGS[ch.name]
                while heads and heads[-1][0] >= lv:
                    heads.pop()
                if t:
                    out.append(Line(t, [h for _, h in heads], css_path(ch), ch.name, ch, True, bool(ch.find_parent("table"))))
                    heads.append((lv, t))
            elif ch.name == "img":
                alt = (ch.get("alt") or "").strip()
                if alt:
                    buf.append(alt)
            elif ch.name in BLOCKS:
                flush(block)
                walk(ch, ch)
                flush(ch)
            else:
                walk(ch, block)

    walk(root, root)
    flush(root)
    return out


class Sentence:
    """文 (§2.6 の 1: 「。」・改行・箇条書きの 1 項目・「※」で始まる注。かっこ書きも 1 つの文)"""

    def __init__(self, text, line, start, end, parenthetical=False):
        self.text, self.line, self.start, self.end, self.parenthetical = text, line, start, end, parenthetical

    @property
    def heading_path(self):
        return self.line.heading_path

    @property
    def selector(self):
        return self.line.selector

    def span(self, source_id):
        return {"source_id": source_id, "selector": self.line.selector, "start": self.start, "end": self.end,
                "text": self.text, "heading_path": list(self.line.heading_path)}

    def __repr__(self):
        return f"Sentence({self.text[:40]!r})"


PAREN = re.compile(r"（[^（）]{2,80}）|\([^()]{2,80}\)")


def sentences(lns):
    out = []
    for ln in lns:
        if ln.is_heading:
            out.append(Sentence(ln.text, ln, 0, len(ln.text)))
            continue
        t = ln.text
        cur, start = "", 0
        i = 0
        pieces = []
        while i < len(t):
            c = t[i]
            if c == "※" and cur.strip():
                pieces.append((start, i))
                start = i
                cur = ""
            cur += c
            if c in "。\n":
                pieces.append((start, i + (c == "。")))
                start = i + 1
                cur = ""
            i += 1
        if cur.strip():
            pieces.append((start, len(t)))
        for a, b in pieces:
            s = t[a:b].strip()
            if not s:
                continue
            a2 = t.index(s, a)
            out.append(Sentence(s, ln, a2, a2 + len(s)))
            for m in PAREN.finditer(s):
                if m.group(0) != s:
                    out.append(Sentence(m.group(0), ln, a2 + m.start(), a2 + m.end(), parenthetical=True))
    return out


# ---- 方針の文のふるい (§3.2 の 4) ----
def classify_policy(text):
    """文 → "office" (開庁・休館などの文。方針にしない) / "dropoff" (持ち込みの文。mode: dropoff の品目だけ) /
    "policy" (収集の方針になりうる) / "other" (ごみの収集の言葉が無い)"""
    t = nfkc(text)
    if any(w in t for w in OFFICE_WORDS):
        return "office"
    if any(w in t for w in DROPOFF_WORDS):
        return "dropoff"
    if any(w in t for w in GOMI_WORDS):
        return "policy"
    return "other"


def policy_eligible(sentence):
    """方針 (祝日・年末年始・休み) の値に使ってよい文か: 開庁時間・持ち込みの文でなく、文か、すぐ上の見出しがごみの収集を言っている
    (金沢「2026年度年末年始のごみ収集について」の下の「年末 ： 12月29日（火曜日）まで」)"""
    cls = classify_policy(sentence.text)
    if cls == "policy":
        return True
    if cls != "other" or not sentence.heading_path:
        return False
    return bool(GOMI_HEADING.search(nfkc(sentence.heading_path[-1])))


def change_sentences(sents):
    """deferred・caveat の見分け (§2.6) をする文: 本文の範囲の文のすべて → [(文, 分類)]。分類には policy_class
    (classify_policy の値) を付ける。「収集・ごみ…」の言葉は求めない (能美「祝日・振替休日などで、日程が変更になることがありますのでご確認ください」も caveat)。
    開庁時間・持ち込みの言葉がある文も落とさない: 「年末年始のごみ収集日程は、決まり次第ホームページや窓口でお知らせします」は
    「窓口」を含んでも deferred (§2.6 に除く決まりは無く、7 は止める方を採る)。ページの足の開庁時間の文は本文の範囲 (§3.2) で外れる"""
    out = []
    for s in sents:
        c = classify_change(s.text)
        if c:
            out.append((s, {**c, "policy_class": classify_policy(s.text)}))
    return out


STRONG_QUALIFIER_GROUPS = ("count", "range", "change", "negative", "period")


def policy_like(text):
    """収集日の方針・但し書きになりうる文を含むか: deferred・caveat の文 (§2.6。開庁時間・持ち込みの言葉があっても) か、
    ごみの収集の言葉と但し書きの印 (数え方・範囲・休み・否定・期間。「※」「注」だけは数えない) を両方持つ文。
    外す決まり (お問い合わせの欄・使い回しの部分) の安全弁: これに当たる文は外さない (外しすぎは答えない方に倒れない)"""
    for s in sentences([Line(text, [], "", "")]):
        if classify_change(s.text):
            return True
        if classify_policy(s.text) == "policy" and any(g in STRONG_QUALIFIER_GROUPS for g, _ in qualifier_marks(s.text)):
            return True
    return False


def section_ok(sentence, schedule_heading_paths=()):
    """方針の文の場所の確かめ (§3.2 の 5): 日程の表と同じ節 (同じ見出しの下) か、見出しがごみの収集を名指しした節にあるか"""
    hp = list(sentence.heading_path)
    for sp in schedule_heading_paths:
        sp = list(sp)
        if sp and hp[:len(sp)] == sp:
            return True
        if not sp and not hp:
            return True
    return any(GOMI_HEADING.search(nfkc(h)) for h in hp)


# ---- deferred と caveat (§2.6) ----
def period_names(text):
    """文の中の期間の名前 (年末年始・お盆など、祭典の名前も)。祝日・祭日・振替休日・休日は入れない"""
    t = nfkc(text)
    found, rest = [], t
    for p in PERIOD_NAMES:
        if p in rest:
            name = PERIOD_CANON.get(p, p)
            if name not in found:
                found.append(name)
            rest = rest.replace(p, "□")
    for m in FESTIVAL.finditer(rest):
        if m.group(0) not in found:
            found.append(m.group(0))
    return found


DATE_RANGE = re.compile(r"(\d{1,2})[/月](\d{1,2})日?(?:\([^)]{1,3}\))?\s*(?:[~〜～\-ー－]|から)\s*(?:(\d{1,2})[/月])?(\d{1,2})日?")


def classify_change(text):
    """文 → None か {"kind": "deferred" | "caveat", "periods": [...], "markers": [...], "dates": [(月, 日), …] か None,
    "mentions_holiday": bool}。期間の名前と印の両方があれば deferred、印だけなら caveat"""
    t = nfkc(text)
    markers = [m for m in MARKERS_MAY + MARKERS_LATER if m in t]
    if not markers:
        return None
    periods = period_names(t)
    hol = any(w in t for w in HOLIDAY_KINDS_WORDS)
    if not periods:
        # 期間の名前が無い: 印と、収集日の変わることを言う言葉の両方があれば caveat
        # (「区報…でお知らせします」だけの文は、前の文の続き (context) で、それ自体は注意にしない)
        # 「表示されない場合があります」「引き取りできる場合があります」のような、収集日の変わりうることを言わない文は注意にしない。
        # 注意にしなかった文に「変更」などの印があれば、但し書きの洗い出し (§3.8) が拾うので、止める方に倒れる
        if not any(w in t for w in CHANGE_WORDS):
            return None
        return {"kind": "caveat", "periods": [], "markers": markers, "dates": None, "mentions_holiday": hol}
    ds = []
    for m in DATE_RANGE.finditer(t):
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2 = int(m.group(3)) if m.group(3) else m1
        d2 = int(m.group(4))
        ds += [(m1, d1), (m2, d2)]
    return {"kind": "deferred", "periods": periods, "markers": markers, "dates": ds or None, "mentions_holiday": hol}


# ---- deferred・caveat の文を使ってよいか: 閉じた見本 (主題 + 含みの句) ----
# deferred・caveat の例外は (止める・注意の向きなので) いつも作るが、文を「使った」と数えるのは、文の全体が
# 「閉じた主題」+「閉じた含みの句 (場合があります・決まり次第お知らせします…)」にまるごと当たるときだけ。主題に日付・数字・
# 休み・打ち消し・第N があれば使わない (「8月14日は休み、ほかの日も変更になる場合があります」の 8/14 を caveat に飲み込ませない)。
# 使わなかった文は使われなかった但し書きとして止める
_CH_PERIOD = r"(?:年末年始|年末|年始|お盆休み|お盆|盆休み|ゴールデンウィーク|GW|大型連休|連休|お正月|正月|祝日|祭日|振替休日|祝休日|休日|国民の休日)"
_CH_PERIODS = _CH_PERIOD + r"(?:(?:・|、|及び|および|や|と)" + _CH_PERIOD + r")*(?:等|など)?"
_CH_WX1 = r"(?:台風|悪天候|荒天|大雨|大雪|降雪|積雪|天候|災害|道路状況|交通事情)"
_CH_WEATHER = _CH_WX1 + r"(?:(?:・|、|や)" + _CH_WX1 + r")*(?:等|など)?"
_CH_TOPIC = r"(?:ごみの|資源の|ごみ・資源の|資源・ごみの)?(?:収集日程|収集日|収集曜日|収集時間|日程|ごみ収集日程|ごみ収集日|収集)"
_CH_YEAR = r"(?:(?:令和|平成)\d{1,2}年度?|\d{4}年度?)の?"
_CH_BY = r"(?:により|で|の影響で|の影響により|の関係で)、?"
CHANGE_SUBJECTS = (
    ("none", re.compile(r"")),                                                                # 変更になる場合があります
    ("topic", re.compile(_CH_TOPIC + r"(?:は|が)、?")),                                        # ごみの収集日は…
    # 年末年始は… / 年末年始の収集日は… / 令和8年年末年始のごみ・資源収集に関する内容は、… (足立)
    ("period", re.compile(r"(?:" + _CH_YEAR + r")?" + _CH_PERIODS + r"(?:の期間|期間中?|中)?(?:は|の" + _CH_TOPIC +
                          r"(?:は|が|については|につきましては)|については|につきましては|の(?:ごみ・資源収集|資源・ごみ収集|ごみ収集|収集)に"
                          r"(?:関する|ついての)(?:内容|情報|お知らせ)は)、?")),
    ("topic_by_period", re.compile(_CH_TOPIC + r"(?:は|が)、?" + _CH_PERIODS + _CH_BY + r"(?:一部)?")),   # 収集日は、祝日・お盆休み・年末年始等により、一部
    ("period_by", re.compile(_CH_PERIODS + _CH_BY + r"(?:" + _CH_TOPIC + r"(?:は|が)、?)?(?:一部)?")),    # 祝日・振替休日などで、日程が
    ("weather", re.compile(_CH_WEATHER + r"(?:の影響)?(?:により|で|の場合は?|時は)、?(?:" + _CH_TOPIC + r"(?:は|が)、?)?")),  # 台風等の影響により、
)
_CH_MEDIA_W = r"(?:広報(?:紙|誌)?|区報|市報|町報|村報|ニュース|ホームページ|HP|回覧|窓口|チラシ|カレンダー|張り紙|貼り紙|掲示|看板|アプリ|SNS|メール|ウェブサイト)"
# 媒体の名前の前に付けてよいのは、自治体の名前・「区の」「公式」「集積所看板への」と「12月の」「3月号の」だけ (閉じた形)
_CH_MEDIA_PRE = (r"(?:\d{1,2}月(?:号)?の)?(?:(?:[一-龥]{1,4}|[ぁ-ん]{2,5})(?:市|区|町|村)の?|[ぁ-ん]{2,5}|(?:市|区|町|村)の)?"
                 r"(?:公式)?(?:集積所(?:看板)?への)?")
_CH_MEDIA1 = _CH_MEDIA_PRE + _CH_MEDIA_W
_CH_MEDIA = _CH_MEDIA1 + r"(?:(?:、|・|や|及び|および|と|または|又は)" + _CH_MEDIA1 + r")*(?:等|など)?"
CHANGE_HEDGES = (
    # 変更になる場合があります / 収集日程、時間が異なることがあります / 通常の周期と異なることがございます (川口)
    ("may_change", re.compile(r"(?:" + _CH_TOPIC + r"(?:、時間)?(?:が|は)、?)?(?:一部)?"
                              r"(?:変更(?:に|と)?なる|変更される|変更する|変わる|異なる|通常の周期と異なる)(?:場合|こと)が(?:あります|ございます)"
                              r"(?:ので、?(?:ご(?:確認|注意|了承)ください))?"), None),
    # 収集を休んだり、変更することがあります (渋谷) / 収集を休止することがあります
    ("may_rest", re.compile(r"(?:(?:ごみの?)?(?:収集|回収)を)?(?:休んだり、?変更する|休止する|中止する|お休みする|休む)(?:場合|こと)が(?:あります|ございます)"),
     None),
    # 決まり次第、ホームページや窓口でお知らせします / 改めて板橋区ホームページ、区報、集積所看板への張り紙でお知らせいたします
    # (期間を名指しした主題だけ。期間の無い「日程が決まり次第お知らせします」は日程そのものが決まっていない → 使わない)
    ("notice", re.compile(r"(?:決まり次第|後日|改めて|別途)?、?" + _CH_MEDIA + r"(?:で|にて)(?:お知らせ|おしらせ|ご案内|周知)(?:します|いたします|する予定です)"),
     ("period", "topic_by_period", "period_by")),
    ("changed_mark", re.compile(r"変更あり"), ("period",)),                                    # （年末年始は変更あり）(渋谷)
)
_CH_LEAD = re.compile(r"^(?:[※*・●○◆◇■□☆★◎•]|注意|注釈|注記\d*|\(注\d*\)|\(補足\)|注\d*)+[:：]?")
_CH_BAD = re.compile(r"\d|第|休|ません|ない|なし|中止|休止|除|以外|のみ|だけ")


def _ch_norm(text):
    """deferred・caveat の見本と比べる前の形: NFKC、空白を除く、読点を「、」に、頭の注・箇条書きの印、文末の「。」、文全体のかっこ"""
    t = re.sub(r"[,，､]", "、", squash(text))
    for _ in range(2):
        t = _CH_LEAD.sub("", t)
        t = re.sub(r"。$", "", t)
        m = re.fullmatch(r"\(([^()]*)\)", t)
        if m:
            t = m.group(1)
    return t


def change_template(text):
    """deferred・caveat の文 → 見本の名前 ("<主題>+<含み>") か None。文の全体が閉じた主題 + 閉じた含みの句にまるごと当たり、
    主題 (年度の「令和8年」を除く) に日付・数字・休み・打ち消し・第N・除く・のみ が無いときだけ"""
    t = _ch_norm(text)
    for hname, hrx, subjects in CHANGE_HEDGES:
        for i in range(len(t) + 1):
            if not hrx.fullmatch(t, i):
                continue
            subj = t[:i]
            if _CH_BAD.search(re.sub(_CH_PERIOD, "", re.sub(_CH_YEAR, "", subj))):
                continue
            for sname, srx in CHANGE_SUBJECTS:
                if (subjects is None or sname in subjects) and srx.fullmatch(subj):
                    return f"{sname}+{hname}"
    return None


def change_templates():
    """deferred・caveat の見本の名前の閉じた並び (関門 consume の種類 change)"""
    return {f"{s}+{h}" for h, _, subjects in CHANGE_HEDGES for s, _ in CHANGE_SUBJECTS if subjects is None or s in subjects}


# ---- 但し書きの洗い出し (§3.8) ----
# 7 回目: 月・回数・出し方を限る言い方 (印の無い「不燃ごみは奇数月です」「隔月の収集です」「年6回です」「2か月に1回」「3か月ごと」
# 「予約制です」「申込制になりました」が落ちていた)。数え方 (count) は月・回数を限る (policy_like の安全弁にも数える)。
# 出し方 (mode: 予約・申し込み・事前) は品目の出し方を変える言い方。安全弁には数えない (サイトの共通の欄の「粗大ごみは事前に申し込み」を
# 本文に残して、すべての系列を止めないため)。但し書きの洗い出しには数える
_QN = r"[\d一二三四五六七八九十]+"                       # 数 (漢数字も)
QUALIFIER_MARK_RX = (
    ("count", "奇数月・偶数月・隔月", re.compile(r"奇数月|偶数月|隔月|奇数週|偶数週|奇数日|偶数日|隔年")),
    ("count", "Nか月", re.compile(_QN + r"\s*[かヵヶケカ箇個]\s*月")),
    ("count", "年N回", re.compile(r"[年月週]\s*に?\s*" + _QN + r"\s*回(?!目)|" + _QN + r"\s*回\s*[/／]\s*[年月週]|年間\s*" + _QN + r"\s*回|"
                                 r"年度\s*(?:内|中)?\s*に?\s*" + _QN + r"\s*回|" + _QN + r"\s*(?:週間?|日|年|[かヵヶケカ箇個]\s*月)\s*に\s*" +
                                 _QN + r"\s*回|" + _QN + r"\s*回(?:程度|ほど|ずつ|のみ|だけ|まで|限り|の?収集|の?回収)")),
    ("count", "ごと・おき", re.compile(r"(?:" + _QN + r"\s*(?:[かヵヶケカ箇個]\s*月|週間?|日|年)|毎?週|毎?月|隔週|年|日)\s*(?:ごと|おき)")),
    ("range", "春・秋", re.compile(r"春[季期]|秋[季期]|春\s*(?:と|・|、|及び)\s*秋|秋\s*(?:と|・|、|及び)\s*春|春秋")),
    ("mode", "予約・申込・事前", re.compile(r"予約|申込|申し込|事前")),
)


def qualifier_marks(text):
    """文の中の但し書きの印 → [(組, 印)]"""
    t = nfkc(text)
    out = [(g, m) for g, ms in QUALIFIER_MARKS.items() for m in ms if m in t]
    out += [(g, name) for g, name, rx in QUALIFIER_MARK_RX if rx.search(t)]
    if MONTH_SPAN_MARK.search(t) or re.search(r"\d{1,2}月から", t):
        out.append(("range", "N月～N月"))
    return out


def _nm_norm(text):
    """月の見本と比べる前の形: NFKC、空白 (ゼロ幅を含む) を除く、読点 (「，」「,」「､」) を「、」に、頭の注・箇条書きの印
    (「※」「*」「・」「●」「☆」など) と文末の「。」1 つを除く。ほかは寄せない"""
    t = re.sub(r"[,，､]", "、", squash(text))
    return re.sub(r"。$", "", re.sub(r"^[※*・●○◆◇■□☆★◎]+", "", t))


def _months_of(subj):
    """見本に当たった月の並び・範囲の文字 → 月の集まり (範囲は年をまたいで広げる。「11月〜2月」→ 11・12・1・2)"""
    out = set()
    for name, ms in PERIOD_MONTHS.items():
        if name in subj:
            out |= set(ms)
            subj = subj.replace(name, "□")
    subj = re.sub(_NM_YE, "", subj)
    for m in re.finditer(r"(\d{1,2})月?(?:[~〜\-]|から|より)(\d{1,2})月", subj):
        a, b = int(m.group(1)), int(m.group(2))
        x = a
        while True:
            out.add(x)
            if x == b:
                break
            x = x % 12 + 1
    subj = re.sub(r"(\d{1,2})月?(?:[~〜\-]|から|より)(\d{1,2})月", "□", subj)
    for m in re.finditer(r"\d{1,2}(?:" + _NM_SEP + r"\d{1,2})*(?=月)", subj):
        out |= {int(x) for x in re.findall(r"\d{1,2}", m.group(0))}
    return out


def named_months(text):
    """但し書きの文が名指しした月 → 月の集まり (空 = 月を名指ししていない → §3.8 の 4 で系列ごと止める)。
    文の全体が MONTH_TEMPLATES の見本 (閉じた月の並び・範囲 + 決まった述語) の 1 つにまるごと当たるときだけ月を返す:
    「8月～10月は休み」→ 8・9・10、「1月は第2月曜日」→ 1、「※年末及び1月は収集日程、時間が異なります。」(三鷹) → 12・1、
    「夏（7月～9月）は隔週」→ 7・8・9。期間の名前は 年末 → 12、年始・正月 → 1、年末年始 → 12・1、お盆 → 7・8、
    ゴールデンウィーク → 4・5。日付 (「12月31日」) は月の名指しにしない。
    見本に当たらない文 (「5・6月のみ」「6・9・12・3月の第4木曜日」(その月だけかもしれない)・「10月から」「9月末日まで」
    「4月～翌年3月」「8月以外」「その他の月は」・述語の無い「11月〜2月」) と、月をまたぐ但し書き (祝日・休日・第5週。MONTH_WIDE)
    を言う文は空"""
    months, _ = named_months_template(text)
    return months


def named_months_template(text):
    """named_months と、当たった見本の名前 → (月の集まり, "named_months:<見本>" か None)。月を返すのは関門 (consume.take) を
    通ったときだけ (月を名指しした但し書きは、その月のほかを使ったことになるので)"""
    raw = nfkc(text)
    if MONTH_WIDE.search(raw):
        return set(), None
    t = _nm_norm(text)
    for name, rx in MONTH_TEMPLATES:
        m = rx.fullmatch(t)
        if m:
            subj = m.group("subj")
            if name == "changes_period" and re.search(r"\d", subj):
                return set(), None
            tag = consume.take("named_months", name, text)
            if not tag:
                return set(), None
            return {x for x in _months_of(subj) if 1 <= x <= 12}, tag
    return set(), None


# 印 (QUALIFIER_MARKS) の無い文でも但し書きにするもの (default-deny。使われなければ止める):
#   日付と収集の言葉の両方がある文 (「東地区の収集は12月28日までです」。読めない言い方でも、その日の答えを変えうる)、
#   期間の名前 (ゴールデンウィーク・祭典) と収集の言葉の両方がある文、
#   行の折り返しの続き (「祝日も収集\nできません。」の「できません。」。前の文の打ち消しが消えないように)、
#   読み取りが値を返したのに関門を通らなかった文 (consume.left の unconsumed_reading。見本の外・品目の決まらない文)、
#   日程の節の文で、地区・場所・曜日・前後の日・日付を言うか、続きの言葉で始まる文 (schedule_hints)
_HINT_DATE = re.compile(r"\d{1,2}月\d{1,2}日|(?<![\d/])\d{1,2}/\d{1,2}(?![\d/])")
_HINT_CTX = re.compile(r"まで|から|より|最終|休|収集|回収|出せ|出し|中止|なし|ありません|しません|行いません|変更|臨時|振替|特別")
_CONTINUATION = re.compile(r"^(?:でき(?:ま|な|か)|いたしかね|いたしませ|しませ|しな[いく]|ませ|ない|なく|せず|られ|なりませ|ではあ|"
                           r"じゃな|となりま|になりま|とは(?:なり|限り))")
# 日程の節 (policy_eligible の文。見出しの行は除く) で、印が無くても但し書きにする言葉 (default-deny: 読み取りが使わなかった文が
# 地区・場所、曜日、前後の日、日付を言うか、前の文の続きの言葉で始まれば、その日の答えを変えうる。黙って落とさない):
#   地区・場所 (「東町は翌日に収集します」「北部は…」)、曜日 (「東町の燃やすごみは火曜日と金曜日です」)、
#   前後の日 (「その翌日に収集します」)、日付 (「8月17日は…」。言い方によらない)、続きの頭 (「が、東町は翌日になります」「ただし、…」)
_HINT_PLACE = re.compile(r"地区|地域|区域|丁目|番地|ブロック|エリア|コース|班|自治会|町内会|町会|集合住宅|マンション|アパート|団地|一部|"
                         r"山間|島しょ|離島|お住ま?い|[東西南北]部|[一-龥々ヶァ-ヺ]{1,5}(?:町(?![民長役外]|村)|村(?![民長役外])|"
                         r"区(?![民長役内報域分別画切間]))")
_HINT_NOT_PLACE = re.compile(r"市区町村|市町村|都道府県|区市町村|(?:地区|地域|区域|町名|町丁)(?:別|ごと)")    # 「地区別ごみ収集日程表」(地区ごとの一覧の題)
_HINT_WEEKDAY = re.compile(r"[月火水木金土日]曜")
_HINT_RELDAY = re.compile(r"翌日|前日|その日|翌週|前週|当日|翌々日|次の日|翌平日|前の平日|次の平日|同じ曜日")
_HINT_ASOF = re.compile(r"\d{1,2}月\d{1,2}日(?:\([^()]{1,4}\))?(?:現在|時点)")                            # 「1月1日現在の情報」(日程でない日付)
# 収集・回収の動き (動詞の形。「収集日」「収集カレンダー」の名詞は入れない) と「出せる・出せない」。日程の節の文が、使われずにこれを
# 言えば但し書き (「収集を延期します」「収集は終了しました」「収集を再開します」「集積所に出せなくなりました」: 止める・変える言い方は
# 閉じた並びにできないので、黙って落とさない)。出さない言い方 (「8月は出さないでください」) も。出し方の指図 (「袋に入れて出してください」
# 「朝8時までに出してください」) は入れない
_HINT_VERB = re.compile(r"(?:収集|回収)(?:し|す|さ|せ|でき|いた|致|を|は|が|も|の(?:停止|廃止|中止|休止|中断|延期|見合わせ|取りやめ|再開|開始|終了))|"
                        r"出せ|出さ|出しては|集め(?:ま|な|ら|て)|受け?付け(?:てい|てお|ませ|な)|持ち去")
CONT_LEAD = re.compile(r"^(?:[※*・●○◆◇■□☆★◎]\s*)*(?:が[、,]|けれど|けど|だが|ですが|でも[、,]|しかし|ただし|但し|なお|また[、,]|さらに)")
# 7 回目: 月の並び (「4月、6月、8月、10月、12月、2月です」。日付「4月1日」は日付のわけ)、月・回数と一緒の「出して」
# (「偶数月に出してください」「8月に出してください」)、曜日の略 (「燃やすごみの日は月・木です」)
_HINT_MONTHS = re.compile(r"\d{1,2}月(?!\d)\s*(?:[、,，・]|及び|および|と)\s*\d{1,2}月(?!\d)|"
                          r"(?<![\d/])\d{1,2}(?:\s*[・、,，]\s*\d{1,2})+\s*月(?!\d)")
_HINT_PUT_WHEN = re.compile(r"\d{1,2}月(?!\d{1,2}日)|毎週|隔週|毎月|奇数|偶数|隔月|第\s*\d|[年月週]\s*に?\s*\d+\s*回|\d+\s*[かヵヶケカ箇個]\s*月")
_HINT_MONTH1 = re.compile(r"(?<!\d年)(?<![\d/])\d{1,2}月(?![\d日])")              # 「2026年4月」「令和8年4月」(時期) は除く
_HINT_WD_ABBR = re.compile(r"(?<!\d)[月火水木金土日]\s*[・、,]\s*[月火水木金土日](?!\d)")


def schedule_hints(sentence, own_names=()):
    """日程の節の文 (見出しでない policy_eligible の文) の、印の無い但し書きのわけ → [("hint", わけ)]。
    own_names (自治体そのものの名前「世田谷区」) は地区・場所に数えない"""
    if sentence.line.is_heading or not policy_eligible(sentence):
        return []
    t = nfkc(sentence.text)
    out = []
    tp = _HINT_NOT_PLACE.sub("", t)
    for n in own_names:
        tp = tp.replace(nfkc(n), "") if n else tp
    if _HINT_PLACE.search(tp):
        out.append(("hint", "地区・場所"))
    if _HINT_WEEKDAY.search(t):
        out.append(("hint", "曜日"))
    if _HINT_RELDAY.search(t):
        out.append(("hint", "前後の日"))
    if _HINT_DATE.search(_HINT_ASOF.sub("", t)) and not UPDATED.search(t):
        out.append(("hint", "日付"))
    if CONT_LEAD.match(squash(t)):
        out.append(("hint", "続きの頭"))
    if _HINT_VERB.search(t):
        out.append(("hint", "収集の動き"))
    if _HINT_MONTHS.search(t):
        out.append(("hint", "月の並び"))
    elif _HINT_MONTH1.search(_HINT_ASOF.sub("", t)) and not UPDATED.search(t):
        out.append(("hint", "月"))                     # 「不燃ごみは5月です」(日付でない月の名指し)
    if "出して" in t and _HINT_PUT_WHEN.search(t):
        out.append(("hint", "出す月・回数"))
    if _HINT_WD_ABBR.search(t):
        out.append(("hint", "曜日"))
    return out


def qualifier_hints(sentence, own_names=()):
    """印の無い文を但し書きにするわけ → [("hint", わけ)] (上に書いたもの)"""
    t = nfkc(sentence.text)
    out = []
    if _HINT_DATE.search(t) and _HINT_CTX.search(t) and not UPDATED.search(t):
        out.append(("hint", "日付"))
    if period_names(t) and _HINT_CTX.search(t):
        out.append(("hint", "期間"))                    # 「東地区はゴールデンウィーク中も収集します」(GW は印の言葉に無い)
    if _CONTINUATION.match(squash(t)):
        out.append(("hint", "続き"))
    why = getattr(sentence, "unconsumed_reading", None)
    if why and not getattr(sentence, "consumed", None):
        out.append(("hint", f"読めたが使えない:{why}"))
    return out + [h for h in schedule_hints(sentence, own_names) if h not in out]


def scan_qualifiers(sents, own_names=()):
    """本文の範囲の文のうち、但し書きの印を含むもの (と、印が無くても qualifier_hints に当たるもの) →
    [{"qualifier_id", "text", "marks", "selector", "heading_path", "months"}]。own_names は自治体の名前 (地区・場所に数えない)"""
    out = []
    for s in sents:
        if s.parenthetical:
            continue
        marks = qualifier_marks(s.text) or qualifier_hints(s, own_names)
        if marks:
            h = sha256_text(squash(s.text))[:10]
            out.append({"qualifier_id": f"q{h}", "text": s.text, "marks": marks, "selector": s.selector,
                        "heading_path": list(s.heading_path), "months": sorted(named_months(s.text)), "sentence": s})
    return out


# ---- 中身の sha256 (見張り。§5.8) ----
def content_sha256(html_text, boilerplate=None):
    """本文の範囲の文字を正規化した sha256 (更新日・閲覧数の行は除く)。HTML のバイト列が変わっても中身が同じなら同じ"""
    reg = content_region(html_text, boilerplate=boilerplate)
    keep = [squash(l.text) for l in reg.lines() if not UPDATED.search(nfkc(l.text))]
    return sha256_text("\n".join(x for x in keep if x))


def block_hashes(html_text):
    """使い回しの部分を数えるための、本文の候補 (body から頭・足を外さない) の塊の sha256 の集まり (8 字以上)。
    方針・但し書きの文を持つ塊 (policy_like) は数えない: 地区ごとのページに同じ文で載る「祝日も収集します。（年末年始を除く）」
    「年末及び1月は収集日程、時間が異なることがあります。」(三鷹) を使い回しとして外さないため"""
    soup = parse(html_text)
    out = set()
    for ln in lines(soup.body or soup):
        if len(squash(ln.text)) >= 8 and not policy_like(ln.text):
            out.add(ln.hash)
    return out


def region_hashes(html_text):
    """関係の有る無しを決めるための、本文の範囲 (§3.2 の 1・2。使い回しの部分はまだ外さない) の行のうち、
    リンクの文字が半分に満たない行 (中身の行。新着情報・関連ページのリンクの並びを除く) の sha256 の集まり (8 字以上)"""
    out = set()
    for ln in content_region(html_text).lines():
        if len(squash(ln.text)) < 8 or ln.element is None:
            continue
        full = len(squash(ln.element.get_text(" "))) or 1
        if len(squash(text_outside_links(ln.element))) >= 0.5 * full:
            out.add(ln.hash)
    return out


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 1.0


def _containment(a, b):
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def boilerplate_hashes(pages, min_pages=3, related=0.15, regions=None, related_region=0.4, frame_share=0.5):
    """同じホストのページ {名前: 塊の sha256 の集まり} → 中身の関係の無いページ min_pages 以上に出る塊の sha256 の集まり。
    2 つのページは、次のどちらかなら関係あり (印刷用・別の地区の同じ形のページ):
      - 本文の範囲の中身の行 (regions。region_hashes) の重なりが、少ない方のページの related_region 以上
      - サイトの枠 (ホストのページの frame_share 以上に出る塊。頭・足・メニュー) を除いた塊の集まりの Jaccard が related 以上
    ページ全体の塊で比べると、どのページも枠の分だけ似て、同じ形の地区のページ (三鷹の 8 地区: 0.48〜0.57) と関係の無い
    ページの差が消える (三鷹の地区の方針の文が使い回しとして外れていた)。枠を除くだけだと、ホストのページの半分以上が
    地区のページなら、地区に共通の中身 (「第1とは1～7日…」・規則の行) まで枠に入り、地区のページどうしが関係なしに見える。
    本文の範囲の中身の行の重なりは、三鷹の地区のページどうしで 0.73〜0.94、関係の無いページとは 0.00〜0.33。
    どちらかで関係ありとすれば、外す塊は少ない方 (答えない方) に寄る"""
    names = list(pages)
    regions = {k: set(v) for k, v in (regions or {}).items()}
    by_hash = {}
    for n in names:
        for h in pages[n]:
            by_hash.setdefault(h, []).append(n)
    frame = {h for h, ns in by_hash.items() if len(ns) >= max(min_pages, frame_share * len(names))}
    own = {n: set(pages[n]) - frame for n in names}

    memo = {}

    def is_related(a, b):
        k = (a, b) if a <= b else (b, a)
        if k not in memo:
            by_region = a in regions and b in regions and _containment(regions[a], regions[b]) >= related_region
            memo[k] = by_region or _jaccard(own[a], own[b]) >= related
        return memo[k]

    out = set()
    for h, ns in by_hash.items():
        if len(ns) < min_pages:
            continue
        chosen = []
        for n in ns:
            if not any(is_related(n, c) for c in chosen):
                chosen.append(n)
            if len(chosen) >= min_pages:
                out.add(h)
                break
    return out


def main(argv=None):
    import argparse, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import decode
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="本文の範囲と方針の文の分け方を表示する")
    ap.add_argument("html")
    a = ap.parse_args(argv)
    d = decode.decode(pathlib.Path(a.html).read_bytes(), "text/html")
    reg = content_region(d.text)
    print(reg, "外した:", reg.removed[:20])
    for s in reg.sentences():
        cls = classify_policy(s.text)
        ch = classify_change(s.text)
        q = qualifier_marks(s.text)
        print(f"[{cls}{'/' + ch['kind'] if ch else ''}{' q' if q else ''}] {' / '.join(s.heading_path)[-30:]} :: {s.text[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
