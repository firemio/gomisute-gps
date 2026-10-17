#!/usr/bin/env python3
"""cli_bench.py の結果 (out/cli_bench/{mcp,web}.jsonl) と採点 (scores.json) をまとめる → docs/mcp-bench-cli.json

  python bench/cli_summary.py [scores.json]

scores.json: {"<問いID>": {"mcp": {...採点...}, "web": {...}}} (採点は答えの方式を隠したエージェントがつけたもの)
"""
import json, os, statistics as st, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
rows = {m: [json.loads(l) for l in open(os.path.join(ROOT, "out", "cli_bench", m + ".jsonl"), encoding="utf-8")] for m in ("mcp", "web")}
scores = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
NUM = ("wall_seconds", "duration_api_ms", "num_turns", "total_cost_usd", "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
       "main_model_input_total", "main_model_output", "helper_input_total", "helper_output", "web_search_requests_all")

def agg(rs):
    out = {"n": len(rs), "errors": sum(bool(r["is_error"]) for r in rs)}
    for k in NUM:
        v = [r[k] for r in rs if r.get(k) is not None]
        if v: out[k] = {"mean": round(st.mean(v), 4 if k == "total_cost_usd" else 1), "median": round(st.median(v), 4 if k == "total_cost_usd" else 1),
                        "sum": round(sum(v), 4 if k == "total_cost_usd" else 1)}
    v = [(r.get("input_tokens") or 0) + (r.get("cache_creation_input_tokens") or 0) + (r.get("cache_read_input_tokens") or 0) for r in rs]
    out["input_tokens_total_incl_cache"] = {"mean": round(st.mean(v)), "median": st.median(v)}
    return out

def score_agg(qids, mode):
    s = [scores[q][mode] for q in qids if q in scores and mode in scores[q]]
    if not s: return None
    keys = sorted({k for x in s for k, v in x.items() if isinstance(v, bool)})
    out = {"n": len(s), **{k: sum(bool(x.get(k)) for x in s) for k in keys}}
    for k in ("claims_checked", "claims_wrong", "claims_outdated", "claims_unverifiable"):
        if any(k in x for x in s): out[k] = sum(x.get(k, 0) for x in s)
    return out

summary = {}
for name, pred in (("kanazawa_28", lambda q: q.startswith("c")), ("onagawa_10", lambda q: not q.startswith("c")), ("all_38", lambda q: True)):
    summary[name] = {}
    for m in ("mcp", "web"):
        rs = [r for r in rows[m] if pred(r["id"])]
        summary[name][m] = {"usage": agg(rs), "score": score_agg([r["id"] for r in rs], m)}
rep = {"experiment": "同じ 38問 (金沢 28・女川 10) を、claude CLI (claude -p、Opus) に gomisute-gps の MCP だけ / ウェブ検索だけで聞いた。1問ずつ新しいセッション、追加の指示なし",
       "tool": "bench/cli_bench.py", "summary": summary,
       "cases": {q: {m: {**{k: v for k, v in r.items() if k not in ("mode",)}, "score": scores.get(q, {}).get(m)}
                     for m in ("mcp", "web") for r in rows[m] if r["id"] == q}
                 for q in [r["id"] for r in rows["mcp"]]}}
json.dump(rep, open(os.path.join(ROOT, "docs", "mcp-bench-cli.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for name, v in summary.items():
    for m in ("mcp", "web"):
        u = v[m]["usage"]
        print(f"{name:12s} {m:4s} n={u['n']} err={u['errors']} wall {u['wall_seconds']['mean']}s (med {u['wall_seconds']['median']}) api {u['duration_api_ms']['mean']}ms "
              f"turns {u['num_turns']['mean']} cost ${u['total_cost_usd']['mean']} (sum ${u['total_cost_usd']['sum']}) opus-in {u['main_model_input_total']['mean']} opus-out {u['main_model_output']['mean']} "
              f"helper-in {u['helper_input_total']['mean']} searches {u['web_search_requests_all']['mean']} score {v[m]['score']}")
