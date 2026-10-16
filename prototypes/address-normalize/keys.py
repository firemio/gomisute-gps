"""rules.json の Python 実装の雛形 (検証用)。strip -> canon -> fold"""
import re, unicodedata

KN = dict(zip("〇一二三四五六七八九", range(10)))
NUM = "〇一二三四五六七八九十百"

def kan2int(s):
    if all(c in KN for c in s):
        return int("".join(str(KN[c]) for c in s))
    total = cur = 0
    for c in s:
        if c == "十":
            total += (cur or 1) * 10; cur = 0
        elif c == "百":
            total += (cur or 1) * 100; cur = 0
        else:
            cur = KN[c]
    return total + cur

RE_SUFFIX = re.compile(f"([{NUM}]+)(丁目|番丁|番町|条|線|地割)")
RE_DAI = re.compile(f"第([{NUM}]+)")
RE_DASH = re.compile(r"(?<=\d)[‐‑‒–—―−ーｰ－-](?=\d)")
RE_QUAL = re.compile(r"(の一部|一部|地内|[ノの]部)$")

def strip_q(s):
    quals = []
    while True:
        m = RE_QUAL.search(s)
        if not m or m.start() == 0:
            return s, quals
        quals.append(m.group(1)); s = s[:m.start()]

def canon(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    s = RE_DASH.sub("-", s)
    s = RE_SUFFIX.sub(lambda m: f"{kan2int(m[1])}{m[2]}", s)
    s = RE_DAI.sub(lambda m: f"第{kan2int(m[1])}", s)
    s = re.sub(r"(\d+)丁$", r"\1丁目", s)
    return s   # 「大字」は canon では落とさない (大字X と X が別の町字として並存する)

FOLD = str.maketrans({"ノ": "の", "之": "の", "ヶ": "ケ", "ヵ": "ケ", "斉": "斎", "舘": "館", "薮": "藪", "刈": "苅",
                      "嶽": "岳", "冨": "富", "渕": "淵", "澤": "沢", "濱": "浜", "邊": "辺", "邉": "辺", "嶋": "島",
                      "龍": "竜", "槇": "槙"})

def fold_from_canon(c):
    return re.sub(r"^(大字|字)", "", c.translate(FOLD))

def keys(s):
    s2, q = strip_q(unicodedata.normalize("NFKC", s).strip())
    c = canon(s2)
    return c, fold_from_canon(c), q

if __name__ == "__main__":
    for t in ["木ノ新保七番丁", "木の新保七番丁", "木ノ新保7番丁", "寺地町ニノ部", "湖南第二土地区画整理事業", "湖南第2区画整理地",
              "神田町五丁目", "山ノ上町５丁目", "山の上町5丁目", "鞍月東1丁", "丸塚町", "丸塚町", "字松ヶ沢", "芳斉", "藤江町地内"]:
        print(t, keys(t))
