"""地区の文法 (読み取り役 A。DESIGN.md §3.9・§2.3)

  python nationwide/readers/members.py "膝折町2丁目1～6番・7番の一部（黒目川右岸）・8～21番"

地区のマスの文字 → 中身 (members) のリスト。名前は資料の文字のまま (text・name_verbatim) で、条件は §2.3 の正規形
(range・except・remainder・partial・han・facility・line_side・enclave・note) にする。
町字の一覧 (ABR) には寄せない。町字への当て方は address-normalize (docs/address-normalize-schema.md) だけで行い、
当たり方を match に持つ。address-normalize はまだ呼べないので、ここでは同じ形を返す差し替え (AddressNormalizeStub) を使い、
match はいつも unchecked、machiaza_id は null、geometry は unresolved (どの地区とも言い切らない)。
カタカナの「ニ」(小松「向本折第ニ」、鶴ヶ島「脚折町ニ～六丁目」) は canon では寄せず、fold の段だけで「二」として読む
(fold でしか読めなかった中身は match: fold。裁定まで自動で選ばない)。
"""
import json, re, sys, unicodedata

KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
ZW = re.compile(r"[​‌‍﻿]")
DASH = r"[~〜～\-－ー―‐−]"
OPEN, CLOSE = "(（〔［[≪《〈<", ")）〕］]≫》〉>"
FACILITY = re.compile(r"団地|住宅|官舎|宿舎|社宅|自衛隊|駐屯地|寮|号棟|マンション|ハイツ|コーポ|アパート|集積所")
COMMUNITY = re.compile(r"自治会|町内会|町会|行政区|区会|部落|集落|組合|班$")
LINE_SIDE = re.compile(r"(?:(国道|県道|都道|府道|道道|市道|町道|村道|線路|鉄道|街道|通り|線|川|バイパス|自動車道)[^、・,()（）]{0,20}?)?"
                       r"(東|西|南|北|右岸|左岸|沿い|以東|以西|以南|以北)(?:側)?")
SIDE = re.compile(r"(.+?)(?:の)?((?:東|西|南|北|右岸|左岸|沿い|以東|以西|以南|以北)(?:側)?)")
REMAINDER = re.compile(r"^(?:上記|前記|左記|ほか|その他)?(?:以外|を除く|を除いた(?:区域|地域)?|の(?:区域|地域)以外)$")


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


# ---- address-normalize の差し替え ----
def kanji_num(s):
    if not s:
        return None
    if all(c in KANJI for c in s):
        return int("".join(str(KANJI[c]) for c in s))
    a, sep, b = s.partition("十")
    if sep and (not a or a in KANJI) and (not b or b in KANJI) and "十" not in b:
        return (KANJI[a] if a else 1) * 10 + (KANJI[b] if b else 0)
    return None


def canon(text):
    """address-normalize の canon の段 (§5 の表): NFKC、空白の除去、数字の間のダッシュを '-' に、
    漢数字 + 丁目・番丁・番町・条・線・地割 を算用数字に、「第 + 漢数字」を「第N」に。カタカナの「ニ」は寄せない"""
    t = re.sub(r"\s+", "", ZW.sub("", nfkc(text)))
    t = re.sub(r"(?<=\d)[\-－ー―‐−–—](?=\d)", "-", t)
    t = re.sub(r"([〇一二三四五六七八九十]+)(?=丁目|番丁|番町|条|線|地割)", lambda m: str(kanji_num(m.group(1)) or m.group(1)), t)
    t = re.sub(r"(?<=第)([〇一二三四五六七八九十]+)", lambda m: str(kanji_num(m.group(1)) or m.group(1)), t)
    t = re.sub(r"(\d)丁(?!目)", r"\1丁目", t)
    return t


VARIANTS = str.maketrans({"斉": "斎", "舘": "館", "薮": "藪", "刈": "苅", "嶽": "岳", "冨": "富", "渕": "淵", "澤": "沢",
                          "濱": "浜", "邊": "辺", "邉": "辺", "嶋": "島", "龍": "竜", "槇": "槙", "ノ": "の", "之": "の",
                          "ヶ": "ケ", "ヵ": "ケ"})


def fold(text):
    """address-normalize の fold の段: ノ・之 → の、ヶ・ヵ → ケ、異体字、先頭の「大字」「字」を外す。
    加えて、カタカナの「ニ」を、第のあと・数の区切り・丁目の前でだけ「二」として読む (§3.7 の決まり。地名の側)"""
    t = canon(text).translate(VARIANTS)
    t = re.sub(r"^(?:大字|字)", "", t)
    t = re.sub(r"(?<=第)ニ|ニ(?=" + DASH + r"|・|丁目|番)", "二", t)
    t = re.sub(r"([〇一二三四五六七八九十]+)(?=丁目|番|" + DASH + r"|・)", lambda m: str(kanji_num(m.group(1)) or m.group(1)), t)
    t = re.sub(r"(?<=" + DASH + r")([〇一二三四五六七八九十]+)(?=丁目)", lambda m: str(kanji_num(m.group(1)) or m.group(1)), t)
    return t


class AddressNormalizeStub:
    """address-normalize の resolveLabel の差し替え。町字の表を持たないので、照合はせず match_status: unchecked を返す
    (parsed の key_canon・key_fold は同じ規則で作る)。本物ができたら、同じ形の答え (match: exact / canonical / folded、
    match_status: unique / preferred / ambiguous / none) を返すものに差し替える"""
    name = "address-normalize-stub@0"

    def resolve_label(self, lg_code, text):
        return {"parsed": {"key_canon": canon(text), "key_fold": fold(text)}, "match_status": "unchecked", "match": None,
                "town": None, "auto_pick": False}


def to_member_match(res):
    """resolveLabel の答え → members[].match (exact / fold / ambiguous / none / unchecked) と machiaza_id"""
    st = res.get("match_status")
    if st == "unchecked":
        return "unchecked", None
    if st in ("unique", "preferred") and res.get("auto_pick") and res.get("match") in ("exact", "canonical"):
        return "exact", (res.get("town") or {}).get("machiaza_id")
    if st in ("unique", "preferred") and res.get("match") == "folded":
        return "fold", None
    if st == "ambiguous":
        return "ambiguous", None
    return "none", None


NORMALIZER = AddressNormalizeStub()


# ---- 分ける ----
def split_top(text, seps="、,，;；\n"):
    """かっこの外の区切りで分ける。「・」は、両側が名前のとき (数の間でないとき) だけ区切りにする"""
    out, cur, depth = [], "", 0
    t = nfkc(text)
    for i, c in enumerate(t):
        if c in OPEN:
            depth += 1
        elif c in CLOSE:
            depth = max(0, depth - 1)
        sep = depth == 0 and (c in seps or (c == "・" and not _num_dot(t, i)))
        if sep:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def _num_dot(t, i):
    """t[i] の「・」が数の並びの中か (「1・3～5」「野崎2・3丁目」「4・5丁目」「6番・7番」)"""
    a = t[i - 1] if i > 0 else ""
    b = t[i + 1] if i + 1 < len(t) else ""
    if a.isdigit() and b.isdigit():
        return True
    if (a.isdigit() or a in "番号目") and re.match(r"\d", t[i + 1:i + 3] or ""):
        return True
    if a in KANJI and b in KANJI:
        return True
    return False


# ---- 条件 ----
def _num(s):
    s = s.strip()
    if s.isdigit():
        return s
    n = kanji_num(s)
    return str(n) if n is not None else None


def ranges(text, unit):
    """「1～6」「1・3～5」「5-13，5-25～5-末」「177～」→ range の条件のリスト (読めなければ None)。
    丁目の範囲では「-」も範囲の印 (「1-3丁目」)。番・番地では「-」は番地の続き (「6-10～6-17」の「6-10」) で、範囲の印は「～」「から」だけ"""
    out = []
    mark = r"(?:[~〜～\-ー―‐−]|から)" if unit == "chome" else r"(?:[~〜～]|から)"
    num = r"\d+" if unit == "chome" else r"\d+(?:-\d+)*"
    for part in re.split(r"[・,、，]", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(" + num + r")\s*" + mark + r"\s*(" + num + r"|(?:\d+-)?末|)(?:まで)?", part)
        if m:
            to = m.group(2)
            to = "end" if to.endswith("末") else (to or None)
            out.append({"kind": "range", "unit": unit, "from": m.group(1), "to": to})
            continue
        if re.fullmatch(num, part):
            out.append({"kind": "range", "unit": unit, "from": part, "to": part})
            continue
        return None
    return out or None


def _paren_conditions(inner):
    """かっこの中 → 条件のリスト"""
    t = canon(inner)
    if REMAINDER.match(t):
        return [{"kind": "remainder", "text": inner}]
    m = re.fullmatch(r"(.+?)(?:を)?(?:除く|以外|は除く)", t)
    if m:
        body = m.group(1)
        if re.fullmatch(r"飛(?:び)?地", body):
            return [{"kind": "enclave", "negate": True, "text": inner}]
        han = re.findall(r"(\d+)班", body)
        if han:
            return [{"kind": "han", "values": han, "negate": True, "text": inner}]
        ch = re.fullmatch(r"(.+?)丁目", body)
        if ch:
            r = ranges(ch.group(1).replace("から", "~"), "chome")
            if r:
                return [{"kind": "except", "of": r, "text": inner}]
        bn = re.fullmatch(r"(.+?)(番地|番|号)", body)
        if bn:
            r = ranges(bn.group(1), {"番地": "banchi", "番": "ban", "号": "go"}[bn.group(2)])
            if r:
                return [{"kind": "except", "of": r, "text": inner}]
        if FACILITY.search(body):
            return [{"kind": "facility", "name": body, "negate": True, "text": inner}]
        return [{"kind": "except", "of": [{"kind": "note", "text": body}], "text": inner}]
    sides = [s for s in re.split(r"[・、,]", t) if s]
    ls = []
    for s in sides:
        mm = SIDE.fullmatch(s)
        if mm and re.search(r"側|岸|沿い|以[東西南北]", s):
            ls.append({"kind": "line_side", "line": mm.group(1), "side": mm.group(2), "text": s})
    if ls and len(ls) == len(sides):
        return ls
    han = re.fullmatch(r"(\d+(?:・\d+)*)班", t)
    if han:
        return [{"kind": "han", "values": han.group(1).split("・"), "negate": False, "text": inner}]
    if re.search(r"一部", t):
        return [{"kind": "partial", "split_known": False, "text": inner}]
    return [{"kind": "note", "text": inner}]


def _strip_parens(s):
    """末尾のかっこ書きを外す → (本体, [かっこの中])"""
    inners = []
    while True:
        m = re.search(r"[(（〔［\[≪《〈<]([^()（）〔〕［］\[\]≪≫《》〈〉<>]*)[)）〕］\]≫》〉>]\s*$", s)
        if not m:
            break
        inners.insert(0, m.group(1))
        s = s[:m.start()].rstrip()
    return s, inners


def parse_member(text, lg_code=None, normalizer=None):
    """中身 1 つの文字 → member の dict。読めなければ unit: unparsed (文のまま。当てない)"""
    normalizer = normalizer or NORMALIZER
    raw = text.strip()
    body, inners = _strip_parens(raw)
    pconds = [c for inner in inners for c in _paren_conditions(inner)]
    t = canon(body)
    name, conds = t, []
    if re.search(r"(?<=第)ニ|ニ(?=" + DASH + r"|・|丁目|番)", t) and fold(body) != t.translate(VARIANTS):
        # 「脚折町ニ～六丁目」: カタカナの「ニ」は fold の段でだけ数に読む → fold の読みで中身を作り、match: fold (裁定まで選ばない)
        inner = parse_member(fold(body) + "".join(f"（{x}）" for x in inners), lg_code, normalizer)
        inner["text"] = raw
        inner["match"] = "fold"
        inner["extra"]["fold_only"] = True
        inner["extra"]["name_verbatim"] = _verbatim_name(body, inner["extra"].get("name_verbatim") or "")
        return inner
    whole = re.search(r"全域$", t) and not re.fullmatch(r"(?:町内|市内|村内|区内)?全域", t)
    if whole:
        t = name = t[:-2]                       # 三鷹「深大寺全域」は町字の丸ごと (名前は「深大寺」)
        body = re.sub(r"全域\s*$", "", body)
    rng = r"\d+(?:(?:から|" + DASH + r")\d+)?"
    t = re.sub(r"(\d+)丁目(?:から|" + DASH + r")(\d+)丁目", r"\1~\2丁目", t)     # 三鷹「上連雀1丁目～5丁目」= 1～5丁目
    m = re.fullmatch(r"(.+?)((?:" + rng + r"[・,、])*" + rng + r")丁目(.*)", t)
    if m:
        r = ranges(m.group(2).replace("から", "~"), "chome")
        rb = _rest_blocks(m.group(3))
        if r is None or rb is None:
            return _unparsed(raw, normalizer)
        name, conds = m.group(1), r + rb
    elif re.search(r"\d+(?:番地|番|号)|\d+-\d+", t):
        mm = re.fullmatch(r"(\D+?)(\d.*)", t)
        rb = _rest_blocks(mm.group(2)) if mm else None
        if rb is None:
            return _unparsed(raw, normalizer)
        name, conds = mm.group(1), rb
    elif not re.search(r"\d", t) and re.search(r"\d", fold(body)):
        # 「脚折町ニ～六丁目」: fold でしか数が読めない → fold の読みで中身を作り、match: fold (裁定まで選ばない)
        inner = parse_member(fold(body) + "".join(f"（{x}）" for x in inners), lg_code, normalizer)
        inner["text"] = raw
        inner["match"] = "fold"
        inner["extra"]["fold_only"] = True
        inner["extra"]["name_verbatim"] = _verbatim_name(body, inner["extra"].get("name_verbatim") or "")
        return inner
    hm = re.fullmatch(r"(.+?)(\d+)班", name)
    if hm:
        name = hm.group(1)
        conds.append({"kind": "han", "values": [hm.group(2)], "negate": False, "text": hm.group(2) + "班"})
    pm = re.search(r"の?一部$", name)
    if pm:
        name = name[:pm.start()]
        if not any(c["kind"] == "line_side" for c in pconds):
            conds.insert(0, {"kind": "partial", "split_known": False, "text": "一部"})   # 分け方が書いていない「一部」
    conds += pconds
    unit = "machiaza"
    if REMAINDER.match(name):
        unit, name = "remainder", ""
    elif any(c["kind"] == "range" and c["unit"] != "chome" for c in conds):
        unit = "block_range"
    elif any(c["kind"] == "line_side" for c in conds):
        unit = "line_side"                      # 「大字浜崎の一部（県道和光志木線北側）」: 分け方は線の片側として書いてある
    elif COMMUNITY.search(name) or any(c["kind"] == "han" for c in conds):
        unit = "community"
    elif FACILITY.search(name) and not any(c["kind"] == "range" for c in conds):
        unit = "facility"
    elif name in ("全域", "全町", "全村", "全市", "全区", "全地区", "町内全域", "市内全域", "村内全域"):
        unit = "municipality"
    elif any(c["kind"] == "remainder" for c in conds):
        unit = "remainder"
    res = normalizer.resolve_label(lg_code, name) if name else {"match_status": "unchecked"}
    match, mid = to_member_match(res)
    return {"text": raw, "unit": unit, "machiaza_id": mid, "match": match, "conditions": conds,
            "geometry": "unresolved",          # 当て方 (address-normalize) が無いうちは、どの中身も場所を決めない
            "extra": {"name_verbatim": _verbatim_name(body, name), "key_canon": (res.get("parsed") or {}).get("key_canon"),
                      "key_fold": (res.get("parsed") or {}).get("key_fold"), "normalizer": normalizer.name}}


def _verbatim_name(body, name):
    """資料の文字のままの名前 (canon する前)。丁目・番・「の一部」の前まで"""
    b = nfkc(body).strip()
    m = re.match(r"(\D+?)(?=[\d〇一二三四五六七八九十ニ]+(?:丁目|番|号|" + DASH + r"|から|・)|の?一部$|$)", b)
    v = (m.group(1) if m else b).strip()
    return v or name


UNIT_WORDS = {"番地": "banchi", "番": "ban", "号": "go", "号棟": "gotou", "街区": "gaiku"}


def _rest_blocks(rest):
    """丁目・名前のあとの番・号の並び (「1～6番・7番の一部（黒目川右岸）・8～21番」「6-2，6-10～6-17」「133-3～21,177～」)
    → 条件のリスト。単位の無い数は、後ろの単位の付いた数の単位を使う。読めなければ None"""
    rest = (rest or "").strip()
    if not rest:
        return []
    out, pending = [], []
    parts = split_top(rest, seps="、,，・")
    for part in parts:
        p, inners = _strip_parens(part)
        extra = [c for inner in inners for c in _paren_conditions(inner)]
        partial = bool(re.search(r"の?一部$", p))
        p = re.sub(r"の?一部$", "", p)
        m = re.fullmatch(r"(.+?)(番地|号棟|番|号|街区)", p)
        num, unit = (m.group(1), UNIT_WORDS[m.group(2)]) if m else (p, None)
        if not re.fullmatch(r"\d+(?:-\d+)*(?:(?:[~〜～]|から)(?:\d+(?:-\d+)*|(?:\d+-)?末)?)?", num):
            return None
        pending.append((num, part, partial, extra))
        if unit:
            for n, pt, pa, ex in pending:
                r = ranges(n.replace("から", "~"), unit)
                if r is None:
                    return None
                if pa:
                    out.append({"kind": "partial", "split_known": False, "text": pt})
                out += r + ex
            pending = []
    for n, pt, pa, ex in pending:
        r = ranges(n.replace("から", "~"), "banchi")
        if r is None:
            return None
        if pa:
            out.append({"kind": "partial", "split_known": False, "text": pt})
        out += r + ex
    return out


def _unparsed(raw, normalizer):
    return {"text": raw, "unit": "unparsed", "machiaza_id": None, "match": "unchecked", "conditions": [], "geometry": "unresolved",
            "extra": {"name_verbatim": raw, "normalizer": normalizer.name}}


def parse_cell(text, lg_code=None, normalizer=None, town=None):
    """地区のマスの文字 → members のリスト。town (町名の列の文字) があれば、このマスは丁目・番の列として町名に付ける
    (世田谷「赤堤」+「1・3～5」、中央「銀座」+「1～2丁目」・「全域」)"""
    t = nfkc(text).strip()
    if town is not None:
        tn = nfkc(town).strip()
        if re.fullmatch(r"全域|全部|全て|すべて|[ー\-―‐−－]", t) or not t:
            m = parse_member(tn, lg_code, normalizer)
            m["text"] = f"{town} {text}".strip()
            return [m]
        if re.fullmatch(r"[\d〇一二三四五六七八九十・,、~〜～\-ー]+(?:丁目)?", t):
            ch = t if t.endswith("丁目") else t + "丁目"
            m = parse_member(tn + ch, lg_code, normalizer)
            m["text"] = f"{town} {text}".strip()
            m["extra"]["name_verbatim"] = tn
            return [m]
        m = parse_member(tn + t, lg_code, normalizer)
        m["text"] = f"{town} {text}".strip()
        m["extra"]["name_verbatim"] = tn
        return [m]
    parts = []
    for part in split_top(t):
        # 数で始まる切れ端 (「江北１～５、７丁目」の「７丁目」、「6-2，6-10～6-17」の「6-10～6-17」) は前の中身の続き
        if parts and re.match(r"[\d〇一二三四五六七八九十]", part) and not re.match(r"[〇一二三四五六七八九十]\D", part):
            parts[-1] += "、" + part
        else:
            parts.append(part)
    return [parse_member(part, lg_code, normalizer) for part in parts]


def strip_district_title(text):
    """ページの題の地区の名前 (三鷹「令和8年度 大沢3丁目・深大寺全域・井口全域・野崎2・3丁目地区」) → 地区の文字の部分"""
    t = nfkc(text).strip()
    t = re.sub(r"^(?:令和|平成)\s*(?:元|\d{1,2})\s*年度?\s*|^20\d\d\s*年度?\s*", "", t)
    t = re.sub(r"\s*(?:の)?(?:地区|地域)(?:用)?$", "", t)
    return t.strip()


# ---- 地名として読める形 (閉じた並び。7 回目) ----
# 型 C の見出し・地区の列のマスを、地区にしてよいかの判定に使う。ここに当たらない名前 (「不燃ごみは奇数月です」「祝日のある週」
# 「変更前」「試行」「特例」) は地区にしない (地区にすると、その節・行の規則がページの地区の規則と別の系列になり、両方が答える)。
# 迷えば地区にしない (止める方): ページの地区のまま読み、見出し・行は但し書きにする
_PN_CH = r"[一-鿿〇々〆ヶヵケァ-ヺー]"                                                # 漢字・カタカナ (地名の字)
_PN_HIRA = r"[ぁ-ゖ]"
_PN_SUFFIX = r"(?:丁目|町内会|自治会|町会|地区|地域|区域|団地|集落|ブロック|コース|方面|町|村|区|字|班|組|部)"
# 地名でない言葉 (日程・条件・版・品目・説明の言葉)。名前の中にあれば地名と読まない
_PN_NOT = re.compile(r"曜|週|毎|隔|[\d〇一二三四五六七八九十]+回|収集|回収|休み|休止|休日|休業|祝日|祝祭|祝休|祭日|振替|年末|年始|期間|[前後上下]?期|季|旬|シーズン|"
                     r"ごみ|ゴミ|資源|変更|改定|改正|予備|臨時|試行|当面|暫定|雨天|通常|特別|特例|例外|以降|以前|以外|現在|令和|平成|"
                     r"年度|予定|日程|カレンダー|お知らせ|注意|奇数|偶数|隔月|一覧|町名|地名|住所|お住|住まい|対象|新しい|変わ|場合|"
                     r"とき|ため|こと|です|ます|ません|ください|全域|全市|市内|全地区|共通|一部|全部|部分|本部|支部|"
                     r"備考|注記|注釈|摘要|合計|凡例|補足|説明|参考|問い?合わ?せ|問合せ|指定|特定|該当|下記|以下|以上|^[旧新春夏秋冬]$")
_PN_NUM = re.compile(r"第?\d{1,3}(?:区|地区|班|組|町内会|自治会|ブロック|コース|方面)?|[A-Z]{1,2}(?:地区|区|コース|ブロック)?|"
                     r"(?:コース|ルート)\d{1,3}|[①-⑳]")
# 残りの地区 (「上記以外」「その他の地域」)。地区の列のマスでは地区として読む (見出しでは総称の言葉)
_PN_REST = re.compile(r"(?:その他|上記以外|前記以外|左記以外|ほか)の?(?:地域|地区|区域|町|全域)?")
_PN_WORD = re.compile(r"(?:大字|字)?(?:" + _PN_CH + r"|" + _PN_HIRA + r")+")                     # 東町・藤蔵・中の島・ひばりが丘
_PN_PARTICLE = re.compile(r"(?<=[一-鿿〇々ァ-ヺー])[はをにで](?=[一-鿿〇々ァ-ヺー])")         # 「東町は翌日」の「は」 (文であって名前でない)
_PN_SUFFIXED = re.compile(r"(?:大字|字)?(?:" + _PN_CH + r"|" + _PN_HIRA + r")+?\d{0,3}" + _PN_SUFFIX)          # あけぼの町・本町1丁目・東1区
_PN_HEAD = re.compile(r"^(?:大字|字)?(?P<stem>(?:" + _PN_CH + r"|" + _PN_HIRA + r"){1,12}?\d{0,3})" + _PN_SUFFIX)


def place_like(name, strict=False):
    """名前 (members の name_verbatim) が地名として読める形か: 番号・英字の地区 (第5区・A地区・1・①)、地区の字で終わる名前
    (東町・本町1丁目・あけぼの町・北部)、上記以外。strict でなければ名前の字だけの名前 (藤蔵・中の島。地区の列のマス・名前の並び) も。
    地名でない言葉 (_PN_NOT) を含めば読まない"""
    t = re.sub(r"\s+", "", ZW.sub("", nfkc(name or "")))
    inner = re.findall(r"\(([^()]*)\)", t)
    if inner and not any(_PN_NOT.search(x) for x in inner):
        t = re.sub(r"\([^()]*\)", "", t)                     # 「(寺井町)横町」の旧町名のかっこ書きは名前の一部 (中身に日程の言葉が無いとき)
    if not t or len(t) > 20:
        return False
    if _PN_NUM.fullmatch(t) or REMAINDER.match(t):
        return True
    if _PN_NOT.search(t) or _PN_PARTICLE.search(t):
        return False
    if _PN_SUFFIXED.fullmatch(t):
        return True
    return not strict and bool(_PN_WORD.fullmatch(t))


def place_part(text, lg_code=None):
    """見出しの文字に地名の部分があるか: 地区の文法の中身の名前が 2 つ以上でどれも地名として読める (「藤蔵・中島」)、1 つで地区の字で
    終わる (「西町（9月改定）」「日野町」「A地区」)、か頭が地区の字で終わる地名 (「西町地区のごみ収集日」の「西町」)。
    地区の字の無い 1 つの名前 (「中島」「特例」) は、地名か見分けられないので地区にしない (止める方)"""
    ms = parse_cell(text, lg_code)
    names = [(m.get("extra") or {}).get("name_verbatim") or "" for m in ms]
    if len(names) >= 2 and all(place_like(n) for n in names):
        return True
    if len(names) == 1 and place_like(names[0], strict=True):
        return True
    t = re.sub(r"\s+", "", ZW.sub("", nfkc(text or "")))
    mm = _PN_HEAD.match(t)
    return bool(mm) and not _PN_NOT.search(mm.group(0)) and not re.fullmatch(r"\d*", mm.group("stem"))


def place_rest(text, lg_code=None):
    """地名の部分のある見出しの、地名を除いた残りの文字 (「西町（9月改定）」→「（9月改定）」、「東町（祝日の週）」→「（祝日の週）」。
    地名だけなら空)。名前として読めた中身の名前と、頭の地区の字で終わる地名を除く"""
    t = re.sub(r"\s+", "", ZW.sub("", nfkc(text or "")))
    for m in parse_cell(text, lg_code):
        n = re.sub(r"\s+", "", nfkc((m.get("extra") or {}).get("name_verbatim") or ""))
        if n and place_like(n) and n in t:
            t = t.replace(n, "", 1)
    mm = _PN_HEAD.match(t)
    if mm and not _PN_NOT.search(mm.group(0)):
        t = t[mm.end():]
    return re.sub(r"^[・、,/／\s]+|[・、,/／\s]+$", "", t)


def _cell_place(m):
    """地区の列のマスの中身 1 つが地名として読めるか (残りの地区・全域も)"""
    name = (m.get("extra") or {}).get("name_verbatim") or ""
    return m.get("unit") in ("remainder", "municipality") or bool(_PN_REST.fullmatch(nfkc(name).replace(" ", ""))) or place_like(name)


_PN_LEAD = re.compile(r"(?:[(（][^()（）]*[)）])?(?:" + _PN_CH + r"|" + _PN_HIRA + r")+")      # 頭の名前の字の並び (数の前まで)


def _head_place(name):
    """名前の頭の名前の字の並び (「東町 ※祝日は収集しません」「東町※」の「東町」) か、頭の地区の字で終わる地名 (「東町隔週」の「東町」) が
    地名として読めるか (名前のマスの読み残しは、その地区だけの但し書きになる。6 回目の形)"""
    t = re.sub(r"\s+", " ", ZW.sub("", nfkc(name or ""))).strip()
    mm = _PN_LEAD.match(t)
    if mm and len(mm.group(0)) >= 2 and place_like(mm.group(0)):
        return True
    mm = _PN_HEAD.match(t.replace(" ", ""))
    return bool(mm) and not _PN_NOT.search(mm.group(0)) and not re.fullmatch(r"\d*", mm.group("stem"))


def any_place(text, lg_code=None):
    """地区の列のマスに、地名として読める中身が 1 つでもあるか (頭の名前だけでも。無ければ地区の行でなく表の注の行:
    「不燃ごみは奇数月です」「第5週は収集しません」)"""
    return any(_cell_place(m) or _head_place((m.get("extra") or {}).get("name_verbatim") or m.get("text") or "")
               for m in parse_cell(text, lg_code))


def readable_district(text, lg_code=None):
    """地区のマスが地区の文法で読めるか (中身の名前がすべて地名として読め、読めない書き添え (note) が無い)。
    品目がすべて無し・空の行の地区のマス (「不燃ごみは奇数月です」) が読めなければ、その行は表の注の行"""
    ms = parse_cell(text, lg_code)
    if not ms:
        return False
    for m in ms:
        if m.get("unit") == "unparsed" or not _cell_place(m):
            return False
        if any(c.get("kind") == "note" for c in m.get("conditions") or []):
            return False
    return True


def _cond_key(c):
    c = {k: v for k, v in c.items() if k not in ("text",)}
    if "of" in c:
        c["of"] = sorted(_cond_key(x) for x in _merge_ranges(c["of"]))
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def _merge_ranges(conds):
    """数だけの範囲 (丁目・番・号) を、単位ごとに続いた範囲にまとめ直す (「1・2」と「1～2」を同じにする)"""
    out, by = [], {}
    for c in conds:
        f, t = c.get("from"), c.get("to")
        if c.get("kind") == "range" and str(f).isdigit() and str(t).isdigit() and int(f) <= int(t):
            by.setdefault(c["unit"], set()).update(range(int(f), int(t) + 1))
        else:
            out.append(c)
    for unit, nums in by.items():
        run = []
        for n in sorted(nums) + [None]:
            if run and (n is None or n != run[-1] + 1):
                out.append({"kind": "range", "unit": unit, "from": str(run[0]), "to": str(run[-1])})
                run = []
            if n is not None:
                run.append(n)
    return out


def key(member):
    """2 つの読みの比べの鍵: (単位, 名前の正規化, 条件の正規形の集まり)。言い回し (「1から3丁目」と「1～3丁目」、「1・2丁目」と
    「1～2丁目」、「以外」と「を除く」) では食い違いにしない (§3.9)。名前に丁目・番が付いていれば (「膝折町3丁目」) その前まで"""
    nv = member.get("name_verbatim") or (member.get("extra") or {}).get("name_verbatim") or member.get("text", "")
    name = canon(re.sub(r"全域$", "", nfkc(nv).strip()))
    name = re.sub(r"\d+(?:[~〜～\-]\d+)?(?:丁目|番地?|号).*$", "", name) or name
    conds = _merge_ranges(member.get("conditions", []))
    return (member.get("unit"), name, tuple(sorted(_cond_key(c) for c in conds)))


def _grouped_keys(ms):
    """members の鍵の並び。同じ (単位, 名前) で条件が数の範囲だけの中身は 1 つにまとめる
    (「大沢1・2・4丁目」を 1 つの中身と書いても、丁目ごとに 3 つと書いても同じ。§3.9 の言い回しの違い)"""
    out, merged = [], {}
    for m in ms:
        u, name, conds = key(m)
        cs = [json.loads(c) for c in conds]
        if cs and all(c.get("kind") == "range" and str(c.get("from")).isdigit() and str(c.get("to")).isdigit() for c in cs):
            merged.setdefault((u, name), []).extend(cs)
        else:
            out.append((u, name, conds))
    for (u, name), cs in merged.items():
        out.append((u, name, tuple(sorted(_cond_key(c) for c in _merge_ranges(cs)))))
    return sorted(out)


def same(a, b):
    """2 つの読みの members のリストが同じか (順番は見ない。同じ町の丁目の範囲は 1 つにまとめて比べる)"""
    return _grouped_keys(a) == _grouped_keys(b)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    for a in argv:
        print(json.dumps(parse_cell(a), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
