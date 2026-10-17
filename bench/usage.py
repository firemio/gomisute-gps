#!/usr/bin/env python3
"""ワークフローの各エージェントの記録 (agent-*.jsonl) から、問いごとの時間・トークン・道具の呼び出し回数を出す。
final_context_tokens は最後の応答を作ったときの文脈の大きさ、input_tokens_all_turns は各ターンで読んだ入力 (キャッシュ読み込みを含む) の合計

  python bench/usage.py <workflow の transcript ディレクトリ> > usage.json
"""
import glob, json, os, sys, datetime as dt
d = sys.argv[1]
out = {}
for f in glob.glob(os.path.join(d, "agent-*.jsonl")):
    meta = os.path.splitext(f)[0] + ".meta.json"
    mj = json.load(open(meta, encoding="utf-8")) if os.path.exists(meta) else {}
    label = mj.get("label") or mj.get("description")
    ts, tools, inp, outp, turns, last_ctx = [], 0, 0, 0, 0, 0
    for line in open(f, encoding="utf-8"):
        e = json.loads(line)
        if e.get("timestamp"): ts.append(dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")))
        m = e.get("message") or {}
        if e.get("type") == "assistant" and isinstance(m, dict):
            u = m.get("usage") or {}
            i = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            if u: turns += 1; last_ctx = i
            inp += i; outp += u.get("output_tokens", 0)
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") not in ("StructuredOutput",): tools += 1
    if ts: out[label or os.path.basename(f)] = {"seconds": round((max(ts) - min(ts)).total_seconds(), 1), "model_turns": turns, "tool_calls": tools,
                                                "final_context_tokens": last_ctx, "input_tokens_all_turns": inp, "output_tokens": outp}
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
