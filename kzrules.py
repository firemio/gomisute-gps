"""金沢市 区域表 (data/kanazawa_rules.csv, kz_rules.py の出力) と住居表示点の読み込み・照合 (split_blocks.py と resolve.py で共有)"""
import collections, csv, re
import pandas as pd
from townnorm import norm_town

def load_rules(path="data/kanazawa_rules.csv"):
    """町 (norm_town) → 区域表の行のリスト"""
    rules = list(csv.DictReader(open(path, encoding="utf-8")))
    by_town = collections.defaultdict(list)
    for r in rules:
        r["ntown"] = norm_town(r["town"])
        for k in ("blk_from", "blk_to", "ban_from", "ban_to"):
            r[k] = int(r[k]) if r[k] else None
        by_town[r["ntown"]].append(r)
    return rules, by_town

def load_jukyo(path="data/gsi_jukyo_17201.csv"):
    """地理院 住居表示住所 (町名・街区・住居番号・経緯度)"""
    pts = pd.read_csv(path, header=None, dtype=str)                 # 見出し行なし (geo_peek.py が見出し無しで書く)
    pts.columns = ["muni", "town", "blk", "num", "uri", "x5", "lon", "lat", "scale"][:len(pts.columns)]
    pts["ntown"] = pts["town"].map(norm_town)
    pts["blk_i"] = pd.to_numeric(pts["blk"].str.extract(r"^(\d+)")[0], errors="coerce")
    pts["num_i"] = pd.to_numeric(pts["num"].str.extract(r"^(\d+)")[0], errors="coerce")
    return pts

def match_row(rs, blk, num):
    """住居表示 (街区 blk, 住居番号 num) に最も具体的に当たる区域表の行。default (別記以外) / 町全体は最後"""
    best = None
    for r in rs:
        if r["kind"] in ("banchi", "block"):
            if r["blk_from"] is not None and not (r["blk_from"] <= blk <= r["blk_to"]): continue
            if r["ban_from"] is not None:
                if r["blk_from"] is None: continue          # 地番型(街区なし番地) は住居表示点に当てない
                if not (r["ban_from"] <= num <= r["ban_to"]): continue
            spec = (r["blk_from"] is not None) + (r["ban_from"] is not None)
            if best is None or spec > best[0]: best = (spec, r)
    if best: return best[1]
    for r in rs:
        if r["kind"] in ("default", "town"): return r
    return None

def match(rs, blk, num):
    """最も具体的に当たるルールの地区。default は最後"""
    r = match_row(rs, blk, num)
    return r["district"] if r else None

def load_parcels(path="data/abr/mt_parcel_city172014.csv", pos_path="data/abr/mt_parcel_pos_city172014.csv"):
    """ABR 地番マスター (代表点つき)。住居表示の区域の筆 (rsdt_addr_flg=1) は町名が住所と違うので除く"""
    pp = pd.read_csv(pos_path, dtype=str, usecols=["machiaza_id", "prc_id", "rep_lon", "rep_lat"]).dropna(subset=["rep_lon"])
    pn = pd.read_csv(path, dtype=str, usecols=["machiaza_id", "prc_id", "oaza_cho", "chome", "koaza", "prc_num1", "prc_num2", "rsdt_addr_flg"]).fillna("")
    m = pp.merge(pn, on=["machiaza_id", "prc_id"])
    m = m[m.rsdt_addr_flg == "0"].reset_index(drop=True)
    m["town"] = m.oaza_cho + m.chome
    m["ntown"] = m.town.map(norm_town)
    m["num1"] = pd.to_numeric(m.prc_num1, errors="coerce")
    return m

def table_town(ntown, by_town):
    """ABR の町名 → 区域表の町名。ABR「平栗」⇔ 区域表「平栗町」のように 町 の有無だけ違うものを当てる"""
    if ntown in by_town: return ntown
    if ntown and ntown + "町" in by_town: return ntown + "町"
    return None

_NUM = r"(\d+)(?:(?:から|〜)(\d+))?"

_COND = re.compile(r"[≪《<＜(（\s]|右岸|左岸|以北|以南|以東|以西|沿い|側")   # 場所の条件 (川の右岸・道路の以北など)

def _parcel_spec(r):
    """区域表の地番の行 (街区なし) を読む → (小字, 番地の下限, 上限, 除く, 条件付き)。読めなければ None"""
    ban = re.sub(r"\s+", " ", r["raw_ban"]).strip()
    neg = ban.endswith("を除く")
    if neg: ban = ban[:-3]
    m = _COND.search(ban)
    cond = bool(m)
    if m and m.group(0) not in "≪《<＜(（ 	": return None       # 括弧なしで場所の条件を書いた行 (大豆田本町「犀川右岸」)
    base = ban[:m.start()].strip() if m else ban
    if not base: return None
    m = re.fullmatch(_NUM, base) or re.fullmatch(r"([^\d\s]+)" + _NUM, base) or re.fullmatch(r"([^\d\s]+)", base)
    if not m: return None                                       # 「12の」「4の26」「ワ65-1からワ65-6」など
    g = m.groups()
    if base[0].isdigit(): k, lo, hi = "", g[0], g[1]
    else: k, lo, hi = g[0], (g[1] if len(g) > 1 else None), (g[2] if len(g) > 2 else None)
    lo = int(lo) if lo else None
    return (k, lo, int(hi) if hi else lo, neg, cond)

def mark_stale_rows(by_town, path="data/abr/mt_parcel_city172014.csv"):
    """番地範囲の行のうち、その番地の半分以上が今の ABR 地番に無い行に _stale を付ける (福増町「南638から794」: 南の地番は 1〜1359 だが 638〜794 が無い)。
    番号が付け替わった可能性があるので、その小字では「別記以外」を言い切らない"""
    pn = pd.read_csv(path, dtype=str, usecols=["oaza_cho", "chome", "koaza", "prc_num1"]).fillna("")
    have = collections.defaultdict(set)
    for t, k, n in zip((pn.oaza_cho + pn.chome).map(norm_town), pn.koaza, pd.to_numeric(pn.prc_num1, errors="coerce")):
        if n == n: have[(table_town(t, by_town), k)].add(int(n))
    n_stale = 0
    for t, rs in by_town.items():
        for r in rs:
            if r["kind"] in ("default", "town") or r["blk_from"] is not None: continue
            sp = r.setdefault("_pspec", _parcel_spec(r))
            if sp is None or sp[1] is None: continue
            nums = have.get((t, sp[0]), set())
            present = sum(1 for n in range(sp[1], sp[2] + 1) if n in nums)
            r["_stale"] = present * 2 < sp[2] - sp[1] + 1        # 範囲の番地の半分以上が今の地番に無い (中屋町 西826から837 は12のうち1)
            n_stale += r["_stale"]
    return n_stale

def match_parcel(rs, koaza, num):
    """地番 (小字 koaza・本番 num) に当たる区域表の行。表から言い切れなければ None。
    住居表示の街区の行は地番に当てない。次は「当たるか表から言えない」ので None:
    条件付きの行 (赤土町 リ≪犀川右岸≫・しじま台2丁目 74から77 都市計画道路…以北) に当たる筆、
    小字の付いた筆と小字の無い番地範囲の行 (神谷内町 1から125: どの小字の番地か書いていない)、
    小字に似た名前の行 (大桑町 猫下 ⇔ 小字 猫シタイ)、読めない行 (高柳町「12の」など) がある町で、明示の行に当たらない筆、
    今の地番に無い番地範囲の行 (mark_stale_rows) と同じ小字で、明示の行に当たらない筆"""
    if len({r["district"] for r in rs}) == 1: return rs[0]
    hits, unsure, fallback, unreadable, stale = [], False, None, False, False
    num = None if num is None or num != num else int(num)
    for r in rs:
        if r["kind"] in ("default", "town"):
            fallback = fallback or r; continue
        if r["blk_from"] is not None: continue                  # 住居表示の街区の行
        if "_pspec" not in r: r["_pspec"] = _parcel_spec(r)
        sp = r["_pspec"]
        if sp is None: unreadable = True; continue
        k, lo, hi, neg, cond = sp
        if r.get("_stale") and k == koaza: stale = True
        if k == "" and koaza:                                   # 番地範囲だけの行と、小字の付いた筆
            if lo is not None and num is not None and lo <= num <= hi: unsure = True
            continue
        same = k == koaza
        similar = not same and koaza and (koaza.startswith(k) or k.startswith(koaza) or (len(k) > 1 and len(koaza) > 1 and k[0] == koaza[0]))
        inrange = lo is None or (num is not None and lo <= num <= hi)
        if similar and inrange: unsure = True; continue
        hit = same and inrange
        if neg: hit = not hit and not similar
        if hit:
            if cond: unsure = True
            else: hits.append(r)
    if unsure or len({r["district"] for r in hits}) > 1: return None
    if hits: return hits[0]
    return None if unreadable or stale else fallback
