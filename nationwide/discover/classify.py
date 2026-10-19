"""候補の点数づけと形式の分類 (コードだけで決める。LLM の判定は judge.py)

ページ・資料ごとに:
  - 種類 (kinds): calendar (収集日カレンダー) / districts (収集区域・地区の一覧) / how_to (出し方・分け方) /
    year_end (年末年始) / change_notice (変更のお知らせ) / app (アプリの案内)
  - 特徴 (features): 題名・見出し・リンクの文字のごみの言葉、本文のごみの言葉、曜日・日付・地区らしい言葉の数、
    曜日や日付の入った表、年度、PDF の文字の有無
  - 点数 (score, 0〜100) と形式 (format_class): html_table / html_text / html_unchecked / pdf_text / pdf_image /
    pdf_unchecked / xlsx/csv / ics / app / blocked (robots.txt で禁止・ブロックで取らなかった資料)
  - 収集日のページ (calendar・districts) からリンクされた資料は features.linked_from_schedule を持ち、少し加点する。
    リンクの文字が地区の名前だけ (「東地区（PDF：1.2MB）」) のような資料は、リンク元の種類を受け継ぐ (features.kinds_from_parent)
  - PDF は全ページの文字から、ページごとのごみの言葉を数える (pdf_page_features)。町民カレンダーのように、表紙は町の行事で
    中のページにごみの収集の表があるものも、page_hits・calendar_pages で calendar にする
  - HTML は role を持つ: hub (案内のページ。ごみのページ・資料へのリンクが多く、自分には収集日の中身が無い) / schedule。
    子を広げるかは hub_expand_mode (crawler.py が先に取る)。hub も候補の並べ方は変えない (パイロットでは、地区の説明のある
    案内のページを LLM が収集日の資料と答えたものが多い)。LLM に見せる順 (judge_rank) でだけ少し下げる
自治体ごとの形式は decide() で決める (上の形式か app_only / blocked / unreachable / not_found)。中を見た候補を先にする
"""
import datetime, re, urllib.parse, unicodedata

# ---- 言葉 ----

GOMI_STRONG = ("ごみ", "ゴミ", "塵芥", "分別", "リサイクル", "清掃", "粗大", "可燃", "不燃", "資源物", "資源ごみ", "燃やせる", "燃えるごみ")
GOMI_MEDIUM = ("収集", "資源", "カレンダー", "廃棄物", "5374")
GOMI_WEAK = ("環境", "くらし", "暮らし", "生活", "住まい", "すまい", "life")
URL_STRONG = ("gomi", "recycl", "garbage", "trash", "haikibutsu", "seiso", "bunbetsu", "shigen", "sodai", "ごみ", "ゴミ", "分別",
              "リサイクル", "清掃")
URL_MEDIUM = ("calendar", "karenda", "shushu", "syusyu", "shuushuu", "syuusyuu", "waste", "kankyo", "schedule", "nittei", "収集",
              "カレンダー", "資源")
URL_WEAK = ("seikatsu", "kurashi", "kurasi", "life", "living", "環境", "くらし")
TARGET = re.compile(r"収集日|カレンダー|地区|地域|区域|曜日|年末年始|出し方|分け方|アプリ|日程|一覧|早見|変更|\d+月")
NEGATIVE = re.compile(r"入札|契約|議会|会議録|議事録|審議会|委員会|求人|採用|職員募集|選挙|パブリックコメント|指定管理|事業系|"
                      r"産業廃棄物|処理基本計画|処理計画|実施計画|統計|予算|決算|補助金|助成|監査|報酬")

GOMI_CTX = r"(?:ごみ|ゴミ|資源|リサイクル|分別|収集|可燃|不燃|粗大|燃やせる|燃える|燃やす)"
KINDS = {
    "calendar": re.compile(r"収集日|収集カレンダー|収集日程|収集曜日|収集予定|収集(?:日)?(?:一覧)?表|収集計画表|"
                           + GOMI_CTX + r".{0,10}(?:カレンダー|日程|曜日|早見表|予定表)|"
                           r"(?:ごみ|ゴミ|資源)(?:出し)?の日|"
                           r"(?:資源|ごみ|ゴミ|古紙|缶|びん|ビン|ペット|プラ|有害)\S{0,6}回収(?:日|カレンダー|表)|"
                           r"(?:カレンダー|日程表).{0,6}(?:ごみ|ゴミ|資源)|gomi.?cal|calendar.{0,12}gomi|gomi.{0,12}calendar|karenda|"
                           r"syu+syu+bi|shu+shu+bi"),
    "districts": re.compile(r"収集(?:区域|地区|地域|エリア)|(?:地区|地域|町名|区域|町丁|自治会|行政区)(?:別|ごと|一覧)|"
                            + GOMI_CTX + r".{0,8}(?:地区|区域)|地区割"),
    "how_to": re.compile(r"出し方|分け方|捨て方|分別(?:の仕方|方法|表|一覧|辞典|早見|ガイド|ルール|区分)|dashikata|wakekata|bunbetsu|sutekata"),
    "year_end": re.compile(r"年末年始|nenmatsu"),
    "change_notice": re.compile(GOMI_CTX + r".{0,12}(?:変更|中止|休止|お休み|臨時|振替|延期)|(?:変更|中止|休止|臨時|振替).{0,8}収集|"
                                r"祝日.{0,6}収集"),
    "app": re.compile(r"(?:ごみ|ゴミ|分別|収集).{0,10}(?:アプリ|app)|アプリ.{0,10}(?:ごみ|ゴミ|分別|収集)|5374|さんあ.?る|ごみスケ"),
}
CAL_STRONG = re.compile(r"カレンダー|日程|曜日|一覧|早見|予定表|calendar|karenda")
KIND_POINTS = {"calendar": 30, "districts": 22, "how_to": 10, "year_end": 8, "change_notice": 5, "app": 6}
SCHEDULE_KINDS = ("calendar", "districts")

GOMI_ITEM = re.compile(r"ごみ|ゴミ|資源|燃や|燃え|可燃|不燃|粗大|プラ|ペット|びん|ビン|缶|古紙|紙類|有害|危険|乾電池|収集")
WEEKDAY = re.compile(r"[月火水木金土日]曜|[(（][月火水木金土日][)）]|毎週\s*[月火水木金土日]|"
                     r"第\s*[1-5一二三四五](?:\s*[・,、]\s*[1-5一二三四五])*\s*[月火水木金土日]|"
                     r"(?<![0-9年])[月火水木金土日]\s*[・、]\s*[月火水木金土日](?![0-9])")
WEEKDAY_CELL = re.compile(r"^[月火水木金土日](?:曜日?)?(?:\s*[・,、/]\s*[月火水木金土日](?:曜日?)?)*$")
DATE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日|(?<![\d/])\d{1,2}/\d{1,2}(?![\d/])|R\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}")
# 年のついた日付 (「2026年9月2日更新」のような新着情報・更新日の並び。収集日の中身には数えない)
DATED = re.compile(r"(?:20\d{2}|令和\s*(?:\d{1,2}|元)|R\s*\d{1,2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
FISCAL = re.compile(r"令和\s*(\d{1,2}|元)\s*年|(20\d{2})\s*年|(?<![A-Za-z])R\s*(\d{1,2})\s*年")
FY_BACK, FY_AHEAD = 6, 2   # 年度として見る年: 今年度の 6 年前から 2 年先まで (昔の年表・沿革の年は見ない)
DISTRICT = re.compile(r"([一-龥々ヶヵァ-ヴー]{1,6})(?:\d+丁目|[一二三四五六七八九十]+丁目|町|地区|団地|自治会|行政区|集落)")
DISTRICT_STOP = {"市", "各", "全", "同", "当", "他", "隣", "近", "対象", "この", "その", "市町", "町村", "市区", "区", "都道府県", "県"}

SCHEDULE_MIN = 35          # これ以上で種類が calendar・districts なら「収集日の資料」とみなす
STRONG_MIN = 60            # これ以上の収集日の資料が見つかったら、手がかりの薄いリンクはもう追わない
LINKED_BONUS = 6           # 収集日のページからリンクされた資料の加点

# リンク元の種類を受け継ぐときの手がかり: 地区・場所の名前らしい / 申請書などではない
PLACE = re.compile(r"地区|地域|区域|方面|エリア|コース|ブロック|校区|学区|校下|自治会|行政区|集落|団地|丁目|[町村区]|"
                   r"[東西南北中](?:部|側)?|第\s*\d|\d+\s*班|[A-ZＡ-Ｚ]\s*(?:地区|コース|ブロック)")
FORM = re.compile(r"申請|届|様式|申込|記入例|要綱|規則|条例|手数料|料金|アンケート|募集|チラシ")
DOC_TYPES = ("pdf", "xlsx", "xls", "csv", "ics")

# PDF のページごとの言葉 (何十ページもある町民カレンダーの中の、ごみの収集のページを見つける)
PAGE_WORDS = ("燃やせる", "燃やす", "可燃", "資源", "収集日", "収集", "地区", "行政区", "曜日")
PAGE_GOMI = ("燃やせる", "燃やす", "燃える", "可燃", "不燃", "資源")
PAGE_SCHED = ("収集", "地区", "行政区", "曜日")              # 収集 は 収集日 も数える
EN_MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                              "september", "october", "november", "december"))}
MONTH_HEAD = re.compile(r"^[ \t]*(?:(?:令和|平成)[ \t]*(?:\d{1,2}|元)[ \t]*年度?[ \t]*|20\d{2}[ \t]*年度?[ \t]*)?"
                        r"(1[0-2]|0?[1-9])[ \t]*月(?:[ \t]*[(（]?[A-Za-z]{3,9}\.?[)）]?)?[ \t]*$", re.M)
MONTH_EN = re.compile(r"^[ \t]*(" + "|".join(EN_MONTHS) + r")[ \t]*$", re.M | re.I)
DAY_LINE = re.compile(r"^[ \t]*(?:[1-9]|[12]\d|3[01])[ \t]*$", re.M)
DAY_GRID = 20              # 1〜31 だけの行がこれ以上あり、曜日の見出しか月の見出しのあるページは、日付の並んだカレンダーの面
WEEK_HEAD = re.compile(r"^[ \t]*([日月火水木金土]|sun|mon|tue|wed|thu|fri|sat)\.?[ \t]*$", re.M | re.I)
WEEK_ROW = re.compile(r"日\s*月\s*火\s*水\s*木\s*金\s*土|月\s*火\s*水\s*木\s*金\s*土\s*日|sun\s*mon\s*tue", re.I)
PAGE_HITS_MAX = 60         # page_hits に書くページの数の上限
PAGE_FOCUS = re.compile(r"燃やせる|燃やす|燃える|可燃|不燃|資源|収集日|収集|行政区|曜日")   # ページの抜粋をここの少し前から

# 案内のページ (hub): ごみのページ・資料へのリンクが多く、自分には収集日の中身 (表・曜日・日付) が無い HTML
HUB_MIN_LINKS = 4
CAL_LINK = re.compile(r"収集日|収集(?:カレンダー|日程|曜日|予定|計画表|表|区域|地区)|カレンダー|日程表|曜日|早見|(?:ごみ|ゴミ|資源)(?:出し)?の日|"
                      r"地区|校下|校区|行政区|町名|区域")
CAL_LINK_URL = re.compile(r"calendar|karenda|shu+shu+|syu+syu+|nittei|chiku")
EVENT = re.compile(r"イベント|行事|催し|講座|教室|健診|検診|休日当番|event", re.I)
KANA_ROW = re.compile(r"^[あかさたなはまやらわアカサタナハマヤラワ]\s*行")
# 地区の名前らしく見えても、町のあいさつ・案内のメニューなど (「町長挨拶」「女川町について」)
PLACE_STOP = re.compile(r"[市町村区](?:長|政|議|民|誌|有|税|営|立|役場)|について|の紹介|案内|はこちら|ホームページ|サイト")
CAL_DOC = re.compile(r"カレンダー|収集|ごみ|ゴミ|calendar|karenda|gomi|shu+shu+|syu+syu+")   # 判定に必ず入れる資料の手がかり


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def url_text(url):
    """URL のパスと問い合わせ (日本語に戻して小文字)"""
    p = urllib.parse.urlsplit(url)
    return nfkc(urllib.parse.unquote(p.path + ("?" + p.query if p.query else ""))).lower()


def link_priority(anchor, url, parent_rel=0, is_doc=False):
    """リンクをどれだけ先に取るか (0 = 手がかりなし)。リンクの文字 > URL > リンク元のページ"""
    a, u = nfkc(anchor).lower(), url_text(url)
    p = 0
    if any(w in a for w in GOMI_STRONG):
        p += 3
    elif any(w in a for w in GOMI_MEDIUM):
        p += 2
    elif any(w in a for w in GOMI_WEAK):
        p += 1
    if any(w in u for w in URL_STRONG):
        p += 2
    elif any(w in u for w in URL_MEDIUM):
        p += 1
    elif p == 0 and any(w in u for w in URL_WEAK):
        p += 1
    if TARGET.search(a) and (p >= 2 or parent_rel >= 1):
        p += 2
    if parent_rel >= 2:
        p += 2 if is_doc else 1
    elif parent_rel == 1 and is_doc:
        p += 1
    if "サイトマップ" in a or (not is_doc and "sitemap" in u):
        p += 2
    if NEGATIVE.search(a):
        p -= 2
    return max(p, 0)


def fiscal_years(text, today=None, limit=5):
    """令和8年度・2026年度・R8 などを西暦の年に。今年度の 6 年前から 2 年先までの年だけを、今年度・来年度に近い順に
    limit まで選ぶ (並びは小さい順)。町民カレンダーの年表や計画の沿革の昔の年で、今年度が押し出されないように"""
    fy = current_fy(today)
    ys = set()
    for m in FISCAL.finditer(text):
        if m.group(1):
            ys.add(2019 if m.group(1) == "元" else 2018 + int(m.group(1)))
        elif m.group(2):
            ys.add(int(m.group(2)))
        elif m.group(3):
            ys.add(2018 + int(m.group(3)))
    near = [y for y in ys if fy - FY_BACK <= y <= fy + FY_AHEAD]
    near.sort(key=lambda y: (y not in (fy, fy + 1), abs(y - fy), -y))
    return sorted(near[:limit])


PLACE_NAME = re.compile(r"([一-龥ぁ-んァ-ヶ]{1,5})([市町村])")
PLACE_NAME_STOP = re.compile(r"^(?:各|当|本|全|同|他|近隣|周辺|構成|関係|都|城下|門前|港)|[市町村区]")   # 「各市町」「構成市町」など
OTHER_MUNI_PENALTY = 25    # 関係する発行元 (組合など) のページで、題名がほかの構成市町村のものは下げる


def other_munis(text, name):
    """題名・見出し (text) に出る市町村の名前のうち、自分 (name。例: 蓮田市) でないもの。自分の名前 (「蓮田」) が出れば []。
    一部事務組合のサイトの、ほかの構成市町村の収集日程のページを見分けるため"""
    t = nfkc(text)
    short = re.sub(r"[市町村区]$", "", nfkc(name))
    if not short or short in t:
        return []
    found = {m.group(0) for m in PLACE_NAME.finditer(t) if not PLACE_NAME_STOP.search(m.group(1))}
    return sorted(found)


def current_fy(today=None):
    d = today or datetime.date.today()
    return d.year if d.month >= 4 else d.year - 1


def text_features(text, max_chars=100_000):
    """本文の特徴: ごみの言葉・曜日・日付 (年のついた日付の数も)・地区らしい言葉の数、年度"""
    t = nfkc(text[:max_chars])
    districts = {m.group(0) for m in DISTRICT.finditer(t) if m.group(1) not in DISTRICT_STOP and m.group(0) not in DISTRICT_STOP}
    return {"gomi_words": len(GOMI_ITEM.findall(t)), "weekday_tokens": len(WEEKDAY.findall(t)),
            "date_tokens": len(DATE.findall(t)), "dated_tokens": len(DATED.findall(t)), "district_tokens": len(districts),
            "fiscal_years": fiscal_years(t), "shushubi_count": t.count("収集日")}


def _page_info(raw):
    """1 ページの言葉の数・月の見出し・日付の並び"""
    t = nfkc(raw)
    hits = {w: t.count(w) for w in PAGE_WORDS}
    months = {int(m.group(1)) for m in MONTH_HEAD.finditer(t)} | {EN_MONTHS[m.group(1).lower()] for m in MONTH_EN.finditer(t)}
    week = len(set(WEEK_HEAD.findall(t))) >= 5 or bool(WEEK_ROW.search(t)) or len(WEEKDAY.findall(t)) >= 2
    grid = len(DAY_LINE.findall(t)) >= DAY_GRID and (week or bool(months))    # 番号の並んだ一覧 (業者の一覧など) と分ける
    gomi = sum(t.count(w) for w in PAGE_GOMI)
    sched = sum(hits[w] for w in PAGE_SCHED)
    hit = gomi >= 2 and (sched >= 1 or bool(months) or grid)
    return {"hit": hit, "months": months, "grid": grid, "score": gomi + sched + 3 * bool(months) + 3 * grid}


def pdf_page_features(text, sep="\f"):
    """PDF の全ページの文字 (sep で区切る) → (特徴, 言葉の多い順のごみの収集のページ番号, カレンダーらしいページ番号)。特徴:
      page_hits: ごみの収集のページ (燃やせる・燃やす・可燃・資源などが 2 つ以上と、収集・地区・行政区・曜日か、
                 月の見出しか、日付の並び) の番号 (1 から)
      calendar_pages: カレンダーらしいページの数 (月の見出しが 2 つ以上か日付の並びのある page_hits のページと、
                 その隣の日付の並んだ面。page_hits のページに出る月が 3 つ以上なら、月の見出しのあるページはみな)
      page_months: カレンダーらしいページに出た月の数
      multi_page_calendar: 何ページにもわたるカレンダー (calendar_pages 3 以上で、月が 3 つ以上か日付の並びが 3 面以上)
    1 ページ目は町の行事などの表紙のことが多いので、1 ページ目だけで決めない"""
    pts = text.split(sep) if text else []
    info = [_page_info(p) for p in pts]
    hit = [i for i, x in enumerate(info) if x["hit"]]
    if not hit:
        return {}, [], []
    cal, months = set(), set()
    for i in hit:
        adj = [j for j in (i - 1, i + 1) if 0 <= j < len(info) and info[j]["grid"] and not info[j]["hit"]]
        if info[i]["grid"] or len(info[i]["months"]) >= 2 or adj:
            cal.add(i)
            cal.update(adj)
    hit_months = set().union(*(info[i]["months"] for i in hit))
    if len(hit_months) >= 3:
        cal.update(i for i in hit if info[i]["months"])
    for i in cal:
        months |= info[i]["months"]
    feats = {"page_hits": [i + 1 for i in hit][:PAGE_HITS_MAX]}
    if cal:
        feats["calendar_pages"] = len(cal)
        feats["page_months"] = len(months)
        feats["gomi_months"] = len(set().union(*(info[i]["months"] for i in cal if info[i]["hit"])))   # ごみの言葉のあるページの月
        grids = sum(1 for i in cal if info[i]["grid"])
        if len(cal) >= 3 and (len(months) >= 3 or grids >= 3):
            feats["multi_page_calendar"] = True
    best = sorted(hit, key=lambda i: (-(i in cal), -info[i]["score"], i))
    return feats, [i + 1 for i in best], sorted(i + 1 for i in cal)


LONG_PDF = 8               # これより多いページの PDF (計画・統計・冊子) は、カレンダーのページが 1 つだけではカレンダーとしない


def pdf_calendar(f):
    """PDF の特徴から、ごみの収集のカレンダーとみなすか: 何ページにもわたるカレンダーか、月が 2 つ以上出るか、
    ページの半分以上がカレンダーのページ (広報の 1 ページだけの行事カレンダーなどは入れない)。
    長い資料 (8 ページを超える) は、カレンダーのページが 2 つ以上で、ごみの言葉のあるページに月が 2 つ以上出るときだけ
    (処理計画・清掃事業の冊子の中の 1 ページの月別の表をカレンダーにしない)"""
    cp = f.get("calendar_pages", 0)
    if not cp:
        return False
    if f.get("multi_page_calendar"):
        return True
    n = f.get("pages_read") or f.get("pages") or 1
    if n > LONG_PDF:
        return cp >= 2 and f.get("gomi_months", f.get("page_months", 0)) >= 2
    return f.get("page_months", 0) >= 2 or 2 * cp >= n


def cover_is_calendar(f):
    """1 ページ目の言葉 (「事業系」「委員会」など) で下げないカレンダーの PDF か: 何ページにもわたるカレンダーか、
    短い PDF (8 ページまで) でカレンダーのページがあるもの。長い資料 (計画・冊子) は表紙の言葉で下げる"""
    n = f.get("pages_read") or f.get("pages") or 1
    return bool(f.get("multi_page_calendar")) or (n <= LONG_PDF and pdf_calendar(f))


def schedule_tables(tables, page_gomi=False):
    """曜日・日付の入った「収集日らしい表」(ごみの言葉があるページか、表の中にごみの言葉がある)"""
    out = []
    for t in tables:
        cells = [nfkc(c) for c in t["cells"]]
        wd = sum(1 for c in cells if WEEKDAY.search(c) or WEEKDAY_CELL.match(c))
        days = sum(1 for c in cells if re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= 31)
        dates = sum(1 for c in cells if DATE.search(c))
        gomi = page_gomi or any(GOMI_ITEM.search(c) for c in cells)
        if gomi and t["rows"] >= 2 and (wd >= 2 or dates >= 3 or days >= 20):
            out.append(t)
    return out


def table_features(tables, page_gomi=False):
    """HTML の表: 全部の数と、曜日・日付の入った「収集日らしい表」の数"""
    sched = schedule_tables(tables, page_gomi)
    return {"tables": len(tables), "schedule_tables": len(sched),
            "schedule_table_cells": max((len(t["cells"]) for t in sched), default=0)}


def labels_of(rec, anchors=()):
    """種類を決める文字: 題名・見出し・PDF の題名と始め・CSV の見出し・リンクの文字"""
    parts = [rec.get("title") or ""] + [h for h in rec.get("headings") or []]
    f = rec.get("features") or {}
    parts += [f.get("pdf_title") or "", rec.get("_head") or ""]
    parts += [" ".join(f.get("header") or [])] + list(anchors)
    return nfkc(" \n ".join(p for p in parts if p))


def update(rec, anchors=()):
    """rec (url・file_type・fetched・title・headings・features を持つ) に kinds・score・format_class を入れ直す"""
    f = rec.setdefault("features", {})
    labels = labels_of(rec, anchors)
    u = url_text(rec["url"])
    kinds = []
    for k, rx in KINDS.items():
        if rx.search(labels) or (k in ("calendar", "how_to", "year_end") and rx.search(u)):
            kinds.append(k)
    if "calendar" in kinds and "change_notice" in kinds and not CAL_STRONG.search(labels):
        kinds.remove("calendar")                              # 「収集日の変更のお知らせ」はカレンダーではない
    if "calendar" not in kinds and f.get("shushubi_count", 0) >= 2 and f.get("weekday_tokens", 0) + f.get("date_tokens", 0) >= 5:
        kinds.append("calendar")
    if "districts" not in kinds and f.get("district_tokens", 0) >= 15 and f.get("weekday_tokens", 0) >= 5 and f.get("gomi_words", 0) >= 5:
        kinds.append("districts")
    if "calendar" not in kinds and pdf_calendar(f):
        kinds.append("calendar")                              # PDF の中のごみの収集のカレンダーのページ (町民カレンダーなど)
    if rec.get("vendor"):
        kinds = sorted(set(kinds) | {"app"})
    own = [rec.get("title") or ""] + list(rec.get("headings") or [])[:3] + [f.get("pdf_title") or ""]
    if not cover_is_calendar(f):
        own.append((rec.get("_head") or "")[:200])            # カレンダーの PDF は、表紙 (1 ページ目) の言葉で下げない
    f["negative"] = sorted(set(NEGATIVE.findall(nfkc(" ".join(own)))))
    ly = fiscal_years(labels)                                 # 題名・リンクの文字の年度 (本文の年度より確か)
    if ly:
        f["label_years"] = ly
    else:
        f.pop("label_years", None)
    f.pop("kinds_from_parent", None)
    inherit = _inherit(rec, kinds, labels)
    if inherit:
        kinds += inherit
        f["kinds_from_parent"] = inherit
    rec["kinds"] = [k for k in KINDS if k in kinds]
    f["gomi_label"] = sum(1 for w in GOMI_STRONG + GOMI_MEDIUM if w in labels)
    rec["score"] = score(rec)
    rec["format_class"] = format_class(rec)
    role = page_role(rec, labels)
    if role:
        rec["role"] = role
    else:
        rec.pop("role", None)
    return rec


def _inherit(rec, kinds, labels):
    """収集日のページからリンクされ、自分では種類の分からない資料 (リンクの文字が地区の名前だけなど) に、リンク元の種類を渡す。
    資料 (PDF など) は、地区らしい名前か、曜日・日付の多い中身か、文字の無い PDF (画像・輪郭線) のとき。
    HTML は中身に曜日・日付の表か、曜日・日付が多いときだけ。申請書・計画などは受け継がない"""
    f = rec.get("features") or {}
    parent = [k for k in f.get("linked_from_schedule") or [] if k in SCHEDULE_KINDS]
    if not parent or kinds or rec.get("vendor") or f.get("negative") or FORM.search(labels) or NEGATIVE.search(labels):
        return []
    evidence = bool(f.get("schedule_tables")) or f.get("weekday_tokens", 0) + f.get("date_tokens", 0) >= 8
    ft = rec.get("file_type")
    if ft == "html":
        ok = rec.get("fetched") and evidence
    elif ft in DOC_TYPES:
        ok = evidence or bool(PLACE.search(labels)) or f.get("pdf_kind") in ("image", "vector")
    else:
        ok = False
    return parent if ok else []


def score(rec):
    f, kinds = rec.get("features") or {}, rec.get("kinds") or []
    s = max((KIND_POINTS[k] for k in kinds), default=0) + 4 * max(0, len(kinds) - 1)
    s += min(12, 4 * f.get("gomi_label", 0))
    s += min(8, f.get("gomi_words", 0) // 4)
    if f.get("schedule_tables"):
        s += 18
    s += min(10, f.get("weekday_tokens", 0) // 3)
    s += min(10, f.get("date_tokens", 0) // 8)
    s += min(12, f.get("district_tokens", 0) // 3)
    ft = rec.get("file_type")
    if ft in ("pdf", "xlsx", "xls", "csv"):
        s += 4
    if ft == "ics":
        s += 10
    if pdf_calendar(f):
        s += min(10, 2 * f["calendar_pages"]) + (6 if f.get("multi_page_calendar") else 0)
    fy = current_fy()
    if any(y in (fy, fy + 1) for y in (f.get("label_years") or []) + (f.get("fiscal_years") or [])):
        s += 5
    if f.get("linked_from_schedule") and ft != "html":
        s += LINKED_BONUS                              # 収集日のページからリンクされた資料
    s -= 12 * min(2, len(f.get("negative") or []))
    if f.get("other_muni"):
        s -= OTHER_MUNI_PENALTY                        # 組合のサイトの、ほかの構成市町村のページ
    if not rec.get("fetched"):
        s = min(s, 60)                                 # 中を見ていないものは控えめに
    return max(0, min(100, s))


def format_class(rec):
    ft = rec.get("file_type")
    f = rec.get("features") or {}
    if rec.get("vendor"):
        return "app"
    if rec.get("not_fetched") in ("robots_disallowed", "blocked"):
        return "blocked"                               # 取ってはいけない・取れない (pdf_unchecked と分ける)
    if ft == "html":
        if not rec.get("fetched"):
            return "html_unchecked"                    # 大きすぎて読まなかったページなど
        return "html_table" if f.get("schedule_tables") else "html_text"
    if ft == "pdf":
        k = f.get("pdf_kind")
        if k == "text":
            return "pdf_text"
        if k in ("image", "vector", "broken_text", "empty"):
            return "pdf_image"
        return "pdf_unchecked"
    if ft in ("xlsx", "xls", "csv"):
        return "xlsx/csv"
    if ft == "ics":
        return "ics"
    return ft or "other"


def relevance(rec):
    """リンク元としての重み: 2 = ごみの本題のページ、1 = ごみの話が出るページ、0 = 関係なし"""
    if not rec:
        return 0
    if set(rec.get("kinds") or []) - {"app"} or rec.get("score", 0) >= 40:
        return 2
    if rec.get("score", 0) >= 15 or (rec.get("features") or {}).get("gomi_label", 0):
        return 1
    return 0


def is_schedule(rec):
    j = rec.get("judge") or {}
    if j.get("is_collection_schedule") is True:
        return True
    if j.get("is_collection_schedule") is False:
        return False
    return bool(set(rec.get("kinds") or []) & set(SCHEDULE_KINDS)) and rec.get("score", 0) >= SCHEDULE_MIN and not rec.get("vendor")


def is_strong(rec):
    """収集日の資料らしいものが見つかった (このあと手がかりの薄いリンクは追わない。案内のページの子は先に取り終える)"""
    return rec.get("fetched") and bool(set(rec.get("kinds") or []) & set(SCHEDULE_KINDS)) and rec.get("score", 0) >= STRONG_MIN


def newest_year(rec):
    """いちばん新しい年度 (題名・リンクの文字の年度があればそれ、無ければ本文の年度)。無ければ 0"""
    f = rec.get("features") or {}
    return max(f.get("label_years") or f.get("fiscal_years") or [0])


JUDGE_NO_PENALTY = 25      # LLM が「収集日の資料ではない」と答えた候補は、並べ方でこれだけ点を下げる (下の組にまとめては落とさない)
NOTICE_PENALTY = 20        # 年末年始・休止のお知らせが主のページ・資料は、並べ方でこれだけ点を下げる (1 年分のカレンダーを先に)
NOTICE_DOC = re.compile(r"年末年始|nenmatsu|nenmatu|nenshi|nensi|kyushi|kyuushi")


def notice_doc(rec):
    """年末年始・収集の休止のお知らせが主のものか (題名・始めの見出し・PDF の題名・URL に「年末年始」「nenmatsu」「kyushi」など)。
    1 年分のカレンダーで「年末年始の収集は…」と書いてあるだけのもの (本文・PDF の始めの文字) は入れない"""
    f = rec.get("features") or {}
    own = " ".join([rec.get("title") or "", " ".join((rec.get("headings") or [])[:1]), f.get("pdf_title") or ""])
    return "year_end" in (rec.get("kinds") or []) and bool(NOTICE_DOC.search(nfkc(own) + " " + url_text(rec.get("url") or "")))


def rank_key(rec):
    """並べ方: LLM が「収集日の資料」と答えたもの → 中を見たもの (取ってはいけない・取れないものは最後) → 点数 →
    (同じ点数なら) 新しい年度の出てくるもの (前の年度の版と今の年度の版が並ぶとき)。
    LLM が「違う」と答えたものは点数を 25 下げて並べる (LLM の見落としで、収集日の資料が出力の上限から落ちないように)。
    年末年始・休止のお知らせが主のもの (notice_doc) は、LLM が「収集日の資料」と答えても先の組に入れず、点数を 20 下げる
    (東松島市・江東区・戸田市: 年末年始・休止のお知らせが、1 年分の日程表より先になっていた)。
    案内のページ (hub) も下げない (パイロットでは、地区ごとの説明のある案内のページを LLM が収集日の資料と答えたものが多い)"""
    j = (rec.get("judge") or {}).get("is_collection_schedule")
    seen = 0 if rec.get("fetched") else 2 if rec.get("not_fetched") in ("robots_disallowed", "blocked") else 1
    notice = notice_doc(rec)
    s = rec.get("score", 0) - (JUDGE_NO_PENALTY if j is False else 0) - (NOTICE_PENALTY if notice else 0)
    return (0 if j is True and not notice else 1, seen, -s, -newest_year(rec), rec["url"])


# ---- 案内のページ (hub) ----

def has_schedule_content(rec):
    """収集日の中身 (曜日・日付の入った表、または曜日・日付の多い本文) があるか。
    年のついた日付 (「2026年9月2日更新」のような新着情報の並び) は数えない"""
    f = rec.get("features") or {}
    dates = max(0, f.get("date_tokens", 0) - f.get("dated_tokens", 0))
    return bool(f.get("schedule_tables")) or f.get("weekday_tokens", 0) + dates >= 8


def page_role(rec, labels=""):
    """hub (案内のページ: 自分がごみのページで、ごみのページ・資料へのリンクが多く、自分には収集日の中身が無い HTML) /
    schedule (収集日の資料) / None (どちらでもない)。ごみのページ = 題名などに強いごみの言葉があるか、種類がある
    (「生涯学習カレンダー」のようにカレンダーだけの言葉では、ごみのページとしない)"""
    f = rec.get("features") or {}
    gomi = any(w in labels for w in GOMI_STRONG) or bool(set(rec.get("kinds") or []) - {"app"})
    if (rec.get("file_type") == "html" and rec.get("fetched") and gomi and not has_schedule_content(rec)
            and (f.get("hub_links", 0) + f.get("hub_docs", 0) >= HUB_MIN_LINKS or f.get("hub_cal_links", 0) >= 2)):
        return "hub"
    if set(rec.get("kinds") or []) & set(SCHEDULE_KINDS) and rec.get("score", 0) >= SCHEDULE_MIN and not rec.get("vendor"):
        return "schedule"
    return None


def hub_link_kind(anchor, url):
    """案内のページのリンクの種類: cal (収集日・カレンダー・地区の言葉) / gomi (ごみの言葉) /
    place (地区・町の名前らしい短い文字、「あ行」など) / None。申請書・入札・イベントなどは None"""
    a, u = nfkc(anchor), url_text(url)
    if NEGATIVE.search(a) or FORM.search(a) or EVENT.search(a) or EVENT.search(u):
        return None
    if CAL_LINK.search(a) or CAL_LINK_URL.search(u):
        return "cal"
    al = a.lower()
    if any(w in al for w in GOMI_STRONG + GOMI_MEDIUM) or any(w in u for w in URL_STRONG):
        return "gomi"
    if a and len(a) <= 30 and (PLACE.search(a) or KANA_ROW.search(a)) and not PLACE_STOP.search(a):
        return "place"
    return None


def hub_expand_mode(rec):
    """子を広げるか: own (自分が収集日・地区のページ。cal の子・地区の名前だけの資料・同じフォルダの地区らしい子を広げる) /
    links (自分は収集日のページではないが、収集日・カレンダー・地区の言葉のリンクがある。cal の子だけ) / None (広げない)。
    収集日の表のあるページでも、自分が収集日・地区のページで、地区ごとのカレンダーへのリンクが並ぶなら own"""
    if rec.get("file_type") != "html" or not rec.get("fetched") or rec.get("offsite"):
        return None
    f = rec.get("features") or {}
    own = bool(set(rec.get("kinds") or []) & set(SCHEDULE_KINDS)) and not f.get("negative")
    if rec.get("role") == "hub":
        if own:
            return "own"
        return "links" if f.get("hub_cal_links", 0) >= 1 else None
    if own and f.get("hub_cal_links", 0) + f.get("hub_place_links", 0) >= 2:
        return "own"
    return None


def judge_rank(rec, anchors=()):
    """LLM に見せる順の点: 点数に、収集日の中身の手がかり (表・PDF のカレンダーのページ・資料の URL やリンクの文字) を足し、
    案内のページ (hub) は下げる"""
    f = rec.get("features") or {}
    r = rec.get("score", 0)
    if f.get("schedule_tables"):
        r += 10
    if pdf_calendar(f):
        r += 10 + (5 if f.get("multi_page_calendar") else 0)
    elif f.get("page_hits"):
        r += 3
    if rec.get("file_type") != "html" and is_cal_doc(rec, anchors):
        r += 5
    if rec.get("role") == "hub":
        r -= 10
    return r


def is_cal_doc(rec, anchors=()):
    """URL かリンクの文字に カレンダー・収集・ごみ がある"""
    return bool(CAL_DOC.search(url_text(rec["url"]) + " " + nfkc(" ".join(anchors)).lower()))


def decide(cands, site_statuses):
    """自治体ごとの形式 → (format_class, いちばんの候補 or None, 見つかった形式の一覧)。
    中を見た候補を先に選ぶ。いちばんが robots.txt で禁止・ブロックの資料なら形式は blocked"""
    sched = sorted((c for c in cands if is_schedule(c)), key=rank_key)
    if sched:
        formats = sorted({c["format_class"] for c in sched})
        return sched[0]["format_class"], sched[0], formats
    if any(c.get("vendor") for c in cands):
        return "app_only", None, ["app"]
    st = {"unreachable" if s == "top_failed" else s for s in site_statuses if s != "same_site"}   # トップが 404・5xx = 入れなかった
    if st and st <= {"blocked", "robots_disallowed", "unreachable"}:
        return "blocked" if st & {"blocked", "robots_disallowed"} else "unreachable", None, []
    return "not_found", None, []
