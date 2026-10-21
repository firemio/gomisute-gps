#!/usr/bin/env python3
"""本番: 計画 (out/llmread/plan.json) のすべての資料を DeepSeek と Opus に聞き (同じ入力・同じ問いかけ)、採点してまとめる

  GOMISUTE_LLM_API_KEY=... python nationwide/llmread/full.py ask [--cap-opus 60] [--cap-deepseek 5]
  python nationwide/llmread/full.py report       # out/llmread/raw/<モデル>/・inputs.json・scores/・results.json
  python nationwide/llmread/full.py score [--check]
      # モデルを呼ばずに、git に入れた生の答え (out/llmread/raw/) と資料の記録 (out/llmread/inputs.json) から
      # scores/・results.json を作り直す。--check は書き換えずに、作り直したものが今のものと同じか確かめる

順番 (費用の上限を超えそうなら、そこから先を止める): tier A の自治体 → 女川 12 か月 → 金沢 5 地区 (計画の並び)。
上限は、すでにある答え (試走) の費用も含めたモデルごとの合計。聞く前に「見積もり × 2」を取っておき、終わったら実際の費用に置き換える。
同じ問いかけ・同じ入力の答えがあれば聞き直さない (runners.ask)。

採点の単位 (系列) は run.py score と同じ: jgd は正解のコース × 品目、女川は月 × マス、金沢は地区 × 品目。
系列ごとの判定:
  correct  正解の期間の日付が 1 日も違わない
  wrong    正解に無い日を 1 日でも出した (危ない数)
  missing  違う日は無いが、足りない日がある (品目・地区が当たらないものを含む)
  abstained 答えてよい (answerable) にならなかった。correct・wrong・missing とは別に数える
"""
import argparse, collections, concurrent.futures as cf, hashlib, json, pathlib, statistics, sys, tempfile, threading, time

from common import ANSWERS, INPUTS, OUT, now_utc, read_json, say, write_json
import calc, plan as planmod, prep, prompt, runners, score
import run as runmod

SAFETY = 2.0
INPUTS_FILE = OUT / "inputs.json"        # 資料ごとの入力の記録 (種類・ページ数・文字数・sha256)。score が使う
YEAR_END = {"12-29", "12-30", "12-31", "01-01", "01-02", "01-03"}
TYPE_JA = {"html_table": "HTML の表", "html_text": "HTML の文", "csv": "CSV・Excel", "pdf_text": "文字の PDF", "pdf_image": "画像の PDF"}


def ordered_docs(pl):
    """計画の並び (tier A → 女川 → 金沢) のキャッシュにある資料"""
    return [d for t in pl["targets"] for d in t["docs"] if d.get("cached")]


def est_cost(pl, model, d):
    e = (pl.get("estimate") or {}).get("by_model", {}).get(model) or {}
    instr = (pl.get("estimate") or {}).get("instruction_chars") or 7000
    pages = (len(d["pages"]) if d.get("pages") else d.get("pages_total") or 0) if d.get("kind") == "pdf" else 0
    return (e.get("a_usd_per_char") or 2e-5) * (instr + (d.get("text_chars") or 0)) + (e.get("b_usd_per_page") or 0.03) * pages


def failed(rec):
    """エラーか、答えの形が正しくない記録 (どちらのモデルも同じ扱い: 1 回だけ聞き直す)"""
    return bool(rec.get("error") or rec.get("answer") is None or calc.validate(rec["answer"]))


def rec_cost(rec):
    c = (rec or {}).get("cost") or {}
    return c.get("usd")


def tries(model, doc_id):
    """前に失敗した記録 (<doc_id>.try1.json …) の道すじのリスト。失敗した呼び出しの費用も数えるために残す"""
    p = runners.answer_path(model, doc_id)
    return sorted(p.parent.glob(f"{doc_id}.try*.json"), key=lambda x: int(x.name.rsplit(".try", 1)[1].split(".")[0]))


def tokens_all(rec):
    """記録 → トークン。DeepSeek は呼び出しのすべて (JSON として読めずに聞き直した分・失敗した分も) の合計"""
    att = [a for a in ((rec.get("raw") or {}).get("attempts") or []) if a.get("usage")]
    if rec.get("model") == runners.DEEPSEEK_MODEL and att:
        t = collections.Counter()
        for a in att:
            u = a["usage"]
            t["input"] += u.get("prompt_tokens") or 0
            t["output"] += u.get("completion_tokens") or 0
            t["cache_read"] += u.get("prompt_cache_hit_tokens") or 0
            t["reasoning"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        return dict(t)
    return {k: v for k, v in (runners.tokens(rec) if rec.get("usage") else {}).items() if v is not None}


def keep_failed(model, doc_id):
    """エラーの記録を <doc_id>.try<N>.json に移す (聞き直しで上書きしない)"""
    p = runners.answer_path(model, doc_id)
    q = p.with_name(f"{doc_id}.try{len(tries(model, doc_id)) + 1}.json")
    p.replace(q)
    return q


# ---- 聞く ----
def run_model(model, docs, pl, cap, workers, log, a_max_tries=2):
    pr_sha = prompt.prompt_sha256()
    lock = threading.Lock()
    st = {"spent": 0.0, "reserved": 0.0, "asked": [], "reused": [], "retried": [], "gave_up": [], "errors": [], "dropped": []}
    pend = {}

    def collect(fut):
        d, r = pend.pop(fut)
        try:
            rec = fut.result()
            c = rec_cost(rec)
            err = rec.get("error")
        except Exception as e:                                    # 時間切れなど: 費用が分からないので見積もり × 2 を使ったことにする
            rec, c, err = None, r, f"{type(e).__name__}: {str(e)[:300]}"
        with lock:
            st["reserved"] -= r
            st["spent"] += c if c is not None else r
            st["asked"].append(d["doc_id"])
            if err:
                st["errors"].append({"doc_id": d["doc_id"], "error": err})
        say(f"[{model}] {d['doc_id']} ${c} {err or ''} 合計 ${st['spent']:.3f}")

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, d in enumerate(docs):
            m = prep.prepare(d)
            st["spent"] += sum(rec_cost(read_json(t)) or 0 for t in tries(model, d["doc_id"]))   # 前に失敗した呼び出しの費用
            old = runners.load_answer(model, d["doc_id"])
            if old and not failed(old) and old.get("prompt_sha256") == pr_sha and old.get("input_sha256") == m["input_sha256"]:
                st["spent"] += rec_cost(old) or 0
                st["reused"].append(d["doc_id"])
                continue
            if old and failed(old):
                if len(tries(model, d["doc_id"])) + 1 >= a_max_tries:
                    st["spent"] += rec_cost(old) or 0
                    st["gave_up"].append(d["doc_id"])
                    continue
                keep_failed(model, d["doc_id"])                   # 聞き直す (同じ問いかけ・同じ設定)。失敗の記録は残す
                st["spent"] += rec_cost(old) or 0
                st["retried"].append(d["doc_id"])
            r = est_cost(pl, model, d) * SAFETY
            while True:
                with lock:
                    ok = st["spent"] + st["reserved"] + r <= cap
                if ok or not pend:
                    break
                done, _ = cf.wait(list(pend), return_when=cf.FIRST_COMPLETED)
                for f in done:
                    collect(f)
            if not ok:
                st["dropped"] = [x["doc_id"] for x in docs[i:]]
                say(f"[{model}] 上限 ${cap} に届くので {d['doc_id']} から先 ({len(st['dropped'])} 本) を止める")
                break
            with lock:
                st["reserved"] += r
            pend[pool.submit(runners.ask, model, m)] = (d, r)
            while len(pend) >= workers:
                done, _ = cf.wait(list(pend), return_when=cf.FIRST_COMPLETED)
                for f in done:
                    collect(f)
        while pend:
            done, _ = cf.wait(list(pend), return_when=cf.FIRST_COMPLETED)
            for f in done:
                collect(f)
    st["spent"] = round(st["spent"], 4)
    log[model] = {k: v for k, v in st.items() if k != "reserved"}
    log[model]["cap_usd"] = cap


def cmd_ask(a):
    pl = runmod.load_plan()
    docs = ordered_docs(pl)
    if a.only:
        docs = [d for d in docs if d["doc_id"] in set(a.only)]
    log = {"started_at": now_utc().isoformat(timespec="seconds"), "order": "tier A → 女川 → 金沢 (計画の並び)",
           "safety": SAFETY}
    caps = {"opus": a.cap_opus, "deepseek": a.cap_deepseek}
    workers = {"opus": a.workers_opus, "deepseek": a.workers_deepseek}
    ths = [threading.Thread(target=run_model, args=(m, docs, pl, caps[m], workers[m], log, a.max_tries)) for m in a.models]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    log["finished_at"] = now_utc().isoformat(timespec="seconds")
    prev = read_json(OUT / "run_log.json") if (OUT / "run_log.json").exists() else {"runs": []}
    prev["runs"].append(log)
    write_json(OUT / "run_log.json", runners.bench_cli().redact(prev))
    for m in a.models:
        L = log[m]
        print(m, "費用", L["spent"], "聞いた", len(L["asked"]), "使い回し", len(L["reused"]), "聞き直し", len(L["retried"]),
              "エラー", len(L["errors"]), "諦めた", len(L["gave_up"]), "止めた", len(L["dropped"]))
    return 0


# ---- まとめ ----
def source_type(d):
    """資料の種類 (機械的に): HTML は <table> があれば表、PDF は 1 ページあたりの文字層が 100 字以上なら文字の PDF"""
    mf = INPUTS / d["doc_id"] / "manifest.json"
    m = read_json(mf)
    if m["kind"] == "html":
        t = (INPUTS / d["doc_id"] / m["text"]).read_text("utf-8")
        return "html_table" if "<table" in t else "html_text"
    if m["kind"] in ("csv", "xlsx", "xls"):
        return "csv"
    n = len(m["pages"]) or 1
    return "pdf_text" if sum(p["chars"] for p in m["pages"]) / n >= 100 else "pdf_image"


def doc_inputs(docs):
    """資料ごとの入力の記録 (data/llmread/inputs/ の manifest から) → {doc_id: {kind, source_type, pages, text_chars, …}}"""
    out = {}
    for d in docs:
        m = prep.prepare(d)
        out[d["doc_id"]] = {"kind": m["kind"], "source_type": source_type(d), "pages": len(m["pages"]),
                            "text_chars": m["text_chars"], "input_sha256": m["input_sha256"], "source_sha256": m["source_sha256"]}
    return out


def count_answers(docs):
    """答えの記録の数 (export_raw と同じ数え方。score で out/llmread/raw/ を読むとき)"""
    n = collections.Counter()
    for model in runmod.MODELS:
        for d in docs:
            if runners.load_answer(model, d["doc_id"]) is None:
                continue
            n[model] += 1
            for _ in tries(model, d["doc_id"]):
                n[model + "_failed_tries"] += 1
    return dict(n)


def export_raw(docs):
    """答えの記録 → out/llmread/raw/<モデル>/<doc_id>.json (家のフォルダの道すじを伏せる。キーは書かれていない)"""
    cb = runners.bench_cli()
    n = collections.Counter()
    for model in runmod.MODELS:
        for d in docs:
            rec = runners.load_answer(model, d["doc_id"])
            if rec is None:
                continue
            rec = dict(rec)
            if model == "deepseek" and "raw" not in rec:
                rec["raw_note"] = "試走 (生の文字を残す前の作り) の記録。answer は API が返した JSON の文字を読んだもの (JSON モード)"
            if model == "opus" and "raw_result" not in rec:
                rec["raw_note"] = "試走 (生の文字を残す前の作り) の記録。answer は claude -p の result の文字を JSON として読んだもの"
            write_json(OUT / "raw" / runners.model_tag(model) / f"{d['doc_id']}.json", runners.scrub(cb.redact(rec)))
            n[model] += 1
            for t in tries(model, d["doc_id"]):
                write_json(OUT / "raw" / runners.model_tag(model) / t.name, runners.scrub(cb.redact(read_json(t))))
                n[model + "_failed_tries"] += 1
    return dict(n)


def sig(entries):
    """系列のモデルの日付 (当たった地区ごと) → 比べるための印 (違う日付の組の集まり。同じ日付の地区が 1 つでも 3 つでも同じ印)。
    当たった地区・品目が無ければ None"""
    if not entries:
        return None
    return tuple(sorted({tuple(e.get("ours") or []) for e in entries}))


def unit_rows(target, key, res, docs, types):
    """採点 → 系列の行 (モデルごと)。jgd で、そのコースの出典がキャッシュに無い系列は in_plan=False (どちらのモデルにも
    資料を渡していないので、数えない)"""
    rows = collections.OrderedDict()
    course_docs = collections.defaultdict(set)
    for d in docs:
        for c in d.get("courses") or []:
            course_docs[str(c)].add(d["doc_id"])
    for model, r in res.items():
        for u in (r or {}).get("units") or []:
            if target == "jgd":
                uid = f"jgd:{key}:{u['course']}:{u['key']}"
                src = sorted(course_docs.get(str(u["course"])) or [])
                in_plan = bool(src)
                src = src or [d["doc_id"] for d in docs]
                entries = [p for p in u.get("per_district") or [] if not p.get("category_unmapped")]
                label = {"course": u["course"], "course_name": u.get("course_name"), "category": u["key"]}
            elif target == "onagawa":
                uid = f"onagawa:{key}:{u['cell']}"
                src = [d["doc_id"] for d in docs]
                in_plan = True
                entries = [p for p in u.get("per_district") or [] if p.get("series")]
                label = {"cell": u["cell"]}
            else:
                uid = f"kanazawa:{key}:{u['category']}"
                src = [d["doc_id"] for d in docs]
                in_plan = True
                entries = [] if u.get("unmapped") else [{"ours": u.get("ours")}]
                label = {"district": key, "category": u["category"], "label": u.get("label")}
            st = {t for t in (types[x] for x in src)}
            row = rows.setdefault(uid, {"series": uid, "target": target, "group": f"{target}:{key}", **label,
                                        "source_type": st.pop() if len(st) == 1 else "mixed", "docs": src, "in_plan": in_plan,
                                        "gt_n": u["gt_n"], "models": {}})
            status = "correct" if u["exact"] else "wrong" if u["wrong_n"] else "missing"
            wrong = sorted({x for p in (u.get("per_district") or [u]) for x in (p.get("wrong") or [])})[:8]
            missing = sorted({x for p in (u.get("per_district") or [u]) for x in (p.get("missing") or [])})[:8]
            # 違う日がすべて年末年始 (12/29〜1/3) か。資料に年末年始の扱いが無く、モデルも「書いていない」と言った系列で起きる
            pw = [p for p in (u.get("per_district") or [u]) if p.get("wrong")]
            ye = bool(pw) and all(len(p["wrong"]) == p.get("wrong_n", len(p["wrong"])) and all(str(x)[5:] in YEAR_END for x in p["wrong"])
                                  for p in pw) and target != "onagawa"
            row["models"][model] = {"status": status, "abstained": not u.get("answered"), "wrong_year_end_only": ye,
                                    "served": "abstained" if not u.get("answered") else status,
                                    "wrong_n": u["wrong_n"], "missing_n": u["missing_n"], "unmapped": bool(u.get("unmapped")),
                                    "why": sorted({y for p in (u.get("per_district") or [u]) for y in (p.get("why") or [])}),
                                    "wrong_sample": wrong, "missing_sample": missing, "_sig": sig(entries)}
    for row in rows.values():                                   # 答えの無いモデル (エラー・形が正しくない) の系列は「足りない」で、答えない
        for model, r in res.items():
            if model not in row["models"]:
                row["models"][model] = {"status": "missing", "abstained": True, "served": "abstained", "wrong_n": 0,
                                        "missing_n": row["gt_n"], "unmapped": True, "no_answer": True,
                                        "why": ["no_valid_answer"], "wrong_sample": [], "missing_sample": [], "_sig": None}
    out = []
    for row in rows.values():
        ms = row["models"]
        if len(ms) == 2:
            a, b = (ms[m]["_sig"] for m in runmod.MODELS)
            row["same_reading"] = None if a is None and b is None else (a == b)   # None: どちらも当たる地区・品目が無い
        for v in ms.values():
            s = v.pop("_sig")
            v["dates_sha1"] = hashlib.sha1(json.dumps(s).encode()).hexdigest()[:10] if s is not None else None
        out.append(row)
    return out


def tally(series, docs_rows, model, types=None):
    ss = [s for s in series if s["in_plan"] and model in s["models"] and (types is None or s["source_type"] in types)]
    c = collections.Counter()
    for s in ss:
        v = s["models"][model]
        c["series"] += 1
        c[v["status"]] += 1
        c["abstained"] += v["abstained"]
        c["answered"] += not v["abstained"]
        c["answered_correct"] += (not v["abstained"]) and v["status"] == "correct"
        c["answered_wrong"] += (not v["abstained"]) and v["status"] == "wrong"
        c["wrong_year_end_only"] += v["status"] == "wrong" and v.get("wrong_year_end_only", False)
        c["wrong_other"] += v["status"] == "wrong" and not v.get("wrong_year_end_only", False)
        c["unmapped"] += v["unmapped"]
        c["wrong_dates"] += v["wrong_n"]
        c["missing_dates"] += v["missing_n"]
    out = {k: c[k] for k in ("series", "correct", "wrong", "wrong_year_end_only", "wrong_other", "missing", "abstained", "answered",
                             "answered_correct", "answered_wrong", "unmapped", "wrong_dates", "missing_dates")}
    ds = [d for d in docs_rows if (types is None or d["source_type"] in types) and d["models"].get(model)]
    cost = [d["models"][model]["cost_usd"] or 0 for d in ds]
    out.update(docs=len(ds), docs_error=sum(1 for d in ds if not d["models"][model]["schema_ok"]),
               cost_usd=round(sum(cost), 4), seconds=round(sum(d["models"][model]["seconds"] or 0 for d in ds), 1))
    return out


def cmd_report(a, out_dir=OUT, from_raw=False):
    """採点してまとめる → out_dir/scores/・out_dir/results.json。
    from_raw: 答えを out/llmread/raw/ から、資料の記録を out/llmread/inputs.json から読む (モデルも data/ の入力も使わない)。
    そうでなければ data/llmread/answers/ と data/llmread/inputs/ から読み、raw/ と inputs.json も書く"""
    pl = runmod.load_plan()
    docs = ordered_docs(pl)
    if from_raw:
        runners.ANSWERS_DIR = OUT / "raw"
        inputs = read_json(INPUTS_FILE)
        raw_n = count_answers(docs)
    else:
        inputs = doc_inputs(docs)
        write_json(INPUTS_FILE, inputs)
        raw_n = export_raw(docs)
    types = {d["doc_id"]: inputs[d["doc_id"]]["source_type"] for d in docs}
    # 資料ごと
    docs_rows = []
    for d in docs:
        m = inputs[d["doc_id"]]
        row = {"doc_id": d["doc_id"], "target": d["target"], "lg_code": d.get("lg_code") or ("045811" if d["target"] == "onagawa" else "172014"),
               "name": d.get("name") or ("女川町" if d["target"] == "onagawa" else "金沢市"),
               "group": f"{d['target']}:{d.get('lg_code') if d['target'] == 'jgd' else d.get('month') or d.get('district')}",
               "source": d.get("url") or d.get("path"), "kind": m["kind"], "source_type": types[d["doc_id"]],
               "pages": m["pages"], "text_chars": m["text_chars"], "models": {}}
        for model in runmod.MODELS:
            rec = runners.load_answer(model, d["doc_id"])
            met = runmod.doc_metrics(rec, m)
            if met:
                x = {k: met[k] for k in ("cost_usd", "cost_usd_peak", "offpeak", "tokens", "seconds", "started_at",
                                         "num_turns", "error", "schema_ok", "schema_errors", "districts", "categories", "schedules")}
                x["unsure_n"] = len(met["unsure"]) if met["unsure"] is not None else None
                # 前に失敗した呼び出し: 費用・秒・トークンを足し、何があったかを書く
                fails = [read_json(t) for t in tries(model, d["doc_id"])]
                x["tries"] = len(fails) + 1
                x["failed_tries"] = [{"error": f.get("error"), "cost_usd": rec_cost(f), "seconds": f.get("seconds")} for f in fails]
                if fails:
                    x["cost_usd_last_try"] = x["cost_usd"]
                    x["cost_usd"] = round((x["cost_usd"] or 0) + sum(rec_cost(f) or 0 for f in fails), 6)
                    if x.get("cost_usd_peak") is not None:
                        x["cost_usd_peak"] = round(x["cost_usd_peak"] + sum(((f.get("cost") or {}).get("usd_peak") or 0) for f in fails), 6)
                    x["seconds"] = round((x["seconds"] or 0) + sum(f.get("seconds") or 0 for f in fails), 1)
                tk = collections.Counter()
                for r_ in [rec] + fails:
                    for k, v in tokens_all(r_).items():
                        tk[k] += v or 0
                x["tokens"] = dict(tk)
                row["models"][model] = x
        docs_rows.append(row)
    # 採点
    series, groups_out = [], {}
    cb = runners.bench_cli()
    for (target, key), ds in runmod.groups(docs).items():
        res = {model: runmod.score_group(target, ds, model) for model in runmod.MODELS
               if any(runners.load_answer(model, d["doc_id"]) for d in ds)}
        write_json(out_dir / "scores" / f"{target}_{key}.json", cb.redact(score.drop_ours(res)))   # 日付の全部 (ours) は大きいので書かない
        groups_out[f"{target}:{key}"] = {"docs": [d["doc_id"] for d in ds], "compare": runmod.compare_models(res),
                                         "errors": {m: r.get("error") for m, r in res.items() if r.get("error")}}
        series += unit_rows(target, key, res, ds, types)
    # まとめ
    totals = {m: tally(series, docs_rows, m) for m in runmod.MODELS}
    per_type = {TYPE_JA.get(t, t): {m: tally(series, docs_rows, m, {t}) for m in runmod.MODELS}
                for t in ("html_table", "html_text", "csv", "pdf_text", "pdf_image", "mixed")}
    per_target = {}
    for tg in ("jgd", "onagawa", "kanazawa"):
        sub = [s for s in series if s["target"] == tg]
        dsub = [d for d in docs_rows if d["target"] == tg]
        per_target[tg] = {m: tally(sub, dsub, m) for m in runmod.MODELS}
    # 自治体あたりの費用 (jgd tier A。キャッシュにある資料だけ)
    per_muni = {}
    for m in runmod.MODELS:
        by = collections.defaultdict(float)
        sec = collections.defaultdict(float)
        for d in docs_rows:
            if d["target"] == "jgd" and d["models"].get(m):
                by[d["lg_code"]] += d["models"][m]["cost_usd"] or 0
                sec[d["lg_code"]] += d["models"][m]["seconds"] or 0
        ona = [d["models"][m] for d in docs_rows if d["target"] == "onagawa" and d["models"].get(m)]
        v = list(by.values())
        per_muni[m] = {"jgd_municipalities": len(v), "mean_usd": round(statistics.mean(v), 4) if v else None,
                       "median_usd": round(statistics.median(v), 4) if v else None, "max_usd": round(max(v), 4) if v else None,
                       "per_1000_mean_usd": round(statistics.mean(v) * 1000, 1) if v else None,
                       "per_1000_median_usd": round(statistics.median(v) * 1000, 1) if v else None,
                       "mean_seconds": round(statistics.mean(sec.values()), 1) if sec else None,
                       "onagawa_year_usd": round(sum(x["cost_usd"] or 0 for x in ona), 4), "onagawa_months": len(ona),
                       "by_lg": {k: round(x, 4) for k, x in by.items()}}
    # 2 つのモデルの読みが同じか
    agree = collections.Counter()
    agree["not_in_plan"] = sum(1 for s in series if not s["in_plan"])
    for s in series:
        if len(s["models"]) < 2 or not s["in_plan"]:
            continue
        a_, b_ = (s["models"][m] for m in runmod.MODELS)
        agree["both_scored"] += 1
        if s.get("same_reading") is None:
            agree["both_unmapped"] += 1
        elif s.get("same_reading"):
            agree["same"] += 1
            agree["same_correct"] += a_["status"] == "correct"
            agree["same_wrong"] += a_["status"] == "wrong"
            agree["same_missing"] += a_["status"] == "missing"
            if not a_["abstained"] and not b_["abstained"]:
                agree["same_both_answered"] += 1
                agree["same_both_answered_correct"] += a_["status"] == "correct"
        else:
            agree["different"] += 1
            ka = {(m, s["models"][m]["status"]) for m in runmod.MODELS}
            ca, cb_ = a_["status"] == "correct", b_["status"] == "correct"
            agree["different_deepseek_only_correct" if ca and not cb_ else "different_opus_only_correct" if cb_ and not ca
                  else "different_both_correct" if ca and cb_ else "different_neither_correct"] += 1
    out = {"about": "LLM が資料を読む本番 (nationwide/llmread/)。同じ入力・同じ問いかけを DeepSeek と Opus に渡し、正解と比べた",
           "decision_rule": pl.get("decision_rule"), "prompt_version": prompt.PROMPT_VERSION, "prompt_sha256": prompt.prompt_sha256(),
           "models": {"deepseek": runners.DEEPSEEK_MODEL, "opus": runners.OPUS_MODEL},
           "answer_keys": {"jgd": "japan-gomi-data tier A (nationwide/llmread/jgd.py)。系列 = 正解のコース × 品目、2026 年度",
                           "onagawa": "out/onagawa_reading.json の final (O1)。系列 = 月 × マス (江島は除く)",
                           "kanazawa": "out/kanazawa_schedules.json (K1)。系列 = 地区 × 品目、2026 年度"},
           "status_meaning": {"correct": "正解の期間の日付が 1 日も違わない", "wrong": "正解に無い日を 1 日でも出した",
                              "missing": "違う日は無いが足りない日がある (地区・品目が当たらないものを含む)",
                              "abstained": "答えてよい (answerable) にならなかった (unsure・祝日や年末年始の扱いが無い など)。"
                                           "correct・wrong・missing とは別に数える", "served": "答えるものだけを見たときの判定 (abstained なら abstained)"},
           "run_log": read_json(OUT / "run_log.json") if (OUT / "run_log.json").exists() else None,
           "raw_files": raw_n, "totals": totals, "per_target": per_target, "per_source_type": per_type,
           "cost_per_municipality": per_muni, "agreement": dict(agree), "groups": groups_out,
           "documents": docs_rows, "series": series}
    write_json(out_dir / "results.json", runners.scrub(cb.redact(out)))
    print(json.dumps({"totals": totals, "agreement": dict(agree)}, ensure_ascii=False, indent=1))
    return 0


def cmd_score(a):
    """モデルを呼ばずに採点を作り直す。--check なら一時のフォルダに作って、out/llmread/ のものと比べる (書き換えない)"""
    if not a.check:
        return cmd_report(a, OUT, from_raw=True)
    with tempfile.TemporaryDirectory(prefix="llmread-score-") as t:
        tmp = pathlib.Path(t)
        cmd_report(a, tmp, from_raw=True)
        bad = []
        names = sorted({p.name for p in (OUT / "scores").glob("*.json")} | {p.name for p in (tmp / "scores").glob("*.json")})
        for rel in ["results.json"] + [f"scores/{n}" for n in names]:
            x, y = OUT / rel, tmp / rel
            if not x.exists() or not y.exists():
                bad.append(f"{rel}: {'今のもの' if not x.exists() else '作り直したもの'}が無い")
                continue
            ka, kb = read_json(x), read_json(y)
            if ka != kb:
                keys = sorted(str(k) for k in set(ka) | set(kb) if ka.get(k) != kb.get(k)) if isinstance(ka, dict) else []
                bad.append(f"{rel}: 違う ({', '.join(keys[:8])})")
    print(f"作り直した採点: results.json と scores/ の {len(names)} 本を out/llmread/ のものと比べた → "
          + ("すべて同じ" if not bad else f"{len(bad)} 本が違う"))
    for b in bad:
        print("  ", b)
    return 1 if bad else 0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ask", "report", "score"])
    ap.add_argument("--models", default="deepseek,opus")
    ap.add_argument("--cap-opus", type=float, default=60.0)
    ap.add_argument("--cap-deepseek", type=float, default=5.0)
    ap.add_argument("--workers-opus", type=int, default=3)
    ap.add_argument("--workers-deepseek", type=int, default=6)
    ap.add_argument("--only", nargs="*", help="この doc_id だけ (試し)")
    ap.add_argument("--max-tries", type=int, default=2, help="エラーの資料を何回目まで聞くか (どちらのモデルも同じ。既定 2 = 1 回だけ聞き直す)")
    ap.add_argument("--check", action="store_true", help="score: 書き換えずに、作り直したものが out/llmread/ のものと同じか確かめる")
    a = ap.parse_args(argv)
    a.models = [x for x in a.models.split(",") if x]
    return cmd_ask(a) if a.cmd == "ask" else cmd_score(a) if a.cmd == "score" else cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
