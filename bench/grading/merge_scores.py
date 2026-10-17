"""採点ワークフローの結果 (results.json) と、方式を隠した ID の鍵 → scores.json ({問いID: {mcp: 採点, web: 採点}})"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "out", "cli_bench", "grading")
key = json.load(open(os.path.join(ROOT, "out", "cli_bench", "grading_key", "key.json"), encoding="utf-8"))
res = json.load(open(sys.argv[1], encoding="utf-8"))
res = res.get("result", res) if isinstance(res, dict) else res
scores = {}
for b in res:
    j = [{g["answer_id"]: g for g in p["grades"]} if p else {} for p in b["judges"]]
    dec = {d["answer_id"]: d for d in (b.get("decisions") or [])}
    ids = set(j[0]) | set(j[1])
    for aid in ids:
        g1, g2 = j[0].get(aid), j[1].get(aid)
        gs = [g for g in (g1, g2) if g]
        d = dec.get(aid)
        if d:
            main, dist, probs, how = d["main_correct"], d["district_status"], d["confirmed_problems"], "adjudicated"
            rejected = d["rejected_problems"]
        else:
            main, dist, probs, how, rejected = gs[0]["main_correct"], gs[0]["district_status"], [], "agreed", []
        k = key[aid]
        scores.setdefault(k["qid"], {})[k["mode"]] = {
            "answer_id": aid, "main_correct": main, "district_correct": dist == "correct", "district_status": dist,
            "definitive": all(g["definitive"] for g in gs), "asked_back": any(g["asked_back"] for g in gs),
            "claims_checked": round(sum(g["claims_checked"] for g in gs) / len(gs)),
            "claims_unverifiable": round(sum(g["claims_unverifiable"] for g in gs) / len(gs)),
            "claims_wrong": sum(p["verdict"] == "wrong" for p in probs), "claims_outdated": sum(p["verdict"] == "outdated" for p in probs),
            "problems": probs, "rejected_problems": rejected, "graded": how,
            "judges_agreed_on_main": len(gs) == 2 and gs[0]["main_correct"] == gs[1]["main_correct"]}
json.dump(scores, open(os.path.join(HERE, "scores.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for mode in ("mcp", "web"):
    for name, pred in (("kanazawa", lambda q: q.startswith("c")), ("onagawa", lambda q: not q.startswith("c"))):
        s = [v[mode] for q, v in scores.items() if pred(q) and mode in v]
        print(mode, name, "n", len(s), "main", sum(x["main_correct"] for x in s), "district", sum(x["district_correct"] for x in s),
              "claims", sum(x["claims_checked"] for x in s), "wrong", sum(x["claims_wrong"] for x in s), "outdated", sum(x["claims_outdated"] for x in s),
              "asked_back", sum(x["asked_back"] for x in s))
        for q, v in sorted(scores.items()):
            if pred(q) and mode in v and (not v[mode]["main_correct"] or v[mode]["problems"]):
                print("   ", q, "main", v[mode]["main_correct"], [(p["verdict"], p["quote"][:40]) for p in v[mode]["problems"]])
