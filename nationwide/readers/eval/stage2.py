"""M1 の作る順の 5〜6 (規則・地区・品目の文法、HTML の表・しるし・CSV) を、tier A の HTML・CSV の自治体に通す (DESIGN.md §6.2)

  python nationwide/readers/eval/stage2.py [--codes 112275,…] [--llm] [--offline]
      # data/readers/<lg_code>/stage2.json (スキーマ v0.2 の形。git に入れない) と、自治体ごとの結果の表を出す

- 入力はキャッシュ (data/discover/cache/) と段 0 の一覧 (data/readers/<lg_code>/docs.json) だけ。サイトには取りに行かない
- 読むのは、一覧のうち HTML (html_table・html_text) と CSV・Excel で、収集日・地区の資料 (kinds に calendar・districts) のもの。
  型 A (CSV・Excel)、型 B (HTML の表)、型 C (HTML の文。表の外の見出しごとの規則) を読む。PDF (型 D 以降) は読まない (pending_printed の判定にだけ使う)
- 読み取り役 A はコード (html_tables・tabular・members・categories・rules)。--llm で B (LLM。表の生の断片 table.B@2、
  本文の範囲の全体 text.B@1 = 型 C の文と、題・自治体の地区の 2 つ目の読み) も読み、A と B を比べる (品目・規則・地区・しるしの意味)。
  キャッシュ (data/readers/llm_cache/) にあれば聞き直さない。--offline はキャッシュだけ (キーを使わない)
- 規則のマスの読み残し (「※第5週は休み」「（月2回）」) は、年末年始・祝日の文としてそのマスの地区・品目に使えなければ、使われなかった但し書き。
  同じ地区・品目をほかの表・資料も書いていれば比べ、違えば先の系列を裁定待ち (rule) にする。重なって落とした系列の deferred・但し書き・
  表のしるし・ページとマスの方針の例外も先の系列に掛ける (和。方針が先の系列の方針と違えば裁定待ち policy。期間の違う資料の方針は写さない)
- 地区・品目の名前のマス (「東町（※祝日は収集しません）」「燃やせるごみ ※年末は別日程」「東町（月2回）」) は閉じた型で読む:
  読んだ名前 (members の名前を丸ごと・品目の言葉) と形の決まった条件を除いて句読点・かっこ・空白しか残らないときだけマスごと使ったことにし、
  1 字でも残れば、残りの切れ端はその地区・品目だけの但し書き (ページの方針の文にも、資料すべての但し書きにもしない)
- 方針の文 (祝日・年末年始・deferred・caveat・休み) の読みは、まだコードだけ (B は作る順の 8)。だから方針を使う系列は single_reading
- 出力は out/schedules/ には書かない (公開の git に入れる日付は、作る順の 9 の build.py から)
"""
import argparse, collections, datetime as dt, gzip, json, os, pathlib, re, sys, urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
READERS = HERE.parent
ROOT = READERS.parent.parent
sys.path.insert(0, str(READERS))
sys.path.insert(0, str(READERS.parent / "discover"))
import categories, consume, content, decode, expand, fetch_docs, holidays, html_tables, markup, members, rules, schema, tabular, \
    version  # noqa: E402
import fetcher                                                                                                             # noqa: E402

TIER_A_HTML_CSV = ("112275", "113697", "131024", "131121", "131156", "131199", "132047")
CACHE = ROOT / "data" / "discover" / "cache"
OUT = ROOT / "data" / "readers"
LLM_CACHE = OUT / "llm_cache"
CAL_LINK = re.compile(r"カレンダー|日程表|収集日程|収集日一覧|calendar|nittei|\d{1,2}月・\d{1,2}月|\d{4}年\d{1,2}月", re.I)
COLLECTION_POINT = re.compile(r"集積所の(?:看板|住所|表示)|集積所\s*所在地|ステーションの表示|看板で(?:必ず)?確認")
READER_A = "code.stage2@1"
MAX_DOCS = 12


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def body_of(url):
    c = fetcher.Cache(CACHE)
    u = fetcher.norm_url(url) or url
    meta = c.meta(u)
    p = c.body_path(u, meta) if meta else None
    if not p:
        return None, meta
    b = p.read_bytes()
    return (gzip.decompress(b) if str(p).endswith(".gz") else b), meta


def select_docs(docs):
    """段 0 の一覧 → 読む資料 (HTML・CSV・Excel で、kinds に calendar か districts があり、キャッシュにあるもの)"""
    out = []
    for x in docs["docs"]:
        if x.get("cache") != "cached":
            continue
        ft = x.get("file_type")
        if ft not in ("html", "csv", "xlsx", "xls"):
            continue
        if not (set(x.get("kinds") or ()) & {"calendar", "districts"} or x.get("best") or x.get("role") == "schedule"):
            continue
        if ft == "html" and x.get("format_class") not in ("html_table", "html_text"):
            continue
        out.append(x)
    out.sort(key=lambda x: (-(x.get("score") or 0), x["url"]))
    return out[:MAX_DOCS]


def printed_docs(docs):
    """同じ自治体の、印字の日付がありそうな資料 (PDF・画像で、リンクの文字か URL がカレンダー・日程表を言うもの)"""
    out = []
    for x in docs["docs"]:
        if x.get("file_type") not in ("pdf", "image"):
            continue
        txt = " ".join((x.get("anchors") or []) + [x.get("title") or "", urllib.parse.unquote(x["url"])])
        if CAL_LINK.search(txt):
            out.append(x)
    return out


def period_of(title_toks, body_toks, file_toks):
    """年の印 → 期間 (valid_from, valid_to, basis の文字) か None (期間の書いていない資料)。
    期間にするのは、題 (title・h1・h2・caption) の年度・期間の印、本文の期間の印 (「2026年4月～2027年3月」)、
    ファイルの名前の年度の印だけ。本文の年度の印 (東秩父「令和8年度より…出し方を変更」) は変わる時期の話のことがあるので、期間にしない"""
    for toks, kinds in ((title_toks, ("fy", "span")), (body_toks, ("span",)), (file_toks, ("fy",))):
        fy = [t for t in toks if t["kind"] in kinds and t.get("from")]
        if fy:
            t = fy[0]
            return t["from"].isoformat(), t["to"].isoformat(), t["text"]
    return None


def region_links(region_root, page_url):
    """本文の範囲のリンク → [(URL, リンクの文字)]"""
    out = []
    for a in region_root.find_all("a"):
        if a.get("href"):
            out.append((urllib.parse.urljoin(page_url, a["href"]), content._clean(a.get_text(" "))))
    return out


class Muni:
    """1 自治体の読みを集めて v0.2 の形にする"""

    def __init__(self, lg, name):
        self.lg, self.name = lg, name
        self.sources, self.periods, self.categories, self.districts, self.schedules, self.exceptions = [], {}, {}, [], [], []
        self.queue, self.notes, self.llm = [], [], {"calls": 0, "cached": 0, "missing": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.compare = collections.Counter()
        self.basis = "residence"
        self.basis_span = None
        self.n_exc = 0
        self.dup = collections.Counter()
        self.seen = {}
        self.contact = {}                       # 地区 → 種類 3 (rule_contact) にしたわけ (新宿の「*」の行)
        self.mode_undecided = []
        self.sentences = {}                     # 資料 → 本文の文 (content.Sentence。使った印つき。出力には書かない)
        self.unconsumed = {}                    # 資料 → 使われなかった但し書き (scan_qualifiers の形。文つき。出力には書かない)

    def period_id(self, per):
        key = (per["valid_from"], per.get("valid_to"))
        for pid, p in self.periods.items():
            if (p["valid_from"], p.get("valid_to")) == key and p["year_evidence"]["ok"] == per["year_evidence"]["ok"]:
                return pid
        pid = f"p{len(self.periods) + 1}"
        per["period_id"] = pid
        self.periods[pid] = per
        return pid

    def category_id(self, label, cat, ref):
        """品目の id。出し方の決まらない品目 (言葉の無い粗大ごみ) は mode を None のまま持ち、出力の categories には入れず、
        待ち行列 (mode) に入れる (申し込み制と書かない)"""
        ol = categories.official_label(label)
        for cid, c in self.categories.items():
            if c["official_label"] == ol and c["mode"] == cat["mode"]:
                return cid
        cid = f"c{len(self.categories) + 1}"
        rec = {"category_id": cid, "official_label": ol, "coarse": cat["coarse"], "mode": cat["mode"], "ref": ref}
        self.categories[cid] = rec
        if cat["mode"] is None:
            self.mode_undecided.append({"category_id": cid, "official_label": ol, "ref": ref})
            self.queue.append({"item_id": f"{self.lg}:mode:{cid}", "kind": "mode", "status": "open", "label": ol,
                               "note": "出し方 (収集・持ち込み・申し込み) を決める言葉が無い"})
        return cid

    def count_llm(self, recs):
        for r in recs:
            self.llm["missing" if r.get("missing") else ("cached" if r.get("cached") else "calls")] += 1
            self.llm["prompt_tokens"] += r.get("prompt_tokens", 0) or 0
            self.llm["completion_tokens"] += r.get("completion_tokens", 0) or 0


def _llm_b(table_el, rd, context, a, m):
    if not a.llm:
        return None, []
    ans, recs = markup.reader_b(table_el, rd, context, LLM_CACHE, offline=a.offline)
    m.count_llm(recs)
    return markup.merge_b(ans), recs


def _csv_b(rows, rd, context, a, m):
    """CSV の見本の行を B に読ませる → (B の答え, B の行 → 元の行の対応)。注の行 (見出しより上の行・1 つのマスだけの行・役目の無い行)
    と途中の見出しの行も見せる (7 回目。A が止めた行を B にも読ませる)"""
    if not a.llm:
        return None, None
    from bs4 import BeautifulSoup
    pick = sorted(set(tabular.sample_rows(rd, rd["header_index"])) | set(tabular.note_rows(rd)))
    html = tabular.as_table_html(rows, pick, rd["header_index"])
    t = BeautifulSoup(html, "html.parser").find("table")
    fake = {"n_head": 1, "grid": [None] * (len(pick) + 1)}
    ans, recs = markup.reader_b(t, fake, context, LLM_CACHE, offline=a.offline, size=40)
    m.count_llm(recs)
    row_map = {i + 1: orig for i, orig in enumerate(pick)}
    return markup.merge_b(ans), row_map


def _text_b(lines, title, a, m):
    """本文の範囲の B (型 C の文の読みと、ページの地区の 2 つ目の読み)"""
    if not a.llm:
        return None
    ans, rec = markup.reader_text_b(lines, title, LLM_CACHE, offline=a.offline)
    m.count_llm([rec])
    return ans


RULE_ROLES = ("category", "rule", "catlist", "catlabel", "weekday")


def rule_cell_elements(readings):
    """日程の表の、規則を読んだマス (見出しの下の、品目・規則の列) の要素の id の集まり"""
    out = set()
    for t, rd in readings:
        if t is None or t == "text":
            continue
        roles, n_head = rd.get("roles") or [], rd.get("n_head", 0)
        for r, row in enumerate(rd.get("grid") or []):
            if r < n_head:
                continue
            for c, cell in enumerate(row):
                if cell is not None and c < len(roles) and roles[c] in RULE_ROLES:
                    out.add(id(cell.el))
    return out


def table_body_cells(readings):
    """日程の表の、見出しの下の行のマス (どの列も。注の行・読まなかった行も) の要素の id。その中の文はページの方針の文にしない
    (規則・名前のマスはその地区・品目、備考などの列はその行、注の行はその表の注として読む。読めなければ但し書き)"""
    out = set()
    for t, rd in readings:
        if t is None or t == "text":
            continue
        for r, row in enumerate(rd.get("grid") or []):
            if r >= rd.get("n_head", 0):
                out |= {id(c.el) for c in row if c is not None}
    return out


def _pos_texts(rd, poss):
    """表の位置 [(行, 列, 文字)] → (文字の切れ端のリスト (改行で分ける。同じマスは 1 つ), マスの要素の id の集まり)"""
    grid = rd.get("grid") or []
    texts, ids, seen = [], set(), set()
    for r, i, text in poss:
        c = grid[r][i] if 0 <= r < len(grid) and 0 <= i < len(grid[r]) else None
        k = id(c) if c is not None else (r, i)
        if k in seen:
            continue
        seen.add(k)
        if c is not None:
            ids.add(id(c.el))
        # 「-」「ー」など無しのしるし・区切りだけの切れ端は注が無いのと同じ (空のマス)
        texts += [x.strip() for x in (text or "").split("\n")
                  if x.strip() and not rules.is_none_mark(x) and not PUNCT_ONLY.fullmatch(content.squash(x))]
    return texts, ids


def _table_notes(rd):
    """表の注の行 (html_tables.read_grid の notes) → (文字の切れ端のリスト, マスの要素の id の集まり)"""
    return _pos_texts(rd, [(r, i, n["text"] if k == 0 else "") for n in rd.get("notes") or [] for k, (r, i) in enumerate(n["cells"])])


def _in_cells(sentence, cell_ids):
    """文が、規則を読んだマスの中にあるか (そのマスの文は cell_notes がその地区・品目だけに読む)。要素の同一性で見る"""
    el = sentence.line.element
    if el is None:
        return False
    return id(el) in cell_ids or any(id(p) in cell_ids for p in el.parents)


# 表の見出しの行のマスで、列の役目の言葉 (html_tables.column_roles の地区・丁目・品目の列の札・規則の列の札・リンク・索引) と区切りだけで
# できているもの (「地区」「町名」「収集曜日」「地域別カレンダー」)。表の読みが列の役目として読んだ文字 (閉じた並び。1 字でも残れば読んでいない)
HEAD_ROLE_WORDS = re.compile(r"町名|地区名?|区域|地域名?|町丁目|町丁|地名|住所|町会|自治会|行政区|集落|お住まいの?|収集区域|丁目|番地|等|"
                             r"50音|五十音|あ行|頭文字|よみ|読み|種別|品目|分別|種類|区分|指定日|収集日|収集の日|収集曜日|曜日|日程|回収日|"
                             r"出す日|カレンダー|PDF|pdf|calendar|ごみの?|資源の?|番号|一覧|別|名|の")


def head_label_cells(readings):
    """日程の表の見出しの行の、列の役目の札だけのマスの要素の id (品目の見出しは名前のマス note_cells で見る)"""
    out = set()
    for t, rd in readings:
        if t is None or t == "text":
            continue
        roles, grid = rd.get("roles") or [], rd.get("grid") or []
        # 見出しの行と、表の途中の見出しの行 (7 回目。そのあとの行の規則は読まないので、札の言葉だけのマスは読んだことにしてよい)
        for r in list(range(min(rd.get("n_head", 0), len(grid)))) + [x for x in rd.get("mid_heads") or [] if x < len(grid)]:
            for i, c in enumerate(grid[r]):
                if c is None or i >= len(roles) or roles[i] in ("category", "other"):
                    continue
                if PUNCT_ONLY.fullmatch(HEAD_ROLE_WORDS.sub("", content.squash(c.text))):
                    out.add(id(c.el))
    return out


def _in_spans(sentence, spans):
    """文が、型 C の行の所 (html_tables.read_text の spans・listed_spans: 同じ行 (Line) の start〜end) の中にあるか。
    行の同一性と行の中の位置で見る (文字の一部の一致では見ない)"""
    return any(sp.get("line") is sentence.line and sp["start"] <= sentence.start and sentence.end <= sp["end"] for sp in spans)


def _rule_cell_ids(rd, row, label):
    """その行・品目の規則を読んだマスの要素の id。曜日の行の表 (三鷹) は、規則にした曜日の列のマス"""
    grid = rd.get("grid") or []
    pos = (row.get("cells") or {}).get(label)
    pos = [pos] if isinstance(pos, tuple) else list(pos or [])
    if rd["orientation"] == "weekday_rows" and "weekday" in (rd.get("roles") or []):
        wc = rd["roles"].index("weekday")
        pos = [(r, wc) for r, _ in pos]
    out = set()
    for r, i in pos:
        c = grid[r][i] if r < len(grid) and i < len(grid[r]) else None
        if c is not None:
            out.add(id(c.el))
    return out


# 地区・品目の名前のマスの読み残し (§3.8)。閉じた型 (whitelist) で読む: マスの文字から、読んだ名前 (地区の文法 members の名前を
# 丸ごと・品目の言葉) と形の決まった条件 (範囲・除く・一部・班・施設・線の片側・上記以外) を除き、残りが句読点・かっこ・空白だけの
# ときだけマスごと使ったことにする。1 字でも残れば (「（月2回）」「（水曜日に収集）」「第2週」「毎週」「（夏山シーズン中は毎週）」・
# ※の無い注)、残りの切れ端はそのマスの地区 (行) か品目だけを止める但し書き (ページの方針の文にも、資料すべての但し書きにもしない)。
# 夏見・冬木・祝園は members の読んだ名前の字なので、印の字を隠す決まりは使わない
PUNCT_ONLY = re.compile(r"[\s\x00、。,，.．・･:：;；/／\-－‐−–—―ー~〜～()（）\[\]［］{}｛｝〔〕《》〈〉≪≫<>＜＞「」『』【】]*")
BRACKET = re.compile(r"[(（\[［〔《〈≪<＜「『【]([^(（\[［〔《〈≪<＜「『【)）\]］〕》〉≫>＞」』】]*)[)）\]］〕》〉≫>＞」』】]")
PIECE_SPLIT = re.compile(r"\x00|[()（）\[\]［］〔〕《》〈〉≪≫<>＜＞「」『』【】。]|(?=[※*])")
NAME_CH = r"[一-鿿〇々〆ゝゞヽヾヵヶぁ-ゖァ-ヺー]"
PLACE_RUN = re.compile(NAME_CH + r"+")
NOTE_START = re.compile(r"\s*(?:[※*]|注)")                                                    # 名前のあとの注の始まり (NFKC のあと)
NTH_PREFIX = re.compile(r"^\s*第?\s*\d{1,2}(?:\s*[・,、･]\s*\d{1,2})*(?:回目)?(?![\d月日/])")    # 曜日の表の品目の行の頭の回 (規則に使う)

# 地区の名前の形 (members の名前 name_verbatim の全体が、どれかに合うときだけ名前として読む)
PLACE_NAMES = (
    ("地名の字", re.compile(NAME_CH + r"+")),                                          # 夏見・祝園・大字浜崎・あけぼの町・中の島
    ("番号の地区", re.compile(NAME_CH + r"*第?\d{1,3}(?:区|地区|班|組|町内会|自治会)")),  # 第5区・東1区・本町3班
    ("英字の地区", re.compile(r"[A-Z]{1,2}(?:地区|区|コース)?")),                       # A地区・Bコース
    ("コースの番号", re.compile(r"(?:コース|ルート)(?:番号)?[\dA-Z]{1,3}|[\dA-Z]{1,3}(?:コース|ルート)")),  # コース番号1・3コース
    ("上記以外", members.REMAINDER),                                                    # 上記以外・その他以外 (members の残りの地区)
)
# 名前の形でも、日程・但し書きの言葉がくっついたもの (「東町隔週」「東町は毎週」「東町のみ」) は名前にしない (止める側にだけ働く)
NOT_PLACE = re.compile(r"曜|週|毎|隔|[\d〇一二三四五六七八九十]+回|収集|休み|休止|祝日|祝祭|祭日|振替|年末|年始|夏[期季場]|冬[期季場]|"
                       r"期間|シーズン|(?<=[一-鿿〇々ァ-ヺー])[はにをでも](?=[一-鿿〇々ァ-ヺー])|(?:のみ|だけ|など)$|^[春夏秋冬祝祭休注]$")
# 地区の名前のあとの数の並び。どれかの全体に合い、しるしの条件を members が読んだときだけ読んだ部分にする (名前の一部に残った数は読み残し)
TAILS = (
    ("丁目・番", re.compile(r"(?:[\d〇一二三四五六七八九十ニ]+(?:丁目|番地|番|号棟|号|街区)?|[~〜～\-－―‐−ー]|から|まで|末|の?一部|[・,、，])+"),
     {"range"}),                                                                        # 1~3丁目・2丁目1~6番・7番の一部、8~21番
    ("班", re.compile(r"\d+班"), {"han"}),                                              # 東町3班
    ("全域", re.compile(r"全域|全部|全て|すべて"), None),                                # 「明石町 全域」「深大寺全域」
    ("一部", re.compile(r"の?一部"), None),                                             # 「大字浜崎の一部（県道和光志木線北側）」
)
# 地区のマスのかっこの中で、形の決まった条件として読むもの (members._paren_conditions の読みの種類ごと。_district_paren_ok)
PARTIAL_TEXT = re.compile(r"(?:[\d〇一二三四五六七八九十・,、~〜～\-]+(?:番地|番|号|丁目))?の?一部")       # （7・8・10～12番一部）（一部）
COMMUNITY_NAME = re.compile(r"(?:町内会|自治会|町会|区会|振興会|行政区|自治区|団地|住宅|マンション|ハイツ|コーポ|アパート|寮|社宅|"
                            r"宿舎|官舎)$")                                             # 《吉津町内会》《駅前マンション》 (中身の名前)
PLACE_SUFFIX = re.compile(r"(?:町|丁目|地区|地域|区域|区|字|団地|住宅|町内会|自治会)$")   # （冬木地区を除く）の除く地区の名前

# 品目の名前の言葉。品目のマスの行は、頭からこの言葉と区切りだけで読める所までを名前として読む (辞書の分類・ほかの品目の言葉と、下の言葉)
CAT_FILL = re.compile(r"ごみの種類|資源の種類|ごみ|ゴミ|資源|類|物|等|など|その他|製|廃|古|無色|茶|容器|包装|マーク|回収|くず|種類|"
                      r"種別|品目|分別|区分")
CAT_WORDS = [rx for rx, _ in categories.COARSE_RULES] + [categories.OTHER_ITEMS, categories.STATION, CAT_FILL]
CAT_SEP = re.compile(r"[\x00・･、,，/／]+")


consume.register("name_cell", {"place:" + n for n, _ in PLACE_NAMES} | {"tail:" + n for n, _, _ in TAILS} |
                 {"punct", "district_paren", "category_list", "category_words", "header_rule", "nth_prefix", "cell_pieces_scoped"})


def _place_ok(name):
    """地区の名前 (NFKC) が名前の形のどれかに丸ごと合うか → 合った形の名前 (PLACE_NAMES の名前) か None
    (空白・かっこ・※ を含む名前は、どの形にも合わない)"""
    for label, rx in PLACE_NAMES:
        if rx.fullmatch(name) and (label == "上記以外" or not NOT_PLACE.search(name)):
            return label
    return None


def _take_place(name):
    """名前を読んだ部分にしてよいか (名前の形に合い、関門を通ったとき)"""
    label = _place_ok(name)
    return bool(label) and bool(consume.take("name_cell", "place:" + label, name))


def _pieces(texts):
    """読み残しの文字 → 切れ端 (NFKC・空白なし。かっこ・※ の前で分け、句読点・かっこ・空白だけのものは捨てる)"""
    out = []
    for t in texts:
        for p in PIECE_SPLIT.split(content.squash(t)):
            if p and not PUNCT_ONLY.fullmatch(p) and p not in out:
                out.append(p)
    return out


def _brackets(t, ok):
    """かっこ書きを内側から 1 つずつ見る: ok(中身) なら読んだ部分、でなければ読み残し → (かっこを \\x00 にした文字, 読んだ, 残り)"""
    frags, rest = [], []

    def one(mm):
        inner = mm.group(1).replace("\x00", "")
        if inner:
            tpl = "punct" if PUNCT_ONLY.fullmatch(inner) else ok(inner)
            (frags if consume.take("name_cell", tpl, inner) else rest).append(inner)
        return "\x00"
    while True:
        t2 = BRACKET.sub(one, t)
        if t2 == t:
            return t, frags, rest
        t = t2


def _district_paren_ok(inner):
    """地区のマスのかっこの中が、形の決まった条件だけか (範囲・除く・一部・班・施設・線の片側・上記以外と、町内会・施設の名前)
    → 見本の名前 "district_paren" か None"""
    return "district_paren" if _district_paren_conds(inner) else None


def _district_paren_conds(inner):
    """_district_paren_ok の中身 (条件のどれもが形の決まったものか)"""
    t = content.squash(inner)
    for c in members._paren_conditions(t):
        k = c.get("kind")
        if k in ("remainder", "enclave", "han"):
            continue
        if k == "except" and all(o.get("kind") == "range" for o in c.get("of") or []):
            continue
        if k == "except":                                   # 名前を除く (「冬木地区を除く」): 名前の形で、地区の終わりの字のときだけ
            body = "".join(o.get("text") or "" for o in c.get("of") or [])
            if _place_ok(body) and PLACE_SUFFIX.search(body):
                continue
        elif k == "facility" and _place_ok(c.get("name") or ""):
            continue
        elif k == "line_side" and (not c.get("line") or _place_ok(c["line"])):
            continue
        elif k == "partial" and PARTIAL_TEXT.fullmatch(t):
            continue
        elif k == "note" and _place_ok(t) and COMMUNITY_NAME.search(t):
            continue
        return False
    return True


def _spaced(s):
    """NFKC、ゼロ幅の字を除き、空白の並びを 1 つにする (名前の中の空白「東町 夏」を名前の字と区別するため、空白は残す)"""
    return re.sub(r"\s+", " ", re.sub(r"[​‌‍﻿]", "", content.nfkc(s))).strip()


def _member_read(x):
    """地区の中身 1 つ → (読んだ部分, 読み残し)。読んだ部分は、名前 (name_verbatim を丸ごと。名前の形のときだけ)、名前のあとの数の並び
    (members が範囲・班を読んだときだけ)、形の決まったかっこ書き。名前が読めない・形に合わなければ、かっこの外の全体が名前の形の
    ときだけ名前にし、それも合わなければ ※・注の前の名前の字の並びだけを名前にして、あとは読み残し"""
    t, frags, rest = _brackets(_spaced(x.get("text") or ""), _district_paren_ok)
    core = _spaced(t.replace("\x00", ""))
    name = _spaced((x.get("extra") or {}).get("name_verbatim") or "")
    exact = bool(x.get("unit") != "unparsed" and name and core.startswith(name) and _take_place(name))
    if exact:
        frags.append(name)
        tail = core[len(name):]
    elif _take_place(core):                                 # 名前が無い・合わない中身 (コース名・ほかの読みの中身): かっこの外の全体
        frags.append(core)
        tail = ""
    else:                                                   # 「東町 ※祝日は収集しません」: ※・注の前の名前だけ (読み残しの文字を短くするだけ)
        mm = PLACE_RUN.match(core)
        head = mm.group(0) if mm and NOTE_START.match(core, mm.end()) and _take_place(mm.group(0)) else ""
        if head:
            frags.append(head)
        tail = core[len(head):]
    tail = content.squash(tail)
    if tail and not PUNCT_ONLY.fullmatch(tail):
        kinds = {c.get("kind") for c in x.get("conditions") or []}
        label = next((lb for lb, rx, need in TAILS if rx.fullmatch(tail) and (need is None or kinds & need)), None) if exact else None
        if label and consume.take("name_cell", "tail:" + label, tail):
            frags.append(tail)
        else:
            rest.append(tail)
    return frags, rest


def _bare(s):
    return re.sub(r"[、,，;；・･]", "", content.squash(s))


def district_rest(text, mem):
    """地区のマスの文字と地区の文法の中身 → (読んだ部分のリスト, 読み残しの切れ端のリスト)。どの中身にも入らなかった文字も読み残し"""
    frags, rest = [], []
    left = _bare(text)
    for x in mem or []:
        f, r = _member_read(x)
        frags += f
        rest += r
        mt = _bare(x.get("text") or "")
        if mt and mt in left:
            left = left.replace(mt, "\x00", 1)
    return frags, _pieces(rest + [left])


def _marks(text):
    """(前の名前) 地区のマスの文字だけ → 読み残しの切れ端 (空でなければ、その地区を止める)"""
    return district_rest(text, members.parse_cell(text))[1]


def _cat_prefix(s):
    """品目の名前の文字 (NFKC・空白なし) → 頭から品目の言葉と区切りだけで読める長さ (言葉は長く当たるものを取る)"""
    i = 0
    while i < len(s):
        ends = [m.end() for rx in CAT_WORDS + [CAT_SEP] for m in [rx.match(s, i)] if m and m.end() > i]
        if not ends:
            break
        i = max(ends)
    return i


def _cat_list_ok(inner):
    """品目のかっこ書き (「（無色びん・茶びん）」「（燃えるごみ）」) が品目の言葉だけか → 見本の名前 "category_list" か None"""
    t = content.squash(inner)
    return "category_list" if _cat_prefix(t) == len(t) else None


def label_rest(text, kind):
    """品目の名前のマス (行ごと) → (読んだ部分, 読み残しの切れ端)。見出し (category) は規則に使った切れ端 (三郷「(第1・3週)」) も、
    曜日の表の品目の列 (catlist) は行の頭の回 (三鷹「第2・4」) も読んだ部分。名前は頭から品目の言葉で読める所まで"""
    frags, rest = [], []
    for line in content.nfkc(text).split("\n"):
        t = content.squash(line)
        if kind == "catlist":
            mm = NTH_PREFIX.match(t)
            if mm and consume.take("name_cell", "nth_prefix", mm.group(0)):
                frags.append(mm.group(0))
                t = t[mm.end():]
        if kind == "category":
            for f in html_tables.HEADER_RULE.findall(t):
                if consume.take("name_cell", "header_rule", f):
                    frags.append(f)
                    t = t.replace(f, "\x00")
        t, f, r = _brackets(t, _cat_list_ok)
        frags += f
        rest += r
        n = _cat_prefix(t)
        if t[:n].replace("\x00", "") and not consume.take("name_cell", "category_words", t[:n]):
            n = 0                                           # 関門を通らなければ、品目の名前も読み残し (その品目を止める)
        if t[:n].replace("\x00", ""):
            frags.append(t[:n].replace("\x00", ""))
        rest.append(t[n:])
    return frags, _pieces(rest)


# 品目の名前の読み残し (「燃やせるごみ（祝日は休み）」「燃やすごみ（東町は火曜日・金曜日）」「東町の燃やすごみ」) は別の品目にしない:
# 頭の品目の言葉 (もとの品目) に寄せ、同じもとの品目の名前が同じ読みにあれば 1 つにまとめる。読み残しは、もとの品目の但し書きとして
# 資料の中のその品目の系列すべてを止める (地区の名前が入れば地区ごとの規則なので、どの地区かは決めない)。回数の書き添えだけ
# (「（週2回）」) で、同じもとの品目がほかに無いものは今までどおり (その系列の規則と回数が合えば使う)
PLACE_PREFIX = re.compile(r"^(" + NAME_CH + r"{1,12}?)の(.+)$")
FREQ_ONLY = re.compile(r"\(?[週月]\d回\)?")
LEAD_MARKS = re.compile(r"^[※*＊・●○◆◇■□☆★◎]+")
GOMI_WORD = re.compile(r"ごみ|ゴミ|資源")


def _cat_ok(name):
    return bool(name) and categories.is_category_header(name) and categories.coarse(name) != ["other"]


def label_base(label, kind="text"):
    """品目の名前 → (もとの品目の名前 (NFKC・空白なし) か None, 読み残しの切れ端)。読み残しが無ければ (名前そのもの, [])"""
    pieces = label_rest(label, kind)[1]
    if not pieces:
        return label, []
    t = LEAD_MARKS.sub("", content.squash(label))
    mm = PLACE_PREFIX.match(t)
    if mm and _cat_prefix(mm.group(2)) == len(mm.group(2)) and not _cat_prefix(mm.group(1)) and _cat_ok(mm.group(2)):
        return mm.group(2), pieces                          # 「東町の燃やすごみ」
    if kind == "category":
        t = html_tables.HEADER_RULE.sub("\x00", t)
    elif kind == "catlist":
        t = NTH_PREFIX.sub("", t)
    while True:
        t2 = BRACKET.sub("\x00", t)
        if t2 == t:
            break
        t = t2
    n = _cat_prefix(t)
    base = t[:n].replace("\x00", "").strip("・･、,/／")
    return (base if _cat_ok(base) else None), pieces


def _merge_rules(ps, table):
    """寄せた品目の規則の読み → 1 つ。表 (曜日の行・品目の行) は行ごとの規則と同じくまとめ、型 C の文は違えば multiple"""
    ps = [p for p in ps if p is not None]
    if len(ps) <= 1:
        return ps[0] if ps else None
    if table:
        return html_tables._combine(ps)
    if len({html_tables.json_key(p) for p in ps}) == 1:
        return ps[0]
    return rules.Parsed(" / ".join(p.text for p in ps), [i for p in ps for i in p.irs], "multiple",
                        rest="/".join(p.rest for p in ps if p.rest), notes=["読み残しのある品目の名前を、もとの品目に寄せた (規則が違う)"])


def merge_labels(rd):
    """1 つの読みの品目の名前を、もとの品目に寄せる (rd を書き換える) → [(もとの品目の名前, [但し書き])] (資料の中のその品目すべてを止める)。
    地区の行の表 (品目の列) は名前を変えず、読み残しを但し書きにするだけ"""
    ori = rd["orientation"]
    kind = {"district_rows": "category", "category_rows": "catlabel", "weekday_rows": "catlist", "text_sections": "text"}.get(ori)
    if not kind:
        return []
    info = {}
    for label in list(rd.get("categories") or {}):
        base, pieces = label_base(label, kind)
        freq = bool(pieces) and all(FREQ_ONLY.fullmatch(p) for p in pieces)
        info[label] = (content.squash(base) if base else None, pieces, freq)
    groups = collections.defaultdict(list)
    wide = []
    for label, (key, pieces, freq) in info.items():
        if key:
            groups[key].append(label)
        elif pieces and not freq and (categories.coarse(label) != ["other"] or GOMI_WORD.search(content.nfkc(label))):
            # もとの品目が決まらない: 大まかな分類の同じ品目すべて (「東町のごみ」など分類の決まらないごみは資料すべて)。
            # ごみの言葉の無い注の行 (曜日の表のマスの「※祝日は収集しません」) は、そのマスの品目だけ (note_cells)
            wide.append((("coarse",) + tuple(categories.coarse(label)), [_qual(p) for p in pieces]))
    for key, labs in groups.items():
        noted = [x for x in labs if info[x][1] and (not info[x][2] or len(labs) > 1)]
        if not noted:
            continue
        wide.append((key, [_qual(p) for x in noted for p in info[x][1]]))
        if ori == "district_rows":
            continue
        target = next((x for x in labs if not info[x][1]), None) or next((x for x in labs if content.squash(x) == key), None) or key
        moved = [x for x in labs if x != target]
        if not moved:
            continue
        cats = rd["categories"]
        old = [cats[x] for x in labs if x in cats]
        cats[target] = {**old[0], **categories.category(target, " ".join(labs))} if target not in cats else cats[target]
        for x in moved:
            cats.pop(x, None)
        rd.setdefault("label_origins", {}).setdefault(target, []).extend(labs)
        for row in rd["rows"]:
            have = [x for x in labs if x in row["rules"]]
            if have:
                row["rules"][target] = _merge_rules([row["rules"].pop(x) for x in have], ori != "text_sections")
            pos = []
            for x in labs:
                c = row["cells"].pop(x, None)
                pos += [c] if isinstance(c, tuple) else list(c or [])
            if pos:
                row["cells"][target] = pos
            if isinstance(row.get("other_cells"), dict):
                oth = [c for x in labs for c in row["other_cells"].pop(x, [])]
                if oth:
                    row["other_cells"][target] = oth
            if ori != "text_sections":
                continue
            row["spans"][target] = [sp for x in labs for sp in row["spans"].pop(x, [])]
            pq = [h for x in labs for h in (row.get("page_quals") or {}).pop(x, [])]
            if pq:
                row["page_quals"][target] = pq
            ls = [x for x in labs if x in row.get("listed", {})]
            if ls:
                days = [row["listed"].pop(x) for x in ls]
                wds = [row["listed_wd"].pop(x, None) for x in ls]
                conflict = [row["listed_conflict"].pop(x) for x in ls if x in row["listed_conflict"]]
                if len({tuple(sorted(d)) for d in days}) > 1:
                    conflict.append("読み残しのある品目の名前を寄せた日付の一覧が、もとの品目の一覧と違う (足し合わせない)")
                row["listed"][target], row["listed_wd"][target] = days[0], wds[0] or [None] * len(days[0])
                if conflict:
                    row["listed_conflict"][target] = conflict[0]
                row["listed_spans"][target] = [sp for x in ls for sp in row["listed_spans"].pop(x, [])]
                row["listed_rest"][target] = "/".join(r for r in (row["listed_rest"].pop(x, "") for x in ls) if r)
    return wide


def heading_notes(rd):
    """型 C の但し書きにする見出し (html_tables.read_text の heading_quals: 地名と読めない見出し「祝日のある週」・版や期間の見出し
    「旧日程」) → [(品目の鍵, [但し書き])]。鍵は merge_labels の読み残しと同じ (資料の中のその品目の系列すべてを止める)"""
    out = []
    for label, hs in (rd.get("heading_quals") or {}).items():
        base = label_base(label, "text")[0]
        key = content.squash(base) if base else ("coarse",) + tuple(categories.coarse(label))
        out.append((key, [_qual(h) for h in hs]))
    return out


def _label_hit(key, label, cat):
    """名前の読み残しの鍵 (もとの品目の名前か ("coarse", 分類…)) が、資料の品目 (label・cat) に当たるか"""
    if isinstance(key, tuple):
        ks = set(key[1:]) - {"other"}
        return not ks or bool(ks & set(cat.get("coarse") or []))
    base = label_base(label, "text")[0]
    return key in {content.squash(label)} | ({content.squash(base)} if base else set())


def _rest_pieces(text, frags):
    """文 (名前のマスの中の文) から、マスの読んだ部分を除いた残りの切れ端 (NFKC・空白なし) のリスト"""
    t = content.squash(text)
    for f in sorted({content.squash(f) for f in frags if content.squash(f)}, key=len, reverse=True):
        t = t.replace(f, "\x00")
    return _pieces([t])


def _covered(piece, pieces):
    """文の切れ端が、マスの読み残しの切れ端 (と句読点) だけでできているか (マスと文で切れ目が違っても、同じ文字なら済み)"""
    if any(piece in x for x in pieces):
        return True
    t = piece
    for x in sorted(pieces, key=len, reverse=True):
        t = t.replace(x, "\x00")
    return bool(PUNCT_ONLY.fullmatch(t))


def _qual(piece):
    return {"qualifier_id": expand.rest_qualifier_id(piece), "months": sorted(content.named_months(piece)), "text": piece}


def note_cells(readings, lines, lg):
    """地区・品目の名前のマス (表の地区の列・品目の見出し・品目の列、CSV の同じ所、型 C の地区の見出しと行の頭の品目) の読み残し →
    (要素の id → {"frags", "pieces"}, {(読みの番号, 行): [但し書き]}, {(読みの番号, 品目): [但し書き]})"""
    elems, by_row, by_label = {}, {}, {}

    def put(d, key, qs):
        lst = d.setdefault(key, [])
        lst += [q for q in qs if q["qualifier_id"] not in {x["qualifier_id"] for x in lst}]

    def mark(el, frags, pieces):
        rec = elems.setdefault(id(el), {"frags": set(), "pieces": set()})
        rec["frags"] |= set(frags)
        rec["pieces"] |= {content.squash(p) for p in pieces}

    heads = {content.nfkc(ln.text).strip() for ln in lines if ln.is_heading}
    for ri, (t, rd) in enumerate(readings):
        ori, roles, n_head = rd["orientation"], rd.get("roles") or [], rd.get("n_head", 0)
        grid = rd.get("grid") if t not in (None, "text") else None
        gcell = lambda r, i: grid[r][i] if grid and r < len(grid) and i < len(grid[r]) else None
        for row in rd["rows"]:                              # 地区のマス (ページの題・自治体の地区は、ページの文として読む)
            if ori == "district_rows":
                mem, els = row["members"], [c.el for i, x in enumerate(roles) if x in ("district", "chome")
                                            for c in [gcell(row["row"], i)] if c is not None]
            elif t == "text" and row.get("district_from") == "heading" and row["district_text"]:
                mem = row.get("members") or members.parse_cell(row["district_text"], lg)
                els = [ln.element for ln in lines if ln.is_heading and ln.text == row["district_text"] and ln.element is not None]
            elif row.get("district_from") == "column" and row["district_text"]:
                # 品目の行・曜日の行の表の地区・丁目の列 (7 回目。地区の値ごとの行): 地区の列のマスと同じに読む
                mem = row.get("members") or members.parse_cell(row["district_text"], lg)
                els = list({id(c.el): c.el for r, i in row.get("district_cells") or [] for c in [gcell(r, i)] if c is not None}.values())
            else:
                continue
            frags, pieces = district_rest(row["district_text"], mem)
            put(by_row, (ri, row["row"]), [_qual(p) for p in pieces])
            # 型 C の地区の見出しの下に表もあれば、見出しの注は表の系列にも掛かりうる → 洗い出しにも残す (済みにしない)
            shared = t == "text" and any(ln.in_table and row["district_text"] in ln.heading_path for ln in lines)
            for el in els:
                mark(el, frags, [] if shared else pieces)
        cells = {}                                          # 品目のマス → (マス, 種類, 品目)
        if ori == "district_rows":
            for i, x in enumerate(roles):
                if x != "category":
                    continue
                label = rd["header"][i]
                if grid is None:                            # CSV: 見出しの文字
                    put(by_label, (ri, label), [_qual(p) for p in label_rest(label, "category")[1]])
                for h in range(n_head if grid is not None else 0):
                    c = gcell(h, i)
                    if c is not None:
                        cells.setdefault(id(c.el), [c, "category", set()])[2].add(label)
        elif ori in ("category_rows", "weekday_rows"):
            kind = "catlabel" if ori == "category_rows" else "catlist"
            col = roles.index("catlabel") if ori == "category_rows" or "catlist" not in roles else roles.index("catlist")
            for label, co in [(k, v) for row in rd["rows"] for k, v in row["cells"].items()]:    # 地区ごとの行もすべて
                if grid is None:
                    for orig in (rd.get("label_origins") or {}).get(label, [label]):
                        put(by_label, (ri, label), [_qual(p) for p in label_rest(orig, kind)[1]])
                    continue
                for r, _ in (co if isinstance(co, list) else [co]):
                    c = gcell(r, col)
                    if c is not None:
                        cells.setdefault(id(c.el), [c, kind, set()])[2].add(label)
        elif t == "text":                                   # 行の頭の品目 (【燃やすごみ】・「燃やすごみ：」)。見出しの品目はページの文として読む
            for label in rd["categories"]:
                for orig in (rd.get("label_origins") or {}).get(label, [label]):      # もとの品目に寄せた名前は、寄せる前の名前で
                    if content.nfkc(orig).strip() not in heads:
                        put(by_label, (ri, label), [_qual(p) for p in label_rest(orig, "text")[1]])
        for c, kind, labels in cells.values():
            frags, pieces = label_rest(c.text, kind)
            for label in labels:
                put(by_label, (ri, label), [_qual(p) for p in pieces])
            mark(c.el, frags, pieces)
    return elems, by_row, by_label


def _note_rec(el, elems):
    """要素 (か親) が名前のマスなら、その記録"""
    if el is None:
        return None
    for x in [el, *el.parents]:
        if id(x) in elems:
            return elems[id(x)]
    return None


# ---- 表の中の参照の印と、表の外の注 (7 回目) ----
# マスの名前・規則のあとの「※」「※1」「*」「★」「注1」、かっこの中の「（注1）」「（※）」(印のあとに文が続く「※祝日は休み」はマスの注で、
# 参照の印でない。印だけのマス (「○」「●」の収集のしるし) は前に字が無いので印にしない)
_SYMS = r"[※*＊★☆◎●○◆◇■□△▲†‡＃#]"
REF_MARK = re.compile(r"(?<=\S)\s*(?P<s1>" + _SYMS + r")\s*(?P<n1>\d{1,2}(?![\d月日/年時回]))?\s*(?=$|[)）\]］】>＞〉》、,・/\s])|"
                      r"[(（【\[［<＜〈《]\s*(?P<s2>" + _SYMS + r"|注)\s*(?P<n2>\d{1,2}(?![\d月日/年時回]))?\s*[)）】\]］>＞〉》]|"
                      r"(?<=\S)\s*注\s*(?P<n3>\d{1,2})(?![\d月日/年時回])\s*(?=$|[)）\]］】>＞〉》、,・/\s])")
# 表の外の文の頭の印 (「※祝日も収集します」「※1 …」「注1：…」「（注1）…」「＜注＞…」「* …」「★…」。「注意」は印でない)
FOOT_LEAD = re.compile(r"^\s*[(（【\[［<＜〈《]?\s*(?:(?P<s>" + _SYMS + r")\s*(?P<n>\d{1,2}(?![\d月日/年時回]))?|"
                       r"注\s*(?P<n2>\d{1,2}(?![\d月日/年時回]))?(?=\s*[)）】\]］>＞〉》:：.．\s]|$))")


def _mark_key(sym, num):
    return ({"＊": "*", "＃": "#"}.get(sym, sym), num or "")


def ref_marks_in(text):
    """マス・行の文字の中の参照の印 → {(印, 番号)}"""
    out = set()
    for line in content.nfkc(text or "").split("\n"):
        for mm in REF_MARK.finditer(line.strip()):
            sym = mm.group("s1") or mm.group("s2") or "注"
            out.add(_mark_key(sym, mm.group("n1") or mm.group("n2") or mm.group("n3")))
    return out


def foot_key(text):
    """表の外の文の頭の印 → (印, 番号) か None"""
    mm = FOOT_LEAD.match(content.nfkc(text or ""))
    if not mm:
        return None
    return _mark_key(mm.group("s") or "注", mm.group("n") or mm.group("n2"))


def _series_of_positions(rd):
    """表の読み → {(行, 列): {(行の番号, 品目)}} (そのマスが名前・規則・備考になった系列)"""
    pos = collections.defaultdict(set)
    roles, n_head, ori = rd.get("roles") or [], rd.get("n_head", 0), rd["orientation"]
    for rec in rd.get("rows") or []:
        labels = list(rec.get("rules") or {})
        rows_ = rec.get("src_rows") or [rec["row"]]
        for label, co in (rec.get("cells") or {}).items():
            for p in ([co] if isinstance(co, tuple) else list(co or [])):
                if isinstance(p, tuple):
                    pos[p].add((rec["row"], label))
                    if ori == "category_rows" and "catlabel" in roles:
                        pos[(p[0], roles.index("catlabel"))].add((rec["row"], label))
        dcells = rec.get("district_cells") or ([(rec["row"], i) for i, x in enumerate(roles) if x in ("district", "chome")]
                                               if ori == "district_rows" else [])
        if ori == "weekday_rows" and "weekday" in roles:
            dcells = list(dcells) + [(r, roles.index("weekday")) for r in rows_]
        for p in dcells:
            pos[p] |= {(rec["row"], label) for label in labels}
        oc = rec.get("other_cells") or []
        for label, lst in (oc.items() if isinstance(oc, dict) else [(None, oc)]):
            for r, i, _ in lst:
                pos[(r, i)] |= {(rec["row"], x) for x in ([label] if label else labels)}
    if ori == "district_rows":                              # 品目の見出しのマス: その品目のすべての行
        for i, x in enumerate(roles):
            if x != "category":
                continue
            label = rd["header"][i]
            for h in range(n_head):
                pos[(h, i)] |= {(rec["row"], label) for rec in rd.get("rows") or [] if label in (rec.get("rules") or {})}
    return pos


def table_marks(readings):
    """日程の表・型 C の行の参照の印 → ({(印, 番号): {(読みの番号, 行の番号, 品目)}}, 系列の決まらない印の集まり)。
    印のマスが注の行・見出しの総称のマスなど、どの系列にもならないマスにあれば、その印の系列は決まらない"""
    marks, unknown = collections.defaultdict(set), set()
    for ri, (t, rd) in enumerate(readings):
        if t == "text":
            for rec in rd["rows"]:
                for label in set(rec.get("spans") or {}) | set(rec.get("listed_spans") or {}):
                    texts = [label] + [sp["text"] for d in (rec.get("spans") or {}, rec.get("listed_spans") or {})
                                       for sp in d.get(label, [])]
                    for k in set().union(*[ref_marks_in(x) for x in texts]):
                        marks[k].add((ri, rec["row"], label))
            continue
        grid = rd.get("grid")
        if t is None or not grid:
            continue
        pos = _series_of_positions(rd)
        where = collections.defaultdict(list)
        for r, row in enumerate(grid):
            for i, c in enumerate(row):
                if c is not None:
                    where[id(c)].append((r, i, c))
        for lst in where.values():
            keys = ref_marks_in(lst[0][2].text)
            if not keys:
                continue
            sc = set().union(*[pos.get((r, i), set()) for r, i, _ in lst])
            for k in keys:
                if sc:
                    marks[k] |= {(ri, a, b) for a, b in sc}
                else:
                    unknown.add(k)
    return marks, unknown


def footnotes(sents, readings):
    """表の外の、表の参照の印で始まる文 (と、同じ行のあとの文) → {id(文): 系列の集まり か None (系列が決まらない)}。
    これらはページの方針の文にしない (印の無い系列に当てない)。系列が決まれば、その系列だけを止める但し書き。決まらなければ
    (印の番号が表に無い・印のマスが系列にならない) 使われなかった但し書き (資料の品目すべてを止める)"""
    marks, unknown = table_marks(readings)
    syms = {s for s, _ in marks} | {s for s, _ in unknown}
    out = {}
    if not syms:
        return out
    by_line = collections.defaultdict(list)
    for s in sents:
        if not s.line.in_table and not s.line.is_heading:
            by_line[id(s.line)].append(s)
    for lst in by_line.values():
        cur, sc, spans = False, None, []
        for s in sorted([x for x in lst if not x.parenthetical], key=lambda x: x.start):
            k = foot_key(s.text)
            if k and k[0] in syms:                          # 注の始まり (次の印の文までが、この印の注)
                cur, sc = True, (marks.get(k) if k not in unknown and marks.get(k) else None)
            if cur:
                out[id(s)] = sc
                spans.append((s.start, s.end, sc))
        for s in lst:
            if s.parenthetical:
                for a, b, x in spans:
                    if a <= s.start and s.end <= b:
                        out[id(s)] = x
    return out


def _split_inline(row, mem):
    """マスの中の別扱い (能美「第1・第3木曜日 注：松が岡 第2・第4木曜日」) で行の地区を分ける →
    [(添え字, 中身のリスト, {品目: Parsed})] か None (分けられない: 名前がどの中身にも当たらない・全部に当たる・2 つの注が重なる)"""
    groups = {}
    for label, p in row["rules"].items():
        for inl in p.inline:
            idx = frozenset(i for i, x in enumerate(mem)
                            if any(content.squash((x.get("extra") or {}).get("name_verbatim") or x.get("text") or "").startswith(content.squash(n))
                                   for n in inl["names"]))
            if not idx or len(idx) == len(mem):
                return None
            groups.setdefault(idx, []).append((label, inl))
    if not groups:
        return None
    sets = list(groups)
    if any(a & b for i, a in enumerate(sets) for b in sets[i + 1:]):
        return None
    taken = frozenset().union(*sets)
    out = [("x", [mem[i] for i in range(len(mem)) if i not in taken], None)]
    for k, idx in enumerate(sets, 1):
        out.append((f"i{k}", [mem[i] for i in sorted(idx)], dict(groups[idx])))
    res = []
    for suffix, ms, inl_by_label in out:
        rr = {}
        for label, p in row["rules"].items():
            if p.status != "inline":
                rr[label] = p
            elif inl_by_label and label in inl_by_label:
                rr[label] = rules.Parsed(inl_by_label[label]["text"], [inl_by_label[label]["ir"]], "ok",
                                         notes=["マスの中の別扱い (inline) で分けた地区の規則"])
            else:
                rr[label] = rules.Parsed(p.text, p.irs, "ok", p.pieces, notes=["マスの中の別扱い (inline) を除いた地区の規則"])
        res.append((suffix, ms, rr))
    return res


def _ref(sid, tix, row):
    """値の場所 (ref)。型 C の文 (表の番号が "c") は表の番号を持たないので、note に書く (スキーマの table は整数)"""
    if isinstance(tix, int):
        return {"source_id": sid, "table": tix, "row": row}
    return {"source_id": sid, "row": row, "note": "本文の文 (型 C。表の外)"}


def _row_state(states):
    if states and all(s == "agreed" for s in states):
        return "agreed"
    if any(s == "disputed" for s in states):
        return "disputed"
    return "single"


def read_source(m, doc, a, cal_docs):
    """1 つの資料を読む → その資料の系列を m に足す"""
    body, meta = body_of(doc["url"])
    if body is None:
        m.notes.append(f"キャッシュに無い: {doc['url']}")
        return
    sid = f"s{len(m.sources) + 1}"
    hdr = (meta or {}).get("headers") or {}
    ct = doc.get("content_type") or hdr.get("content-type", "")
    is_csv = doc.get("file_type") in ("csv", "xlsx", "xls")
    host = urllib.parse.urlsplit(doc["url"]).hostname or ""
    src = {"source_id": sid, "url": doc["url"], "source_key": doc.get("source_key") or doc["url"],
           "found_from": (doc.get("found_from") or [None])[0], "anchor_text": (doc.get("anchors") or [None])[0],
           "anchor_context": doc.get("anchor_context"), "title": doc.get("title"), "publisher": m.name,
           "format": "csv" if doc.get("file_type") == "csv" else ("xlsx" if is_csv else doc.get("format_class") or "html_text"),
           "pages": None, "sha256": doc.get("sha256") or (meta or {}).get("sha256"), "content_sha256": None,
           "last_modified": doc.get("last_modified"), "fetched_at": doc.get("fetched_at")}
    times = version.http_times(doc.get("last_modified"), doc.get("fetched_at"), sid)
    readings, sents, page_text, region, lines, dlabel, nd = [], [], "", None, [], "", None
    anchor_toks = version.year_tokens(" ".join(doc.get("anchors") or []))
    if is_csv:
        file_toks = version.filename_tokens(doc["url"])
        if doc.get("file_type") == "csv":
            rows, enc = tabular.read_csv(body, ct)
            sheets = [{"sheet": "csv", "rows": rows}]
            src["extra"] = {"encoding": enc}
        else:
            sheets, skipped = tabular.read_excel(body)
            # 古い年度のシートを外す: 期間はシートの中身ではなく、ファイルの名前・リンクの文字の年の印から先に決める
            # (シートの題の行から決めると、古いシートが先にあるとき、その年度を期間にして新しいシートを外してしまう)
            pre = period_of([], [], file_toks + anchor_toks)
            if pre:
                sheets, sk = tabular.drop_old_sheets(sheets, {"valid_from": pre[0], "valid_to": pre[1]})
                skipped += sk
            src["extra"] = {"sheets_skipped": skipped, "sheet_period_from": pre[2] if pre else None}
            times += version.xlsx_times(body, sid) if body[:2] == b"PK" else []
        title_toks, body_toks = [], []
        other_texts = []                                # 日程の表にならなかったシート (「注意事項」「凡例」) の文字
        for s in sheets:
            rd = tabular.read_rows(s["rows"], m.lg, own_names=(m.name,))
            if rd["orientation"] == "other":
                other_texts += [c for r in s["rows"] for c in r if c and c not in other_texts]
                continue
            title_toks += version.year_tokens(" ".join(rd.get("title_rows") or []))
            rd["_csv_rows"] = s["rows"]
            rd["_sheet"] = s["sheet"]
            readings.append((None, rd))
        # 同じファイルのほかのシートの文字は、表のすべての系列の注 (関門で読めなければ止める。CSV・Excel には文の洗い出しの網が無い)
        for t, rd in readings:
            if other_texts:
                rd["notes"] = rd.get("notes", []) + [{"row": -1, "text": "\n".join(other_texts), "cells": [(-1, 0)], "other_sheet": True}]
    else:
        try:
            html = decode.decode(body, ct).text
        except decode.DecodeError as e:
            m.notes.append(f"文字コードが決まらない: {doc['url']} ({e})")
            return
        bp = fetch_docs.load_boilerplate(host)
        region = content.content_region(html, boilerplate=bp)
        lines = region.lines()
        sents = content.sentences(lines)
        page_text = "\n".join(ln.text for ln in lines)
        src["content_sha256"] = content.sha256_text(content.squash(page_text))
        nd, nd_text = rules.nth_definition([s.text for s in sents])
        title_toks, body_toks = version.html_tokens(html)
        file_toks = version.filename_tokens(doc["url"])
        times += [version._time("page_updated", d, sid) for d in version.page_updated(page_text)]
        first = lines[0].text if lines else ""
        # ページの題が地区の名前のとき (三鷹「令和8年度 大沢3丁目・…地区」) だけ、表の地区にする。ほかは自治体全体
        dlabel = members.strip_district_title(first) if re.search(r"(?:地区|地域)(?:用)?\s*$", content.nfkc(first)) else ""
        for i, t in html_tables.tables(region.root):
            rd = html_tables.read_table(t, nd, m.lg, district_label=dlabel)
            if rd["orientation"] == "other" or not rd.get("rows"):
                continue
            rd["_table_index"] = i
            rd["_nth_def"] = nd_text
            readings.append((t, rd))
        # 型 C: 表の外の文 (川北「【可燃物（燃えるごみ）】 毎週 月曜日・木曜日」)
        rdt = html_tables.read_text(lines, nd, district_label=dlabel, own_names=(m.name,))
        if rdt["orientation"] != "other":
            rdt["_table_index"] = "c"
            rdt["_nth_def"] = nd_text
            readings.append(("text", rdt))
        if COLLECTION_POINT.search(content.nfkc(page_text)):
            m.basis = "collection_point"
            s0 = next((s for s in sents if COLLECTION_POINT.search(content.nfkc(s.text))), None)
            m.basis_span = s0.span(sid) if s0 else None
    if not readings:
        return
    m.sources.append(src)
    pr = period_of(title_toks, body_toks, file_toks)
    if pr:
        vf, vt, basis_text = pr
        ye = version.year_evidence(vf, vt, title_toks, body_toks, file_toks, anchor_toks, times, token_ref={"source_id": sid})
        per = {"valid_from": vf, "valid_to": vt, "basis": {"text": basis_text, "ref": {"source_id": sid}, "kind": "stated"},
               "year_evidence": ye}
    else:
        fa = (doc.get("fetched_at") or dt.date.today().isoformat())[:10]
        per = {"valid_from": fa, "valid_to": None, "basis": {"text": "期間の書いていない資料", "ref": {"source_id": sid}, "kind": "undated"},
               "year_evidence": version.year_evidence(fa, None, times=times)}
    pid = m.period_id(per)
    per = m.periods[pid]
    # 品目の名前の読み残しは、もとの品目に寄せて、その品目の但し書きに (資料の中のその品目すべてを止める。merge_labels)
    # 型 C の地名と読めない見出し・版や期間の見出しの節の品目は、資料の中のその品目すべてを止める (ページの地区の同じ品目も。寄せる前の名前で)
    wide_notes = [x for t, rd in readings for x in heading_notes(rd)]
    wide_notes += [x for t, rd in readings for x in merge_labels(rd)]
    src_districts, src_cats = [], {}
    for t, rd in readings:
        for label, c in rd.get("categories", {}).items():
            src_cats[label] = c
    cat_ids = {label: m.category_id(label, c, {"source_id": sid, "note": "表の見出し" if c.get("col") is not None else "本文の見出し"})
               for label, c in src_cats.items()}
    cats_for_scope = {cid: m.categories[cid] for cid in cat_ids.values()}
    wide_q = collections.defaultdict(list)                  # 品目 → 資料の中のその品目すべてを止める但し書き (名前の読み残し)
    for key, qs in wide_notes:
        for label, cid in cat_ids.items():
            if _label_hit(key, label, src_cats[label]):
                wide_q[cid] += [q for q in qs if q["qualifier_id"] not in {x["qualifier_id"] for x in wide_q[cid]}]
    # ほかの表・資料と突き合わせる品目の鍵: 品目そのもの (公式の名前と出し方) と、大まかな分類がこの資料の日付の品目の中で
    # 1 つだけならその分類も (世田谷の HTML と CSV の「可燃ごみ」と「可燃」)。2 つ以上なら分類では突き合わせない
    # (東秩父のプラスチック 2 つを 1 つにまとめて落とさない)
    coarse_n = collections.Counter(tuple(sorted(c["coarse"])) for c in src_cats.values() if c["mode"] in schema.DATED_MODES)
    match_key = {label: (("coarse",) + tuple(sorted(c["coarse"])) if coarse_n[tuple(sorted(c["coarse"]))] == 1
                         else ("category", cat_ids[label])) for label, c in src_cats.items()}
    # 方針の文 (コードの読み。B はまだ)。日程の表のマスと型 C の規則の行の中の文は、マス・行ごとに読む (cell_notes。その地区・品目だけ)。
    # 表の見出しの下の行のマスは、どの列もページの方針の文にしない (備考などの列はその行、注の行はその表の系列だけに関門で読む。
    # 読めなければ但し書き)。表の題・見出しの行の文は、これまでどおりページの文として読む
    rule_cells = rule_cell_elements(readings)
    body_cells = table_body_cells(readings)
    # 型 C の規則・日付の一覧の行の所 (行と行の中の位置)。その中の文も、行ごとに読む (文字の一部の一致では見ない)
    text_spans = [sp for t, rd in readings if t == "text" for r in rd["rows"]
                  for d in (r.get("spans") or {}, r.get("listed_spans") or {}) for lst in d.values() for sp in lst]
    # 地区・品目の名前のマスの書き添えは、その地区・品目だけの但し書き (ページの方針の文にして広げない)
    name_cells, row_notes, label_notes = note_cells(readings, lines, m.lg)
    # 表の参照の印 (「燃やすごみ※」「東町※1」) の注 (表の外の「※祝日も収集します」) は、ページの方針の文にしない (印の無い系列に当てない)。
    # 印の系列が決まれば、その系列だけを止める但し書き (foot_q)。決まらなければ使われなかった但し書き (資料の品目すべてを止める)
    foot = footnotes(sents, readings) if sents else {}
    foot_q = collections.defaultdict(list)
    for s in sents:
        sc = foot.get(id(s))
        if sc and not s.parenthetical:
            q = _qual(s.text)
            for key in sc:
                if q["qualifier_id"] not in {x["qualifier_id"] for x in foot_q[key]}:
                    foot_q[key].append(q)
    page_sents = [s for s in sents if not _in_cells(s, rule_cells) and _note_rec(s.line.element, name_cells) is None and
                  not _in_spans(s, text_spans) and not _in_cells(s, body_cells) and id(s) not in foot]
    excs, used, loose = expand.exceptions_from_sentences(page_sents, per, cats_for_scope, sid, start=m.n_exc + 1)
    m.n_exc += len(excs)
    used = expand.consume_parentheticals(page_sents, used)
    m.sentences[sid] = sents                                # 文 (使った印つき)。build の方針の B が同じ文で比べる
    # ページの B (型 C の文の読みと、ページの地区の 2 つ目の読み)。表の向きが地区の行でないとき・型 C があるときだけ聞く
    b_page = None
    if any(rd["orientation"] in ("category_rows", "weekday_rows", "text_sections") for _, rd in readings) and lines:
        b_page = _text_b(lines, doc.get("title") or (lines[0].text if lines else ""), a, m)
    # 表ごと
    cell_excs = []
    # 読み切った規則のマス (要素の id。見出しの行の列の札だけのマスも)・型 C の行の所 (cell_notes が読み残し無し)
    read_cells, read_spans = head_label_cells(readings), []
    aux_left = {}                                           # 備考などの列・表の注の行のマス → どれかの系列で読み残しがあったか
    cells_q = []                                            # 系列ごとのマス・名前の但し書き (build の B との比べに渡す)
    dups = []                                               # 重なって落とした系列 (先の系列, こちらの材料)
    for ri, (t, rd) in enumerate(readings):
        tix = rd.get("_table_index", 0)
        is_table = t is not None and t != "text"
        inv = markup.inventory(t, rd, page_text) if is_table else []
        unhandled = markup.unhandled(inv)
        markup.apply_partial(inv, rd)                       # 塩竈の斜体 → その町に partial (聞き返す)
        contact = markup.contact_rows(inv)                  # 新宿の「*」の行 → 種類 3
        for rule_name in sorted({x["rule"] for x in markup.pending_rules(inv)}):
            m.queue.append({"item_id": f"{m.lg}:{pid}:markup:{sid}:t{tix}:{rule_name}", "kind": "markup", "status": "open",
                            "rule": rule_name, "count": sum(1 for x in inv if x["rule"] == rule_name),
                            "note": "読みを足す決まり。人が確かめるまで markup_unhandled のまま"})
        value_rows = markup.affected_rows(inv, markup.VALUE_READINGS)
        struct = rd.get("structure") or {"ok": True}
        struct_item = None
        if not struct.get("ok"):
            struct_item = f"{m.lg}:{pid}:structure:{sid}:t{tix}"
            m.queue.append({"item_id": struct_item, "kind": "structure", "status": "open", "issues": struct.get("issues")})
        # B
        cmp, cmp_mk, page_district_state = None, {}, None
        if is_table:
            b, _ = _llm_b(t, rd, page_text[:1500], a, m)
            cmp = markup.compare_table(rd, b) if b else None
            cmp_mk = markup.compare_markup(inv, b.get("markup")) if b else {}
        elif t is None:
            b, row_map = _csv_b(rd["_csv_rows"], rd, "", a, m)
            cmp = markup.compare_table(rd, b, row_map) if b else None
        else:
            for row in rd["rows"]:                          # 型 C の地区の中身 (比べる前に)
                row["members"] = members.parse_cell(row["district_text"], m.lg) if row["district_text"] else None
            cmp = markup.compare_text(rd, b_page) if b_page else None
        if rd["orientation"] in ("category_rows", "weekday_rows", "text_sections"):
            mem_page = members.parse_cell(dlabel, m.lg) if dlabel else []
            page_district_state = markup.compare_page_district(dlabel, mem_page, b_page) if b_page else "missing"
            m.compare[f"page_district:{page_district_state}"] += 1
        table_item = None
        if cmp and cmp.get("table") == "disputed":
            table_item = f"{m.lg}:{pid}:structure:{sid}:t{tix}:table"
            m.queue.append({"item_id": table_item, "kind": "structure", "status": "open",
                            "note": "日程の表か・表の向きが 2 つの読みで違う"})
        for k, v in cmp_mk.items():
            m.compare[f"markup:{k}:{v}"] += 1
        # 地区と系列
        for row in rd["rows"]:
            if rd["orientation"] == "district_rows":
                mem = row["members"]
                dlabel_text = row["district_text"]
            else:
                dlabel_text = row["district_text"] or m.name
                mem = (row.get("members") or members.parse_cell(dlabel_text, m.lg)) if row["district_text"] else \
                    [{"text": m.name, "unit": "municipality", "machiaza_id": None, "match": "unchecked", "conditions": [],
                      "geometry": "unresolved", "extra": {"name_verbatim": m.name}}]
            dkey = content.squash(dlabel_text)
            for mm in mem:
                mm["ref"] = _ref(sid, tix, row["row"])
            base_did = f"{m.lg}-{sid}-t{tix}-r{row['row']}"
            dist_quals = row_notes.get((ri, row["row"]), [])  # 地区のマスの書き添え (その行の地区の品目すべて)
            split = _split_inline(row, mem) if any(p.status == "inline" for p in row["rules"].values()) else None
            groups = [("", mem, row["rules"])] if not split else split
            for suffix, gmem, grules in groups:
                did = base_did + (f"-{suffix}" if suffix else "")
                gkey = dkey + (f"#{suffix}" if suffix else "")
                n_before = len(m.schedules)
                drec = {"district_id": did, "partition_id": "all", "label": dlabel_text or m.name, "label_kind": "official",
                        "members": [{k: v for k, v in x.items() if k in ("text", "unit", "machiaza_id", "match", "conditions",
                                                                          "geometry", "ref", "extra")} for x in gmem],
                        "place_resolution": schema.weakest([x["geometry"] for x in gmem]),
                        "ref": _ref(sid, tix, row["row"])}
                if suffix:
                    drec["extra"] = {"split_from_row": base_did, "why": "マスの中の別扱い (inline) で地区を分けた"}
                for label, parsed in grules.items():
                    cat = src_cats[label]
                    cid = cat_ids[label]
                    if cat["mode"] not in schema.DATED_MODES:
                        continue                    # 持ち込み・申し込み・決まらない品目は日付の系列にしない
                    if t == "text" and label in (row.get("listed") or {}):
                        continue                    # 同じ地区・品目の日付の一覧がある: 規則は一覧の系列と比べる (下の listed。系列を 2 つ作らない)
                    sel = rd.get("selector") or (f"csv row {row['row']}" if t is None else None)
                    rule_span = {"source_id": sid, "selector": sel or ((row.get("cells") or {}).get(label) or [None])[0],
                                 "text": parsed.text or label}
                    # マスの読み残し: 年末年始・祝日の文はそのマスの地区・品目だけの例外に、ほかは使われなかった但し書きに。
                    # 品目の名指しは、系列と同じこの資料の品目 (cats_for_scope) で見る (「（燃やすごみは祝日の収集は休み）」は
                    # 燃やすごみのマスなら使い、ほかの品目・資料に無い品目を名指しすれば但し書きに残す)
                    ce, left = expand.cell_notes(parsed.rest, per, {"category_ids": [cid], "district_ids": [did]},
                                                 dict(rule_span, text=parsed.rest), start=m.n_exc + 1, prefix="e",
                                                 cats=cats_for_scope)
                    m.n_exc += len(ce)
                    cell_quals = [{"qualifier_id": expand.rest_qualifier_id(x), "months": sorted(content.named_months(x)), "text": x}
                                  for x in left]
                    # 行の備考などの列 (その行の系列だけ) と表の注の行 (その表の系列すべて) も、マスの注と同じく関門に通す。
                    # 読めた年末年始・祝日の文はこの系列だけの例外、読めなければこの系列を止める但し書き (ページの方針にしない)
                    oth = row.get("other_cells") or []
                    oth = oth.get(label, []) if isinstance(oth, dict) else oth
                    for texts, ids in (_pos_texts(rd, oth), _table_notes(rd)):
                        if not texts:
                            continue
                        x_rest = "/".join(texts)
                        ce2, left2 = expand.cell_notes(x_rest, per, {"category_ids": [cid], "district_ids": [did]},
                                                       dict(rule_span, text=x_rest), start=m.n_exc + 1, prefix="e", cats=cats_for_scope)
                        m.n_exc += len(ce2)
                        ce = ce + ce2
                        cell_quals += [{"qualifier_id": expand.rest_qualifier_id(x), "months": sorted(content.named_months(x)), "text": x}
                                       for x in left2]
                        for i in ids:
                            aux_left[i] = aux_left.get(i, False) or bool(left2)
                    for q in foot_q.get((ri, row["row"], label), []) + [_qual(h) for h in (row.get("page_quals") or {}).get(label, [])]:
                        # 表の参照の印の注 (印のある系列だけ)・条件つきの地区の見出し (「東町（祝日の週）」。ページの地区の同じ品目)
                        if q["qualifier_id"] not in {x["qualifier_id"] for x in cell_quals}:
                            cell_quals.append(q)
                    for q in dist_quals + label_notes.get((ri, label), []):    # 地区・品目の名前のマスの書き添え
                        # 品目の見出しの回数の書き添え (世田谷「可燃ごみ（週2回）」) は、この系列の規則と同じ回数を言っているだけなら使う
                        # (毎週の曜日の数・第1〜4 の回の数と合うとき。合わなければ、その系列を止める但し書きのまま)
                        if q in label_notes.get((ri, label), []) and \
                                consume.take("freq", rules.freq_template(q["text"], parsed.ir if parsed.status == "ok" else None), q["text"]):
                            continue
                        if q["qualifier_id"] not in {x["qualifier_id"] for x in cell_quals}:
                            cell_quals.append(q)
                    if not left:                    # マス・行を読み切った: その中の文 (要素・行の位置が同じもの) だけを但し書きから外す
                        if t == "text":
                            read_spans += (row.get("spans") or {}).get(label, [])
                        elif is_table:
                            read_cells |= _rule_cell_ids(rd, row, label)
                    row_excs = list(ce)
                    if suffix:
                        for inl in [i for p in row["rules"].values() for i in p.inline]:
                            if label in (grules or {}) and parsed.text == inl["text"]:
                                row_excs.append({"exception_id": f"e{m.n_exc + 1}", "kind": "inline",
                                                 "scope": {"category_ids": [cid], "district_ids": [did]}, "text": inl["text"],
                                                 "span": dict(rule_span, text=inl["text"])})
                                m.n_exc += 1
                    cells_q.extend(cell_quals)
                    keys = [(gkey, ("category", cid))] + ([(gkey, match_key[label])] if match_key[label][0] == "coarse" else [])
                    hit = next((k for k in keys if k in m.seen), None)
                    if hit is not None:
                        # 同じ地区・品目がほかの表・資料にもある: 比べて、違えば先の系列を裁定待ちに (§3.11 資料どうしも比べる)
                        prev_sid, prev_parsed, prev_spec = m.seen[hit]
                        # こちらのマスの読み残し (但し書き) は、先の系列にも掛ける (重なりで落とさない)
                        dest = prev_spec["_cell_qualifiers"] if "_cell_qualifiers" in prev_spec else prev_spec["qualifiers"]
                        for q in cell_quals:
                            if q["qualifier_id"] not in {x["qualifier_id"] for x in dest}:
                                dest.append({"qualifier_id": q["qualifier_id"], "months": q["months"]})
                        # こちらの資料の deferred・但し書き・ページとマスの方針の例外・表のしるし・構造の裁定待ちは、資料を読み終えてから
                        # 先の系列に掛ける (_carry)。マスの例外はこちらの (落とした) 地区に当てたままにしない
                        dups.append((prev_spec, {"cid": cid, "ce": ce, "tix": tix, "row": row["row"], "pid": pid,
                                                 "unhandled": list(unhandled), "disputes": [x for x in (struct_item, table_item) if x],
                                                 "printed_pending": bool(row.get("calendar_links"))}))
                        if markup._same_parsed(prev_parsed, parsed):
                            m.dup["agreed"] += 1
                            prev_spec.setdefault("extra", {}).setdefault("also_in", []).append(rule_span)
                        else:
                            m.dup["disputed"] += 1
                            item = f"{m.lg}:{pid}:rule:{prev_spec['district_id']}:{cid}:vs:{sid}:t{tix}:r{row['row']}"
                            prev_spec["disputes"].append(item)
                            prev_spec["provenance"]["status"] = "disputed"
                            m.queue.append({"item_id": item, "kind": "rule", "status": "open",
                                            "readings": {"A": {"source_id": prev_sid, "text": prev_parsed.text},
                                                         "B": {"source_id": sid, "text": parsed.text}},
                                            "note": "同じ地区・品目の規則が、ほかの表・資料と違う"})
                        continue
                    cell_excs += row_excs
                    disputes = []
                    readers = [{"reader": READER_A, "kind": "code", "inputs": {"source_sha256": src["sha256"], "table": tix,
                                                                              "row": row["row"]}}]
                    status = "single"
                    if cmp:
                        rule_state = cmp["rules"].get((row["row"], label), "missing")
                        if suffix:
                            rule_state = "missing"  # 分けた読み (inline) は B が読んでいない (B の答えの形に無い)
                        states = [("category", cmp["categories"].get(label, "missing")), ("rule", rule_state)]
                        if rd["orientation"] == "district_rows" or row.get("district_from") == "column":
                            states.append(("members", cmp["members"].get(row["row"], "missing")))
                        elif rd["orientation"] == "text_sections" and row.get("district_from") == "heading":
                            states.append(("members", cmp["members"].get(row["row"], "missing")))
                        else:                       # 題か自治体の地区 (三鷹): ページの地区の 2 つ目の読み
                            states.append(("members", page_district_state or "missing"))
                        for rname in sorted({x for rr in (row.get("src_rows") or [row["row"]]) for x in value_rows.get(rr, ())}):
                            states.append(("markup", cmp_mk.get(rname, "missing")))
                        if any(k.startswith("b_only:") and v == "disputed" for k, v in cmp_mk.items()):
                            states.append(("markup", "disputed"))
                        states.append(("structure", cmp.get("table", "missing")))
                        m.compare.update(f"{k}:{v}" for k, v in states)
                        b_reader = f"llm.{markup.PROMPT_TEXT if t == 'text' else markup.PROMPT_VERSION}"
                        readers.append({"reader": b_reader, "kind": "llm", "model": "deepseek-flash",
                                        "prompt": b_reader[4:], "inputs": {"table": tix}, "agree": all(s == "agreed" for _, s in states)})
                        status = _row_state([s for _, s in states])
                        if status == "disputed":
                            for kind, s in states:
                                if s != "disputed":
                                    continue
                                if kind == "structure":
                                    if table_item and table_item not in disputes:
                                        disputes.append(table_item)
                                    continue
                                item = f"{m.lg}:{pid}:{kind}:{did}:{cid}"
                                if item in disputes:
                                    continue
                                disputes.append(item)
                                m.queue.append({"item_id": item, "kind": kind, "status": "open", "row": row["row"], "label": label})
                    if struct_item:
                        disputes.append(struct_item)
                    extra = {"rule_status": parsed.status, "rule_notes": parsed.notes, "policies_reader": "code_only"}
                    if any(rr in contact for rr in (row.get("src_rows") or [row["row"]])):
                        extra["answer_type"] = "rule_contact"
                        extra["markup"] = "asterisk_contact@1"
                        m.contact[did] = "asterisk_contact@1"
                    spec = {"district_id": did, "category_id": cid, "period": per, "origin": "expanded",
                            "rule": {"text": parsed.text, "span": rule_span, "parsed": parsed},
                            "printed_pending": bool(cal_docs) or bool(row.get("calendar_links")),
                            "exceptions": excs + cell_excs, "rest_consumed": True, "_cell_qualifiers": cell_quals,
                            "qualifiers": [], "markup_unhandled": unhandled, "disputes": disputes,
                            "provenance": {"readers": readers, "independence": "code_llm" if cmp else None, "status": status,
                                           "spans": [rule_span], "adjudication": None},
                            "ref": _ref(sid, tix, row["row"]),
                            "gate_unit": "C" if t == "text" else ("A" if t is None else "B"),
                            "extra": extra, "extra_blocked": ["single_reading"]}
                    if extra.get("answer_type") == "rule_contact" and "rule_unparsed" not in (spec["extra_blocked"]):
                        spec["extra_blocked"].append("rule_unparsed")      # 問い合わせの行の規則は読まない (種類 3)
                    for k in keys:
                        m.seen.setdefault(k, (sid, parsed, spec))
                    m.schedules.append(spec)
                # 型 C の日付の一覧 (listed)
                for label, mds in (row.get("listed") or {}).items():
                    cat, cid = src_cats[label], cat_ids[label]
                    if cat["mode"] not in schema.DATED_MODES:
                        continue
                    ds = [rules._year_for(mo, d, per) for mo, d in mds]
                    if not ds or any(x is None for x in ds):
                        m.notes.append(f"{did}/{cid}: 日付の一覧の年が期間で決まらない")
                        continue
                    lsp = (row.get("listed_spans") or {}).get(label, [])
                    sp = {"source_id": sid, "selector": (lsp or [{}])[0].get("selector"), "text": " ".join(x["text"] for x in lsp)}
                    # 一覧の日付の並びの後ろ (「※7月13日は収集しません」「（8月は休みです）」) は、規則のマスの読み残しと同じく
                    # cell_notes と関門に通す (その地区・品目だけ)。読み切ったときだけ、その行の文を但し書きから外す。
                    # 祝日の注 (「（祝日は休み）」) は一覧の日付に当てられない (一覧は日付そのもの) ので使わず、但し書きに残す
                    lrest = (row.get("listed_rest") or {}).get(label, "")
                    ce, left = expand.cell_notes(lrest, per, {"category_ids": [cid], "district_ids": [did]}, dict(sp, text=lrest),
                                                 start=m.n_exc + 1, prefix="e", cats=cats_for_scope, holiday=False)
                    m.n_exc += len(ce)
                    lq = [{"qualifier_id": expand.rest_qualifier_id(x), "months": sorted(content.named_months(x)), "text": x} for x in left]
                    disputes = []
                    # かっこの曜日が、期間で決めた年の曜日と合わなければ (別の年度の一覧) 止める
                    wds = (row.get("listed_wd") or {}).get(label) or [None] * len(ds)
                    bad = [x.isoformat() for x, w in zip(ds, wds)
                           if w and (not holidays.holiday(x) if w == "祝" else rules.WD[w] != rules.WD_ORDER[x.weekday()])]
                    why = []
                    if bad:
                        why.append(("listed_weekday", f"日付の一覧のかっこの曜日が、期間で決めた年の曜日と合わない ({', '.join(bad[:3])})"))
                    if (row.get("listed_conflict") or {}).get(label):
                        why.append(("listed_sections", row["listed_conflict"][label]))
                    # 同じ地区・品目の規則の文もあれば、一覧の系列の規則として比べる (印字と規則の比べ。読めない規則なら裁定待ち)
                    rp = (row.get("rules") or {}).get(label)
                    rule = None
                    if rp is not None:
                        rsp = (row.get("spans") or {}).get(label, [])
                        rule = {"text": rp.text, "span": {"source_id": sid, "selector": (rsp or [{}])[0].get("selector"), "text": rp.text},
                                "parsed": rp}
                        ce2, left2 = expand.cell_notes(rp.rest, per, {"category_ids": [cid], "district_ids": [did]},
                                                       dict(rule["span"], text=rp.rest), start=m.n_exc + 1 + len(ce), prefix="e",
                                                       cats=cats_for_scope)
                        m.n_exc += len(ce2)
                        ce = ce + ce2
                        lq += [{"qualifier_id": expand.rest_qualifier_id(x), "months": sorted(content.named_months(x)), "text": x} for x in left2]
                        if not left2:
                            read_spans += rsp
                        if rp.status != "ok":
                            why.append(("listed_vs_rule", "同じ地区・品目の規則の文が読めない (一覧と比べられない)"))
                    for k, note in why:
                        item = f"{m.lg}:{pid}:rule:{did}:{cid}:{k}"
                        disputes.append(item)
                        m.queue.append({"item_id": item, "kind": "rule", "status": "open", "note": note})
                    cell_excs += ce
                    if not left:
                        read_spans += lsp
                    spec = {"district_id": did, "category_id": cid, "period": per, "origin": "listed",
                            "printed_dates": sorted({x.isoformat() for x in ds}), "exceptions": excs + cell_excs,
                            "_cell_qualifiers": dist_quals + label_notes.get((ri, label), []) + lq +  # 地区の見出し・品目の書き添え・並びの後ろ
                                                [_qual(h) for h in (row.get("page_quals") or {}).get(label, [])],
                            "qualifiers": [], "markup_unhandled": [], "disputes": disputes,
                            "provenance": {"readers": [{"reader": READER_A, "kind": "code", "inputs": {"source_sha256": src["sha256"]}}],
                                           "independence": None, "status": "single", "spans": [sp], "adjudication": None},
                            "ref": _ref(sid, tix, row["row"]), "gate_unit": "C",
                            "extra": {"policies_reader": "code_only"}, "extra_blocked": ["single_reading"]}
                    if rule:
                        spec["rule"] = rule
                    cells_q.extend(spec["_cell_qualifiers"])
                    m.schedules.append(spec)
                if len(m.schedules) > n_before:            # 日付の系列が 1 つも無い地区 (持ち込みだけ・ほかの資料と重なるだけ) は入れない
                    m.districts.append(drec)
                    src_districts.append(did)
    # 但し書き: 本文の範囲の印のある文のうち、どの値にも使われていないもの。外すのは、その文そのものが使われたとき (consume.used)、
    # 名前のマスの文、読み切った規則のマス・型 C の行の中の文 (要素・行の位置が同じもの) だけ。文字の一致・部分の一致では外さない
    # (ほかの節・ほかのマスの同じ文字の文が使われていても、この文は残る)
    # 備考などの列・表の注の行のマスは、どの系列でも関門で読み切ったときだけ読んだマス。表の注の行で読み残しのあるものは、
    # 但し書きの印が無くても但し書き (表の系列は cell_notes の読み残しで止めてある)
    read_cells |= {i for i, x in aux_left.items() if not x}
    note_left = {i for t, rd in readings if t not in (None, "text") for i in _table_notes(rd)[1] if aux_left.get(i, True)}
    quals = content.scan_qualifiers(sents, own_names=(m.name,))
    flagged = {id(q["sentence"]) for q in quals}
    quals += [{"qualifier_id": expand.rest_qualifier_id(s.text), "text": s.text, "marks": [("hint", "表の注の行")], "selector": s.selector,
               "heading_path": list(s.heading_path), "months": sorted(content.named_months(s.text)), "sentence": s}
              for s in sents if not s.parenthetical and id(s) not in flagged and _in_cells(s, note_left)]
    # 表の参照の印の注で系列の決まらないもの (印の番号が表に無い・印のマスが系列にならない) は、印・手がかりが無くても但し書き
    # (ページの方針の文から外したので、黙って落とさない)
    flagged = {id(q["sentence"]) for q in quals}
    quals += [{"qualifier_id": expand.rest_qualifier_id(s.text), "text": s.text, "marks": [("hint", "表の参照の印の注")],
               "selector": s.selector, "heading_path": list(s.heading_path), "months": sorted(content.named_months(s.text)),
               "sentence": s}
              for s in sents if not s.parenthetical and id(s) not in flagged and id(s) in foot]
    unconsumed = []
    for q in quals:
        if consume.used(q["sentence"]):
            continue
        rec = _note_rec(q["sentence"].line.element, name_cells)
        if rec is not None:
            # 名前のマスの文: 名前として読んだ部分と、そのマスの地区・品目を止める但し書き (note_cells の読み残し) だけなら済み。
            # マスの読み残しに無い文字が残れば (マスの文字と行の文字が違う・図の alt)、どこに掛かるか決まらないので資料の品目すべてを止める
            if not (all(_covered(p, rec["pieces"]) for p in _rest_pieces(q["text"], rec["frags"])) and
                    consume.take("name_cell", "cell_pieces_scoped", q["text"])):
                unconsumed.append(q)
            else:
                cells_q.append(q)                           # その地区・品目だけを止める名前のマスの文 (B との比べの A の但し書き)
            continue
        if _in_cells(q["sentence"], read_cells) or _in_spans(q["sentence"], read_spans):
            continue                                        # 読み切った規則のマス・型 C の規則と一覧の行の中の文
        if foot.get(id(q["sentence"])):
            cells_q.append(q)                               # 表の参照の印の注: 印のある系列を止める (foot_q)。B との比べの A の但し書き
            # 7 回目の後退の直し: 印のある系列に当てても、ほかの系列を止めるのはやめない。「※第5週は収集しません」のように
            # 表全体・ページ全体の注かもしれず、印の注だけかは決まらない (止める側。ページの方針の文にはしない)
        unconsumed.append(q)

    def quals_for(cid):
        out = []
        for q in unconsumed:
            if q["sentence"].line.in_table:
                out.append(q)                                 # 表の中の但し書きは、その資料の品目すべて (同じ表の品目より広い)
            else:
                sc = expand.block_scope(q["text"], cats_for_scope, q["heading_path"])     # 節の品目 (節の外なら、すべて)
                if sc == "*" or cid in sc:
                    out.append(q)
        return out + wide_q.get(cid, [])                      # 品目の名前の読み残し (資料の中のその品目すべて)
    # 例外の scope を、この資料の地区に絞る
    for e in excs:
        e.setdefault("scope", {})["district_ids"] = list(src_districts)
    m.exceptions += excs + cell_excs
    for spec in [s for s in m.schedules if isinstance(s, dict) and s.get("ref", {}).get("source_id") == sid]:
        spec["exceptions"] = excs + cell_excs
        qs = list(spec.pop("_cell_qualifiers", [])) + quals_for(spec["category_id"])
        seen_q = set()
        spec["qualifiers"] = []
        for q in qs:
            if q["qualifier_id"] in seen_q:
                continue
            seen_q.add(q["qualifier_id"])
            spec["qualifiers"].append({"qualifier_id": q["qualifier_id"], "months": q["months"]})
    for prev_spec, d in dups:
        _carry(m, prev_spec, d, excs, quals_for(d["cid"]), sid, pid)
    named = {q["qualifier_id"]: q for qs in list(row_notes.values()) + list(label_notes.values()) for q in qs}
    m.unconsumed[sid] = unconsumed                          # 使われなかった但し書き (文つき。build の方針の B が同じ文で比べる)
    scoped = {q["qualifier_id"]: q for q in cells_q + list(named.values()) + [q for qs in wide_q.values() for q in qs]}
    src["extra"] = {**(src.get("extra") or {}), "qualifiers_unconsumed": [{"qualifier_id": q["qualifier_id"], "text": q["text"][:120],
                                                                          "months": q["months"]} for q in unconsumed],
                    "qualifiers_name_cells": [{"qualifier_id": k, "text": q["text"][:120], "months": q["months"]} for k, q in named.items()],
                    "qualifiers_cells": [{"qualifier_id": k, "text": q["text"], "months": q["months"]} for k, q in scoped.items()],
                    "policies_loose": [x["text"][:120] for x in loose], "nth_definition": readings[0][1].get("_nth_def")}


STOP_KINDS = ("deferred", "caveat", "contingency")


def _sig(e):
    """例外の中身 (比べるため。id・scope・原文の場所を除く)"""
    x = {k: v for k, v in e.items() if k not in ("exception_id", "scope", "span", "text", "test", "extra", "template")}
    x["extra"] = {k: v for k, v in (e.get("extra") or {}).items() if k != "carried_from"}
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def _carry(m, prev, d, excs, quals, sid, pid):
    """重なって落とした系列 (後の表・資料) の止める材料を、残った系列 prev に掛ける (和。止める方が勝つ):
    その資料の deferred・caveat の文 (品目が当たるもの)、使われなかった但し書き (ページ・表の中。quals)、落とした行の表のしるし
    (markup_unhandled)・構造の裁定待ち・印字の資料へのリンク、そして方針の例外 (ページの祝日・年末年始・第5週・休みの文と、そのマスの
    cell_notes)。例外は写しを作って prev の地区・品目だけに当てる (落とした地区・ほかの品目に広げない)。
    方針は種類ごとに、落とした行に当たっていた中身の集まりと prev に当たる中身の集まりを比べる: 同じなら写さない。prev にもあって
    違えば (年末年始・祝日の扱いが資料どうしで違う) prev を裁定待ち (policy) にし、写しも足す。prev に無ければ和で写す。
    ただし期間が prev と違う資料の方針は、prev の期間の値ではないので写さず、同じでなければ裁定待ちにする"""
    cid, did = prev["category_id"], prev["district_id"]
    mine = [e for e in prev["exceptions"] if expand.in_scope(e, cid, did, prev["period"].get("period_id"))]
    cats_in = lambda e: (e.get("scope") or {}).get("category_ids") in (None, "*") or d["cid"] in e["scope"]["category_ids"]
    theirs = [e for e in excs if cats_in(e)] + list(d["ce"])       # 落とした行に当たっていた例外 (ページの文とマス)
    same_period = d.get("pid", pid) == prev["period"].get("period_id")
    for kind in sorted({e["kind"] for e in theirs if e["kind"] not in STOP_KINDS}):
        a = {_sig(x) for x in mine if x["kind"] == kind}
        b = {_sig(x) for x in theirs if x["kind"] == kind}
        if a == b or (not a and same_period):
            continue
        item = f"{m.lg}:{pid}:policy:{did}:{cid}:vs:{sid}:t{d['tix']}:r{d['row']}:{kind}"
        if item not in prev["disputes"]:
            prev["disputes"].append(item)
            prev["provenance"]["status"] = "disputed"
            m.queue.append({"item_id": item, "kind": "policy", "status": "open",
                            "readings": {"A": [x.get("text") for x in mine if x["kind"] == kind],
                                         "B": {"source_id": sid, "text": [x.get("text") for x in theirs if x["kind"] == kind]}},
                            "note": "同じ地区・品目の方針 (ページ・マスの年末年始・祝日など) が、ほかの表・資料と違う" +
                                    ("" if same_period else " (期間の違う資料)")})
    have_sig = {_sig(x) for x in mine}
    for e in theirs:
        if _sig(e) in have_sig:
            continue                                        # 同じ中身がもう当たっている (同じ資料のほかの表など)
        if e["kind"] not in STOP_KINDS and not same_period:
            continue                                        # 期間の違う資料の方針は写さない (裁定待ちにしてある)
        have_sig.add(_sig(e))
        m.n_exc += 1
        c = dict(e, exception_id=f"e{m.n_exc}", scope={"category_ids": [cid], "district_ids": [did]},
                 extra={**(e.get("extra") or {}), "carried_from": {"source_id": sid, "exception_id": e["exception_id"]}})
        prev["exceptions"] = prev["exceptions"] + [c]
        m.exceptions.append(c)
    have = {q["qualifier_id"] for q in prev["qualifiers"]}
    for q in quals:
        if q["qualifier_id"] not in have:
            have.add(q["qualifier_id"])
            prev["qualifiers"].append({"qualifier_id": q["qualifier_id"], "months": q["months"]})
    prev["markup_unhandled"] = sorted(set(prev.get("markup_unhandled") or []) | set(d.get("unhandled") or []))
    for item in d.get("disputes") or []:
        if item not in prev["disputes"]:
            prev["disputes"].append(item)
    if d.get("printed_pending") and prev.get("origin") == "expanded":
        prev["printed_pending"] = True


def build_doc(m):
    """集めた読み → スキーマ v0.2 の dict (検査を通す)"""
    scheds = [expand.expand_series(s) for s in m.schedules]
    dated = sorted({s["category_id"] for s in scheds}, key=lambda c: int(c[1:]))
    # 出し方の決まらない品目 (mode が None) は入れない (build の categories_mode_undecided と待ち行列 mode に残す)
    cats = [c for c in m.categories.values() if c["mode"] and (c["category_id"] in dated or c["mode"] not in schema.DATED_MODES)]
    parts = [{"partition_id": "all", "label": "資料の表の地区 (品目すべて)", "category_ids": dated, "basis": m.basis,
              "basis_span": m.basis_span}]
    blocked = collections.Counter(b.split(":")[0] for s in scheds for b in s["blocked_by"])
    doc = {"schema": schema.SCHEMA, "lg_code": m.lg, "name": m.name, "periods": list(m.periods.values()), "partitions": parts,
           "districts": m.districts, "categories": cats, "schedules": scheds, "exceptions": m.exceptions, "sources": m.sources,
           "watch": {"sources": [{"source_id": s["source_id"], "content_sha256": s.get("content_sha256"), "sha256": s.get("sha256")}
                                 for s in m.sources]},
           "coverage": {"answer_type": "rule_contact", "by_district": {d: "rule_contact" for d in m.contact},
                        "population_share": None, "population_basis": None,
                        "blocked_series": dict(blocked), "gate_pending": {"series": sum(1 for s in scheds if s["answerable"])},
                        "quality": None},
           "build": {"builder": "eval/stage2.py@1", "series": len(scheds), "answerable": sum(1 for s in scheds if s["answerable"]),
                     "blocked_by": dict(blocked), "queue": m.queue[:500], "queue_size": len(m.queue),
                     "compare": dict(m.compare), "cross_source_duplicates": dict(m.dup), "llm": m.llm, "notes": m.notes,
                     "categories_mode_undecided": m.mode_undecided, "rule_contact_districts": dict(m.contact),
                     "attribution": "読み取りの段の確かめ。out/schedules/ には書かない"}}
    for c in doc["categories"]:
        c.setdefault("ref", {"source_id": m.sources[0]["source_id"]})
    return doc


def run_one(lg, a, write=True):
    docs = json.loads((OUT / lg / "docs.json").read_text("utf-8"))
    m = Muni(lg, docs.get("name") or lg)
    sel = select_docs(docs)
    cal = printed_docs(docs)
    for d in sel:
        read_source(m, d, a, cal)
    doc = build_doc(m)
    errs = schema.validate(doc)
    if write:
        p = OUT / lg / "stage2.json"
        tmp = p.with_suffix(".part")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), "utf-8")
        os.replace(tmp, p)
    return doc, errs, sel, cal


def measure_rules(doc):
    """測るだけ (§6.2): 規則の読み (IR) を japan-gomi-data の日付と、ふつうの日 (2026 年度、窓 W (12/15〜1/31) の外、休日でない日、
    先方の不明の日を除く) だけで比べる → {"series", "compared", "exact", "differ", "uncomparable", "examples"}。
    答えるとした系列の採点 (J1) ではない (どの系列も answerable でない)。文法の読み違いを見つけるため"""
    import jgd, holidays as hol
    gt = jgd.load_gt(doc["lg_code"])
    if gt is None:
        return {"why": "正解が無い"}
    amap = jgd.area_map(gt, doc)
    split = jgd.split_names(gt)
    courses_of = collections.defaultdict(set)
    for (course, name), ds in amap.items():
        if jgd.nk(name) not in split:
            for d in ds:
                courses_of[d].add(course)
    gtc = {c["course"]: c for c in gt["courses"]}
    gt_keys = {k for c in gt["courses"] for k in c["by_category"]}
    cats = {c["category_id"]: c for c in doc["categories"]}
    plain = {d for d in jgd.daterange("2026-04-01", "2027-03-31")
             if not ("2026-12-15" <= d <= "2027-01-31") and not hol.holiday(d)}
    res = {"series": len(doc["schedules"]), "compared": 0, "exact": 0, "differ": 0, "uncomparable": 0, "examples": []}
    for s in doc["schedules"]:
        ir = (s.get("rule") or {}).get("ir")
        keys = [k for k in cats[s["category_id"]]["coarse"] if k in gt_keys]
        cs = sorted(courses_of.get(s["district_id"], ()))
        if not ir or ir.get("kind") in ("ambiguous_week", "none", "on_demand") or rules.needs_anchor(ir) or not keys or not cs:
            res["uncomparable"] += 1
            continue
        mine = {d.isoformat() for d in rules.occurrences(ir, "2026-04-01", "2027-03-31")}
        bad = []
        for c in cs:
            win = jgd.gt_window(gt, gtc[c]) & plain
            for k in keys:
                g = set(gtc[c]["by_category"].get(k, [])) & win
                if g != mine & win:
                    bad.append({"course": c, "key": k, "only_gt": sorted(g - mine)[:3], "only_ours": sorted((mine & win) - g)[:3]})
        res["compared"] += 1
        if bad:
            res["differ"] += 1
            if len(res["examples"]) < 5:
                res["examples"].append({"series": schema.series_key(s), "rule": s["rule"]["text"], "diffs": bad[:2]})
        else:
            res["exact"] += 1
    return res


def summary_line(doc, errs):
    s = doc["schedules"]
    b = collections.Counter(x.split(":")[0] for y in s for x in y["blocked_by"])
    return {"lg_code": doc["lg_code"], "name": doc["name"], "sources": len(doc["sources"]), "districts": len(doc["districts"]),
            "categories": len(doc["categories"]), "series": len(s), "answerable": sum(1 for x in s if x["answerable"]),
            "blocked_by": dict(b.most_common()), "schema_errors": len(errs), "compare": doc["build"]["compare"],
            "llm": doc["build"]["llm"], "queue": doc["build"]["queue_size"],
            "cross_source_duplicates": doc["build"]["cross_source_duplicates"]}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="作る順の 5〜6 の読み取りを tier A の HTML・CSV に通す")
    ap.add_argument("--codes", default=",".join(TIER_A_HTML_CSV))
    ap.add_argument("--llm", action="store_true", help="2 つ目の読み (LLM) も (キャッシュに無ければ API を呼ぶ)")
    ap.add_argument("--offline", action="store_true", help="LLM はキャッシュだけ (キーを使わない)")
    ap.add_argument("--measure", action="store_true", help="測るだけ: 規則の読みを japan-gomi-data とふつうの日で比べる")
    a = ap.parse_args(argv)
    rows = []
    for lg in [c.strip() for c in a.codes.split(",") if c.strip()]:
        doc, errs, sel, cal = run_one(lg, a)
        r = summary_line(doc, errs)
        r["docs_read"] = [d["url"] for d in sel]
        r["printed_docs"] = len(cal)
        if a.measure:
            r["measure_rules"] = measure_rules(doc)
        rows.append(r)
        print(json.dumps(r, ensure_ascii=False))
        for e in errs[:5]:
            print("   検査:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
