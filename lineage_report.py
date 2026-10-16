#!/usr/bin/env python3
"""旧町名の系譜 → address-normalize の town_alias / town_alias_target 形式 + 検証レポート

入力:
  out/abr_check.json         abr_check.py  (32町 + 27町の ABR 照合)
  out/census_lineage.json    census_lineage.py (国勢調査 2000→2020 差分)
  out/old_town_lineage.json  Claude 調査＋反証ワークフローの結果 {records:[...], verdict:{verdicts:[...]}}
出力:
  out/kanazawa_town_alias.csv, out/kanazawa_town_alias_target.csv, docs/abr_check.md
"""
import csv, json, sys, collections
import pandas as pd
from townnorm import norm_town, match_key

sys.stdout.reconfigure(encoding="utf-8")
LG = "172014"
chk = json.load(open("out/abr_check.json", encoding="utf-8"))
census = json.load(open("out/census_lineage.json", encoding="utf-8"))
wf = json.load(open("out/old_town_lineage.json", encoding="utf-8"))

town = pd.read_csv(f"data/abr/mt_town_fullset_city{LG}.csv", dtype=str).fillna("")
town = town[town.machiaza_type != "3"]
ID = {norm_town(o + c): mid for o, c, mid in zip(town.oaza_cho, town.chome, town.machiaza_id)}

# 反証で corrected なら差し替え、refuted は confidence を落として残す
final = {r["town"]: dict(r, verify="unverified") for r in wf["records"]}
for v in wf["verdict"]["verdicts"]:
    r = final.get(v["town"])
    if r is None: continue
    if v["verdict"] == "corrected" and v.get("corrected_record"):
        r = final[v["town"]] = dict(v["corrected_record"], verify="corrected")
    r["verify"] = v["verdict"] if r.get("verify") != "corrected" else "corrected"
    r["verify_note"] = v.get("corrections", "")
    r["evidence"] = r.get("evidence", []) + v.get("independent_evidence", [])
    if v["verdict"] in ("refuted", "insufficient"): r["confidence"] = min(r.get("confidence", 0.5), 0.4)

REL = {"abolished": None, "remnant": "remnant", "not_a_town": "provisional", "current_alias": "variant"}
shares = {norm_town(e["town"]).replace("ノ", "の"): {norm_town(s["town"]): s["share"] for s in e["successors"]} for e in census}
alias_rows, target_rows = [], []
for i, (name, r) in enumerate(sorted(final.items()), 1):
    succ = r.get("successors", [])
    rel = REL[r["status"]] or ("renamed" if len(succ) == 1 and succ[0]["part"] == "whole" else "split")
    src = "census_diff" if match_key(name) in shares else "research"
    alias_rows.append({"alias_id": i, "lg_code": LG, "alias_name": name, "alias_key": match_key(name), "relation": rel,
                       "event": r["event"], "valid_until": r.get("abolished_on") or "", "precision": r["precision"],
                       "source": src, "verify": r["verify"], "confidence": r["confidence"],
                       "evidence": json.dumps(r.get("evidence", [])[:6], ensure_ascii=False)})
    sh = shares.get(match_key(name), {})
    for s in succ:
        n = norm_town(s["name"])
        target_rows.append({"alias_id": i, "lg_code": LG, "machiaza_id": ID.get(n, ""), "name": s["name"],
                            "share": sh.get(n, ""), "part": s["part"], "gomi": n in {norm_town(x) for x in r.get("gomi_successors", [])}})

def dump(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
dump("out/kanazawa_town_alias.csv", alias_rows)
dump("out/kanazawa_town_alias_target.csv", target_rows)
unknown_ids = [t for t in target_rows if not t["machiaza_id"]]

# ---- レポート ----
s = chk["summary"]; cc = s["class_counts"]["missing_poly"]
L = ["# ABR 町字マスターで金沢の「ポリゴンが引けない32町」を引けるか — 検証結果", "",
     "生成: `lineage_report.py` (入力 `abr_check.py` / `census_lineage.py` / Claude 調査＋反証ワークフロー)", "",
     "## 結論", "",
     f"- **ABR では旧町字・廃止町字は引けない。** 金沢の町字 {s['abr_town_rows']:,} 行、全国 727,467 行とも `ablt_date` 0件・別名0件。"
     f" `efct_date` は {s['efct_date_top'].get('1947-04-17', 0):,} 行が 1947-04-17 のプレースホルダ",
     "- ABR で引けるのは「現行の町字」まで。旧町名は **国勢調査の差分 (2000年以降)** と **文献調査 (それ以前)** で自前に持つ必要がある",
     "- しかも **ABR に無い ≠ 廃止**。地番の登記が無い残存地 (西堀川町・八幡町・笠舞町・糸田町・神田町) や、郵便番号が現役の北安江町・西念町も ABR に無い",
     "- 住居表示で消えた町名の未実施地番が「旧町名＋イロハ小字」として ABR に残ることがある (大字行のない小字 = 幽霊大字)。"
     "ただし地番マスターに筆があるものだけが本物 (窪町 31筆 ○ / 入江町ヘ 0筆 ×)",
     "- 1つの町名が数十年かけて段階的に消える・中間町名を経る・別の範囲で復活する (観音町 2019) ので、町名は安定キーにならない",
     "", "## 32町の分類", "", "| 分類 | 件数 | 意味 |", "|---|---|---|"]
MEAN = {"ABR_NOT_FOUND": "ABR にも国勢調査2020にも無い (廃止済み、または地番の無い残存地)",
        "OLD_TOWN_REMNANT(旧町名+小字)": "大字行の無い小字行だけが ABR に残る。筆があるのは窪町・円光寺町のみ (入江町ヘは筆0で実体なし)",
        "LAND_READJUSTMENT(区画整理の仮称)": "町字ではない (区画整理事業地の呼び名)",
        "PARCEL_AREA(地番区域)": "ABR に現存。国勢調査2020は親の町名のまま (2020年以降の丁目新設とみられるが ABR の日付は仮置き)。地番の位置参照あり",
        "SPLIT_INTO_CHOME": "区域表が丁目を付けずに書いている (ABR・国勢調査は丁目単位)",
        "NEW_AFTER_CENSUS": "国勢調査2020後に新設 (ABR に効力発生日あり)",
        "AZA_AREA(小字あり)": "ABR に大字として現存し小字を持つ。国勢調査では丁目側に吸収",
        "CURRENT_TOWN": "ABR に現存。国勢調査の小地域では別の町と合算"}
for k, v in sorted(cc.items(), key=lambda x: -x[1]): L.append(f"| {k} | {v} | {MEAN.get(k, '')} |")
L += ["", "## 消えた町名の系譜 (Claude 調査 → 反証検証)", "",
      "| 旧町名 | 状態 | 廃止 | 事由 | 後継 (面積比) | 検証 | 確信度 |", "|---|---|---|---|---|---|---|"]
tg = collections.defaultdict(list)
for t in target_rows: tg[t["alias_id"]].append(t)
for a in alias_rows:
    succ = ", ".join(f"{t['name']}" + (f"({float(t['share']):.0%})" if t["share"] != "" else "") for t in tg[a["alias_id"]])
    L.append(f"| {a['alias_name']} | {a['relation']} | {a['valid_until'] or '?'} ({a['precision']}) | {a['event']} | {succ} | {a['verify']} | {a['confidence']:.2f} |")
L += ["", "## 国勢調査差分で機械的に取れた系譜 (金沢 2000→2020)", ""]
for e in census:
    L.append(f"- {e['town']} ({e['last_seen']}年まで) → " + ", ".join(f"{x['town']} {x['share']:.0%}" for x in e["successors"][:5]))
if wf["verdict"].get("cross_cutting_findings"):
    L += ["", "## 反証担当の横断所見", "", wf["verdict"]["cross_cutting_findings"]]
L += ["", "## 全行 (ABR 照合)", "", open("out/abr_check.md", encoding="utf-8").read()]
open("docs/abr_check.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
print(f"alias {len(alias_rows)} / targets {len(target_rows)} (machiaza_id 不明 {len(unknown_ids)}: {[t['name'] for t in unknown_ids]})")
print(collections.Counter(a["relation"] for a in alias_rows), collections.Counter(a["verify"] for a in alias_rows))
