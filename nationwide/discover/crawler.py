"""1 つの公式サイトの中から、家庭ごみの収集のページと資料を探す (検索エンジンは使わない)

  1. 公式サイトのトップを取る (転送されたら転送先のホストもサイトの内とする)。サイトの内 = 同じホストか、
     www を外した名前の下のホスト (www4.city.kanazawa.lg.jp → city.kanazawa.lg.jp の下はみな内)。
     トップにつながらなければ、https と http・www の有る無しを入れ替えて試す (start_attempts に残す)。
     トップが障害の臨時ページ (「臨時案内」「システム障害」で、サイトの内へのリンクがほとんど無い) なら unreachable とする
  2. robots.txt の Sitemap: か /sitemap.xml (サイトマップの索引も 5 つまで) を読み、URL にごみの言葉があるものを候補に入れる
  3. 同じサイトのリンクを、深さ 3 まで・1 サイト 150 件まで、手がかりの強い順にたどる
     (リンクの文字や URL に ごみ・ゴミ・収集・分別・資源・リサイクル・環境・清掃・カレンダー・gomi・kankyo・recycle・
     seikatsu・kurashi などがあるものを先に。手がかりの無いリンクはトップからのものだけ、最後に)。
     手がかりのとても強いリンク (点 5 以上、または収集日・カレンダーの言葉で点 4 以上) は、深さ 5 までたどる
     (組織の木の深い所にある「ごみ収集カレンダー」のページなど)
  4. PDF・Excel・CSV・iCalendar は、ごみの言葉があるか、ごみのページからのリンクなら取る (ページの 1 つ先まで。1 サイト 25 件まで)
  5. アプリ・業者 (5374・さんあ～る・ごみスケ・スマートごみアプリなど) やアプリストアへのリンク、
     業者のホストから読み込むスクリプト (埋め込みのアプリ) は記録だけして、取りに行かない
  6. サイトの検索が素直な GET のフォームなら「ごみ 収集日」で 1 回だけ検索する
  7. ほかのサイト: 公の団体 (一部事務組合・広域連合・清掃組合・衛生組合・環境組合、市町村でない lg.jp、都道府県) へのリンクで、
     リンクの文字か URL にごみの言葉があるものは「関係する発行元」(related) として、別の上限 (related_budget、既定 15) で、
     同じ礼儀でたどる (入口から 3 段まで。2 段目からはごみの手がかりのとても強いリンクだけ)。そのほかの .jp のサイトは、
     文字がはっきりごみのものだけ 1 ページ取り、そこから資料だけを見る。業者・アプリのホストへは行かない。
     関係する発行元は、自分のサイトを探し終えてから取る (自分のサイトの探し方は変えない)
収集日の資料らしいものが見つかったら、手がかりの薄いリンクは追わず、そこから 60 件までで止める。
問い合わせ (?…) つきの URL: 日付で切り替わるもの (?ym=202610 など、イベントカレンダーのわな) は同じ形で 3 つまで
(ごみの手がかりのあるものは 14 まで)。ほかの形 (?page_id=5、detail.aspx?c_id=10&id=77 など) は、手がかりのあるものは
いくつでも、無いものは同じ形で 60 まで。形 = ホスト・パス・問い合わせの名前の組。
収集日のページ (calendar・districts) は、リンクしている資料の一覧 (documents) を持ち、その資料に linked_from_schedule を付ける。
案内のページ (hub: ごみのページ・資料へのリンクが多く、自分には収集日の中身が無い) で、収集日・カレンダー・地区の言葉のあるものは、
その子 (収集日・地区の言葉のリンク、資料、同じフォルダの地区らしいページ) を別の上限 (hub_budget、既定 40。資料は 15 まで) で、
ほかより先に取る (深さの上限を 2 つまで超えてよい)。「見つかったら 60 件で止める」は、その子を取り終えてから数える。
案内のページの子は、問い合わせの形 (kana.html?kana=… など) ごとに 3 つまで。上限に届いたら子を増やさない。
資料の上限 (15) を超えた子の資料は、ふつうの列 (max_docs の内) に渡す。子の深さは、知っているいちばん浅い道の深さにする
(案内のページから深く取ったページでも、そこからのリンクをふつうの列に入れる)。
"""
import gzip, heapq, html, io, re, time, urllib.parse, zlib
from collections import Counter, defaultdict

import classify, pages
from fetcher import CAP_OTHER, Halted, netloc_of, norm_url, safe_join

# 業者・アプリ (取りに行かない)。(名前, ホストの正規表現, ごみ専用か)
VENDORS = [
    ("5374", r"(^|\.)5374\.jp$", True),
    ("sanaru", r"(^|\.)delight-system\.(com|co\.jp)$", True),           # さんあ～る (ディライトシステム)
    ("gomisuke", r"(^|\.)gomisuke\.jp$", True),                         # ごみスケ (G-Place)
    ("smartgomiapp", r"smartgomiapp|scoooop", True),                    # スマートごみアプリ
    ("gomisaku", r"(^|\.)gomisaku\.jp$", True),
    ("kviewer", r"(^|\.)kintoneapp\.com$", False),                      # kintone の公開ビュー (岡山市など)
    ("google_calendar", r"^calendar\.google\.com$", False),
    ("app_store", r"^(apps|itunes)\.apple\.com$", False),
    ("google_play", r"^play\.google\.com$", False),
    ("line", r"^(line\.me|lin\.ee|page\.line\.me|liff\.line\.me)$", False),
]
# 取りに行かないほかのホスト (検索エンジン・SNS・地図・ソフトの配布など)
EXCLUDED = re.compile(r"(^|\.)(google\.[a-z.]+|goo\.gl|bing\.com|yahoo\.co\.jp|yahoo\.com|duckduckgo\.com|twitter\.com|x\.com|"
                      r"facebook\.com|instagram\.com|youtube\.com|youtu\.be|tiktok\.com|wikipedia\.org|adobe\.com|"
                      r"microsoft\.com|apple\.com|amazon\.[a-z.]+|note\.com|ameblo\.jp|hatena\.ne\.jp|navitime\.co\.jp|"
                      r"mapion\.co\.jp|line\.me)$")
QUERY_DATE_VARIANTS = 3     # 日付で切り替わる問い合わせ (イベントカレンダーのわな) は同じ形でこの数まで
QUERY_DATE_VARIANTS_GOMI = 14   # 同じく、ごみの手がかりのあるリンク (ごみカレンダーの月送りなど)
QUERY_VARIANTS = 60         # ほかの問い合わせの、手がかりの無いリンクは同じ形でこの数まで (手がかりのあるものは数えず通す。
                            # dd.aspx?menuid=… のように、サイト全体が 1 つの形の CMS があるため)
QUERY_KANA_VARIANTS = 3     # 五十音の索引 (リンクの文字が「あ」「か行」だけ。kana.html?kana=… など) は同じ形でこの数まで
KANA_INDEX = re.compile(r"^[ぁ-んァ-ヶ](?:\s*行)?$")
DATE_PARAM = re.compile(r"^(?:y|m|d|yy|yyyy|mm|dd|ym|ymd|yyyymm|yyyymmdd|year|month|day|date|week|nen|tsuki|hi|"
                        r"cal_?date|target_?date|disp_?date|start_?date|end_?date|caldate|calendar_?(?:year|month|date))$", re.I)
DATE_VALUE = re.compile(r"^(?:19|20)\d{2}[-/.]?(?:0?[1-9]|1[0-2])(?:[-/.]?(?:0?[1-9]|[12]\d|3[01]))?$")
SITEMAP_MAX = CAP_OTHER     # gzip のサイトマップを広げたときの上限
SEARCH_QUERY = "ごみ 収集日"
MAX_HUB_DOCS = 80           # 収集日のページに並べる資料の数の上限
HUB_EXTRA_DEPTH = 2         # 案内のページの子は、深さの上限をこの数まで超えて取ってよい
HUB_MAX_DOCS = 15           # 案内のページの子として取る資料の数の上限 (hub_budget の内)
HUB_LIST_MAX = 20           # summary に書く、子を広げた案内のページの数
HUB_FOLDER_KIDS = 12        # 1 つの案内のページから広げる、同じフォルダの地区らしいページの数の上限
HUB_FOLDER_DEPTH = 2        # 同じフォルダの子を広げるのは、フォルダがこの深さ以上のときだけ (/a/b/ 以上。平らなサイトのメニューを広げない)
HUB_SHAPE_KIDS = 3          # 案内のページの子は、問い合わせの形 (kana.html?kana=あ など、ホスト・パス・名前の組) ごとにこの数まで
HUB_HEAP_MAX = 300          # 案内のページの子の列に置く数の上限 (上限に届いた後に覚えすぎない)
STRONG_EXTRA_DEPTH = 2      # 手がかりのとても強いリンクは、深さの上限をこの数まで超えてたどる
DEEP_BUDGET = 30            # 深さの上限を超えて取るページの数の上限 (1 サイト、budget の内。案内のページの子は別)
STRONG_P = 5                # 「とても強い」: 点がこれ以上、または収集日・カレンダーの言葉 (CAL_LINK) で点 4 以上
RELATED_HOPS = 3            # 関係する発行元 (一部事務組合など) のサイトでたどる段の数 (入口のページ = 1)
START_VARIANTS = 2          # トップにつながらないときに試す、ほかの書き方 (https/http・www) の数
# 公の団体らしいリンクの文字・ホスト名
PUBLIC_ANCHOR = re.compile(r"組合|広域連合|広域行政|広域市町村圏|衛生センター|環境センター|クリーンセンター|清掃センター|清掃工場")
PUBLIC_NOT = re.compile(r"協同組合|生活協同|生協|農協|漁協|協会|連合会|商工|株式会社|有限会社|[(（]株[)）]")   # 組合でも公の団体でないもの
PUBLIC_HOST = re.compile(r"kumiai|kouiki|koiki|kyodo|kyoudou|eisei|seiso|union|rengo")
PREF_SUB = re.compile(r"\.(?:pref\.[a-z-]+|metro\.tokyo)\.(?:lg\.)?jp$")      # 都道府県の部局 (環境局・消防庁など)
HOP_KINDS = ("lg_jp", "union", "named")   # 2 段目からもたどる発行元 (都道府県・ほかの市町村は入口のページと資料だけ)
COMMERCIAL_HOST = re.compile(r"\.(?:co\.jp|com|net|ne\.jp|biz|info|shop|app|io)$")
MUNI_HOST = re.compile(r"^(?:www\d*\.)?(?:city|town|vill|village|ward)\.")
PREF_HOST = re.compile(r"^(?:www\d*\.)?(?:pref\.[a-z-]+|metro\.tokyo)\.(?:lg\.)?jp$")
# 障害・停止の臨時ページ (トップがこれで、サイトの内へのリンクがほとんど無ければ unreachable とする)
OUTAGE = re.compile(r"臨時(?:案内|ページ|サイト)|システム障害|障害(?:が発生|のため|により)|(?:緊急|臨時)メンテナンス|メンテナンス中|"
                    r"(?:閲覧|アクセス|利用|表示)(?:でき|出来)(?:ない|ません)")
OUTAGE_LINKS = 10           # サイトの内へのリンクがこれより少ないときだけ


def variant_shape(u):
    """問い合わせつきの URL の形 (ホスト・パス・問い合わせの名前の組) と、日付で切り替わるか。問い合わせが無ければ (None, False)"""
    sp = urllib.parse.urlsplit(u)
    if not sp.query:
        return None, False
    params = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
    names = tuple(sorted({k.lower() for k, _ in params}))
    datey = any(DATE_PARAM.match(k) or DATE_VALUE.match(v.strip()) for k, v in params)
    return (sp.netloc, sp.path, names, datey), datey


def gunzip_capped(data, cap):
    """gzip を広げる (cap バイトまで。それより大きいものは切る)。壊れていれば None"""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            return g.read(cap)
    except (OSError, EOFError, zlib.error, MemoryError):
        return None


def vendor_of(netloc):
    host = netloc.split(":")[0]
    for name, rx, gomi_only in VENDORS:
        if re.search(rx, host):
            return name, gomi_only
    return None, False


def site_base(netloc):
    """サイトの内を決める名前 (先頭の www・www4 などを外す)"""
    host, _, port = netloc.partition(":")
    host = re.sub(r"^www\d*\.", "", host)
    return host + (":" + port if port else "")


def in_base(netloc, base):
    host, _, port = netloc.partition(":")
    bhost, _, bport = base.partition(":")
    return port == bport and (host == bhost or host.endswith("." + bhost))


def public_body(netloc, anchor=""):
    """ほかのホストが公の団体のサイトか → 種類 (pref: 都道府県とその部局 / lg_jp: 市町村でない lg.jp (一部事務組合・広域連合など) /
    union: ホスト名が組合・広域など / named: リンクの文字が組合・環境センターなど / muni: ほかの市町村で、リンクの文字が
    組合・清掃センターなど) か None。会社らしいホスト (.co.jp・.com など) と、協同組合・協会などは入れない。
    業者・アプリのホストは呼ぶ側で除く"""
    h = netloc.split(":")[0].lower()
    a = classify.nfkc(anchor)
    public_words = bool(PUBLIC_ANCHOR.search(a)) and not PUBLIC_NOT.search(a)
    if PREF_HOST.match(h) or PREF_SUB.search(h):
        return "pref"
    if h.endswith(".lg.jp"):
        if not MUNI_HOST.match(h):
            return "lg_jp"
        return "muni" if public_words else None
    if COMMERCIAL_HOST.search(h) or PUBLIC_NOT.search(a):
        return None
    if PUBLIC_HOST.search(h):
        return "union"
    return "named" if public_words else None


def gomi_hint(anchor, url):
    """リンクの文字か URL にごみの言葉があるか (ほかのサイトへ行くかの決まり)"""
    a, u = classify.nfkc(anchor).lower(), classify.url_text(url)
    return any(w in a for w in classify.GOMI_STRONG + classify.GOMI_MEDIUM) or \
        any(w in u for w in classify.URL_STRONG + classify.URL_MEDIUM)


def strong_link(p, anchor, url):
    """深さの上限を超えてたどる、とても強いリンクか (点 5 以上、または収集日・カレンダーの言葉で点 4 以上)"""
    return p >= STRONG_P or (p >= 4 and classify.hub_link_kind(anchor, url) == "cal")


def start_variants(url, dns_failed=False):
    """トップにつながらないときに試す、ほかの書き方 (www の有る無し、https と http)。名前が引けない (DNS) ときは www だけ"""
    sp = urllib.parse.urlsplit(url)
    host = sp.netloc
    out = []
    if not re.fullmatch(r"[\d.]+(?::\d+)?|\[.*", host):                 # IP アドレスに www は付けない
        other = host[4:] if host.startswith("www.") else "www." + host
        out.append(urllib.parse.urlunsplit((sp.scheme, other, sp.path or "/", sp.query, "")))
    if not dns_failed:
        flip = "http" if sp.scheme == "https" else "https"
        out.insert(0, urllib.parse.urlunsplit((flip, host, sp.path or "/", sp.query, "")))
    return [norm_url(u) for u in out if norm_url(u)][:START_VARIANTS]


class Options:
    def __init__(self, depth=3, budget=150, max_docs=25, related_budget=15, after_strong=60, site_time=1800.0, site_search=True,
                 hub_budget=40):
        self.depth, self.budget, self.max_docs = depth, budget, max_docs
        self.related_budget = related_budget      # ほかのサイト (関係する発行元など) で取る数の上限 (budget とは別)
        self.after_strong, self.site_time, self.site_search = after_strong, site_time, site_search
        self.hub_budget = hub_budget              # 案内のページの子を取る数の上限 (budget とは別)

    def as_dict(self):
        return dict(self.__dict__)


class SiteCrawl:
    """1 つの公式サイトを探す。run() のあと records (URL → 候補)・apps・status などを持つ"""

    def __init__(self, start_url, fetcher, opts, skip_scope=None):
        self.start = norm_url(start_url)
        self.skip_scope = skip_scope                     # トップの転送先が前に探したサイトの内なら探さない (同じサイトの別名)
        self.f, self.o = fetcher, opts
        self.bases = [site_base(netloc_of(self.start))] if self.start else []
        self.main_host = netloc_of(self.start) if self.start else None
        self.heap, self.seq, self.queued = [], 0, {}
        self.done, self.records = set(), {}
        self.anchors = defaultdict(list)
        self.found_from = {}
        self.parents = defaultdict(list)                 # URL → リンクしていたページ (5 つまで)
        self.page_docs = defaultdict(list)               # ページ → リンクしている資料 [(URL, リンクの文字)]
        self.apps = {}
        self.fetched = self.docs = self.offsite = 0
        self.skipped = Counter()
        self.read_errors = []
        self.variants = Counter()
        self.status, self.stop_reason, self.blocked = "ok", None, None
        self.final_top, self.top_result = None, None
        self.start_used, self.start_attempts = None, []  # つながったトップの URL と、試した書き方
        self.sitemaps, self.search = [], None
        self.strong_at = None
        self.hub_heap, self.hub_queued = [], set()       # 案内のページの子 (ほかより先に取る)
        self.hub_fetched = self.hub_docs = 0
        self.hub_shapes = Counter()                      # 案内のページの子の、問い合わせの形ごとの数
        self.hub_handoff = set()                         # 資料の上限を超えて、ふつうの列に渡した案内のページの子の資料
        self.hubs = []                                   # 子を広げた案内のページ
        self.min_depth = {}                              # URL → 知っているいちばん浅い深さ
        self.deep_fetched = 0                            # 深さの上限を超えて取ったページの数 (案内のページの子は別)
        self.deep_weak = set()                           # 深さの上限を超える、収集日・カレンダーの言葉の無いリンク
        self.rel_heap, self.rel_queued = [], set()       # ほかのサイト (関係する発行元など) の列 (自分のサイトのあとに取る)
        self.rel_hops = {}                               # URL → 入口からの段 (1 = 入口)
        self.related = {}                                # ホスト → 関係する発行元の記録
        self.started = time.monotonic()

    # ---- 内か外か ----

    def in_scope(self, netloc):
        return any(in_base(netloc, b) for b in self.bases)

    # ---- 入口 ----

    def _top(self):
        """トップを取る。つながらなければ / → ほかの書き方 (https/http・www) の順に試す → トップの候補か None"""
        top = self._visit(self.start, 0, None, "html", top=True)
        self.start_attempts.append({"url": self.start, "result": (self.top_result or {}).get("result")})
        sp = urllib.parse.urlsplit(self.start)
        root = f"{sp.scheme}://{sp.netloc}/"
        if top is None and self.status == "ok" and root not in self.done:
            top = self._visit(root, 0, None, "html", top=True)    # 公式サイトの URL のパスが古いときは / から
            self.start_attempts.append({"url": root, "result": (self.top_result or {}).get("result")})
        if top is not None:
            self.start_used = self.start_attempts[-1]["url"]
            return top
        if self.status not in ("ok", "unreachable") or (self.top_result or {}).get("http"):
            return None                                       # ブロック・robots.txt で禁止・HTTP の誤り (404 など) は試し直さない
        dns = "dns" in str((self.top_result or {}).get("error") or "")
        for alt in start_variants(self.start, dns_failed=dns):
            if alt in self.done or self.f.stopped.get(netloc_of(alt), {}).get("kind") == "blocked":
                continue
            self.status, self.blocked = "ok", None
            top = self._visit(alt, 0, None, "html", top=True)
            self.start_attempts.append({"url": alt, "result": (self.top_result or {}).get("result")})
            if top is not None:
                self.start_used = alt
                return top
        return None

    def _outage(self, top):
        """トップが障害の臨時ページか (「臨時案内」「システム障害」などで、サイトの内へのリンクがほとんど無い)"""
        page = top.get("_page")
        if page is None:
            return False
        if not OUTAGE.search(classify.nfkc(" ".join([page.title, page.description] + [h for _, h in page.headings[:3]]))):
            return False
        inside = set()
        for href, _, tag in page.links:
            href = html.unescape(href or "").strip()
            if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                u = norm_url(safe_join(page.base, href))
                if u and self.in_scope(netloc_of(u)) and u.split("#")[0] != top["url"]:
                    inside.add(u)
        return len(inside) < OUTAGE_LINKS

    def run(self):
        if not self.start:
            self.status = "no_site"
            return self
        try:
            top = self._top()
            if top is None:
                if self.status == "ok":
                    self.status = "top_failed"
                return self._finish()
            if self.skip_scope is not None and self.skip_scope(netloc_of(self.final_top or self.start)):
                self.status = "same_site"                     # 前に探した公式サイトと同じ (古いドメインからの転送など)
                return self._finish()
            if self._outage(top):
                self.status = "unreachable"                   # 障害の臨時ページ (--resume でやり直す)
                self.blocked = {"url": top["url"], "reason": "障害の臨時ページ (" + (top.get("title") or "")[:60] + ")",
                                "where": "top"}
                return self._finish()
            self._sitemaps()
            if self.o.site_search and top.get("_page") is not None:
                self._site_search(top["_page"])
            self._loop()
            if self.status == "ok" and self.stop_reason != "time":
                self._related_loop()
            left = sorted((e for e in self.heap if e[4] == "doc" and -e[0] >= 4 and e[3] not in self.done))[:30]
            for negp, depth, _, url, _ in left:
                self._unfetched_doc(url, depth, "not_reached")
            for negp, depth, _, url, typ in sorted(self.hub_heap)[:30]:
                if typ == "doc" and url not in self.done:
                    self._unfetched_doc(url, depth, "hub_budget")
            for url in sorted(self.hub_handoff - self.done)[:30]:      # ふつうの列に渡したが取れなかった案内のページの子の資料
                self._unfetched_doc(url, self.min_depth.get(url), "hub_max_docs")
        except Halted:
            self.status = "halted"
        return self._finish()

    def _finish(self):
        for rec in self.records.values():
            classify.update(rec, self._anchors_of(rec))
            rec.pop("_page", None)
        self._link_hubs()
        return self

    def _anchors_of(self, rec):
        return self.anchors.get(rec["url"], []) + self.anchors.get(rec.get("requested_url") or "", [])

    def _link_hubs(self):
        """収集日のページに、リンクしている資料の一覧を付け、その資料に linked_from_schedule を付けて点数を付け直す"""
        hubs = {u: r for u, r in self.records.items() if r["file_type"] == "html" and classify.is_schedule(r)}
        by_req = {r["requested_url"]: r for r in self.records.values() if r.get("requested_url")}
        for u, hub in hubs.items():
            docs = []
            for d, text in self.page_docs.get(u, [])[:MAX_HUB_DOCS]:
                rec = self.records.get(d) or by_req.get(d)
                docs.append({"url": rec["url"] if rec else d, "text": text,
                             "format_class": rec["format_class"] if rec else None,
                             **({"not_fetched": rec["not_fetched"]} if rec and rec.get("not_fetched") else {})})
            if docs:
                hub["documents"] = docs
        if not hubs:
            return
        for rec in self.records.values():
            if rec["url"] in hubs:
                continue
            ps = self.parents.get(rec["url"], []) + self.parents.get(rec.get("requested_url") or "", [])
            kinds = sorted({k for p in ps if p in hubs for k in hubs[p]["kinds"] if k in classify.SCHEDULE_KINDS})
            if kinds:
                rec["features"]["linked_from_schedule"] = kinds
                classify.update(rec, self._anchors_of(rec))

    def _main_count(self):
        """budget・after_strong で数える取得の数 (案内のページの子と、ほかのサイトのものは数えない)"""
        return self.fetched - self.hub_fetched - self.offsite

    def _depth(self, url, depth):
        """知っているいちばん浅い深さ (案内のページの子として深く入れたものでも、ふつうの道の方が浅ければそちら)"""
        return min(depth, self.min_depth.get(url, depth))

    def _loop(self):
        while self.heap or (self.hub_heap and self.hub_fetched < self.o.hub_budget):
            if self.status in ("blocked", "unreachable", "robots_disallowed"):
                break
            if time.monotonic() - self.started > self.o.site_time:
                self.stop_reason = "time"
                break
            if self.hub_heap and self.hub_fetched < self.o.hub_budget:     # 案内のページの子を先に (止める決まりより前)
                negp, depth, _, url, typ = heapq.heappop(self.hub_heap)
                if url in self.done:
                    continue
                depth = self._depth(url, depth)
                if typ == "doc" and self.hub_docs >= HUB_MAX_DOCS:
                    self.skipped["hub_max_docs"] += 1           # 資料の上限を超えた子は、ふつうの列 (max_docs の内) に渡す
                    self._push(url, depth, max(-negp, 2), "doc", self.found_from.get(url))
                    self.hub_handoff.add(url)
                    continue
                self._visit(url, depth, self.found_from.get(url), typ, priority=-negp, via_hub=True)
                continue
            if self._main_count() >= self.o.budget:
                self.stop_reason = "budget"
                break
            if self.strong_at is not None and self._main_count() - self.strong_at >= self.o.after_strong:
                self.stop_reason = "found"
                break
            negp, depth, _, url, typ = heapq.heappop(self.heap)
            if url in self.done:
                continue
            p = -negp
            deep_weak = url in self.deep_weak and self._depth(url, depth) > self.o.depth
            if self.strong_at is not None and (p <= 1 or deep_weak):
                self.skipped["weak_after_found"] += 1
                continue
            if typ == "doc" and self.docs >= self.o.max_docs:
                self.skipped["max_docs"] += 1
                if p >= 4:
                    self._unfetched_doc(url, depth, "max_docs")
                continue
            depth = self._depth(url, depth)
            if typ == "html" and depth > self.o.depth:
                if self.deep_fetched >= DEEP_BUDGET:
                    self.skipped["deep_budget"] += 1            # 深さの上限を超えるページは 30 まで
                    continue
                self.deep_fetched += 1
            self._visit(url, depth, self.found_from.get(url), typ, priority=p)
        else:
            self.stop_reason = self.stop_reason or "frontier_empty"

    def _related_loop(self):
        """ほかのサイト (関係する発行元・.jp のごみのページとその資料) を、別の上限 (related_budget) で取る"""
        while self.rel_heap and self.offsite < self.o.related_budget:
            if self.status in ("blocked", "unreachable", "robots_disallowed"):
                break
            if time.monotonic() - self.started > self.o.site_time:
                break
            negp, depth, _, url, typ = heapq.heappop(self.rel_heap)
            if url in self.done:
                continue
            self._visit(url, depth, self.found_from.get(url), typ, priority=-negp, via_related=True)
        left = sum(1 for e in self.rel_heap if e[3] not in self.done)
        if left:
            self.skipped["related_budget"] += left

    # ---- 1 件取る ----

    def _visit(self, url, depth, parent, typ, top=False, priority=None, via_hub=False, via_related=False):
        self.done.add(url)
        r = self.f.get(url)
        if r.kind != "robots_disallowed":                     # robots.txt で取らなかったものは数えない
            self.fetched += 1
            if via_related:
                self.offsite += 1                             # ほかのサイトのものは別に数える
                host = netloc_of(url)
                if host in self.related:
                    self.related[host]["fetched"] += 1
            elif via_hub:
                self.hub_fetched += 1                         # 案内のページの子は別に数える
                self.hub_docs += typ == "doc"
            elif typ == "doc":
                self.docs += 1
        final = r.final_url or url
        self.done.add(final)
        fnl = netloc_of(final)
        if top:
            self.final_top = final
            self.top_result = {"url": url, "result": r.kind, "http": r.status, "error": r.error}
            if r.ok and not self.in_scope(fnl):
                self.bases.append(site_base(fnl))             # トップの転送先もサイトの内
            if r.ok:
                self.main_host = fnl
        if r.kind in ("blocked", "unreachable", "robots_disallowed", "timeout") and typ == "doc":
            self._unfetched_doc(url, depth, r.kind)
        if r.kind in ("blocked", "unreachable"):
            nl = netloc_of(final if r.kind == "blocked" else url)
            if top or nl == self.main_host:
                self.status = r.kind
                self.blocked = {"url": final, "reason": r.error, "where": self._block_where(final, top)}
            else:
                self.skipped[r.kind] += 1
            return None
        if r.kind == "robots_disallowed":
            if top:
                self.status = "robots_disallowed"
            self.skipped["robots"] += 1
            return None
        if r.kind == "too_large":
            ft = pages.file_type(final, r.content_type)
            if ft != "other":                                 # 大きすぎるページ・資料も、リンクの文字で候補にしておく
                self._record(url, final, ft, depth, parent, r, {"too_large": True, "size": r.size}, fetched=False,
                             not_fetched="too_large")
            self.skipped["too_large"] += 1
            return None
        if not r.ok:
            self.skipped[r.kind] += 1
            return None
        try:
            return self._read(url, final, fnl, depth, parent, typ, top, r, via_hub)
        except (OSError, MemoryError) as e:                  # キャッシュを読めないなど。この 1 件だけ飛ばす
            self.skipped["read_error"] += 1
            self.read_errors.append(f"{type(e).__name__}"[:80])
            return None

    def _block_where(self, url, top=False):
        """ブロック・つながらないのは、どこで分かったか: robots (robots.txt) / top (トップ) / page (ほかのページ)"""
        s = self.f.stopped.get(netloc_of(url)) or {}
        if str(s.get("url") or "").endswith("/robots.txt"):
            return "robots"
        return "top" if top else "page"

    def _read(self, url, final, fnl, depth, parent, typ, top, r, via_hub=False):
        ft = pages.file_type(final, r.content_type, r.head(1024))
        offsite = not self.in_scope(fnl)
        body = r.body() if ft in ("html", "csv", "ics") or not r.raw_path() else b""
        if ft == "html":
            if typ == "doc":                                    # 資料のつもりが HTML (案内ページなど)
                typ = "html"
            page = pages.parse_html(body, final, r.headers.get("content-type", ""))
            outside = offsite or typ == "offsite"
            hubf, kids = ({}, []) if outside else self._hub_links(page, final)
            rec = self._record(url, final, "html", depth, parent, r, hubf, page=page)
            if page.canonical:
                c = norm_url(page.canonical)
                if c and c != final and c != url:
                    rec["canonical"] = c                      # 同じページの別の URL (比べるときに使う)
            if not outside:
                self._vendor_scripts(page, final)
            if outside and final not in self.rel_hops and url in self.rel_hops:
                self.rel_hops[final] = self.rel_hops[url]
            self._links(page, final, depth, rec, offsite_page=outside, via_hub=via_hub)
            if kids:
                self._expand_hub(rec, kids, depth)
            if not top:
                rec.pop("_page", None)                        # 読んだページを持ち続けない (トップだけサイト内検索に使う)
            return rec
        if ft == "pdf":
            feats, first, text = pages.analyze_pdf(path=r.raw_path(), data=None if r.raw_path() else body)
            pf, best, cal = classify.pdf_page_features(text, pages.PAGE_SEP)    # ページごとのごみの言葉
            feats.update(pf)
            excerpt = pages.pdf_excerpt(text, pf.get("page_hits"), best, focus=classify.PAGE_FOCUS)  # LLM には、ごみの収集のページの抜粋も
            pts = pages.split_pages(text)
            if not classify.pdf_calendar(feats):
                cal = []
            # 曜日・日付・地区などの数は、始めの 3 ページとカレンダーのページから (分け方の冊子の全ページでは多くなりすぎる)
            ftext = pages.PAGE_SEP.join(pts[:3] + [pts[i - 1] for i in cal if i > 3])
            # 年度は、始めの 3 ページと、ごみの言葉の多いページ (6 つまで) から (年表・統計の表の昔の年を入れない)
            fytext = pages.PAGE_SEP.join(pts[:3] + [pts[i - 1] for i in (best or [])[:6] if 3 < i <= len(pts)])
            return self._record(url, final, "pdf", depth, parent, r, feats, text=ftext, excerpt=excerpt, head=first[:300],
                                fy_text=fytext)
        if ft in ("xlsx", "xls"):
            if ft == "xlsx":
                feats, ex, text = pages.analyze_xlsx(path=r.raw_path(), data=None if r.raw_path() else body)
            else:
                feats, ex, text = {"xls": "古い Excel (xls) は中を見ていない"}, "", ""
            return self._record(url, final, ft, depth, parent, r, feats, text=text, excerpt=ex)
        if ft == "csv":
            feats, ex, text = pages.analyze_csv(body)
            return self._record(url, final, "csv", depth, parent, r, feats, text=text, excerpt=ex)
        if ft == "ics":
            feats, ex, text = pages.analyze_ics(body)
            return self._record(url, final, "ics", depth, parent, r, feats, text=text, excerpt=ex)
        self.skipped["other_type"] += 1
        return None

    def _record(self, url, final, ft, depth, parent, r, feats, page=None, text="", excerpt="", head="", fetched=True,
                not_fetched=None, fy_text=None):
        rec = {"url": final, "file_type": ft, "depth": depth, "found_from": parent, "fetched": fetched,
               "offsite": not self.in_scope(netloc_of(final)), "features": dict(feats)}
        if not_fetched:
            rec["not_fetched"] = not_fetched
        if final != url:
            rec["requested_url"] = url
        if r is not None:
            rec["http"] = {"status": r.status, "size": r.size, "sha256": r.sha256,
                           "last_modified": r.headers.get("last-modified"), "fetched_at": r.fetched_at,
                           "from_cache": r.from_cache}
        if page is not None:
            rec["title"] = page.title
            rec["headings"] = [h for _, h in page.headings][:8]
            text = page.text
            gomi_page = bool(classify.GOMI_ITEM.search(classify.nfkc(page.title + " ".join(rec["headings"]))))
            rec["features"].update(classify.table_features(page.tables, page_gomi=gomi_page))
            excerpt = pages.excerpt(page, classify.schedule_tables(page.tables, page_gomi=gomi_page))
            rec["_page"] = page
        if text:
            rec["features"].update(classify.text_features(text))
        if fy_text is not None:
            rec["features"]["fiscal_years"] = classify.fiscal_years(classify.nfkc(fy_text[:100_000]))
        if not self.in_scope(netloc_of(final)) and netloc_of(final) in self.related:
            rec["related_publisher"] = netloc_of(final)      # 関係する発行元 (一部事務組合など) のサイトのもの
        rec["_excerpt"] = excerpt[:pages.EXCERPT_MAX]
        rec["_head"] = head
        if ft == "pdf" and r is not None:
            rec["_pdf_path"] = r.raw_path()
        classify.update(rec, self.anchors.get(final, []) + self.anchors.get(url, []))
        old = self.records.get(final)
        if old is None or (fetched and not old.get("fetched")) or (old.get("fetched") == fetched and old.get("score", 0) <= rec["score"]):
            self.records[final] = rec
        if final != url and url in self.records and not self.records[url].get("fetched"):
            del self.records[url]                             # 取る前に置いた仮の候補は消す
        if classify.is_strong(rec) and self.strong_at is None:
            self.strong_at = self._main_count()
        return rec

    # ---- リンク ----

    def _push(self, url, depth, p, typ, parent):
        """取る候補に入れる。新しく入れたら True"""
        self.min_depth[url] = min(depth, self.min_depth.get(url, depth))
        if url in self.done:
            return False
        key = (p, -depth)
        if url in self.queued and self.queued[url] >= key:
            return False
        new = url not in self.queued
        self.queued[url] = key
        self.found_from.setdefault(url, parent)
        self.seq += 1
        heapq.heappush(self.heap, (-p, depth, self.seq, url, typ))
        return new

    def _variant_ok(self, u, p, text=""):
        """問い合わせつきの URL を入れてよいか (日付で切り替わるわなを避ける。収集日・カレンダーの言葉のリンクは落とさない)"""
        shape, datey = variant_shape(u)
        if shape is None or u in self.queued:
            return True, None
        if datey:
            cap = QUERY_DATE_VARIANTS_GOMI if p >= 2 else QUERY_DATE_VARIANTS
        elif KANA_INDEX.match(classify.nfkc(text or "").strip()):
            cap = QUERY_KANA_VARIANTS                           # 五十音の索引は少しだけ (中身は同じ形の表のことが多い)
        else:
            cap = None if p >= 2 else QUERY_VARIANTS
        if cap is not None and self.variants[shape] >= cap:
            self.skipped["query_variants"] += 1
            return False, None
        return True, shape

    def _anchor(self, url, text):
        a = self.anchors[url]
        text = " ".join((text or "").split())[:120]
        if text and text not in a and len(a) < 6:
            a.append(text)

    def _push_related(self, u, depth, p, typ, parent, hop, text):
        """ほかのサイトの列に入れる (自分のサイトを探し終えてから、related_budget の内で取る)。
        収集日・カレンダーの言葉のリンクは、お知らせの記事より先にする"""
        if u in self.done or u in self.rel_queued or len(self.rel_heap) >= HUB_HEAP_MAX:
            return False
        self.rel_queued.add(u)
        self.rel_hops[u] = hop
        self.found_from.setdefault(u, parent)
        pr = p + (2 if classify.hub_link_kind(text, u) == "cal" else 0)
        self.seq += 1
        heapq.heappush(self.rel_heap, (-pr, depth, self.seq, u, typ))
        return True

    def _note_related(self, netloc, kind, u, text, parent):
        """関係する発行元 (一部事務組合・広域連合・都道府県など) として記録する (10 まで)"""
        if netloc not in self.related and len(self.related) < 10:
            self.related[netloc] = {"host": netloc, "kind": kind, "url": u, "anchor": " ".join((text or "").split())[:120],
                                    "found_from": parent, "fetched": 0}

    def _links(self, page, url, depth, rec, offsite_page=False, via_hub=False):
        """リンクの文字・リンク元・資料の一覧を覚え、取る候補に入れる。
        深さの上限 (3) を超えたページ (手がかりのとても強いリンクでたどったもの・案内のページの子) からは、
        資料と、とても強いリンクだけを入れる (深さ 5 まで)。それより深いページは覚えるだけ。
        ほかのサイトのページ (offsite_page) からは、資料と、関係する発行元の同じホストのとても強いリンク (3 段まで) だけ"""
        max_page = self.o.depth + STRONG_EXTRA_DEPTH
        if depth > max_page and not via_hub and not offsite_page:   # 資料のつもりで取った深い HTML の先は追わない
            return
        push_ok = offsite_page or depth <= max_page
        rel = classify.relevance(rec)
        here = netloc_of(url)
        hop = self.rel_hops.get(url, RELATED_HOPS) if offsite_page else 0
        follow_here = offsite_page and (self.related.get(here) or {}).get("kind") in HOP_KINDS and hop < RELATED_HOPS
        links = list(page.links)
        if page.refresh:
            links.insert(0, (page.refresh, "(meta refresh)", "refresh"))
        if page.nofollow and not page.refresh:
            self.skipped["nofollow_pages"] += 1
            return
        for href, text, tag in links:
            href = html.unescape(href or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            u = norm_url(safe_join(page.base, href))
            if not u or u == url or len(u) > 500:
                continue
            nl = netloc_of(u)
            self._anchor(u, text)
            if url not in self.parents[u] and len(self.parents[u]) < 5:
                self.parents[u].append(url)
            ext = pages.ext_of(u)
            is_doc = ext in pages.DOC_EXT
            if is_doc and len(self.page_docs[url]) < MAX_HUB_DOCS and u not in (d for d, _ in self.page_docs[url]):
                self.page_docs[url].append((u, " ".join((text or "").split())[:120]))
            p = classify.link_priority(text, u, rel, is_doc)
            if tag == "refresh":
                p = max(p, 5)
                if depth == 0 and not self.in_scope(nl) and not vendor_of(nl)[0] and not EXCLUDED.search(nl.split(":")[0]):
                    self.bases.append(site_base(nl))           # トップが別のホストへ移っている (meta refresh・JavaScript)
            vname, gomi_only = vendor_of(nl)
            if vname:
                if gomi_only or p >= 2:
                    self._app(u, vname, text, url)
                continue                                        # 業者・アプリのホストへは行かない
            if not push_ok or EXCLUDED.search(nl.split(":")[0]) or ext in pages.SKIP_EXT:
                continue
            inside = self.in_scope(nl)
            host = nl.split(":")[0]
            if offsite_page:
                pub = self.related.get(nl)
                if is_doc:
                    if p >= 3 and (host.endswith(".jp") or pub or nl == here):
                        self._push_related(u, depth + 1, p, "doc", url, hop, text)
                elif follow_here and nl == here and strong_link(p, text, u):
                    self._push_related(u, depth + 1, p, "offsite", url, hop + 1, text)
                continue                                        # ほかのサイトのページからは、資料と同じ発行元の強いリンクだけ
            shape = None
            if inside:
                ok, shape = self._variant_ok(u, p, text)
                if not ok:
                    continue
            pushed = False
            child = depth + 1
            if is_doc:
                if inside and p >= 2:
                    pushed = self._push(u, child, p, "doc", url)
                elif not inside and p >= 3 and (host.endswith(".jp") or public_body(nl, text)):
                    self._push_related(u, child, p, "doc", url, 1, text)
            elif inside:
                if p == 0 and child > 1:
                    continue
                if child > self.o.depth:
                    if child > max_page or not strong_link(p, text, u):
                        continue                                # 深さの上限を超えるのは、とても強いリンクだけ
                    if classify.hub_link_kind(text, u) != "cal":
                        p -= 2 * (child - self.o.depth)         # 収集日・カレンダーの言葉でなければ、深さの内のリンクより後に
                        self.deep_weak.add(u)                   # (収集日の資料が見つかったあとは取らない。浅い道があれば別)
                    else:
                        self.deep_weak.discard(u)
                pushed = self._push(u, child, p, "html", url)
            else:
                kind = public_body(nl, text)
                if kind and gomi_hint(text, u) and (p >= 3 or (p >= 2 and rel >= 2)):
                    self._note_related(nl, kind, u, text, url)  # 関係する発行元 (一部事務組合など)。入口 = 1 段
                    first = 1 if (self.related.get(nl) or {}).get("kind") in HOP_KINDS else RELATED_HOPS
                    self._push_related(u, child, p, "offsite", url, first, text)
                elif p >= 4 and host.endswith(".jp"):
                    self._push_related(u, child, p, "offsite", url, RELATED_HOPS, text)   # ほかの .jp のごみのページ (資料だけ見る)
            if pushed and shape is not None:
                self.variants[shape] += 1

    def _vendor_scripts(self, page, url):
        """業者のホストから読み込むスクリプト (埋め込みのアプリ。ごみスケの JSONP など) をアプリとして記録する (取りに行かない)"""
        for src in page.scripts:
            u = norm_url(src)
            if not u:
                continue
            vname, gomi_only = vendor_of(netloc_of(u))
            if vname and gomi_only:
                self._app(u, vname, "(埋め込み) " + (page.title or "")[:100], url)

    # ---- 案内のページ (hub) ----

    def _hub_links(self, page, url):
        """案内のページかを見るためのリンクの数 (hub_links・hub_docs・hub_cal_links・hub_place_links) と、
        子を広げるときの候補 [(種類, URL, 資料か, 同じフォルダか)]。同じサイトのリンクだけ。1 つの URL は強い種類で数える"""
        order = {"cal": 3, "gomi": 2, "place": 1, None: 0}
        best = {}
        for href, text, tag in page.links:
            if tag == "refresh":
                continue
            href = html.unescape(href or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            u = norm_url(safe_join(page.base, href))
            if not u or u == url or len(u) > 500:
                continue
            nl = netloc_of(u)
            if not self.in_scope(nl) or vendor_of(nl)[0] or pages.ext_of(u) in pages.SKIP_EXT:
                continue
            k = classify.hub_link_kind(text, u)
            if u not in best or order[k] > order[best[u]]:
                best[u] = k
        folder = url.rsplit("/", 1)[0] + "/"
        fpath = urllib.parse.urlsplit(folder).path.strip("/")
        deep = bool(fpath) and fpath.count("/") + 1 >= HUB_FOLDER_DEPTH
        n, kids = Counter(), []
        for u, k in best.items():
            is_doc = pages.ext_of(u) in pages.DOC_EXT
            if k in ("cal", "gomi"):
                n["hub_docs" if is_doc else "hub_links"] += 1
            if k == "cal":
                n["hub_cal_links"] += 1
            elif k == "place":
                n["hub_place_links"] += 1
            kids.append((k, u, is_doc, deep and u.startswith(folder)))
        return dict(n), kids

    def _expand_hub(self, rec, kids, depth):
        """収集日・カレンダー・地区の言葉のある案内のページの子を、案内のページの子の列に入れる (ほかより先に取る)。
        cal の子 (収集日・カレンダー・地区の言葉) > 資料 (own だけ。地区の名前だけ・言葉の無いもの) >
        同じフォルダの地区らしいページ (own だけ。深いフォルダのときだけ、12 まで)。links は cal の子だけ"""
        mode = classify.hub_expand_mode(rec)
        child = depth + 1
        if not mode or child > self.o.depth + HUB_EXTRA_DEPTH or self.hub_fetched >= self.o.hub_budget:
            return                                              # 上限に届いたら子を増やさない
        added = folder_kids = 0
        for k, u, is_doc, same in kids:
            if u in self.done or u in self.hub_queued or len(self.hub_heap) >= HUB_HEAP_MAX:
                continue
            shape, _ = variant_shape(u)
            if shape is not None and self.hub_shapes[shape] >= HUB_SHAPE_KIDS:
                self.skipped["hub_shape"] += 1                  # 同じ形の問い合わせ (kana.html?kana=…) は 3 つまで
                continue
            if k == "cal":
                p = 3
            elif mode != "own":
                continue
            elif is_doc and k in ("place", None):                 # 地区の名前だけ・PDF (1.2MB) だけの資料 (出し方などのごみの資料は広げない)
                p = 2
            elif not is_doc and same and k in ("place", None) and folder_kids < HUB_FOLDER_KIDS:
                p = 1
                folder_kids += 1
            else:
                continue
            self.hub_queued.add(u)
            if shape is not None:
                self.hub_shapes[shape] += 1
            self.found_from.setdefault(u, rec["url"])
            self.min_depth[u] = min(child, self.min_depth.get(u, child))
            self.seq += 1
            heapq.heappush(self.hub_heap, (-p, child, self.seq, u, "doc" if is_doc else "html"))
            added += 1
        if added and len(self.hubs) < HUB_LIST_MAX:
            self.hubs.append({"url": rec["url"], "mode": mode, "children": added})

    def _app(self, u, vendor, text, found_on):
        if u in self.apps:
            return
        rec = {"url": u, "file_type": "app", "vendor": vendor, "depth": None, "found_from": found_on, "fetched": False,
               "offsite": True, "features": {}, "title": text[:120]}
        self.apps[u] = classify.update(rec, [text])

    def _unfetched_doc(self, u, depth, why):
        """取らなかった・取れなかった資料も、リンクの文字だけで候補にしておく (点数は 60 まで)"""
        if u in self.records:
            return
        rec = {"url": u, "file_type": pages.DOC_EXT.get(pages.ext_of(u), "other"), "depth": depth,
               "found_from": self.found_from.get(u), "fetched": False, "not_fetched": why,
               "offsite": not self.in_scope(netloc_of(u)), "features": {}}
        self.records[u] = classify.update(rec, self.anchors.get(u, []))

    # ---- サイトマップ ----

    def _sitemaps(self):
        top = self.final_top or self.start
        sp = urllib.parse.urlsplit(top)
        rob = self.f.robots(top)
        urls = [norm_url(s) for s in rob.sitemaps()]
        urls = [u for u in urls if u and self.in_scope(netloc_of(u))][:3] or [f"{sp.scheme}://{sp.netloc}/sitemap.xml"]
        queue, seen, total = list(urls), set(), 0
        while queue and len(seen) < 6:
            sm = queue.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            r = self.f.get(sm, cap=CAP_OTHER)
            info = {"url": sm, "result": r.kind, "http": r.status, "urls": 0}
            self.sitemaps.append(info)
            if not r.ok:
                if r.kind in ("blocked", "unreachable") and netloc_of(sm) == self.main_host:
                    self.status = r.kind
                    self.blocked = {"url": sm, "reason": r.error, "where": "sitemap"}   # 人が見るときのため (どこで止まったか)
                    return
                continue
            try:
                data = r.body()
            except OSError:
                continue
            if data[:2] == b"\x1f\x8b":
                data = gunzip_capped(data, SITEMAP_MAX)          # 広げすぎない (gzip 爆弾)
                if data is None:
                    continue
            text = data.decode("utf-8", "replace")
            low = text[:5000].lower()
            if "<urlset" not in low and "<sitemapindex" not in low:
                continue
            locs = [html.unescape(x.strip()) for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.S | re.I)]
            info["urls"] = len(locs)
            if "<sitemapindex" in low:
                kids = [k for k in (norm_url(x) for x in locs) if k and self.in_scope(netloc_of(k))]
                rank = {k: classify.link_priority("", k, 0, True) for k in kids}      # True: URL の sitemap を手がかりに数えない
                good = sorted((k for k in kids if rank[k] >= 2), key=lambda k: -rank[k])
                queue.extend(good[:5] if good else kids if len(kids) <= 5 else [])   # 手がかりの無い大きな索引は読まない
                continue
            pushed = 0
            for loc in locs:
                u = norm_url(loc)
                if not u or not self.in_scope(netloc_of(u)) or u in self.done:
                    continue
                ext = pages.ext_of(u)
                if ext in pages.SKIP_EXT:
                    continue
                is_doc = ext in pages.DOC_EXT
                p = classify.link_priority("", u, 0, is_doc)
                if p >= 2 and total < 300:
                    self._push(u, 1, p, "doc" if is_doc else "html", sm)
                    pushed += 1
                    total += 1
            info["pushed"] = pushed

    # ---- サイト内検索 ----

    def _site_search(self, page):
        form = pages.find_search_form(page)
        if not form:
            return
        action, hidden, name = form
        a = norm_url(action)
        if not a or not self.in_scope(netloc_of(a)):
            return
        params = list(hidden) + [(name, SEARCH_QUERY)]
        cs = page.charset if page.charset in ("utf-8", "cp932", "euc_jp") else "utf-8"
        sp = urllib.parse.urlsplit(a)
        old = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
        qs = urllib.parse.urlencode(old + params, encoding=cs, errors="ignore")
        u = norm_url(urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path, qs, "")))
        if u:
            self.search = {"url": u}
            self._push(u, 1, 5, "html", page.url)
            self._anchor(u, "(サイト内検索: " + SEARCH_QUERY + ")")

    # ---- まとめ ----

    def summary(self):
        moved = self.bases[1] if len(self.bases) > 1 else None    # トップの転送・meta refresh で移った先
        return {"start_url": self.start, "start_used": self.start_used,
                **({"start_attempts": self.start_attempts} if len(self.start_attempts) > 1 else {}),
                **({"moved_to": moved} if moved else {}),         # トップが別のホストへ転送された (台帳の URL が古いかもしれない)
                "final_url": self.final_top, "top": self.top_result, "bases": self.bases, "status": self.status,
                "stop_reason": self.stop_reason, "blocked": self.blocked, "fetched": self.fetched, "docs": self.docs,
                "offsite": self.offsite, "skipped": dict(sorted(self.skipped.items())), "sitemaps": self.sitemaps,
                **({"read_errors": self.read_errors[:10]} if self.read_errors else {}),
                "site_search": self.search, "frontier_left": len([1 for _, _, _, u, _ in self.heap if u not in self.done]),
                "deep_fetched": self.deep_fetched, "hub_fetched": self.hub_fetched, "hub_docs": self.hub_docs, "hubs": self.hubs,
                "hub_left": len([1 for _, _, _, u, _ in self.hub_heap if u not in self.done]),
                "related": list(self.related.values()),
                "related_left": len([1 for _, _, _, u, _ in self.rel_heap if u not in self.done])}
