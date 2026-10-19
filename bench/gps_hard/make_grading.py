#!/usr/bin/env python3
"""緯度経度だけの難しい 20問の採点の入力: 正解 (住所を区域表に当てた地区)・その日のほかの候補の地区の品目・前後の公式の収集日と、
方式を伏せた答え (A01…) を組にする

  python bench/gps_hard/make_grading.py → out/cli_bench_gps/grading/batch_{1,2}.json、鍵は out/cli_bench_gps/grading_key/key.json
"""
import datetime as dt, json, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT); sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import resolve
k = resolve.Kanazawa()
OUT = os.path.join(ROOT, "out", "cli_bench_gps", "grading"); os.makedirs(OUT, exist_ok=True)
KEY = os.path.join(ROOT, "out", "cli_bench_gps", "grading_key"); os.makedirs(KEY, exist_ok=True)

def dates(district, d0, n):
    out = []
    for x in k.days(district, d0, n):
        out.append(f"{x['date']}({x['weekday']}): " + ("分からない(範囲外)" if not x["in_calendar_range"] else
                   ("・".join(c["official_label"] for c in x["collections"]) or "収集なし")))
    return out

cases = json.load(open("docs/gps-hard-questions.json", encoding="utf-8"))["cases"]
refs = {}
for c in cases:
    d = dt.date.fromisoformat(c["date"])
    refs[c["id"]] = {
        "question": c["question"],
        "true_location": f"金沢市{c['address']} (国土地理院の住居表示住所" + (f"。問いの緯度経度は、ここから {c['jitter_m']}m ずらした GPS の位置)" if c["jitter_m"] else "の位置そのもの)"),
        "truth_main": c["truth"],
        "other_candidate_districts_on_that_date": {o: (k.days(o, d, 1)[0]["collections"] and [x["official_label"] for x in k.days(o, d, 1)[0]["collections"]]) or [] for o in c["other_districts"]},
        "calendar_window_official": {dd: dates(dd, d - dt.timedelta(days=14), 35) for dd in [c["truth"]["district"]] + c["other_districts"]},
        "put_out_by": "収集日の午前8時30分まで (金沢市「ごみの出し方のルール」)", "contact": "金沢市 ごみ減量推進課 076-220-2302",
        "note": "正しい地区は、利用者の本当の場所 (true_location) の住所を市の区域表の行に当てたもの。問いの緯度経度だけからは、近くの別の地区 (other_candidate_districts) とも読める"}
items = []
for mode in ("mcp", "web"):
    for c in cases:
        p = os.path.join(ROOT, "out", "cli_bench_gps", mode, c["id"] + ".json")
        if os.path.exists(p): items.append({"qid": c["id"], "mode": mode, "answer": json.load(open(p, encoding="utf-8")).get("result") or ""})
rnd = random.Random(11)
rnd.shuffle(items)
key, batches = {}, {"1": [], "2": []}
for i, it in enumerate(items):
    aid = f"A{i + 1:02d}"
    key[aid] = {"qid": it["qid"], "mode": it["mode"]}
    batches["1" if int(it["qid"][1:]) <= 10 else "2"].append({"answer_id": aid, "question_id": it["qid"], "answer": it["answer"]})
for b, es in batches.items():
    qids = sorted({e["question_id"] for e in es})
    json.dump({"references": {q: refs[q] for q in qids}, "answers": sorted(es, key=lambda e: (e["question_id"], e["answer_id"]))},
              open(os.path.join(OUT, f"batch_{b}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("batch", b, len(es))
json.dump(refs, open(os.path.join(OUT, "refs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(key, open(os.path.join(KEY, "key.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
