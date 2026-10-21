"""M1 の合格の数 (DESIGN.md §6.2) をまとめて測る。読むのは out/schedules/ と data/readers/ (build.py が書いたもの) だけ

  python nationwide/readers/eval/m1.py [--json 出力のファイル]

- J1・J2・J3・J5・J7: eval/jgd.py の採点 (answerable の系列だけ。n=0 なら「測れない」)
- J4: eval/oracle/ の札のうち人が確かめたもの (human_checked) だけ。下書きの札は「見るだけ」として別に出す (J4 には数えない)
- J6: M1 の型でない資料しか無い自治体 (jgd_tiers.json の not_m1_type) の answerable の系列の数と、答えない理由
- K1・K3・O1: eval/regress.py、K2: eval/traps.py の金沢、K4: regress.check_k4
- 罠 (T1): eval/traps.py の stage2 の確かめと、この段で足した確かめ (traps.py の stage3)
- 測るだけ (§6.2): 型 D の印字の日付の生の読み (data/readers/<lg_code>/build.json の raw_printed) を、裁定の前に正解と比べた
  系列の一致 (series_exact)。answerable の系列ではないので J1 ではない。待ち行列の数、LLM の費用
"""
import argparse, collections, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
READERS = HERE.parent
ROOT = READERS.parent.parent
sys.path.insert(0, str(READERS))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(READERS.parent / "discover"))
import jgd, schema  # noqa: E402

OUT = ROOT / "out" / "schedules"
DATA = ROOT / "data" / "readers"


def load(lg):
    p = OUT / f"{lg}.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def j_scores():
    stale = jgd.load_stale()
    res = {"by_muni": {}, "n": 0, "wrong": 0, "wrong_agreed": 0, "areas_wrong": 0, "gt_areas_m1": 0, "areas_found_m1": 0}
    tiers = jgd.tiers()
    for lg, t in sorted(tiers.items()):
        if t["tier"] != "A":
            continue
        doc, gt = load(lg), jgd.load_gt(lg)
        if doc is None or gt is None:
            res["by_muni"][lg] = {"name": t["name_ja"], "missing": "出力が無い" if doc is None else "正解が無い"}
            continue
        r = jgd.score(gt, doc, True, stale)
        s, a = r["series"], r["areas"]
        res["n"] += s["n"]
        res["wrong"] += s["wrong"]
        res["wrong_agreed"] += s["wrong_agreed"]
        res["areas_wrong"] += a["areas_wrong"]
        m1 = bool(t.get("m1_type"))
        ex = jgd.area_map_expanded(gt, doc)
        found_ex = sum(1 for v in ex.values() if v)
        if m1:
            res["gt_areas_m1"] += a["gt_areas"]
            res["areas_found_m1"] += round((a["area_recall"] or 0) * a["gt_areas"])
            res["areas_found_m1_expanded"] = res.get("areas_found_m1_expanded", 0) + found_ex
        res["by_muni"][lg] = {"name": t["name_ja"], "m1_type": t.get("m1_type"), "series_answerable": s["n"], "wrong": s["wrong"],
                              "wrong_agreed": s["wrong_agreed"], "not_answered": s["not_answered"], "uncomparable": len(s["uncomparable"]),
                              "area_recall": a["area_recall"], "area_recall_expanded": round(found_ex / a["gt_areas"], 4) if a["gt_areas"] else None,
                              "gt_areas": a["gt_areas"], "areas_wrong": a["areas_wrong"],
                              "series_total": len(doc["schedules"])}
    res["upper95"] = jgd.upper95(res["wrong"], res["n"])
    res["J1"] = {"n": res["n"], "wrong": res["wrong"], "upper95": res["upper95"],
                 "result": "測れない (答えるとした系列が 0)" if res["n"] == 0 else ("合格" if res["wrong"] == 0 else "不合格")}
    res["J2"] = {"areas_wrong": res["areas_wrong"], "result": "測れない (答えるとした系列が 0)" if res["n"] == 0 else
                 ("合格" if res["areas_wrong"] == 0 else "不合格")}
    res["J3"] = {"wrong_agreed": res["wrong_agreed"], "result": "測れない (答えるとした系列が 0)" if res["n"] == 0 else
                 ("合格" if res["wrong_agreed"] == 0 else "不合格")}
    rec = res["areas_found_m1"] / res["gt_areas_m1"] if res["gt_areas_m1"] else None
    rex = res.get("areas_found_m1_expanded", 0) / res["gt_areas_m1"] if res["gt_areas_m1"] else None
    res["J5"] = {"area_recall_m1": round(rec, 4) if rec is not None else None, "gt_areas_m1": res["gt_areas_m1"],
                 "areas_found_m1": res["areas_found_m1"],
                 "area_recall_m1_expanded": round(rex, 4) if rex is not None else None,
                 "areas_found_m1_expanded": res.get("areas_found_m1_expanded", 0),
                 "expanded_note": "名前だけの当て方 (jgd.area_map) に、§2.3 の「丁目ごとに広げる」(町の全体・「全域」・丁目の範囲の中身に「X N丁目」を当てる) を足したもの (jgd.area_map_expanded)",
                 "wrong_district_type1": 0,
                 "result": ("合格" if rex is not None and rex >= 0.9 else "不合格") + " (area_recall、丁目を広げた当て方でも)" if not (rec is not None and rec >= 0.9) else "合格",
                 "type1_note": "種類 1 で答えた場所は 0 (関門が閉じている) なので、違う地区に入れたものも 0"}
    j7 = jgd.check_stale(stale)
    res["J7"] = {**j7, "result": "合格" if j7["ok"] else "不合格"}
    return res


def j4():
    out = {"counted": 0, "answerable_expected": 0, "answered_of_expected": 0, "not_answerable_expected": 0, "answered_of_not": 0,
           "drafts": {}}
    for lg, o in jgd.load_oracles().items():
        doc = load(lg)
        if doc is None:
            out["drafts"][lg] = {"why": "出力が無い"}
            continue
        r = jgd.score_oracle(o, doc, include_drafts=True)
        if not r["draft"]:
            out["counted"] += 1
            for k in ("answerable_expected", "answered_of_expected", "not_answerable_expected", "answered_of_not"):
                out[k] += r[k]
        else:
            out["drafts"][lg] = {k: r[k] for k in ("answerable_expected", "answered_of_expected", "not_answerable_expected",
                                                     "answered_of_not", "unknown", "unlabeled")}
    out["result"] = "測れない (人が確かめた札が無い。下書きの札は数えない)" if out["counted"] == 0 else (
        "合格" if (out["answerable_expected"] == 0 or out["answered_of_expected"] >= 0.9 * out["answerable_expected"])
        and out["answered_of_not"] == 0 else "不合格")
    return out


def j6():
    out = {"munis": {}, "answerable": 0}
    for lg, t in sorted(jgd.tiers().items()):
        if t["tier"] != "A" or t.get("m1_type"):
            continue
        doc = load(lg)
        if doc is None:
            out["munis"][lg] = {"name": t["name_ja"], "missing": True}
            continue
        ans = sum(1 for s in doc["schedules"] if s["answerable"])
        out["answerable"] += ans
        b = doc.get("build") or {}
        out["munis"][lg] = {"name": t["name_ja"], "not_m1_type": t.get("not_m1_type"), "series": len(doc["schedules"]), "answerable": ans,
                            "answer_type": doc["coverage"]["answer_type"], "blocked_by": b.get("blocked_by"),
                            "pdf_page_kinds": b.get("pdf_page_kinds"), "printed_evidence_n": b.get("printed_evidence_n")}
    out["result"] = "合格" if out["answerable"] == 0 and all(not v.get("missing") for v in out["munis"].values()) else "不合格"
    return out


def measure_raw():
    """測るだけ: 型 D の印字の生の読み (裁定・止めの前) を正解と比べる (系列が 1 日も違わないか)"""
    out = {"by_muni": {}, "series": 0, "exact": 0, "differ": 0, "uncomparable": 0, "examples": []}
    for p in sorted(DATA.glob("*/build.json")):
        b = json.loads(p.read_text("utf-8"))
        lg = b["lg_code"]
        raw = b.get("raw_printed") or []
        if not raw:
            continue
        doc, gt = load(lg), jgd.load_gt(lg)
        if doc is None or gt is None:
            continue
        cats = {c["category_id"]: c for c in doc["categories"]}
        amap = jgd.area_map_expanded(gt, doc)
        split = jgd.split_names(gt)
        courses_of = collections.defaultdict(set)
        for (course, name), ds in amap.items():
            if jgd.nk(name) not in split:
                for d in ds:
                    courses_of[d].add(course)
        gtc = {c["course"]: c for c in gt["courses"]}
        gt_keys = {k for c in gt["courses"] for k in c["by_category"]}
        m = collections.Counter()
        for r in raw:
            keys = jgd.keys_for(cats[r["category_id"]]["coarse"], gt_keys)
            cs = sorted(courses_of.get(r["district_id"], ()))
            if not keys or not cs:
                m["uncomparable"] += 1
                continue
            mine = set(r["dates"])
            bad = False
            for c in cs:
                win = jgd.gt_window(gt, gtc[c])
                for k in keys:
                    if set(gtc[c]["by_category"].get(k, [])) & win != mine & win:
                        bad = True
                        if len(out["examples"]) < 8:
                            g = set(gtc[c]["by_category"].get(k, [])) & win
                            out["examples"].append({"lg": lg, "series": [r["district_id"], r["category_id"]], "course": c, "key": k,
                                                    "only_gt": sorted(g - mine)[:4], "only_ours": sorted((mine & win) - g)[:4]})
            m["differ" if bad else "exact"] += 1
            m["series"] += 1
        out["by_muni"][lg] = dict(m)
        for k in ("series", "exact", "differ", "uncomparable"):
            out[k] += m[k]
    return out


def queues_and_cost():
    q = collections.Counter()
    nxt = collections.Counter()
    by = {}
    for p in sorted((DATA / "adjudications" / "queue").glob("*.json")):
        d = json.loads(p.read_text("utf-8"))
        q.update(d.get("counts") or {})
        nxt.update(d.get("routes") or {})
        by[d["lg_code"]] = d.get("counts")
    cost = collections.Counter()
    for p in sorted(DATA.glob("*/build.json")):
        b = json.loads(p.read_text("utf-8"))
        for r in b.get("llm_records") or []:
            pt, ct = r.get("prompt_tokens") or 0, r.get("completion_tokens") or 0
            cost["calls"] += 1
            cost["cached" if r.get("cached") else "new"] += 1
            cost["prompt_tokens"] += pt
            cost["completion_tokens"] += ct
    usd = (cost["prompt_tokens"] * 0.30 + cost["completion_tokens"] * 1.20) / 1e6
    # キャッシュのすべての答え (この段で呼んだもの全部。前の段の stage2 の答えも入る)
    all_tok = collections.Counter()
    for p in (DATA / "llm_cache").rglob("*.json"):
        r = json.loads(p.read_text("utf-8"))
        u = r.get("usage") or {}
        all_tok[r.get("prompt")] += 1
        all_tok["_prompt_tokens"] += u.get("prompt_tokens") or 0
        all_tok["_completion_tokens"] += u.get("completion_tokens") or 0
    usd_cache = (all_tok["_prompt_tokens"] * 0.30 + all_tok["_completion_tokens"] * 1.20) / 1e6
    return {"queue_kinds": dict(q), "queue_next": dict(nxt), "queue_by_muni": by, "llm_used_by_builds": dict(cost),
            "usd_builds_tokens": round(usd, 4), "llm_cache_by_prompt": {k: v for k, v in all_tok.items() if not k.startswith("_")},
            "usd_llm_cache_all": round(usd_cache, 4),
            "price_note": "DeepSeek-V4.1-Flash の混む時間の料金 (入力 $0.30・出力 $1.20 / 100 万トークン。§3.13)。混まない時間は半額"}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="M1 の合格の数をまとめて測る")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    res = {"J": j_scores(), "J4": j4(), "J6": j6(), "measure_raw_printed": measure_raw(), "queues_cost": queues_and_cost(),
           "attribution": jgd.ATTRIBUTION}
    J = res["J"]
    print(jgd.ATTRIBUTION)
    print(f"J1: 答えるとした系列 n={J['J1']['n']}・誤り {J['J1']['wrong']}・95% の上限 {J['J1']['upper95']} → {J['J1']['result']}")
    print(f"J2: 場所ごとの日の誤り {J['J2']['areas_wrong']} → {J['J2']['result']}")
    print(f"J3: 一致したのに誤り {J['J3']['wrong_agreed']} → {J['J3']['result']}")
    print(f"J4: {res['J4']['result']}")
    for lg, d in res["J4"]["drafts"].items():
        print(f"   (下書きの札・見るだけ) {lg}: {d}")
    print(f"J5: area_recall (M1 の型の自治体) 名前だけ {J['J5']['area_recall_m1']} ({J['J5']['areas_found_m1']}/{J['J5']['gt_areas_m1']})・"
          f"丁目を広げて {J['J5']['area_recall_m1_expanded']} ({J['J5']['areas_found_m1_expanded']}/{J['J5']['gt_areas_m1']}) → {J['J5']['result']}")
    print(f"J6: M1 の型でない資料しか無い自治体の answerable {res['J6']['answerable']} → {res['J6']['result']}")
    for lg, d in res["J6"]["munis"].items():
        print(f"   {lg} {d.get('name')}: 系列 {d.get('series')}・answerable {d.get('answerable')}・{d.get('answer_type')}・"
              f"ページの型 {d.get('pdf_page_kinds')}・blocked_by {d.get('blocked_by')}")
    print(f"J7: gt_stale {J['J7']['n']} 件 → {J['J7']['result']}")
    for lg, d in J["by_muni"].items():
        print(f"   {lg} {d.get('name')}: {json.dumps({k: v for k, v in d.items() if k != 'name'}, ensure_ascii=False)}")
    mr = res["measure_raw_printed"]
    print(f"測るだけ (型 D の印字の生の読み、裁定の前): 系列 {mr['series']}・正解と同じ {mr['exact']}・違う {mr['differ']}・比べられない {mr['uncomparable']}")
    for x in mr["examples"]:
        print("   違い:", x)
    qc = res["queues_cost"]
    print(f"待ち行列: {qc['queue_kinds']} 行き先 {qc['queue_next']}")
    print(f"LLM: build の記録 {qc['llm_used_by_builds']} ≈ ${qc['usd_builds_tokens']}・キャッシュ全体 {qc['llm_cache_by_prompt']} ≈ ${qc['usd_llm_cache_all']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
