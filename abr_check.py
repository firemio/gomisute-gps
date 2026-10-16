#!/usr/bin/env python3
"""金沢 missing_poly 32町 / unresolved 27町 を ABR(町字・地番・住居表示) で引けるか検証

入力: out/kanazawa_report.json, data/abr/*.csv (fetch_abr.py), data/estat_17201 (国勢調査小地域)
出力: out/abr_check.json, out/abr_check.md
"""
import json, re, sys, collections
import pandas as pd, geopandas as gpd
from townnorm import norm_town

sys.stdout.reconfigure(encoding="utf-8")
LG = "172014"
A = "data/abr/"
rep = json.load(open("out/kanazawa_report.json", encoding="utf-8"))

town = pd.read_csv(A + f"mt_town_fullset_city{LG}.csv", dtype=str).fillna("")
tpos = pd.read_csv(A + "mt_town_pos_pref17.csv", dtype=str).fillna("")
tpos = tpos[tpos.lg_code == LG]
blk = pd.read_csv(A + "mt_rsdtdsp_blk_pref17.csv", dtype=str).fillna("")
blk = blk[blk.lg_code == LG]
prc = pd.read_csv(A + f"mt_parcel_city{LG}.csv", dtype=str, usecols=["machiaza_id", "prc_id", "prc_num1", "ablt_date"]).fillna("")
ppos = pd.read_csv(A + f"mt_parcel_pos_city{LG}.csv", dtype=str, usecols=["machiaza_id", "prc_id", "rep_lon"]).fillna("")

# 町字の表記: 大字・町 + 丁目 (小字は別列)。norm_town はごみ側と同じ正規化
town["name"] = town.oaza_cho + town.chome
town["ntown"] = town.name.map(norm_town)
oaza_rows = town[town.machiaza_type.isin(["1", "2"])]          # 1=大字・町 2=丁目 (3=小字)
koaza_by_oaza = town[town.machiaza_type == "3"].groupby("oaza_cho").size()
pos_ids = set(tpos[tpos.rep_lon != ""].machiaza_id)
blk_cnt = blk.groupby("machiaza_id").size()
prc_cnt = prc.groupby("machiaza_id").size()
ppos_cnt = ppos[ppos.rep_lon != ""].groupby("machiaza_id").size()

census = gpd.read_file("data/estat_17201/r2ka17201.shp", ignore_geometry=True)
census_names = sorted(set(census[census.HCODE == 8101].S_NAME.map(norm_town)))

by_n = collections.defaultdict(list)
for r in oaza_rows.itertuples(): by_n[r.ntown].append(r)
# 小字(type=3)行: 「大字+小字」でも引けるように (木ノ新保/四番丁 → 木ノ新保4番丁)
koaza_rows = town[town.machiaza_type == "3"]
by_koaza = collections.defaultdict(list)
for r in koaza_rows.itertuples(): by_koaza[norm_town(r.oaza_cho + r.koaza)].append(r)
# 大字行(type=1)を持たず小字行だけがぶら下がる「幽霊大字」= 住居表示で町名は消えたが未実施地番が旧町名+イロハで残る
phantom = koaza_rows[~koaza_rows.machiaza_id.str[:4].isin(town[town.machiaza_type != "3"].machiaza_id.str[:4])]
phantom_by_oaza = {norm_town(k): sorted(g.koaza) for k, g in phantom.groupby("oaza_cho")}
rules_aza = collections.defaultdict(set)
import csv as _csv
for rr in _csv.DictReader(open("data/kanazawa_rules.csv", encoding="utf-8")):
    if rr["kind"] == "aza":
        m = re.match(r"([ァ-ヴ甲乙丙丁戊己庚辛壬癸]|[一二三四五六七八九十壱弐参]+字|子)", rr["raw_ban"])
        if m: rules_aza[norm_town(rr["town"])].add(m[1])

def abr_info(r):
    mid = r.machiaza_id
    # 大字(type=1)に丁目がぶら下がる場合、地番・小字は大字idに付くことがあるので子孫(先頭4桁一致)も集計
    fam = town[town.machiaza_id.str[:4] == mid[:4]].machiaza_id if r.machiaza_type == "1" else pd.Series([mid])
    return {
        "machiaza_id": mid, "name": r.name, "type": {"1": "oaza_cho", "2": "chome"}[r.machiaza_type],
        "status_flg": r.status_flg, "rsdt_addr_flg": r.rsdt_addr_flg, "wake_num_flg": r.wake_num_flg,
        "efct_date": r.efct_date, "ablt_date": r.ablt_date, "src_code": r.src_code,
        "koaza": int(koaza_by_oaza.get(r.oaza_cho, 0)) if r.machiaza_type == "1" else 0,
        "rep_point": mid in pos_ids,
        "rsdt_blocks": int(sum(blk_cnt.get(i, 0) for i in fam)),
        "parcels": int(sum(prc_cnt.get(i, 0) for i in fam)),
        "parcels_with_pos": int(sum(ppos_cnt.get(i, 0) for i in fam)),
    }

def lookup(t):
    hits = by_n.get(t, [])
    how = "exact"
    if not hits:                                  # 丁目付きで ABR に無ければ親の大字で
        m = re.match(r"(.+?)(\d+)丁目$", t)
        if m and by_n.get(m[1]): hits, how = by_n[m[1]], "parent_oaza"
    if not hits and t in phantom_by_oaza:
        ks = phantom_by_oaza[t]
        return "phantom_oaza", [{"name": t, "type": "phantom_oaza(小字のみ)", "koaza_list": ks,
                                 "rules_aza_letters": sorted(rules_aza.get(t, [])),
                                 "rules_letters_in_abr": sorted(rules_aza.get(t, set()) & set(ks))}]
    if not hits and by_koaza.get(t):
        r = by_koaza[t][0]
        return "oaza+koaza", [{"machiaza_id": r.machiaza_id, "name": r.oaza_cho + "/" + r.koaza, "type": "koaza"}]
    if not hits:
        cands = [k for k in by_n if k.startswith(t) and k != t]
        if cands: hits, how = [x for c in sorted(cands) for x in by_n[c]], "children_chome"
    return how if hits else "none", [abr_info(h) for h in hits]

def census_rel(t):
    if t in census_names: return "in_census"
    kids = [c for c in census_names if c.startswith(t) and c != t]
    m = re.match(r"(.+?)(\d+)丁目$", t)
    if m and m[1] in census_names: return f"census_has_parent:{m[1]}"
    if kids: return "census_children:" + ",".join(kids[:8])
    return "absent"

def classify(how, infos, t):
    """ABR から見た missing_poly の正体"""
    if "区画整理" in t: return "LAND_READJUSTMENT(区画整理の仮称)"
    if how == "none": return "ABR_NOT_FOUND"
    if how == "phantom_oaza": return "OLD_TOWN_REMNANT(旧町名+小字)"
    if how == "oaza+koaza": return "KOAZA_MATCH"
    i0 = infos[0]
    if how == "exact" and i0["efct_date"] > "2020-10-01": return "NEW_AFTER_CENSUS"      # 国勢調査基準日後に設置
    if how == "exact" and i0["type"] == "oaza_cho" and i0["koaza"] > 0: return "AZA_AREA(小字あり)"
    if how == "exact" and i0["parcels"] and not i0["rsdt_blocks"]: return "PARCEL_AREA(地番区域)"
    if how == "exact": return "CURRENT_TOWN"
    if how == "children_chome": return "SPLIT_INTO_CHOME"
    if how == "parent_oaza": return "CHOME_NOT_IN_ABR"
    return how

rows = []
for m in rep["missing_poly"]:
    how, infos = lookup(m["town"])
    rows.append({"set": "missing_poly", "town": m["town"], "reason": m["reason"], "kinds": m["kinds"],
                 "abr_match": how, "class": classify(how, infos, m["town"]),
                 "census": census_rel(m["town"]), "abr": infos[:6]})
for u in rep["unresolved"]:
    how, infos = lookup(u["town"])
    rows.append({"set": "unresolved", "town": u["town"], "reason": u["reason"], "districts": u.get("districts"),
                 "abr_match": how, "class": classify(how, infos, u["town"]),
                 "census": census_rel(u["town"]), "abr": infos[:6]})

# ABR 全体の性質 (スキーマ判断材料)
summary = {
    "abr_town_rows": len(town), "by_type": town.machiaza_type.value_counts().to_dict(),
    "ablt_date_nonempty": int((town.ablt_date != "").sum()),
    "efct_date_top": town.efct_date.value_counts().head(6).to_dict(),
    "status_flg": town.status_flg.value_counts().to_dict(),
    "town_pos_rep_points": len(pos_ids), "parcels": len(prc), "parcels_with_pos": int((ppos.rep_lon != "").sum()),
    "parcel_ablt_nonempty": int((prc.ablt_date != "").sum()),
    "class_counts": {s: collections.Counter(r["class"] for r in rows if r["set"] == s) for s in ("missing_poly", "unresolved")},
}
json.dump({"summary": summary, "rows": rows}, open("out/abr_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

with open("out/abr_check.md", "w", encoding="utf-8") as f:
    f.write("| set | town | gomi reason | ABR match | class | census | ABR id/type/status/efct | koaza | rep_pt | 街区 | 地番(位置付) |\n|" + "---|" * 11 + "\n")
    for r in rows:
        a = r["abr"][0] if r["abr"] else {}
        more = f" (+{len(r['abr'])-1})" if len(r["abr"]) > 1 else ""
        f.write(f"| {r['set']} | {r['town']} | {r['reason']} | {r['abr_match']}{more} | {r['class']} | {r['census']} | "
                f"{a.get('machiaza_id','')} {a.get('name','')} {a.get('type','')} s{a.get('status_flg','')} {a.get('efct_date','')} | "
                f"{a.get('koaza','')} | {a.get('rep_point','')} | {a.get('rsdt_blocks','')} | {a.get('parcels','')}({a.get('parcels_with_pos','')}) |\n")
print(json.dumps(summary, ensure_ascii=False, indent=1, default=dict))
print(open("out/abr_check.md", encoding="utf-8").read())
