"""kanazawa_rows.json → data/kanazawa_rules.csv (town,blk_from,blk_to,ban_from,ban_to,raw_blk,raw_ban,district,kind)"""
import json, re, csv, sys, collections
sys.stdout.reconfigure(encoding="utf-8")
Z2H = str.maketrans("０１２３４５６７８９－", "0123456789-")
rows = json.load(open("data/kanazawa_rows.json", encoding="utf-8"))

def rng(s):
    """'1から6'→(1,6) '7'→(7,7) '17以降'→(17,9999) ''→None  else 'X'"""
    s = s.translate(Z2H).replace(" ", "")
    if not s: return None
    if m := re.fullmatch(r"(\d+)", s): return (int(s), int(s))
    if m := re.fullmatch(r"(\d+)(?:から|～|~|-)(\d+)", s): return (int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d+)(?:以降|以上|から)", s): return (int(m[1]), 9999)
    if m := re.fullmatch(r"(\d+)(?:以前|以下|まで)", s): return (1, int(m[1]))
    return "X"

def split_list(s):
    s = s.translate(Z2H).strip("、, ")
    return [p for p in re.split(r"[、,]", s) if p] if s else [""]

AZA = re.compile(r"[ァ-ヴィィ甲乙丙丁戊己庚辛壬癸]|山|欠|川淵|平|開")
out, town, kinds, odd = [], None, collections.Counter(), []
for r in rows:
    tbl, cells = r[0], r[1:]
    if cells[:1] == ["町名"]: continue
    if len(cells) == 4:
        if cells[0]: town = cells[0]
        blk, ban, dist = cells[1:]
    elif len(cells) == 3: blk, ban, dist = cells
    else: odd.append(cells); continue
    if not town: odd.append(cells); continue
    dist = dist.replace("\xa0", " ")
    note = ""
    if m := re.match(r"(.+?)\s*[\(（](注意)[\)）](.*)", dist): dist, note = m[1].strip(), m[3].strip()
    for blk1 in split_list(blk):
        for ban1 in split_list(ban):
            b, n = rng(blk1), rng(ban1)
            if b is None and n is None: kind = "town"
            elif "別記以外" in ban1 or "別記以外" in blk1: kind = "default"; b = n = None
            elif "X" in (b, n) and AZA.search(ban1 + blk1): kind = "aza"       # 字・地番 (住居表示外)
            elif "X" in (b, n): kind = "unparsed"
            elif b is not None and n is None: kind = "block"
            else: kind = "banchi"
            kinds[kind] += 1
            if kind == "unparsed": odd.append([town, blk1, ban1, dist])
            bb = b if isinstance(b, tuple) else (None, None)
            nn = n if isinstance(n, tuple) else (None, None)
            out.append([town, *bb, *nn, blk1, ban1, dist, kind, note])

with open("data/kanazawa_rules.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["town", "blk_from", "blk_to", "ban_from", "ban_to", "raw_blk", "raw_ban", "district", "kind", "note"])
    w.writerows(out)
towns = collections.defaultdict(set)
for o in out: towns[o[0]].add(o[-2])
def cls(s):
    if s == {"town"}: return "whole"
    if "aza" in s: return "aza"
    if "unparsed" in s: return "unparsed"
    return "jukyo_split"
tk = collections.Counter(cls(s) for s in towns.values())
print("rules", len(out), dict(kinds))
print("towns", len(towns), dict(tk), f"町丁目単位で解決 {tk['whole']/len(towns):.1%}")
print("districts", len({o[-3] for o in out}))
print("unparsed/odd", len(odd))
for o in odd[:25]: print("  ", o)
