"""版の証拠 (年の印と時の証拠) と、祝日の印の突き合わせ (DESIGN.md §2.2・§3.3)

  python nationwide/readers/version.py <資料のファイル> --from 2026-04-01 --to 2027-03-31 [--url URL] [--anchor リンクの文字]
                                       [--last-modified "Tue, 07 Apr 2026 00:26:27 GMT"] [--fetched-at 2026-10-19T03:14:18+09:00]

曜日の並びは年の証拠にしない (2020 年度と 2026 年度は 4 月〜3 月の日と曜日の並びがすべて同じ)。次の 2 つがそろったときだけ ok:
  1. 年の印 (令和8年度・R8・2026年度・2026年4月～2027年3月) が、ファイルの中の見える文字か、ファイルの名前にある。
     リンクの文字にしか無いとき (anchor_only) は ok にしない。PDF では白い文字・見えない文字を数えない (川越の白い「令和２年度」)。
     暦年だけの印 (「2026年」) は、期間がちょうどその暦年のときだけ年の印にする (年度・暦年の中の短い期間では印にしない)。
     更新日・掲載日の日付も年の印にしない。
     年の印どうしがぶつかれば (題・ファイルの名前・本文・リンクの文字) conflict
  2. 時の証拠 (HTTP の Last-Modified、ページの「更新日」、PDF・Excel のメタデータの更新日 (無ければ作成日)) の少なくとも 1 つが
     有効期間の始まりの 15 か月前から終わりまでの間にあり、どれもその範囲より古くない。古いものと新しいものがあれば conflict
"""
import datetime as dt, email.utils, io, re, sys, unicodedata, urllib.parse

ERA = {"令和": 2018, "平成": 1988}
WINDOW_MONTHS = 15
DYNAMIC_SECONDS = 2 * 3600          # Last-Modified が取った時刻とこれより近ければ、動的に作るページとみなして数えない
MIN_VISIBLE_PT = 3.0                # これより小さい文字は読めない (日高の「使い方」の図に縮めて入った令和６年度の表) ので数えない


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def _era_year(era, n):
    return ERA[era] + (1 if n in ("元", "1") else int(n))


# ---- 年の印 ----
_ERA_N = r"(令和|平成)\s*(元|\d{1,2})\s*"
_AD = r"(?:\(\s*20\d\d\s*年?\s*\)\s*)?"            # 令和８年（２０２６年）４月 の西暦のかっこ書き
TOKEN_PATTERNS = [
    # 期間: 2026年4月～2027年3月 / 令和8年4月〜令和9年3月 / R8年4月1日～R9年3月31日
    ("span", re.compile(r"(?:(20\d\d)|" + _ERA_N + r"|R\s*(\d{1,2}))\s*年\s*" + _AD + r"(\d{1,2})\s*月(?:\s*\d{1,2}\s*日)?"
                        r"(?:\s*\([^)]{1,3}\))?\s*[~〜～ー\-－から]+\s*(?:(20\d\d)|" + _ERA_N + r"|R\s*(\d{1,2}))?\s*年?\s*"
                        + _AD + r"(\d{1,2})\s*月")),
    ("fy", re.compile(_ERA_N + r"年度")),
    ("fy", re.compile(r"(20\d\d)\s*年度")),
    ("fy", re.compile(r"(?<![A-Za-z0-9])R\s*(\d{1,2})\s*年度")),
    ("fy_short", re.compile(r"(?<![A-Za-z0-9])R\s*(\d{1,2})(?![\d年])")),
    ("year", re.compile(_ERA_N + r"年(?!度)")),
    ("year", re.compile(r"(?<!\d)(20\d\d)\s*年(?!度)")),
]


# ページ・資料の更新日・掲載日 (時の証拠であって年の印ではない。§3.3 の 2)。年の印を探す前に消す
META_DATE = re.compile(r"(?:最終)?(?:更新|掲載|公開|作成|登録|発行)日(?:時)?\s*[:：]?\s*"
                       r"(?:20\d\d\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]\s*\d{1,2}\s*日?|"
                       r"(?:令和|平成)\s*(?:元|\d{1,2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?)")


def year_tokens(text):
    """文字 → 年の印のリスト [{"text", "kind" (fy / year / span), "fiscal_year", "from", "to"}]。from・to は月の初めの日付 (span と年度)。
    「更新日：2026年4月7日」のような更新日・掲載日の日付は年の印にしない (時の証拠。ページの本文の範囲に入っていても)"""
    t = META_DATE.sub(lambda m: " " * len(m.group(0)), nfkc(text))
    out, used = [], []
    for kind, pat in TOKEN_PATTERNS:
        for m in pat.finditer(t):
            if any(a <= m.start() < b for a, b in used):
                continue
            g = [x for x in m.groups()]
            rec = {"text": m.group(0).strip(), "kind": kind, "fiscal_year": None, "from": None, "to": None}
            try:
                if kind == "span":
                    y1 = int(g[0]) if g[0] else (_era_year(g[1], g[2]) if g[1] else 2018 + int(g[3]))
                    m1 = int(g[4])
                    y2 = int(g[5]) if g[5] else (_era_year(g[6], g[7]) if g[6] else (2018 + int(g[8]) if g[8] else None))
                    m2 = int(g[9])
                    if y2 is None:
                        y2 = y1 + (1 if m2 < m1 else 0)
                    if not (1 <= m1 <= 12 and 1 <= m2 <= 12):
                        continue
                    rec["from"], rec["to"] = dt.date(y1, m1, 1), _month_end(y2, m2)
                    rec["fiscal_year"] = y1 if m1 >= 4 else y1 - 1
                elif kind in ("fy", "fy_short"):
                    fy = _era_year(g[0], g[1]) if len(g) > 1 and g[0] in ERA else (int(g[0]) if len(g[0]) == 4 else 2018 + int(g[0]))
                    if kind == "fy_short" and not 1 <= fy - 2018 <= 30:
                        continue
                    rec.update(fiscal_year=fy, kind="fy", **{"from": dt.date(fy, 4, 1), "to": dt.date(fy + 1, 3, 31)})
                else:
                    y = _era_year(g[0], g[1]) if g[0] in ERA else int(g[0])
                    rec.update(**{"from": dt.date(y, 1, 1), "to": dt.date(y, 12, 31)})
            except (ValueError, TypeError, IndexError):
                continue
            out.append(rec)
            used.append((m.start(), m.end()))
    return out


def _month_end(y, m):
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))


def filename_tokens(url):
    """ファイルの名前の年の印 (R8_01.pdf・r08_chomin_calendar.pdf・reiwa8nendo….pdf・2026tikubetsu.xls)"""
    name = urllib.parse.unquote(urllib.parse.urlsplit(url or "").path.rsplit("/", 1)[-1])
    out = []
    for m in re.finditer(r"(?i)(?:^|[^a-z0-9])(?:r|reiwa)0?(\d{1,2})(?!\d)", name):
        n = int(m.group(1))
        if 1 <= n <= 30:
            fy = 2018 + n
            out.append({"text": m.group(0).lstrip("_-. "), "kind": "fy", "fiscal_year": fy,
                        "from": dt.date(fy, 4, 1), "to": dt.date(fy + 1, 3, 31)})
    for m in re.finditer(r"(?<!\d)(20[12]\d)(?!\d)", name):
        y = int(m.group(1))
        out.append({"text": m.group(1), "kind": "fy", "fiscal_year": y, "from": dt.date(y, 4, 1), "to": dt.date(y + 1, 3, 31)})
    out += [dict(x, kind="fy") for x in year_tokens(name) if x["kind"] == "fy"]
    return out


def token_matches(tok, valid_from, valid_to):
    """年の印が期間と合うか (ok にしてよい強い印として): 年度・期間の印は期間の真ん中を含む。
    暦年の印 (「2026年」) は、期間がちょうどその暦年 (1月1日〜12月31日) のときだけ (川口の暦年のカレンダー)。
    暦年の中の短い期間 (半年ごとの 2026年4月〜9月) には合うとしない (2025 年度の資料の「2026年1月」でも当たってしまう)"""
    lo, hi = _date(valid_from), _date(valid_to)
    if tok["kind"] == "year":
        y = tok["from"].year
        return lo == dt.date(y, 1, 1) and hi == dt.date(y, 12, 31)
    mid = lo + (hi - lo) / 2
    return tok["from"] <= mid <= tok["to"]


def token_weak(tok, valid_from, valid_to):
    """暦年の印が期間の年のどれかに当たるが、期間がちょうどその暦年ではない (年度・暦年の中の短い期間): 弱い印。
    2025 年度の資料にも「2026年1月」があるので、これだけでは ok にしない (reasons に weak_year_token)"""
    lo, hi = _date(valid_from), _date(valid_to)
    return tok["kind"] == "year" and lo.year <= tok["from"].year <= hi.year and not token_matches(tok, valid_from, valid_to)


# ---- 見える文字 (PDF) ----
def _is_whiteish(color):
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return min(r, g, b) >= 240


def pdf_visible_text(pdf_bytes, pages=None):
    """PDF → [(ページ, 文字, フォントの大きさ, 見えるか, わけ)]。白い文字は、下に白くない塗りの図形があるときだけ見えるとする。
    透明 (alpha 0)・ページの外・3pt より小さい文字も見えないとする"""
    import fitz
    out = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for pno in range(len(doc)) if pages is None else [p for p in pages if p < len(doc)]:
            page = doc[pno]
            fills = []
            try:
                for dr in page.get_drawings():
                    f = dr.get("fill")
                    if f and dr.get("rect") is not None and min(f) < 0.94:
                        fills.append(dr["rect"])
            except Exception:                                  # 図形の読めない PDF は、白い文字をすべて見えないとする
                fills = []
            for b in page.get_text("dict").get("blocks", []):
                for line in b.get("lines", []):
                    for sp in line.get("spans", []):
                        txt = sp.get("text", "")
                        if not txt.strip():
                            continue
                        r = fitz.Rect(sp["bbox"])
                        why = None
                        if sp.get("alpha", 255) == 0:
                            why = "transparent"
                        elif not r.intersects(page.rect):
                            why = "off_page"
                        elif sp.get("size", 0) < MIN_VISIBLE_PT:
                            why = "too_small"
                        elif _is_whiteish(sp.get("color", 0)):
                            c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                            if not any(f.contains(c) for f in fills):
                                why = "white_on_white"
                        out.append((pno + 1, txt, round(sp.get("size", 0), 1), why is None, why))
    return out


def pdf_tokens(pdf_bytes):
    """PDF の見える文字の年の印 → (題の印, 本文の印, 見えない文字の年の印)。題 = 1 ページ目の大きい文字 (上から 3 番目の大きさまで)"""
    spans = pdf_visible_text(pdf_bytes)
    vis = [s for s in spans if s[3]]
    p1 = [s for s in vis if s[0] == 1]
    sizes = sorted({s[2] for s in p1}, reverse=True)[:3]
    title = " ".join(s[1] for s in p1 if s[2] in sizes)
    body = " ".join(s[1] for s in vis)
    hidden = " ".join(s[1] for s in spans if not s[3])
    return year_tokens(title), year_tokens(body), year_tokens(hidden)


def html_tokens(html_text):
    """HTML の年の印 → (題の印 (title・h1・h2・caption), 本文の印)。本文は content.py の本文の範囲"""
    import content
    soup = content.parse(html_text)
    heads = [soup.title.get_text(" ") if soup.title else ""] + [h.get_text(" ") for h in soup.find_all(["h1", "h2", "caption"])]
    reg = content.content_region(html_text)
    return year_tokens(" ".join(heads)), year_tokens(reg.text())


# ---- 時の証拠 ----
def _date(x):
    if x is None:
        return None
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def http_date(v):
    """Last-Modified の値 → datetime (UTC)。読めなければ None"""
    try:
        d = email.utils.parsedate_to_datetime(v)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


UPDATED_RE = re.compile(r"(?:最終)?更新日\s*[:：]?\s*(?:(20\d\d)\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})|"
                        r"(令和|平成)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2}))")


def page_updated(text):
    """ページの「更新日：2026年4月1日」「最終更新日 2026-04-01」「更新日：令和8年4月1日」 → 日付のリスト"""
    out = []
    for m in UPDATED_RE.finditer(nfkc(text)):
        g = m.groups()
        try:
            if g[0]:
                out.append(dt.date(int(g[0]), int(g[1]), int(g[2])))
            else:
                out.append(dt.date(_era_year(g[3], g[4]), int(g[5]), int(g[6])))
        except ValueError:
            continue
    return out


def pdf_date(v):
    """PDF のメタデータの日付 (D:20260303094000+09'00') → date"""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", v or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def pdf_times(pdf_bytes, source_id=None):
    """PDF のメタデータの更新日 (無ければ作成日) → 時の証拠のリスト"""
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        md = doc.metadata or {}
    mod, cre = pdf_date(md.get("modDate")), pdf_date(md.get("creationDate"))
    if mod:
        return [_time("pdf_modified", mod, source_id)]
    return [_time("pdf_created", cre, source_id)] if cre else []


def xlsx_times(xlsx_bytes, source_id=None):
    """Excel (xlsx) の文書のプロパティの更新日 (無ければ作成日)"""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True)
    except Exception:
        return []
    p = wb.properties
    if p.modified:
        return [_time("xlsx_modified", p.modified.date(), source_id)]
    return [_time("xlsx_created", p.created.date(), source_id)] if p.created else []


def _time(kind, value, source_id=None, note=None):
    r = {"kind": kind, "value": _date(value).isoformat()}
    if source_id:
        r["source_id"] = source_id
    if note:
        r["note"] = note
    return r


def http_times(last_modified, fetched_at=None, source_id=None):
    """Last-Modified → 時の証拠 (取った時刻とほとんど同じなら、動的に作るページとして note を付けて数えない)"""
    lm = http_date(last_modified)
    if lm is None:
        return []
    note = None
    if fetched_at:
        fa = dt.datetime.fromisoformat(fetched_at)
        if abs((fa - lm).total_seconds()) < DYNAMIC_SECONDS:
            note = "dynamic"
    return [_time("last_modified", lm.date(), source_id, note)]


# ---- 版の証拠 ----
def window(valid_from, valid_to):
    lo = _date(valid_from)
    y, m = lo.year, lo.month - WINDOW_MONTHS
    while m < 1:
        m += 12
        y -= 1
    return dt.date(y, m, min(lo.day, 28)), _date(valid_to)


def year_evidence(valid_from, valid_to, in_file_title=(), in_file_body=(), file_name=(), anchor=(), times=(),
                  hidden=(), token_ref=None, place=None):
    """期間と、見つかった年の印・時の証拠 → §2.2 の year_evidence ({"token", "anchor_tokens", "time", "conflict", "ok", "reasons"})。
    年の印のぶつかり (§3.3 の 1。version の裁定へ):
      - 題 (in_file_title) に期間と合わない年度・期間の印がある (「令和7年度 令和8年度」)
      - ファイルの名前に期間と合わない年度の印がある (題は令和8年度、名前は R7_calendar.pdf・gomi2019.pdf)
      - 本文の年度・期間の印がどれも期間と合わない (本文は「令和2年度」だけ、名前は R8_cal.pdf)
      - リンクの文字の年の印が、ファイルの中の年の印とぶつかる
      - 期間と合うのが暦年の印 (「2026年」) だけで、本文に期間と合わない年度・期間の印 (「令和7年度」) がある
    本文に合う年度・期間の印と合わない印が混じるもの (町民カレンダーの記事の「令和7年度」「2030年度」) は、ぶつかりにしない"""
    reasons, conflict = [], None
    if valid_to is None:
        return {"token": None, "anchor_tokens": [a["text"] for a in anchor], "time": list(times), "conflict": None,
                "ok": False, "reasons": ["undated"]}
    match = lambda ts: [t for t in ts if token_matches(t, valid_from, valid_to)]
    other = lambda ts: [t for t in ts if t["kind"] in ("fy", "span") and not token_matches(t, valid_from, valid_to)]
    places = (("in_file", in_file_title, place or "title"), ("in_file", in_file_body, "body"), ("file_name", file_name, "file_name"))
    token, tok_kind = None, None
    for where, ts, pl in places:
        mt = [t for t in match(ts) if t["kind"] in ("fy", "span")] or match(ts)
        if mt:
            token = {"text": mt[0]["text"], "where": where, "place": pl, "visible": True, "fiscal_year": mt[0]["fiscal_year"]}
            tok_kind = mt[0]["kind"]
            if token_ref:
                token["ref"] = token_ref
            break
    if token is None and match(anchor):
        token = {"text": match(anchor)[0]["text"], "where": "anchor_only", "place": "anchor", "visible": True,
                 "fiscal_year": match(anchor)[0]["fiscal_year"]}
        tok_kind = match(anchor)[0]["kind"]
        reasons.append("anchor_only")
    if token is None:
        reasons.append("no_year_token")
        if any(token_weak(t, valid_from, valid_to) for _, ts, _ in places for t in ts):
            reasons.append("weak_year_token")                  # 暦年の印しか無い (年度の期間では年の印にならない)
        if any(other(ts) for _, ts, _ in places):
            reasons.append("year_token_outside_period")        # 年の印はあるが期間と合わない (江東の nenmatunenshi2019.html)
    if other(in_file_title):
        conflict = {"kind": "token", "note": "題に期間と合わない年の印がある",
                    "tokens": [t["text"] for t in in_file_title]}
    elif token and other(file_name):
        conflict = {"kind": "token", "note": "ファイルの名前の年の印が期間と合わない",
                    "tokens": [token["text"]] + [t["text"] for t in file_name]}
    elif token and other(in_file_body) and not match(in_file_body):
        conflict = {"kind": "token", "note": "本文の年度の印がどれも期間と合わない",
                    "tokens": [token["text"]] + sorted({t["text"] for t in other(in_file_body)})}
    elif token and tok_kind == "year" and other(in_file_body):
        conflict = {"kind": "token", "note": "期間と合うのは暦年の印だけで、本文に期間と合わない年度の印がある",
                    "tokens": [token["text"]] + sorted({t["text"] for t in other(in_file_body)})}
    elif token and token["where"] != "anchor_only" and other(anchor) and not match(anchor):
        conflict = {"kind": "token", "note": "リンクの文字の年の印が、ファイルの中の年の印とぶつかる",
                    "tokens": [token["text"]] + [t["text"] for t in anchor]}
    if hidden and match(hidden) == [] and other(hidden):
        reasons.append("hidden_other_year")                    # 白い文字などに別の年度がある (数えないが記録する)
    lo, hi = window(valid_from, valid_to)
    ts = []
    for t in times:
        t = dict(t)
        d = _date(t["value"])
        t["in_window"] = lo <= d <= hi
        ts.append(t)
    counted = [t for t in ts if t.get("note") != "dynamic"]
    older = [t for t in counted if _date(t["value"]) < lo]
    inside = [t for t in counted if t["in_window"]]
    time_ok = bool(inside) and not older
    if not counted:
        reasons.append("no_time_evidence")
    elif not inside:
        reasons.append("time_outside_window")
    if older and inside and conflict is None:
        conflict = {"kind": "time", "note": "時の証拠がぶつかる (古いファイルを上げ直したときの形)",
                    "older": [t["value"] for t in older], "in_window": [t["value"] for t in inside]}
    elif older and not inside:
        reasons.append("time_old")
    ok = bool(token) and token["where"] in ("in_file", "file_name") and conflict is None and time_ok
    if conflict:
        reasons.append(f"conflict_{conflict['kind']}")
    return {"token": token, "anchor_tokens": [a["text"] for a in anchor], "time": ts, "conflict": conflict, "ok": ok,
            "reasons": reasons}


# ---- 祝日の印の突き合わせ (§4.1 の 14) ----
def holiday_mark_check(marked_dates, valid_from, valid_to):
    """印字のカレンダーの祝日の印 (赤い数字・「祝」) の日 → 祝日の表との違い {"extra": 表に無い印, "missing": 印の無い休日,
    "other_fy": 印が別の年度の祝日に合う年度 (2020 年度のカレンダーの使い回しなら 2020)}"""
    import holidays
    lo, hi = _date(valid_from), _date(valid_to)
    marked = {_date(x) for x in marked_dates}
    table = {d for d in holidays.table() if lo <= d <= hi}
    extra = sorted(marked - table)
    missing = sorted(table - marked)
    other = []
    if extra:
        for fy in range(holidays.FIRST_FY, holidays.LAST_FY + 1):
            shift = dt.date(fy, 4, 1) - lo
            hs = {d - shift for d in holidays.fiscal_year(fy)}
            if fy != lo.year and extra and set(extra) <= hs:
                other.append(fy)
    return {"extra": [str(d) for d in extra], "missing": [str(d) for d in missing], "other_fy": other,
            "ok": not extra and not missing}


def main(argv=None):
    import argparse, json, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="資料の版の証拠を見る")
    ap.add_argument("file")
    ap.add_argument("--from", dest="vf", required=True)
    ap.add_argument("--to", dest="vt", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--anchor", default="")
    ap.add_argument("--last-modified")
    ap.add_argument("--fetched-at")
    a = ap.parse_args(argv)
    body = pathlib.Path(a.file).read_bytes()
    times = http_times(a.last_modified, a.fetched_at) if a.last_modified else []
    hidden = ()
    if body[:5] == b"%PDF-":
        title, text, hidden = pdf_tokens(body)
        times += pdf_times(body)
    else:
        import decode
        html = decode.decode(body, "text/html").text
        title, text = html_tokens(html)
        times += [_time("page_updated", d) for d in page_updated(html)]
    ev = year_evidence(a.vf, a.vt, title, text, filename_tokens(a.url), year_tokens(a.anchor), times, hidden)
    print(json.dumps(ev, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
