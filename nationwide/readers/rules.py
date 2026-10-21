"""規則の文法 (読み取り役 A) と、方針の文の文法 (DESIGN.md §3.7・§2.6)

  python nationwide/readers/rules.py "第１・３・(5)火曜日"            # 規則の文 → IR (と日付の見本)
  python nationwide/readers/rules.py --policy "年末は12月29日まで" --fy 2026   # 方針の文 → 例外の形

ここでは文 → IR (中間の形) と、IR → 日付 (休み・年末年始を考えない素の日付)、RRULE への圧縮だけをする。
休み・年末年始・印字との比べを当てて系列にするのは expand.py。

文字の前処理 (この文法の中だけ): NFKC、空白 (ゼロ幅を含む) を除く、区切り「･」「、」「，」「,」「と」を「・」に、
「曜日」「曜」の省略、「第」の省略 (「2･4水」)、漢数字 (「第二・四」) を数字に。カタカナの「ニ」を「二」として読むのは
「第」と区切りの間だけ (「第ニ・四水」)。地名の中の「ニ」(小松「向本折第ニ」) はここでは寄せない (地区の文法の側。members.py)。
読めなかった文は IR にしない (None。rule_unparsed)。「第N週」は、資料が意味を決めていなければ ambiguous_week (広げない)。
"""
import datetime as dt, re, sys, unicodedata

import consume

WD = {"月": "MO", "火": "TU", "水": "WE", "木": "TH", "金": "FR", "土": "SA", "日": "SU"}
WD_ORDER = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WD_IDX = {w: i for i, w in enumerate(WD_ORDER)}
KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# 「無し」のしるし (§3.7)。NFKC ではこれらは 1 つにならないので、この表で寄せる
NONE_MARKS = set("ー-―‐−✕×－")
ZW = re.compile(r"[\s​‌‍﻿]+")
SEP = "・"
W = r"[月火水木金土日]"
WDS = W + r"(?:曜日?)?"                       # 「水」「水曜」「水曜日」
NUMS = r"\d{1,2}(?:・\(?\d{1,2}\)?)*"        # 1・3・(5)


# ---- 前処理 ----
def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def _kanji_num(s):
    """漢数字の数 (一〜三十一。「十」「二十五」も) → int。読めなければ None"""
    if not s or any(c not in KANJI and c != "十" for c in s):
        return None
    if "十" not in s:
        return int("".join(str(KANJI[c]) for c in s)) if len(s) <= 2 else None
    a, _, b = s.partition("十")
    if "十" in b:
        return None
    return (KANJI[a] if a else 1) * 10 + (KANJI[b] if b else 0) if (not a or a in KANJI) and (not b or b in KANJI) else None


def _num_run(m):
    """数字の並び (漢数字・カタカナのニを含む) を算用数字に"""
    out = []
    for part in re.split(r"(・)", m.group(0)):
        if part == "・" or not part:
            out.append(part)
            continue
        p = part.replace("ニ", "二")
        lead, core, trail = re.match(r"^(\(?)(.*?)(\)?)$", p).groups()
        n = core if core.isdigit() else _kanji_num(core)
        out.append(f"{lead}{n}{trail}" if n is not None else part)
    return "".join(out)


_NUMCH = r"[0-9〇一二三四五六七八九十ニ]"
# 「第」のあとの数の並び (第二・四、第ニ、第1・第3)。カタカナの「ニ」は、すぐ後が区切り・曜日・回・週・番・(・) のときだけ数に読む
_DAI_RUN = re.compile(r"(?<=第)\(?" + _NUMCH + r"{1,3}\)?(?:・第?\(?" + _NUMCH + r"{1,3}\)?)*(?=[・月火水木金土日回週番)(]|曜|$)")
# 回目・番目・週目・週の前の数の並び (一回目と三回目 → 1・3回目)
_ORD_RUN = re.compile(r"\(?[0-9〇一二三四五六七八九十]{1,3}\)?(?:・\(?[0-9〇一二三四五六七八九十]{1,3}\)?)*(?=回目|番目|週目|週)")
_DAY_RUN = re.compile(r"(?<=毎月)[0-9〇一二三四五六七八九十]{1,3}(?:・[0-9〇一二三四五六七八九十]{1,3})*(?=日)")


def prep(text):
    """規則の文法だけで使う前処理 → 文字列"""
    t = ZW.sub("", nfkc(text))
    t = t.replace("･", SEP).replace("、", SEP).replace(",", SEP).replace("，", SEP).replace("&", SEP)
    t = re.sub(r"(?<=[\d回目週番月火水木金土日曜])(?:と|及び|および|又は|または)(?=第?[\d一二三四五六七八九十ニ月火水木金土日])", SEP, t)
    t = re.sub(r"・第(?=\(?[\d一二三四五六七八九十ニ])", "・", t)      # 第1・第3 → 第1・3
    t = re.sub(r"(?<=[はが])・", "", t)                                  # 「ペットボトルは、5・6…月」の読点
    t = _DAI_RUN.sub(_num_run, t)
    t = _ORD_RUN.sub(_num_run, t)
    t = _DAY_RUN.sub(_num_run, t)
    t = re.sub(r"(\d)回目・(?=\d)", r"\1・", t)                            # 1回目・3回目 → 1・3回目
    t = re.sub(r"(\d)番目・(?=\d)", r"\1・", t)
    t = re.sub(r"(\d)週・(?=第?\d)", r"\1・", t)                            # 第1週・第3週 → 第1・3週
    t = re.sub(r"(\d)週目・(?=\d)", r"\1・", t)
    return t


def _nums(s):
    """「1・3・(5)」→ [1, 3, 5]"""
    return [int(x) for x in re.findall(r"\d+", s)]


def _wds(s):
    """「月・木」「月曜日・木曜日」「月曜日~土曜日」→ ["MO", "TH"] (範囲は広げる)"""
    s = s.replace("曜日", "").replace("曜", "")
    m = re.fullmatch(r"(" + W + r")[~〜～\-ー](" + W + r")", s) or re.fullmatch(r"(" + W + r")から(" + W + r")(?:まで)?", s)
    if m:
        a, b = WD_IDX[WD[m.group(1)]], WD_IDX[WD[m.group(2)]]
        if a > b:
            return None
        return list(WD_ORDER[a:b + 1])
    parts = [p for p in s.split(SEP) if p]
    if not parts or any(p not in WD for p in parts):
        return None
    out = []
    for p in parts:
        if WD[p] not in out:
            out.append(WD[p])
    return sorted(out, key=WD_IDX.get)


# ---- 規則の文 → IR ----
_WD_LIST = WDS + r"(?:・" + WDS + r")*"
_WD_RANGE = WDS + r"(?:[~〜～\-]|から)" + WDS + r"(?:まで)?"
RX = {
    "except": re.compile(r"[(（]?(?:第)?(" + NUMS + r")(?:回目|番目)(?:の(" + W + r")曜日?)?(?:は|を)?除(?:く|き)[)）]?"),
    "nth": re.compile(r"^(?:毎月)?(?:第)?(" + NUMS + r")(?:回目|番目)?(?:の)?(" + _WD_LIST + r")$"),
    "nth_week": re.compile(r"^(?:毎月)?(?:第)?(" + NUMS + r")週目?(?:の)?(" + _WD_LIST + r")$"),
    "weekly": re.compile(r"^(?:毎週)?(?:の)?(" + _WD_RANGE + r"|" + _WD_LIST + r")$"),
    "last": re.compile(r"^(?:毎月)?(?:最終|月末の?|最後の)(" + WDS + r")$"),
    "biweekly": re.compile(r"^隔週(?:の)?(" + WDS + r")$|^(" + WDS + r")\(?隔週\)?$"),
    "cycle": re.compile(r"^(\d{1,2})週(?:間)?(?:に1回|ごと|毎)(?:の)?(" + WDS + r")$"),
    "mday": re.compile(r"^毎月(\d{1,2}日?(?:・\d{1,2}日?)*)日$"),
    "on_demand": re.compile(r"随時|申込|申し込|予約|要連絡|電話で"),
    "months": re.compile(r"^((?:\d{1,2}・)+\d{1,2}|\d{1,2})月(?:のみ|だけ|の|は)(.+)$"),
    # 季節の枠。日まで書いてあれば日を枠の端にする (「4月15日～9月15日は毎週月曜日」を 4〜9 月まるごとにしない)
    "season": re.compile(r"^(\d{1,2})月(?:(\d{1,2})日)?[~〜～\-]+(\d{1,2})月(?:(\d{1,2})日)?(?:の間)?(?:は|のみ|だけ)?(.+)$"),
    "subcycle": re.compile(r"(\d{1,2})回に1回"),
}
_STRIP = re.compile(r"^(?:収集日?|回収日?|指定日)[:：]?|(?:に)?(?:収集|回収)(?:します)?$")
_WEEK_ANY = re.compile(r"第?(" + NUMS + r")週(?!間)目?")
_SUBJECT = re.compile(r"^((?:(?!年末|年始)[^\d第毎隔曜]){1,12}?)(?:は|が)(?=\d{1,2}(?:・\d{1,2})*月|\d{1,2}月[~〜～\-])")


def is_none_mark(text):
    """マスの文字が「無し」のしるしだけか (大田の「ー」「-」)"""
    t = ZW.sub("", nfkc(text))
    return bool(t) and all(c in NONE_MARKS for c in t)


def _one(t):
    """前処理した 1 つの規則の文 → IR か None"""
    t = _STRIP.sub("", t)
    m = RX["except"].search(t)
    ex = None
    if m:
        ex = _nums(m.group(1))
        exwd = m.group(2)
        t = (t[:m.start()] + t[m.end():]).strip()
        ir = _one(t)
        if not ir or ir["kind"] not in ("weekly", "monthly_nth"):
            return None
        if exwd and ir["kind"] == "weekly" and ir["weekdays"] != [WD[exwd]]:
            return None
        if ir["kind"] == "weekly":
            ir["except_nth"] = sorted(set(ex))
            return ir
        nth = [n for n in ir["nth"] if n not in ex]
        return dict(ir, nth=nth) if nth else None
    if (m := RX["weekly"].match(t)):
        wds = _wds(m.group(1))
        return {"kind": "weekly", "weekdays": wds} if wds else None
    if (m := RX["nth_week"].match(t)):
        wds = _wds(m.group(2))
        if not wds or len(wds) != 1:
            return None
        return {"kind": "ambiguous_week", "weekday": wds[0], "weeks": sorted(set(_nums(m.group(1))))}
    if (m := RX["nth"].match(t)) and m.group(2) != "日":    # 「毎月1日」「1・3日」の「日」だけは曜日ではない (日付。下の monthly_day へ)
        wds = _wds(m.group(2))
        ns = sorted(set(_nums(m.group(1))))
        if wds and len(wds) == 1 and ns and all(1 <= n <= 5 for n in ns):
            return {"kind": "monthly_nth", "weekday": wds[0], "nth": ns}
        return None
    if (m := RX["last"].match(t)):
        w = _wds(m.group(1))
        return {"kind": "monthly_last", "weekday": w[0]} if w else None
    if (m := RX["biweekly"].match(t)):
        w = _wds(m.group(1) or m.group(2))
        return {"kind": "biweekly", "weekday": w[0], "anchor": None} if w and len(w) == 1 else None
    if (m := RX["cycle"].match(t)):
        w = _wds(m.group(2))
        n = int(m.group(1))
        if not w or len(w) != 1 or n < 1:
            return None
        if n == 1:
            return {"kind": "weekly", "weekdays": w}
        if n == 2:
            return {"kind": "biweekly", "weekday": w[0], "anchor": None}
        return {"kind": "cycle", "weekday": w[0], "every_weeks": n, "anchor": None}
    if (m := RX["mday"].match(t)):
        ds = sorted(set(_nums(m.group(1))))
        return {"kind": "monthly_day", "days": ds} if ds and all(1 <= d <= 31 for d in ds) else None
    if (m := RX["season"].match(t)):
        a, da, b, db = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        inner = _one(m.group(5))
        if not inner or not (1 <= a <= 12 and 1 <= b <= 12):
            return None
        # 月だけの終わりは、その月の終わりの日まで (2 月は 29 日まで: うるう年の 2 月 29 日を落とさない。うるう年でなければ当たる日が無い)
        da, db = int(da) if da else 1, int(db) if db else _last_day(b)
        if not (1 <= da <= _last_day(a) and 1 <= db <= _last_day(b)):
            return None                              # 枠の端の日が月に無い (「4月31日」・「2月30日」) → 読まない
        return {"kind": "seasonal", "from": f"{a:02d}-{da:02d}", "to": f"{b:02d}-{db:02d}", "rule": inner}
    if (m := RX["months"].match(t)):
        ms = sorted(set(_nums(m.group(1))), key=lambda x: (x < 4, x))
        add = _ADDS.fullmatch(m.group(2))            # 足す言い方は文の終わりの決まった形だけ (後ろの文字を消して読まない)
        inner = _one(add.group(1) if add else m.group(2))
        if not inner or any(not 1 <= x <= 12 for x in ms):
            return None
        ir = {"kind": "months", "months": ms, "rule": inner}
        if add:
            ir["adds_to"] = None                     # 足す規則 (どの品目に足すかは呼ぶ側が決める。それだけでは広げない: is_additive)
        return ir
    return None


# 足す規則の終わり (「第1水曜日でも出せます」「第1水曜日にも収集します」「第1水曜日も」)。この後ろに文字があれば規則にしない
# (「…でも出せますが、8月は休み」「…でも出せます（雨天中止）」の後ろは rest に残り、使われなければ但し書き)
_ADDS = re.compile(r"(.+?)(?:でも|にも|も)(?:(?:出せ|出し)(?:ます|る)?|(?:収集|回収)(?:します|する|できます)?)?")


def is_additive(ir):
    """足す規則 (adds_to を持つ months。中の規則も見る) か。ほかの規則に足す日なので、それだけを系列の規則として広げない"""
    while ir:
        if "adds_to" in ir:
            return True
        ir = ir.get("rule")
    return False


def _last_day(m):
    """月の終わりの日 (2 月は 29。季節の枠の端は「MM-DD」の文字で比べるので、うるう年でない年の 02-29 は当たる日が無いだけ)"""
    return {2: 29, 4: 30, 6: 30, 9: 30, 11: 30}.get(m, 31)


class Parsed:
    """規則の文を読んだ結果: irs (IR のリスト)、status (ok / none / on_demand / unparsed / ambiguous / multiple / partial_subject / inline)、
    pieces (IR ごとの原文の切れ端)、rest (読めなかった残り。呼ぶ側が年末年始・祝日の文として使えなければ、使われなかった但し書き)、
    subjects (品目の一部にだけ当たる規則の主語。東秩父「ペットボトルは…」)、notes、
    inline (マスの中の別扱い。能美「第1・第3木曜日 注：松が岡 第2・第4木曜日」→ [{"names": ["松が岡"], "ir": …, "text": …}]。
    status は inline で、irs はほかの地区の規則。地区を分けられる呼ぶ側だけが使い、ほかは読めないものとして扱う)"""

    def __init__(self, text, irs, status, pieces=(), rest="", notes=(), subjects=(), inline=()):
        self.text, self.irs, self.status, self.pieces, self.rest, self.notes = text, list(irs), status, list(pieces), rest, list(notes)
        self.subjects = list(subjects)
        self.inline = list(inline)

    @property
    def ir(self):
        return self.irs[0] if len(self.irs) == 1 else None

    def record(self):
        r = {"text": self.text, "irs": self.irs, "status": self.status, "rest": self.rest, "notes": self.notes,
             "subjects": self.subjects}
        if self.inline:
            r["inline"] = self.inline
        return r

    def __repr__(self):
        return f"Parsed({self.status}, {self.irs})"


def _pieces(t):
    """1 つのマスの中の規則の切れ端 (改行・「/」・読点の後の別の規則) に分ける。かっこの中の除外は切らない"""
    out, depth, cur = [], 0, ""
    for c in t:
        if c in "(（":
            depth += 1
        elif c in ")）":
            depth = max(0, depth - 1)
        if c in "\n/／" and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
            continue
        cur += c
    if cur:
        out.append(cur)
    return [x for x in out if x]


# マスの中の別扱い「<規則> 注：<地区の名前> <規則>」(能美)。地区の名前には数字・「第」を入れない
_INLINE = re.compile(r"^(?P<a>.+?)注[:：](?P<name>[^\d第毎隔:：/]{1,20}?)(?P<b>(?:毎週|毎月|第|隔週|\d).+)$")
# 回数の書き添え「（週2回）」: 曜日の数と合えば規則と同じことを言っているだけ。「（月2回）」は毎週と合わないので残す
_FREQ = re.compile(r"^\(?週(\d)回\)?$")
_FREQ_M = re.compile(r"^\(?月(\d)回\)?$")


def freq_template(text, ir):
    """回数の書き添え (「（週2回）」「週2回」「（月2回）」) が規則と同じ回数を言っているだけか → 見本の名前 か None。
    週N回: except_nth の無い毎週の規則で、曜日の数が N。月N回: 第1〜4 の月ごとの規則 (except_nth なし) で、回の数が N
    (第5 を含む規則は月によって回数が違うので合わない)。ほかの規則・ほかの言い方は None (使われなかった但し書き)"""
    t = ZW.sub("", nfkc(text or ""))
    if not ir or ir.get("except_nth"):
        return None
    m = _FREQ.match(t)
    if m and ir["kind"] == "weekly" and int(m.group(1)) == len(ir["weekdays"]):
        return "weekly_count"
    m = _FREQ_M.match(t)
    if m and ir["kind"] == "monthly_nth" and int(m.group(1)) == len(ir["nth"]) and set(ir["nth"]) <= {1, 2, 3, 4}:
        return "monthly_count"
    return None
# マスの頭の規則の終わりになりうる所 (年末年始の文・「※」の注・かっこ書きの始まり・「…ますが」の逆接)
_HEAD_END = re.compile(r"年末|年始|※|\(|(?<=ます)が")


def parse(text, nth_definition=None):
    """規則の文 (マスの文字) → Parsed。nth_definition: 資料が「第N週」の意味を決めていれば "nth" (月の何回目) か
    "calendar_SU" / "calendar_MO" (暦の行。週の始まり)。決めていなければ ambiguous_week のまま"""
    raw = text or ""
    if is_none_mark(raw):
        return Parsed(raw, [{"kind": "none"}], "none")
    whole = prep(raw.replace("\n", ""))
    if not whole:
        return Parsed(raw, [], "unparsed", rest="")
    if RX["on_demand"].search(whole) and not re.search(W + r"曜", whole):
        return Parsed(raw, [{"kind": "on_demand"}], "on_demand")
    ir = _one(re.sub(r"^[※*]+", "", whole))           # 折り返しただけのマス (東秩父「第1・2・3・5 / 金曜日」) は 1 つの文として読む
    if ir is not None:
        if ir["kind"] == "ambiguous_week" and nth_definition:
            ir = resolve_week(ir, nth_definition)
        return Parsed(raw, [ir], "ambiguous" if ir["kind"] == "ambiguous_week" else "ok", [whole])
    mi = _INLINE.match(whole)
    if mi:
        a, b = _one(mi.group("a")), _one(mi.group("b"))
        names = [x for x in mi.group("name").split(SEP) if x]
        if a and b and names and "ambiguous_week" not in (a["kind"], b["kind"]):
            return Parsed(raw, [a], "inline", [mi.group("a")], "",
                          [f"マスの中の別扱い (注: {'・'.join(names)})"],
                          inline=[{"names": names, "ir": b, "text": "注:" + mi.group("name") + mi.group("b")}])
    t = prep(raw.replace("\n", "/"))
    t = re.sub(r"(曜日?)・(?=第|毎|隔|\d)", r"\1/", t)          # 「第2・4水曜日、第1土曜日」は 2 つの規則
    irs, pieces, rest, subjects, notes_freq = [], [], [], [], []
    for piece in _pieces(t):
        p = re.sub(r"^[※*]+", "", piece)
        subj = None
        ms = _SUBJECT.match(p)
        if ms:
            subj, p = ms.group(1), p[ms.end():]
        ir = _one(p)
        if ir is None:
            # 「火曜日・金曜日」「第2・4水曜日」の後ろに年末年始の文・注・かっこ書きが続くマス (金沢) は、頭の規則だけを読み、
            # 残りは rest (呼ぶ側が使えなければ、使われなかった但し書きとして系列を止める)。「第2・4水曜日（第5週は休み）」の
            # 注の「第5週」を規則と読まない
            for mh in _HEAD_END.finditer(p):
                if mh.start() and (ir := _one(p[:mh.start()])) is not None:
                    tail = p[mh.start():]
                    if consume.take("freq", freq_template(tail, ir), tail):     # 使うのは関門を通ったときだけ
                        notes_freq.append(f"回数の書き添え「{tail}」は曜日の数と合う")
                    else:
                        rest.append(tail)
                    break
            if ir is None:
                rest.append(piece)
                continue
        if ir["kind"] == "ambiguous_week" and nth_definition:
            ir = resolve_week(ir, nth_definition)
        irs.append(ir)
        pieces.append(piece)
        subjects.append(subj)
    rest_s = "/".join(x for x in rest if x)
    if not irs:
        mw = _WEEK_ANY.search(whole)
        # 曜日の無い「第1・3週」(三郷「びん、かん（第1・3週）」)。曜日はほかの列にある。
        # 「（第5週は休み）」「第5週には収集がない」のような注の「第N週」(すぐ後が「は・が・も」) は規則にしない
        if mw and not re.match(r"\)*(?:は|が|も|に[はも])", whole[mw.end():]):
            return Parsed(raw, [{"kind": "ambiguous_week", "weeks": sorted(set(_nums(mw.group(1))))}], "ambiguous",
                          [mw.group(0)], rest_s, ["曜日がこの文に無い"])
        return Parsed(raw, [], "unparsed", rest=rest_s)
    if any(subjects):
        return Parsed(raw, irs, "partial_subject", pieces, rest_s,
                      ["品目の一部にだけ当たる規則がある (" + "・".join(s for s in subjects if s) + ")"], subjects)
    irs = _merge_weekly(irs)
    if len(irs) > 1:
        return Parsed(raw, irs, "multiple", pieces, rest_s, ["1 つのマスに 2 つ以上の規則"] + notes_freq)
    st = "ambiguous" if irs[0]["kind"] == "ambiguous_week" else "ok"
    return Parsed(raw, irs, st, pieces, rest_s, notes_freq)


def _merge_weekly(irs):
    """同じマスの毎週の規則 (except_nth の無いもの) は 1 つにまとめる"""
    wk = [i for i in irs if i["kind"] == "weekly" and not i.get("except_nth")]
    if len(wk) <= 1:
        return irs
    days = sorted({d for i in wk for d in i["weekdays"]}, key=WD_IDX.get)
    return [{"kind": "weekly", "weekdays": days}] + [i for i in irs if i not in wk]


def merge(irs):
    """同じ品目の規則の並び (曜日ごとの行で書いた表: 三鷹の「火曜日 燃やせるごみ」「金曜日 燃やせるごみ」) → IR のリスト。
    毎週どうし・同じ曜日でない第N どうしはまとめない (まとめられるのは毎週どうしだけ)"""
    return _merge_weekly(list(irs))


def resolve_week(ir, how):
    """ambiguous_week を、資料の決め方で monthly_nth か calendar_week に"""
    if how == "nth":
        return {"kind": "monthly_nth", "weekday": ir["weekday"], "nth": ir["weeks"]}
    if how in ("calendar_SU", "calendar_MO"):
        return {"kind": "calendar_week", "weekday": ir["weekday"], "weeks": ir["weeks"], "week_starts": how[-2:]}
    return ir


def week_readings(ir):
    """ambiguous_week の 3 通りの読み (月の何回目、日曜始まりの暦の行、月曜始まりの暦の行) → [(名前, IR)]"""
    return [("nth", resolve_week(ir, "nth")), ("calendar_SU", resolve_week(ir, "calendar_SU")),
            ("calendar_MO", resolve_week(ir, "calendar_MO"))]


NTH_DEF = (
    (re.compile(r"第\d?(?:とは|は)?.{0,6}(?:当該月|その月|毎月)の?(?:第?\d|最初)?(?:番目|回目)"), "nth"),
    (re.compile(r"第1(?:とは|は)1日?[~〜～\-]7日"), "nth"),                                # 三鷹「第1とは1～7日」
    (re.compile(r"(?:当該月|その月)の(?:第?1|1回目)"), "nth"),                              # 滝川「当該月の第1回目」
    (re.compile(r"(?:週|行)は?日曜日?(?:から|始まり)"), "calendar_SU"),
    (re.compile(r"(?:週|行)は?月曜日?(?:から|始まり)"), "calendar_MO"),
)


def nth_definition(texts):
    """本文の文から、第N (第N週) の意味を決める文を探す → ("nth" | "calendar_SU" | "calendar_MO" | None, 文)。
    2 通りの決め方が出たら None (決まらない)"""
    found = {}
    for s in texts:
        t = prep(s)
        for rx, how in NTH_DEF:
            if rx.search(t):
                found.setdefault(how, s)
    if len(found) == 1:
        how, s = next(iter(found.items()))
        return how, s
    return None, None


# ---- IR → 日付 ----
def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def nth_of_month(d):
    """その日が、月の何回目のその曜日か"""
    return (d.day - 1) // 7 + 1


def is_last_of_month(d):
    return (d + dt.timedelta(days=7)).month != d.month


def calendar_row(d, week_starts="SU"):
    """暦の行 (週の始まりの曜日から数えて、月の何行目か)"""
    first = d.replace(day=1)
    off = (first.weekday() - WD_IDX[week_starts]) % 7
    return (d.day - 1 + off) // 7 + 1


def needs_anchor(ir):
    k = ir["kind"]
    if k in ("biweekly", "cycle", "subcycle"):
        return not ir.get("anchor")
    if k in ("months", "seasonal"):
        return needs_anchor(ir["rule"])
    return False


def occurrences(ir, start, end):
    """IR → [start, end] の日付 (休み・年末年始を当てない素の日付)。広げられない IR (ambiguous_week・起点の無い隔週・
    on_demand) は ValueError"""
    start, end = _d(start), _d(end)
    k = ir["kind"]
    if k in ("none", "on_demand"):
        return []
    if k == "ambiguous_week":
        raise ValueError("ambiguous_week は広げない (week_readings で 3 通りを作り、印字と合わせる)")
    if needs_anchor(ir):
        raise ValueError(f"{k} の起点 (anchor) が無い")
    out = []
    if k == "weekly":
        wd = {WD_IDX[w] for w in ir["weekdays"]}
        ex = set(ir.get("except_nth") or ())
        out = [d for d in _days(start, end) if d.weekday() in wd and nth_of_month(d) not in ex]
    elif k == "monthly_nth":
        wd, ns = WD_IDX[ir["weekday"]], set(ir["nth"])
        ex = set(ir.get("except_nth") or ())
        out = [d for d in _days(start, end) if d.weekday() == wd and nth_of_month(d) in ns and nth_of_month(d) not in ex]
    elif k == "monthly_last":
        wd = WD_IDX[ir["weekday"]]
        out = [d for d in _days(start, end) if d.weekday() == wd and is_last_of_month(d)]
    elif k == "monthly_day":
        ds = set(ir["days"])
        out = [d for d in _days(start, end) if d.day in ds]
    elif k == "calendar_week":
        wd, ws = WD_IDX[ir["weekday"]], set(ir["weeks"])
        out = [d for d in _days(start, end) if d.weekday() == wd and calendar_row(d, ir.get("week_starts", "SU")) in ws]
    elif k in ("biweekly", "cycle"):
        every = 2 if k == "biweekly" else ir["every_weeks"]
        a = _d(ir["anchor"])
        if a.weekday() != WD_IDX[ir["weekday"]]:
            raise ValueError("起点の曜日が規則の曜日と合わない")
        out = [d for d in _days(start, end) if (d - a).days % (7 * every) == 0]
    elif k == "months":
        ms = set(ir["months"])
        out = [d for d in occurrences(ir["rule"], start, end) if d.month in ms]
    elif k == "seasonal":
        f, t = ir["from"], ir["to"]
        def inside(d):
            md = d.strftime("%m-%d")
            return f <= md <= t if f <= t else (md >= f or md <= t)
        out = [d for d in occurrences(ir["rule"], start, end) if inside(d)]
    elif k == "subcycle":
        base = occurrences(ir["of"], start, end)
        a = _d(ir["anchor"])
        if a not in base:
            raise ValueError("subcycle の起点が元の規則の日に無い")
        i0 = base.index(a)
        out = [d for i, d in enumerate(base) if (i - i0) % ir["every"] == 0]
    else:
        raise ValueError(f"知らない IR の種類 {k}")
    return out


def is_monthly(ir):
    """§2.5 の 4 の「月ごとの規則」か。毎週の規則は、except_nth も months・seasonal の枠も無い weekly だけ"""
    return not (ir["kind"] == "weekly" and not ir.get("except_nth"))


def canon(ir):
    """比べるための形 (並びをそろえ、空のものを除く)"""
    if ir is None:
        return None
    out = {}
    for k, v in sorted(ir.items()):
        if v in (None, [], ""):
            continue
        if isinstance(v, dict):
            v = canon(v)
        elif isinstance(v, list):
            v = sorted(v, key=lambda x: (WD_IDX.get(x, 99), x) if isinstance(x, str) else (0, x))
        out[k] = v
    return out


def same(a, b):
    return canon(a) == canon(b)


# ---- RRULE への圧縮 (§2.5: 圧縮した形から dates に 1 日も違わず戻るときだけ付ける) ----
def to_rrule(ir):
    """IR → RRULE の文字列 (DTSTART・UNTIL は付けない)。表せない IR は None"""
    k = ir["kind"]
    if k == "weekly" and not ir.get("except_nth"):
        return "FREQ=WEEKLY;BYDAY=" + ",".join(ir["weekdays"])
    if k == "monthly_nth" and not ir.get("except_nth"):
        return "FREQ=MONTHLY;BYDAY=" + ",".join(f"{n}{ir['weekday']}" for n in ir["nth"])
    if k == "monthly_last":
        return f"FREQ=MONTHLY;BYDAY=-1{ir['weekday']}"
    if k == "monthly_day":
        return "FREQ=MONTHLY;BYMONTHDAY=" + ",".join(map(str, ir["days"]))
    if k == "months":
        inner = to_rrule(ir["rule"])
        return inner + ";BYMONTH=" + ",".join(map(str, sorted(ir["months"]))) if inner and inner.startswith("FREQ=MONTHLY") else None
    return None


def rrule_dates(rrule, start, end):
    """RRULE → [start, end] の日付 (dateutil)"""
    from dateutil.rrule import rrulestr
    s, e = _d(start), _d(end)
    r = rrulestr(rrule, dtstart=dt.datetime(s.year, s.month, s.day))
    return [x.date() for x in r.between(dt.datetime(s.year, s.month, s.day), dt.datetime(e.year, e.month, e.day, 23, 59), inc=True)]


def compress(ir, dates, start, end):
    """IR と日付 → {"rule_kind": "rrule", "rrule", "exdates", "rdates"} (戻して 1 日も違わないときだけ)。表せなければ None"""
    rr = to_rrule(ir) if ir else None
    if not rr:
        return None
    base = set(rrule_dates(rr, start, end))
    ds = {_d(x) for x in dates}
    ex, rd = sorted(base - ds), sorted(ds - base)
    if (base - set(ex)) | set(rd) != ds:
        return None
    return {"rule_kind": "rrule", "rrule": rr, "exdates": [x.isoformat() for x in ex], "rdates": [x.isoformat() for x in rd]}


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


# ---- 方針の文の文法 (§2.6。content.py で本文の範囲の文に切ったもの) ----
_MD = r"(\d{1,2})[月/](\d{1,2})日?(?:\((" + W + r")(?:曜日?)?\))?"

# ---- 祝日の扱いの文: 閉じた見本の並び (whitelist) ----
# 文の全体が (小さな、決まった前処理 _hp_norm のあとで) 下の見本のどれか 1 つにまるごと当たるときだけ値を返す。
# ほかの言い方 (別の節・「が、」「ただし」の逆接・「場合があります」「未定」「かどうか」「？」の含み・祝日のほかの主語
# (「東地区では」「ざつがみは」)・文の中の 2 つ目の「。」) はすべて None (使われなかった但し書きとして系列を止める。
# 読めなかった文は 2 つ目の読み (LLM) と人の裁定に回る)。打ち消しの印を探す書き方 (blacklist) では、2 回直しても
# 言い換え (「祝日も収集 年末年始休み」「祝日でも、第5週は収集しません」) が確かでない値になって残ったので、読む形を閉じる
_HP_KINDS = r"祝(?:日|休日)(?:(?:・|、|及び|および)振替休日)?(?:(?:・|、|及び|および)国民の休日)?"   # 並べて書いた休日の種類
_HP_INCL = r"(?:\(振替休日(?:・国民の休日)?(?:を)?含(?:む|みます)\))?"                       # 「（振替休日を含む）」
_HP_K = _HP_KINDS + _HP_INCL
_HP_VERB = r"(?:します|いたします|を行います|を実施します)"
_HP_DO = r"(?:収集|回収)" + _HP_VERB                                                               # 「収集します」
_HP_TO = r"(?P<to>翌日|次の日|翌平日|翌週の同じ曜日|前の平日)"
_HP_ITEM = r"[^・、。の()\d祝をがはもでに除外]{0,10}?(?:ごみ|ゴミ)"                                # 「燃えるごみ」「資源ごみ」「ごみ」
# 品目の主語 (「燃やさないごみは」「燃えるごみ・資源ごみ・燃えないごみの収集は、」)。品目の名前は「ごみ」で終わるものだけ。
# どの品目に当てるかは呼ぶ側 (expand.value_scope・_cell_names_other) が決め、資料に無い品目なら使わない。
# 地区・コース・「ごみ」で終わらない名前 (「東地区では」「ざつがみは」「ペットボトルは」) の主語は見本の外 (None)
_HP_SUBJ_ITEMS = _HP_ITEM + r"(?:(?:・|と)" + _HP_ITEM + r")*(?:は|の(?:収集|回収)は)[、・]?"
_HP_SUBJ = r"(?:" + _HP_SUBJ_ITEMS + r")?"
_HP_TEMPLATES = (
    # 祝日（・振替休日・国民の休日）も（通常どおり）収集します
    ("collected", "collected", re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")(?:も|でも)(?:通常(?:どおり|通り)?)?" + _HP_DO + _HP_INCL)),
    # 動詞の無い「祝日も収集」: マスの注をかっこで包んだもの (「（祝日も収集）」) だけ (holiday_policy(noverb=True)・関門の cell_holiday)。
    # 本文の文では読まない: 「祝日も収集\nできません。」のように、行の折り返しのあとに打ち消しが続きうる
    ("collected_noverb", "collected", re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")(?:も|でも)(?:通常(?:どおり|通り)?)?(?:収集|回収)" + _HP_INCL)),
    # 打ち消しの打ち消し: 祝日も収集はお休みしません / 祝日でも収集を休むことはありません / 祝日も収集を休止しません
    ("collected_double_negative", "collected",
     re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")(?:も|でも)(?:収集|回収)(?:は|を)?(?:お休みしません|休みません|休むことはありません|休止しません)")),
    # 品目の主語と「実施します」(羽咋「燃えるごみ・資源ごみ・燃えないごみの収集は、祝日、振替休日も実施します」)
    ("collected_items", "collected", re.compile(_HP_SUBJ_ITEMS + r"(?:" + _HP_K + r")も実施(?:します|いたします)")),
    # すべての平日 (金沢「収集は年末年始を除く月曜日～金曜日（祝日含む）に行います」)
    ("all_weekdays_kanazawa", "all_weekdays",
     re.compile(r"収集は(?P<ye>年末年始を除く)?月曜日?(?:[~〜\-]|から)金曜日?(?:まで)?\(祝日(?:を)?含む\)に行います")),
    # すべての平日: 月曜日～金曜日（祝日を含む）に収集します
    ("all_weekdays", "all_weekdays",
     re.compile(r"月曜日?(?:[~〜\-]|から)金曜日?(?:まで)?\(祝日(?:を)?含む\)に(?:収集|回収)します")),
    # 祝日（・振替休日）は収集しません / 祝日も収集はありません / 祝日でも収集は行いません / 祝日も収集を休止します
    ("not_collected", "not_collected",
     re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")(?:は|も|でも)(?:収集|回収)(?:しません|いたしません|はありません|は行いません|を行いません|"
                r"を行わない|はお休みです|はお休み|は休みです|は休み|を休止します|は休止します|なし)" + _HP_INCL)),
    # 祝日は（収集は）お休みです
    ("not_collected_rest", "not_collected", re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")は(?:収集は)?お?休み(?:です)?")),
    # 祝日の収集は休み (マスの注「（祝日の収集は休み）」)
    ("not_collected_of", "not_collected",
     re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")の(?:ごみの?)?(?:収集|回収)は(?:お?休み(?:です)?|ありません|行いません|しません|休止します|なし)")),
    # 祝日の場合は翌日に収集します
    ("moved", "moved", re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")の場合(?:は|、)?" + _HP_TO + r"に(?:収集|回収)します")),
    # 祝日は収集を休み、翌日に収集します
    ("moved_rest", "moved",
     re.compile(_HP_SUBJ + r"(?:" + _HP_K + r")は(?:収集|回収)を(?:お?休み|休止し)[、・]" + _HP_TO + r"に(?:収集|回収)します")),
)
_HP_SHIFT = {"翌日": "next_day", "次の日": "next_day", "翌平日": "next_weekday", "翌週の同じ曜日": "next_same_weekday",
             "前の平日": "prev_weekday"}
# 年末年始だけを除く言い方 (12 月・1 月の日付だけ。「他」「等」「ほか」「など」、ほかの月の日付は入れない)。
# 頭の 1 つ (「日曜日、年末年始（12月31日～1月3日まで）を除き、」) と、終わりのかっこ 1 つ (「（年末年始を除く）」
# 「（年末年始は休み）」) のどちらか 1 つだけを外す。外せるのは「収集します」の見本の前後だけ
# (「祝日は収集しません（年末年始を除く）」は、年末年始の祝日を収集するのかどうか決まらない)
_EX_PAREN = r"\((?:12月\d{1,2}日[~〜\-]1月\d{1,2}日(?:まで)?|12/\d{1,2}[~〜\-]1/\d{1,2})\)"
_EX_BENIGN = re.compile(r"(?:日曜日?[・、])?年末年始(?:" + _EX_PAREN + r")?を除(?:き|いて)[、・]?")
_EX_TAIL = re.compile(r"\(年末年始(?:を除く|は除く|は休み|はお休み)\)")


def _hp_norm(text):
    """祝日の見本と比べる前の形: NFKC、空白 (ゼロ幅を含む) を除く、読点 (「，」「,」「､」) を「、」に、頭の注・箇条書きの印
    (「※」「*」「・」「●」「◆」「☆」など)、文末の「。」1 つと、文の全体を包むかっこ 1 組 (マスの注「（祝日の収集は休み）」) を除く。
    ほかは寄せない (マスの読み残し rules.parse(...).rest は prep で「、」が「・」になっているので、節の切れ目は見本の側で「、」「・」の両方を見る)"""
    t = re.sub(r"[,，､]", "、", ZW.sub("", nfkc(text)))
    t = re.sub(r"^[※*・●○◆◇■□☆★◎]+", "", t)
    t = re.sub(r"。$", "", t)
    m = re.fullmatch(r"\(([^()]*)\)", t)
    if m:
        t = re.sub(r"。$", "", m.group(1))
    return t


# ---- 年末年始・休みの文: 閉じた見本の並び (whitelist) ----
# 文の全体が (前処理 _ye_norm のあとで) 下の見本のどれか 1 つにまるごと当たるときだけ材料を返す。日付の範囲・並びは、それぞれ
# すぐ後ろに自分の述語 (まで・から・休み・収集しません) を持つこと (「12月29日～12月31日通常収集、1月1日～1月3日休み」の
# 12/29〜31 を休みと読まない)。探す書き方 (search) では、後ろに休みの言葉があるだけで前の範囲まで休みにし、文の残りを読まないまま
# 使ったので、読む形を閉じる。見本の外の文は材料にしない (使われなかった但し書きとして系列を止める)
_YE_RNG = r"(?:[~〜\-]|から)"
_YE_RANGE = _MD + _YE_RNG + _MD + r"(?:まで)?"
_YE_RANGES = _YE_RANGE + r"(?:(?:・|及び|および|と)" + _YE_RANGE + r")*"
_YE_DAYS = _MD + r"(?:(?:・|と|及び|および)" + _MD + r")*"
_YE_OFF = (r"(?:お休み(?:です|します|となります|いたします)?|休み(?:です|となります|ます)?|ありません|しません|いたしません|"
           r"行いません|休止(?:します|です|となります)?|中止(?:します|です|となります)?|なし)")
# 休みの理由の句: 閉じた並び (固有の名前の無い一般の理由だけ)。「東町の道路工事のため」「山田町の祭りにより」のような、地名・施設の
# 名前のある理由は見本の外 (その地区だけの休みかもしれない。読まずに但し書きとして止める)。expand の品目の読み (value_scope) も同じ並び
REASON_WORDS = (r"(?:(?:施設|処理施設|焼却施設|ごみ処理施設|清掃工場|清掃センター|クリーンセンター|処理場|収集車両?)の)?(?:定期)?"
                r"(?:点検|整備|修繕|改修工事|改修|工事|メンテナンス)|祭典|祝日|休日|振替休日|年末年始|お盆|大型連休|"
                r"ゴールデンウ[ィイ]ーク|悪天候|荒天|台風|降雪|積雪|大雪|選挙")
REASON = r"(?:" + REASON_WORDS + r")(?:のため|により|に伴い|につき)"
_YE_REASON = r"(?:" + REASON + r"・?)?"                                             # 「施設の定期点検のため」
_YE_OBJ = r"(?:" + _HP_ITEM + r"の?)?(?:ごみの?|資源の?)?(?:収集|回収)?(?:は|を)?"    # 「可燃ごみの収集は」「ごみの収集を」
_YE_LAST_HEAD = r"年末(?:は|の(?:収集|最終収集日?|最終)(?:は|日は)?)?:?"
_YE_FIRST_HEAD = r"年始(?:は|の(?:収集|収集開始日?|開始)(?:は|日は)?)?:?"
_YE_TAIL = r"(?:の?(?:収集|回収)(?:します|いたします|となります|です|を行います|を開始します|開始)?|です|となります)?"
_YE_TEMPLATES = (
    # 年末は12月29日まで、年始は1月4日から収集します (金沢のマス「年末は12月28日(月曜日)まで年始は1月4日(月曜日)から」)
    ("closure_pair", re.compile(_HP_SUBJ + _YE_LAST_HEAD + _MD + r"(?:まで|が最終(?:日)?|最終(?:日)?)(?:で|・)?" + _YE_FIRST_HEAD + _MD +
                                r"(?:から|より)" + _YE_TAIL)),
    # 12/29(火)最終で 1/5(火)から (羽咋のマス)
    ("closure_pair_bare", re.compile(_HP_SUBJ + _MD + r"(?:まで|最終)(?:で|・)?" + _MD + r"(?:から|より)" + _YE_TAIL)),
    # 年末 ： 12月29日（火曜日）まで (金沢の市全体。年始の文と組にするのは呼ぶ側)
    ("closure_last", re.compile(_HP_SUBJ + _YE_LAST_HEAD + _MD + r"(?:まで|が最終(?:日)?(?:です)?|最終(?:日)?)" + _YE_TAIL)),
    # 年始 ： 1月4日（月曜日）より
    ("closure_first", re.compile(_HP_SUBJ + _YE_FIRST_HEAD + _MD + r"(?:から|より)" + _YE_TAIL)),
    # 12月29日（火）から1月3日（日）までは、ごみの収集をお休みします / 5月3日から5月5日及び12月31日から1月2日の期間、収集はお休みです
    ("range_off", re.compile(_HP_SUBJ + _YE_RANGES + r"(?:の期間|の間|期間中)?(?:は|の|中は)?・?" + _YE_REASON + _YE_OBJ + _YE_OFF)),
    # 12月29日～12月31日は通常どおり収集し、1月1日～1月3日は休みです / 12月29日（火）は通常どおり収集し、12月30日（水）から1月3日（日）
    # までは収集しません (休みは後ろの範囲だけ。前の日・範囲も自分の述語「通常どおり収集」を持つ)
    ("range_normal_then_off", re.compile(_HP_SUBJ + r"(?:" + _YE_RANGE + r"|" + _YE_DAYS + r")(?:は)?通常(?:どおり|通り)?(?:の)?(?:収集|回収)"
                                         r"(?:し|します|です)?・?" + _YE_RANGE + r"(?:は)?" + _YE_OBJ + _YE_OFF)),
    # 12月31日のごみ収集はありません / 注意：12月31日（木）は収集しません / 5月4日(月)、12月31日(木)は、施設の定期点検のため可燃ごみの収集は行いません
    ("days_off", re.compile(_HP_SUBJ + _YE_DAYS + r"(?:は|の)・?" + _YE_REASON + _YE_OBJ + _YE_OFF)),
    # 1/1(金)は休みのため 1/5(火)に特別収集します (羽咋)
    ("makeup", re.compile(_HP_SUBJ + _MD + r"(?:は|が)お?休み(?:のため|なので)・?" + _MD + r"(?:に|へ)(?:特別|振替|臨時)?(?:収集|回収)"
                          r"(?:します|を行います|です)?")),
)
_YE_LEAD = re.compile(r"^(?:[※*・●○◆◇■□☆★◎]|注意|注釈|\(注\d*\)|注\d*)+:?")


def _ye_norm(text):
    """年末年始の見本と比べる前の形: 規則の前処理 (prep。NFKC・空白なし・「、」は「・」) のあと、頭の注・箇条書きの印
    (「※注意」「（注）」)、文末の「。」1 つと、文の全体を包むかっこ 1 組を除く"""
    t = _YE_LEAD.sub("", prep(text))
    t = re.sub(r"。$", "", t)
    m = re.fullmatch(r"\(([^()]*)\)", t)
    if m:
        t = re.sub(r"。$", "", _YE_LEAD.sub("", m.group(1)))
    return t


def _year_for(m, d, period):
    """期間の中で月・日に当たる日付 (1 つだけのとき)。期間が無いか、2 つ以上なら None"""
    if not period or not period.get("valid_to"):
        return None
    lo, hi = _d(period["valid_from"]), _d(period["valid_to"])
    hits = []
    for y in range(lo.year, hi.year + 1):
        try:
            x = dt.date(y, m, d)
        except ValueError:
            continue
        if lo <= x <= hi:
            hits.append(x)
    return hits[0] if len(hits) == 1 else None


def _date_of(groups, period):
    """(月, 日, 曜日) → (日付 か None, わけ)"""
    m, d, w = int(groups[0]), int(groups[1]), groups[2]
    x = _year_for(m, d, period)
    if x is None:
        return None, "no_year"
    if w and WD[w] != WD_ORDER[x.weekday()]:
        return None, "weekday_mismatch"
    return x, None


def _hp_match(t, names=None, noverb=False):
    """前処理した文 t が、見本 (names だけ。None ならすべて) のどれかにまるごと当たるか → (見本の名前, 値, マッチ) か None。
    動詞の無い見本 (_noverb) は noverb のときだけ"""
    for name, value, rx in _HP_TEMPLATES:
        if (names is None or name in names) and (noverb or not name.endswith("_noverb")):
            m = rx.fullmatch(t)
            if m:
                return name, value, m
    return None


def holiday_policy(text, noverb=False):
    """方針の文 → 祝日の扱い {"value", "covers", "shift"?, "except_year_end", "template"} か None。
    文の全体が _HP_TEMPLATES の見本の 1 つにまるごと当たるときだけ読む (閉じた見本の並び)。見本の前の年末年始だけを除く
    言い方 (_EX_BENIGN) か、後ろの年末年始のかっこ (_EX_TAIL) は、どちらか 1 つだけ、「収集します」の見本と一緒のときだけ外す
    (template に "+ye_lead" / "+ye_tail" を付ける)。動詞の無い「祝日も収集」(_noverb) は noverb (かっこで包んだマスの注) のときだけ。
    covers は見本の中で並べて書いた休日の種類だけ (「祝日・振替休日」「祝日（振替休日を含む）」)。
    「変更になることがあります」の文 (caveat・deferred) は祝日の扱いにしない (§2.6 の 8。呼ぶ側で classify_change を先に見る)。
    見本に当たらない文 (逆接・ほかの節・含み・祝日のほかの主語・「祝日の翌日は収集しません」) は None
    (使われなかった但し書きとして残り、系列を止める)"""
    t = _hp_norm(text)
    if "祝" not in t:
        return None
    hit, exc_ye, variant = _hp_match(t, noverb=noverb), False, ""
    if hit is None:
        collected = ("collected", "collected_double_negative", "collected_noverb")
        m = _EX_BENIGN.match(t)
        if m and m.end() < len(t):
            hit, exc_ye, variant = _hp_match(t[m.end():], collected, noverb), True, "+ye_lead"
        else:
            m = _EX_TAIL.search(t)
            if m and m.end() == len(t) and m.start():
                hit, exc_ye, variant = _hp_match(t[:m.start()], collected, noverb), "除" in m.group(0), "+ye_tail"
    if hit is None:
        return None
    name, value, m = hit
    name += variant
    if value == "all_weekdays":
        r = {"value": "collected", "covers": ["all_weekdays"], "except_year_end": bool(m.groupdict().get("ye"))}
    else:
        s = m.group(0)
        r = {"value": value, "covers": ["national"] + (["substitute"] if "振替休日" in s else []) +
             (["citizens"] if "国民の休日" in s else []), "except_year_end": exc_ye}
    if value == "moved":
        r["shift"] = {"to": _HP_SHIFT[m.group("to")]}
    r["template"] = name
    return r


def _closure_off(a, b, pa, pb):
    """休みの範囲 a〜b → closure の材料 (last は前の日、first は次の日)"""
    rec = {"treatment": "closure", "last": None, "first": None, "problems": [p for p in (pa, pb) if p]}
    if a and b:
        if b < a:
            rec["problems"].append("range_order")
        else:
            rec.update(last=(a - dt.timedelta(days=1)).isoformat(), first=(b + dt.timedelta(days=1)).isoformat(),
                       off_from=a.isoformat(), off_to=b.isoformat())
    return rec


def year_end_template(text):
    """方針の文が当たる年末年始の見本の名前 (_YE_TEMPLATES) か None (関門 consume に渡す)"""
    t = _ye_norm(text)
    return next((n for n, rx in _YE_TEMPLATES if rx.fullmatch(t)), None)


def year_end_parts(text, period, with_spans=False):
    """方針の文 → 年末年始の材料のリスト [{"treatment", "last"?, "first"?, "removed"?, "to"?, "removed_dates"?, "problems"}]。
    文の全体が _YE_TEMPLATES の見本の 1 つにまるごと当たるときだけ読む (見本の外は空のリスト。見本の名前は year_end_template)。
    「年末は12月29日まで」「年始は1月4日から」は片方ずつ (last だけ・first だけ) を返し、組み合わせは呼ぶ側 (expand.py)。
    with_spans なら (材料のリスト, 読んだ所 [(start, end)], 前処理した文) を返す (読み残し leftover を見るため。
    見本に当たれば文の全体を読んだ所にする)"""
    t = _ye_norm(text)
    name = year_end_template(text)
    if name is None:
        return ([], [], t) if with_spans else []
    ds = [_date_of(g.groups(), period) for g in re.finditer(_MD, t)]
    out = []
    if name in ("closure_pair", "closure_pair_bare"):
        (a, pa), (b, pb) = ds
        rec = {"treatment": "closure", "last": a and a.isoformat(), "first": b and b.isoformat(), "problems": [p for p in (pa, pb) if p]}
        if a and b and not (a < b and (b - a).days <= 40):
            rec["problems"].append("closure_order")
        out.append(rec)
    elif name == "closure_last":
        (a, pa), = ds
        out.append({"treatment": "closure", "last": a and a.isoformat(), "first": None, "problems": [pa] if pa else []})
    elif name == "closure_first":
        (b, pb), = ds
        out.append({"treatment": "closure", "last": None, "first": b and b.isoformat(), "problems": [pb] if pb else []})
    elif name == "range_off":
        for i in range(0, len(ds), 2):
            (a, pa), (b, pb) = ds[i], ds[i + 1]
            out.append(_closure_off(a, b, pa, pb))
    elif name == "range_normal_then_off":
        (a, pa), (b, pb) = ds[-2], ds[-1]
        out.append(_closure_off(a, b, pa, pb))
    elif name == "days_off":
        out.append({"treatment": "skip", "removed_dates": [x.isoformat() for x, _ in ds if x], "problems": [p for _, p in ds if p]})
    elif name == "makeup":
        (a, pa), (b, pb) = ds
        out.append({"treatment": "makeup", "removed": a and a.isoformat(), "to": b and b.isoformat(), "others": None,
                    "problems": [p for p in (pa, pb) if p]})
    return (out, [(0, len(t))], t) if with_spans else out


_BENIGN = re.compile(r"[・。.()「」\[\]【】:：※/]|収集(?:いた)?します|収集し|収集|回収|します|いたします|です|となります|ます|"
                     r"通常(?:ど|と)おり|は|で|の|を|に|も|が|、")


def leftover(t, spans):
    """前処理した文 t から読んだ所 spans を除いた残りのうち、意味のある文字 (区切り・「収集します」などを除く)。
    空なら、文はすべて読まれた"""
    keep = [c for i, c in enumerate(t) if not any(a <= i < b for a, b in spans)]
    return _BENIGN.sub("", "".join(keep))


# ---- 第5週・大型連休の文: 閉じた見本の並び ----
# 「5番目の曜日」「5回目の曜日」(月の何回目か) だけ。「第5週」(暦の行かもしれない)・「第5月曜日」(曜日を言う)・
# 「5番目の曜日も休みなく収集します」のような言い換えは見本の外 (None。使われなかった但し書きとして止める)
_FW_SUBJ = r"(?:毎?月の)?[「『]?(?:5番目|5回目)の(?:各)?曜日[」』]?"
_FIFTH_TEMPLATES = (
    # 月の「5番目の曜日」に収集はありません (新宿)
    ("fifth_none", re.compile(_HP_SUBJ + _FW_SUBJ + r"(?:に|は|の)(?:ごみの?)?(?:収集|回収)?(?:は)?"
                              r"(?:ありません|しません|いたしません|行いません|お休みです|休みです|お休み|休み|なし)")),
)
# 大型連休・ゴールデンウィークも収集する言い方だけ (「ゴールデンウィーク中も収集しません」「…とはなりません」は見本の外)
_LH = r"(?:大型連休|ゴールデンウィーク|GW)(?:中|期間中|の期間中?)?(?:も|でも)[、]?"
_LONG_TEMPLATES = (
    # ※大型連休中もごみ収集は通常通り実施します (羽咋)
    ("long_collected", re.compile(_HP_SUBJ + _LH + r"(?:ごみの?)?(?:収集|回収)(?:は|を)?(?:通常(?:どおり|通り))?"
                                  r"(?:実施します|実施いたします|行います|します|いたします)")),
    # 大型連休中も通常どおり収集します
    ("long_collected_usual", re.compile(_HP_SUBJ + _LH + r"通常(?:どおり|通り)(?:ごみの?)?(?:収集|回収)(?:を実施します|を行います|します|いたします)")),
)


def fifth_week(text):
    """「月の「5番目の曜日」に収集はありません」(新宿) → {"value": "none", "template"}。見本の外 (「第5週がある月の…休み」(赤平)・
    「5番目の曜日も休みなく収集します」) は None"""
    t = _hp_norm(text)
    for name, rx in _FIFTH_TEMPLATES:
        if rx.fullmatch(t):
            return {"value": "none", "template": name}
    return None


def long_holiday(text):
    """「大型連休中もごみ収集は通常通り実施します」(羽咋) → {"value": "collected", "template"}。打ち消し (「…も収集しません」
    「…収集はお休みです」「…とはなりません」「…しないことがあります」) を含む見本の外の文は None"""
    t = _hp_norm(text)
    for name, rx in _LONG_TEMPLATES:
        if rx.fullmatch(t):
            return {"value": "collected", "template": name}
    return None


def main(argv=None):
    import argparse, json
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="規則の文法・方針の文法を試す")
    ap.add_argument("text")
    ap.add_argument("--policy", action="store_true")
    ap.add_argument("--fy", type=int, default=2026)
    a = ap.parse_args(argv)
    if a.policy:
        per = {"valid_from": f"{a.fy}-04-01", "valid_to": f"{a.fy + 1}-03-31"}
        print(json.dumps({"holiday_policy": holiday_policy(a.text), "year_end": year_end_parts(a.text, per),
                          "fifth_week": fifth_week(a.text), "long_holiday": long_holiday(a.text)}, ensure_ascii=False, indent=1))
        return 0
    p = parse(a.text)
    print(json.dumps(p.record(), ensure_ascii=False))
    if p.ir and p.status == "ok":
        ds = occurrences(p.ir, f"{a.fy}-04-01", f"{a.fy}-05-31")
        print("4〜5 月:", [d.isoformat() for d in ds])
        print("RRULE:", to_rrule(p.ir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
