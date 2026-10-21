"""規則と例外・方針から系列 (地区 × 品目 × 期間) の日付を作る (DESIGN.md §2.5・§2.6)

  python nationwide/readers/expand.py traps            # 罠の再現セット (eval/traps.json) の文法・広げ方の段を確かめる

規則から日付を作ってよいのは、§2.5 の条件 1〜6 がすべてそろったときだけ。1 つでも欠けたら answerable: false で、理由を blocked_by に書く:
  1. 規則が 1 つに決まる (rule_unparsed・rule_ambiguous・no_anchor)
  2. 期間と版の証拠 (period_unknown・version_unproven)
  3. 祝日の扱いと、どの種類の休日までか (holiday_policy_missing・holiday_shift_target_missing。覆われない休日はその日だけ不明)
  4. 年末年始と休み: (A) 年末年始の文が 1 つも無ければ year_end_missing。(B) 毎週の規則は休みの日を抜くだけ。
     (C) 月ごとの規則は窓 W (12/15〜1/31) の中を、窓の中の日付の一覧か、休みに当たる回の扱いとほかの回の数え方の両方の文が
     無ければ year_end_window で不明にする。(D) 年末年始の外の休み・祝日の休みで月ごとの規則の回が消える月は closure_month で不明
  5. 同じ期間の印字の日付の資料が無いか、もう読んだ (pending_printed)
  6. 使われなかった但し書き・決まりの無いしるしが無い (unconsumed_qualifier:<id>・markup_unhandled:<種類>)
印字・一覧の日付にも、2 (版)・6 (但し書きとしるし)・deferred (名前のある期間は印字より強い) を同じように掛ける。
印字と規則の両方があれば、全部の月 (12・1 月も) で比べ、例外の原文で説明のつかない (地区, 品目, 月) は rule_vs_printed の裁定へ。
"""
import datetime as dt, re, sys

import categories, consume, content, holidays, rules

W_FROM, W_TO = (12, 15), (1, 31)
YE_PERIODS = ("年末年始", "年末", "年始", "正月")
OK_STATUS = ("agreed", "agreed_weak", "adjudicated")


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _days(a, b):
    a, b = _d(a), _d(b)
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


def _month_range(y, m):
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


# ---- 窓 W ----
def year_end_windows(valid_from, valid_to, closures=()):
    """期間の中の年末年始の窓 W (12/15〜1/31) のリスト [(from, to)]。休みの期間 (closures の (last, first)) が
    12/15 より前に始まるか 1/31 より後に終わるなら、窓をそこまで広げる"""
    lo, hi = _d(valid_from), _d(valid_to)
    out = []
    for y in range(lo.year - 1, hi.year + 1):
        a, b = dt.date(y, *W_FROM), dt.date(y + 1, *W_TO)
        for last, first in closures:
            if last and first and a - dt.timedelta(days=40) <= _d(last) <= b:
                a = min(a, _d(last) + dt.timedelta(days=1))
                b = max(b, _d(first) - dt.timedelta(days=1))
        a, b = max(a, lo), min(b, hi)
        if a <= b:
            out.append((a, b))
    return out


def in_scope(exc, category_id, district_id=None, period_id=None):
    sc = exc.get("scope") or {}
    cs, ds, ps = sc.get("category_ids"), sc.get("district_ids"), sc.get("period_ids")
    return (cs in (None, "*") or category_id in cs) and (not ds or district_id in ds) and (not ps or period_id in ps)


# ---- 方針の文 → 例外 (読み取り役 A) ----
AMBIGUOUS = None              # value_scope の「品目が決まらない」(その文は値に使わず、但し書きとして残す)
_EXCEPT = re.compile(r"□[^。、，,□]{0,2}?(?:以外|を?除)")         # 「燃やすごみ以外は」「びん・缶を除き」(「年末年始を除く」は品目でない)
_KEYS = {k for _, ks in categories.COARSE_RULES for k in ks}

# 名指しの消し跡 (□ 公式の名前・辞書の分類、◇ 辞書に無い品目の言葉) と、その前後の付け足し (「資源物」「プラ製容器包装」「金属類」)
_MARK_AFFIX = re.compile(r"[製用]?([□◇])(?:製品|製|類|物|等|系|用|品|性)?")
_CLAUSE = re.compile(r"[□◇。、，,．；;：:（）()「」『』［］\[\]【】〔〕<>＜＞※＊*◆■●○◎★☆/／!?！？→⇒~〜～]+")
_POLICY = re.compile(r"祝|休|年末|年始|正月|盆|連休|収集|回収")
# 主題 (「Xは」「Xも」) の中の、品目を狭めない言葉: 方針そのもの (祝日・年末年始)、収集の言葉、「ごみ」「すべて」「各」「全」、
# 主題が市の名前だけ (「市川市は」= 市の全体)。消したあとに漢字・カタカナ・英字が残れば、品目 (か地区・住まいの種類など) の名前
# = どの品目の値か決まらない (地区名・「集合住宅」は消さない。言っていない地区・品目に広げない)
_NOT_NAME = re.compile(
    r"国民の(?:祝日|休日)|振り?替え?休日|祝祭日|祝休日|祝日|祭日|休日|ハッピーマンデー|年末年始|年末|年始|お?正月|元日|元旦|"
    r"大晦日|お盆|盆|大型連休|連休|ゴールデンウ[ィイ]ーク|シルバーウ[ィイ]ーク|GW|スケジュール|お住まいの(?:地域|地区)|"
    r"期間|当日|前日|翌日|翌週|翌月|毎週|毎月|隔週|"
    r"今年度|来年度|年度|本年|今年|来年|例年|今回|今後|当分|当面|令和|平成|午前|午後|時間|場合|以降|以前|以内|平日|"
    r"原則|基本|通年|引き?続き|従来|及び|並びに|又は|指定日|(?:曜日|祝日|休日|年末年始)以外|以外の(?:祝日|休日|日)|"
    r"[一-龥々ヶ]{1,5}市$|"
    r"年内|年明け|毎年|休業|業務|作業|代替|振替|各[一-龥々ァ-ヺー]+|"
    r"収集日程|回収日程|日程表|収集日|回収日|収集車|収集所|収集|回収|休み明け|休み|曜日|開始|最終|再開|特別|変則|間隔|本日|特に|閉庁|開庁|"
    r"その他の(?:祝日|休日)|そのほかの(?:祝日|休日)|集積所|ステーション|し尿|汲み取り|くみ取り|"
    r"雨天|荒天|悪天候|天候|台風|通常|平常|通り|同様|同じ|臨時|変更|予定|日程|日時|"
    r"重な|当た|該当|含|除|行|実施|出し方|出|指定袋|袋|カレンダー|お知らせ|下記|上記|以下|以上|注意|本市|当市|当町|当村|"
    r"市内|町内|村内|全域|ごみ|ゴミ|資源|すべて|全て|全部|各種|各|全|品目|"
    r"[祝休曜月火水木金土日週年回第分前後中間際頃時朝夜雨雪次方等翌々的目続0-9]")
_NAME = re.compile(r"[一-龥々〆ヶ]+|[ァ-ヺー]{2,}|[A-Za-z]{2,}")
# 名指しした品目の名前の一部: 「金属・陶器・ガラスごみ」(1 つの「…ごみ」に並ぶ)、「燃やすごみ（生ごみ・紙おむつなど）」(品目のかっこ書き)
_ENUM = re.compile(r"[□◇](?:・[□◇])+(?=ごみ|ゴミ)|□(?:ごみ|ゴミ)?[（(][^（）()]*[）)]")
# 「<言葉>ごみ」: 直前の漢字・カタカナの語 (「危険ごみ」「大型ごみ」) か、ひらがなの修飾 (「出せるごみ」「燃やしてはいけないごみ」)。
# 助詞のあと (「すべてのごみ」)、副詞・接続の言葉のあと (「通常通りごみの」)、品目を狭めない「各」「全」は除く。
# 「家庭ごみ」は除かない (京都市の「家庭ごみ」は燃やすごみだけで、資源ごみと分ける。すべての品目とは決まらない)
_COMPOUND = re.compile(r"([一-龥々〆ヶ]+|[ァ-ヺー]+|[ぁ-ゖ]+)(ごみ|ゴミ)(?!袋|箱|ボックス|ネット|シール|処理券|集積所|ステーション|置き?場)")
_NOT_COMPOUND = re.compile(r"(?:[のがをにでとやへはもかて]|から|まで|より|など|だけ|のみ|ずつ|ごと|ため|ので|とおり|どおり|通り|また|なお|"
                           r"ただし|および|または|ならびに|及び|並びに|又は|おける|関する|ついての|よる)$")
_GENERIC_PRE = re.compile(r"各|全|各種|資源|各家庭")


def _other_names(t):
    """名指しを消した文 (□・◇) に残る、辞書に無い品目の名前の集まり:
    「危険ごみ」「埋立ごみ」(資料の名前でない「<言葉>ごみ」。「すべてのごみ」「各ごみ」は品目を狭めない) と、
    祝日・年末年始・収集の文の主題「Xは」「Xも」の X (「剪定枝は」「陶器類は」「白色トレイは」。祝日・年末年始・「ごみ」は除く)"""
    out = set()
    t = _MARK_AFFIX.sub(r"\1", t)
    for m in _COMPOUND.finditer(t):
        w = m.group(1)
        # 「年末年始ごみ」「令和8年度ごみ」「集積所ごみ」の前の語は品目でない (主題と同じ言葉を消して何も残らない)
        if not _NOT_COMPOUND.search(t[max(0, m.start(1) - 4):m.end(1)]) and not _GENERIC_PRE.fullmatch(w) and \
                (not re.match(r"[一-龥々〆ヶァ-ヺー]", w) or _NAME.search(_NOT_NAME.sub("", w))):
            out.add(w + m.group(2))
    if not _POLICY.search(t):
        return out
    for c in _CLAUSE.split(re.sub(r"又は|または|もしくは|及び|および|並びに|ならびに", "・", t)):
        for seg in re.split(r"[はも]", c)[:-1]:
            # 主題は名詞で終わる (「詳しくは」「収集しないものは」「祝日でも」「本市では」「12月31日までは」は品目でない)
            if re.search(r"(?:[一-龥々〆ヶァ-ヺー0-9]|ごみ|ゴミ)$", seg):
                out |= set(_NAME.findall(_NOT_NAME.sub("", seg)))
    return out


def _marked(t, cats, generic_labels, sep=""):
    """公式の名前・辞書の分類を □、辞書に無い品目の言葉を ◇ にした文 → (文, 当たった分類の鍵, 辞書に無い品目の言葉)。
    sep が空でなければ、文の空白はその字 (語の切れ目。「令和8年度 ごみの収集日」の「年度ごみ」を品目にしない)"""
    t = re.sub(r"[□◇]", "※", t)                   # 本文の □・◇ (箇条書きのしるし) を消し跡と取り違えない
    labs = []
    for c in cats.values():
        lab = re.sub(r"\s+", "", categories.nfkc(c.get("official_label") or ""))
        if len(lab) >= 2 and (generic_labels or not set(c.get("coarse") or ()) <= {"recyclable", "other"}):
            labs += [lab] + ([re.sub(r"\s+", sep, categories.nfkc(c["official_label"]).strip())] if sep else [])
    for lab in sorted(labs, key=lambda x: -len(x)):
        t = t.replace(lab, "□")
    t = re.sub(r"[(（][^)）]*(?:回|曜)[^)）]*[)）]", "", t)
    keys = set()
    for rx, ks in categories.COARSE_RULES:
        if rx.search(t):
            keys |= set(ks)
            t = rx.sub("□", t)
    words = []
    t = categories.OTHER_ITEMS.sub(lambda m: words.append(m.group(0)) or "◇", t)
    spans = [m.span() for m in _ENUM.finditer(t) if "□" in m.group(0)]       # その中の辞書に無い言葉は、名指しした品目の名前の一部
    pos = [i for i, ch in enumerate(t) if ch == "◇"]
    return t, keys, {w for w, i in zip(words, pos) if not any(x <= i < y for x, y in spans)}


def _item_words(text, cats, generic_labels=True):
    """文・見出しの品目の言葉 → (資料の品目に当たらない品目の集まり, 名指しした品目を「以外」「を除く」で外しているか)。
    categories.named_in と同じ順で公式の名前・辞書の分類を消し、残った分類のうち資料のどの品目の分類にも無いもの
    (「ペットボトルは祝日も収集しません」で、表が燃やすごみ・燃やさないごみだけなら pet_bottle) と、辞書に無い品目の名前
    (categories.OTHER_ITEMS と _other_names。「剪定枝」「危険ごみ」。分類の鍵 _KEYS でない文字列)。
    「<言葉>ごみ」と主題 (_other_names) は空白を語の切れ目にして見る。1 字ずつ空いた文 (PDF の「危 険 ご み」) は詰めて見る"""
    s = categories.nfkc(text)
    t, keys, names = _marked(re.sub(r"\s+", "", s), cats, generic_labels)
    have = {k for c in cats.values() for k in (c.get("coarse") or ())}
    toks = s.split()
    spaced = len(toks) >= 4 and sum(len(x) == 1 for x in toks) * 3 >= len(toks) * 2
    tb, _, names_b = (t, keys, names) if spaced else _marked("、".join(toks), cats, generic_labels, "、")
    return (keys - have) | names_b | _other_names(tb), bool(_EXCEPT.search(t))


def _level_value(text, cats):
    """見出し・同じ行のほかの文の名指し (値を当てる向き) → id のリスト / "*" / AMBIGUOUS / "" (名指しが無い。上の段へ)。
    文そのものは閉じた見本 (sentence_scope) で読む。ここは文の外の段 (見出し・同じ項目のほかの文) で、どの品目の節かを決めるだけ。
    資料の品目に無い品目だけを言う (「ペットボトルは…」)、名指しを「以外」「を除き」で外す、なら AMBIGUOUS
    (ほかの品目にも上の見出しにも広げない)。表にある品目と一緒なら (「燃やすごみとペットボトルは」) その品目だけ。ただし
    中身の分からない品目 (「資源」「その他」) があれば、表に無い品目はその中かもしれない → AMBIGUOUS。
    辞書の分類の無い品目 (「剪定枝」「危険ごみ」「陶器類」) は、資料のどの品目の中か分からない (陶器は燃やさないごみの中かも
    しれない) ので、表にある品目と一緒でも AMBIGUOUS (狭めると、その品目で文が使われたことになる)"""
    unknown, excluded = _item_words(text, cats)
    ids, generic = categories.named_in(text, cats)
    if excluded or unknown - _KEYS or \
            (unknown and (not ids or any(set(c.get("coarse") or ()) <= {"recyclable", "other"} for c in cats.values()))):
        return AMBIGUOUS
    if ids and not generic:
        return sorted(ids)
    if not ids and generic == {"ごみ"}:
        return "*"                                       # 「ごみの収集は…」だけ = すべての品目
    if not ids and not generic:
        return ""
    return AMBIGUOUS                                     # 「資源」だけ・名指しと「ごみ」「資源」が混ざる → どの品目か決まらない


# ---- 文の品目: 閉じた見本 (値を当てる向き。value_scope・cell_notes) ----
# 方針の文 (祝日・年末年始・休み・第5週) の値をどの品目に当てるかは、文全体が下の見本に当たるときだけ決める。
# 当たらない文は AMBIGUOUS: 値に使わず、使われなかった但し書きとして残して系列を止める (コードで読めない言い回しは、
# 2 つ目の読み (LLM) と人の裁定に回す。言っていない品目・地区に値を当てるより、答えないほうがよい)。
#   1. 正規化 (_scope_text): NFKC、空白を詰める (PDF の「危 険 ご み」)、行頭の印 (※・◆・「注意」「(注)」) を外す、日付のあとの
#      曜日のかっこ書き「(火)」「(月曜日)」を外す、文末の「。」と、文全体を包むかっこを外す
#   2. 印 (_scope_marks): 資料の品目の公式の名前と、辞書の分類 (categories.COARSE_RULES) のうち資料の 1 つの品目だけが持つものを
#      その品目の印 (私用領域の 1 字) に。資料に無い分類・2 つ以上の品目が持つ分類・辞書に無い品目の言葉 (categories.OTHER_ITEMS) は ◇
#   3. 最初の方針の言葉 (_ANCHOR) の前が主題 (_SUBJECTS のどれかにそのまま当たること)、そこから後が残り (_rest_objects)
_CAT_RANGE = "\ue000-\uf8ff"
_CAT = f"[{_CAT_RANGE}]"                                                      # 資料の品目の印 (1 品目 1 字)
_CATS = rf"{_CAT}(?:(?:・|、|,|と|及び|および){_CAT})*"                         # 「燃やすごみ・燃やさないごみ」
_HOL = r"(?:国民の祝日|国民の休日|振り?替え?休日|祝祭日|祝休日|祝日|祭日|休日)"
_HOLS = rf"{_HOL}(?:(?:・|、|,|と|及び|および){_HOL})*"                         # 「祝日・振替休日」
_YE_W = r"(?:年末年始|年末|年始|お?正月|元日|元旦|大晦日)"
_DATE = r"(?:(?:令和|平成)\d{1,2}年|\d{4}年)?(?:\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2})"
_DATES = rf"{_DATE}(?:(?:・|、|,|と|及び|から|~|-){_DATE}|(?:から|~)\d{{1,2}}日)*(?:まで|の間|の期間|期間中)?"
_SEASON = r"(?:大型連休|連休|お盆|ゴールデンウ[ィイ]ーク|GW)(?:中|期間中|の期間)?"
_W_DAY = r"(?:(?:12|1)月\d{1,2}日|(?:12|1)/\d{1,2})"
_W_PAREN = rf"\({_W_DAY}(?:から|~){_W_DAY}(?:まで)?\)"                        # 年末年始のかっこ書き (窓 W の中の 1 つの期間だけ)
_REASON = rules.REASON                                                         # 「施設の定期点検のため」(閉じた並び。地名のある理由は外さない)
_TIME = r"(?:朝|午前|午後)?\d{1,2}時(?:\d{1,2}分|半)?(?:まで)?"
# 方針の言葉: 主題と残りの切れ目 (ここより前が主題)
_ANCHOR = re.compile("|".join((_HOL, r"ハッピーマンデー", _YE_W, r"お盆|盆|大型連休|連休|ゴールデンウ[ィイ]ーク|GW", r"収集|回収|お?休み",
                                _DATE, r"(?:毎?月の)?[「『]?(?:第5|第五|5番目|5週目)", r"雨天|荒天|悪天候")))
# 主題の見本 (主題の末尾の「は」「では」「の」「、」などを外して、どれかにそのまま当たること)
_SUBJECTS = (
    ("", re.compile(r""), ""),                                                 # 主題なし: 方針の言葉で始まる (「祝日も収集します」「年末は…」)
    ("ごみ全体", re.compile(r"(?:すべての|全ての|全部の|各|全)?(?:ごみ|ゴミ)"), "*"),   # 「ごみは」「すべてのごみは」「各ごみの収集は」
    ("自治体の全域", re.compile(r"本市|当市|当町|当村|市内(?:全域)?|全域"), "*"),     # 「本市では」(「本町」は地区の名前にも使うので入れない)
    ("資料の品目", re.compile(_CATS), "ids"),                                   # 「燃やさないごみは」「燃やすごみ・びんは」「可燃物は」
    ("品目と中身の例", re.compile(rf"{_CAT}\(◇+(?:[・、]◇+)*(?:など|等)?\)"), "ids"),  # 「燃やすごみ（生ごみ・紙おむつなど）は」
)
# 「家庭ごみ」は「ごみ全体」に入れない (京都市の「家庭ごみ」は燃やすごみだけで、資源ごみと分ける。どの品目か決まらない)
_SUBJ_TAIL = re.compile(r"[はもでのが、,:]+$")
# 残りの中の主題 (「Xは」「Xも」) の見本。品目・地区の名前は入らない (残りの主題は方針の言葉だけ)
_TOPICS = (
    ("祝日", re.compile(rf"(?:収集日が)?{_HOLS}(?:\({_HOLS}を?含[むめ]\))?(?:の日)?")),                                       # 「祝日は」「祝日・振替休日も」
    ("祝日と重なる日", re.compile(rf"(?:収集日が)?{_HOLS}(?:と|に)(?:重なった|重なる|あたる|当たる)(?:場合|とき|日)?")),  # 「収集日が祝日と重なった場合も」
    ("年末年始", re.compile(rf"{_YE_W}(?:{_W_PAREN})?(?:の期間|期間中?|中)?")),                      # 「年末は」「年末年始（12月29日～1月3日）は」
    ("日付", re.compile(_DATES)),                                                                   # 「12月31日は」「12月31日から1月3日までは」
    ("連休", re.compile(_SEASON)),                                                                  # 「大型連休中も」
    ("第5週", re.compile(r"(?:毎?月の)?[「『]?(?:第5|第五|5番目)(?:週|の(?:週|曜日))?[」』]?")),     # 「第5週は」「月の「5番目の曜日」は」
)
_EXCEPT_OK = re.compile(rf"{_YE_W}(?:{_W_PAREN})?")                            # 外してよいのは年末年始だけ (「祝日も収集します（年末年始を除く）」)
_COLLECT_TAIL = re.compile(r"(?:の)?(?:ごみの?)?(?:収集|回収)(?:日|作業)?$")      # 主題の「…の収集」(「祝日の収集は」「年末年始のごみ収集は」)
_CLAUSE_MARKS = (
    ("only", re.compile(r"のみ|だけ|に限(?:り|る|って)|限定")),                   # 「燃やすごみのみ収集」「東地区に限り」: いつも止める
    ("except", re.compile(r"を除(?:く|き|いて|外)?|以外")),                       # 「（月曜日を除く）」: 年末年始のほかは止める
    ("topic", re.compile(r"については|に関しては|においては|の場合(?:は|も|でも|には)?|(?<!ま)では|(?<!ま)でも|には|とは|は|も")),  # 主題: _TOPICS で見る
)
# 主題・外しの印の無いかっこ書きの見本: 日付・時刻 (「（12月29日～1月3日）」)、祝日を含む (「（祝日含む）」)
_BARE_PAREN = re.compile(rf"{_DATES}|{_TIME}(?:~{_TIME})?|{_HOLS}を?含[むみめ](?:ます)?")
_CLAUSE_BOUNDARY = "、,:;「」『』※"                          # 「/」は日付 (1/1) に使うので切れ目にしない
# 収集の目的語 (「祝日も燃やすごみを収集します」「…のため可燃ごみの収集は行いません」): 「X を/の 収集」の X
_OBJECT = re.compile(r"([^、,:;「」()※はもがで]*?)(?:を|の)(?:ごみの?)?(?:収集|回収)")
_OBJECT_X = re.compile(rf"(?:{_REASON})?(?:通常(?:どおり|通り|とおり)?|平常どおり)?(?:(?:{_HOLS}|{_YE_W}|{_DATES}|{_SEASON})の?)?"
                       rf"(?:{_CATS}|ごみ|ゴミ)?")
_PREDICATE = re.compile(r"休|収集(?!日)|回収(?!日)|行[いうわ]|実施|あり|なし|ない|しま|せん|出せ|出し|です|ます")   # 目的語の前にあれば対比
# 地区・住まいの種類・コース・一部 (残り・同じ行・見出しにあれば、言っていない地区に広げない)
_DISTRICT = re.compile(r"地区|地域|区域|丁目|ブロック|エリア|コース|班|自治会|町内会|集合住宅|マンション|アパート|団地|一部|山間|島しょ|離島|お住ま?い")
# 決めていない言い方 (「収集する場合があります」「原則として」「予定です」「収集しますか」)
_HEDGE = re.compile(r"場合[がも](?:あ|ご)|こと[がも](?:あ|ご)|ところ[がも]|原則|基本的|予定|未定|かどうか|ですか|ますか|\?|"
                    r"ほとんど|おおむね|たいてい|大体|だいたい")
_GOMI_OK = re.compile(r"(?:[のをはもがにでと、,(「:]|各|全|通り|どおり|とおり)$")   # 「ごみ」の前 (「危険ごみ」「出せるごみ」は品目の名前)
# 残りに使ってよい漢字・カタカナの言葉 (閉じた語彙)。これと理由の句 (_REASON) を除いて漢字・カタカナ・英字が残れば、
# 見本に無い名前 (地区・施設・品目) かもしれない → AMBIGUOUS (「祝日も山田町で収集します」「祝日も収集します（山田町）」)
_REST_WORDS = re.compile("|".join((
    _HOLS, r"ハッピーマンデー", _YE_W, _SEASON, _DATES, _TIME,
    r"(?:毎週|毎月|隔週)?(?:第\d)?[月火水木金土日](?:曜日?)?",                       # 曜日 (「月曜日～金曜日」「月の「5番目の曜日」」)
    r"第?\d+(?:番目|回|週目?|日|月|年度?)?",
    r"収集日?|回収日?|ごみ|ゴミ|お?休み|休業|休止|中止|実施|通常|平常|通り|変更|最終|最後|開始|再開|行[いうわえ]|含[むみめ]|除[くきい]|"
    r"以外|期間|間|中|場合|重な|当た|同様|当日|前日|翌日|翌週|日程|曜日|週|雨天|荒天|悪天候|出[しせす]|朝|午前|午後|毎|"
    r"及び|並びに|又は|全|各|以降|以前|注意|確認|協力|了承|願|下さい")))
_LEAD = re.compile(rf"^(?:[※◆◇●○◎■□★☆・*\-−]\s*|[{_CAT_RANGE}]\s+|(?:注意|注)\d*(?:[:：]\s*|\s+)|\(注\d*\)\s*|お知らせ[:：]\s*|"
                   r"(?:また|なお|ただし|但し|さらに)[、,]?\s*)+")
_WDAY_PAREN = re.compile(r"(?<=[\d日])\((?:[月火水木金土日祝](?:曜日?)?[・、]?)+\)")   # 日付のあとだけ (「収集します（月曜日）」は残す)


def _scope_text(text):
    """正規化 (上の 1)。本文の □・◇ (箇条書きのしるし) と私用領域の字 (PDF の字形) は、品目の印と取り違えないよう ※・◇ にする"""
    t = categories.nfkc(text).replace("〜", "~")
    t = re.sub(r"[□◇]", "※", t)
    for _ in range(3):
        t = _LEAD.sub("", t.strip())
        t = re.sub(r"\s+", "", t)
        if t.startswith("(") and t.endswith(")") and _one_group(t):
            t = t[1:-1]
    t = re.sub(f"[{_CAT_RANGE}]", "◇", t)
    return _WDAY_PAREN.sub("", t).rstrip("。.")


def _one_group(t):
    depth = 0
    for i, ch in enumerate(t):
        depth += (ch == "(") - (ch == ")")
        if depth == 0 and i < len(t) - 1:
            return False
    return depth == 0


def _scope_marks(t, cats):
    """印 (上の 2) → (印をつけた文, {印: 品目の id})"""
    chars, labs = {}, []
    for i, cid in enumerate(sorted(cats)):
        ch = chr(0xE000 + i)
        chars[ch] = cid
        lab = re.sub(r"\s+", "", categories.nfkc(cats[cid].get("official_label") or ""))
        if len(lab) >= 2:
            labs.append((lab, ch))
    for lab, ch in sorted(labs, key=lambda x: -len(x[0])):
        t = t.replace(lab, ch)
    for rx, keys in categories.COARSE_RULES:
        own = [ch for ch, cid in chars.items() if set(cats[cid].get("coarse") or ()) & set(keys)]
        t = rx.sub(own[0] if len(own) == 1 else "◇", t)
    t = categories.OTHER_ITEMS.sub("◇", t)
    t = re.sub(rf"({_CAT}|◇)(?:類|物|ごみ|ゴミ)", r"\1", t)                    # 「可燃物」「金属類」「生ごみ」
    t = re.sub(rf"({_CAT})製\1", r"\1", t)                                    # 「プラ製容器包装」
    t = re.sub(rf"[「『]({_CATS})[」』]", r"\1", t)                           # 「「燃やせないごみ」の収集は」
    return t, chars


def _clauses(t):
    """残り → [(種類, 句)]。句は、同じかっこの深さで前の切れ目 (、・「」・前の主題の印) から印までの文字"""
    out, starts, i = [], [0], 0
    while i < len(t):
        ch = t[i]
        if ch == "(":
            starts.append(i + 1)
        elif ch == ")":
            if len(starts) > 1:
                starts.pop()
        elif ch in _CLAUSE_BOUNDARY:
            starts[-1] = i + 1
        else:
            m = next((m for m in (rx.match(t, i) for _, rx in _CLAUSE_MARKS) if m), None)
            if m:
                kind = next(k for k, rx in _CLAUSE_MARKS if rx.match(t, i))
                out.append((kind, t[starts[-1]:i]))
                starts[-1] = i = m.end()
                continue
        i += 1
    return out


def _topic_key(chunk):
    """残りの主題の句 → 数える鍵 / "" (方針の名詞・理由・時刻。数えない) / AMBIGUOUS (見本に無い主題)"""
    c = re.sub(r"^(?:ただし|但し|なお|また|さらに|[にでがをとのやへ])+", "", chunk)
    c = _COLLECT_TAIL.sub("", c)
    if c in ("", "ごみ", "ゴミ") or re.fullmatch(_REASON, c) or re.fullmatch(_TIME, c):
        return ""
    if any(rx.fullmatch(c) for _, rx in _TOPICS):
        return c
    m = re.match(rf"{_DATES}(?:まで|から)?", c)                                # 「12月28日まで年始は」の「年始」(前の主題の続き)
    if m and 0 < m.end() < len(c):
        return _topic_key(c[m.end():])
    return AMBIGUOUS


def _rest_objects(rest, object_ok=True):
    """方針の言葉から後 (上の 3) → 収集の目的語の品目の印の集まり (無ければ空) / AMBIGUOUS。
    残りに辞書に無い品目 (◇)・中身の分からない「資源」・地区の言葉・決めていない言い方があれば、
    目的語のほかの所で品目を言えば (「祝日は休み（燃やすごみは収集）」の対比)、目的語が 2 つ・方針の言葉より後
    (「祝日は休み（燃やすごみの収集は行います）」)、主題が見本に無い (「月曜日の祝日は」「海の日は」)、主題が 2 つ以上
    (「祝日は休み（5/3～5/5は収集）」。「年末は…年始は…」の組だけはよい)、年末年始のほかを外す・限る、なら AMBIGUOUS"""
    if re.search(r"◇|資源", rest) or _DISTRICT.search(rest) or _HEDGE.search(rest):
        return AMBIGUOUS
    objs = []
    for m in _OBJECT.finditer(rest):
        x = m.group(1)
        if not _OBJECT_X.fullmatch(x):
            return AMBIGUOUS                                    # 「祝日もざつがみを収集」「祝日も危険ごみを収集」
        if re.search(_CAT, x):
            if not object_ok or _PREDICATE.search(rest[:m.start(1)]):
                return AMBIGUOUS
            objs.append(m)
    if len(objs) > 1:
        return AMBIGUOUS
    marks, t = set(), rest
    if objs:                                                    # 目的語の品目だけを消す (「燃やすごみ、燃やさないごみの収集」の前の品目は残る)
        m = objs[0]
        marks = set(re.findall(_CAT, m.group(1)))
        t = rest[:m.start(1)] + re.sub(_CATS, "", m.group(1)) + rest[m.end(1):]
    if re.search(_CAT, t):
        return AMBIGUOUS                                        # 目的語のほかの所で品目を言う
    for g in re.finditer(r"ごみ|ゴミ", t):
        if g.start() and not _GOMI_OK.search(t[:g.start()]):
            return AMBIGUOUS                                    # 「危険ごみ」「出せるごみ」
    if re.search(r"[一-龥々〆ヶァ-ヺA-Za-z]", _REST_WORDS.sub("", re.sub(_REASON, "", t))):
        return AMBIGUOUS                                        # 閉じた語彙に無い名前
    for g in re.finditer(r"\(([^()]*)\)", t):                  # 主題・外しの印の無いかっこ書きは見本だけ (「（月曜日）」「（月・水）」は止める)
        if not any(rx.search(g.group(1)) for _, rx in _CLAUSE_MARKS) and not _BARE_PAREN.fullmatch(g.group(1)):
            return AMBIGUOUS
    keys = set()
    for kind, chunk in _clauses(t):
        if kind == "only":
            return AMBIGUOUS
        if kind == "except":
            if not _EXCEPT_OK.fullmatch(re.sub(r"^[にでがをとのやへ]+", "", chunk)):
                return AMBIGUOUS
            continue
        k = _topic_key(chunk)
        if k is AMBIGUOUS:
            return AMBIGUOUS
        if k:
            keys.add(k)
    if len(keys) > 1 and not keys <= {"年末", "年始"}:
        return AMBIGUOUS
    return marks


def _limits(t):
    """文の中の外し・限り (「山田町を除く」「東地区のみ」) が年末年始のほかにあるか"""
    return any(k == "only" or (k == "except" and not _EXCEPT_OK.fullmatch(re.sub(r"^[にでがをとのやへ]+", "", c)))
               for k, c in _clauses(t))


def _piece_scope(t, cats, strict=False):
    """正規化した 1 つの文 → "" (主題なし) / "*" (ごみ全体・自治体の全域) / id のリスト / AMBIGUOUS。
    strict はかっこ書きを含む文 (_line_value): 方針の言葉の無い文でも、主題 (「Xは」) があれば見本に当たること
    (「山田町は月・木（祝日も収集）」の「山田町」)"""
    t, chars = _scope_marks(t, cats)
    m = _ANCHOR.search(t)
    if not m:                                                   # 方針の言葉の無い文 (「詳しくはカレンダーをご覧ください」「燃やすごみは月・木」)
        if _limits(t) or _DISTRICT.search(t):
            return AMBIGUOUS
        named = re.search(rf"{_CAT}|◇|資源", t)
        m2 = re.search(r"では|は|も|が|:", t)
        if not named and not (strict and m2):
            return ""
        subj, rest = (t[:m2.start()], t[m2.start():]) if m2 else (t, "")
        kind = next((k for _, rx, k in _SUBJECTS if rx.fullmatch(_SUBJ_TAIL.sub("", subj).strip("【】[]<>"))), None)
        if kind is None or re.search(rf"{_CAT}|◇|資源", rest):
            return AMBIGUOUS
        return sorted({chars[c] for c in subj if c in chars}) if kind == "ids" else kind
    subj = _SUBJ_TAIL.sub("", t[:m.start()]).strip("【】[]<>")
    kind = next((k for _, rx, k in _SUBJECTS if rx.fullmatch(subj)), None)
    if kind is None:
        return AMBIGUOUS                                        # 「東地区では」「Aコースは」「危険ごみは」「東地区の場合、」
    marks = _rest_objects(t[m.start():], object_ok=kind == "")
    if marks is AMBIGUOUS:
        return AMBIGUOUS
    if kind == "ids":
        return sorted({chars[c] for c in subj if c in chars})
    return sorted({chars[c] for c in marks}) if marks else kind


def sentence_scope(text, cats, strict=False):
    """文 (「。」で切れた文が並んでもよい) だけの品目 → "" (主題なし。行・見出しへ) / "*" (ごみ全体) / id のリスト / AMBIGUOUS。
    文ごとに _piece_scope。品目を言う文と、言わない方針の文・ほかの品目を言う文が並べば AMBIGUOUS"""
    pieces = [p for p in _scope_text(text).split("。") if p]
    vals = [_piece_scope(p, cats, strict) for p in pieces]
    if any(v is AMBIGUOUS for v in vals):
        return AMBIGUOUS
    named = {tuple(v) for v in vals if isinstance(v, list)}
    if len(named) > 1:
        return AMBIGUOUS
    if named:
        if any(v == "*" or (v == "" and _ANCHOR.search(p)) for p, v in zip(pieces, vals)):
            return AMBIGUOUS
        return list(named.pop())
    return "*" if "*" in vals else ""


_SUBJ_NEUTRAL = re.compile(r"詳しく|くわしく|詳細|なお|ただし|但し|また|さらに|注意|ご注意")


def _other_value(p, cats):
    """同じ項目のほかの文 → id のリスト / AMBIGUOUS / ""。品目の名指しは見出しと同じ (_level_value。新宿の「金属・陶器・ガラスごみ」)。
    主題 (方針の言葉か「は」「も」の前) が品目でも閉じた語彙でもない名前 (「山田町は月曜日です」)、地区の言葉、年末年始のほかの
    外し・限り (「山田町を除く」) は AMBIGUOUS"""
    t, _ = _scope_marks(_scope_text(p), cats)
    if _DISTRICT.search(t) or _limits(t):
        return AMBIGUOUS
    cut = [x.start() for x in (_ANCHOR.search(t), re.search(r"では|は|も|が|:", t)) if x]
    subj = re.sub(rf"{_CAT}|◇|資源|(?:すべての|全ての|各)?(?:ごみ|ゴミ)", "", t[:min(cut)] if cut else "")
    if re.search(r"[一-龥々〆ヶァ-ヺA-Za-z]", _REST_WORDS.sub("", _SUBJ_NEUTRAL.sub("", subj))):
        return AMBIGUOUS
    v = _level_value(p, cats)
    return "" if v == "*" else v


_PAREN_ONLY = re.compile(r"[(（][^()（）]*[)）]")


def _line_value(line, text, cats):
    """同じ行 (箇条書きの 1 項目) の、文のほかの部分 → id のリスト / AMBIGUOUS / "" (名指しが無い)。
    文がかっこ書きなら、それを含む文 (かっこ書きを除く) の主題を見本で見る (sentence_scope の strict。「東地区は月・木（祝日も収集）」
    「山田町は月・木（祝日も収集）」の「（祝日も収集）」は地区の文 → AMBIGUOUS。「燃やすごみは月・木（祝日も収集）」なら燃やすごみ。
    「毎週木曜日（祝日の収集は休み）」は主題なし)。文が 1 つの文なら、同じ項目のほかの文 (_other_value。新宿「金属・陶器・ガラスごみの
    収集日は…。月の「5番目の曜日」に収集はありません。」)。ほかの文がかっこ書きだけの文で品目を言っていれば AMBIGUOUS
    (かっこ書きの品目で文の品目を決めない: 「祝日は収集しません。（燃やすごみは収集します）」を燃やすごみの休みと読まない)"""
    sq = lambda x: re.sub(r"\s+", "", categories.nfkc(x))
    whole, s = sq(line), sq(text).rstrip("。.")
    pieces = [p for p in re.split(r"(?<=。)", whole) if p.rstrip("。")]
    host = next((p for p in pieces if s and s in p and p.rstrip("。") != s), None)
    if host is not None:
        v = sentence_scope(host.replace(s, "", 1), cats, strict=True)
        return "" if v == "*" else v
    others = [p for p in pieces if p.rstrip("。") != s] if s and s in whole else [whole]
    vals = [_other_value(p, cats) for p in others]
    if any(v is AMBIGUOUS for v in vals):
        return AMBIGUOUS
    if any(v and _PAREN_ONLY.fullmatch(p.rstrip("。")) for p, v in zip(others, vals)):
        return AMBIGUOUS
    return sorted({c for v in vals if v for c in v}) or ""


# 見出し (いちばん上のほか) に使ってよい言葉の閉じた並び: 品目の名前と、ごみの収集・年末年始・お知らせの一般の言葉だけ。
# ほかの言葉 (地名・地区・場所の見出し「厚田区」「東町」「山田町」「北部」「旧厚田村」、月の見出し「4月～9月」) が残る見出しの節の文は、
# その地区・月だけの話かもしれない → AMBIGUOUS (地区の言葉 _DISTRICT を探す書き方では、地名だけの見出しが通った)
_HEAD_WORDS = re.compile("|".join((
    r"(?:令和|平成)?\d{1,4}年度?(?:版)?", r"第?\d+(?:\.|\))?",
    r"ごみ|ゴミ|資源物?|家庭|一般|生活|リサイクル",
    r"収集日程|収集日|収集曜日|収集カレンダー|カレンダー|収集|回収|日程|曜日|時間|日",
    r"出し方|分け方|分別|捨て方|品目|種類|一覧|早見表|表|方法|ルール|マナー",
    r"年末年始|年末|年始|お?正月|祝日|休日|振替休日|祝祭日|ゴールデンウ[ィイ]ーク|大型連休|連休|お盆|お?休み",
    r"お知らせ|ご案内|案内|ご注意(?:ください)?|注意(?:事項)?|注|備考|補足|その他|目次|概要|詳細|基本|よくある質問|Q&A|トップページ|ページ",
    r"について|に関して|に関する|における|の|と|及び|および|等|など|ほか|変更|臨時|特別|休止|中止|再開|開始|新しい|今年度|来年度|本年度|今年|来年",
    r"ページ下部|ページ上部|下部|上部|添付ファイル|添付|ファイル|PDF|下記|上記|以下|ご覧(?:ください|下さい)|ご確認(?:ください|下さい)|確認|手順|"
    r"参照|[はをにでがへも]")))                                              # 案内の見出し (三鷹「収集日程はページ下部の添付ファイルをご覧ください。」)
_HEAD_PUNCT = re.compile(r"[\s・、,，。.:：;；/／()（）\[\]［］【】「」『』<>＜＞〈〉《》■□◆◇●○◎★☆※*\-－―ー~〜～!！?？&＆]+")
_BUT_LEAD = re.compile(r"^(?:[※◆◇●○◎■□★☆・*\-−]\s*)*(?:ただし|但し|なお)")


def generic_heading(h, cats):
    """見出しの文字が、品目の名前と一般の言葉 (_HEAD_WORDS) だけでできているか"""
    t, _ = _scope_marks(_scope_text(h), cats)
    t = _HEAD_PUNCT.sub("", _HEAD_WORDS.sub("", re.sub(_CAT, "", t)))
    return not t


def value_scope(text, cats, heading_path=(), line_text=None):
    """値 (祝日・年末年始・休み・第5週) を当てる品目 → id のリスト / "*" / AMBIGUOUS。
    文そのものは閉じた見本 (sentence_scope) で読む: 主題が無い・「ごみ」全体なら「*」、資料の品目の名前 (か、資料の 1 つの品目だけが
    持つ辞書の分類) だけならその品目、ほかの主題 (地区・住まいの種類・コース・辞書に無い品目・「Xの場合、」「Xについては」)
    や、残りの中の対比 (「祝日は休み（燃やすごみは収集）」) は AMBIGUOUS。
    見出しは、いちばん上 (ページ・表の題。地区は呼ぶ側が資料・表ごとに当てる) のほかは、品目の見出しか一般の見出し (generic_heading の
    閉じた並び) だけ。ほかの見出し (地名・地区・場所・月) の節の文は、文に主題があっても AMBIGUOUS。
    主題が無ければ、同じ行 (_line_value)、近い見出しから上へ (_level_value)、の順に品目の名指しを探す。どこにも無ければ "*"
    (節の外の文はすべての品目。§2.6)。「すべてのごみは」の文が品目の節にあれば、どちらか決まらない → AMBIGUOUS。
    「ただし」「なお」で始まる主題の無い文は、同じ行に主題 (品目) が無ければ AMBIGUOUS (前の段落の品目の話かもしれない)。
    AMBIGUOUS なら呼ぶ側はその文を値に使わず、但し書きとして残す (広げると、言っていない品目・地区に値を当ててしまう。
    狭めると、ほかの品目で文が使われたことになる)"""
    v = sentence_scope(text, cats)
    if v is AMBIGUOUS:
        return AMBIGUOUS
    hs = list(heading_path or ())
    for h in hs[1:]:
        if _DISTRICT.search(categories.nfkc(h)) or not generic_heading(h, cats):
            return AMBIGUOUS                                    # 地名・地区・場所・月の見出しの節 (品目の主語があっても)
    if isinstance(v, list):
        return v
    lv = _line_value(line_text, text, cats) if line_text and line_text != text else ""
    if lv is AMBIGUOUS:
        return AMBIGUOUS
    if v == "" and not lv and _BUT_LEAD.match(categories.nfkc(text).strip()):
        return AMBIGUOUS                                        # 「ただし」「なお」で始まり、同じ行に主題が無い

    def levels():
        yield lv
        for i in range(len(hs) - 1, -1, -1):
            c = _level_value(hs[i], cats)
            yield "" if c == "*" else c
    for c in levels():
        if c is AMBIGUOUS:
            return AMBIGUOUS
        if c:
            return AMBIGUOUS if v == "*" else c
    return "*"


def block_scope(text, cats, heading_path=()):
    """止める向き (deferred・caveat・使われなかった但し書き) の品目 → id のリストか "*"。
    §2.6 の 4・§3.8 の 4 のとおり、文のある節の品目 (節の外なら、すべての品目)。節は、近い見出しから上へ、品目を名指しした
    最初の見出し。見出しに「ごみ」「資源」だけの言葉があれば (「年末年始のごみ・資源収集」) すべての品目。
    文の中の言葉では狭めない (足立「ごみ・資源収集に関する内容は…あだち広報で」の「資源」、「広報紙」の「紙」で狭めると、
    ほかの品目の日付が止まらない)。文が節の外の品目や「ごみ」「資源」、資料の品目に無い品目を名指ししているか、
    名指しを「以外」「を除き」で外していれば、すべての品目に広げる (どの系列の話か決まらない)"""
    sec = "*"
    for h in reversed(list(heading_path or ())):
        ids, generic = categories.named_in(h, cats, generic_labels=False)
        if generic or any(_item_words(h, cats, generic_labels=False)):
            break                                        # 資料に無い品目の節 (「ペットボトル」) は、どの品目の節か決まらない
        if ids:
            sec = sorted(ids)
            break
    if sec == "*":
        return "*"
    ids, generic = categories.named_in(text, cats, generic_labels=False)
    if generic or ids - set(sec) or any(_item_words(text, cats, generic_labels=False)):
        return "*"
    return sec


def _window_for(period_name, dates_md, period):
    """deferred の期間の名前と文の中の日付 → 窓 {"from", "to", "default"} か None (日付の無い年末年始のほかの期間)"""
    if dates_md:
        ds = [rules._year_for(m, d, period) for m, d in dates_md]
        ds = [x for x in ds if x]
        if len(ds) >= 2:
            return {"from": min(ds).isoformat(), "to": max(ds).isoformat(), "default": False}
    if period_name in YE_PERIODS and period and period.get("valid_to"):
        ws = year_end_windows(period["valid_from"], period["valid_to"])
        if ws:
            return {"from": ws[0][0].isoformat(), "to": ws[-1][1].isoformat(), "default": True,
                    "windows": [[a.isoformat(), b.isoformat()] for a, b in ws]}
    return None


_BLOCK_TAGS = ("p", "li", "div", "dd", "dt", "blockquote", "section", "article")
_END = re.compile(r"[。．.！!？?]$")


def _same_block(a, b):
    """2 つの行 (content.Line) が同じ塊の続きか: PDF の見た目の行 (同じページの同じ表の注・ページの文)、同じ親の続けて並んだ
    <p>・<li>・<div> (要素の無い行は、同じ見出しの下の行)。見出し・表のマスは塊にしない"""
    if a.tag == "pdf" or b.tag == "pdf":
        return a.tag == b.tag and getattr(a, "page", None) == getattr(b, "page", None) and \
            getattr(a, "owner", None) == getattr(b, "owner", None)
    if a.is_heading or b.is_heading or a.in_table or b.in_table:
        return False
    if a.element is None or b.element is None:
        return a.element is None and b.element is None and list(a.heading_path) == list(b.heading_path)
    return a.element.parent is b.element.parent and a.element.name in _BLOCK_TAGS and b.element.name in _BLOCK_TAGS


def _continued(s, sents):
    """この文のすぐ後ろの文が、この文の続きか (行の折り返し・<p> や <li> や PDF の見た目の行で切れた文):
    同じ行なら、続きの言葉 (「できません」「しない」「いたしかねます」…) で始まるか、この文が「。」で終わらずに逆接・添えの言葉
    (「が、」「けれど」「ただし」「なお」…。content.CONT_LEAD) で始まるか。同じ塊の次の行 (_same_block) なら、この文が「。」で
    終わらず、次の文が続きの言葉か逆接・添えの言葉で始まるか。
    「祝日も収集します\nできない地区もあります」「<p>祝日も収集します</p><p>が、東町は翌日になります。</p>」の前の文を、それだけで読んで使わない"""
    i = next((k for k, x in enumerate(sents) if x is s), None)
    x = next((x for x in sents[(i + 1) if i is not None else len(sents):] if not x.parenthetical), None)
    if x is None:
        return False
    open_end = not _END.search(s.text.strip())
    if x.line is not s.line and not (open_end and _same_block(s.line, x.line)):
        return False
    xt = content.squash(x.text)
    if content._CONTINUATION.match(xt):
        return True
    return open_end and bool(content.CONT_LEAD.match(xt))


def exceptions_from_sentences(sents, period, cats, source_id, start=1):
    """本文の範囲の文 (content.Sentence) → (例外のリスト, 使った文のリスト, 年末年始の材料のうち使えなかったもの)。
    cats は {品目の id: {"coarse": [...]}}。方針の値 (祝日・年末年始・休み・第5週) には policy_eligible の文だけを使い
    (開庁時間・持ち込みの文を外す。§3.2 の 4)、deferred・caveat は change_sentences の見分け方 (§2.6) で決める。
    文を使う (used に入れ、値の例外を作る) のは関門 (consume.take) を通ったときだけ: 読み取りが値を返し、文の全体が閉じた見本に
    当たったとき。例外の template にその見本の名前を書く。読み取りが値を返したのに使えなかった文 (見本の外・品目の決まらない文
    (AMBIGUOUS)・行の折り返しの続きのある文) は consume.left で印をつけ、但し書きの印が無くても但し書きにする。
    deferred・caveat の例外は (止める・注意の向きなので) いつも作るが、文を使うのは見本 (content.change_template) に当たるときだけ"""
    out, used, loose = [], [], []
    n = start
    def eid():
        nonlocal n
        n += 1
        return f"e{n - 1}"
    lasts, firsts = [], []
    for s in sents:
        text = s.text
        ch = content.classify_change(text)
        span = s.span(source_id)
        if ch:
            tag = consume.take("change", content.change_template(text), text)
            bscope = {"category_ids": block_scope(text, cats, s.heading_path)}
            mine = []
            if ch["kind"] == "deferred":
                for p in ch["periods"]:
                    w = _window_for(p, ch["dates"], period)
                    mine.append({"exception_id": eid(), "kind": "deferred", "period_name": p, "window": w,
                                 "overrides_printed": True, "scope": dict(bscope),
                                 "test": {"period": p, "marker": ch["markers"][0]}, "text": text, "span": span})
            else:
                mine.append({"exception_id": eid(), "kind": "caveat", "window": None, "scope": bscope,
                             "test": {"period": None, "marker": ch["markers"][0]}, "text": text, "span": span})
            out += mine
            if consume.mark(s, tag):
                for e in mine:
                    e["template"] = tag
                used.append(s)
            else:
                consume.left(s, "change")     # 見本の外の deferred・caveat (「8月14日は休み、ほかの日も変更になる場合があります」)
            continue
        if not content.policy_eligible(s):
            continue
        hp = rules.holiday_policy(text)
        fw = rules.fifth_week(text)
        lh = rules.long_holiday(text)
        parts = rules.year_end_parts(text, period)
        if not (hp or fw or lh or parts):
            continue                    # 読み取りが値を返さない文 (但し書きの印・日付があれば scan_qualifiers が止める)
        vs = value_scope(text, cats, s.heading_path, s.line.text)
        if vs is AMBIGUOUS:
            consume.left(s, "scope")    # どの品目の値か決まらない → 使わない (印が無くても但し書きとして残り、当たりうる品目を止める)
            continue
        if _continued(s, sents):
            consume.left(s, "continued")
            continue
        scope = {"category_ids": vs}
        if hp:
            tag = consume.take("holiday_policy", hp["template"], text)
            if not consume.mark(s, tag):
                consume.left(s, "holiday_policy")
                continue
            e = {"exception_id": eid(), "kind": "holiday_policy", "scope": scope, "value": hp["value"], "covers": hp["covers"],
                 "text": text, "span": span, "template": tag}
            if hp.get("shift"):
                e["shift"] = hp["shift"]
            if hp.get("except_year_end"):
                e["extra"] = {"except_year_end": True}
            out.append(e)
            used.append(s)
            continue
        if fw:
            tag = consume.take("fifth_week", fw["template"], text)
            if (vs == "*" and len(cats) > 1) or not consume.mark(s, tag):
                consume.left(s, "fifth_week")   # 品目ごとの扱い (§3.7「どの品目に当たるかは span の節で決める」) が決まらない
                continue
            out.append({"exception_id": eid(), "kind": "fifth_week", "value": "none", "scope": scope, "text": text, "span": span,
                        "template": tag})
            used.append(s)
            continue
        if lh:
            tag = consume.take("long_holiday", lh["template"], text)
            if not consume.mark(s, tag):
                consume.left(s, "long_holiday")
                continue
            out.append({"exception_id": eid(), "kind": "long_holiday", "value": "collected", "scope": scope, "text": text,
                        "span": span, "template": tag})
            used.append(s)
            continue
        if any(p.get("problems") for p in parts):
            loose.append({"text": text, "problems": sorted({x for p in parts for x in p.get("problems", [])}), "span": span})
            consume.left(s, "year_end_problems")
            continue
        _, spans, t = rules.year_end_parts(text, period, with_spans=True)
        tag = consume.take("year_end", rules.year_end_template(text), text)
        if not tag or rules.leftover(t, spans):        # 文の全体を読んだときだけ (本文の文もマスと同じく読み残しを見る)
            consume.left(s, "year_end")
            continue
        parts = pair_in_text(parts)
        pending = False
        for p in parts:
            if p["treatment"] == "closure" and p.get("last") and not p.get("first"):
                lasts.append((p, s, scope, tag))
                pending = True
            elif p["treatment"] == "closure" and p.get("first") and not p.get("last"):
                firsts.append((p, s, scope, tag))
                pending = True
            else:
                for e in parts_to_exceptions([p], text, span, scope, period, eid):
                    out.append(dict(e, template=tag))
        if not pending:
            consume.mark(s, tag)
            used.append(s)
    # 「年末は…まで」と「年始は…から」が別の文 (金沢の市全体) → 同じ見出しの下の組を 1 つの休みの期間に。組にするのは、1 つの文の
    # 見本 (closure_pair) と同じく、最後の日が最初の日より前で、間が 40 日までのときだけ (「年始は3月1日から」と組にしない)
    for p, s, scope, tag in lasts:
        mate = next((f for f in firsts if list(f[1].heading_path) == list(s.heading_path) and f[2] == scope and
                     closure_ok(p["last"], f[0]["first"])), None)
        if mate:
            firsts.remove(mate)
            fp, fs, _, ftag = mate
            text = s.text + "\n" + fs.text
            out.append({"exception_id": eid(), "kind": "year_end", "scope": scope, "treatment": "closure", "last": p["last"],
                        "first": fp["first"], "others": None, "text": text, "template": tag,
                        "span": {"source_id": source_id, "selector": s.selector, "text": text, "heading_path": list(s.heading_path)}})
            for x, xtag in ((s, tag), (fs, ftag)):
                consume.mark(x, xtag)
                if x not in used:
                    used.append(x)
        else:
            loose.append({"text": s.text, "problems": ["年始の最初の日が無い"], "span": s.span(source_id)})
            consume.left(s, "year_end_unpaired")
    for p, s, scope, tag in firsts:
        loose.append({"text": s.text, "problems": ["年末の最後の日が無い"], "span": s.span(source_id)})
        consume.left(s, "year_end_unpaired")
    return out, used, loose


def closure_ok(last, first):
    """年末の最後の日と年始の最初の日を 1 つの休みの期間にしてよいか (rules の closure_order と同じ: 前後が逆でなく、間が 40 日まで)"""
    if not last or not first:
        return False
    a, b = _d(last), _d(first)
    return a < b and (b - a).days <= 40


def pair_in_text(parts):
    """1 つの文・マスの中の「年末は…まで」(last だけ) と「年始は…から」(first だけ) を 1 つの休みの期間にする (金沢の燃やすごみのマス)"""
    lasts = [p for p in parts if p["treatment"] == "closure" and p.get("last") and not p.get("first")]
    firsts = [p for p in parts if p["treatment"] == "closure" and p.get("first") and not p.get("last")]
    if len(lasts) == 1 and len(firsts) == 1 and closure_ok(lasts[0]["last"], firsts[0]["first"]):
        rest = [p for p in parts if p is not lasts[0] and p is not firsts[0]]
        return rest + [{"treatment": "closure", "last": lasts[0]["last"], "first": firsts[0]["first"], "problems": []}]
    return parts


def parts_to_exceptions(parts, text, span, scope, period, eid):
    """年末年始の材料 (rules.year_end_parts) → 例外のリスト。窓 W の外の休みは cancelled (1 日ずつ)"""
    out = []
    for p in parts:
        if p["treatment"] == "closure" and p.get("off_from") and not _in_any_w(p["off_from"], period) and \
                not _in_any_w(p["off_to"], period):
            for x in _days(p["off_from"], p["off_to"]):          # 年末年始の外の休みの期間 (赤平の 5/3〜5/5) は 1 日ずつ cancelled
                out.append({"exception_id": eid(), "kind": "cancelled", "scope": scope, "date": x.isoformat(), "text": text,
                            "span": span})
        elif p["treatment"] == "closure" and p.get("last") and p.get("first"):
            e = {"exception_id": eid(), "kind": "year_end", "scope": scope, "treatment": "closure", "last": p["last"],
                 "first": p["first"], "others": None, "text": text, "span": span}
            if p.get("off_from"):
                e["extra"] = {"off_from": p["off_from"], "off_to": p["off_to"]}
            out.append(e)
        elif p["treatment"] == "makeup":
            out.append({"exception_id": eid(), "kind": "year_end", "scope": scope, "treatment": "makeup",
                        "removed": p["removed"], "to": p["to"], "others": None, "text": text, "span": span})
        elif p["treatment"] == "skip":
            in_w, out_w = [], []
            for x in p["removed_dates"]:
                (in_w if _in_any_w(x, period) else out_w).append(x)
            if in_w:
                out.append({"exception_id": eid(), "kind": "year_end", "scope": scope, "treatment": "skip",
                            "dates": in_w, "others": None, "text": text, "span": span})
            for x in out_w:
                out.append({"exception_id": eid(), "kind": "cancelled", "scope": scope, "date": x, "text": text, "span": span})
    return out


def cell_exceptions(text, period, scope, span, start=1, prefix="e"):
    """規則のマスに書かれた年末年始・休みの文 (金沢「年末は12月28日（月曜日）まで 年始は1月4日（月曜日）から」、
    羽咋「12/29(火)最終で 1/5(火)から」) → (例外のリスト, 使えなかった文の問題)。scope はそのマスの地区と品目"""
    parts = rules.year_end_parts(text, period)
    if not parts:
        return [], []
    probs = sorted({x for p in parts for x in p.get("problems", [])})
    if probs:
        return [], probs
    parts = pair_in_text(parts)
    if any(p["treatment"] == "closure" and not (p.get("last") and p.get("first")) and not p.get("off_from") for p in parts):
        return [], ["年末の最後の日か年始の最初の日が無い"]
    n = [start]

    def eid():
        n[0] += 1
        return f"{prefix}{n[0] - 1}"
    return parts_to_exceptions(parts, text, span, scope, period, eid), []


def rest_qualifier_id(text):
    """マスの読み残しの但し書きの id (content.scan_qualifiers と同じ作り方)"""
    return "q" + content.sha256_text(content.squash(text))[:10]


_CELL_HOLIDAY_MAX = 30


def _cell_names_other(text, scope, cats=None):
    """マスの文を、そのマスの品目 (scope) に当ててはいけないか。当ててよいのは、文の品目 (sentence_scope。閉じた見本) が
    主題なし (「（祝日の収集は休み）」「年末は…まで 年始は…から」) か、そのマスの品目そのもの (燃やすごみのマスの
    「（燃やすごみは祝日の収集は休み）」) のときだけ。ほかの品目 (「（ペットボトルは祝日休み）」「（危険ごみは祝日休み）」)、
    「ごみ」全体 (マスの外の話)、地区・コース、見本に無い言い回しは当てない。cats (資料の品目) が無ければ、主題なしの文だけ"""
    own = (scope or {}).get("category_ids")
    v = sentence_scope(text, cats or {})
    if v == "":
        return False
    return not (isinstance(v, list) and own not in (None, "*") and set(v) == set(own))


def cell_notes(rest, period, scope, span, start=1, prefix="e", cats=None, holiday=True):
    """規則のマスの、規則のあとの文 (rules.Parsed.rest。「/」で切れている) → (例外のリスト, 使われなかった文のリスト)。
    読むのは年末年始・休みの文 (金沢「年末は…まで 年始は…から」) と祝日の扱い (§3.8「マスの中の「（祝日の収集は休み）」は
    その品目の holiday_policy」) だけで、どれもそのマスの地区・品目 (scope) だけに当てる。使うのは関門 (consume.take) を通った
    切れ端だけ (文の全体が見本に当たり、読み残しが無い。動詞の無い「（祝日も収集）」はかっこで包んだ注だけ)。例外の template に見本の名前。
    ほかの文 (「※第5週は休み」「（月2回）」)、主題がそのマスの品目でも主題なしでもない文 (_cell_names_other。cats は資料の品目。
    年末年始の文も同じ) は、使われなかった但し書きとして返す (呼ぶ側がその系列を止める)。
    holiday=False (日付の一覧の後ろの注) なら祝日の扱いの文も使わない (一覧は日付そのもので、祝日の扱いを当てる規則が無い)"""
    out, left = [], []
    n = start
    for piece in [x.strip() for x in (rest or "").split("/") if x.strip()]:
        if _cell_names_other(piece, scope, cats):
            left.append(piece)
            continue
        parts, spans, t = rules.year_end_parts(piece, period, with_spans=True)
        if parts:
            tag = consume.take("cell_year_end", rules.year_end_template(piece), piece)
            ex, probs = cell_exceptions(piece, period, scope, dict(span, text=piece), start=n, prefix=prefix)
            if tag and ex and not probs and not rules.leftover(t, spans):
                n += len(ex)
                out += [dict(e, template=tag) for e in ex]
            else:
                left.append(piece)
            continue
        hp = holiday and rules.holiday_policy(piece, noverb=True)
        if hp and not content.classify_change(piece) and len(content.squash(piece)) <= _CELL_HOLIDAY_MAX and \
                not re.search(r"\d{1,2}[月/]\d|年末|年始|正月", piece):
            tag = consume.take("cell_holiday", hp["template"], piece)
            if tag:
                e = {"exception_id": f"{prefix}{n}", "kind": "holiday_policy", "scope": scope, "value": hp["value"], "covers": hp["covers"],
                     "text": piece, "span": dict(span, text=piece), "template": tag}
                if hp.get("shift"):
                    e["shift"] = hp["shift"]
                n += 1
                out.append(e)
                continue
        left.append(piece)
    return out, left


def _in_any_w(x, period):
    if not period or not period.get("valid_to"):
        return False
    return any(a <= _d(x) <= b for a, b in year_end_windows(period["valid_from"], period["valid_to"]))


def consume_parentheticals(sents, used):
    """使った文のかっこ書きを、使ったものに数える → 増やした used。数えるのは関門 (consume.take の種類 paren) を通ったものだけ:
    - 使った文の中のかっこ書き (文の span の中。文の見本がかっこ書きごと読んだ。「祝日も収集します（年末年始を除く）」)
    - 文の外のかっこ書きは、それ自体が見本 (年末年始を除く・休み: rules._EX_TAIL) に当たり、同じ行のすぐ前の文が「祝日も収集します」の
      見本で使った文のときだけ (三鷹「祝日も収集します。（年末年始を除く）」)。
    ほかのかっこ書き (「（お盆は休み）」「（第5月曜日は休み）」「（燃やすごみは収集します）」) は使わない (但し書きとして止める)"""
    out = list(used)
    for i, s in enumerate(sents):
        if any(s is u for u in out):
            continue
        if s.parenthetical:
            host = next((u for u in used if u.line is s.line and not u.parenthetical and u.start <= s.start and s.end <= u.end), None)
            if host is not None and consume.mark(s, consume.take("paren", "inside_consumed_sentence", s.text)):
                out.append(s)
            continue
        prev = next((x for x in reversed(sents[:i]) if x.line is s.line and not x.parenthetical), None)
        if prev is None or not any(prev is u for u in used):
            continue
        if not rules._EX_TAIL.fullmatch(re.sub(r"。$", "", content.squash(s.text))):
            continue
        ptag = getattr(prev, "consumed", "") or ""
        if re.fullmatch(r"holiday_policy:collected(?:_double_negative)?", ptag) and \
                consume.mark(s, consume.take("paren", "ye_excluded_after_collected", s.text)):
            out.append(s)
    return out


# ---- 系列 ----
def _ranges_from_days(days):
    out = []
    for d in sorted(days):
        if out and out[-1][1] + dt.timedelta(days=1) == d:
            out[-1][1] = d
        else:
            out.append([d, d])
    return out


class Unknown:
    """unknown_ranges を日ごとに集める (同じ日に 2 つの理由があれば、先に入れたもの)"""

    def __init__(self):
        self.days = {}

    def add(self, a, b, reason, note=None, exception_id=None):
        for d in _days(a, b):
            self.days.setdefault(d, (reason, note, exception_id))

    def __contains__(self, d):
        return d in self.days

    def ranges(self):
        out = []
        for d in sorted(self.days):
            r, note, eid = self.days[d]
            if out and out[-1]["_to"] + dt.timedelta(days=1) == d and (out[-1]["reason"], out[-1].get("note"),
                                                                         out[-1].get("exception_id")) == (r, note, eid):
                out[-1]["_to"] = d
                continue
            rec = {"_from": d, "_to": d, "reason": r}
            if note:
                rec["note"] = note
            if eid:
                rec["exception_id"] = eid
            out.append(rec)
        return [{"from": x.pop("_from").isoformat(), "to": x.pop("_to").isoformat(), **x} for x in out]


def _shift(d, to, base_weekday=None):
    if to == "next_day":
        return d + dt.timedelta(days=1)
    if to == "next_weekday":
        x = d + dt.timedelta(days=1)
        while x.weekday() >= 5 or holidays.holiday(x):
            x += dt.timedelta(days=1)
        return x
    if to == "next_same_weekday":
        return d + dt.timedelta(days=7)
    if to == "prev_weekday":
        x = d - dt.timedelta(days=1)
        while x.weekday() >= 5 or holidays.holiday(x):
            x -= dt.timedelta(days=1)
        return x
    return None


def conditions(spec):
    """系列の材料 → (blocked_by のリスト, 当てる例外 {種類: [...]})。日付は作らない (expand_series が作る)"""
    blocked = []
    per = spec["period"]
    excs = [e for e in spec.get("exceptions", []) if in_scope(e, spec["category_id"], spec["district_id"], per.get("period_id"))]
    by = {}
    for e in excs:
        by.setdefault(e["kind"], []).append(e)
    origin = spec["origin"]
    parsed = (spec.get("rule") or {}).get("parsed")
    # 2. 期間と版
    if not per.get("valid_to") or (per.get("basis") or {}).get("kind") == "undated":
        blocked.append("period_unknown")
    elif not (per.get("year_evidence") or {}).get("ok"):
        blocked.append("version_unproven")
    if origin == "expanded":
        # 1. 規則
        if parsed is None or parsed.status in ("unparsed", "multiple", "partial_subject", "inline") or not parsed.irs:
            blocked.append("rule_unparsed")
        elif parsed.status == "ambiguous":
            blocked.append("rule_ambiguous")
        elif parsed.status in ("none", "on_demand"):
            blocked.append("rule_unparsed")
        elif rules.is_additive(parsed.ir):
            blocked.append("rule_unparsed")            # 足す規則 (「…でも出せる」) は、それだけでは系列の規則として広げない
        elif rules.needs_anchor(parsed.ir):
            blocked.append("no_anchor")
        # 3. 祝日
        hps = by.get("holiday_policy", [])
        if not hps or len({(h["value"], tuple(h.get("covers") or ())) for h in hps}) > 1:
            blocked.append("holiday_policy_missing")
        elif hps[0]["value"] == "moved" and not hps[0].get("shift"):
            blocked.append("holiday_shift_target_missing")
        elif hps[0]["value"] == "unknown":
            blocked.append("holiday_policy_missing")
        # 大型連休も収集する (long_holiday) と、祝日は休み・ずらす (holiday_policy) が同じ系列に当たれば、連休の祝日の扱いが
        # 決まらない (どの日が「大型連休」かを文は言わない。どちらかに倒して日を作らない)
        if by.get("long_holiday") and any(h["value"] in ("not_collected", "moved") for h in hps):
            blocked.append("holiday_policy_conflict")
        # 4 (A). 年末年始の文
        ye = by.get("year_end", [])
        dfr_ye = [e for e in by.get("deferred", []) if e.get("period_name") in YE_PERIODS]
        if not ye and not dfr_ye:
            blocked.append("year_end_missing")
        # 同じ系列に当たる休みの期間 (closure) が 2 つ以上あって違えば、どちらが正しいか決まらない (和を取って両方の日を消さない)
        if len({(e["last"], e["first"]) for e in ye if e.get("treatment") == "closure" and e.get("last") and e.get("first")}) > 1:
            blocked.append("year_end_conflict")
        # 5. 印字の日付の資料
        if spec.get("printed_pending"):
            blocked.append("pending_printed")
    elif origin in ("printed", "listed") and not spec.get("printed_dates"):
        blocked.append("pending_printed")
    # deferred (どの出どころにも)
    for e in by.get("deferred", []):
        if not (e.get("window") or {}).get("from"):
            r = f"deferred_undated:{e.get('period_name')}"
            if r not in blocked:
                blocked.append(r)
    # 6. 但し書きとしるし。規則のマスの読み残し (rules.Parsed.rest) は、呼ぶ側が年末年始・祝日の文として使ったと言わない限り
    # (rest_consumed)、使われなかった但し書き (「火曜日・金曜日 ※第5週は休み」「水曜日（月2回）」を毎週と読んで終わりにしない)
    if parsed is not None and parsed.rest and not spec.get("rest_consumed"):
        r = f"unconsumed_qualifier:{rest_qualifier_id(parsed.rest)}"
        if r not in blocked:
            blocked.append(r)
    for q in spec.get("qualifiers", []):
        if not q.get("months"):
            r = f"unconsumed_qualifier:{q['qualifier_id']}"
            if r not in blocked:
                blocked.append(r)
    for k in spec.get("markup_unhandled", []):
        r = f"markup_unhandled:{k}"
        if r not in blocked:
            blocked.append(r)
    # 読みの状態: answerable にしてよいのは agreed・agreed_weak・adjudicated だけ (§2.8)。記録が無い・状態の無いものも答えない
    st = (spec.get("provenance") or {}).get("status")
    for item in spec.get("disputes", []):
        r = f"disputed:{item}"
        if r not in blocked:
            blocked.append(r)
    if st not in OK_STATUS:
        if st in ("disputed", "both_unsure"):              # both_unsure も裁定へ (§3.11)
            if not spec.get("disputes"):
                blocked.append("disputed:unspecified")
        elif "single_reading" not in blocked:              # single・記録が無い・知らない状態
            blocked.append("single_reading")
    for b in spec.get("extra_blocked", []):
        if b not in blocked:
            blocked.append(b)
    return blocked, by


def effective_ir(ir, by):
    """当てる例外に第5週の扱い (fifth_week: none。新宿「5番目の曜日」) があれば、規則の IR に except_nth: [5] を足したもの
    (§2.6・§3.7)。monthly_nth は nth から 5 を除く。months・seasonal は中の規則に。ほかの種類はそのまま (日付で 5 回目を除く)"""
    if not ir or not by.get("fifth_week"):
        return ir
    k = ir["kind"]
    if k == "weekly":
        return dict(ir, except_nth=sorted(set(ir.get("except_nth") or ()) | {5}))
    if k == "monthly_nth":
        return dict(ir, nth=[n for n in ir["nth"] if n != 5])
    if k in ("months", "seasonal"):
        return dict(ir, rule=effective_ir(ir["rule"], by))
    return ir


def base_dates(ir, period, by):
    """第5週の扱いを当てた素の日付 (休み・年末年始は当てない)"""
    eff = effective_ir(ir, by)
    base = set(rules.occurrences(eff, period["valid_from"], period["valid_to"]))
    if by.get("fifth_week"):
        base = {d for d in base if rules.nth_of_month(d) != 5}
    return base


def is_monthly(ir, by):
    """§2.5 の 4 の月ごとの規則か。第5週の扱いが当たる規則は、月の何回目かを数えるので月ごとの規則 (except_nth つきの weekly と同じ)"""
    return bool(by.get("fifth_week")) or rules.is_monthly(effective_ir(ir, by))


def expand_dates(ir, period, by, monthly=None):
    """規則の IR と当てる例外 → (日付の集まり, Unknown, 使った例外の id)。条件 3・4 の決まりで休み・年末年始を当てる"""
    vf, vt = _d(period["valid_from"]), _d(period["valid_to"])
    monthly = is_monthly(ir, by) if monthly is None else monthly
    base = base_dates(ir, period, by)
    unk = Unknown()
    used = []
    added = set()
    ye = by.get("year_end", [])
    closures = [(e["last"], e["first"]) for e in ye if e.get("treatment") == "closure" and e.get("last") and e.get("first")]
    wins = year_end_windows(vf, vt, closures)
    # deferred: 名前のある期間は、どの規則でも不明 (印字より強い)
    for e in by.get("deferred", []):
        w = e.get("window") or {}
        if w.get("from"):
            for a, b in ([(_d(x), _d(y)) for x, y in w["windows"]] if w.get("windows") else [(_d(w["from"]), _d(w["to"]))]):
                a, b = max(a, vf), min(b, vt)
                if a <= b:
                    unk.add(a, b, "deferred", e.get("period_name"), e["exception_id"])
            used.append(e["exception_id"])
    # 4. 年末年始
    if not monthly:
        for e in ye:
            t = e.get("treatment")
            if t == "closure" and e.get("last") and e.get("first"):
                base = {d for d in base if not (_d(e["last"]) < d < _d(e["first"]))}
            elif t == "skip":
                base -= {_d(x) for x in e.get("dates") or []}
            elif t == "makeup" and e.get("removed") and e.get("to"):
                base.discard(_d(e["removed"]))
                added.add(_d(e["to"]))
            elif t == "dates":
                for a, b in wins:
                    base = {d for d in base if not a <= d <= b} | {_d(x) for x in e.get("dates") or [] if a <= _d(x) <= b}
            used.append(e["exception_id"])
    else:
        for a, b in wins:
            listed = [e for e in ye if e.get("treatment") == "dates" and e.get("dates")]
            stated = [e for e in ye if e.get("others") == "unchanged"]
            if listed:
                for e in listed:
                    base = {d for d in base if not a <= d <= b} | {_d(x) for x in e["dates"] if a <= _d(x) <= b}
                    used.append(e["exception_id"])
            elif stated:
                # (b) 休みに当たる回の扱いと、ほかの回の数え方 (ずらさない) の両方を文が言っている
                for e in stated:
                    if e.get("treatment") == "closure":
                        base = {d for d in base if not (_d(e["last"]) < d < _d(e["first"]))}
                    elif e.get("treatment") == "skip":
                        base -= {_d(x) for x in e.get("dates") or []}
                    elif e.get("treatment") == "makeup":
                        base.discard(_d(e["removed"]))
                        added.add(_d(e["to"]))
                    used.append(e["exception_id"])
            else:
                unk.add(a, b, "year_end_window", "月ごとの規則の年末年始の窓 (回の数え方が文に無い)")
    # 3. 祝日と、4 (D) 年末年始の外の休み
    hp = (by.get("holiday_policy") or [None])[0]
    closure_months = set()
    in_w = lambda d: any(a <= d <= b for a, b in wins)
    for d in sorted(base):
        if in_w(d) and monthly:
            continue
        h = holidays.holiday(d)
        if not h or hp is None:
            continue
        covered = holidays.is_covered(d, hp.get("covers"))
        if not covered:
            unk.add(d, d, "holiday_kind_not_covered", h["name"], hp["exception_id"])
            continue
        if hp["value"] == "not_collected":
            base.discard(d)
            if monthly:
                closure_months.add((d.year, d.month))
        elif hp["value"] == "moved":
            base.discard(d)
            t = _shift(d, (hp.get("shift") or {}).get("to"))
            if t:
                added.add(t)
            if monthly:
                closure_months.add((d.year, d.month))
    if hp:
        used.append(hp["exception_id"])
    for e in by.get("cancelled", []):
        x = _d(e["date"])
        if x in base:
            base.discard(x)
            if monthly:
                closure_months.add((x.year, x.month))
        used.append(e["exception_id"])
    for e in by.get("moved", []):
        x = _d(e.get("date"))
        if x in base:
            base.discard(x)
            if e.get("to"):
                added.add(_d(e["to"]))
            if monthly:
                closure_months.add((x.year, x.month))
        used.append(e["exception_id"])
    for (y, m) in sorted(closure_months):
        a, b = _month_range(y, m)
        unk.add(max(a, vf), min(b, vt), "closure_month", "休み・祝日で月ごとの規則の回が消える月 (ほかの回の数え方が文に無い)")
    for e in by.get("long_holiday", []):
        used.append(e["exception_id"])
    for e in by.get("fifth_week", []):
        used.append(e["exception_id"])
    dates = {d for d in (base | added) if vf <= d <= vt and d not in unk}
    return dates, unk, list(dict.fromkeys(used))


def expand_series(spec):
    """系列の材料 → スキーマ v0.2 の schedules の 1 件 (dict)。spec:
      district_id, category_id, period (period_id・valid_from・valid_to・basis・year_evidence)、origin (expanded / printed / listed)、
      rule ({"text", "span", "parsed": rules.Parsed})、printed_dates、printed_pending、exceptions (すべて。ここで scope を見る)、
      qualifiers (当たりうる使われなかった但し書き [{"qualifier_id", "months"}])、markup_unhandled、provenance、disputes、gate_unit、ref"""
    blocked, by = conditions(spec)
    per = spec["period"]
    parsed = (spec.get("rule") or {}).get("parsed")
    origin = spec["origin"]
    unk = Unknown()
    dates, used, mism = set(), [], []
    caveats = [e["exception_id"] for e in by.get("caveat", []) + by.get("contingency", [])]
    queue, ex_queue = [], []
    can_expand = origin == "expanded" and not blocked
    if can_expand:
        dates, unk, used = expand_dates(parsed.ir, per, by)
    elif origin in ("printed", "listed") and spec.get("printed_dates") and per.get("valid_to") and \
            not any(b in blocked for b in ("period_unknown",)):
        dates = {_d(x) for x in spec["printed_dates"]}
        for e in by.get("deferred", []):
            w = e.get("window") or {}
            if w.get("from"):
                for a, b in ([(_d(x), _d(y)) for x, y in w["windows"]] if w.get("windows") else [(_d(w["from"]), _d(w["to"]))]):
                    a, b = max(a, _d(per["valid_from"])), min(b, _d(per["valid_to"]))
                    if a <= b:
                        unk.add(a, b, "deferred", e.get("period_name"), e["exception_id"])
        if parsed is not None and parsed.ir and parsed.status == "ok" and not rules.is_additive(parsed.ir):
            mism, queue = compare_printed(parsed.ir, sorted(dates), per, by, spec.get("item_prefix", "") +
                                          f":{spec['district_id']}:{spec['category_id']}")
            for q in queue:
                blocked.append(f"disputed:{q['item_id']}")
        # 確かめ (§4.1 の 5): 資料が休みと書いた日 (休みの期間の中・skip・cancelled・振り替えの元の日) が印字・一覧の日付にあれば、
        # どちらが正しいか決まらない → 例外ごとに裁定へ (印字を正として例外を捨てない・例外を当てて印字を消さない)
        # 祝日は収集しない・ずらすと書いた資料 (ページ・マスの祝日の扱い) も、一覧・印字の祝日の日付とぶつかれば裁定へ
        clash = {}
        off = explained_days({"year_end": by.get("year_end", []), "cancelled": by.get("cancelled", []),
                              "holiday_policy": by.get("holiday_policy", [])}, per, all_hp=True)
        for d in sorted(dates):
            for eid in off.get(d, []):
                clash.setdefault(eid, []).append(d.isoformat())
        for eid, ds in clash.items():
            item = {"item_id": (spec.get("item_prefix", "") + f":{spec['district_id']}:{spec['category_id']}:exception_vs_printed:{eid}")
                    .lstrip(":"), "kind": "policy", "status": "open", "exception_id": eid, "dates": ds,
                    "note": "資料が休みと書いた日が、印字・一覧の日付にある (§4.1 の 5)"}
            ex_queue.append(item)
            blocked.append(f"disputed:{item['item_id']}")
        dates = {d for d in dates if d not in unk}
    # 月を名指しした但し書き → その月だけ不明 (§2.5 の 6)
    if per.get("valid_to"):
        vf, vt = _d(per["valid_from"]), _d(per["valid_to"])
        for q in spec.get("qualifiers", []):
            for mth in q.get("months") or []:
                for y in range(vf.year, vt.year + 1):
                    a, b = _month_range(y, mth)
                    a, b = max(a, vf), min(b, vt)
                    if a <= b:
                        unk.add(a, b, "unconsumed_qualifier", q["qualifier_id"])
        dates = {d for d in dates if d not in unk}
    answerable = not blocked
    rule = None
    if spec.get("rule"):
        rule = {"text": spec["rule"].get("text", ""), "ir": parsed.ir if parsed is not None and parsed.status in ("ok", "ambiguous")
                else None}
        if spec["rule"].get("span"):
            rule["span"] = spec["rule"]["span"]
        if answerable and origin == "expanded" and rule["ir"]:
            comp = rules.compress(effective_ir(rule["ir"], by), sorted(dates), per["valid_from"], per["valid_to"])
            if comp:
                rule.update(comp)
    s = {"district_id": spec["district_id"], "category_id": spec["category_id"], "period_id": per["period_id"],
         "dates_origin": origin if (answerable or dates) else None,
         "rule": rule, "dates": sorted(d.isoformat() for d in dates) if answerable else [],
         "unknown_ranges": unk.ranges() if answerable else [],
         "answerable": answerable, "blocked_by": blocked,
         "gate": {"unit": "expanded" if origin == "expanded" else spec.get("gate_unit", "B"), "open": False},
         "published": False, "caveats": caveats, "expanded_with": used if origin == "expanded" else [],
         "rule_mismatches": mism, "provenance": spec.get("provenance") or {"readers": [], "status": "single"}}
    if spec.get("ref"):
        s["ref"] = spec["ref"]
    if spec.get("extra"):
        s["extra"] = spec["extra"]
    if queue:
        s.setdefault("extra", {})["rule_vs_printed"] = queue
    if ex_queue:
        s["extra"] = dict(s.get("extra") or {}, exception_vs_printed=ex_queue)     # 材料 (spec) の extra は書き換えない
    return s


def explained_days(by, period, all_hp=False):
    """例外の原文で説明のつく「規則にあって印字に無い」日 (休みの期間の中・skip・cancelled・祝日の休み)。
    all_hp (印字・一覧との突き合わせ) なら、祝日の扱いの例外のどれもが休み・ずらすと言う祝日を入れる (1 つ目だけにしない)"""
    out = {}
    for e in by.get("year_end", []):
        if e.get("treatment") == "closure" and e.get("last") and e.get("first"):
            for d in _days(_d(e["last"]) + dt.timedelta(days=1), _d(e["first"]) - dt.timedelta(days=1)):
                out.setdefault(d, []).append(e["exception_id"])
        elif e.get("treatment") == "skip":
            for x in e.get("dates") or []:
                out.setdefault(_d(x), []).append(e["exception_id"])
        elif e.get("treatment") == "makeup" and e.get("removed"):
            out.setdefault(_d(e["removed"]), []).append(e["exception_id"])
    for e in by.get("cancelled", []):
        out.setdefault(_d(e["date"]), []).append(e["exception_id"])
    hps = (by.get("holiday_policy") or [])[:None if all_hp else 1]
    for hp in hps:
        if hp.get("value") in ("not_collected", "moved") and period.get("valid_to"):
            for d in _days(period["valid_from"], period["valid_to"]):
                h = holidays.holiday(d)
                if h and holidays.is_covered(d, hp.get("covers")):
                    out.setdefault(d, []).append(hp["exception_id"])
    return out


def compare_printed(ir, printed_dates, period, by, item_prefix):
    """印字と規則を (地区, 品目, 月) ごとに全部の月で比べる → (rule_mismatches, 裁定の待ち行列)。
    その月の違う日がすべて例外の原文で説明がつくとき (印字にだけある日が無く、規則にだけある日がどれも休みの期間・skip・
    cancelled・祝日の休みに入る) だけ rule_mismatches に入れ、ほかは rule_vs_printed の裁定へ"""
    vf, vt = _d(period["valid_from"]), _d(period["valid_to"])
    rd = base_dates(ir, period, by)                        # 第5週の扱いを当てた規則の日 (当てないと 5 回目の日がいつも違いに出る)
    pd = {_d(x) for x in printed_dates}
    ex = explained_days(by, period)
    mism, queue = [], []
    y, m = vf.year, vf.month
    while dt.date(y, m, 1) <= vt:
        a, b = _month_range(y, m)
        r_m = {d for d in rd if a <= d <= b}
        p_m = {d for d in pd if a <= d <= b}
        if r_m != p_m:
            only_r, only_p = r_m - p_m, p_m - r_m
            month = f"{y:04d}-{m:02d}"
            if not only_p and only_r and all(d in ex for d in only_r):
                mism.append({"month": month, "rule_dates": sorted(x.isoformat() for x in r_m),
                             "printed_dates": sorted(x.isoformat() for x in p_m),
                             "explained_by": sorted({e for d in only_r for e in ex[d]}),
                             "note": "規則の日が例外の原文 (休みの期間・休みの日) に入る"})
            else:
                queue.append({"item_id": f"{item_prefix}:{month}".lstrip(":"), "kind": "rule_vs_printed", "status": "open",
                              "decision": None, "proposed": "printed",
                              "readings": {"rule": sorted(x.isoformat() for x in r_m), "printed": sorted(x.isoformat() for x in p_m)},
                              "unexplained": {"rule_only": sorted(x.isoformat() for x in only_r - set(ex)),
                                              "printed_only": sorted(x.isoformat() for x in only_p)}})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return mism, queue


def main(argv=None):
    import argparse, pathlib
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="規則から系列の日付を作る (罠の再現セットの文法・広げ方の段)")
    ap.add_argument("cmd", choices=["traps"])
    ap.parse_args(argv)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "eval"))
    import traps
    return traps.main()


if __name__ == "__main__":
    sys.exit(main())
