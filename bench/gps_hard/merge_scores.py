#!/usr/bin/env python3
"""採点ワークフローの結果 → out/cli_bench_gps/grading/scores.json と方式ごとの集計

  python bench/gps_hard/merge_scores.py <ワークフローの結果.json>
"""
import collections, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
G = os.path.join(ROOT, "out", "cli_bench_gps", "grading")
key = json.load(open(os.path.join(ROOT, "out", "cli_bench_gps", "grading_key", "key.json"), encoding="utf-8"))
res = json.load(open(sys.argv[1], encoding="utf-8"))
res = res.get("result", res) if isinstance(res, dict) else res
scores = {}
for b in res:
    j = [{g["answer_id"]: g for g in p["grades"]} if p else {} for p in b["judges"]]
    dec = {d["answer_id"]: d for d in (b.get("decisions") or [])}
    for aid in set(j[0]) | set(j[1]):
        gs = [g for g in (j[0].get(aid), j[1].get(aid)) if g]
        d = dec.get(aid)
        base = d if d else gs[0]
        k = key[aid]
        scores.setdefault(k["qid"], {})[k["mode"]] = {
            "answer_id": aid, "outcome": base["outcome"], "truth_among_options": base["truth_among_options"],
            "district_named": base["district_named"], "problems": (d or {}).get("confirmed_problems", []),
            "graded": "adjudicated" if d else "agreed", "judges_agreed": len(gs) == 2 and gs[0]["outcome"] == gs[1]["outcome"]}
json.dump(scores, open(os.path.join(G, "scores.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for mode in ("mcp", "web"):
    for kind, pred in (("split_town", lambda q: int(q[1:]) <= 10), ("near_boundary", lambda q: int(q[1:]) > 10), ("all", lambda q: True)):
        s = [v[mode] for q, v in scores.items() if pred(q) and mode in v]
        c = collections.Counter(x["outcome"] for x in s)
        print(mode, kind, len(s), dict(c), "truth among options when asking back:", sum(x["truth_among_options"] for x in s if x["outcome"] == "asked_back"),
              "| wrong side claims", sum(len(x["problems"]) for x in s))
