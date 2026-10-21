"""2 つのモデルに同じ問いかけを渡して答えを記録する → data/llmread/answers/<モデル>/<doc_id>.json

  DeepSeek: llm.py (deepseek-flash、JSON モード、画像は image_part)。キーは環境変数 GOMISUTE_LLM_API_KEY
  Opus:     Claude Code の CLI (claude -p --model claude-opus-5-5 --output-format json)。道具は Read だけ。
            CLAUDECODE などの親の環境変数は bench/cli_bench.py と同じように外す。リポジトリの外の空のフォルダで動かす
            (CLAUDE.md・記憶を読ませない)。画像はそのフォルダに写し、Read で開かせる

記録: 使ったトークン・費用・秒・時刻 (UTC)。同じ問いかけ (prompt_sha256) と同じ入力 (input_sha256) の答えがあれば聞き直さない。
"""
import json, os, re, shutil, subprocess, tempfile, time

from common import ANSWERS, bench_cli, now_utc, read_json, write_json
import prompt

DEEPSEEK_MODEL = "deepseek-flash"
OPUS_MODEL = "claude-opus-5-5"
# DeepSeek の料金 (100 万トークンあたり USD。docs/nationwide-plan.md §6 と、この仕事の指示の値)。混まない時間は半額
DS_PRICE = {"input_miss": 0.30, "input_hit": 0.006, "output": 1.20}
# 混まない時間 (UTC 16:30〜00:30。DeepSeek の料金表の割引の時間)。料金表 (https://api-docs.deepseek.com/quick_start/pricing) で確かめること
DS_OFFPEAK_UTC = ((16, 30), (0, 30))


def model_tag(model):
    return {"deepseek": DEEPSEEK_MODEL, "opus": OPUS_MODEL}.get(model, model)


# 答えの記録を読む場所。ふだんは data/llmread/answers/。full.py score は out/llmread/raw/ (git に入れた生の答え) に切り替える
ANSWERS_DIR = ANSWERS


def answer_path(model, doc_id):
    return ANSWERS_DIR / model_tag(model) / f"{doc_id}.json"


def load_answer(model, doc_id):
    p = answer_path(model, doc_id)
    return read_json(p) if p.exists() else None


def _fresh(rec, m, pr):
    return rec and not rec.get("error") and rec.get("prompt_sha256") == pr["prompt_sha256"] \
        and rec.get("input_sha256") == m["input_sha256"]


def is_offpeak(t):
    (h1, m1), (h2, m2) = DS_OFFPEAK_UTC
    x = t.hour * 60 + t.minute
    a, b = h1 * 60 + m1, h2 * 60 + m2
    return (a <= x or x < b) if a > b else (a <= x < b)


def deepseek_cost(usage, started):
    """usage (prompt_cache_hit_tokens・prompt_cache_miss_tokens・completion_tokens) と始めた時刻 → 費用の記録"""
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = (usage.get("prompt_tokens") or 0) - hit
    out = usage.get("completion_tokens") or 0
    peak = (miss * DS_PRICE["input_miss"] + hit * DS_PRICE["input_hit"] + out * DS_PRICE["output"]) / 1e6
    off = is_offpeak(started)
    return {"usd": round(peak / 2 if off else peak, 6), "usd_peak": round(peak, 6), "offpeak": off,
            "price_per_mtok": DS_PRICE, "offpeak_utc": "16:30-00:30 (料金表で要確認)"}


def ask_deepseek(m, force=False, max_tokens=32000):
    import llm
    pr = prompt.build(m)
    p = answer_path("deepseek", m["doc_id"])
    old = read_json(p) if p.exists() else None
    if _fresh(old, m, pr) and not force:
        return old
    parts = [{"type": "text", "text": pr["text"]}]
    for name, path in pr["images"]:
        parts += [{"type": "text", "text": f"画像: {name}"}, llm.image_part(path.read_bytes())]
    started = now_utc()
    t0 = time.time()
    rec = {"doc_id": m["doc_id"], "model": DEEPSEEK_MODEL, "prompt_version": prompt.PROMPT_VERSION,
           "prompt_sha256": pr["prompt_sha256"], "input_sha256": m["input_sha256"], "started_at": started.isoformat(timespec="seconds"),
           "images": len(pr["images"])}
    raw = {}
    try:
        ans, usage = llm.ask_json(pr["system"], parts, model=DEEPSEEK_MODEL, max_tokens=max_tokens, raw=raw)
        rec.update(answer=ans, usage=usage, error=None)
    except Exception as e:                                       # 記録して止めない (キーは例外の文に入らない)
        rec.update(answer=None, usage=None, error=f"{type(e).__name__}: {str(e)[:500]}")
    # 費用は呼び出しのすべて (JSON として読めずに聞き直した分も) の合計
    att = raw.get("attempts") or []
    costs = [deepseek_cost(a["usage"], started) for a in att if a.get("usage")]
    if costs:
        rec["cost"] = {**costs[-1], "usd": round(sum(c["usd"] for c in costs), 6), "usd_peak": round(sum(c["usd_peak"] for c in costs), 6),
                       "calls": len(costs)}
    else:
        rec["cost"] = None
    rec["raw"] = {"attempts": att, "note": "API の生の答え (content は JSON の文字、reasoning_content は推論の文字)。最後のものを answer に読んだ"}
    rec["seconds"] = round(time.time() - t0, 1)
    write_json(p, scrub(rec))
    return rec


def scrub(o):
    """記録から API キーの文字を伏せる (念のため。例外の文などに混ざらないように)"""
    k = os.environ.get("GOMISUTE_LLM_API_KEY")
    if not k or len(k) < 8:
        return o
    s = json.dumps(o, ensure_ascii=False)
    return json.loads(s.replace(k, "***")) if k in s else o


def extract_json(text):
    """答えの文字 → JSON (前後の説明・``` を外す)。読めなければ None"""
    if not text:
        return None
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    for cand in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t else ""):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def ask_opus(m, force=False, timeout=1800):
    cb = bench_cli()
    pr = prompt.build(m)
    p = answer_path("opus", m["doc_id"])
    old = read_json(p) if p.exists() else None
    if _fresh(old, m, pr) and not force:
        return old
    work = tempfile.mkdtemp(prefix="llmread-opus-")              # リポジトリの外の空のフォルダ
    try:
        text = pr["text"]
        if pr["images"]:
            lines = []
            for name, path in pr["images"]:
                dst = os.path.join(work, name)
                shutil.copyfile(path, dst)
                lines.append(f"- {name}: {dst}")
            text += "\n画像のファイル (答える前に Read ですべて開いて見ること):\n" + "\n".join(lines)
        sysf = os.path.join(work, "system.txt")
        with open(sysf, "w", encoding="utf-8") as f:
            f.write(pr["system"])
        cmd = [shutil.which("claude") or "claude", "-p", "--model", OPUS_MODEL, "--output-format", "json",
               "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--tools", "Read", "--allowedTools", "Read", "--system-prompt-file", sysf]
        env = {k: v for k, v in os.environ.items() if k not in cb.DROP_ENV and k != "GOMISUTE_LLM_API_KEY"}
        started = now_utc()
        t0 = time.time()
        pr_ = subprocess.run(cmd, cwd=work, env=env, input=text, capture_output=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
        wall = round(time.time() - t0, 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        d = json.loads(pr_.stdout)
    except json.JSONDecodeError:
        d = {"is_error": True, "result": None, "stdout": pr_.stdout[-2000:]}
    ans = extract_json(d.get("result"))
    rec = {"doc_id": m["doc_id"], "model": OPUS_MODEL, "prompt_version": prompt.PROMPT_VERSION,
           "prompt_sha256": pr["prompt_sha256"], "input_sha256": m["input_sha256"], "started_at": started.isoformat(timespec="seconds"),
           "images": len(pr["images"]), "seconds": wall, "answer": ans,
           "usage": d.get("usage"), "model_usage": d.get("modelUsage"), "num_turns": d.get("num_turns"),
           "duration_ms": d.get("duration_ms"), "duration_api_ms": d.get("duration_api_ms"),
           "permission_denials": d.get("permission_denials"),
           "cost": {"usd": d.get("total_cost_usd"), "note": "claude -p の total_cost_usd (CLI が API の料金で数えた値)"},
           "raw_result": d.get("result"), "stop_reason": d.get("stop_reason"), "error": None}
    if d.get("is_error") or ans is None:
        rec["error"] = "CLI がエラーを返した" if d.get("is_error") else "答えが JSON として読めない"
        rec["stderr"] = (pr_.stderr or "")[-2000:]
        rec["stdout_tail"] = d.get("stdout")
    write_json(p, cb.redact(rec))
    return rec


def ask(model, m, force=False):
    return ask_deepseek(m, force) if model == "deepseek" else ask_opus(m, force)


def tokens(rec):
    """記録 → {"input", "output", "cache_read", "cache_write"} (モデルごとの名前をそろえる)"""
    u = rec.get("usage") or {}
    if rec.get("model") == DEEPSEEK_MODEL:
        return {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"), "cache_read": u.get("prompt_cache_hit_tokens"),
                "reasoning": ((u.get("completion_tokens_details") or {}).get("reasoning_tokens"))}
    return {"input": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
            "output": u.get("output_tokens"), "cache_read": u.get("cache_read_input_tokens"),
            "cache_write": u.get("cache_creation_input_tokens")}
