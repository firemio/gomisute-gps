"""文字を「使った」と数える唯一の関門 (default-deny。DESIGN.md §3.8 の 5・§2.6)

文・かっこ書き・マスの注と読み残し・地区や品目の名前のマスの文字・回数の書き添えを「使った」(但し書きとして止めない) と
数えてよいのは、ここ (take) を通ったものだけ。通すのは、読み取りが値を返し、かつ文の全体が名前のある閉じた見本に
まるごと当たったときだけで、その見本の名前 ("<種類>:<見本>") を返す。呼ぶ側は例外の template に書く (どの見本で読んだかの記録)。
見本の名前が無い・閉じた並び (_registry) に無い読みは使わない: 使われなかった但し書きとして、その範囲 (決まらなければ資料全体) を止める。
読み取りが値を返したのに使えなかった文 (見本の外・品目の決まらない文 (value_scope の AMBIGUOUS)) は left で印をつけ、
但し書きの印 (QUALIFIER_MARKS) が無くても但し書きにする (content.scan_qualifiers。黙って落とさない)。

使った文 (Sentence) には consumed (見本の名前) を、使えなかった文には unconsumed_reading (わけ) を付ける。
但し書きの洗い出しから外すのは、その文そのものが使われたとき (used。文の同一性) だけ。文字が同じ・文字を含む別の文が使われていても外さない。
テストはこの take を「いつも None」に差し替えて、どの道も何も使わないこと (止める方に倒れること) を確かめる。
"""

_REG = {}
_EXTRA = {}


def register(kind, names):
    """読み取りの側の閉じた見本の並びを足す (eval/stage2.py の名前のマスの型など、ここから import しない読み取り)"""
    _EXTRA.setdefault(kind, set()).update(names)


def _registry():
    """種類 → 閉じた見本の名前の集まり (読み取りの見本の並びから作る。ここに無い名前は使わない)"""
    if not _REG:
        import content, rules
        hp = {n for n, _, _ in rules._HP_TEMPLATES}
        hp_page = {n for n in hp if not n.endswith("_noverb")}
        variants = lambda names: names | {n + "+ye_lead" for n in names} | {n + "+ye_tail" for n in names}
        _REG.update({
            "holiday_policy": variants(hp_page),                         # 本文の文 (動詞の無い「祝日も収集」は読まない)
            "cell_holiday": variants(hp),                                # マスの注 (動詞の無いものは、かっこで包んだ注だけ)
            "year_end": {n for n, _ in rules._YE_TEMPLATES},
            "cell_year_end": {n for n, _ in rules._YE_TEMPLATES},
            "long_holiday": {n for n, _ in rules._LONG_TEMPLATES},
            "fifth_week": {n for n, _ in rules._FIFTH_TEMPLATES},
            "change": content.change_templates(),
            "named_months": {n for n, _ in content.MONTH_TEMPLATES},
            "paren": {"inside_consumed_sentence", "ye_excluded_after_collected"},
            "freq": {"weekly_count", "monthly_count"},
        })
    return {**_REG, **_EXTRA}


def take(kind, template, text=None):
    """読み取りの返した見本の名前 template → 使ってよければ "<kind>:<template>"、だめなら None。
    template が空 (読み取りが値を返さない・見本に当たらない)、kind の閉じた並びに無い名前なら使わない。
    動詞の無い祝日の注 (「祝日も収集」) は、かっこで包んだマスの注 (text) だけ"""
    if not template:
        return None
    names = _registry().get(kind)
    if names is None or template not in names:
        return None
    if template.endswith("_noverb") or "_noverb+" in template:
        t = (text or "").strip()
        if kind != "cell_holiday" or len(t) < 2 or t[0] not in "(（" or t[-1] not in ")）":
            return None
    return f"{kind}:{template}"


def mark(sentence, tag):
    """文を使ったと記録する (tag は take の返したもの)。tag が無ければ何もしない → 使っていない"""
    if tag:
        sentence.consumed = tag
    return bool(tag)


def left(sentence, why):
    """読み取りが値を返したのに使えなかった文 (見本の外・品目の決まらない文) の印。scan_qualifiers がこの文を但し書きにする"""
    if not getattr(sentence, "consumed", None):
        sentence.unconsumed_reading = why


def used(sentence):
    """但し書きの洗い出しから外してよい文か: その文そのもの (Sentence) が関門を通って使われ (consumed)、使えなかった印
    (unconsumed_reading) が無いときだけ。文字の同じほかの文 (別の節・別のマス) が使われていても、この文は使ったことにならない
    (文字の比べ (squash の一致・部分の一致) で外さない)"""
    return bool(getattr(sentence, "consumed", None)) and not getattr(sentence, "unconsumed_reading", None)
