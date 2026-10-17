#!/usr/bin/env python3
"""金沢市: 区域表(rules) × 町丁目ポリゴン(e-Stat) × 住居表示点(GSI) → 収集地区ポリゴン

  1. 町丁目まるごとのルール → 小地域ポリゴンをそのまま採用
  2. 街区/番地で割れる町 → 住居表示点にルールを当て、点のボロノイ∩町ポリゴンを地区ごとにunion
  3. 字・地番型 / 解析不能 → unresolved として残す (登記所備付地図で別途)
出力: out/kanazawa_zones.geojson, out/kanazawa_report.json
"""
import csv, json, re, sys, time, collections
import pandas as pd, geopandas as gpd, shapely
from shapely.geometry import MultiPoint
from shapely.ops import unary_union

sys.stdout.reconfigure(encoding="utf-8")
T0 = time.time()
from townnorm import norm_town

from kzrules import load_rules, load_jukyo, match

# ---- 入力 ----
rules, by_town = load_rules()

poly = gpd.read_file("data/estat_17201/r2ka17201.shp")
poly = poly[poly["HCODE"] == 8101]                         # 8154 = 水面等を除外
poly["ntown"] = poly["S_NAME"].map(norm_town)
poly = poly.dissolve(by="ntown", as_index=False)[["ntown", "geometry"]].to_crs(6675)
P = dict(zip(poly["ntown"], poly["geometry"]))

pts = load_jukyo()
gpts = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.lon.astype(float), pts.lat.astype(float)), crs=6668).to_crs(6675)
HOLD, SUFFIX = None, ""
if "--holdout" in sys.argv:                 # 住居表示住所の 20% を無作為に除く
    HOLD = gpts.sample(frac=0.2, random_state=42)
elif "--holdout-blocks" in sys.argv:        # 街区 (町名+街区) の 10% を丸ごと除く (利用者の街区に住所が1つも無い場合の評価)。--seed N でシードを変える
    SEED = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 314   # 314 は BLIND_M を決めたときのシード。報告は複数シードで
    keys = gpts[["ntown", "blk"]].drop_duplicates().sample(frac=0.1, random_state=SEED)
    HOLD = gpts[gpts.set_index(["ntown", "blk"]).index.isin(pd.MultiIndex.from_frame(keys))]
    SUFFIX = "_blocks" if SEED == 314 else f"_blocks_s{SEED}"
if HOLD is not None:
    gpts = gpts.drop(HOLD.index)
PTS = {t: g for t, g in gpts.groupby("ntown")}
print(f"load {time.time()-T0:.1f}s  polys={len(P)} points={len(gpts)} towns_in_points={len(PTS)}")


feats, rep = [], collections.Counter()
missing_poly, unresolved = [], []
for town, rs in by_town.items():
    kinds = {r["kind"] for r in rs}
    geom = P.get(town)
    if geom is None:
        parent = next((k for k in P if town.startswith(k) and k != town), None)
        children = [k for k in P if k.startswith(town) and k != town]
        reason = ("census_outdated(parent=%s)" % parent) if parent else \
                 ("chome_split_in_census" if children else "not_in_census")
        missing_poly.append({"town": town, "reason": reason, "kinds": sorted(kinds)})
        rep["no_poly:" + reason.split("(")[0]] += 1; continue
    if kinds == {"town"}:
        dists = {r["district"] for r in rs}
        feats.append({"town": town, "district": rs[0]["district"], "method": "chome", "geometry": geom})
        rep["chome"] += 1
        if len(dists) > 1: rep["chome_conflict"] += 1
        continue
    if kinds & {"aza", "unparsed"} or town not in PTS:
        unresolved.append({"town": town, "reason": "aza/unparsed" if kinds & {"aza", "unparsed"} else "no_jukyo_points",
                           "districts": sorted({r["district"] for r in rs})})
        rep["unresolved"] += 1; continue
    g = PTS[town].copy()
    g["district"] = [match(rs, b, n) if pd.notna(b) and pd.notna(n) else None for b, n in zip(g.blk_i, g.num_i)]
    rep["pts_total"] += len(g); rep["pts_unmatched"] += int(g["district"].isna().sum())
    g = g[g["district"].notna()].drop_duplicates(subset="geometry")
    if g.empty:
        unresolved.append({"town": town, "reason": "no_match"}); rep["unresolved"] += 1; continue
    if g["district"].nunique() == 1:
        feats.append({"town": town, "district": g["district"].iat[0], "method": "points_single", "geometry": geom})
        rep["split_single"] += 1; continue
    cells = shapely.voronoi_polygons(MultiPoint(list(g.geometry)), extend_to=geom.buffer(500))
    vc = gpd.GeoDataFrame(geometry=list(cells.geoms), crs=6675)
    vc = gpd.sjoin(vc, g[["district", "geometry"]], predicate="contains", how="inner")
    vc["geometry"] = vc.geometry.intersection(geom)
    for d, grp in vc.groupby("district"):
        feats.append({"town": town, "district": d, "method": "voronoi", "geometry": unary_union(list(grp.geometry))})
    rep["split_voronoi"] += 1

out = gpd.GeoDataFrame(feats, crs=6675)
zones = out.dissolve(by="district", as_index=False, aggfunc={"town": lambda s: sorted(set(s)), "method": lambda s: sorted(set(s))}) \
    if False else out.dissolve(by="district", as_index=False)[["district", "geometry"]]
city = unary_union(list(poly.geometry))
covered = unary_union(list(out.geometry))
overlap = sum(a.area for a in out.geometry) - covered.area
rep.update({"towns_in_rules": len(by_town), "districts_out": len(zones),
            "area_coverage": round(covered.area / city.area, 4),
            "overlap_m2": round(overlap), "elapsed_s": round(time.time() - T0, 1)})
if HOLD is None:        # --holdout は 80% の点で作った区域なので本番の成果物は書かない
    zones.to_crs(4326).to_file("out/kanazawa_zones.geojson", driver="GeoJSON")
    out.to_crs(4326).to_file("out/kanazawa_town_parts.geojson", driver="GeoJSON")
    json.dump({"stats": rep, "missing_poly": missing_poly, "unresolved": unresolved},
              open("out/kanazawa_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
else:
    H = HOLD[HOLD["ntown"].isin({t for t, rs in by_town.items() if {r['kind'] for r in rs} - {'town'}})].copy()
    H["truth"] = [match(by_town[t], b, n) if pd.notna(b) and pd.notna(n) else None
                  for t, b, n in zip(H.ntown, H.blk_i, H.num_i)]
    H = H[H.truth.notna()]
    j = gpd.sjoin(H, zones.rename(columns={"district": "pred"}), predicate="within", how="left")
    j = j[~j.index.duplicated()]
    ok = (j.truth == j.pred).sum(); miss = j.pred.isna().sum()
    rep["holdout_n"] = len(j); rep["holdout_acc"] = round(ok / len(j), 4); rep["holdout_nozone"] = int(miss)
    bad = j[(j.truth != j.pred) & j.pred.notna()]
    rep["holdout_wrong_towns"] = bad.ntown.value_counts().head(8).to_dict()
    json.dump(rep, open(f"out/kanazawa_holdout{SUFFIX}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # resolve.py の完全ホールドアウト評価用: 除外した住所の行番号と、それ抜きで作った区域 (git 管理外)
    json.dump(sorted(int(i) for i in HOLD.index), open(f"out/kanazawa_holdout{SUFFIX}_ids.json", "w"))
    out.to_crs(4326).to_file(f"out/kanazawa_town_parts_holdout{SUFFIX}.geojson", driver="GeoJSON")
print(json.dumps(rep, ensure_ascii=False))
print("missing_poly", len(missing_poly))
for m in missing_poly: print("  ", m)
print("unresolved", len(unresolved), [u["town"] for u in unresolved][:30])
