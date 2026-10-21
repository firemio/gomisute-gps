"""正解と比べる (地区・品目の当ては名前だけ。当たらないものは「当たらない」と書き、推し量らない)

  japan-gomi-data tier A (jgd.py): 系列 = 正解のコース × 正解の品目の鍵。1 年分 (正解の期間 ∩ 2026 年度、
      正解の不明の日を除く) の日付が 1 日も違わないか。違う日 (こちらだけ) と足りない日 (正解だけ) の数
  女川 (out/onagawa_reading.json の final。O1 のマス): 月 × (燃やせるごみの色 + 資源の表の行 × 列) のマスの日
  金沢 (out/kanazawa_schedules.json。355 系列。RRULE・segments を legacy_occurrences で広げる): 地区 × 品目の 2026 年度の日付

地区の当て方: 正解の地区の名前 (町名・コース名・色・表の行) と、LLM の地区の label か members が、字をそろえて同じ
(jgd.area_map と同じ。割れ町は当てない)。その資料が正解の 1 コースだけの出典で、答えの地区が 1 つなら、そのコース (source_doc)。
品目の当て方: 正解の名前 (自治体ごとの名前・群の名前・ふつうの名前) と label が同じ → LLM の coarse → label を「・」で分けた
それぞれが正解の名前 (label_parts)。どれでもなければ unmapped。どの方法で当てたかを結果に書く。
"""
import collections, datetime as dt, re, unicodedata

from common import ROOT, read_json
import calc
import jgd

_SEP = re.compile(r"[\s・、,，/／()（）「」『』【】\[\]〔〕:：。.]")
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_GENERIC = None


def nk(s):
    return jgd.nk(s)


def loose(s):
    """字をそろえ、区切りの記号と空白を外したもの (「缶・びん ペットボトル」=「缶・びん・ペットボトル」)"""
    return _SEP.sub("", nk(unicodedata.normalize("NFKC", s or "")))


def strip_paren(s):
    return _PAREN.sub("", s or "")


def _daterange(a, b):
    d, e = calc._d(a), calc._d(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def _diff(ours, gt):
    """ours はモデルの日付 (2 つのモデルの読みどうしを比べるために全部残す)"""
    return {"exact": ours == gt, "wrong": sorted(ours - gt), "missing": sorted(gt - ours), "ours": sorted(ours)}


def _summary(units):
    """単位 (系列・マス) のリスト → 数"""
    s = collections.Counter({k: 0 for k in ("units", "exact", "wrong_dates", "missing_dates", "gt_dates", "unmapped", "answered",
                                             "answered_exact", "answered_wrong")})
    for u in units:
        s["units"] += 1
        s["exact"] += u["exact"]
        s["wrong_dates"] += u["wrong_n"]
        s["missing_dates"] += u["missing_n"]
        s["gt_dates"] += u["gt_n"]
        if u.get("unmapped"):
            s["unmapped"] += 1
        if u.get("answered"):
            s["answered"] += 1
            s["answered_exact"] += u["exact"]
            s["answered_wrong"] += not u["exact"]
    return dict(s)


# ---- japan-gomi-data ----
def _generic_labels():
    global _GENERIC
    if _GENERIC is None:
        import yaml
        files = jgd.load_tar()
        _GENERIC = {k: v["label"] for k, v in yaml.safe_load(files["schema/categories.yaml"].decode("utf-8"))["categories"].items()}
    return _GENERIC


def map_category_jgd(cat, gt):
    """LLM の品目 → (正解の鍵のリスト, 方法)"""
    keys = sorted({k for c in gt["courses"] for k in c["by_category"]})
    tax = gt.get("taxonomy") or {}
    ov = tax.get("overrides") or {}
    lab = {}
    for k in keys:
        for name in {(ov.get(k) or {}).get("label"), _generic_labels().get(k)} - {None}:
            lab.setdefault(loose(name), set()).add(k)
    groups = {loose(g["label"]): {m for m in g.get("members") or [] if m in keys} for g in tax.get("groups") or []}
    for method, key in (("label", loose(cat["label"])), ("label_paren", loose(strip_paren(cat["label"])))):
        if key in lab:
            return sorted(lab[key]), method
        if key in groups and groups[key]:
            return sorted(groups[key]), method + "_group"
    co = set(cat.get("coarse") or ())
    got = {k for k in co if k in keys}
    if "paper_cloth" in keys and {"paper", "cloth"} <= co:
        got.add("paper_cloth")
    if "paper_cloth" in co:
        got |= {k for k in ("paper", "cloth") if k in keys}
    if got:
        return sorted(got), "llm_coarse"
    parts = [p for p in re.split(r"[・、/／]", nk(strip_paren(cat["label"]))) if p]
    if len(parts) > 1 and all(loose(p) in lab for p in parts):
        return sorted(set().union(*(lab[loose(p)] for p in parts))), "label_parts"
    return [], "unmapped"


def score_jgd(lg, items):
    """items: [(計画の資料, 答え (dict), 広げた答え)] → 採点"""
    gt = jgd.load_gt(lg)
    if gt is None:
        return {"error": f"正解が無い ({lg})。jgd.py fetch・convert を先に"}
    split = jgd.split_names(gt)
    gt_keys = sorted({k for c in gt["courses"] for k in c["by_category"]})
    src_courses = collections.defaultdict(set)
    for c in gt["courses"]:
        for s in c["source"].values():
            for u in (s.get("source_url"), s.get("pdf_url")):
                if u:
                    src_courses[u].add(c["course"])
    pseudo = {"districts": []}
    series = {}                                              # 地区の id → [(品目の鍵, 系列, 期間)]
    cat_map, dist_label = {}, {}
    for doc, ans, ex in items:
        if not ans or not ex or not ex.get("period"):
            continue
        cats = {c["id"]: c for c in ans["categories"]}
        for c in ans["categories"]:
            cat_map[(doc["doc_id"], c["id"])] = (c["label"], *map_category_jgd(c, gt))
        for d in ans["districts"]:
            did = f"{doc['doc_id']}:{d['id']}"
            dist_label[did] = d["label"]
            pseudo["districts"].append({"district_id": did, "label": d["label"], "members": [{"text": m} for m in d["members"]]})
        for s in ex["series"]:
            keys = cat_map[(doc["doc_id"], s["category"])][1] if s["category"] in cats else []
            series.setdefault(f"{doc['doc_id']}:{s['district']}", []).append((keys, s, ex["period"]))
    amap = jgd.area_map(gt, pseudo)
    course_d = collections.defaultdict(dict)                 # コース → {地区の id: 方法}
    for (course, name), ds in amap.items():
        if nk(name) in split:
            continue
        for d in ds:
            course_d[course].setdefault(d, "name")
    for doc, ans, ex in items:                                # その資料が 1 コースだけの出典で、答えの地区が 1 つ
        cs = src_courses.get(doc.get("url"), set())
        if ans and len(cs) == 1 and len(ans["districts"]) == 1:
            course_d[next(iter(cs))].setdefault(f"{doc['doc_id']}:{ans['districts'][0]['id']}", "source_doc")
    units = []
    for co in gt["courses"]:
        win = jgd.gt_window(gt, co)
        ds = course_d.get(co["course"], {})
        for k in gt_keys:
            gtd = set(co["by_category"].get(k, [])) & win
            per = []
            for did, how in sorted(ds.items()):
                ss = [(s, p) for keys, s, p in series.get(did, []) if k in keys]
                if not ss:
                    if gtd:
                        per.append({"district": did, "how": how, "category_unmapped": True, **_diff(set(), gtd), "answerable": False})
                    continue
                w = win & set(_daterange(ss[0][1]["from"], ss[0][1]["to"]))
                ours = set().union(*(set(s["dates"]) for s, _ in ss)) & w
                per.append({"district": did, "how": how, "categories": sorted({s["category"] for s, _ in ss}),
                            **_diff(ours, gtd & w), "answerable": all(s["answerable"] for s, _ in ss),
                            "why": sorted({y for s, _ in ss for y in s["why"]})})
            if not gtd and not any(p.get("wrong") for p in per):
                continue
            u = {"course": co["course"], "course_name": co.get("course_name_ja"), "key": k, "gt_n": len(gtd),
                 "unmapped": not ds, "per_district": per}
            if not ds:
                u.update(exact=False, wrong_n=0, missing_n=len(gtd), answered=False)
            else:
                u.update(exact=all(p["exact"] for p in per) and bool(per), wrong_n=sum(len(p["wrong"]) for p in per),
                         missing_n=sum(len(p["missing"]) for p in per), answered=bool(per) and all(p["answerable"] for p in per))
            units.append(u)
    used = {d for ds in course_d.values() for d in ds}
    return {"answer_key": jgd.ATTRIBUTION, "lg_code": lg, "summary": _summary(units),
            "courses": len(gt["courses"]), "courses_mapped": sum(1 for c in gt["courses"] if course_d.get(c["course"])),
            "district_map": {c: ds for c, ds in course_d.items()},
            "districts_unused": sorted({d for d in dist_label} - used),
            "category_map": [{"doc": k[0], "id": k[1], "label": v[0], "keys": v[1], "how": v[2]} for k, v in cat_map.items()],
            "units": [_slim(u) for u in units]}


def drop_ours(o):
    """採点から、モデルの日付の全部 (ours) を除く (scores/ に書くとき。大きいので)"""
    if isinstance(o, dict):
        return {k: drop_ours(v) for k, v in o.items() if k != "ours"}
    if isinstance(o, list):
        return [drop_ours(v) for v in o]
    return o


def _slim(u, k=6):
    """結果に書く日付の数を絞る"""
    u = dict(u)
    if "per_district" in u:
        u["per_district"] = [{**p, "wrong": p["wrong"][:k], "missing": p["missing"][:k], "wrong_n": len(p["wrong"]),
                              "missing_n": len(p["missing"])} for p in u["per_district"]]
    for x in ("wrong", "missing"):
        if isinstance(u.get(x), list):
            u[x + "_n_all"], u[x] = len(u[x]), u[x][:k]
    return u


# ---- 女川 ----
_ONA = None


def _onagawa():
    global _ONA
    if _ONA is None:
        _ONA = read_json(ROOT / "out" / "onagawa_reading.json")
    return _ONA


ENOSHIMA = "burnable|色分けなし（江島地区）"


def score_onagawa(month, ans, ex):
    """月 1 つの答え → マスごとの比べ。江島 (色分けなし) のマスは、正解に印字の日付が無いので比べない"""
    rd = _onagawa()
    st, cells = rd["structure"], rd["final"][month]["cells"]
    t_dist = {}                                               # 正解の地区 (色・行) → 行政区の集まり
    for g in st["burnable_groups"]:
        t_dist[("burnable", g["color"])] = {loose(x) for x in g["gyoseiku"]}
    for r in st["resource_rows"]:
        t_dist[("resource", r["label"])] = {loose(x) for x in r["gyoseiku"]}
    t_cat = {loose(st["burnable_label"]): ("burnable", None)}
    for c in st["resource_columns"]:
        t_cat[loose(c["label"])] = ("resource", c["label"])
    # 正解の地区 → LLM の地区: label が同じ → members の集まりが同じ → members が正解の地区に含まれる地区たちで、和がちょうど正解の地区
    # (members_cover。色の地区を資源の表の行ごとに書いた答え)。members_cover の地区は 1 つずつ比べ、どれも合えば正しい
    lm = {d["id"]: {loose(x) for x in d["members"]} for d in ans["districts"]}
    tmap = {}
    for k, v in t_dist.items():
        hit = [(d["id"], "label") for d in ans["districts"] if loose(d["label"]) == loose(k[1])]
        if not hit:
            hit = [(i, "members") for i, ms in lm.items() if ms and ms == v]
        if not hit:
            sub = [i for i, ms in lm.items() if ms and ms <= v]
            if sub and set().union(*(lm[i] for i in sub)) == v:
                hit = [(i, "members_cover") for i in sub]
        tmap[k] = hit
    cmap = {c["id"]: t_cat.get(loose(c["label"])) for c in ans["categories"]}
    units = []
    for key, want in cells.items():
        if key == ENOSHIMA:
            continue
        parts = key.split("|")
        part, dlabel, clabel = parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
        gt = {int(x) for x in want}
        per = []
        for did, how in tmap.get((part, dlabel), []):
            ss = [s for s in ex["series"] if s["district"] == did and cmap.get(s["category"]) == (part, clabel)]
            ours = {int(x[8:]) for s in ss for x in s["dates"] if x[:7] == month}
            per.append({"district": did, "how": how, "series": [s["category"] for s in ss], **_diff(ours, gt),
                        "answerable": bool(ss) and all(s["answerable"] for s in ss),
                        "why": sorted({w for s in ss for w in s["why"]} | (set() if ss else {"no_series"}))})
        if not per or not any(p["series"] for p in per):
            units.append({"cell": key, "unmapped": True, "gt_n": len(gt), "exact": False, "wrong_n": 0, "missing_n": len(gt),
                          "answered": False, "per_district": per})
            continue
        units.append({"cell": key, "gt_n": len(gt), "exact": all(p["exact"] for p in per),
                      "wrong_n": sum(len(p["wrong"]) for p in per), "missing_n": sum(len(p["missing"]) for p in per),
                      "answered": all(p["answerable"] for p in per), "per_district": per})
    return {"answer_key": "out/onagawa_reading.json の final (O1 の 324 マス。Opus 2 人の読みが一致したもの)", "month": month,
            "summary": _summary(units), "district_map": {"|".join(k): [list(h) for h in v] for k, v in tmap.items()},
            "category_map": {k: list(v) if v else None for k, v in cmap.items()}, "skipped": [ENOSHIMA + " (正解に印字の日付が無い)"],
            "units": units}


# ---- 金沢 ----
_KZ = None


def legacy_occurrences(s):
    """金沢の v0.1 の系列 (out/kanazawa_schedules.json) → 日付の集まり (dates があればそれ、無ければ RRULE・segments を広げる)。
    resolve.Kanazawa._occurrences (MCP サーバーの答え) と同じ広げ方"""
    from dateutil.rrule import rrulestr
    if s.get("dates"):
        return {dt.date.fromisoformat(x) for x in s["dates"]}
    lo, hi = dt.date.fromisoformat(s["valid_from"]), dt.date.fromisoformat(s["valid_to"])
    if s.get("rule_kind") == "rrule":
        st = lo + dt.timedelta(days=7 if "PHASE" in s["rrule"] else 0)
        got = {x.date() for x in rrulestr(s["rrule"].replace(";PHASE=+7d", ""), dtstart=dt.datetime.combine(st, dt.time()))
               .between(dt.datetime.combine(lo, dt.time()), dt.datetime.combine(hi, dt.time()), inc=True)}
        return (got - set(map(dt.date.fromisoformat, s.get("exdates", [])))) | set(map(dt.date.fromisoformat, s.get("rdates", [])))
    got = set()
    for sg in s.get("segments", []):
        got |= {x.date() for x in rrulestr(sg["rrule"] + ";UNTIL=" + sg["until"].replace("-", "") + "T235959",
                                           dtstart=dt.datetime.fromisoformat(sg["dtstart"]))}
        got -= set(map(dt.date.fromisoformat, sg["exdates"]))
    return got


def _kanazawa():
    global _KZ
    if _KZ is None:
        _KZ = read_json(ROOT / "out" / "kanazawa_schedules.json")
    return _KZ


def score_kanazawa(district, ans, ex):
    """地区 1 つの答え → 品目ごとの 2026 年度の日付 (正解は v0.1 の系列。legacy_occurrences で広げる)"""
    truth = [s for s in _kanazawa()["schedules"] if s["district"] == district]
    dids = [d["id"] for d in ans["districts"] if loose(d["label"]) == loose(district)]
    how = "label"
    if not dids:
        dids = [d["id"] for d in ans["districts"] if loose(strip_paren(d["label"])) == loose(district)]
        how = "label_paren"
    cmap = {}
    for c in ans["categories"]:
        hit = [t["category"] for t in truth if loose(c["label"]) in {loose(t["official_label"]), loose(t["category_label"])}]
        if len(hit) == 1:
            cmap[c["id"]] = (hit[0], "label")
    units = []
    for t in truth:
        gt = {d.isoformat() for d in legacy_occurrences(t)}
        ss = [s for s in ex["series"] if s["district"] in dids and (cmap.get(s["category"]) or (None,))[0] == t["category"]]
        if not ss:
            units.append({"category": t["category"], "label": t["official_label"], "unmapped": True, "gt_n": len(gt), "exact": False,
                          "wrong_n": 0, "missing_n": len(gt), "answered": False})
            continue
        w = set(_daterange(max(ex["period"]["from"], t["valid_from"]), min(ex["period"]["to"], t["valid_to"])))
        ours = {x for s in ss for x in s["dates"]} & w
        df = _diff(ours, gt & w)
        units.append({"category": t["category"], "label": t["official_label"], "gt_n": len(gt & w), **df,
                      "wrong_n": len(df["wrong"]), "missing_n": len(df["missing"]), "answered": all(s["answerable"] for s in ss),
                      "why": sorted({w_ for s in ss for w_ in s["why"]})})
    return {"answer_key": "out/kanazawa_schedules.json (K1 の 355 系列のうち、この地区の 5 系列)", "district": district,
            "summary": _summary(units), "district_map": {"ids": dids, "how": how},
            "category_map": {k: list(v) for k, v in cmap.items()}, "units": [_slim(u) for u in units]}
