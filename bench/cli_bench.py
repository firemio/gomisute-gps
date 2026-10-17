#!/usr/bin/env python3
"""実際の MCP で測る: 同じ質問を claude CLI (claude -p) に、gomisute-gps の MCP だけ / ウェブ検索だけ で聞き、時間・トークン・費用を記録する

  python bench/cli_bench.py mcp [--jobs 1] [--only c01,O2]
  python bench/cli_bench.py web [--jobs 4]
  → out/cli_bench/<mode>/<id>.json (claude -p --output-format json の出力そのもの) と out/cli_bench/<mode>.jsonl (1問1行の集計)

質問は docs/mcp-bench-kanazawa.json (28問) と docs/mcp-bench-onagawa.json (10問)。どちらも Opus、追加の指示 (システムプロンプト) なし、1問ずつ新しいセッション。
- mcp: 組み込みの道具をすべて外し (--tools "")、MCP サーバー gomisute (mcp_server.py) の3つのツールだけを許す。サーバーは問いごとに起動する (データの読み込み 約5秒を含む)
- web: MCP サーバーなし、WebSearch と WebFetch だけを許す
claude CLI にログイン済みであること。Claude Code の中から動かすときは、親の環境変数 (CLAUDECODE など) を外して起動する。
"""
import argparse, concurrent.futures as cf, json, os, shutil, subprocess, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "cli_bench")
DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_ENTRYPOINT")
MCP_TOOLS = ["mcp__gomisute__garbage_collection_by_address", "mcp__gomisute__garbage_collection_by_location", "mcp__gomisute__garbage_sorting"]

def questions():
    qs = []
    for f in ("mcp-bench-kanazawa.json", "mcp-bench-onagawa.json"):
        for c in json.load(open(os.path.join(ROOT, "docs", f), encoding="utf-8"))["cases"]:
            qs.append({"id": c["id"], "question": c["question"]})
    return qs

HOME = os.path.expanduser("~")

def redact(o):
    """記録からホームフォルダのパス (ユーザー名を含む) を伏せる。ウェブだけの答えが、CLI が PDF を保存した場所を書くことがある"""
    if isinstance(o, str):
        for h in (HOME, HOME.replace("\\", "/"), HOME.replace(":\\", "--").replace("\\", "-")): o = o.replace(h, "~")   # C--Users-<名前> (Claude のプロジェクトのフォルダ名) も
        return o
    if isinstance(o, dict): return {k: redact(v) for k, v in o.items()}
    if isinstance(o, list): return [redact(v) for v in o]
    return o

def run(mode, q, workdir, cfg):
    path = os.path.join(OUT, mode, q["id"] + ".json")
    if os.path.exists(path):
        try:
            d = json.load(open(path, encoding="utf-8"))
            if not d.get("is_error") and d.get("result"): return q["id"], "cached"
        except json.JSONDecodeError: pass
    cmd = ["claude", "-p", q["question"], "--model", "opus", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config"]
    if mode == "mcp": cmd += ["--mcp-config", cfg, "--tools", "", "--allowedTools", *MCP_TOOLS]
    else: cmd += ["--mcp-config", '{"mcpServers":{}}', "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch"]
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    t0 = time.time()
    cmd[0] = shutil.which("claude") or "claude"
    p = subprocess.run(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=1800, encoding="utf-8", errors="replace")
    wall = round(time.time() - t0, 1)
    try: d = json.loads(p.stdout)
    except json.JSONDecodeError: d = {"is_error": True, "result": None, "stdout": p.stdout[-2000:]}
    d["wall_seconds"], d["stderr"], d["question"] = wall, p.stderr[-2000:], q["question"]
    json.dump(redact(d), open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return q["id"], "error" if d.get("is_error") else "ok"

def row(mode, qid):
    d = json.load(open(os.path.join(OUT, mode, qid + ".json"), encoding="utf-8"))
    u = d.get("usage") or {}
    stu = u.get("server_tool_use") or {}
    return {"id": qid, "mode": mode, "is_error": d.get("is_error"), "wall_seconds": d.get("wall_seconds"), "duration_ms": d.get("duration_ms"),
            "duration_api_ms": d.get("duration_api_ms"), "num_turns": d.get("num_turns"), "total_cost_usd": d.get("total_cost_usd"),
            "input_tokens": u.get("input_tokens"), "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": u.get("cache_read_input_tokens"), "output_tokens": u.get("output_tokens"),
            "web_search_requests": stu.get("web_search_requests"), "web_fetch_requests": stu.get("web_fetch_requests"),
            "permission_denials": len(d.get("permission_denials") or []),
            **_by_model(d.get("modelUsage") or {}), "answer": d.get("result")}

def _by_model(mu):
    """モデル別の入力 (キャッシュを含む)。ウェブだけのときは WebFetch がページを小さいモデル (Haiku) で要約してから渡す"""
    tot = lambda m: (m.get("inputTokens") or 0) + (m.get("cacheReadInputTokens") or 0) + (m.get("cacheCreationInputTokens") or 0)
    main = {k: v for k, v in mu.items() if "opus" in k}
    helper = {k: v for k, v in mu.items() if "opus" not in k}
    return {"main_model_input_total": sum(tot(v) for v in main.values()), "main_model_output": sum(v.get("outputTokens") or 0 for v in main.values()),
            "helper_models": sorted(helper), "helper_input_total": sum(tot(v) for v in helper.values()),
            "helper_output": sum(v.get("outputTokens") or 0 for v in helper.values()), "web_search_requests_all": sum(v.get("webSearchRequests") or 0 for v in mu.values())}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["mcp", "web"])
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--rows-only", action="store_true", help="測り直さず、記録から 1問1行の集計だけ作り直す")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(os.path.join(OUT, a.mode), exist_ok=True)
    qs = [q for q in questions() if not a.only or q["id"] in a.only.split(",")]
    workdir = tempfile.mkdtemp(prefix=f"gomisute-bench-{a.mode}-")         # 空のフォルダで動かす (リポジトリのファイルを読ませない)
    cfg = os.path.join(workdir, "..", f"gomisute-bench-{a.mode}-mcp.json")
    json.dump({"mcpServers": {"gomisute": {"command": sys.executable, "args": [os.path.join(ROOT, "mcp_server.py")]}}}, open(cfg, "w", encoding="utf-8"))
    if not a.rows_only:
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            for fut in cf.as_completed([ex.submit(run, a.mode, q, workdir, cfg) for q in qs]):
                print(*fut.result(), flush=True)
    rows = [row(a.mode, q["id"]) for q in questions() if os.path.exists(os.path.join(OUT, a.mode, q["id"] + ".json"))]
    with open(os.path.join(OUT, a.mode + ".jsonl"), "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("done", len(rows), "errors", sum(bool(r["is_error"]) for r in rows))
