"""HTML の本文の範囲と、使い回しの塊 (資料の読み込み。prep.py・fetch_docs.py が使う)

本文の範囲は、ページの形だけで決める: main・article・role="main"・CMS の本文の id を候補にし、いちばん内側で大きいものを選ぶ。
頭・足・横・メニュー・パンくずなど (タグ・role・id・class の名前・隠した要素) を外し、同じホストの関係の無いページ 3 つ以上に
同じ形で出る塊 (使い回しの塊。sha256 で比べる) を外す。文の意味は見ない (どの文が祝日の扱いか・但し書きかを決めるのは LLM)。
"""
import hashlib, re, unicodedata, warnings

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning

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
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BLOCKS = {"p", "div", "li", "dt", "dd", "td", "th", "caption", "tr", "table", "section", "article", "main", "blockquote",
          "pre", "ul", "ol", "dl", "figure", "figcaption", "address", "center", "body", "html"}


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def squash(s):
    """比べるための形: NFKC、空白 (ゼロ幅を含む) を除く"""
    return re.sub(r"[\s​‌‍﻿]+", "", nfkc(s))


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def clean(s):
    """空白をそろえる (行の中の空白は 1 つ、改行の前後の空白と空の行を除く)"""
    s = s.replace("\xa0", " ").replace("　", " ")
    s = re.sub("[​‌‍﻿]", "", s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


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


def text_outside_links(el):
    """リンクの文字を除いた文字"""
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


def _strip(root):
    """root の中の外すもの (スクリプト、頭・足・横など) を取り除く → 外したものの道すじ"""
    removed = []
    total = _text_len(root)
    for el in list(root.find_all(True)):
        if getattr(el, "decomposed", False) or el.parent is None:
            continue
        if el.name in SKIP_TAGS or (_excluded(el) and _text_len(el) <= 0.5 * total):
            # ページ全体を包む要素 (ASP.NET の <form>、Nuxt の div#pageTop) は名前が当たっても外さない
            removed.append(css_path(el))
            el.decompose()
    for c in root.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    return removed


class Region:
    """本文の範囲: root (要素)、method (main / article / role_main / cms:#id / body)、removed (外したものの道すじ)"""

    def __init__(self, root, method, removed, boilerplate_removed=0):
        self.root, self.method, self.removed, self.boilerplate_removed = root, method, removed, boilerplate_removed

    def lines(self):
        return lines(self.root)

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


def content_region(html_or_soup, boilerplate=None):
    """HTML → Region。boilerplate (使い回しの塊の sha256 の集まり。fetch_docs.load_boilerplate) があれば、その塊を外す"""
    soup = parse(html_or_soup) if isinstance(html_or_soup, (str, bytes)) else html_or_soup
    body = soup.body or soup
    cands = [(el, how) for el, how in _candidates(body) if not _excluded(el)]
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
        root, method, _ = max(ok, key=lambda x: len(list(x[0].parents)))
    else:
        root, method = body, "body"
    removed = _strip(root)
    n_bp = 0
    if boilerplate:
        for ln in lines(root):
            el = ln.element
            # 塊がほかの行を含むもの (塊の文字と行の文字が違うもの) は外さない
            if ln.hash in boilerplate and el is not None and not getattr(el, "decomposed", False) and el.parent is not None \
                    and el is not root and squash(el.get_text(" ")) == squash(ln.text):
                removed.append(ln.selector)
                el.decompose()
                n_bp += 1
    return Region(root, method, removed, n_bp)


# ---- 行 ----
class Line:
    """本文の 1 行 (ブロックの要素か見出しの文字)。hash は使い回しの塊を比べる sha256"""

    def __init__(self, text, selector, element=None):
        self.text, self.selector, self.element = text, selector, element
        self.hash = sha256_text(squash(text))

    def __repr__(self):
        return f"Line({self.text[:30]!r})"


def lines(root):
    """要素 → Line のリスト (見出し・ブロックの要素で切る。br は行の中の改行)"""
    out, buf = [], []

    def flush(el):
        t = clean("".join(buf))
        buf.clear()
        if t:
            out.append(Line(t, css_path(el), el))

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
                t = clean(ch.get_text(" "))
                if t:
                    out.append(Line(t, css_path(ch), ch))
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


# ---- 使い回しの塊 (fetch_docs.py が data/readers/boilerplate/ に書く) ----
def block_hashes(html_text):
    """使い回しの部分を数えるための、body の塊の sha256 の集まり (8 字以上。頭・足も外さずに数える)"""
    soup = parse(html_text)
    return {ln.hash for ln in lines(soup.body or soup) if len(squash(ln.text)) >= 8}


def region_hashes(html_text):
    """ページどうしの関係の有る無しを決めるための、本文の範囲 (使い回しの塊はまだ外さない) の行のうち、
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
    同じ形の地区のページ (三鷹の 8 地区) どうしは関係ありになるので、地区に共通の文は使い回しとして外れない。
    どちらかで関係ありとすれば、外す塊は少ない方に寄る (LLM に渡す文字が多い方に倒す)"""
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
