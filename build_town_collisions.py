#!/usr/bin/env python3
"""金沢市の町名のうち、ほかの市区町村にも同じ町名があるもの → out/kanazawa_town_collisions.json

  python build_town_collisions.py      (data/abr/mt_town_fullset_all.csv が要る)

住所に市町村名が無いとき、この町名なら金沢市と決めつけない (押野2丁目は野々市市にも、本町1丁目は全国に141か所ある)
"""
import collections, json
import pandas as pd
from townnorm import norm_town

import resolve, kzaddress
ours = kzaddress.AddressIndex(resolve.Kanazawa()).known        # 住所の読み取りで町名として当てる名前すべて (区域表・地理院・ABR 地番・国勢調査の町名)
df = pd.read_csv("data/abr/mt_town_fullset_all.csv", dtype=str, usecols=["lg_code", "pref", "county", "city", "ward", "oaza_cho", "chome"]).fillna("")
df = df[df.lg_code != "172014"]
df["ntown"] = (df.oaza_cho + df.chome).map(norm_town)
df = df[df.ntown.isin(ours)]
where = collections.defaultdict(set)
for t, p, c, ci, w in zip(df.ntown, df.pref, df.county, df.city, df.ward):
    where[t].add(p + c + ci + w)
out = {t: {"count": len(v), "examples": sorted(v)[:5]} for t, v in sorted(where.items())}
json.dump(out, open("out/kanazawa_town_collisions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(len(out), "town names also exist elsewhere;", sum(1 for v in out.values() if any(e.startswith("石川県") for e in v["examples"])), "of them in Ishikawa (by example)")
