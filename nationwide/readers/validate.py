"""自分の中での確かめ (DESIGN.md §4.1 の 1〜21)。系列ごとに、当てはまる確かめ・当てはまらない確かめを分けて見る

  python nationwide/readers/validate.py out/schedules/112429.json [...]     # 確かめの結果の要約 (書き直さない)

- 「当てはまらない」(規則から作った日付を、同じ IR から作った日付と比べるもの) は数えない。当てはまるのに材料が無くて見られない
  ものは not_run に理由を書く (通ったと数えない)
- 落ちた確かめは待ち行列の項目 (kind は確かめごと) にし、その系列を disputed:<item_id> で止める (answerable を false に、
  dates と unknown_ranges を空にする)。落ちたのを黙って通さない
- ctx は build.py が渡す読みの材料: {"sources": {source_id: {"sentences": [content.Sentence], "used_texts": [...],
  "qualifiers": [...], "page": ページの番号か None, "kind": "html" | "pdf"}}, "docs_not_fetched": [...], "islands": bool}
"""
import collections, datetime as dt, json, re, sys

import content, expand, holidays, rules, schema

APPLIES = {   # 確かめの番号 → 当てはまる日付の出どころ (printed・listed / expanded / all)
    1: "printed", 2: "printed", 3: "printed", 4: "printed", 5: "printed", 6: "printed", 7: "printed", 8: "printed",
    9: "all", 10: "all", 11: "all", 12: "all", 13: "expanded", 14: "printed", 15: "all", 16: "all", 17: "expanded",
    18: "printed", 19: "all", 20: "all", 21: "all"}
KIND_OF = {1: "cell", 2: "cell", 3: "cell", 4: "cell", 5: "policy", 7: "cell", 8: "cell", 9: "members", 12: "version",
           13: "policy", 15: "qualifier", 16: "markup", 17: "policy", 18: "rule_vs_printed", 20: "policy", 21: "policy"}


def _d(x):
    return dt.date.fromisoformat(str(x)[:10])


def _applies(n, s):
    a = APPLIES[n]
    o = s.get("dates_origin") or ("expanded" if (s.get("rule") or {}).get("ir") and not s.get("dates") else None)
    if a == "all":
        return True
    if a == "printed":
        return o in ("printed", "listed")
    return o == "expanded"


class Result:
    def __init__(self):
        self.checks = {n: {"applies_to": APPLIES[n], "ran": 0, "failed": 0, "not_run": collections.Counter(), "examples": []}
                       for n in APPLIES}
        self.fails = []            # [(番号, 系列の鍵, 説明)]

    def ran(self, n):
        self.checks[n]["ran"] += 1

    def not_run(self, n, why):
        self.checks[n]["not_run"][why] += 1

    def fail(self, n, s, note):
        self.checks[n]["failed"] += 1
        key = schema.series_key(s) if s else None
        if len(self.checks[n]["examples"]) < 5:
            self.checks[n]["examples"].append({"series": key, "note": note})
        self.fails.append((n, key, note))

    def summary(self):
        return {str(n): {**{k: v for k, v in c.items() if k != "not_run"}, "not_run": dict(c["not_run"])} for n, c in self.checks.items()}


def _weekdays_of(s):
    """系列の曜日の決まり (規則の IR か、列の見出しの曜日 extra.column_note) → 曜日の番号の集まりか None"""
    ir = (s.get("rule") or {}).get("ir")
    if ir and ir.get("kind") == "weekly" and not ir.get("except_nth"):
        return {rules.WD_IDX[w] for w in ir["weekdays"]}
    if ir and ir.get("kind") == "monthly_nth":
        return {rules.WD_IDX[ir["weekday"]]}
    note = ((s.get("extra") or {}).get("column_note") or "")
    if note:
        p = rules.parse(note)
        irs = [x for x in p.irs if x and x.get("kind") in ("weekly",)]
        if len(irs) == 1:
            return {rules.WD_IDX[w] for w in irs[0]["weekdays"]}
    return None


def _count_of(s):
    """列の見出し・規則の文の回数の書き添え (「（月2回）」) → 月の回数か None"""
    note = nfkc(((s.get("extra") or {}).get("column_note") or "") + " " + ((s.get("rule") or {}).get("text") or ""))
    m = re.search(r"月\s*(\d)\s*回", note)
    return int(m.group(1)) if m else None


def nfkc(t):
    return content.nfkc(t or "")


def _explained(d, excs, s):
    """日 d が例外の原文 (振り替え・足す・動かす) で説明がつくか"""
    for e in excs:
        if not expand.in_scope(e, s["category_id"], s["district_id"], s["period_id"]):
            continue
        if e["kind"] == "year_end" and e.get("treatment") == "makeup" and e.get("to") == d.isoformat():
            return True
        if e["kind"] in ("added", "moved") and (e.get("date") == d.isoformat() or e.get("to") == d.isoformat()):
            return True
    return False


def run(doc, ctx=None):
    """v0.2 の dict と読みの材料 → Result"""
    ctx = ctx or {}
    res = Result()
    periods = {p["period_id"]: p for p in doc["periods"]}
    excs = doc.get("exceptions", [])
    cats = {c["category_id"]: c for c in doc["categories"]}
    scheds = doc["schedules"]
    for s in scheds:
        p = periods[s["period_id"]]
        ds = [_d(x) for x in s.get("dates") or []]
        unk = schema.unknown_days(s)
        printed = s.get("dates_origin") in ("printed", "listed")
        # 1 曜日
        if _applies(1, s) and s.get("answerable"):
            wds = _weekdays_of(s)
            if wds is None:
                res.not_run(1, "規則・曜日の書き添えが無い")
            else:
                res.ran(1)
                bad = [d for d in ds if d.weekday() not in wds and not _explained(d, excs, s)]
                if bad:
                    res.fail(1, s, f"曜日が規則と合わない日 {[x.isoformat() for x in bad[:3]]}")
        # 2 何回目
        if _applies(2, s) and s.get("answerable"):
            ir = (s.get("rule") or {}).get("ir")
            if not ir or ir.get("kind") != "monthly_nth":
                res.not_run(2, "第N の規則が無い")
            else:
                res.ran(2)
                bad = [d for d in ds if rules.nth_of_month(d) not in ir["nth"] and not _explained(d, excs, s)]
                if bad:
                    res.fail(2, s, f"何回目が規則と合わない日 {[x.isoformat() for x in bad[:3]]}")
        # 3 月ごとの数
        if _applies(3, s) and s.get("answerable"):
            k = _count_of(s)
            if k is None:
                res.not_run(3, "月の回数が資料に無い")
            else:
                res.ran(3)
                by_m = collections.Counter((d.year, d.month) for d in ds)
                vf, vt = _d(p["valid_from"]), _d(p["valid_to"])
                bad = []
                y, m = vf.year, vf.month
                while dt.date(y, m, 1) <= vt:
                    month_days = {x for x in unk if (x.year, x.month) == (y, m)}
                    if m not in (12, 1) and not month_days and by_m.get((y, m), 0) != k:
                        bad.append(f"{y}-{m:02d}: {by_m.get((y, m), 0)}")
                    m += 1
                    if m == 13:
                        y, m = y + 1, 1
                if bad:
                    res.fail(3, s, f"月の回数 {k} と合わない月 {bad[:3]}")
        # 4 期間
        if _applies(4, s) and s.get("answerable"):
            res.ran(4)
            lo, hi = p["valid_from"], p.get("valid_to") or "9999-12-31"
            if any(not lo <= x <= hi for x in s.get("dates") or []):
                res.fail(4, s, "期間の外の日付")
        # 5 年末年始 (印字の日付が、資料の休みの日に無い)
        if _applies(5, s) and s.get("answerable"):
            mine = [e for e in excs if expand.in_scope(e, s["category_id"], s["district_id"], s["period_id"])
                    and e["kind"] in ("year_end", "cancelled")]
            if not mine:
                res.not_run(5, "年末年始・休みの文が無い")
            else:
                res.ran(5)
                off = expand.explained_days({"year_end": [e for e in mine if e["kind"] == "year_end"],
                                             "cancelled": [e for e in mine if e["kind"] == "cancelled"]}, p)
                bad = [d for d in ds if d in off]
                if bad:
                    res.fail(5, s, f"休みと書いた日に収集の日がある {[x.isoformat() for x in bad[:3]]}")
        # 6 祝日の印・14 祝日の表との突き合わせ: 型 D・B の日付の表には祝日の印が無い
        if _applies(6, s) and s.get("answerable"):
            res.not_run(6, "祝日の印を刷った月表ではない (型 D・B の日付の表)")
        if _applies(14, s) and s.get("answerable"):
            res.not_run(14, "祝日の印を刷った月表ではない (型 D・B の日付の表)")
        # 8 格子: 日が月の日数の中で並んでいる
        if _applies(8, s) and s.get("answerable"):
            res.ran(8)
            if s.get("dates") != sorted(set(s.get("dates") or [])):
                res.fail(8, s, "日付が並んでいないか重なる")
        # 12 版の証拠
        if s.get("answerable"):
            res.ran(12)
            ye = p.get("year_evidence") or {}
            tok = ye.get("token") or {}
            if not ye.get("ok") or tok.get("where") == "anchor_only" or ye.get("conflict"):
                res.fail(12, s, "版の証拠がそろわない")
        # 13 月ごとの規則の窓 W と休みの月
        if _applies(13, s) and s.get("answerable"):
            ir = (s.get("rule") or {}).get("ir")
            by = collections.defaultdict(list)
            for e in excs:
                if expand.in_scope(e, s["category_id"], s["district_id"], s["period_id"]):
                    by[e["kind"]].append(e)
            if ir and expand.is_monthly(ir, by):
                res.ran(13)
                stated = [e for e in by.get("year_end", []) if e.get("others") == "unchanged" or e.get("treatment") == "dates"]
                if not stated:
                    for a, b in expand.year_end_windows(p["valid_from"], p["valid_to"]):
                        inside = [d for d in ds if a <= d <= b]
                        cover = all(x in unk for x in expand._days(a, b))
                        if inside or not cover:
                            res.fail(13, s, f"窓 W ({a}〜{b}) に回の数え方の文が無いのに日付がある・不明になっていない")
                            break
            else:
                res.not_run(13, "毎週の規則")
        # 17 休日の種類
        if _applies(17, s) and s.get("answerable"):
            res.ran(17)
            ir = (s.get("rule") or {}).get("ir")
            hp = next((e for e in excs if e["kind"] == "holiday_policy" and
                       expand.in_scope(e, s["category_id"], s["district_id"], s["period_id"])), None)
            if ir and hp:
                base = rules.occurrences(ir, p["valid_from"], p["valid_to"])
                for d in base:
                    h = holidays.holiday(d)
                    if h and not holidays.is_covered(d, hp.get("covers")) and d not in unk:
                        res.fail(17, s, f"覆われない休日 {d} ({h['name']}) が不明になっていない")
                        break
        # 18 印字と規則 (expand_series が待ち行列に入れたものが blocked_by にある)
        if _applies(18, s):
            ir = (s.get("rule") or {}).get("ir")
            if not ir:
                if s.get("answerable"):
                    res.not_run(18, "規則が無い (印字だけ)")
            else:
                res.ran(18)
                q = (s.get("extra") or {}).get("rule_vs_printed") or []
                missing = [x["item_id"] for x in q if f"disputed:{x['item_id']}" not in s.get("blocked_by", [])]
                if missing:
                    res.fail(18, s, f"印字と規則の違いが裁定に入っていない {missing[:2]}")
    # 7 同じ規則は同じ日付 (印字の系列で、規則の IR が同じもの)
    groups = collections.defaultdict(list)
    for s in scheds:
        ir = (s.get("rule") or {}).get("ir")
        if s.get("answerable") and s.get("dates_origin") in ("printed", "listed") and ir:
            groups[(s["category_id"], s["period_id"], json.dumps(rules.canon(ir), sort_keys=True))].append(s)
    for k, ss in groups.items():
        if len(ss) < 2:
            continue
        res.ran(7)
        base = set(ss[0]["dates"]) - {d.isoformat() for d in schema.unknown_days(ss[0])}
        for s in ss[1:]:
            mine = set(s["dates"]) - {d.isoformat() for d in schema.unknown_days(s)}
            if mine != base:
                res.fail(7, s, "同じ規則の地区と日付が違う")
    if not groups:
        res.not_run(7, "規則の IR を持つ印字の系列が無い")
    # 9 地区の重なり (名前と条件が同じ中身が、同じ区分けの 2 つの地区にある)。もれは町字の一覧 (ABR) が要る
    import members as mem
    seen = collections.defaultdict(set)
    for d in doc["districts"]:
        for m in d["members"]:
            if m.get("unit") in ("unparsed", "municipality"):
                continue
            seen[(d["partition_id"], mem.key(m))].add(d["district_id"])
    res.ran(9)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    for k, ds in dup.items():
        for s in scheds:
            if s["district_id"] in ds and s.get("answerable"):
                res.fail(9, s, f"同じ中身 {k[1][1]} が 2 つの地区 {sorted(ds)[:2]} にある")
    res.not_run(9, "もれ: 町字の一覧 (ABR) と address-normalize をまだつないでいない")
    # 10 島
    if ctx.get("islands"):
        res.ran(10)
        if all(m.get("unit") == "municipality" for d in doc["districts"] for m in d["members"]):
            for s in scheds:
                if s.get("answerable"):
                    res.fail(10, s, "有人島のある自治体を町全体の地区だけで答えている")
    else:
        res.not_run(10, "有人島の一覧に無い自治体")
    # 11 資料どうし
    dup = (doc.get("build") or {}).get("cross_source_duplicates") or {}
    res.checks[11]["cross_source"] = dup
    res.ran(11)
    # 15 但し書き (使われなかった但し書きの当たりうる系列が止まっているか、名指しの月が不明)
    for sid, sc in (ctx.get("sources") or {}).items():
        for q in sc.get("unconsumed", []):
            res.ran(15)
            for s in scheds:
                if s.get("answerable") and _same_source(s, sid) and _q_scope(q, s):
                    if q.get("months"):
                        months = {d.month for d in schema.unknown_days(s)}
                        if not set(q["months"]) <= months:
                            res.fail(15, s, f"但し書き {q['qualifier_id']} の月 {q['months']} が不明になっていない")
                    else:
                        res.fail(15, s, f"使われなかった但し書き {q['qualifier_id']} があるのに答えている")
    # 16 しるし
    for s in scheds:
        if s.get("answerable") and (s.get("extra") or {}).get("markup_unhandled"):
            res.fail(16, s, "決まりの無いしるしがあるのに答えている")
    res.ran(16)
    # 19 地区の資料のもれ
    nf = ctx.get("docs_not_fetched") or []
    res.checks[19]["docs_not_fetched"] = len(nf)
    res.ran(19)
    # 20 方針の文の場所 (方針の例外の span が出典の本文の範囲 (PDF は日程の表のページ) から取ったもの)
    for e in excs:
        if e["kind"] not in ("holiday_policy", "year_end", "cancelled", "fifth_week", "long_holiday"):
            continue
        res.ran(20)
        sp = e.get("span") or {}
        src = (ctx.get("sources") or {}).get(sp.get("source_id"))
        if not sp.get("source_id"):
            res.fail(20, None, f"例外 {e['exception_id']} に span の出典が無い")
        elif src is not None and src.get("kind") == "pdf" and sp.get("page") not in (src.get("table_pages") or [sp.get("page")]):
            res.fail(20, None, f"例外 {e['exception_id']} が日程の表のページの外の文")
    # 21 後で知らせる期間
    for sid, sc in (ctx.get("sources") or {}).items():
        for sent in sc.get("sentences") or []:
            ch = content.classify_change(sent.text)
            if not ch or ch["kind"] != "deferred":
                continue
            res.ran(21)
            mine = [e for e in excs if e["kind"] == "deferred" and (e.get("span") or {}).get("source_id") == sid
                    and content.squash(sent.text) in content.squash(e.get("text") or "")]
            if not mine:
                res.fail(21, None, f"deferred の文が例外になっていない: {sent.text[:40]}")
                continue
            for s in scheds:
                if not s.get("answerable") or not _same_source(s, sid):
                    continue
                for e in mine:
                    if not expand.in_scope(e, s["category_id"], s["district_id"], s["period_id"]):
                        continue
                    w = e.get("window") or {}
                    if not w.get("from"):
                        res.fail(21, s, f"日付の無い deferred ({e.get('period_name')}) なのに答えている")
                        continue
                    p = periods[s["period_id"]]
                    a, b = max(_d(w["from"]), _d(p["valid_from"])), min(_d(w["to"]), _d(p["valid_to"]))
                    if a <= b and not all(x in schema.unknown_days(s) for x in expand._days(a, b)):
                        res.fail(21, s, f"deferred の期間 {a}〜{b} が不明になっていない")
    return res


def _same_source(s, sid):
    return ((s.get("ref") or {}).get("source_id") or ((s.get("rule") or {}).get("span") or {}).get("source_id")) == sid


def _q_scope(q, s):
    sc = q.get("scope")
    if sc in (None, "*"):
        return True
    if isinstance(sc, dict):
        cs, ds = sc.get("category_ids"), sc.get("district_ids")
        return (cs in (None, "*") or s["category_id"] in cs) and (not ds or s["district_id"] in ds)
    return s["category_id"] in sc


def apply(doc, res, lg):
    """落ちた確かめ → 待ち行列の項目と、その系列の止め (disputed:<item_id>)。→ 足した項目のリスト"""
    by_key = {schema.series_key(s): s for s in doc["schedules"]}
    items = []
    for n, key, note in res.fails:
        iid = f"{lg}:validate:{n}:{'/'.join(key) if key else 'doc'}"
        items.append({"item_id": iid, "kind": KIND_OF.get(n, "policy"), "check": n, "note": note,
                      "series": [list(key)] if key else []})
        s = by_key.get(key) if key else None
        if s is not None:
            r = f"disputed:{iid}"
            if r not in s["blocked_by"]:
                s["blocked_by"].append(r)
            s["answerable"] = False
            s["dates"], s["unknown_ranges"] = [], []
            s["published"] = False
    return items


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import pathlib
    for f in (argv if argv is not None else sys.argv[1:]):
        doc = json.loads(pathlib.Path(f).read_text("utf-8"))
        r = run(doc)
        fails = sum(c["failed"] for c in r.checks.values())
        print(f"{doc['lg_code']} {doc.get('name')}: 落ちた確かめ {fails}")
        for n, c in r.summary().items():
            if c["ran"] or c["failed"] or c["not_run"]:
                print(f"  {n:>2} ({c['applies_to']}): 見た {c['ran']}・落ちた {c['failed']}・見られない {c['not_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
