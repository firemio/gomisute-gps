"""金沢・女川の回帰 (DESIGN.md §4.3)。この版は前半: 今のデータ (スキーマ v0.1 と女川の形) を v0.2 に書き直して確かめる

  python nationwide/readers/eval/regress.py              # 書き直して K1・K3 (分け方)・O1 を確かめる (ネットに出ない)
  python nationwide/readers/eval/regress.py --write      # さらに out/schedules/172014.json・045811.json と
                                                         # eval/fixtures/kanazawa_rule_vs_printed.json を書く

入力 (どれもリポジトリか data/ の下のキャッシュ):
  金沢: out/kanazawa_schedules.json (v0.1)、data/kanazawa_rules.csv・data/kanazawa_rules.html (区域表と市全体の年末年始の文)、
        data/kanazawa_cal/*.html (カレンダーのページ)、data/discover/cache/ (ルールのページ 7608.html と Last-Modified)
  女川: out/onagawa_data.json・out/onagawa_reading.json (final)、data/discover/cache/ (町民カレンダーの PDF)

確かめること:
  K1  355 系列の日付が v0.1 と 1 日も違わない。圧縮の形 (RRULE・segments) から dates に戻る。71 地区 × 371 日の答えが同じ
  K3  規則と印字の違う (地区, 品目, 月) 75 か所を、市全体の休みの期間 (12/30〜1/3) で説明のつく 13 (rule_mismatches) と
      説明のつかない 62 (rule_vs_printed の裁定の待ち行列) に分ける。裁定はまだ人がしていない (decision: null)
  O1  女川を v0.2 に書き直して検査が通り、324 マス・日付 742 件が final と同じ

書き直した系列は、どれも answerable: false。金沢は読みが 1 つ (kz_calendar.py のコードだけ。single_reading) で、
カレンダーのページの時の証拠 (Last-Modified・更新日) が残っていない (version_unproven)。女川の燃やせるごみ・資源ごみは
Opus のエージェント 2 人の一致 (同じモデルなので agreed_weak) で版の証拠もそろうが、但し書きの洗い出し (§3.8) を
まだ通していない (型 F の読み取りは M2。reader_not_built)。どれも関門は閉じている (published: false)。
"""
import argparse, collections, csv, datetime as dt, hashlib, json, os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
READERS = HERE.parent
ROOT = READERS.parent.parent
sys.path.insert(0, str(READERS))
sys.path.insert(0, str(READERS.parent / "discover"))
sys.path.insert(0, str(ROOT))                                # resolve.py (金沢の窓口・出す時刻などの定め)
import schema, content, decode, version                     # noqa: E402

LG_KZ, LG_ONA = "172014", "045811"
CONVERTER = "eval/regress.py@1"
FIXTURE_KZ = HERE / "fixtures" / "kanazawa_rule_vs_printed.json"
WD = {"月": "MO", "火": "TU", "水": "WE", "木": "TH", "金": "FR", "土": "SA", "日": "SU"}
Z2H = str.maketrans("０１２３４５６７８９", "0123456789")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def rel(p):
    """手元の場所を書かないため、リポジトリからの道すじにする"""
    try:
        return pathlib.Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return pathlib.Path(p).name


def cache_meta(url, cache_dir=ROOT / "data" / "discover" / "cache"):
    import fetcher, fetch_docs
    c = fetcher.Cache(cache_dir)
    u = fetcher.norm_url(url)
    body, meta = fetch_docs.body_of(c, u)
    return body, meta


# ---- v0.1 の日付 (resolve.Kanazawa._occurrences と同じ) ----
def legacy_occurrences(s):
    """v0.1 の schedule → 日付の集まり (dates があればそれ、無ければ RRULE・segments を広げる)"""
    from dateutil.rrule import rrulestr
    if s.get("dates"):
        return {dt.date.fromisoformat(x) for x in s["dates"]}
    lo, hi = dt.date.fromisoformat(s["valid_from"]), dt.date.fromisoformat(s["valid_to"])
    if s.get("rule_kind") == "rrule":
        st = lo + dt.timedelta(days=7 if "PHASE" in s["rrule"] else 0)
        got = {x.date() for x in rrulestr(s["rrule"].replace(";PHASE=+7d", ""), dtstart=dt.datetime.combine(st, dt.time()))
               .between(dt.datetime.combine(lo, dt.time()), dt.datetime.combine(hi, dt.time()), inc=True)}
        return (got - set(map(dt.date.fromisoformat, s.get("exdates", [])))) | set(map(dt.date.fromisoformat, s.get("rdates", [])))
    got = set()
    for sg in s.get("segments", []):
        got |= {x.date() for x in rrulestr(sg["rrule"] + ";UNTIL=" + sg["until"].replace("-", "") + "T235959",
                                           dtstart=dt.datetime.fromisoformat(sg["dtstart"]))}
        got -= set(map(dt.date.fromisoformat, sg["exdates"]))
    return got


def compressed_dates(rule, valid_from, valid_to):
    """v0.2 の rule の圧縮の形 → 日付の集まり (rule_kind が無ければ None)"""
    if not rule or rule.get("rule_kind") not in ("rrule", "segments"):
        return None
    s = {"valid_from": valid_from, "valid_to": valid_to, **{k: rule[k] for k in ("rule_kind", "rrule", "exdates", "rdates", "segments")
                                                             if k in rule}}
    return legacy_occurrences(s)


# ---- 金沢 ----
def parse_nth(rule_text):
    """「第2・4木曜日」→ IR (monthly_nth)。読めなければ None"""
    m = re.fullmatch(r"第([\d・]+)(.)曜日", re.sub(r"\s+", "", rule_text.translate(Z2H)))
    if not m or m.group(2) not in WD:
        return None
    return {"kind": "monthly_nth", "weekday": WD[m.group(2)], "nth": [int(x) for x in m.group(1).split("・")]}


def parse_burnable(rule_text, fy):
    """「火曜日・ 金曜日 年末は 12月29日 （火曜日） まで 年始は 1月5日 （火曜日） から」→ (IR, 年末の最後の日, 年始の最初の日)"""
    t = re.sub(r"\s+", "", rule_text)
    wds = [WD[w] for w in re.findall(r"(.)曜日", t.split("年末")[0])]
    e, s = re.search(r"年末は(\d+)月(\d+)日", t), re.search(r"年始は(\d+)月(\d+)日", t)
    last = dt.date(fy, int(e[1]), int(e[2])) if e else None
    first = dt.date(fy + 1, int(s[1]), int(s[2])) if s else None
    return {"kind": "weekly", "weekdays": wds}, last, first


def kanazawa_members(rows):
    """区域表の行 (kanazawa_rules.csv) → v0.2 の members"""
    out = []
    for i, r in rows:
        kind, town = r["kind"], r["town"]
        conds = []
        if r["note"]:
            conds.append({"kind": "partial", "split_known": False, "text": f"(注意){r['note']}"})
        if kind == "town":
            m = {"text": town, "unit": "machiaza", "geometry": "polygon"}
        elif kind in ("block", "banchi"):
            if r["blk_from"]:
                conds.append({"kind": "range", "unit": "gaiku", "from": r["blk_from"],
                              "to": None if r["blk_to"] == "9999" else r["blk_to"], "text": r["raw_blk"]})
            if r["ban_from"]:
                conds.append({"kind": "range", "unit": "go" if r["blk_from"] else "banchi", "from": r["ban_from"],
                              "to": None if r["ban_to"] == "9999" else r["ban_to"], "text": r["raw_ban"]})
            m = {"text": " ".join(x for x in (town, r["raw_blk"], r["raw_ban"]) if x), "unit": "block_range", "geometry": "points"}
        elif kind == "default":
            conds.append({"kind": "remainder", "text": r["raw_blk"] or r["raw_ban"]})
            m = {"text": f"{town} {r['raw_blk'] or r['raw_ban']}", "unit": "remainder", "geometry": "points"}
        elif kind == "aza":
            conds.append({"kind": "note", "text": r["raw_ban"] or r["raw_blk"]})
            m = {"text": f"{town} {r['raw_ban'] or r['raw_blk']}", "unit": "machiaza", "geometry": "points"}
        else:
            m = {"text": " ".join(x for x in (town, r["raw_blk"], r["raw_ban"]) if x), "unit": "unparsed", "geometry": "unresolved"}
        if any(c["kind"] == "partial" for c in conds):
            m["geometry"] = "unresolved"
        out.append({**m, "machiaza_id": None, "match": "unchecked", "conditions": conds,
                    "ref": {"source_id": "s_rules", "row": i, "note": "data/kanazawa_rules.csv の行 (見出しの行を 1 とする)"}})
    return out


def html_source(source_id, url, body, title=None, fetched_at=None, last_modified=None, note=None, sha_override=None):
    sha = sha256_bytes(body) if body is not None else sha_override
    src = {"source_id": source_id, "url": url, "source_key": url, "found_from": None, "anchor_text": None,
           "anchor_context": None, "title": title, "publisher": "金沢市 ごみ減量推進課", "format": "html_table", "pages": None,
           "sha256": sha, "content_sha256": None, "last_modified": last_modified, "fetched_at": fetched_at}
    if body is not None:
        html = decode.decode(body, "text/html").text
        src["content_sha256"] = content.content_sha256(html)
    if note:
        src["note"] = note
    return src


def _find_sentence(html, pred):
    reg = content.content_region(html)
    for s in reg.sentences():
        if pred(s):
            return s
    return None


def kanazawa_v02(v01_path=ROOT / "out" / "kanazawa_schedules.json", rules_csv=ROOT / "data" / "kanazawa_rules.csv",
                 rules_html=ROOT / "data" / "kanazawa_rules.html", cal_dir=ROOT / "data" / "kanazawa_cal"):
    """金沢の v0.1 と区域表 → (v0.2 の dict, 裁定の待ち行列のリスト)"""
    import resolve                                        # 窓口・出す時刻・対象外の定め (mcp_server と同じ値)
    raw = pathlib.Path(v01_path).read_bytes()
    v01 = json.loads(raw)
    fy = v01["fiscal_year"]
    vf, vt = f"{fy}-04-01", f"{fy + 1}-03-31"
    # 出典
    rules_body = pathlib.Path(rules_html).read_bytes()
    _, rmeta = cache_meta(resolve.RULES_SOURCE["url"])
    same = rmeta and rmeta.get("sha256") == sha256_bytes(rules_body)
    rules_hdr = (rmeta or {}).get("headers") or {}
    sources = [html_source("s_rules", resolve.RULES_SOURCE["url"], rules_body, resolve.RULES_SOURCE["title"],
                           rmeta.get("fetched_at") if same else None, rules_hdr.get("last-modified") if same else None,
                           None if same else "取得の記録 (Last-Modified) の無い写し")]
    pbody, pmeta = cache_meta(resolve.PUT_OUT_RULES["url"])
    p_same = pbody is not None and sha256_bytes(pbody) == resolve.PUT_OUT_RULES["page_sha256"]
    sources.append(html_source("s_putout", resolve.PUT_OUT_RULES["url"], pbody if p_same else None, resolve.PUT_OUT_RULES["title"],
                               resolve.PUT_OUT_RULES["retrieved_at"],
                               ((pmeta or {}).get("headers") or {}).get("last-modified") if p_same else None,
                               None if p_same else "キャッシュの中身が mcp_server の記録と違う", resolve.PUT_OUT_RULES["page_sha256"]))
    cal_src, cal_html = {}, {}
    for s in v01["schedules"]:
        src = s["source"]
        url = src["url"].split("#")[0]
        page = url.rsplit("/", 1)[-1].split(".")[0]
        if page in cal_src:
            continue
        p = pathlib.Path(cal_dir) / f"{page}.html"
        body = p.read_bytes() if p.exists() else None
        ok = body is not None and sha256_bytes(body) == src["page_sha256"]
        sid = f"s_cal_{page}"
        cal_src[page] = html_source(sid, url, body if ok else None, src["page_title"], src["retrieved_at"], None,
                                    "取得したときの Last-Modified は記録していない (kz_calendar.py)" if ok else
                                    "手元の写しの sha256 が v0.1 の記録と違う", src["page_sha256"])
        if ok:
            cal_html[page] = decode.decode(body, "text/html").text
    sources += [cal_src[k] for k in sorted(cal_src)]
    # 期間と版の証拠 (カレンダーのページの見出し「2026年度版」。時の証拠はカレンダーのページ自身のものだけ)
    title_toks, body_toks, times = [], [], []
    for page, html in sorted(cal_html.items()):
        t, b = version.html_tokens(html)
        title_toks += t
        body_toks += b
        times += [version._time("page_updated", d, f"s_cal_{page}") for d in version.page_updated(html)]
    first_page = sorted(cal_src)[0]
    ye = version.year_evidence(vf, vt, title_toks, body_toks, [], [], times, token_ref={"source_id": f"s_cal_{first_page}"})
    periods = [{"period_id": f"fy{fy}", "valid_from": vf, "valid_to": vt,
                "basis": {"text": f"{fy}年度版", "ref": {"source_id": f"s_cal_{first_page}"}, "kind": "stated"},
                "year_evidence": ye}]
    # 区域表 → 地区
    with open(rules_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by_d = collections.defaultdict(list)
    for i, r in enumerate(rows, start=2):
        by_d[r["district"]].append((i, r))
    districts_v01 = sorted({s["district"] for s in v01["schedules"]})
    missing = sorted(set(districts_v01) - set(by_d))
    if missing:
        raise ValueError(f"区域表に無い地区: {missing}")
    did = lambda d: f"{LG_KZ}-{d}"
    districts = []
    for d in districts_v01:
        mem = kanazawa_members(by_d[d])
        s0 = next(s for s in v01["schedules"] if s["district"] == d)
        districts.append({"district_id": did(d), "partition_id": "all", "label": d, "label_kind": "official", "members": mem,
                          "place_resolution": schema.weakest([m["geometry"] for m in mem]),
                          "ref": {"source_id": "s_rules", "note": "区域表の「校下・地区」の列"},
                          "extra": {"legacy_zone_id": s0["zone_id"], "management_center": s0.get("management_center"),
                                    "calendar_url": s0["source"]["url"]}})
    # 品目 (§2.4 の金沢の読み替え)
    coarse = {"burnable": ["burnable"], "nonburnable": ["non_burnable"], "recyclable": ["beverage_can", "pet_bottle", "plastic"],
              "plastic": ["plastic"], "bottle": ["glass_bottle"]}
    labels = {}
    for s in v01["schedules"]:
        labels.setdefault(s["category"], s["official_label"])
    categories = [{"category_id": c, "official_label": labels[c], "coarse": coarse[c], "mode": "curbside",
                   "ref": {"source_id": f"s_cal_{first_page}", "note": "カレンダーの表の見出し"}} for c in coarse]
    partitions = [{"partition_id": "all", "label": "地区別収集カレンダー (全品目)", "category_ids": list(coarse), "basis": "residence",
                   "basis_span": None}]
    rules_html_text = decode.decode(rules_body, "text/html").text
    sb = _find_sentence(rules_html_text, lambda s: "町にお住まいの方" in s.text)
    if sb:
        partitions[0]["basis_span"] = sb.span("s_rules")
    # 例外と方針
    exceptions = []
    hol_text = "収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。"
    hol = {"exception_id": "e_holiday", "kind": "holiday_policy", "scope": {"category_ids": "*"}, "value": "collected",
           "covers": ["all_weekdays"], "text": hol_text}
    if p_same:
        s = _find_sentence(decode.decode(pbody, "text/html").text, lambda s: s.text == hol_text)
        if s:
            hol["span"] = s.span("s_putout")
    exceptions.append(hol)
    # 市全体の年末年始 (「2026年度年末年始のごみ収集について」の下の行。かっこ書きの小さな文は行の一部なので重ねない)
    ye_lines = [ln for ln in content.content_region(rules_html_text).lines()
                if not ln.is_heading and ln.heading_path and "年末年始のごみ収集" in ln.heading_path[-1]]
    city_text = "\n".join(ln.text for ln in ye_lines)
    m1 = re.search(r"年末\s*：\s*(\d+)月(\d+)日", content.nfkc(city_text).replace(":", "："))
    m2 = re.search(r"年始\s*：\s*(\d+)月(\d+)日", content.nfkc(city_text).replace(":", "："))
    city_last = dt.date(fy, int(m1[1]), int(m1[2]))
    city_first = dt.date(fy + 1, int(m2[1]), int(m2[2]))
    city = {"exception_id": "e_city_year_end", "kind": "year_end", "scope": {"category_ids": "*"}, "treatment": "closure",
            "last": str(city_last), "first": str(city_first), "others": None, "text": city_text}
    if ye_lines:
        # span は、行 (li) をすべて含むいちばん小さな要素 (ul) を指し、text はその要素の行を改行でつないだもの (原文どおり)
        common = ye_lines[0].element
        while common is not None and not all(content._inside(ln.element, common) for ln in ye_lines):
            common = common.parent
        city["span"] = {"source_id": "s_rules", "selector": content.css_path(common), "text": city_text,
                        "heading_path": list(ye_lines[0].heading_path)}
    exceptions.append(city)
    closure = {city_last + dt.timedelta(days=i) for i in range(1, (city_first - city_last).days)}
    # 系列
    schedules, queue = [], []
    period_ok = ye["ok"]
    note_done = set()
    for s in v01["schedules"]:
        d, cat = s["district"], s["category"]
        page = s["source"]["url"].split("#")[0].rsplit("/", 1)[-1].split(".")[0]
        anchor = s["source"]["url"].split("#")[1] if "#" in s["source"]["url"] else ""
        sid = f"s_cal_{page}"
        sel = f"#{anchor} table (caption {d})"
        occ = sorted(legacy_occurrences(s))
        rule = {"text": s["rule_text"], "span": {"source_id": sid, "selector": sel, "text": s["rule_text"]},
                **{k: s[k] for k in ("rule_kind", "rrule", "exdates", "rdates", "segments") if k in s}}
        blocked = ["single_reading"] + ([] if period_ok else ["version_unproven"])
        expanded_with, mism = [], []
        if s.get("year_end_note") and d not in note_done:
            note_done.add(d)
            exceptions.append({"exception_id": f"e_note_{d}", "kind": "year_end", "treatment": "dates",
                               "scope": {"district_ids": [did(d)], "category_ids": [c for c in coarse if c != "burnable"]},
                               "others": None, "text": "（※）年末年始の影響で、収集日を変更しています。ご注意ください。",
                               "span": {"source_id": sid, "selector": f"#{anchor} の表のあと",
                                        "text": "（※）年末年始の影響で、収集日を変更しています。ご注意ください。"}})
        if cat == "burnable":
            ir, last, first = parse_burnable(s["rule_text"], fy)
            rule["ir"] = ir
            eid = f"e_ye_{d}"
            exceptions.append({"exception_id": eid, "kind": "year_end", "scope": {"district_ids": [did(d)], "category_ids": ["burnable"]},
                               "treatment": "closure", "last": str(last), "first": str(first), "others": None,
                               "text": re.sub(r"\s+", " ", s["rule_text"]).strip(),
                               "span": {"source_id": sid, "selector": sel, "text": s["rule_text"]}})
            expanded_with = ["e_holiday", eid]
            origin, gate = "expanded", "expanded"
        else:
            rule["ir"] = parse_nth(s["rule_text"])
            origin, gate = "printed", "B"
            for mm in s.get("rule_mismatches", []):
                y, mo = map(int, mm["month"].split("-"))
                rd = {dt.date(y, mo, x) for x in mm["rule"]}
                td = {dt.date(y, mo, x) for x in mm["table"]}
                only_rule, only_printed = rd - td, td - rd
                if not only_printed and only_rule and only_rule <= closure:
                    mism.append({"month": mm["month"], "rule_dates": sorted(map(str, rd)), "printed_dates": sorted(map(str, td)),
                                 "explained_by": ["e_city_year_end"],
                                 "note": "規則の日が市全体の休みの期間 (年末の最後の日の次の日〜年始の最初の日の前の日) に入る"})
                else:
                    item = f"{LG_KZ}:fy{fy}:rule_vs_printed:{did(d)}:{cat}:{mm['month']}"
                    blocked.append(f"disputed:{item}")
                    queue.append({"item_id": item, "kind": "rule_vs_printed", "status": "open", "decision": None,
                                  "proposed": "printed",
                                  "readings": {"rule": {"text": s["rule_text"], "dates": sorted(map(str, rd))},
                                               "printed": {"source_id": sid, "dates": sorted(map(str, td))}},
                                  "unexplained": {"rule_only": sorted(map(str, only_rule)),
                                                  "printed_only": sorted(map(str, only_printed))},
                                  "evidence": {"source_url": s["source"]["url"], "table_sha256": s["source"]["table_sha256"]},
                                  "note": "規則と印字の違いが、例外の原文 (市全体の年末年始の休みの期間) で説明できない。人が資料で確かめて決める"})
        schedules.append({
            "district_id": did(d), "category_id": cat, "period_id": f"fy{fy}", "dates_origin": origin, "rule": rule,
            "dates": [str(x) for x in occ], "unknown_ranges": [], "answerable": False, "blocked_by": blocked,
            "gate": {"unit": gate, "open": False}, "published": False, "caveats": [], "expanded_with": expanded_with,
            "rule_mismatches": mism,
            "provenance": {"readers": [{"reader": "legacy.kz_calendar@v0.1", "kind": "code",
                                        "inputs": {"source_sha256": s["source"]["page_sha256"],
                                                   "table_sha256": s["source"]["table_sha256"]}}],
                           "independence": None, "status": "single", "spans": [rule["span"]], "adjudication": None,
                           "note": "スキーマ v0.1 からの書き直し。読みは kz_calendar.py のコード 1 つだけ"},
            "ref": {"source_id": sid, "selector": sel},
            "extra": {"legacy_zone_id": s["zone_id"], "year_end_note": s.get("year_end_note")}})
    blocked_count = collections.Counter(b.split(":")[0] for s in schedules for b in s["blocked_by"])
    doc = {
        "schema": schema.SCHEMA, "lg_code": LG_KZ, "name": v01["municipality"]["name"], "periods": periods,
        "partitions": partitions, "districts": districts, "categories": categories, "schedules": schedules,
        "exceptions": exceptions, "sources": sources,
        "watch": {"sources": [{"source_id": x["source_id"], "content_sha256": x["content_sha256"], "sha256": x["sha256"]}
                              for x in sources]},
        "contact": resolve.CONTACT, "put_out_by": resolve.PUT_OUT_BY, "not_covered": resolve.NOT_COVERED,
        "coverage": {"answer_type": "rule_contact", "by_district": {x["district_id"]: "rule_contact" for x in districts},
                     "population_share": None, "population_basis": None, "blocked_series": dict(blocked_count),
                     "gate_pending": {"series": 0}, "quality": None},
        "build": {"converted_from": {"file": rel(v01_path), "schema": "v0.1", "sha256": sha256_bytes(raw)},
                  "converter": CONVERTER, "series": len(schedules), "districts": len(districts),
                  "rule_mismatches_explained": sum(len(s["rule_mismatches"]) for s in schedules),
                  "rule_vs_printed_queued": len(queue), "blocked_by": dict(blocked_count),
                  "year_evidence": {"ok": ye["ok"], "reasons": ye.get("reasons", [])}},
    }
    return doc, queue


# ---- 女川 ----
PDF_TEXT_MIN = 200                  # 文字層の字がこれ以上ある PDF は文字の PDF (pdf_text)


def pdf_format(body):
    """PDF の bytes → "pdf_text" (文字層がある) / "pdf_image" (文字層が無い: 画像か輪郭線)。読めなければ None"""
    import fitz
    try:
        with fitz.open(stream=body, filetype="pdf") as doc:
            n = sum(len(pg.get_text("text").strip()) for pg in doc)
    except Exception:
        return None
    return "pdf_text" if n >= PDF_TEXT_MIN else "pdf_image"


def onagawa_v02(data_path=ROOT / "out" / "onagawa_data.json"):
    """女川の今のデータ → v0.2 の dict。期間は 2 つ (分け方が 2026-10-01 に変わった。§2.2・§5.7)"""
    raw = pathlib.Path(data_path).read_bytes()
    d = json.loads(raw)
    cal_url = "https://www.town.onagawa.miyagi.jp/pdf/calendar/r08_chomin_calendar.pdf"
    body, meta = cache_meta(cal_url)
    hdr = (meta or {}).get("headers") or {}
    src_cal = {"source_id": "s_cal", "url": cal_url, "source_key": cal_url,
               "found_from": "https://www.town.onagawa.miyagi.jp/chomin_calendar.html",
               "anchor_text": "令和8年度 おながわ町民カレンダー（PDF形式：8.43MB）", "anchor_context": None,
               "title": d["calendar_title"], "publisher": "女川町",
               "format": (pdf_format(body) if body else None) or "pdf_text", "pages": None,     # 型 F (文字の PDF の月表。§3.5)
               "sha256": (meta or {}).get("sha256"), "content_sha256": None, "last_modified": hdr.get("last-modified"),
               "fetched_at": (meta or {}).get("fetched_at"),
               "note": "読み取り (Opus のエージェント 2 人) は資料の sha256 を残していない。キャッシュの写しを同じ資料とみなしている"}
    times, title_t, body_t, hidden = [], [], [], []
    if body is not None:
        import fitz
        with fitz.open(stream=body, filetype="pdf") as doc_:
            src_cal["pages"] = len(doc_)
        title_t, body_t, hidden = version.pdf_tokens(body)
        times = version.http_times(hdr.get("last-modified"), meta.get("fetched_at"), "s_cal") + version.pdf_times(body, "s_cal")
    ye = version.year_evidence("2026-04-01", "2027-03-31", title_t, body_t, version.filename_tokens(cal_url),
                               version.year_tokens(src_cal["anchor_text"]), times, hidden,
                               token_ref={"source_id": "s_cal", "page": 1})
    other_src = []
    for i, s in enumerate(d["sources"]):
        if s["url"] == cal_url:
            continue
        fmt, note = "html_text", s["kind"]
        if s["url"].endswith(".pdf"):
            b, _ = cache_meta(s["url"])
            fmt = (pdf_format(b) if b is not None else None) or "pdf_image"
            if b is None:
                note += " (キャッシュに無く、文字層があるかを確かめていない)"
        other_src.append({"source_id": f"s{i}", "url": s["url"], "source_key": s["url"], "found_from": None, "anchor_text": None,
                          "anchor_context": None, "title": s["title"], "publisher": "女川町", "format": fmt, "pages": None,
                          "sha256": None, "content_sha256": None, "last_modified": None, "fetched_at": None, "note": note})
    periods = [{"period_id": pid, "valid_from": a, "valid_to": b,
                "basis": {"text": d["calendar_title"], "ref": {"source_id": "s_cal", "page": 1}, "kind": "stated"},
                "year_evidence": ye,
                "note": note}
               for pid, a, b, note in (("fy2026a", "2026-04-01", "2026-09-30", "分け方が変わる前 (カレンダーは同じ)"),
                                       ("fy2026b", "2026-10-01", "2027-03-31",
                                        "2026-10-01 から分け方が変わった (プラスチックの分別収集)。カレンダーの見出しは 4 月の印刷のまま"))]
    cols = d["resource"]["columns"]
    cat_ids = {cols[0]["label"]: "c_can_bottle_pet", cols[1]["label"]: "c_other_resource", cols[2]["label"]: "c_nonburnable",
               cols[3]["label"]: "c_oversized"}
    coarse = {"c_can_bottle_pet": ["glass_bottle", "beverage_can", "metal", "pet_bottle"],
              "c_other_resource": ["plastic", "paper", "cloth", "other"], "c_nonburnable": ["non_burnable"],
              "c_oversized": ["oversized"], "c_burnable": ["burnable"]}
    categories = [{"category_id": "c_burnable", "official_label": d["burnable"]["label"], "coarse": coarse["c_burnable"],
                   "mode": "curbside", "ref": {"source_id": "s_cal", "note": "各月の表のページの見出し"}}]
    for c in cols:
        cid = cat_ids[c["label"]]
        categories.append({"category_id": cid, "official_label": c["label"], "coarse": coarse[cid], "mode": "curbside",
                           "ref": {"source_id": "s_cal", "note": "資源ごみと燃やせないごみ、粗大ごみの収集日の表の見出し"},
                           "extra": {"contents": c["contents"]}})
    for c in categories:
        if c["category_id"] == "c_other_resource":
            c["notes_by_period"] = [{"period_id": "fy2026b", "text": "2026-10-01 から、赤色のかごはペットボトル以外のプラスチック容器・製品 (プラスチック 100% のもの全て)"}]
    partitions = [{"partition_id": "burnable", "label": "燃やせるごみ (カレンダーの色分け)", "category_ids": ["c_burnable"],
                   "basis": "unknown", "basis_span": None},
                  {"partition_id": "resource", "label": "資源ごみと燃やせないごみ、粗大ごみの収集日 (表の行)",
                   "category_ids": [cat_ids[c["label"]] for c in cols], "basis": "unknown", "basis_span": None}]

    def community(names):
        return [{"text": n, "unit": "island" if n == "江島" else "community", "machiaza_id": None, "match": "unchecked",
                 "conditions": [], "geometry": "unresolved"} for n in names]

    districts, keymap = [], {}
    slug = {"黄色の日": "yellow", "水色の日": "lightblue"}
    for g in d["burnable"]["groups"]:
        did = f"{LG_ONA}-burnable-{slug.get(g['color'], 'enoshima')}"
        districts.append({"district_id": did, "partition_id": "burnable", "label": g["color"], "label_kind": "schedule",
                          "members": community(g["gyoseiku"]), "place_resolution": "unresolved",
                          "ref": {"source_id": "s_cal", "note": "燃やせるごみの凡例 (色)"},
                          "extra": {"weekdays_text": g["weekdays_text"]}})
        keymap[f"burnable|{g['color']}"] = (did, "c_burnable", g)
    for i, r in enumerate(d["resource"]["rows"], start=1):
        did = f"{LG_ONA}-resource-{i}"
        districts.append({"district_id": did, "partition_id": "resource", "label": r["label"], "label_kind": "official",
                          "members": community(r["gyoseiku"]), "place_resolution": "unresolved",
                          "ref": {"source_id": "s_cal", "row": i, "note": "資源ごみの表の行"}})
        for c in cols:
            keymap[f"resource|{r['label']}|{c['label']}"] = (did, cat_ids[c["label"]], r["by_column"][c["label"]])
    prov = {"readers": [{"reader": "legacy.onagawa_workflow.A", "kind": "llm", "model": "claude-opus", "agree": True,
                         "note": "Opus のエージェント (読み手 1)"},
                        {"reader": "legacy.onagawa_workflow.B", "kind": "llm", "model": "claude-opus", "agree": True,
                         "note": "Opus のエージェント (読み手 2)。食い違いは 0 マス"}],
            "independence": "llm_same_model", "status": "agreed_weak", "spans": [], "adjudication": None,
            "note": "out/onagawa_reading.json の 2 人の読み (date_cells_disagreed がすべて 0)"}
    schedules = []
    for key, (did, cid, v) in keymap.items():
        dates = v["dates"] if isinstance(v, dict) else v
        for p in periods:
            ds = [x for x in dates if p["valid_from"] <= x <= p["valid_to"]]
            # 印字の日付にも掛ける条件 (§2.5 の 6): 但し書きの洗い出しとしるしの棚卸しを、この資料 (型 F の町民カレンダー) では
            # まだ通していない。型 F の読み取りは M2 なので reader_not_built で止める (迷ったら答えない)
            blocked = ["reader_not_built"] + ([] if ye["ok"] else ["version_unproven"])
            s = {"district_id": did, "category_id": cid, "period_id": p["period_id"], "dates_origin": "printed", "rule": None,
                 "dates": ds, "unknown_ranges": [], "answerable": False, "blocked_by": blocked, "gate": {"unit": "F", "open": False},
                 "published": False, "caveats": [], "expanded_with": [], "rule_mismatches": [], "provenance": prov,
                 "ref": {"source_id": "s_cal", "note": "月ごとの表 (偶数ページ) と色の日付 (奇数ページ)"}}
            if did.endswith("enoshima"):
                # 江島は日付が印字されていない (色なし)。規則の文だけで、祝日・年末年始の扱いが無い → 規則から作らない (種類 3)
                s.update(dates_origin=None, dates=[], blocked_by=["holiday_policy_missing", "year_end_missing"] + blocked,
                         rule={"text": v["weekdays_text"], "ir": {"kind": "weekly", "weekdays": ["MO", "FR"]}},
                         provenance={**prov, "status": "single", "independence": None,
                                     "note": "規則の文は読み手の記録 (weekdays_text) の写し"})
            schedules.append(s)
    blocked_count = collections.Counter(b.split(":")[0] for s in schedules for b in s["blocked_by"])
    reading = ROOT / "out" / "onagawa_reading.json"
    doc = {
        "schema": schema.SCHEMA, "lg_code": LG_ONA, "name": d["municipality"], "periods": periods, "partitions": partitions,
        "districts": districts, "categories": categories, "schedules": schedules, "exceptions": [],
        "sources": [src_cal] + other_src,
        "watch": {"sources": [{"source_id": "s_cal", "content_sha256": None, "sha256": src_cal["sha256"]}]},
        "contact": d["contact"], "put_out_by": d["put_out_by"], "not_covered": d["not_covered"],
        "coverage": {"answer_type": "rule_contact", "by_district": {x["district_id"]: "rule_contact" for x in districts},
                     "population_share": None, "population_basis": None, "blocked_series": dict(blocked_count),
                     "gate_pending": {"series": sum(1 for s in schedules if s["answerable"] and not s["published"])},
                     "quality": None},
        "build": {"converted_from": {"file": rel(data_path), "sha256": sha256_bytes(raw),
                                     "reading": rel(reading) if reading.exists() else None},
                  "converter": CONVERTER, "legacy_build": d.get("build"), "series": len(schedules),
                  "year_evidence": {"ok": ye["ok"], "reasons": ye.get("reasons", [])},
                  "year_end_note": d.get("year_end_note"), "holiday_policy_note": d.get("holiday_policy")},
        "aux": {"place_to_gyoseiku": d["place_to_gyoseiku"], "sorting_rules": d["sorting_rules"],
                "sorting_source_url": d.get("sorting_source_url")},
    }
    return doc


# ---- 確かめ ----
def check_k1(v01, doc):
    """K1: 355 系列の日付・圧縮の形の戻り・71 地区 × 371 日の答え"""
    by = {(s["district_id"], s["category_id"]): s for s in doc["schedules"]}
    per = {p["period_id"]: p for p in doc["periods"]}
    n = eq = rt_n = rt_ok = 0
    diffs = []
    for s in v01["schedules"]:
        key = (f"{LG_KZ}-{s['district']}", s["category"])
        x = by.get(key)
        n += 1
        a = legacy_occurrences(s)
        b = {dt.date.fromisoformat(v) for v in (x or {}).get("dates", [])}
        if a == b:
            eq += 1
        else:
            diffs.append({"series": key, "only_v01": sorted(map(str, a - b))[:5], "only_v02": sorted(map(str, b - a))[:5]})
        if x:
            p = per[x["period_id"]]
            cd = compressed_dates(x.get("rule"), p["valid_from"], p["valid_to"])
            if cd is not None:
                rt_n += 1
                rt_ok += cd == b
    # 日ごとの答え (resolve.Kanazawa.days と同じ範囲: 期間の 3 日前から 3 日後まで)
    days = days_eq = 0
    legacy_by = collections.defaultdict(list)
    for s in v01["schedules"]:
        legacy_by[s["district"]].append((s, legacy_occurrences(s)))
    for d, ss in legacy_by.items():
        lo, hi = dt.date.fromisoformat(ss[0][0]["valid_from"]), dt.date.fromisoformat(ss[0][0]["valid_to"])
        start = lo - dt.timedelta(days=3)
        for i in range((hi - lo).days + 7):
            day = start + dt.timedelta(days=i)
            want = None if not lo <= day <= hi else sorted(s["category"] for s, occ in ss if day in occ)
            got = schema.day_answer(doc, f"{LG_KZ}-{d}", day)
            g = None if got["status"] == "unknown" else sorted(got["categories"])
            if got["status"] == "partial":
                g = ("partial", g)                              # v0.1 に分からない品目は無いので、partial は違いに数える
            days += 1
            days_eq += want == g
            if want != g and len(diffs) < 20:
                diffs.append({"district": d, "date": str(day), "v01": want, "v02": g})
    return {"series": n, "series_equal": eq, "roundtrip_checked": rt_n, "roundtrip_ok": rt_ok,
            "district_days": days, "district_days_equal": days_eq, "diffs": diffs,
            "ok": n == eq == 355 and rt_n == rt_ok and days == days_eq == 71 * 371}


def check_k3(doc, queue):
    """K3 (分けるところまで): 説明のつく違い 13 と、待ち行列 62 の内わけ"""
    expl = collections.Counter((s["category_id"], m["month"]) for s in doc["schedules"] for m in s["rule_mismatches"])
    q = collections.Counter((x["item_id"].split(":")[-2], x["item_id"].split(":")[-1]) for x in queue)
    return {"explained": sum(expl.values()), "explained_by_cat_month": {f"{c}:{m}": v for (c, m), v in sorted(expl.items())},
            "queued": len(queue), "queued_by_cat_month": {f"{c}:{m}": v for (c, m), v in sorted(q.items())},
            "ok": sum(expl.values()) == 13 and len(queue) == 62}


def check_o1(final, doc):
    """O1: 324 マス・日付 742 件が final と同じ (マス = 月 × (燃やせるごみの色 3 + 表の行 6 × 列 4))"""
    data = json.loads((ROOT / "out" / "onagawa_data.json").read_text("utf-8"))
    names = {f"burnable|{g['color']}": (f"{LG_ONA}-burnable-{ {'黄色の日': 'yellow', '水色の日': 'lightblue'}.get(g['color'], 'enoshima')}", "c_burnable")
             for g in data["burnable"]["groups"]}
    cols = data["resource"]["columns"]
    cid = dict(zip([c["label"] for c in cols], ["c_can_bottle_pet", "c_other_resource", "c_nonburnable", "c_oversized"]))
    for i, r in enumerate(data["resource"]["rows"], start=1):
        for c in cols:
            names[f"resource|{r['label']}|{c['label']}"] = (f"{LG_ONA}-resource-{i}", cid[c["label"]])
    dates_of = collections.defaultdict(set)
    for s in doc["schedules"]:
        dates_of[(s["district_id"], s["category_id"])] |= set(s["dates"])
    cells = cells_eq = n_dates = 0
    diffs = []
    for month, v in final.items():
        for key, days in v["cells"].items():
            cells += 1
            did, c = names[key]
            got = sorted(int(x[8:]) for x in dates_of[(did, c)] if x[:7] == month)
            want = sorted(int(x) for x in days)
            n_dates += len(want)
            if got == want:
                cells_eq += 1
            elif len(diffs) < 10:
                diffs.append({"month": month, "cell": key, "final": want, "v02": got})
    total_v02 = sum(len(s["dates"]) for s in doc["schedules"])
    return {"cells": cells, "cells_equal": cells_eq, "final_dates": n_dates, "v02_dates": total_v02, "diffs": diffs,
            "ok": cells == cells_eq == 324 and n_dates == total_v02 == 742}


K4_XLS = "https://www4.city.kanazawa.lg.jp/material/files/group/68/2026tikubetsu.xls"
DATE_CELL = re.compile(r"^\s*(?:\d{1,2}\s*[/月]\s*\d{1,2}|\d{4}-\d{2}-\d{2}|\d{1,2}(?:\.0)?)\s*日?\s*$")


def check_k4(rules_csv=ROOT / "data" / "kanazawa_rules.csv"):
    """K4: 金沢の 2 つの資料 (HTML の区域表・カレンダーと、Excel 2026tikubetsu.xls)。
    (1) 日付: Excel の地区ごとのシートに日付のマス (セルの値) があるか。無ければ 355 系列の日付は比べられない (測れない)。
    (2) 地区: Excel の町名の索引のシート (ア行〜ヤ・ラ・ワ行) の、町を丸ごと 1 つの地区に当てる行 (街区・番号の無い行) を、
        区域表 (data/kanazawa_rules.csv の kind=town の行) と、町名 → 地区で比べる。違いはすべて一覧にする"""
    import fetch_docs, tabular
    body = fetch_docs.cached(K4_XLS)
    if body is None:
        return {"ok": False, "why": "Excel がキャッシュに無い", "measurable": False}
    sheets, _ = tabular.read_excel(body)
    index_names = ("ア行", "カ行", "サ行", "タ行", "ナ行", "ハ行", "マ行", "ヤ・ラ・ワ行")
    district_sheets = [s for s in sheets if s["sheet"] not in index_names + ("表紙",)]
    with_dates = [s["sheet"] for s in district_sheets
                  if any(DATE_CELL.match(str(v)) for r in s["rows"] for v in r if str(v).strip())]
    # 町を丸ごと当てる行: 町名・街区・番号・地区の 5 列の組 (列 1・6・11 から)
    xls = {}
    for s in sheets:
        if s["sheet"] not in index_names:
            continue
        for start in range(1, 16, 5):
            town = None
            for r in s["rows"][2:]:
                if len(r) < start + 4:
                    continue
                t, blk, ban, dist = (str(r[start + k]).strip() for k in range(4))
                if t:
                    town = t
                if t and dist and not blk and not ban and town:
                    xls[unicodedata_nfkc(town)] = unicodedata_nfkc(dist)
    with open(rules_csv, encoding="utf-8", newline="") as f:
        html = {unicodedata_nfkc(r["town"]): unicodedata_nfkc(r["district"]) for r in csv.DictReader(f) if r["kind"] == "town"}
    both = sorted(set(xls) & set(html))
    differ = []
    for t in both:
        if xls[t] == html[t]:
            continue
        d = {"town": t, "excel": xls[t], "html": html[t]}
        if re.sub(r"\([^()]*\)$", "", html[t]) == xls[t] or re.sub(r"\([^()]*\)$", "", xls[t]) == html[t]:
            d["explained"] = "地区の名前のかっこ書き (同じ地区。HTML の区域表は「安原(緑団地含む)」と書く)"
        differ.append(d)
    only_x = sorted(set(xls) - set(html))
    only_h = sorted(set(html) - set(xls))
    return {"district_sheets": len(district_sheets), "district_sheets_with_dates": len(with_dates),
            "dates_measurable": bool(with_dates), "towns_compared": len(both), "towns_equal": len(both) - len(differ),
            "towns_differ": differ, "only_excel": only_x[:30], "only_excel_n": len(only_x), "only_html": only_h[:30],
            "only_html_n": len(only_h),
            "towns_differ_unexplained": sum(1 for d in differ if not d.get("explained")),
            "ok": False if not with_dates else not any(not d.get("explained") for d in differ),
            "why": None if with_dates else "Excel の地区ごとのシートにセルの日付が無い (カレンダーは図として描かれている)。355 系列の日付は比べられない"}


def unicodedata_nfkc(s):
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", "utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="金沢・女川の今のデータを v0.2 に書き直して確かめる")
    ap.add_argument("--write", action="store_true", help="out/schedules/ と eval/fixtures/ に書く")
    a = ap.parse_args(argv)
    v01 = json.loads((ROOT / "out" / "kanazawa_schedules.json").read_text("utf-8"))
    kz, queue = kanazawa_v02()
    err = schema.validate(kz)
    k1, k3 = check_k1(v01, kz), check_k3(kz, queue)
    print(f"金沢: 検査 {'OK' if not err else err[:5]}・系列 {len(kz['schedules'])}・地区 {len(kz['districts'])}")
    print(f"  K1: 系列 {k1['series_equal']}/{k1['series']} 同じ、圧縮の形の戻り {k1['roundtrip_ok']}/{k1['roundtrip_checked']}、"
          f"日ごとの答え {k1['district_days_equal']}/{k1['district_days']} 同じ → {'合格' if k1['ok'] else '不合格'}")
    for x in k1["diffs"][:5]:
        print("    違い:", x)
    print(f"  K3 (分けるところまで): 説明のつく {k3['explained']} {k3['explained_by_cat_month']}、待ち行列 {k3['queued']} "
          f"{k3['queued_by_cat_month']} → {'合格' if k3['ok'] else '不合格'}")
    print(f"  版の証拠: ok={kz['periods'][0]['year_evidence']['ok']} {kz['periods'][0]['year_evidence'].get('reasons')}")
    print(f"  blocked_by: {kz['build']['blocked_by']}")
    k4 = check_k4()
    print(f"  K4: Excel の地区のシート {k4.get('district_sheets')} のうち日付のマスがあるもの {k4.get('district_sheets_with_dates')}"
          f"{' → 日付は測れない (' + k4['why'] + ')' if k4.get('why') else ''}。"
          f"町を丸ごと当てる行: 比べた町 {k4.get('towns_compared')}・同じ地区 {k4.get('towns_equal')}・違う {len(k4.get('towns_differ') or [])} "
          f"(説明のつかないもの {k4.get('towns_differ_unexplained')})"
          f"・Excel だけ {k4.get('only_excel_n')}・HTML だけ {k4.get('only_html_n')}")
    for x in (k4.get("towns_differ") or [])[:10]:
        print("    違い:", x)
    reading =json.loads((ROOT / "out" / "onagawa_reading.json").read_text("utf-8"))
    ona = onagawa_v02()
    err2 = schema.validate(ona)
    o1 = check_o1(reading["final"], ona)
    print(f"女川: 検査 {'OK' if not err2 else err2[:5]}・系列 {len(ona['schedules'])}・answerable "
          f"{sum(s['answerable'] for s in ona['schedules'])}")
    print(f"  O1: マス {o1['cells_equal']}/{o1['cells']} 同じ、日付 final {o1['final_dates']}・v0.2 {o1['v02_dates']} → "
          f"{'合格' if o1['ok'] else '不合格'}")
    for x in o1["diffs"][:5]:
        print("    違い:", x)
    print(f"  版の証拠: ok={ona['periods'][0]['year_evidence']['ok']} token={ona['periods'][0]['year_evidence']['token']}")
    if a.write:
        write_json(ROOT / "out" / "schedules" / f"{LG_KZ}.json", kz)
        write_json(ROOT / "out" / "schedules" / f"{LG_ONA}.json", ona)
        write_json(FIXTURE_KZ, {"about": "金沢の規則と印字の違いのうち、例外の原文で説明のつかないもの (rule_vs_printed の裁定の待ち行列)。"
                                         "decision は人 (firemio) が資料で確かめて printed / rule と書く。まだ誰も決めていない",
                                "source": "out/kanazawa_schedules.json の rule_mismatches", "items": queue})
        print("書いた: out/schedules/172014.json・045811.json、nationwide/readers/eval/fixtures/kanazawa_rule_vs_printed.json")
    ok = not err and not err2 and k1["ok"] and k3["ok"] and o1["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
