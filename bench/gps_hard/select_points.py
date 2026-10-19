#!/usr/bin/env python3
"""緯度経度だけの難しい問い: 地区が分かれる町の住所の位置 (10) と、地区の境目のそばの住所から GPS の誤差だけずらした位置 (10)

  python bench/gps_hard/select_points.py → docs/gps-hard-questions.json

- 正解は、その住所 (国土地理院の住居表示住所) を区域表の行に当てた地区 (kzrules.match_row)。gomisute-gps の位置の判定 (resolve.locate) は使わない
- 地点は乱数で選ぶ (シード固定)。gomisute-gps の答えを見て選び直すことはしない
- 日付は、正しい地区と、その場所でありうるほかの地区 (同じ町のほかの地区 / 30m 以内の住所の地区) とで、出せる品目が違う日を選ぶ
  (地区を取り違えると答えが変わる日)。問いには「金沢市内」とは書かない
"""
import collections, datetime as dt, json, math, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT); sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np, geopandas as gpd
import resolve

rnd = random.Random(20261018)
k = resolve.Kanazawa()
J = k.jukyo
WD = "月火水木金土日"

truth = {}                                      # 住所の行番号 → 地区 (区域表の行で決まるものだけ)
for i in range(len(J)):
    row, _ = k._addr_district(i)
    if row is not None and not row.get("note"): truth[i] = row["district"]
idx = np.array(sorted(truth))
xy = gpd.GeoSeries(gpd.points_from_xy(J.lon.astype(float).values[idx], J.lat.astype(float).values[idx]), crs=4326).to_crs(6675)
X, Y = xy.x.values, xy.y.values
dist_of = np.array([truth[i] for i in idx])

def labels(district, d):
    day = k.days(district, d, 1)[0]
    return None if day["collections"] is None else sorted(c["official_label"] for c in day["collections"])

def pick_date(district, others):
    """正しい地区で何かを出せて、ほかの候補の地区とは品目が違う日 (2026-10-26〜12-11 の平日)"""
    d = dt.date(2026, 10, 26)
    while d <= dt.date(2026, 12, 11):
        if d.weekday() < 5:
            t = labels(district, d)
            if t and all(labels(o, d) != t for o in others): return d, t
        d += dt.timedelta(days=1)
    return None, None

cases = []
# ---- A: 地区が分かれる町 (区域表で街区・番地によって地区が違う町) の住所の位置そのもの ----
split = sorted(t for t, rs in k.by_town.items() if len({r["district"] for r in rs}) > 1)
by_town = collections.defaultdict(list)
for j, i in enumerate(idx):
    if J.ntown.iloc[i] in split: by_town[J.ntown.iloc[i]].append(j)
towns = sorted(t for t, v in by_town.items() if len({dist_of[j] for j in v}) > 1)     # 住所の上でも地区が分かれている町
rnd.shuffle(towns)
used_towns = set()
for t in towns:
    if sum(c["kind"] == "split_town" for c in cases) >= 10: break
    j = rnd.choice(by_town[t]); i = idx[j]
    others = sorted({dist_of[x] for x in by_town[t]} - {dist_of[j]})
    d, lab = pick_date(dist_of[j], others)
    if not d: continue
    lat, lng = float(J.lat.iloc[i]), float(J.lon.iloc[i])
    cases.append({"kind": "split_town", "lat": round(lat, 6), "lng": round(lng, 6), "jitter_m": 0, "accuracy_m": None,
                  "address": f"{J.town.iloc[i]}{J.blk.iloc[i]}番{J.num.iloc[i]}号", "town": t, "date": str(d),
                  "truth": {"district": dist_of[j], "collections": lab}, "other_districts": others})
    used_towns.add(t)

# ---- B: 地区の境目のそば (30m 以内に別の地区の住所がある住所) から、GPS の誤差だけずらした位置 ----
from scipy.spatial import cKDTree
tree = cKDTree(np.c_[X, Y])
near = []
for j in range(len(idx)):
    nb = tree.query_ball_point([X[j], Y[j]], 30.0)
    od = sorted({dist_of[x] for x in nb} - {dist_of[j]})
    if od: near.append((j, od))
rnd.shuffle(near)
for j, od in near:
    if sum(c["kind"] == "near_boundary" for c in cases) >= 10: break
    i = idx[j]; t = J.ntown.iloc[i]
    if t in used_towns: continue
    d, lab = pick_date(dist_of[j], od)
    if not d: continue
    r, a = rnd.uniform(10, 25), rnd.uniform(0, 2 * math.pi)
    p = gpd.GeoSeries(gpd.points_from_xy([X[j] + r * math.cos(a)], [Y[j] + r * math.sin(a)]), crs=6675).to_crs(4326).iloc[0]
    cases.append({"kind": "near_boundary", "lat": round(p.y, 6), "lng": round(p.x, 6), "jitter_m": round(r, 1), "accuracy_m": 30,
                  "address": f"{J.town.iloc[i]}{J.blk.iloc[i]}番{J.num.iloc[i]}号", "town": t, "date": str(d),
                  "truth": {"district": dist_of[j], "collections": lab}, "other_districts": od})
    used_towns.add(t)

for n, c in enumerate(cases, 1):
    d = dt.date.fromisoformat(c["date"])
    when = f"{d.year}年{d.month}月{d.day}日({WD[d.weekday()]})"
    acc = f" (スマートフォンの位置の精度は約{c['accuracy_m']}m)" if c["accuracy_m"] else ""
    c["id"] = f"g{n:02d}"
    c["question"] = f"今いる場所は 緯度 {c['lat']}、経度 {c['lng']} です{acc}。ここで{when}に出せるごみ (家庭ごみの収集) は何ですか？"
rep = {"experiment": "緯度経度だけの難しい問い 20問 (地区が分かれる町 10・地区の境目のそばで GPS の誤差あり 10)。正解は住所を区域表の行に当てた地区",
       "made_by": "bench/gps_hard/select_points.py (シード 20261018)", "cases": [{k2: c[k2] for k2 in ("id", "kind", "question", "truth", "other_districts", "address", "town", "lat", "lng", "jitter_m", "accuracy_m", "date")} for c in cases]}
json.dump(rep, open("docs/gps-hard-questions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for c in rep["cases"]: print(c["id"], c["kind"], c["address"], c["truth"], "others", c["other_districts"], c["date"], c["jitter_m"])
