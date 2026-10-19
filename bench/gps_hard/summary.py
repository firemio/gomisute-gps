#!/usr/bin/env python3
"""緯度経度だけの難しい 20問の結果 (時間・費用・採点) → docs/gps-hard-results.json。あわせて、38問と合わせた 58問の平均を出す

  python bench/gps_hard/summary.py
"""
import collections, json, os, statistics as st, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
rd = lambda p: [json.loads(l) for l in open(os.path.join(ROOT, p), encoding="utf-8")]
gps = {m: rd(f"out/cli_bench_gps/{m}.jsonl") for m in ("mcp", "web")}
base = {m: rd(f"out/cli_bench/{m}.jsonl") for m in ("mcp", "web")}
scores = json.load(open(os.path.join(ROOT, "out", "cli_bench_gps", "grading", "scores.json"), encoding="utf-8"))
qs = {c["id"]: c for c in json.load(open(os.path.join(ROOT, "docs", "gps-hard-questions.json"), encoding="utf-8"))["cases"]}

def usage(rs):
    return {"n": len(rs), "errors": sum(bool(r["is_error"]) for r in rs), "wall_seconds_mean": round(st.mean(r["wall_seconds"] for r in rs), 1),
            "api_seconds_mean": round(st.mean(r["duration_api_ms"] for r in rs) / 1000, 1), "cost_usd_mean": round(st.mean(r["total_cost_usd"] for r in rs), 4),
            "turns_mean": round(st.mean(r["num_turns"] for r in rs), 1)}

def outcomes(mode, kind=None):
    s = [v[mode] for q, v in scores.items() if kind is None or qs[q]["kind"] == kind]
    c = collections.Counter(x["outcome"] for x in s)
    return {"n": len(s), "correct": c["correct"], "wrong": c["wrong"], "asked_back": c["asked_back"], "declined": c["declined"],
            "asked_back_with_truth_among_options": sum(x["truth_among_options"] for x in s if x["outcome"] == "asked_back"),
            "wrong_side_claims": sum(len(x["problems"]) for x in s)}

rep = {"experiment": "緯度経度だけの難しい 20問 (地区が分かれる町の住所の位置 10・地区の境目のそばで GPS の誤差 10〜25m・精度 30m と伝える 10) を、"
                     "claude -p (Opus) に gomisute-gps の MCP だけ / ウェブ検索だけで聞き、答えた方式を伏せて採点した (採点者2人 + 裁定)",
       "questions": "docs/gps-hard-questions.json", "run_date": "2026-10-18",
       "usage": {m: usage(gps[m]) for m in gps},
       "outcomes": {m: {"all": outcomes(m), "split_town": outcomes(m, "split_town"), "near_boundary": outcomes(m, "near_boundary")} for m in gps},
       "all_58_questions_usage": {m: usage(base[m] + gps[m]) for m in gps},
       "cases": {q: {"kind": qs[q]["kind"], "truth": qs[q]["truth"], "address": qs[q]["address"], **{m: {**scores[q][m], **{k: r[k] for r in gps[m] if r["id"] == q for k in ("wall_seconds", "total_cost_usd", "answer")}} for m in gps}} for q in sorted(scores)}}
json.dump(rep, open(os.path.join(ROOT, "docs", "gps-hard-results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: rep[k] for k in ("usage", "outcomes", "all_58_questions_usage")}, ensure_ascii=False, indent=1))
