#!/usr/bin/env python3
"""回答の採点と、問いごとの時間・トークンの集計

  python bench/score.py <answers.json> <truth: docs/llm-baseline.json など> [usage.json ...]
"""
import json, re, sys, statistics as st
sys.stdout.reconfigure(encoding="utf-8")
LBL = {"燃やすごみ": "burnable", "燃やさないごみ": "nonburnable", "びん": "bottle",
       "資源 あき缶・ペットボトル プラスチック資源など": "recyclable", "資源 プラスチック資源": "plastic"}

def cats(items):
    out = set()
    for s in items:
        if re.search("燃やさない|不燃|埋立|金属ごみ", s): out.add("nonburnable")
        elif re.search("燃やす|可燃", s): out.add("burnable")
        if re.search("びん|ビン|瓶", s): out.add("bottle")
        if re.search("缶|ペット", s) and not re.search("スプレー|カセット", s): out.add("recyclable")
        elif re.search("プラ", s): out.add("plastic")
    return out

def score(answers, truth_cases):
    cases = {c["id"]: c for c in truth_cases}
    rows = []
    for a in answers:
        t = cases[a["id"]]["truth"]; tc = {LBL[x] for x in t["collections"]}; ac = cats(a.get("items", []))
        if "recyclable" in tc and "recyclable" in ac: ac.discard("plastic")
        rows.append({"id": a["id"], "items_correct": ac == tc and a.get("no_collection") == (not tc),
                     "district_correct": bool(a.get("district")) and (t["district"] in a["district"] or a["district"] in t["district"]),
                     "confidence": a.get("confidence"), "hedged": a.get("hedged")})
    return rows

def usage_summary(path):
    d = {k: v for k, v in json.load(open(path, encoding="utf-8")).items() if k.startswith("ask:")}
    keys = ("seconds", "model_turns", "tool_calls", "final_context_tokens", "input_tokens_all_turns", "output_tokens")
    return {"n": len(d), **{k: {"mean": round(st.mean(v[k] for v in d.values()), 1), "median": st.median(v[k] for v in d.values())} for k in keys}}

if __name__ == "__main__":
    ans = json.load(open(sys.argv[1], encoding="utf-8"))
    ans = ans.get("result", ans) if isinstance(ans, dict) else ans
    rows = score(ans, json.load(open(sys.argv[2], encoding="utf-8"))["cases"])
    print("items", sum(r["items_correct"] for r in rows), "/", len(rows), " district", sum(r["district_correct"] for r in rows), "/", len(rows))
    for r in rows:
        if not (r["items_correct"] and r["district_correct"]): print("  MISS", r)
    for p in sys.argv[3:]: print(p.split("\\")[-1].split("/")[-1], json.dumps(usage_summary(p), ensure_ascii=False))
