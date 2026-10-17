#!/usr/bin/env python3
"""resolve.py の地区判定の評価

  python split_blocks.py --holdout          # 住居表示住所の 20% を無作為に除いて区域を作り直す
  python split_blocks.py --holdout-blocks [--seed N]  # 街区の 10% を丸ごと除いて区域を作り直す (314 は BLIND_M を決めたシード)
  python eval_resolve.py [--mode random|blocks] [--seed 314,1,2] [--near 10,30] [--blind 60]
  python eval_resolve.py --mode parcels        # 住居表示の無い筆 (ABR 地番代表点) の 10% を地番の索引から除いて引く
  python eval_resolve.py --mode parcels_split  # 地区が分かれる町の筆の 25% を除いて引く (利用者の地番に位置が無い場合)
  python eval_resolve.py --mode parcels_koaza  # 町+小字の 10% を丸ごと除いて引く (ABR の位置の欠けは小字ごと)
  python eval_resolve.py --jitter 3000,10,10   # 本番のモデルで、住所 3000 件 × 10 点を各住所から 10m 以内に打つ (GPS のずれ)
  python eval_resolve.py --jitter 3000,5,30 [--accuracy]   # 30m 以内のずれ。--accuracy で accuracy_m=30 (確率 0.99 = 必ずこの円の中) を渡す
  python eval_resolve.py --gauss 3000,5,30,0.68 [--no-accuracy]   # 端末の精度の決まりどおりの正規分布の誤差 (Android 0.68 / ブラウザ 0.95)
  python eval_resolve.py ... --set PARCEL_NEAR_M=80   # resolve の定数を変えて試す
  → out/kanazawa_resolve_eval[_blocks|_parcels|_jitter].json

ホールドアウト: 除いた住所を「利用者の位置」として引く。resolve 側はその住所を住所索引からも区域ポリゴンからも知らない。
正解はその住所 (町名・街区・住居番号) を区域表の行に当てた地区。地番は (町名・小字・本番) を kzrules.match_parcel で区域表の行に当てた地区
(resolve と同じ読み方なので、表の読み違いはこの評価では見えない)。区域表で決まらない住所 (表に無い町など) は別に数え、
そこで確定 (ok) を返したら誤り扱い (公式の答えが無いのに言い切った) にする: ok_precision は表で決まる住所だけ、
ok_precision_strict は決まらない住所での ok も誤りに数えた値。
"""
import collections, json, random, re, sys, time
import numpy as np, pandas as pd, geopandas as gpd
import resolve
from kzrules import load_rules, load_jukyo, match_row, load_parcels, table_town, match_parcel, mark_stale_rows

sys.stdout.reconfigure(encoding="utf-8")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
mode = arg("--mode", "random")
pts = load_jukyo()
_, by_town = load_rules()
mark_stale_rows(by_town)        # resolve と同じ読み方 (今の地番に無い番地範囲の行がある小字では「別記以外」を言い切らない)

def truth_of(r):
    rs = by_town.get(r.ntown)
    if not rs: return "NOT_IN_TABLE"
    if np.isnan(r.blk_i) or np.isnan(r.num_i): return "UNDECIDED"
    row = match_row(rs, int(r.blk_i), int(r.num_i))
    return row["district"] if row else "UNDECIDED"

def score(k, queries, accuracy_m=None, confidence=None):
    """queries: [(lat, lng, truth, label)] → 集計"""
    c, conf_ok, wrong, wrong_town = collections.Counter(), collections.Counter(), [], collections.Counter()
    for lat, lng, t, label in queries:
        loc = k.locate(lat, lng, accuracy_m, confidence)
        if loc is None:     # 国勢調査の町の外 (市外扱い)。表で決まる住所なら「候補に正解が無い」に数える
            c[f"{t.lower()}:out_of_coverage" if t in ("NOT_IN_TABLE", "UNDECIDED") else "out_of_coverage_truth_absent"] += 1; continue
        names = [x["collection_district"] for x in loc["candidates"]]
        if t in ("NOT_IN_TABLE", "UNDECIDED"):
            c[f"{t.lower()}:{loc['status']}"] += 1
            if loc["status"] == "ok":
                wrong.append([label, "(表で決まらない住所)", loc["collection_district"], t, loc["method"], loc["district_confidence"]])
                wrong_town[re.match(r"\D+", label)[0]] += 1
            continue
        if loc["status"] == "ok":
            ok = loc["collection_district"] == t
            c["ok_correct" if ok else ("ok_wrong_truth_in_candidates" if t in names else "ok_wrong_truth_absent")] += 1
            conf_ok[(loc["district_confidence"], ok)] += 1
            if not ok:
                wrong.append([label, loc["collection_district"], t, loc["method"], loc["district_confidence"]])
                wrong_town[re.match(r"\D+", label)[0]] += 1
        else:
            c[f"{loc['status']}_{'truth_in' if t in names else 'truth_absent'}"] += 1
    decided = sum(v for k_, v in c.items() if ":" not in k_)
    okn = c["ok_correct"] + c["ok_wrong_truth_in_candidates"] + c["ok_wrong_truth_absent"]
    ok_undecided = sum(v for k_, v in c.items() if k_.endswith(":ok"))
    return {"decided_by_table": decided, "counts": dict(sorted(c.items())),
            "ok_rate": round(okn / max(1, decided), 4), "ok_precision": round(c["ok_correct"] / max(1, okn), 5),
            "ok_on_addresses_not_decided_by_table": ok_undecided,
            "ok_precision_strict": round(c["ok_correct"] / max(1, okn + ok_undecided), 5),
            "by_confidence": {f"{cf}": {"correct": conf_ok[(cf, True)], "wrong": conf_ok[(cf, False)]} for cf in sorted({x for x, _ in conf_ok})},
            "wrong_or_undecided_ok_by_town": dict(wrong_town.most_common()), "wrong_examples": wrong[:30]}

for kv in [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--set"]:   # 例: --set PARCEL_NEAR_M=80 (resolve の定数を変えて試す)
    name, val = kv.split("=")
    setattr(resolve, name, int(val))
t0 = time.time()
if "--jitter" in sys.argv:
    n, m, rad = (int(x) for x in arg("--jitter", "3000,10,10").split(","))
    k = resolve.Kanazawa()
    rnd = random.Random(20261017)
    sample = pts.sample(n, random_state=20261017)
    xy = gpd.GeoSeries(gpd.points_from_xy(sample.lon.astype(float), sample.lat.astype(float)), crs=6668).to_crs(6675)
    qs = []
    for (i, r), g in zip(sample.iterrows(), xy):
        t = truth_of(r)
        for _ in range(m):
            a, d = rnd.uniform(0, 2 * np.pi), rad * np.sqrt(rnd.random())
            ll = gpd.GeoSeries([gpd.points_from_xy([g.x + d * np.cos(a)], [g.y + d * np.sin(a)])[0]], crs=6675).to_crs(4326).iloc[0]
            qs.append((ll.y, ll.x, t, f"{r.town}{r.blk}-{r.num}"))
    acc = rad if "--accuracy" in sys.argv else None       # --accuracy: 誤差の半径を accuracy_m (確率 0.99 = 必ずこの円の中) として渡す
    rep = {"mode": f"jitter {n}x{m} within {rad}m (production model)" + (f", accuracy_m={acc} (confidence 0.99)" if acc else ""),
           "queries": len(qs), **score(k, qs, acc, 0.99)}
    out = "out/kanazawa_resolve_eval_jitter.json" if rad == 10 and not acc else f"out/kanazawa_resolve_eval_jitter{rad}{'_acc' if acc else ''}.json"
elif "--gauss" in sys.argv:
    # 端末の精度の決まりどおりの誤差: 2次元正規分布で、半径 A に確率 CONF で入る σ。accuracy_m=A, accuracy_confidence=CONF を渡す
    n, m, A, conf = arg("--gauss", "3000,5,30,0.68").split(",")
    n, m, A, conf = int(n), int(m), float(A), float(conf)
    sigma = A / np.sqrt(-2 * np.log(1 - conf))
    k = resolve.Kanazawa()
    rnd = np.random.default_rng(20261017)
    sample = pts.sample(n, random_state=20261017)
    xy = gpd.GeoSeries(gpd.points_from_xy(sample.lon.astype(float), sample.lat.astype(float)), crs=6668).to_crs(6675)
    off = rnd.normal(0, sigma, size=(n, m, 2))
    gx = np.repeat(xy.x.values[:, None], m, 1) + off[:, :, 0]
    gy = np.repeat(xy.y.values[:, None], m, 1) + off[:, :, 1]
    ll = gpd.GeoSeries(gpd.points_from_xy(gx.ravel(), gy.ravel()), crs=6675).to_crs(4326)
    truths = [(truth_of(r), f"{r.town}{r.blk}-{r.num}") for _, r in sample.iterrows()]
    qs = [(g.y, g.x, *truths[j // m]) for j, g in enumerate(ll)]
    no_acc = "--no-accuracy" in sys.argv
    rep = {"mode": f"gauss {n}x{m}, accuracy_m={A} at confidence {conf} (sigma {sigma:.1f}m)" + (", accuracy_m NOT passed" if no_acc else ""),
           "queries": len(qs), "outside_reported_radius": round(float(np.mean(np.hypot(off[:, :, 0], off[:, :, 1]) > A)), 4),
           **score(k, qs, None if no_acc else A, conf)}
    out = f"out/kanazawa_resolve_eval_gauss{int(A)}_{int(round(conf * 100))}{'_noacc' if no_acc else ''}.json"
elif mode in ("parcels", "parcels_split", "parcels_koaza"):
    seed = int(arg("--seed", "20261017"))         # 報告は 20261017。PARCEL_NEAR_M は別のシード (7) で決めた
    pc = load_parcels()
    if mode == "parcels":
        hold = sorted(int(i) for i in pc.sample(frac=0.1, random_state=seed).index)
    elif mode == "parcels_koaza":   # 小字 (町+小字) の 10% を丸ごと外す。ABR の位置の欠けは小字ごとに起きる (欠けの 87% は小字まるごと)
        g = pc[["ntown", "koaza"]].drop_duplicates().sample(frac=0.1, random_state=5)
        hold = sorted(int(i) for i in pc[pc.set_index(["ntown", "koaza"]).index.isin(pd.MultiIndex.from_frame(g))].index)
    else:       # 地区が分かれる町の筆の 25% を外す (利用者の地番に位置が無い場合。住居表示の無い筆の 55% は ABR に位置が無い)
        split = pc[[bool(table_town(t, by_town)) and len({r["district"] for r in by_town[table_town(t, by_town)]}) > 1 for t in pc.ntown]]
        hold = sorted(int(i) for i in split.sample(frac=0.25, random_state=11).index)
    json.dump(hold, open(f"out/kanazawa_holdout_{mode}{'' if seed == 20261017 else f'_s{seed}'}_ids.json", "w"))
    k = resolve.Kanazawa(exclude_parcels=hold)
    def ptruth(r):
        tt = table_town(r.ntown, by_town)
        if not tt: return "NOT_IN_TABLE"
        row = match_parcel(by_town[tt], r.koaza, None if r.num1 != r.num1 else int(r.num1))
        return row["district"] if row else "UNDECIDED"
    qs = [(float(r.rep_lat), float(r.rep_lon), ptruth(r), f"{r.town}{r.koaza}{r.prc_num1}") for r in pc.loc[hold].itertuples()]
    rep = {"mode": {"parcels": "parcel holdout (ABR 地番代表点 10%, 住居表示の無い筆)", "parcels_split": "split-town parcel holdout (地区が分かれる町の筆 25%, seed 11)",
                    "parcels_koaza": "koaza-group parcel holdout (町+小字の 10% を丸ごと, seed 5)"}[mode],
           "seed": {"parcels": seed, "parcels_split": 11, "parcels_koaza": 5}[mode], "holdout_parcels": len(hold), "runs": []}
    for pn in [int(x) for x in arg("--pnear", str(resolve.PARCEL_NEAR_M)).split(",")]:
        resolve.PARCEL_NEAR_M = pn
        rep["runs"].append({"PARCEL_NEAR_M": pn, **score(k, qs)})
    out = (f"out/kanazawa_resolve_eval_{mode}.json" if mode != "parcels" else
           "out/kanazawa_resolve_eval_parcels.json" if seed == 20261017 else f"out/kanazawa_resolve_eval_parcels_s{seed}.json")
else:
    seeds = [int(x) for x in arg("--seed", "314").split(",")] if mode == "blocks" else [None]
    rep = {"mode": f"full holdout ({mode})", "runs": []}
    for seed in seeds:
        suffix = "" if mode != "blocks" else ("_blocks" if seed == 314 else f"_blocks_s{seed}")
        hold = json.load(open(f"out/kanazawa_holdout{suffix}_ids.json"))
        hp = pts.loc[hold]
        k = resolve.Kanazawa(parts_path=f"out/kanazawa_town_parts_holdout{suffix}.geojson", exclude_addresses=hold)
        qs = [(float(r.lat), float(r.lon), truth_of(r), f"{r.town}{r.blk}-{r.num}") for _, r in hp.iterrows()]
        for near in [int(x) for x in arg("--near", str(resolve.NEAR_M)).split(",")]:
            for blind in [int(x) for x in arg("--blind", str(resolve.BLIND_M)).split(",")]:
                resolve.NEAR_M, resolve.BLIND_M = near, blind
                rep["runs"].append({"seed": seed, "holdout_addresses": len(hold), "NEAR_M": near, "BLIND_M": blind, **score(k, qs)})
    if len(seeds) > 1:     # シードを合わせた値
        tot = collections.Counter()
        for r in rep["runs"]: tot.update(r["counts"])
        okn = tot["ok_correct"] + tot["ok_wrong_truth_in_candidates"] + tot["ok_wrong_truth_absent"]
        dec = sum(v for k_, v in tot.items() if ":" not in k_)
        und = sum(v for k_, v in tot.items() if k_.endswith(":ok"))
        wt = collections.Counter()
        for r in rep["runs"]: wt.update(r["wrong_or_undecided_ok_by_town"])
        rep["pooled"] = {"seeds": seeds, "wrong_or_undecided_ok_by_town": dict(wt.most_common()), "decided_by_table": dec, "counts": dict(sorted(tot.items())), "ok_rate": round(okn / max(1, dec), 4),
                         "ok_precision": round(tot["ok_correct"] / max(1, okn), 5), "ok_on_addresses_not_decided_by_table": und,
                         "ok_precision_strict": round(tot["ok_correct"] / max(1, okn + und), 5)}
    out = f"out/kanazawa_resolve_eval{'_blocks' if mode == 'blocks' else ''}.json"
rep["seconds"] = round(time.time() - t0)
json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for r in rep.get("runs", [rep]):
    print(json.dumps({k_: v for k_, v in r.items() if k_ not in ("wrong_examples", "runs", "pooled")}, ensure_ascii=False))
    for w in r["wrong_examples"][:10]: print("   ", w)
if "pooled" in rep: print("pooled", json.dumps(rep["pooled"], ensure_ascii=False))
