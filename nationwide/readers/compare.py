"""2 つの読みをそろえた形で比べる (DESIGN.md §3.11)

  python nationwide/readers/compare.py selftest      # 比べ方の小さな確かめ

- 比べる前にそろえる: 文字は NFKC と空白の除去、日付は (地区, 品目, 月) ごとの日の集まり、地区は (単位, 正規化した名前,
  条件の正規形の集まり)、規則は IR、方針は (値, span)
- 結果は 1 つずつ agreed / agreed_weak / disputed / single / both_unsure。agreed・agreed_weak 以外は裁定に回す
- 独立の度合い (independence): コードと LLM は code_llm (agreed)、別のモデルの LLM 2 つは llm_diff_model (agreed)、
  同じモデルの LLM 2 つは llm_same_model (agreed_weak)。コードの 2 つのやり方は表の形にだけ使う
- 方針の読みは、値が同じでも span が違う所を指していれば食い違い (§3.2)。deferred と caveat が割れたら止める方 (deferred)、
  但し書きは 2 つの読みの和 (§3.8)。表の形の食い違いは LLM の票で決めない (§3.12。adjudicate.py が人へ回す)
"""
import difflib, re, sys, unicodedata

import content, members as mem, rules

OK = ("agreed", "agreed_weak")


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def squash(s):
    return re.sub(r"\s+", "", nfkc(s))


def independence(a, b):
    """読み取り役の記録 2 つ ({"kind": "code" | "llm", "model"}) → 独立の度合い"""
    ka, kb = a.get("kind"), b.get("kind")
    if {ka, kb} == {"code", "llm"}:
        return "code_llm"
    if ka == kb == "llm":
        return "llm_same_model" if (a.get("model") or "") == (b.get("model") or "") else "llm_diff_model"
    return None                                    # コードどうし (表の形だけ)


def state(agree, a_present=True, b_present=True, indep="code_llm"):
    """1 つの値の比べ → 状態"""
    if not a_present and not b_present:
        return "both_unsure"
    if not a_present or not b_present:
        return "single"
    if agree:
        return "agreed_weak" if indep == "llm_same_model" else "agreed"
    return "disputed"


def combine(states):
    """値の状態のリスト → 系列の状態 (disputed があれば disputed、single・both_unsure・missing があれば single、
    agreed_weak があれば agreed_weak、どれも agreed なら agreed)。空なら single"""
    s = list(states)
    if not s:
        return "single"
    if "disputed" in s:
        return "disputed"
    if any(x in ("single", "both_unsure", "missing") for x in s):
        return "single"
    if "agreed_weak" in s:
        return "agreed_weak"
    return "agreed"


_PUNCT = re.compile(r"[\x00、。,，.．・･:：;；()（）「」『』\[\]［］【】※*＊/／~〜～\-－ー]")


def span_covered(span, texts):
    """B の原文 span が、A の文 (texts。A が使った文・A の但し書きの文そのもの) で読み切れているか: span が A の 1 つの文の中に
    あるか、span から A の文をすべて除くと区切りしか残らないとき。文字の一部が同じ (span_overlap) だけでは読み切れていない
    (「祝日も収集しますが、山間部は翌日になります」は A の「祝日も収集します」を含むが、後ろを A は読んでいない)"""
    x = squash(span)
    ts = sorted({squash(t) for t in texts if squash(t)}, key=len, reverse=True)
    if not x or any(x in t for t in ts):
        return True
    for t in ts:
        x = x.replace(t, "\x00")
    return not _PUNCT.sub("", x)


def _locate(text, sentences):
    """原文の文字 → それが入る文 (か、それがまるごと含む 4 字以上の文) の場所 {(行, start, end)}。文字の一部が同じだけの文は入れない"""
    x = squash(text)
    out = set()
    if not x:
        return out
    for s in sentences:
        y = squash(s.text)
        if y and not getattr(s, "parenthetical", False) and (x in y or (len(y) >= 4 and y in x)):
            out.add((id(s.line), s.start, s.end))
    return out


def _a_location(e, sentences):
    """A の例外の原文の場所: span の行・start・end が同じ文 (exceptions_from_sentences の span)。無ければ原文の行ごとに _locate"""
    sp = e.get("span") or {}
    if sp.get("start") is not None and sp.get("end") is not None:
        hit = {(id(s.line), s.start, s.end) for s in sentences
               if s.start == sp["start"] and s.end == sp["end"] and s.line.selector == sp.get("selector")}
        if hit:
            return hit
    return set().union(set(), *[_locate(t, sentences) for t in (e.get("text") or "").splitlines()])


def same_place(a_excs, b_span, sentences=None):
    """B の原文 span が、A の例外 (a_excs) のどれかと同じ文を指すか。sentences (資料の文) があれば文の場所で比べる
    (B の span の入る文がどれも A の例外の文のとき。A の例外の無い文にも入れば、どの文か決まらないので同じにしない)。
    無ければ、どちらかがもう片方を丸ごと含むときだけ (8 字の共通では比べない)"""
    if sentences is not None:
        lb = _locate(b_span or "", sentences)
        la = set().union(set(), *[_a_location(e, sentences) for e in a_excs])
        return bool(lb) and lb <= la                        # B の span の入る文がどれも A の例外の文 (ほかの文にも入るなら決まらない)
    y = squash(b_span)
    return bool(y) and any(squash(e.get("text", "")) and (y in squash(e.get("text", "")) or squash(e.get("text", "")) in y)
                           for e in a_excs)


def span_overlap(a, b, min_common=8):
    """2 つの原文が同じ所を指しているか (どちらかがもう片方を含むか、8 字以上の共通の部分がある)"""
    x, y = squash(a), squash(b)
    if not x or not y:
        return False
    if x in y or y in x:
        return True
    m = difflib.SequenceMatcher(None, x, y, autojunk=False).find_longest_match(0, len(x), 0, len(y))
    return m.size >= min(min_common, len(x), len(y))


# ---- 表のマス (型 D) ----
def b_cells(answer):
    """cells.B の答え → {(行, 列): {"text", "days"}} (0 から)"""
    out = {}
    cells = (answer or {}).get("cells") if isinstance(answer, dict) else None
    if not isinstance(cells, dict):
        return out
    for k, v in cells.items():
        m = re.fullmatch(r"R(\d+)C(\d+)", str(k).strip(), re.I)
        if not m:
            continue
        if isinstance(v, str):
            v = {"text": v, "days": None}
        if not isinstance(v, dict):
            continue
        out[(int(m.group(1)) - 1, int(m.group(2)) - 1)] = v
    return out


# 英語の訳の語: 序数 (「2nd」「4th」) と、小文字を含む 2 字以上の語 (「Burnable」「Course」「Every」「No」)。
# 大文字だけの語 (「A」「NE」「AB」) と数字は消さない (コース・地区の名前かもしれない)
_EN_WORD = re.compile(r"(?<![0-9])[0-9]+(?:st|nd|rd|th)(?![a-z])|[A-Z][a-z]+(?:'s)?|[a-z]{2,}(?:'s)?")


def label_same(a_text, b_text):
    """見出し・地区の文字の比べ: NFKC・空白なしで、英語の訳を除いた日本語の部分が同じか。
    英語の訳として消すのは、小文字を含む英語の語と序数だけ (「Every week」「Tuesday & Friday」「2nd & 4th Wed」「可燃ごみ(Burnable)」。
    英語だけの行もその語が消えて残らない)。大文字だけの語・1 字のローマ字・数字 (「Aコース」「AB コース」「NE地区」「Zone A」
    「Course A」の A、「コース 1」) はコース・地区の名前かもしれないので、英語の語のあとでも残す (「ABコース」と「CDコース」、
    「東地区 East A」と「東地区 East B」は違う)。迷えば違うとする (食い違いは裁定へ回るだけで、日付は出さない)"""
    def ja(t):
        t = _EN_WORD.sub("", nfkc(t or ""))
        t = re.sub(r"[&().,'/:;\-]", "", t)
        t = squash(t).replace("･", "・")
        return re.sub(r"[~〜～∼ー－―‐−]", "~", t)               # 波ダッシュ・長音の字の違い (「1～3」「1〜3」) は同じ
    return ja(a_text) == ja(b_text)


def b_days(cell):
    """B のマス → 日のリスト / [] (空のマス) / None (日の並びでない・読めない)"""
    if cell is None:
        return None
    ds = cell.get("days")
    if isinstance(ds, list) and all(isinstance(x, int) or (isinstance(x, str) and x.isdigit()) for x in ds):
        return sorted(int(x) for x in ds)
    t = squash(cell.get("text") or "")
    if not t:
        return []
    import pdf_layout
    p = pdf_layout.parse_days(t)
    return sorted(p) if p is not None else None


def compare_day_cell(a_days, b_cell):
    """日のマス (A の日のリスト) と B のマス → 状態。A が読めない (壊れた字) なら single"""
    if a_days is None:
        return "single" if b_cell is not None else "both_unsure"
    if b_cell is None:
        return "single"
    bd = b_days(b_cell)
    return "agreed" if bd is not None and sorted(a_days) == bd else "disputed"


# ---- 地区・品目・規則 ----
def _community(m):
    """行政区・自治会の中身をそろえる: 名前はかっこの注記まで含めた資料の文字のまま (「久保（高麗川）」「旭ケ丘１・２」)、条件なし"""
    if m.get("unit") != "community":
        return m
    name = nfkc(m.get("name_verbatim") or m.get("text") or "").strip()
    for c in m.get("conditions") or []:
        t = nfkc(c.get("text") or "")
        if c.get("kind") == "note" and t and t not in name:
            name += f"({t})"
    return {"name_verbatim": name, "text": name, "unit": "community", "conditions": []}


def compare_members(a_members, b_answer):
    """A の中身と B (members.B の答え) → 状態"""
    bm = [m for m in ((b_answer or {}).get("members") or []) if isinstance(m, dict)] if isinstance(b_answer, dict) else []
    if b_answer is None:
        return "single"
    if not bm and not a_members:
        return "both_unsure"
    return "agreed" if bm and mem.same([_community(m) for m in a_members or []], [_community(m) for m in bm]) else "disputed"


def compare_category(a_cat, b_item):
    """A の品目 ({"coarse", "mode"}) と B の 1 件 → 状態"""
    if b_item is None:
        return "single"
    return "agreed" if set(b_item.get("coarse") or []) == set(a_cat.get("coarse") or []) and b_item.get("mode") == a_cat.get("mode") \
        else "disputed"


def compare_rule(parsed_a, b_rule):
    """A の規則 (rules.Parsed) と B の規則の 1 件 → 状態 (IR の正規形で比べる。ambiguous_week は ambiguous_week どうしだけ同じ)"""
    import markup
    if b_rule is None:
        return "single"
    return "agreed" if markup._same_parsed(parsed_a, markup._b_parsed(b_rule)) else "disputed"


def compare_basis(a_basis, b_answer):
    if not isinstance(b_answer, dict) or b_answer.get("basis") not in ("residence", "collection_point", "unknown"):
        return "single"
    return "agreed" if b_answer["basis"] == a_basis else "disputed"


# ---- 方針と但し書き ----
def md_date(s, period):
    """B の日付 ("M/D"・"M月D日"・"YYYY-MM-DD") → ISO か None (期間で年を決める)"""
    t = nfkc(str(s or "")).strip()
    m = re.fullmatch(r"(20\d\d)-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\s*[/月]\s*(\d{1,2})", t)
    if not m or not period or not period.get("valid_to"):
        return None
    d = rules._year_for(int(m.group(1)), int(m.group(2)), period)
    return d.isoformat() if d else None


def _off_days_a(excs):
    """A の例外 → (休みの日の集まり (cancelled・skip・makeup の消えた日), 休みの期間の組 {(last, first)})"""
    days, pairs = set(), set()
    for e in excs:
        if e["kind"] == "cancelled":
            days.add(e["date"])
        elif e["kind"] == "year_end" and e.get("treatment") == "skip":
            days |= set(e.get("dates") or [])
        elif e["kind"] == "year_end" and e.get("treatment") == "makeup" and e.get("removed"):
            days.add(e["removed"])
        elif e["kind"] == "year_end" and e.get("treatment") == "closure" and e.get("last") and e.get("first"):
            pairs.add((e["last"], e["first"]))
    return days, pairs


def _off_days_b(b, period):
    days, pairs = set(), set()
    for x in b.get("closures") or []:
        for d in x.get("dates") or []:
            iso = md_date(d, period)
            if iso:
                days.add(iso)
    for x in b.get("year_end") or []:
        ds = [md_date(d, period) for d in (x.get("dates") or [])]
        ds = [d for d in ds if d]
        t = x.get("treatment")
        if t == "closure" and len(ds) >= 2:
            pairs.add((min(ds), max(ds)))
        elif t in ("skip", "makeup") and ds:
            days.add(ds[0]) if t == "makeup" else days.update(ds)
    return days, pairs


def compare_policy(a_excs, a_used_texts, a_quals, b, period, office_texts=(), sentences=None):
    """方針と但し書きの A (コード) と B (policy.B の答えをまとめたもの) → 結果 (dict)。
    a_excs: A の例外 (holiday_policy・year_end・cancelled・deferred・caveat…)。a_used_texts: A が値に使った文。
    a_quals: A の但し書き (stage2 が実際に使わなかった但し書きと、系列ごとのマス・名前の但し書き。{"text"})。b が None (B が無い) なら、どの値も single。
    sentences (資料の文) があれば、B の deferred・caveat は文の場所で A のものと比べる (same_place。8 字の共通では比べない)。
    → {"holiday_policy": 状態, "off_days": 状態, "closures_pairs": 状態, "others": 状態, "valid": 状態, "office": 状態,
       "deferred_b_only": [...], "caveats_b_only": [...], "qualifiers_b_only": [...], "notes": [...]}"""
    res = {"holiday_policy": "single", "off_days": "single", "closure_pairs": "single", "others": "single", "valid": "single",
           "office": "single", "deferred_b_only": [], "caveats_b_only": [], "qualifiers_b_only": [], "notes": []}
    if b is None:
        return res
    # 祝日: 値と覆う休日の種類と span
    ah = [e for e in a_excs if e["kind"] == "holiday_policy"]
    bh = b.get("holiday_policy") if isinstance(b.get("holiday_policy"), dict) else None
    if not ah and not bh:
        res["holiday_policy"] = "agreed"
    elif ah and bh:
        vals = {(e["value"], tuple(sorted(e.get("covers") or []))) for e in ah}
        bv = (bh.get("value"), tuple(sorted(bh.get("covers") or [])))
        same_val = len(vals) == 1 and next(iter(vals)) == bv and not b.get("conflicting_holiday_policy")
        same_span = any(span_overlap(e.get("text", ""), bh.get("span") or "") for e in ah)
        res["holiday_policy"] = "agreed" if same_val and same_span else "disputed"
        if same_val and not same_span:
            res["notes"].append("祝日の扱いの値は同じだが、span が違う所を指す")
    else:
        res["holiday_policy"] = "disputed"
        res["notes"].append("祝日の扱いを片方だけが読んだ: " + ("A" if ah else "B"))
    # 休みの日と休みの期間
    a_days, a_pairs = _off_days_a(a_excs)
    b_days_, b_pairs = _off_days_b(b, period)
    res["off_days"] = "agreed" if a_days == b_days_ else "disputed"
    if a_days != b_days_:
        res["notes"].append(f"休みの日が違う: A だけ {sorted(a_days - b_days_)[:5]}・B だけ {sorted(b_days_ - a_days)[:5]}")
    res["closure_pairs"] = "agreed" if a_pairs == b_pairs else "disputed"
    if a_pairs != b_pairs:
        res["notes"].append(f"休みの期間が違う: A {sorted(a_pairs)}・B {sorted(b_pairs)}")
    # ほかの回の数え方: A は文が言っているときだけ (コードは推し量らない)。B が値を言えば食い違い (人の policy の裁定)
    b_others = [x for x in b.get("year_end") or [] if x.get("others") in ("unchanged", "shifted")]
    a_others = [e for e in a_excs if e["kind"] == "year_end" and e.get("others")]
    res["others"] = "agreed" if bool(b_others) == bool(a_others) else "disputed"
    # deferred・caveat: 和 (止める方)。B だけが言うものは呼ぶ側が例外に足す
    a_def = [e for e in a_excs if e["kind"] == "deferred"]
    a_cav = [e for e in a_excs if e["kind"] == "caveat"]
    for x in b.get("deferred") or []:
        if not same_place(a_def, x.get("span") or "", sentences):
            res["deferred_b_only"].append(x)
    for x in b.get("caveats") or []:
        if not same_place(a_cav + a_def, x.get("span") or "", sentences):
            res["caveats_b_only"].append(x)
    # 但し書き: 和。B が挙げた文が、A の使った文と A の但し書きの文で読み切れていなければ (span_covered。文の所で比べる。
    # 文字の一部が同じだけでは外さない)、B だけの但し書き
    a_texts = list(a_used_texts) + [q["text"] for q in a_quals]
    for x in b.get("qualifiers") or []:
        sp = x.get("span") or x.get("text") or ""
        if not squash(sp):
            continue
        if span_covered(sp, a_texts):
            continue
        res["qualifiers_b_only"].append(x)
    # 期間
    v = b.get("valid") if isinstance(b.get("valid"), dict) else None
    if v and (v.get("from") or v.get("to")) and period and period.get("valid_to"):
        same = (not v.get("from") or v.get("from") == period["valid_from"]) and (not v.get("to") or v.get("to") == period["valid_to"])
        res["valid"] = "agreed" if same else "disputed"
        if not same:
            res["notes"].append(f"有効期間が違う: B {v.get('from')}〜{v.get('to')}・A {period['valid_from']}〜{period['valid_to']}")
    elif not v and not (period and period.get("valid_to")):
        res["valid"] = "agreed"
    # 開庁時間: B が開庁時間の文と言ったものを A が方針に使っていれば食い違い
    bad = [x for x in b.get("ignored_office_hours") or [] if any(span_overlap(e.get("text", ""), x.get("span") or "")
                                                                 for e in a_excs if e["kind"] in ("holiday_policy", "year_end", "cancelled"))]
    res["office"] = "disputed" if bad else "agreed"
    if bad:
        res["notes"].append("B が開庁時間の文とした文を A が方針に使った")
    return res


def policy_states_for(kinds_used, pres):
    """系列が使う方針の値の種類 (holiday_policy・off_days・closure_pairs・others・valid) と compare_policy の結果 → 状態のリスト"""
    return [pres.get(k, "single") for k in kinds_used] + [pres.get("office", "single")]


def selftest():
    ok = True
    ok &= combine(["agreed", "agreed"]) == "agreed" and combine(["agreed", "single"]) == "single" and combine(["agreed", "disputed"]) == "disputed"
    ok &= combine(["agreed", "agreed_weak"]) == "agreed_weak"
    ok &= independence({"kind": "code"}, {"kind": "llm", "model": "x"}) == "code_llm"
    ok &= independence({"kind": "llm", "model": "x"}, {"kind": "llm", "model": "x"}) == "llm_same_model"
    ok &= state(True, indep="llm_same_model") == "agreed_weak"
    ok &= span_overlap("※祝日も収集します（年末年始を除く）", "祝日も収集します") and not span_overlap("祝日も収集します", "閉庁日：土曜日")
    ok &= compare_day_cell([8, 22], {"text": "8・22", "days": None}) == "agreed"
    ok &= compare_day_cell([8, 22], {"text": "8・23", "days": [8, 23]}) == "disputed"
    ok &= compare_day_cell(None, {"text": "13", "days": [13]}) == "single"
    ok &= label_same("火曜日/金曜日\nEvery week\nTuesday & Friday", "火曜日/金曜日 Every week Tuesday & Friday")
    ok &= not label_same("Aコース", "Bコース") and not label_same("A地区", "B地区") and label_same("Ａコース\nCourse A", "Aコース Course A")
    per = {"valid_from": "2026-04-01", "valid_to": "2027-03-31"}
    ok &= md_date("12/31", per) == "2026-12-31" and md_date("1月1日", per) == "2027-01-01"
    return ok


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "selftest":
        ok = selftest()
        print("OK" if ok else "NG")
        return 0 if ok else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
