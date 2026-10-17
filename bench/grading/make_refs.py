"""採点用の参照: 問いごとに、その地区・行政区の前後の収集日 (公式データ)、出す時刻、問い合わせ先、女川町の分け方 (有効期間つき) を書き出す
  python bench/grading/make_refs.py → out/cli_bench/grading/refs.json"""
import json, os, sys, datetime as dt
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import resolve, onagawa
OUT = os.path.join(ROOT, "out", "cli_bench", "grading"); os.makedirs(OUT, exist_ok=True)
k = resolve.Kanazawa(); o = onagawa.Onagawa()
kz = json.load(open("docs/mcp-bench-kanazawa.json", encoding="utf-8"))["cases"]
on = json.load(open("docs/mcp-bench-onagawa.json", encoding="utf-8"))["cases"]
refs = {}
def dates(days):
    return [f"{d['date']}({d['weekday']}): " + ("分からない(範囲外)" if not d["in_calendar_range"] else ("・".join(c["official_label"] + (f" [規則と違う日。通常は{c['rule_text']}]" if c.get("is_exception") else "") for c in d["collections"]) or "収集なし")) for d in days]
import re
for c in kz:
    m = re.search(r"(\d{4})年(\d+)月(\d+)日", c["question"]); d0 = dt.date(int(m[1]), int(m[2]), int(m[3]))
    dist = c["truth"]["district"]
    days = k.days(dist, d0 - dt.timedelta(days=21), 50)
    rules = sorted({f"{s['category']}: {s.get('rule_text') or s.get('rrule')}" for s in k.sched[dist]})
    refs[c["id"]] = {"question": c["question"], "truth_main": c["truth"], "district": dist,
                     "calendar_window_official": dates(days), "district_rules": rules,
                     "put_out_by": "収集日の午前8時30分まで (金沢市「ごみの出し方のルール」)", "contact": "金沢市 ごみ減量推進課 076-220-2302",
                     "holiday_policy": resolve.HOLIDAY_POLICY,
                     "note": "カレンダーの範囲は 2026-04-01〜2027-03-31。分別方法・集積所の場所はこの参照に無い (参照に無いことは unverifiable)"}
for c in on:
    m = re.search(r"(\d{4})年(\d+)月(\d+)日", c["question"]); d0 = dt.date(int(m[1]), int(m[2]), int(m[3]))
    place = re.search(r"女川町(\S+?) に住んで", c["question"])
    ref = {"question": c["question"], "truth_main": c["truth"]}
    if place:
        r = o.resolve_address("女川町" + place[1], str(d0), 1)
        gys = [x["collection_district"] for x in r.get("candidates", [])] or [r["location"]["collection_district"]]
        ref["gyoseiku_candidates"] = gys
        ref["calendar_window_official"] = {g: dates([o._day(g, d0 + dt.timedelta(days=i)) for i in range(-21, 35)]) for g in gys}
        ref["year_end"] = {g: o.year_end(g) for g in gys}
    ref["sorting_rules_official_with_validity"] = [{k2: x[k2] for k2 in ("item", "destination", "valid_from", "valid_to")} for x in o.d["sorting_rules"]]
    ref["put_out_by"] = o.d["put_out_by"]; ref["contact"] = o.d["contact"]
    ref["note"] = "女川町は 2026-10-01 に分け方を変えた (valid_from/valid_to)。問いの日付の時点で有効なルールが正しい。参照に無いことは unverifiable"
    refs[c["id"]] = ref
json.dump(refs, open(os.path.join(OUT, "refs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(refs), "refs")
