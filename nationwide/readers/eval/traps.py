"""罠の再現セット (eval/traps.json) のうち、文法・広げ方・HTML の表の段 (stage: stage2) を確かめる (DESIGN.md §6.2 の T1 の一部)

  python nationwide/readers/expand.py traps        # または python nationwide/readers/eval/traps.py

入力はキャッシュの資料の文 (quotes が資料の中にあることも確かめる) か、型の読み取りがまだ無いものは資料の文そのもの。
ネットにはつながない。キャッシュに無い資料は、quotes の確かめを飛ばし、文だけで確かめる (結果に cached: false)。
"""
import collections, datetime as dt, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
READERS = HERE.parent
ROOT = READERS.parent.parent
for p in (READERS, READERS / "tests", READERS.parent / "discover", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import categories, consume, content, decode, expand, html_tables, rules  # noqa: E402

FY26 = {"period_id": "fy2026", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
        "year_evidence": {"ok": True}}
AGREED = {"readers": [], "independence": "code_llm", "status": "agreed"}


def load():
    return json.loads((HERE / "traps.json").read_text("utf-8"))["traps"]


def cached_text(url):
    """キャッシュの資料の文字 (HTML は decode、PDF は文字層)。無ければ None"""
    import _util
    b = _util.cached(url)
    if b is None:
        return None
    if b[:4] == b"%PDF":
        import fitz
        with fitz.open(stream=b, filetype="pdf") as doc:
            return "".join(pg.get_text() for pg in doc)
    return decode.decode(b, "text/html").text


def quotes_ok(trap):
    """quotes がキャッシュの資料 (source_key のどれか) の中にあるか → (True / False / None (キャッシュに無い), 足りない quote)"""
    keys = [k for k in (trap.get("source_key") or "").split() if k.startswith("http")]
    if trap.get("source_key", "") and trap["source_key"].startswith("data/"):
        texts = [(ROOT / trap["source_key"]).read_text("utf-8")]
    else:
        texts = [t for t in (cached_text(k) for k in keys) if t]
    if not texts:
        return None, []
    hay = [content.squash(content.parse(t).get_text("") if "<" in t[:2000] else t) for t in texts]
    missing = [q for q in trap.get("quotes", []) if not any(content.squash(q) in h for h in hay)]
    return not missing, missing


def sents_of(*lines, heading=()):
    """文字の行 → content.Sentence のリスト (本文の範囲から切ったのと同じ形)"""
    out = []
    for i, t in enumerate(lines):
        out += content.sentences([content.Line(t, list(heading), f"p:nth-of-type({i + 1})", "p")])
    return out


def region_of(url):
    t = cached_text(url)
    if t is None:
        return None
    return content.content_region(t)


def spec(did, cid, text, excs, period=FY26, **kw):
    s = {"district_id": did, "category_id": cid, "period": period, "origin": "expanded",
         "rule": {"text": text, "parsed": rules.parse(text)}, "exceptions": excs, "provenance": AGREED}
    s.update(kw)
    return expand.expand_series(s)


# ---- 罠ごとの確かめ ----
def check_hakui():
    cats = {"kanen": {"coarse": ["burnable"], "official_label": "可燃ごみ"},
            "shigen": {"coarse": ["recyclable", "non_burnable"], "official_label": "資源ごみ・燃えないごみ"}}
    sents = sents_of("※燃えるごみ・資源ごみ・燃えないごみの収集は、祝日、振替休日も実施します。", "※大型連休中もごみ収集は通常通り実施します。")
    excs, used, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    scopes = [e["scope"]["category_ids"] for e in excs]          # コードが決めた品目のまま (上書きしない)
    n = len(excs)
    groups = {"k1": ("月・木", "12/28(月)最終で 1/4(月)から"), "k2": ("火・金", "12/29(火)最終で 1/5(火)から"),
              "k3": ("水・土", "12/30(水)最終で 1/6(水)から")}
    for did, (_, txt) in groups.items():             # 表のマスの年末年始 (可燃の群ごと) は、そのマスの地区・品目に当てる
        e2, probs = expand.cell_exceptions(txt, FY26, {"category_ids": ["kanen"], "district_ids": [f"172073-{did}"]},
                                           {"source_id": "s1", "text": txt}, start=n + 1)
        n += len(e2)
        excs += e2
    mk, _ = expand.cell_exceptions("1/1(金)は休みのため 1/5(火)に特別収集します", FY26, {"category_ids": ["shigen"]},
                                   {"source_id": "s1", "text": "1/1(金)は休みのため 1/5(火)に特別収集します"}, start=n + 1)
    excs += mk
    out = {}
    for did, (rule, _) in groups.items():
        out[did] = spec(f"172073-{did}", "kanen", rule, excs)
    out["s24tu"] = spec("172073-s24tu", "shigen", "第2・4火曜", excs)
    out["s13tu"] = spec("172073-s13tu", "shigen", "第1・3火曜", excs)
    k2 = out["k2"]
    ok = all(s["answerable"] for s in out.values())
    ok &= "2026-12-29" in k2["dates"] and "2027-01-05" in k2["dates"] and "2026-12-30" not in k2["dates"] and "2027-01-04" not in k2["dates"]
    ok &= [(u["from"], u["reason"]) for u in k2["unknown_ranges"]] == [("2026-09-22", "holiday_kind_not_covered")]
    ok &= out["k1"]["unknown_ranges"] == []
    w = ("2026-12-15", "2027-01-31", "year_end_window")
    ok &= [(u["from"], u["to"], u["reason"]) for u in out["s13tu"]["unknown_ranges"]] == [w]
    ok &= sorted((u["from"], u["reason"]) for u in out["s24tu"]["unknown_ranges"]) == [("2026-09-22", "holiday_kind_not_covered"),
                                                                                       ("2026-12-15", "year_end_window")]
    ok &= not any("2026-12-15" <= d <= "2027-01-31" for d in out["s24tu"]["dates"] + out["s13tu"]["dates"])
    ok &= all(e.get("others") is None for e in mk) and mk and mk[0]["treatment"] == "makeup"
    ok &= [e["kind"] for e in excs[:2]] == ["holiday_policy", "long_holiday"]
    ok &= all(sc == "*" or sorted(sc) == ["kanen", "shigen"] for sc in scopes)
    return ok, (f"可燃 k2 {len(k2['dates'])} 日 (12/29 あり・12/30〜1/4 なし・1/5 あり、不明は 9/22 だけ)、"
                f"資源 第2・4火 の不明 {[(u['from'], u['reason']) for u in out['s24tu']['unknown_ranges']]}、方針の文の品目 {scopes}")


def check_ishikari():
    p = rules.parse("毎週月曜日(1回目の月曜日を除く)")
    ok = p.status == "ok" and p.ir == {"kind": "weekly", "weekdays": ["MO"], "except_nth": [1]} and rules.is_monthly(p.ir)
    cats = {"c1": {"coarse": ["non_burnable"]}}
    excs, _, _ = expand.exceptions_from_sentences(sents_of("12月31日から1月3日の収集はありません。", "祝日の収集は休み"), FY26, cats, "s1")
    s = spec("012351-x", "c1", "毎週月曜日(1回目の月曜日を除く)", excs)
    ok &= s["answerable"]
    ok &= any(u["reason"] == "year_end_window" and u["from"] == "2026-12-15" for u in s["unknown_ranges"])
    ok &= not any(d.startswith("2027-01") for d in s["dates"])
    cm = sorted({u["from"][:7] for u in s["unknown_ranges"] if u["reason"] == "closure_month"})
    ok &= "2026-07" in cm and "2026-09" in cm                 # 海の日 7/20・敬老の日 9/21 (どちらも 3 回目の月曜日) で回が消える月
    reg = region_of("https://www.city.ishikari.hokkaido.jp/kurashi/gomi/1001851/1002471.html")
    real = None
    if reg is not None:
        e2, _, _ = expand.exceptions_from_sentences(reg.sentences(), FY26, cats, "s1")
        real = spec("012351-y", "c1", "毎週月曜日(1回目の月曜日を除く)", e2)
        ok &= "year_end_missing" in real["blocked_by"]             # ページの足の開庁時間「年末年始は除く」は方針にしない
    return ok, (f"IR {p.ir}、合成の休みの期間で窓 W は year_end_window、1 月の月曜日なし、closure_month {cm}"
                + (f"、実際のページでは {real['blocked_by']}" if real else ""))


def check_akabira():
    reg = region_of("https://www.city.akabira.hokkaido.jp/docs/2015041400045.html")
    lines = ["5月3日から5月5日及び12月31日から1月2日の期間、収集はお休みです。", "※また、第5週がある月の水曜日、土曜日の収集は休みとなります。"]
    sents = reg.sentences() if reg is not None else sents_of(*lines)
    cats = {"c1": {"coarse": ["burnable"]}}
    excs, used, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    used = expand.consume_parentheticals(sents, used)
    quals = [q for q in content.scan_qualifiers(sents) if not consume.used(q["sentence"])]      # 文の同一性 (stage2 と同じ)
    q5 = [q for q in quals if "第5週がある月" in q["text"]]
    ok = bool(q5) and not q5[0]["months"] and not rules.fifth_week(lines[1])
    qs = [{"qualifier_id": q["qualifier_id"], "months": q["months"]} for q in quals]
    s_rule = spec("012181-x", "c1", "水曜日・土曜日", excs, qualifiers=qs)
    s_print = expand.expand_series({"district_id": "012181-x", "category_id": "c1", "period": FY26, "origin": "printed",
                                    "printed_dates": ["2026-04-01", "2026-04-04"], "exceptions": excs, "qualifiers": qs,
                                    "provenance": AGREED, "gate_unit": "B"})
    tag = f"unconsumed_qualifier:{q5[0]['qualifier_id']}" if q5 else None
    ok &= tag in s_rule["blocked_by"] and tag in s_print["blocked_by"] and not s_rule["answerable"] and not s_print["answerable"]
    gw = sorted(e["date"] for e in excs if e["kind"] == "cancelled")
    ok &= gw == ["2026-05-03", "2026-05-04", "2026-05-05"]
    return ok, f"第5週の但し書きは使われず {tag} (規則・印字の両方)。5/3〜5/5 は cancelled {gw}"


def check_misato_warabi_bifuka():
    res = {q: rules.parse(q + "の水曜日" if "週" in q and not q.endswith("日") else q) for q in ("第1・3週", "第2、4週", "第1週、第3週")}
    ok = all(p.status == "ambiguous" and p.ir["kind"] == "ambiguous_week" for p in res.values())
    ok &= rules.parse("第2、4週の月2回").status == "ambiguous"
    s = spec("112372-x", "c1", "第1・3週の水曜日", [], )
    ok &= "rule_ambiguous" in s["blocked_by"]
    reg = region_of("https://www.city.misato.lg.jp/soshiki/shiminseikatsu/kurinraifu/2/9739.html")
    mult = None
    if reg is not None:
        sts = collections.Counter()
        for _, t in html_tables.tables(reg.root):
            rd = html_tables.read_table(t)
            for r in rd.get("rows", []):
                for label, p in r["rules"].items():
                    if "第1・3週" in content.nfkc(label):
                        sts[p.status] += 1
        mult = dict(sts)
        ok &= bool(sts) and set(sts) <= {"multiple", "ambiguous"}
    return ok, f"3 つとも ambiguous_week → rule_ambiguous。三郷の見出しの列のマスの読み {mult}"


def check_kunitachi_misato():
    cats = {"c1": {"coarse": ["burnable"]}}
    lines = ["12月31日のごみ収集はありません", "注意：12月31日（木）は収集しません。", "注意：1月1日（金）は収集しません。"]
    sents = sents_of(*lines, heading=("ごみの収集",))
    excs, used, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    ye = [e for e in excs if e["kind"] == "year_end" and e["treatment"] == "skip"]
    left = [q for q in content.scan_qualifiers(sents) if not consume.used(q["sentence"])]
    ok = len(ye) == 3 and sorted({d for e in ye for d in e["dates"]}) == ["2026-12-31", "2027-01-01"] and not left
    s = spec("132152-x", "c1", "月・木", excs + [{"exception_id": "h", "kind": "holiday_policy", "value": "collected",
                                                  "covers": ["national"], "text": "祝日も収集"}])
    ok &= s["answerable"] and "2026-12-31" not in s["dates"] and "2027-01-01" not in s["dates"]
    return ok, f"3 文とも year_end skip ({sorted({d for e in ye for d in e['dates']})})、洗い出しで残った文 {len(left)}"


def check_kanazawa():
    import regress
    if not (ROOT / "data" / "kanazawa_rules.html").exists() or not (ROOT / "out" / "kanazawa_schedules.json").exists():
        return None, "キャッシュに無い"
    v01 = json.loads((ROOT / "out" / "kanazawa_schedules.json").read_text("utf-8"))
    html = (ROOT / "data" / "kanazawa_rules.html").read_text("utf-8", errors="replace")
    try:
        html = decode.decode((ROOT / "data" / "kanazawa_rules.html").read_bytes(), "text/html").text
    except decode.DecodeError:
        pass
    reg = content.content_region(html)
    cats = {c: {"coarse": k} for c, k in (("burnable", ["burnable"]), ("nonburnable", ["non_burnable"]),
                                           ("recyclable", ["beverage_can", "pet_bottle", "plastic"]), ("plastic", ["plastic"]),
                                           ("bottle", ["glass_bottle"]))}
    sents = [s for s in reg.sentences() if s.heading_path and "年末年始のごみ収集" in s.heading_path[-1]]
    city, _, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s_rules")
    city = [e for e in city if e["kind"] == "year_end"]
    hp = {"exception_id": "e_holiday", "kind": "holiday_policy", "value": "collected", "covers": ["all_weekdays"], "scope": {"category_ids": "*"},
          "text": "収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。"}
    assert rules.holiday_policy(hp["text"])["covers"] == ["all_weekdays"]
    ok = len(city) == 1 and city[0]["last"] == "2026-12-29" and city[0]["first"] == "2027-01-04"
    n_mism = n_q = 0
    k2_burn_same = k2_burn_n = k2_pending = 0
    k2b_mismatch_days_in_dates = 0
    k2b_outside_diff = 0
    k2b_unknown_ok = 0
    k2b_n = 0
    for s in v01["schedules"]:
        did, cat = f"172014-{s['district']}", s["category"]
        printed = sorted(d.isoformat() for d in regress.legacy_occurrences(s))
        p = rules.parse(s["rule_text"])
        if cat == "burnable":
            # マスの読み残し (年末年始の文) は cell_notes が読み切ったときだけ使ったことにする (読み残しがあれば止まる)
            ce, left = expand.cell_notes(p.rest, FY26, {"category_ids": [cat], "district_ids": [did]},
                                         {"source_id": "s_cal", "text": p.rest}, prefix="e_ye")
            excs = [hp] + ce
            r = expand.expand_series({"district_id": did, "category_id": cat, "period": FY26, "origin": "expanded",
                                      "rule": {"text": s["rule_text"], "parsed": p}, "exceptions": excs, "provenance": AGREED,
                                      "rest_consumed": not left})
            k2_burn_n += 1
            k2_burn_same += r["answerable"] and r["dates"] == printed and r["unknown_ranges"] == []
            continue
        by = {"year_end": city}
        mism, queue = expand.compare_printed(p.ir, printed, FY26, by, f"172014:fy2026:rule_vs_printed:{did}:{cat}")
        n_mism += len(mism)
        n_q += len(queue)
        r1 = expand.expand_series({"district_id": did, "category_id": cat, "period": FY26, "origin": "expanded",
                                   "rule": {"text": s["rule_text"], "parsed": p}, "exceptions": [hp] + city, "printed_pending": True,
                                   "provenance": AGREED})
        k2_pending += r1["blocked_by"] == ["pending_printed"]
        r2 = expand.expand_series({"district_id": did, "category_id": cat, "period": FY26, "origin": "expanded",
                                   "rule": {"text": s["rule_text"], "parsed": p}, "exceptions": [hp] + city, "provenance": AGREED})
        k2b_n += 1
        bad_days = set()
        for mm in s.get("rule_mismatches", []):
            y, mo = map(int, mm["month"].split("-"))
            bad_days |= {dt.date(y, mo, x).isoformat() for x in set(mm["rule"]) ^ set(mm["table"])}
        k2b_mismatch_days_in_dates += len(bad_days & set(r2["dates"]))
        outside = lambda ds: [d for d in ds if not "2026-12-15" <= d <= "2027-01-31"]
        k2b_outside_diff += outside(r2["dates"]) != outside(printed)
        k2b_unknown_ok += [(u["from"], u["to"], u["reason"]) for u in r2["unknown_ranges"]] == [("2026-12-15", "2027-01-31", "year_end_window")]
    ok &= n_mism == 13 and n_q == 62
    ok &= k2_burn_n == 71 and k2_burn_same == 71 and k2_pending == 284 and k2b_n == 284
    ok &= k2b_mismatch_days_in_dates == 0 and k2b_outside_diff == 0 and k2b_unknown_ok == 284
    return ok, (f"K3: rule_mismatches {n_mism}・rule_vs_printed {n_q}。K2 (1): 燃やすごみ {k2_burn_same}/{k2_burn_n} が v0.1 と同じ、"
                f"ほか {k2_pending}/284 が pending_printed。K2 (2): 窓 W が year_end_window {k2b_unknown_ok}/284、"
                f"違う日が dates に入った数 {k2b_mismatch_days_in_dates}、窓の外が印字と違う系列 {k2b_outside_diff}")


def check_higashichichibu():
    import stage2

    class A:
        llm = False
        offline = True
    if not (stage2.OUT / "113697" / "docs.json").exists():
        return None, "キャッシュに無い"
    doc, errs, _, _ = stage2.run_one("113697", A(), write=False)
    if not doc["sources"]:
        return None, "キャッシュに無い"
    s = doc["schedules"]
    ok = bool(s) and not errs and all("year_end_missing" in x["blocked_by"] and not x["answerable"] and not x["dates"] for x in s)
    unc = [q["text"] for src in doc["sources"] for q in (src.get("extra") or {}).get("qualifiers_unconsumed", [])]
    ok &= not any("ペットボトルは" in t for t in unc)
    pet = [x for x in s if "ペットボトル" in next(c["official_label"] for c in doc["categories"] if c["category_id"] == x["category_id"])]
    ok &= bool(pet) and all("rule_unparsed" in x["blocked_by"] for x in pet)
    return ok, (f"{len(s)} 系列すべて year_end_missing で日付なし。ペットボトルの但し書きは規則のマスの読みが使った (残りの但し書き {len(unc)})、"
                f"ペットボトルの系列は {pet[0]['blocked_by'] if pet else None}")


def check_ota():
    ok = rules.parse("ー").status == "none" and rules.parse("-").status == "none" and rules.parse("ー ").irs == [{"kind": "none"}]
    reg = region_of("https://www.city.ota.tokyo.jp/seikatsu/gomi/shigentogomi/gomishigen.html")
    sents = reg.sentences() if reg is not None else sents_of("注釈：年末年始の収集日は変更する場合があります。区報、町会回覧、ホームページなどでお知らせします。")
    cats = {"c1": {"coarse": ["non_burnable"]}}
    excs, _, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    d = [e for e in excs if e["kind"] == "deferred" and e["period_name"] == "年末年始"]
    ok &= bool(d) and d[0]["window"]["from"] == "2026-12-15" and d[0]["window"]["to"] == "2027-01-31"
    s = expand.expand_series({"district_id": "131113-x", "category_id": "c1", "period": FY26, "origin": "listed",
                              "printed_dates": ["2026-12-09", "2026-12-23", "2027-01-13", "2027-01-27", "2027-02-10"],
                              "exceptions": excs, "provenance": AGREED, "gate_unit": "B"})
    ok &= s["answerable"] and s["dates"] == ["2026-12-09", "2027-02-10"] and any(u["reason"] == "deferred" for u in s["unknown_ranges"])
    return ok, f"「ー」は none。deferred (年末年始) が一覧の日付より強く、窓の中の日付を消して不明に ({s['unknown_ranges']})"


def check_shichikashuku():
    p = rules.parse("第２･５木曜日")
    ok = p.ir == {"kind": "monthly_nth", "weekday": "TH", "nth": [2, 5]}
    reg = region_of("https://town.shichikashuku.miyagi.jp/life/gomi.html")
    st = None
    if reg is not None:
        sts = [html_tables.structure(t) for _, t in html_tables.tables(reg.root)]
        st = [x for x in sts if not x["ok"]]
        ok &= bool(st)
    per = {"period_id": "undated", "valid_from": "2026-10-19", "valid_to": None, "basis": {"text": "-", "kind": "undated"},
           "year_evidence": {"ok": False}}
    s = spec("043028-x", "c1", "第２･５木曜日", [], period=per, disputes=["043028:undated:structure:s1:t0"])
    ok &= "period_unknown" in s["blocked_by"] and "disputed:043028:undated:structure:s1:t0" in s["blocked_by"]
    return ok, f"IR {p.ir}。壊れた表 {st[0]['issues'] if st else None} → structure の裁定、undated → period_unknown"


def check_hidaka():
    cats = {"kanen": {"coarse": ["burnable"]}, "pet": {"coarse": ["pet_bottle"]}}
    t26 = "※注意 5月4日(月)、12月31日(木)は、施設の定期点検のため可燃ごみの収集は行いません。"
    t24 = "※注意 5月6日(月)、1月2日(木)は、施設の定期点検のため可燃ごみの収集は行いません。"
    excs, used, loose = expand.exceptions_from_sentences(sents_of(t26, t24), FY26, cats, "s1")
    canc = [e for e in excs if e["kind"] == "cancelled"]
    skip = [e for e in excs if e["kind"] == "year_end"]
    ok = [e["date"] for e in canc] == ["2026-05-04"] and skip and skip[0]["dates"] == ["2026-12-31"]
    ok &= all(e["scope"]["category_ids"] == ["kanen"] for e in canc + skip)
    ok &= len(loose) == 1 and "weekday_mismatch" in loose[0]["problems"]       # 令和6年度の図の文は曜日が合わない → 使わない
    hp = {"exception_id": "h", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集"}
    s = spec("112429-x", "kanen", "月・木", excs + [hp])
    ok &= s["answerable"] and "2026-05-04" not in s["dates"] and "2026-12-31" not in s["dates"] and "2026-05-07" in s["dates"]
    sp = spec("112429-x", "pet", "第1・3月曜日", excs + [hp])
    ok &= "year_end_missing" in sp["blocked_by"]                             # 点検の文は可燃だけ。ペットボトルには年末年始の文が無い
    return ok, (f"令和8年度の文 → 可燃の cancelled {[e['date'] for e in canc]}・year_end skip {skip[0]['dates'] if skip else None}。"
                f"令和6年度の文は曜日が合わず使わない ({loose[0]['problems'] if loose else None})")


def check_miyake_kawakita():
    m, ev = categories.mode("粗大可燃ごみ", "持込(三宅村クリーンセンター持込時間 午前9時~午後4時30分) 月曜日・水曜日・金曜日")
    ok = m == "dropoff"
    cats = {"c1": {"coarse": ["burnable"]}}
    reg = region_of("https://www.town.kawakita.ishikawa.jp/seikatsu1/gomi/")
    line = "※収集日は、祝日・お盆休み・年末年始等により、一部変更になることがあります。"
    sents = reg.sentences() if reg is not None else sents_of(line)
    excs, _, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    d = {e["period_name"]: e for e in excs if e["kind"] == "deferred" and line in e["text"]}
    ok &= "年末年始" in d and "お盆" in d and d["お盆"]["window"] is None and d["年末年始"]["window"]["from"] == "2026-12-15"
    ok &= not any(e["kind"] == "holiday_policy" and line in e["text"] for e in excs)
    s = spec("173240-x", "c1", "毎週 月曜日・木曜日", [e for e in excs if line in e["text"]])
    ok &= "deferred_undated:お盆" in s["blocked_by"] and "holiday_policy_missing" in s["blocked_by"] and not s["answerable"]
    return ok, f"三宅の持込は {m} (日付の系列にしない)。川北は {s['blocked_by']}"


def read_page(url, lg, name):
    """キャッシュのページを stage2 の読み (コードだけ。LLM は使わない) に通す → (Muni, スキーマ v0.2 の dict) か None (キャッシュに無い)"""
    import stage2
    if stage2.body_of(url)[0] is None:
        return None

    class A:
        llm = False
        offline = True
    m = stage2.Muni(lg, name)
    stage2.read_source(m, {"url": url, "file_type": "html", "format_class": "html_table"}, A(), [])
    if not m.sources:
        return None
    return m, stage2.build_doc(m)


def check_nomi():
    import schema
    r = read_page("https://www.city.nomi.lg.jp/docs/5563.html", "172111", "能美市")
    if r is None:
        return None, "キャッシュに無い"
    m, doc = r
    ds = {d["district_id"]: d for d in doc["districts"]}
    cat = {c["official_label"]: c["category_id"] for c in doc["categories"]}
    pla = cat.get("容器包装プラ")
    split_i = [d for d in doc["districts"] if (d.get("extra") or {}).get("split_from_row") and d["district_id"].endswith("-i1")]
    split_x = [d for d in doc["districts"] if (d.get("extra") or {}).get("split_from_row") and d["district_id"].endswith("-x")]
    names = lambda d: [(x.get("extra") or {}).get("name_verbatim") for x in d["members"]]
    ok = len(split_i) == 1 and len(split_x) == 1 and names(split_i[0]) == ["松が岡"] and "松が岡" not in names(split_x[0])
    ok &= split_x[0]["extra"]["split_from_row"] == split_i[0]["extra"]["split_from_row"] if ok else False
    irs = {s["district_id"]: s["rule"]["ir"] for s in doc["schedules"] if s["category_id"] == pla}
    ok &= bool(split_i) and irs.get(split_i[0]["district_id"]) == {"kind": "monthly_nth", "weekday": "TH", "nth": [2, 4]}
    ok &= bool(split_x) and irs.get(split_x[0]["district_id"]) == {"kind": "monthly_nth", "weekday": "TH", "nth": [1, 3]}
    ok &= any(e["kind"] == "inline" for e in doc["exceptions"])
    ok &= all("PDF" not in d["label"] for d in ds.values())                                  # リンクの文字を外す
    ok &= all("pending_printed" in s["blocked_by"] and not s["answerable"] for s in doc["schedules"])   # 地区ごとの PDF
    unc = [q["text"] for src in doc["sources"] for q in (src.get("extra") or {}).get("qualifiers_unconsumed", [])]
    ok &= not any("松が岡" in t for t in unc) and not schema.validate(doc)
    return ok, (f"「注：松が岡」で行を分けた ({split_i[0]['district_id'] if split_i else None}: 第2・4木、残り: 第1・3木)、"
                f"リンクの文字を外し、{len(doc['schedules'])} 系列すべて pending_printed")


def check_shibuya_murata():
    reg = region_of("https://www.city.shibuya.tokyo.jp/kurashi/gomi/kateigomi/gomid.html")
    cats = {"funen": {"coarse": ["non_burnable"]}, "kanen": {"coarse": ["burnable"]}}
    # キャッシュに無いときは、ページと同じ節の形の文で (全体の文は節の外、注は不燃ごみの節の中)
    sents = reg.sentences() if reg is not None else (
        sents_of("年末年始は収集を休んだり、変更することがあります。日程が決まり次第、しぶや区ニュース等でお知らせします。",
                 heading=("ごみ収集日", "地域ごとの収集日（曜日）")) +
        sents_of("毎月29日・30・31日の不燃ごみの収集はありません。(年末年始は変更あり)",
                 heading=("ごみ収集日", "地域ごとの収集日（曜日）", "（注）カレンダーの見方 不燃ごみ「月1回」収集日")))
    excs, _, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    d_all = [e for e in excs if e["kind"] == "deferred" and "収集を休んだり" in e["text"]]
    d_fun = [e for e in excs if e["kind"] == "deferred" and e["text"].strip() in ("(年末年始は変更あり)", "（年末年始は変更あり）")]
    ok = bool(d_all) and d_all[0]["scope"]["category_ids"] == "*" and bool(d_fun) and d_fun[0]["scope"]["category_ids"] == ["funen"]
    ok &= not any(e["kind"] == "caveat" and "年末年始" in e["text"] for e in excs)
    s = expand.expand_series({"district_id": "131130-x", "category_id": "kanen", "period": FY26, "origin": "listed",
                              "printed_dates": ["2026-12-16", "2026-12-30", "2027-02-03"], "exceptions": excs, "provenance": AGREED})
    ok &= s["answerable"] and s["dates"] == ["2027-02-03"]
    m_excs, _, _ = expand.exceptions_from_sentences(sents_of("※ごみの収集日は変更する場合があります。"), FY26, cats, "s2")
    ok &= [e["kind"] for e in m_excs] == ["caveat"]
    s2 = expand.expand_series({"district_id": "043222-x", "category_id": "kanen", "period": FY26, "origin": "printed",
                               "printed_dates": ["2026-12-16", "2026-12-30"], "exceptions": m_excs, "provenance": AGREED})
    ok &= s2["answerable"] and s2["dates"] == ["2026-12-16", "2026-12-30"] and s2["caveats"] == [m_excs[0]["exception_id"]]
    return ok, f"渋谷は deferred (全品目と不燃ごみの節)、一覧の日付の窓の中は不明。村田は caveat で日付を消さず注意 {s2['caveats']}"


def check_shinjuku():
    reg = region_of("https://www.city.shinjuku.lg.jp/seikatsu/file09_01_00001.html")
    cats = {"metal": {"coarse": ["metal"]}, "kanen": {"coarse": ["burnable"]}}
    line = "金属・陶器・ガラスごみの収集日は、その月の「1番目と3番目にあたる各曜日」または「2番目と4番目にあたる各曜日」です。月の「5番目の曜日」に収集はありません。"
    sents = reg.sentences() if reg is not None else sents_of(line)
    excs, used, _ = expand.exceptions_from_sentences(sents, FY26, cats, "s1")
    fw = [e for e in excs if e["kind"] == "fifth_week"]
    ok = bool(fw) and fw[0]["scope"]["category_ids"] == ["metal"]
    ok &= not any("5番目の曜日" in q["text"] for q in content.scan_qualifiers(sents) if not consume.used(q["sentence"]))
    hp = {"exception_id": "h", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も"}
    ye = {"exception_id": "y", "kind": "year_end", "treatment": "dates", "dates": [], "text": "x"}
    s = spec("131041-x", "metal", "第1・3・5水曜日", fw + [hp, ye])
    ok &= s["answerable"] and not any(rules.nth_of_month(dt.date.fromisoformat(d)) == 5 for d in s["dates"])
    w = spec("131041-y", "metal", "毎週水曜日", fw + [hp, ye])          # 毎週の規則でも第5週の扱いがあれば月ごとの規則 (窓 W は不明)
    ok &= w["answerable"] and any(u["reason"] == "year_end_window" for u in w["unknown_ranges"])
    summary = f"「5番目の曜日」は金属・陶器・ガラスの fifth_week が使い、5 回目の日は作らない ({fw[0]['scope'] if fw else None})"
    r = read_page("https://www.city.shinjuku.lg.jp/seikatsu/file09_01_00001.html", "131041", "新宿区")
    if r is not None:
        import schema
        m, doc = r
        contact = doc["coverage"]["by_district"]
        cs = [x for x in doc["schedules"] if x["district_id"] in contact]
        ok &= bool(contact) and set(contact.values()) == {"rule_contact"}
        ok &= bool(cs) and all("rule_unparsed" in x["blocked_by"] and not x["answerable"] and
                               x["extra"].get("answer_type") == "rule_contact" for x in cs)          # 「*」の行は種類 3
        ok &= doc["partitions"][0]["basis"] == "collection_point"                                     # 集積所の住所
        conds = collections.Counter(c["kind"] for d in doc["districts"] for x in d["members"] for c in x["conditions"])
        ok &= conds["remainder"] >= 1 and conds["partial"] >= 1 and not schema.validate(doc)          # 条件は正規形
        metal = [c["category_id"] for c in doc["categories"] if "金属" in c["official_label"]]
        fws = [e for e in doc["exceptions"] if e["kind"] == "fifth_week"]
        ok &= bool(fws) and all(e["scope"]["category_ids"] == metal for e in fws)
        sp = next((x for x in m.schedules if x["category_id"] in metal and x["rule"]["parsed"].ir), None)
        if sp is not None and sp["period"].get("valid_to"):
            _, by = expand.conditions(sp)
            ok &= "fifth_week" in by and not any(rules.nth_of_month(d) == 5 for d in expand.base_dates(sp["rule"]["parsed"].ir, sp["period"], by))
        else:
            ok = False
        summary += (f"。ページ: 「*」の行 {len(contact)} 地区は種類 3 (rule_unparsed)、basis {doc['partitions'][0]['basis']}、"
                    f"条件 remainder {conds['remainder']}・partial {conds['partial']}")
    return ok, summary


CHECKS = {"hakui": check_hakui, "ishikari": check_ishikari, "akabira": check_akabira, "misato_warabi_bifuka": check_misato_warabi_bifuka,
          "kunitachi_misato": check_kunitachi_misato, "kanazawa": check_kanazawa, "higashichichibu": check_higashichichibu,
          "ota": check_ota, "shichikashuku": check_shichikashuku, "hidaka": check_hidaka, "miyake_kawakita": check_miyake_kawakita,
          "nomi": check_nomi, "shibuya_murata": check_shibuya_murata, "shinjuku": check_shinjuku}


# ---- stage3: 型 D の読み・比べ・待ち行列を通した確かめ (build.py をキャッシュだけで通す。LLM の答えもキャッシュだけ) ----
def _build_offline(lg):
    import argparse, build
    a = argparse.Namespace(llm=True, offline=True, write=False)          # B はキャッシュだけ (無ければ single)
    r = build.build_one(lg, a)
    return r, build.LAST.get("doc"), build.LAST.get("queue") or []


def check_hidaka_d():
    import _util
    if _util.cached("https://www.city.hidaka.lg.jp/material/files/group/13/reiwa8nendogominitteihyou.pdf") is None:
        return None, "キャッシュに無い"
    r, doc, q = _build_offline("112429")
    dists = {d["district_id"]: d for d in doc["districts"]}
    broken = [d for d in dists.values() if (d.get("extra") or {}).get("course_label") and
              any(0x2B00 <= ord(c) <= 0x2BFF for c in d["extra"]["course_label"])]
    bs = [s for s in doc["schedules"] if s["district_id"] in {d["district_id"] for d in broken}]
    ok = len(broken) == 11 and bs and all(not s["answerable"] for s in bs)
    ok &= all((s.get("extra") or {}).get("states", {}).get("course_label") == "single" for s in bs if (s.get("extra") or {}).get("states"))
    burn = next(c["category_id"] for c in doc["categories"] if c["official_label"] == "可燃ごみ")
    canc = [e for e in doc["exceptions"] if e["kind"] == "cancelled" and e["scope"].get("category_ids") == [burn]]
    ok &= len({tuple(e["scope"]["district_ids"]) for e in canc}) >= 19           # 20 コースのうち「注意１」の 13 番を除く
    csv_ids = {x["source_id"] for x in doc["sources"] if x["url"].endswith(".csv")}
    ok &= all({"version_unproven", "period_unknown"} & set(s["blocked_by"]) for s in doc["schedules"]
              if (s.get("ref") or {}).get("source_id") in csv_ids)
    ok &= r["answerable"] == 0
    return ok, (f"壊れたコース番号の表 {len(broken)} (その系列 {len(bs)} はどれも答えない)・点検の文の cancelled {len(canc)}・"
                f"CSV の系列 {sum(1 for s in doc['schedules'] if (s.get('ref') or {}).get('source_id') in csv_ids)} は版で止まる・answerable {r['answerable']}")


def check_mitaka_january():
    import _util
    if _util.cached("https://www.city.mitaka.lg.jp/c_service/095/attached/attach_95671_8.pdf") is None:
        return None, "キャッシュに無い"
    r, doc, q = _build_offline("132047")
    rvp = [i for i in q if i["kind"] == "rule_vs_printed"]
    months = sorted({i["item_id"].rsplit(":", 1)[1] for i in rvp})
    blocked = [s for s in doc["schedules"] if any(b.startswith("disputed:") and ":rule_vs_printed:" in b for b in s["blocked_by"])]
    ok = bool(rvp) and months == ["2027-01"] and all(not s["answerable"] for s in blocked) and r["answerable"] == 0
    ok &= any(i["kind"] == "structure" for i in q)                   # 毎週の列の破線: 表の形は人へ
    return ok, f"HTML の規則と PDF の印字が 1 月だけ違う (下連雀) → rule_vs_printed {len(rvp)} 件 {months}、止めた系列 {len(blocked)}"


CHECKS.update({"hidaka_d": check_hidaka_d, "mitaka_january": check_mitaka_january})


def run_all(stage="stage2"):
    out = []
    for t in load():
        if t.get("stage") != stage:
            continue
        qok, missing = quotes_ok(t) if t.get("quotes") else (None, [])
        fn = CHECKS.get(t["trap_id"])
        if fn is None:
            out.append({"trap_id": t["trap_id"], "ok": False, "summary": "確かめが無い"})
            continue
        ok, summary = fn()
        if ok is None:              # 確かめに要る資料がキャッシュ (git に入らない data/) に無い → 飛ばす (誤りにも合格にも数えない)
            out.append({"trap_id": t["trap_id"], "ok": False, "skipped": True, "summary": summary, "cached": False})
            continue
        if qok is False:
            ok = False
            summary += f" / 資料の中に無い quote: {missing}"
        out.append({"trap_id": t["trap_id"], "ok": bool(ok), "summary": summary + ("" if qok else " (資料はキャッシュに無い)" if qok is None else ""),
                    "cached": qok is not None})
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    res = run_all() + (run_all("stage3") if "--stage3" in sys.argv else [])
    for r in res:
        print(("SKIP" if r.get("skipped") else "OK  " if r["ok"] else "NG  ") + r["trap_id"].ljust(22) + " " + r["summary"])
    done = [r for r in res if not r.get("skipped")]
    print(f"{sum(r['ok'] for r in done)}/{len(done)} が期待する安全な結果" + (f" (飛ばした {len(res) - len(done)})" if len(done) < len(res) else ""))
    return 0 if all(r["ok"] for r in done) else 1


if __name__ == "__main__":
    sys.exit(main())
