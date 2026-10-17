#!/usr/bin/env python3
"""金沢市 地区別収集カレンダー (HTML の表) → schedule (schema v0.1) + 検証

入力: data/kanazawa_rules.html (区域表。地区名 → カレンダーページ#アンカー)
      data/kanazawa_cal/*.html (python fetch.py で取得したカレンダーページ)
出力: out/kanazawa_schedules.json, 検証結果を表示

表の形 (2026年度版):
  見出し1段目: 燃やすごみ | (月) | 燃やさないごみ | 資源 あき缶・ペットボトル プラスチック資源など (2列) | 資源 プラスチック資源 | びん
  見出し2段目: 各品目の規則 (第1水曜日 / 第2・4木曜日 / 第5木曜日 / 第3水曜日)
  本体: 1列目 = 燃やすごみの規則 (火曜日・金曜日、年末は12月29日まで、年始は1月5日から)、以降 = 月ごとの実日付
自治体が出している実日付を正とし、規則 (第N曜日) との食い違いはすべて報告する。RRULE は圧縮形式にすぎない。
"""
import datetime as dt, hashlib, html, json, pathlib, re, sys, collections
from dateutil.rrule import rrulestr
from normalize import fit_rule, WD_IDX

sys.stdout.reconfigure(encoding="utf-8")
BASE = "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/"
CAL = pathlib.Path("data/kanazawa_cal")
WD = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
Z2H = str.maketrans("０１２３４５６７８９", "0123456789")
CATS = [  # 見出しの文字列 → (category, label)。順に照合する
    (r"燃やすごみ", ("burnable", "燃やすごみ")),
    (r"燃やさないごみ", ("nonburnable", "燃やさないごみ")),
    (r"あき缶.*ペットボトル", ("recyclable", "資源 (あき缶・ペットボトル・プラスチック資源など)")),
    (r"プラスチック資源", ("plastic", "資源 (プラスチック資源のみ)")),
    (r"びん", ("bottle", "びん")),
]

def text(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).replace("\xa0", " ").strip()

def grid(table):
    """rowspan / colspan を展開した表 (行 × 列 の文字列)"""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    out, pend = [], {}                                # pend[(r, c)] = rowspan で下の行に伸びるセル
    for r, tr in enumerate(rows):
        cells = re.findall(r"<(t[hd])([^>]*)>(.*?)</\1>", tr, re.S)
        row, c = [], 0
        for _, attrs, body in cells:
            while (r, c) in pend: row.append(pend.pop((r, c))); c += 1
            cs = int((re.search(r'colspan="(\d+)"', attrs) or [0, 1])[1])
            rs = int((re.search(r'rowspan="(\d+)"', attrs) or [0, 1])[1])
            v = text(body)
            for k in range(cs):
                row.append(v)
                for d in range(1, rs): pend[(r + d, c + k)] = v
            c += cs
        while (r, c) in pend: row.append(pend.pop((r, c))); c += 1
        out.append(row)
    return out

def nth_dates(rule, year, month):
    """'第2・4木曜日' → その月の該当日"""
    m = re.fullmatch(r"第([\d・]+)(.)曜日", rule)
    if not m: return None
    ns, wd = [int(x) for x in m[1].split("・")], WD[m[2]]
    days = [d for d in range(1, 32) if _ok(year, month, d) and dt.date(year, month, d).weekday() == wd]
    return [days[n - 1] for n in ns if n <= len(days)]

def _ok(y, m, d):
    try: dt.date(y, m, d); return True
    except ValueError: return False

def burnable(rule, fy):
    """'火曜日・ 金曜日 年末は 12月29日 （火曜日） まで 年始は 1月5日 （火曜日） から' → 日付"""
    wds = [WD[w] for w in re.findall(r"(.)曜日", rule.split("年末")[0])]
    end = re.search(r"年末は\s*(\d+)月(\d+)日", rule); start = re.search(r"年始は\s*(\d+)月(\d+)日", rule)
    last = dt.date(fy, int(end[1]), int(end[2])) if end else None
    first = dt.date(fy + 1, int(start[1]), int(start[2])) if start else None
    d, out = dt.date(fy, 4, 1), []
    while d <= dt.date(fy + 1, 3, 31):
        if d.weekday() in wds and not (last and first and last < d < first): out.append(d)
        d += dt.timedelta(days=1)
    return wds, last, first, out

# ---- 地区 → ページ#アンカー (区域表のリンクから) ----
top = open("data/kanazawa_rules.html", encoding="utf-8", errors="replace").read()
anchors = {}
for h, a in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', top, re.S):
    m = re.match(re.escape(BASE) + r"(\d+)\.html#(\w+)$", h)
    if m and m[1] != "20102": anchors.setdefault(text(a), (m[1], m[2]))

schedules, problems, stats = [], [], collections.Counter()
for district, (page, anchor) in sorted(anchors.items()):
    path = CAL / f"{page}.html"
    if not path.exists():                                  # 初回は市のページを取得 (data/ は git 管理外)
        from fetch import get
        CAL.mkdir(parents=True, exist_ok=True)
        path.write_bytes(get(f"{BASE}{page}.html")[0])
    src = path.read_text(encoding="utf-8", errors="replace")
    tables = [t for t in re.findall(r"<table.*?</table>", src, re.S)
              if (cap := re.search(r"<caption>(.*?)</caption>", t, re.S))
              and text(cap[1]) in (district, re.sub(r"（.*?）", "", district))]   # 区域表「安原（緑団地含む）」⇔ 表「安原」
    if len(tables) != 1:
        problems.append((district, f"table count {len(tables)}")); continue
    start = src.find(tables[0])
    head = text(src[src.rfind("</table>", 0, start) + 8 if src.rfind("</table>", 0, start) > 0 else max(0, start - 3000):start])
    fyh = re.findall(r"(\d{4})年度版", head)
    fy = int(fyh[-1]) if fyh else None
    center = (re.findall(r"（([^（）]*管理センター)）", head) or [None])[-1]
    year_end_note = "年末年始の影響で" in head          # 表の直前の「（※）年末年始の影響で、収集日を変更しています」
    g = grid(tables[0])
    hdr, rules, body = g[0], g[1], g[2:]
    title = text((re.search(r"<title>(.*?)</title>", src, re.S) or [None, ""])[1])
    section = (re.findall(r"([^\s（）]+（[^（）]*管理センター）\s*\d{4}年度版)", head) or [None])[-1]
    retrieved = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")
    source = {"url": f"{BASE}{page}.html#{anchor}", "page_title": title, "section": section,
              "publisher": "金沢市 ごみ減量推進課", "retrieved_at": retrieved,
              "page_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "table_sha256": hashlib.sha256(tables[0].encode("utf-8")).hexdigest()}
    zone_id = f"17201-{district}"
    # 列 → 品目
    cols = {}
    for c, h in enumerate(hdr):
        for pat, cat in CATS:
            if re.search(pat, h): cols.setdefault(cat, []).append(c); break
    # 燃やすごみ
    wds, last, first, bdates = burnable(body[0][0], fy)
    fit = fit_rule(bdates, dt.date(fy, 4, 1), dt.date(fy + 1, 3, 31))
    schedules.append({"zone_id": zone_id, "district": district, "category": "burnable", "category_label": "燃やすごみ",
                      "official_label": hdr[0],
                      "rule_text": body[0][0], **fit, "valid_from": f"{fy}-04-01", "valid_to": f"{fy + 1}-03-31",
                      "source": source, "management_center": center, "year_end_note": year_end_note})
    stats["burnable"] += 1
    # 月ごとの実日付の品目
    for (cat, label), cs in cols.items():
        if cat == "burnable": continue
        rule_text = rules[cs[0]]
        dates, mism = [], []
        for row in body:
            mm = re.fullmatch(r"(\d+)月", row[1].translate(Z2H))
            if not mm: continue
            mon = int(mm[1]); yr = fy if mon >= 4 else fy + 1
            got = sorted({int(x) for c in cs for x in re.findall(r"\d+", row[c].translate(Z2H))})
            dates += [dt.date(yr, mon, d) for d in got]
            exp = nth_dates(rule_text, yr, mon)
            if exp is not None and sorted(exp) != got:
                mism.append({"month": f"{yr}-{mon:02d}", "rule": exp, "table": got})
        fit = fit_rule(dates, dt.date(fy, 4, 1), dt.date(fy + 1, 3, 31)) if dates else {"rule_kind": "explicit", "dates": []}
        # 往復検証: 圧縮形式から元の日付集合に戻るか
        if fit["rule_kind"] == "rrule":
            rr = fit["rrule"].replace(";PHASE=+7d", "")
            st = dt.date(fy, 4, 1) + dt.timedelta(days=7 if "PHASE" in fit["rrule"] else 0)
            gen = {x.date() for x in rrulestr(rr, dtstart=dt.datetime.combine(st, dt.time()))
                   .between(dt.datetime(fy, 4, 1), dt.datetime(fy + 1, 3, 31), inc=True)}
            gen = (gen - set(map(dt.date.fromisoformat, fit["exdates"]))) | set(map(dt.date.fromisoformat, fit["rdates"]))
            stats["roundtrip_ok" if gen == set(dates) else "roundtrip_ng"] += 1
        elif fit["rule_kind"] == "segments":
            gen = set()
            for sg in fit["segments"]:
                gen |= {x.date() for x in rrulestr(sg["rrule"] + ";UNTIL=" + sg["until"].replace("-", "") + "T235959",
                                                   dtstart=dt.datetime.fromisoformat(sg["dtstart"]))}
                gen -= set(map(dt.date.fromisoformat, sg["exdates"]))
            stats["roundtrip_ok" if gen == set(dates) else "roundtrip_ng"] += 1
        stats[f"{cat}:{fit['rule_kind']}"] += 1
        stats["rule_mismatch_months"] += len(mism)
        if mism: problems.append((district, cat, rule_text, mism[:3]))
        schedules.append({"zone_id": zone_id, "district": district, "category": cat, "category_label": label,
                          "official_label": hdr[cs[0]],
                          "rule_text": rule_text, **fit, "dates": [str(d) for d in dates],
                          "rule_mismatches": mism, "valid_from": f"{fy}-04-01", "valid_to": f"{fy + 1}-03-31",
                          "source": source, "management_center": center, "year_end_note": year_end_note})

json.dump({"municipality": {"lg_code": "172014", "name": "石川県金沢市"}, "fiscal_year": fy,
           "schedules": schedules}, open("out/kanazawa_schedules.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"districts={len(anchors)} schedules={len(schedules)} {dict(stats)}")
print("problems:", len(problems))
for p in problems[:30]: print("  ", p)
