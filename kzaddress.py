"""金沢市: 利用者が伝えた住所の文字列 → 区域表の行

住所の書き方はいろいろなので、読み方を1つに決めつけない:
  - 「1-1」「1番1」「1番地1」は、住居表示 (街区-住居番号) とも地番 (本番-枝番) とも読める。町に住居表示があるか、
    その地番が ABR にあるかで、ありうる読み方を全部出し、地区がすべて同じときだけ確定する
  - 住居表示の住所は国土地理院の住居表示住所か ABR の住居表示街区に、地番は ABR の地番に、実在するときだけ使う
  - 「泉野出町1番1号」のように丁目が抜けた住所、「古府町291番地」のように小字が抜けた地番は、ありうる丁目・小字を候補にして聞き返す
  - 建物名・階・部屋番号 (空白の後ろ、「2階」「201号室」など) は落とす。「字」「大字」は外す
  - 市町村名が書かれていない住所で、同じ町名がほかの市区町村にもある (押野2丁目は野々市市にも) ときは確定しない
"""
import collections, json, os, re, unicodedata
from townnorm import norm_town
from kzrules import match_row, match_parcel, table_town

_KANJI = "〇一二三四五六七八九"

def kanji_int(t):
    """漢数字 (〇〜九百九十九) → int"""
    if all(ch in _KANJI for ch in t): return int("".join(str(_KANJI.index(ch)) for ch in t))
    n, cur = 0, 0
    for ch in t:
        if ch in _KANJI: cur = _KANJI.index(ch)
        elif ch == "十": n += (cur or 1) * 10; cur = 0
        elif ch == "百": n += (cur or 1) * 100; cur = 0
    return n + cur

_NUMEND = r"\d+(?:番地?\d*(?:号)?|号|-\d+(?:-\d+){0,2})"

def clean(text):
    """住所の文字列 → (本体, 市町村名が書かれていたか, 対象外の自治体名 or None)"""
    s = unicodedata.normalize("NFKC", str(text)).strip()
    s = re.sub(r"[‐‑‒–—―─━−－]", "-", s)                                        # いろいろなダッシュ → -
    s = re.sub(r"^\s*(日本国?)?\s*〒?\s*\d{3}-?\d{4}\s*", "", s)                     # 郵便番号 (後ろの空白ごと)
    s = re.sub(r"^\s*日本国?\s*", "", s)
    s = re.sub(r"[一二三四五六七八九十百〇]+(?=番|号|-)", lambda x: str(kanji_int(x[0])), s)
    s = re.sub(r"(?<=番地|.番)[一二三四五六七八九十百〇]+(?=号?(\s|$))", lambda x: str(kanji_int(x[0])), s)   # 二十九番地五 → 29番地5
    s = re.sub(r"(?<=\d)\s*[-ーの]\s*(?=\d)", "-", s)                            # 29の5・29ー5 → 29-5 (数字に挟まれた長音だけ。町名の長音はそのまま)
    s = re.sub(r"(?<=\d番地)の(?=\d)", "", s)                                      # 29番地の5 → 29番地5
    m = re.match(r"^(.*?" + _NUMEND + r")\s.*$", s)                          # 番号の後ろの空白から先 (建物名・階・部屋) を落とす
    if m: s = m[1]
    s = re.sub(r"(?<=\d)\s+(?=\d)", "-", s)                                      # 「寺町4丁目 1 8」: 空白で区切った数字は別の番号 (1-8。18 にしない)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^(.*?\d+番地?\d+号).*$", r"\1", s)                           # 29番5号201号室 → 29番5号
    m = re.match(r"^(北海道|東京都|(?:京都|大阪)府|.{2,3}県)", s)
    if m and m[1] != "石川県":
        c = re.match(r"^(.{1,4}郡.{1,4}?[町村]|.{1,5}?市(?:.{1,4}?区)?|.{1,4}?区)", s[len(m[1]):])   # 静岡県静岡市清水区 まで名前に入れる
        return s, True, m[1] + (c[1] if c else "")
    if m: s = s[len(m[1]):]
    if s.startswith("金沢市"): return re.sub(r"^(大字|字)", "", s[3:]), True, None
    m = re.match(r"^(.{1,4}郡.{1,4}?[町村]|.{1,4}市)", s)
    return re.sub(r"^(大字|字)", "", s), False, (m[1] if m else None)

class AddressIndex:
    """住所の読み取りに要る索引 (Kanazawa の索引から作る)"""
    def __init__(self, k):
        self.k = k
        self.known = (set(k.by_town) | set(k.jukyo.ntown) | set(k.parcels.ntown) | set(k.towns.ntown)) - {""}
        self.jukyo_towns = set(k.jukyo.ntown)
        self.jukyo_pts = collections.defaultdict(set)              # (町, 街区) → 住居番号
        for t, b, n in zip(k.jukyo.ntown, k.jukyo.blk_i, k.jukyo.num_i):
            if b == b and n == n: self.jukyo_pts[(t, int(b))].add(int(n))
        self.abr_blocks = set()                                    # ABR 住居表示 街区
        import pandas as pd
        b = pd.read_csv("data/abr/mt_rsdtdsp_blk_pref17.csv", dtype=str, usecols=["lg_code", "oaza_cho", "chome", "blk_num"]).fillna("")
        b = b[b.lg_code == "172014"]
        for t, n in zip((b.oaza_cho + b.chome).map(norm_town), pd.to_numeric(b.blk_num, errors="coerce")):
            if n == n: self.abr_blocks.add((t, int(n)))
        self.block_towns = {t for t, _ in self.abr_blocks} | self.jukyo_towns
        pn = pd.read_csv("data/abr/mt_parcel_city172014.csv", dtype=str, usecols=["oaza_cho", "chome", "koaza", "prc_num1", "rsdt_addr_flg"]).fillna("")
        pn = pn[pn.rsdt_addr_flg == "0"]
        self.parcels = collections.defaultdict(set)                # 町 → {(小字, 本番)}
        for t, kz, n in zip((pn.oaza_cho + pn.chome).map(norm_town), pn.koaza, pd.to_numeric(pn.prc_num1, errors="coerce")):
            if n == n: self.parcels[t].add((kz, int(n)))
        self.koazas = {t: {kz for kz, _ in v} for t, v in self.parcels.items()}
        p = "out/kanazawa_town_collisions.json"
        self.collisions = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

    def town_of(self, t):
        """t の頭から一番長く当たる町名 → (町名, 残り)。次の文字が漢字の町名の続き (尾浦町の「町」) なら当てない"""
        for i in range(len(t), 0, -1):
            nt = norm_town(t[:i])
            if nt in self.known:
                rest = t[i:]
                if rest and re.match(r"町|丁目|通|区", rest): continue
                if t[i - 1].isdigit() and rest[:1] in ("-", "番") or t[i - 1].isdigit() and rest[:1].isdigit(): continue   # 笠舞2-29-5 の「笠舞2」を 2丁目にしない
                return nt, rest
        return None, t

    def chome_siblings(self, town):
        return sorted(x for x in self.known if re.fullmatch(re.escape(town) + r"\d+丁目", x))

    def _sibling_chiban(self, base, num):
        """丁目が抜けた地番: 実在する「町名X丁目 num番地」の読み方 (実在するものだけ。小字は evaluate で見る)"""
        return [{"kind": "chiban", "town": ct, "koaza": "", "num": num} for ct in self.chome_siblings(base)
                if any(n == num for _, n in self.parcels.get(ct, set()))]

    def _sibling_jukyo(self, base, blk, num):
        """「町名 A-B」の丁目が抜けているとき: 実在する「町名X丁目 A番B号」の読み方 (実在するものだけ)"""
        return [{"kind": "jukyo", "town": ct, "blk": blk, "num": num} for ct in self.chome_siblings(base)
                if num in self.jukyo_pts.get((ct, blk), set())]

    # ---------------- 読み方 ----------------
    def readings(self, s):
        """住所の本体 → 読み方のリスト [{kind, town, ...}]"""
        out = []
        m = re.fullmatch(r"(.+?)(\d+)-(\d+)-(\d+)(?:-\d+)?", s)                    # 笠舞2-29-5 (丁目-街区-住居番号(-部屋))
        if m:
            base = re.sub(r"(大字|字)$", "", m[1])
            ct = norm_town(f"{base}{m[2]}丁目")
            if ct in self.known:
                out.append({"kind": "jukyo", "town": ct, "blk": int(m[3]), "num": int(m[4])})
                out.append({"kind": "chiban", "town": ct, "koaza": "", "num": int(m[3])})     # 窪6-223-1 (6丁目223番地1)
                return out, ct
        town, rest = self.town_of(s)
        m = re.fullmatch(r"(.+?)(\d+)-(\d+)", s)                                     # 笠舞2-29 (丁目-街区。「笠舞」だけの町は無い)
        if m and not town:
            base = re.sub(r"(大字|字)$", "", m[1])
            ct = norm_town(f"{base}{m[2]}丁目")
            if ct in self.known:
                out = [{"kind": "block", "town": ct, "blk": int(m[3])}, {"kind": "chiban", "town": ct, "koaza": "", "num": int(m[3])}]
                return out + self._sibling_jukyo(base, int(m[2]), int(m[3])) + self._sibling_chiban(base, int(m[2])), ct   # 笠舞1丁目2番29号・湊2丁目1番地2 とも読める
        if not town: return out, None
        rest = re.sub(r"^(大字|字)", "", rest)
        if not rest:
            out = [{"kind": "town", "town": town}]
            if self.chome_siblings(town): out.append({"kind": "needs_chome", "town": town, "blk": None, "num": None})   # 「泉野出町」: 丁目が抜けているかもしれない
            return out, town
        m = re.fullmatch(r"(\d+)-(\d+)-(\d+)(?:-\d+)?", rest)                    # 丁目-街区-住居番号(-部屋)
        if m:
            ct = norm_town(f"{town}{m[1]}丁目")
            if ct in self.known:
                out.append({"kind": "jukyo", "town": ct, "blk": int(m[2]), "num": int(m[3])})
                out.append({"kind": "chiban", "town": ct, "koaza": "", "num": int(m[2])})       # 窪6-223-1 (6丁目223番地1)
            out.append({"kind": "chiban", "town": town, "koaza": "", "num": int(m[1])})
            return out, town
        m = re.fullmatch(r"(\d+)(番地?|-)(\d+)(号)?", rest)                         # 29番5号 / 29-5 / 29番地5
        if m:
            a, sep, b, go = int(m[1]), m[2], int(m[3]), m[4]
            out.append({"kind": "jukyo", "town": town, "blk": a, "num": b})
            if not go: out.append({"kind": "chiban", "town": town, "koaza": "", "num": a})
            if not go and sep == "-":
                ct = norm_town(f"{town}{a}丁目")                                     # 泉野出町3-16 = 泉野出町3丁目16番 とも読める
                if ct in self.known: out.append({"kind": "block", "town": ct, "blk": b})
                out += self._sibling_jukyo(town, a, b)                                 # 泉野出町1丁目3番16号 とも読める (丁目が抜けている)
            if not go and self.chome_siblings(town):
                out += self._sibling_chiban(town, a)                                   # 古府1番地1 = 古府2丁目1番地1 とも読める
            if go and town not in self.block_towns and self.chome_siblings(town):    # 泉野出町1番1号 (丁目が抜けている)
                out.append({"kind": "needs_chome", "town": town, "blk": a, "num": b})
            return out, town
        m = re.fullmatch(r"(\d+)(番地?)?", rest)                                    # 7番 / 291番地 / 7
        if m:
            out.append({"kind": "block", "town": town, "blk": int(m[1])})
            out.append({"kind": "chiban", "town": town, "koaza": "", "num": int(m[1])})
            return out, town
        m = re.fullmatch(r"([^\d\-]+?)(\d+)(?:(?:番地?|-)\d*)?(?:号)?", rest)          # 南291番地4 / ロ21-3 / 猫シタイ5番地
        if m:
            kz = re.sub(r"^(大字|字)", "", m[1])
            out.append({"kind": "chiban", "town": town, "koaza": kz, "num": int(m[2])})
            return out, town
        return [{"kind": "unparsed", "town": town, "rest": rest}], town

    def evaluate(self, r):
        """1つの読み方 → (地区の集合 or None, 区域表の行, 説明, 実在するか)"""
        k, t = self.k, r["town"]
        tt = table_town(t, k.by_town)
        rs = k.by_town.get(tt, []) if tt else []
        whole = next((x for x in rs if x["kind"] in ("town", "default")), None)
        if r["kind"] == "town":
            ds = {x["district"] for x in rs}
            if not rs: return None, [], f"町 {t} は区域表に無い", False
            sure = whole is not None and len(ds) == 1                                # 町全体の行が無い (行が街区・小字の一部だけ) なら、町名だけでは決めない
            return ds, rs, f"町 {t} の区域表の行" + ("" if sure else " (町の一部の行しか無い、または地区が分かれる。番地まで要る)"), (True if sure else "undecided")
        if r["kind"] == "jukyo":
            desc = f"住居表示 {t} {r['blk']}番{r['num']}号"
            num_known = r["num"] in self.jukyo_pts.get((t, r["blk"]), set())
            blk_known = bool(self.jukyo_pts.get((t, r["blk"]))) or (t, r["blk"]) in self.abr_blocks
            if not (num_known or blk_known): return None, [], f"{desc} は見つからない", False
            if not rs: return None, [], f"{desc} (区域表に {t} の行が無い)", "no_row"
            if k.uniform.get(tt) and whole: return {whole["district"]}, [whole], desc, True
            if not num_known:
                # 街区はあるが、住居番号が国土地理院の住所に無い (打ち間違いかもしれない): 街区の中がすべて同じ地区のときだけ決める
                bds, brows, _, bex = self.evaluate({"kind": "block", "town": t, "blk": r["blk"]})
                note = f"{desc} (住居番号 {r['num']} は国土地理院の住所に無い。街区 {r['blk']}番 はある)"
                return bds, brows, note, (True if bex is True and bds and len(bds) == 1 else "undecided")
            row = match_row(rs, r["blk"], r["num"])
            if not row: return {x["district"] for x in rs}, rs, f"{desc} (区域表の行では言い切れない)", "undecided"
            return {row["district"]}, [row], desc, True
        if r["kind"] == "block":
            exists = bool(self.jukyo_pts.get((t, r["blk"]))) or (t, r["blk"]) in self.abr_blocks
            if not exists: return None, [], f"{t} {r['blk']}番 は見つからない", False
            if not rs: return None, [], f"{t} {r['blk']}番 (区域表に {t} の行が無い)", "no_row"
            if k.uniform.get(tt) and whole: return {whole["district"]}, [whole], f"{t} {r['blk']}番 (街区)", True
            rows = [x for x in rs if x["blk_from"] is not None and x["blk_from"] <= r["blk"] <= x["blk_to"]]
            ds = {x["district"] for x in rows} | {k._addr_district(i)[0]["district"] for i in self._block_addr_idx(t, r["blk"]) if k._addr_district(i)[0]}
            if not rows and whole: ds |= {whole["district"]}; rows = [whole]
            if not ds: return {x["district"] for x in rs}, rs, f"{t} {r['blk']}番 (街区。区域表の行では言い切れない)", "undecided"
            return ds, rows, f"{t} {r['blk']}番 (街区。住居番号が無いので街区の中の行をすべて見る)", True
        if r["kind"] == "chiban":
            have = self.parcels.get(t, set())
            others = sorted(kz for kz, n in have if n == r["num"] and kz) if not r["koaza"] else []
            if (r["koaza"], r["num"]) not in have or others:
                if not r["koaza"] and any(n == r["num"] for _, n in have):     # 小字が抜けている (小字なしの同じ番号があっても、小字つきの筆もあれば決めない)
                    kzs = sorted({kz for kz, n in have if n == r["num"]})
                    ds = set()
                    for kz in kzs:
                        row = match_parcel(rs, kz, r["num"]) if rs else None
                        ds |= {row["district"]} if row else {x["district"] for x in rs}
                    return (ds or None), rs, f"地番 {t}{r['num']}番地 (小字が書かれていない。ありうる小字: {'・'.join(x or '(小字なし)' for x in kzs[:6])})", "needs_koaza"
                return None, [], f"地番 {t}{r['koaza']}{r['num']}番地 は見つからない", False
            if not rs: return None, [], f"地番 {t}{r['koaza']}{r['num']}番地 (区域表に {t} の行が無い)", "no_row"
            if k.uniform.get(tt) and whole: return {whole["district"]}, [whole], f"地番 {t}{r['koaza']}{r['num']}番地", True
            row = match_parcel(rs, r["koaza"], r["num"])
            return ({row["district"]} if row else {x["district"] for x in rs}), ([row] if row else rs), \
                f"地番 {t}{r['koaza']}{r['num']}番地" + ("" if row else " (区域表の行では言い切れない)"), (True if row else "undecided")
        if r["kind"] == "needs_chome":
            sib = self.chome_siblings(t)
            ds = set()
            for ct in sib:
                tt2 = table_town(ct, k.by_town)
                rs2 = k.by_town.get(tt2, []) if tt2 else []
                if r["blk"] is None: ds |= {x["district"] for x in rs2}; continue
                row = match_row(rs2, r["blk"], r["num"]) if rs2 else None
                if row: ds.add(row["district"])
            return (ds or None), [], f"{t} の丁目が書かれていない ({'・'.join(sib)})", "needs_chome"
        return None, [], f"住所の「{r.get('rest', '')}」の部分を、番地・号として読めない", False

    def _block_addr_idx(self, t, blk):
        j = self.k.jukyo
        return [int(i) for i in j.index[(j.ntown == t) & (j.blk_i == blk)]]
