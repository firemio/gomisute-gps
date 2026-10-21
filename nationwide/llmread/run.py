#!/usr/bin/env python3
"""LLM が資料を読む (nationwide/llmread/) の入口

作者 firemio の決定 (2026-10-21): 日本語の文の読み取り・解釈は LLM がする (Python の決まりの直しを 8 回重ねても穴が残った)。
Python がするのは、資料の読み込み・PDF のページの画像化・モデルの呼び出し・JSON の形の確かめ・LLM が書いた規則からの日付の計算
(祝日・第N曜日・休み)・読みどうしの比べ・正解との採点だけ。
決め方: 同じ入力で Opus と DeepSeek を比べ、どちらも正しければ DeepSeek を使う (安い)。

  python nationwide/llmread/run.py plan                                   # out/llmread/plan.json (資料の一覧)
  python nationwide/llmread/run.py prep jgd:112275 onagawa:2026-04        # 入力を作る (data/llmread/inputs/)
  python nationwide/llmread/run.py ask --models deepseek,opus jgd:112275  # 聞く (data/llmread/answers/)。同じ入力と問いかけなら聞き直さない
  python nationwide/llmread/run.py score jgd:112275 kanazawa:三和          # 採点 (答えのあるモデルだけ)
  python nationwide/llmread/run.py smoke [--models deepseek,opus]         # 試走 (朝霞・女川 2 か月) → out/llmread/smoke.json と見積もり

DeepSeek のキーは環境変数 GOMISUTE_LLM_API_KEY。Opus は claude CLI にログイン済みであること。
"""
import argparse, calendar, collections, sys

from common import OUT, read_json, say, write_json
import calc, plan as planmod, prep, prompt, runners, score

MODELS = ("deepseek", "opus")


def load_plan():
    p = OUT / "plan.json"
    return read_json(p) if p.exists() else planmod.make()


def docs_for(pl, specs):
    out = []
    for s in specs:
        got = planmod.select(pl, s)
        if not got:
            raise SystemExit(f"計画に無い (またはキャッシュに無い): {s}")
        out += got
    return out


def doc_metrics(rec, m):
    """答えの記録 1 件 → 費用・トークン・秒・形の確かめ"""
    if rec is None:
        return None
    ans = rec.get("answer")
    errs = calc.validate(ans) if ans is not None else ["答えが無い"]
    return {"cost_usd": (rec.get("cost") or {}).get("usd"), "cost_usd_peak": (rec.get("cost") or {}).get("usd_peak"),
            "offpeak": (rec.get("cost") or {}).get("offpeak"), "tokens": runners.tokens(rec), "seconds": rec.get("seconds"),
            "started_at": rec.get("started_at"), "num_turns": rec.get("num_turns"), "error": rec.get("error"),
            "schema_errors": errs[:10], "schema_ok": not errs, "images": rec.get("images"), "text_chars": m["text_chars"],
            "districts": len(ans["districts"]) if not errs else None, "categories": len(ans["categories"]) if not errs else None,
            "schedules": len(ans["schedules"]) if not errs else None, "unsure": (ans.get("unsure") if not errs else None)}


def score_group(target, docs, model):
    """同じ正解の単位 (jgd は自治体、女川は月、金沢は地区) の答えをまとめて採点"""
    items = []
    for d in docs:
        rec = runners.load_answer(model, d["doc_id"])
        ans = rec.get("answer") if rec else None
        if ans is None or calc.validate(ans):
            items.append((d, None, None))
            continue
        win = (str(score.jgd.FY_FROM), str(score.jgd.FY_TO)) if target == "jgd" else \
            (f"{d['month']}-01", f"{d['month']}-{calendar.monthrange(*map(int, d['month'].split('-')))[1]}") if target == "onagawa" else ("2026-04-01", "2027-03-31")
        items.append((d, ans, calc.expand_answer(ans, win)))
    ok = [x for x in items if x[1] is not None]
    if not ok:
        return {"error": "形の正しい答えが無い"}
    d0, ans0, ex0 = ok[0]
    if target == "jgd":
        return score.score_jgd(d0["lg_code"], ok)
    if target == "onagawa":
        return score.score_onagawa(d0["month"], ans0, ex0)
    return score.score_kanazawa(d0["district"], ans0, ex0)


def groups(docs):
    g = collections.OrderedDict()
    for d in docs:
        key = (d["target"], d.get("lg_code") or d.get("month") or d.get("district"))
        if d["target"] == "onagawa":
            key = ("onagawa", d["month"])
        elif d["target"] == "kanazawa":
            key = ("kanazawa", d["district"])
        g.setdefault(key, []).append(d)
    return g


def compare_models(res):
    """2 つのモデルの採点を並べる: どちらも完全に正しい単位の数 (決め方: どちらも正しければ DeepSeek)"""
    out = {}
    for m, r in res.items():
        s = (r or {}).get("summary") or {}
        out[m] = {k: s.get(k) for k in ("units", "exact", "wrong_dates", "missing_dates", "answered", "answered_exact", "answered_wrong")}
    return out


def estimate(pl, smoke_rows):
    """試走の費用 → 本番の見積もり。費用 = a × (指示 + 資料の文字数) + b × ページ数 を、モデルごとに
    文字だけの資料から a、PDF から b を決めて当てる (出力の費用も含めた粗い比例。確かさ低)"""
    instr = len(prompt.SYSTEM) + len(prompt.INSTRUCTIONS)
    est = {"method": "費用 = a × (指示 + 資料の文字数) + b × PDF のページ数。a は文字だけの資料、b は PDF の資料の試走から (出力の費用を含めた比例)",
           "instruction_chars": instr, "by_model": {}}
    for model in MODELS:
        rows = [r for r in smoke_rows if r["model"] == model and r["metrics"] and r["metrics"]["cost_usd"] is not None]
        tx = [r for r in rows if not r["metrics"]["images"]]
        px = [r for r in rows if r["metrics"]["images"]]
        if not tx or not px:
            est["by_model"][model] = {"error": "文字だけの資料と PDF の両方の試走が要る"}
            continue
        key = "cost_usd_peak" if model == "deepseek" else "cost_usd"
        a = sum(r["metrics"][key] for r in tx) / sum(instr + r["metrics"]["text_chars"] for r in tx)
        b = sum((r["metrics"][key] - a * (instr + r["metrics"]["text_chars"])) / r["metrics"]["images"] for r in px) / len(px)
        b = max(b, 0.0)
        tot = collections.Counter()
        per = []
        for t in pl["targets"]:
            for d in t["docs"]:
                if not d.get("cached"):
                    continue
                pages = (len(d["pages"]) if d.get("pages") else d.get("pages_total") or 0) if d.get("kind") == "pdf" else 0
                c = a * (instr + (d.get("text_chars") or 0)) + b * pages
                tot[t["target"]] += c
                tot["calls_" + t["target"]] += 1
                per.append((round(c, 4), d["doc_id"]))
        est["by_model"][model] = {"a_usd_per_char": a, "b_usd_per_page": round(b, 5), "price_basis": key,
                                  "jgd_tier_a_usd": round(tot["jgd"], 2), "onagawa_12_months_usd": round(tot["onagawa"], 2),
                                  "kanazawa_5_districts_usd": round(tot["kanazawa"], 2),
                                  "total_usd": round(tot["jgd"] + tot["onagawa"] + tot["kanazawa"], 2),
                                  "calls": {k[6:]: v for k, v in tot.items() if k.startswith("calls_")},
                                  "largest_docs": sorted(per, reverse=True)[:5]}
        if model == "deepseek":
            est["by_model"][model]["note"] = "混む時間の料金で数えた。混まない時間 (UTC 16:30〜00:30) に回せば半額。指示の部分がキャッシュに当たればさらに安い"
        else:
            est["by_model"][model]["note"] = "claude -p の total_cost_usd から。ページの多い PDF は Read の回数が増え、ページあたりの費用は試走より上がりうる"
    return est


def cmd_smoke(a, pl):
    docs = docs_for(pl, a.specs or planmod.SMOKE)
    rows, scores = [], {}
    for d in docs:
        m = prep.prepare(d)
        for model in a.models:
            say(f"{d['doc_id']} … {model}")
            rec = runners.ask(model, m, force=a.force)
            met = doc_metrics(rec, m)
            say(f"  {model}: ${met['cost_usd']} {met['seconds']}s tokens={met['tokens']} schema_ok={met['schema_ok']} {met['error'] or ''}")
            rows.append({"doc_id": d["doc_id"], "target": d["target"], "model": model, "metrics": met})
    for (target, key), ds in groups(docs).items():
        res = {model: score_group(target, ds, model) for model in a.models}
        scores[f"{target}:{key}"] = {"docs": [d["doc_id"] for d in ds], "compare": compare_models(res), "detail": res}
        say(f"{target}:{key} {compare_models(res)}")
    out = {"about": "試走: 同じ入力・同じ問いかけを DeepSeek と Opus に。資料ごとの費用・トークン・秒と、正解との採点",
           "prompt_version": prompt.PROMPT_VERSION, "prompt_sha256": prompt.prompt_sha256(), "per_doc": rows, "scores": scores,
           "estimate": estimate(pl, rows)}
    cb = runners.bench_cli()
    write_json(OUT / "smoke.json", cb.redact(out))
    pl["estimate"] = out["estimate"]
    write_json(OUT / "plan.json", pl)
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "prep", "ask", "score", "smoke"])
    ap.add_argument("specs", nargs="*")
    ap.add_argument("--models", default="deepseek,opus")
    ap.add_argument("--force", action="store_true", help="答えがあっても聞き直す")
    a = ap.parse_args(argv)
    a.models = [x for x in a.models.split(",") if x]
    if a.cmd == "plan":
        pl = planmod.make()
        print("書いた: out/llmread/plan.json", pl["totals"])
        return 0
    pl = load_plan()
    if a.cmd == "smoke":
        out = cmd_smoke(a, pl)
        print("書いた: out/llmread/smoke.json")
        for k, v in out["scores"].items():
            print(k, v["compare"])
        for m, e in out["estimate"]["by_model"].items():
            print(m, {k: e.get(k) for k in ("jgd_tier_a_usd", "onagawa_12_months_usd", "kanazawa_5_districts_usd", "total_usd", "error")})
        return 0
    docs = docs_for(pl, a.specs)
    if a.cmd == "prep":
        for d in docs:
            m = prep.prepare(d, force=a.force)
            print(d["doc_id"], m["kind"], "文字", m["text_chars"], "ページ", len(m["pages"]))
        return 0
    if a.cmd == "ask":
        for d in docs:
            m = prep.prepare(d)
            for model in a.models:
                met = doc_metrics(runners.ask(model, m, force=a.force), m)
                print(d["doc_id"], model, {k: met[k] for k in ("cost_usd", "seconds", "tokens", "schema_ok", "error")})
        return 0
    for (target, key), ds in groups(docs).items():
        res = {model: score_group(target, ds, model) for model in a.models}
        write_json(OUT / "scores" / f"{target}_{key}.json", runners.bench_cli().redact(score.drop_ours(res)))   # report と同じ形
        print(f"{target}:{key}", compare_models(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
