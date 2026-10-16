#!/usr/bin/env python3
"""国勢調査 小地域境界 2000→2005→2010→2015→2020 の差分から、消えた町名と後継町名を面積按分で推定

  消えた町 X (年 y に存在、y+5 に不在) の y 時点ポリゴンと y+5 時点の各町の重なり面積比 → successors
  ABR に履歴が無い (ablt_date 全国0件) ので、旧町字→現町字の機械的な一次ソースとして使う
出力: out/census_lineage.json
"""
import json, sys
import geopandas as gpd
from townnorm import norm_town

sys.stdout.reconfigure(encoding="utf-8")
SRC = [("2000", "data/estat_17201_2000/h12ka17201.shp"), ("2005", "data/estat_17201_2005/h17ka17201.shp"),
       ("2010", "data/estat_17201_2010/h22ka17201.shp"), ("2015", "data/estat_17201_2015/h27ka17201.shp"),
       ("2020", "data/estat_17201/r2ka17201.shp")]

def load(f):
    g = gpd.read_file(f)
    g = g[g.HCODE == 8101].copy()                                  # 8154 = 水面
    g["ntown"] = g.S_NAME.map(norm_town)
    return g.dissolve(by="ntown", as_index=False)[["ntown", "geometry"]].to_crs(6675)

layers = {y: load(f) for y, f in SRC}
events = []
for (y0, _), (y1, _) in zip(SRC, SRC[1:]):
    a, b = layers[y0], layers[y1]
    gone = sorted(set(a.ntown) - set(b.ntown))
    born = sorted(set(b.ntown) - set(a.ntown))
    for t in gone:
        geom = a[a.ntown == t].geometry.iat[0]
        hit = b[b.intersects(geom)].copy()
        hit["share"] = hit.geometry.intersection(geom).area / geom.area
        hit = hit[hit.share > 0.01].sort_values("share", ascending=False)
        events.append({"town": t, "last_seen": y0, "gone_by": y1, "area_m2": round(geom.area),
                       "successors": [{"town": r.ntown, "share": round(r.share, 3), "new": r.ntown in born}
                                      for r in hit.itertuples()]})
    print(f"{y0}->{y1}: gone {len(gone)} born {len(born)}")

json.dump(events, open("out/census_lineage.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for e in events:
    print(e["town"], e["last_seen"], "→", ", ".join(f"{s['town']}({s['share']:.0%}{'*' if s['new'] else ''})" for s in e["successors"][:5]))
