#!/usr/bin/env python3
"""住所の文字列 → 地区 (resolve.resolve_address) の評価: 実在の住所をいろいろな書き方にして、確定 (ok) が正しいかを数える

  python bench/eval_address.py [件数=2000]   → out/kanazawa_address_eval.json

正解は区域表の行 (住居表示は kzrules.match_row、地番は kzrules.match_parcel)。区域表で決まらない住所での確定も誤りに数える。
書き方:
  住居表示 (国土地理院の住所): 金沢市町N番M号 / 町N-M / 丁目-N-M / 漢数字 / 「N番地M」/ 後ろに空白と階・部屋 / 市町村名なし / 丁目を落とした形
    (「N番M号」と「N-M」) / 空白で区切った「町 N M」/ 郵便番号つき / いろいろなダッシュ / 町名だけ
  地番 (ABR の地番、住居表示の無い筆): 町{小字}N番地 / 町{小字}N-枝 / 町字{小字}N番地 / 小字を落とした形 / 丁目を落とした「町N-枝」
丁目を落とした形・小字を落とした形・市町村名なし・町名だけは「確定しないのが正しい」場合があるので、確定したときに正しいかだけを数える。
"""
import collections, json, os, random, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd
import resolve
from townnorm import norm_town
from kzrules import load_rules, match_row, match_parcel, table_town, mark_stale_rows

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
rnd = random.Random(20261018)
k = resolve.Kanazawa()
_, bt = load_rules(); mark_stale_rows(bt)
KAN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}

def kan(n):
    if n <= 10: return KAN[n]
    if n < 20: return "十" + KAN[n - 10]
    if n < 100: return KAN[n // 10] + "十" + (KAN[n % 10] if n % 10 else "")
    return str(n)

stats = collections.defaultdict(collections.Counter)
wrong = collections.defaultdict(list)
def check(form, text, truth, date="2026-10-20"):
    r = resolve.resolve_address(k, text, date, 1)
    st = r["status"]; d = r["location"].get("collection_district") if r.get("location") else None
    if st == "ok":
        good = truth is not None and d == truth
        stats[form]["ok_correct" if good else "ok_wrong"] += 1
        if not good and len(wrong[form]) < 15: wrong[form].append([text, d, truth])
    else:
        stats[form][st] += 1

# ---- 住居表示 ----
J = k.jukyo.dropna(subset=["blk_i", "num_i"])
J = J.loc[rnd.sample(list(J.index), min(N, len(J)))]
for t, raw, b, n in zip(J.ntown, J.town, J.blk_i.astype(int), J.num_i.astype(int)):
    rs = bt.get(t)
    row = match_row(rs, b, n) if rs else None
    truth = row["district"] if row else None
    m = re.match(r"^(.*?)(\d+)丁目$", t)
    check("jukyo_canonical", f"金沢市{raw}{b}番{n}号", truth)
    check("jukyo_hyphen", f"金沢市{m[1]}{m[2]}-{b}-{n}" if m else f"金沢市{t}{b}-{n}", truth)
    check("jukyo_kanji", f"金沢市{raw}{kan(b)}番{kan(n)}号", truth)
    check("jukyo_banchi_form", f"金沢市{raw}{b}番地{n}", truth)
    check("jukyo_with_room", f"金沢市{raw}{b}-{n} {rnd.choice(['2階', '201号室', '1F', 'サンハイツ101', '2号棟'])}", truth)
    check("jukyo_no_city", f"{raw}{b}番{n}号", truth)
    check("jukyo_space_separated", f"金沢市{raw} {b} {n}", truth)
    check("jukyo_postal_code", f"〒920-0000 金沢市{raw}{b}番{n}号", truth)
    check("jukyo_other_dash", f"金沢市{raw}{b}{rnd.choice(['―', '－', '−', 'ー', '–'])}{n}", truth)
    check("town_only", f"金沢市{raw}", truth)
    if m: check("jukyo_chome_dropped", f"金沢市{m[1]}{b}番{n}号", truth)
    if m: check("jukyo_chome_dropped_hyphen", f"金沢市{m[1]}{b}-{n}", truth)

# ---- 地番 (ABR、住居表示の無い筆) ----
pn = pd.read_csv("data/abr/mt_parcel_city172014.csv", dtype=str, usecols=["oaza_cho", "chome", "koaza", "prc_num1", "prc_num2", "rsdt_addr_flg"]).fillna("")
pn = pn[(pn.rsdt_addr_flg == "0") & (pn.prc_num1 != "")]
pn = pn.loc[rnd.sample(list(pn.index), min(N, len(pn)))]
for oz, ch, kz, n1, n2 in zip(pn.oaza_cho, pn.chome, pn.koaza, pn.prc_num1, pn.prc_num2):
    t = norm_town(oz + ch); tt = table_town(t, bt)
    num = int(n1)
    row = match_parcel(bt[tt], kz, num) if tt else None
    truth = row["district"] if row else None
    town = oz + ch
    check("chiban_canonical", f"金沢市{town}{kz}{num}番地" + (f"{n2}" if n2 else ""), truth)
    check("chiban_hyphen", f"金沢市{town}{kz}{num}-{n2 or 1}", truth)
    if ch: check("chiban_chome_dropped_hyphen", f"金沢市{oz}{num}-{n2 or 1}", truth)   # 丁目を落とした地番 (湊2丁目1番地2 → 湊1-2)
    if kz:
        check("chiban_aza", f"金沢市{town}字{kz}{num}番地", truth)
        check("chiban_koaza_dropped", f"金沢市{town}{num}番地", truth)

rep = {"n_sample": N, "by_form": {f: dict(c) for f, c in stats.items()}, "wrong_examples": dict(wrong)}
tot = collections.Counter()
for c in stats.values(): tot.update(c)
rep["total"] = dict(tot)
json.dump(rep, open("out/kanazawa_address_eval.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for f, c in stats.items():
    okn = c["ok_correct"] + c["ok_wrong"]
    print(f"{f:22s} ok {okn:5d} (wrong {c['ok_wrong']:3d})  ambiguous {c['ambiguous']:5d}  unresolved {c['unresolved']:5d}  out {c['out_of_coverage']:3d}")
print("total", dict(tot))
for f, w in wrong.items():
    for x in w[:4]: print("  WRONG", f, x)
