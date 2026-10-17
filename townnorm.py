"""町名正規化 (ごみ区域表 / e-Stat / 地理院 / ABR の表記ゆれ吸収)"""
import functools, re

KAN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

def kan2num(s):
    if s in KAN: return KAN[s]
    if s.startswith("十"): return 10 + KAN.get(s[1:], 0)
    if "十" in s: a, b = s.split("十"); return KAN[a] * 10 + KAN.get(b, 0)
    return None

@functools.lru_cache(maxsize=None)          # 同じ町名を何十万回も正規化するので覚えておく (起動を速くする)
def norm_town(s):
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789")).replace(" ", "").replace("　", "")
    s = s.replace("ヶ", "ケ").replace("ヵ", "ケ").replace("之", "の")
    s = s.translate(str.maketrans("舘薮刈嶽傅", "館藪苅岳伝"))      # 異体字ゆれ (ABR 傅燈寺町 ⇔ 区域表 伝燈寺町)
    s = re.sub(r"(\d+)丁$", r"\1丁目", s)                         # 鞍月東1丁
    s = re.sub(r"([^\d丁番])(\d+)$", r"\1\2丁目", s)              # もりの里1
    s = re.sub(r"([一二三四五六七八九十]+)丁目", lambda m: f"{kan2num(m[1])}丁目", s)
    s = re.sub(r"([一二三四五六七八九十]+)番丁", lambda m: f"{kan2num(m[1])}番丁", s)
    return s

def match_key(s):
    """照合キー: norm_town に加えて ノ/の を畳む (国勢調査2005「木の新保7番丁」⇔ 区域表「木ノ新保7番丁」)。
    全国 ABR では畳むと同一自治体内で17件衝突するので、表示名には使わず、当たったら候補を全部返す前提"""
    return norm_town(s).replace("ノ", "の")
