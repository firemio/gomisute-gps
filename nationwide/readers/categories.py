"""品目の辞書 (読み取り役 A。DESIGN.md §2.4)

  python nationwide/readers/categories.py "プラスチック資源\n燃やせないごみ"     # 公式の名前・大まかな分類・出し方

- official_label は印字どおり (改行は空白 1 つ、全角の空白はそのまま)。言い換えない
- coarse は japan-gomi-data の 14 の鍵と、中身の分からない資源 recyclable・その他 other。印字が「びん・缶」なら分ける
- mode は収集 (curbside)・ステーション (station)・持ち込み (dropoff)・申し込み (reservation)。日付で答えるのは curbside と station だけ。
  粗大ごみのように、持ち込みか申し込みか収集かを決める言葉が無いものは None (決まらない。系列は答えない)
"""
import re, sys, unicodedata

# 上から順に当てる (「スプレー缶」を「缶」より先に、「燃やせない」を「燃やせる」より先に)。当たった所は消して次へ
COARSE_RULES = (
    (re.compile(r"スプレー缶?|カセット(?:ボンベ|缶)|ガスボンベ"), ("spray_can",)),
    (re.compile(r"有害|乾電池|電池|蛍光(?:管|灯)|水銀|体温計"), ("hazardous",)),
    (re.compile(r"粗大"), ("oversized",)),
    (re.compile(r"不燃|燃やさない|燃えない|燃やせない|もえない|もやさない|もやせない"), ("non_burnable",)),
    (re.compile(r"生ごみ|生ゴミ|厨芥"), ("kitchen",)),
    (re.compile(r"可燃|燃やす|燃える|燃やせる|もえる|もやす|もやせる"), ("burnable",)),
    (re.compile(r"ペットボトル|(?<!カー)ペット"), ("pet_bottle",)),           # 「カーペット」はペットボトルでない
    (re.compile(r"プラ(?:スチック)?|プラマーク|容器包装|容リ"), ("plastic",)),
    (re.compile(r"(?:空き?|あき)?(?:缶|かん|カン)"), ("beverage_can",)),
    (re.compile(r"(?:空き?|あき)?(?:びん|ビン|瓶)|ガラス"), ("glass_bottle",)),
    (re.compile(r"金属|小型家電|鉄類"), ("metal",)),
    # 「紙」だけのときは、広報紙・機関紙・情報紙・会報紙・用紙・表紙 (知らせの紙) を外す (「広報紙でお知らせします」は品目を言っていない)
    (re.compile(r"古紙|新聞|雑誌|段ボール|ダンボール|雑がみ|雑紙|紙パック|紙類|(?<![報関用表])紙(?!面)"), ("paper",)),
    # 「配布」「分布」「公布」の「布」は品目でない (地名の「調布」「布施」も)。「布団」は古布でない (粗大ごみのことが多い。
    # 分類は決めず、OTHER_ITEMS で名指しとみなす)
    (re.compile(r"古布|古着|衣類|布類|(?<![配頒公散分宣流調])布(?![団施])"), ("cloth",)),
)
# 辞書の分類 (COARSE_RULES) に無い品目の言葉。分類は決めない: 文・見出しが名指しすれば、資料のどの品目か決まらない
# (「剪定枝は祝日も収集します」で表が燃やすごみ・燃やさないごみだけなら、どちらにも「*」にもしない)。
# 地名に入る 1 字 (「藤枝」「草津」の枝・草、「木曜」の木) は単独で使わない
OTHER_ITEMS = re.compile(
    r"(?:剪定|せん定|木の?)枝|枝(?:木|葉|・葉)|(?:刈り?|雑|枯れ?)草|草木|落ち?葉|木くず|"
    r"発泡スチロール|スチロール|トレイ|トレー(?!ニング)|"
    r"せともの|瀬戸物|陶磁器|陶器|磁器|食器|割れ物|"
    r"おむつ|オムツ|布団|ふとん|カーペット|じゅうたん|絨毯|畳|マットレス|家具|"
    r"家電|傘|刃物|ライター|灰|食用油|廃油|危険物|埋立|埋め立て|自転車|タイヤ|消火器|在宅医療|注射")
RESOURCE = re.compile(r"資源")
DROPOFF = re.compile(r"自己搬入|持(?:ち)?込(?:み)?|搬入")
RESERVATION = re.compile(r"申(?:し)?込(?:み)?|予約|受付センター|電話で")
STATION = re.compile(r"リサイクルステーション|資源ステーション|回収ステーション|拠点回収")
NEED_MODE = re.compile(r"粗大")


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def official_label(text):
    """印字の名前 → official_label (改行と続く空白は空白 1 つ、前後の空白を除く。全角の空白はそのまま)"""
    t = (text or "").replace("\r", "")
    t = re.sub(r"[ \t]*\n[ \t　]*", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip(" \t")


def coarse(label):
    """名前 → 大まかな分類の鍵のリスト (並びは当たった順)。どれにも当たらなければ「資源」なら recyclable、ほかは other"""
    t = re.sub(r"[(（][^)）]*(?:回|曜)[^)）]*[)）]", "", nfkc(label))      # 「資源（週1回）」の回数のかっこ書きは見ない
    out = []
    for rx, keys in COARSE_RULES:
        if rx.search(t):
            for k in keys:
                if k not in out:
                    out.append(k)
            t = rx.sub("□", t)
    if not out:
        out = ["recyclable"] if RESOURCE.search(t) else ["other"]
    return out


def mode(label, context=""):
    """名前と、同じ行の文 (出し方・規則のマス) → (mode か None, 根拠の言葉)"""
    t = nfkc(f"{label} {context}")
    m = DROPOFF.search(t)
    if m:
        return "dropoff", m.group(0)
    m = RESERVATION.search(t)
    if m:
        return "reservation", m.group(0)
    m = STATION.search(t)
    if m:
        return "station", m.group(0)
    if NEED_MODE.search(nfkc(label)):
        return None, None                  # 粗大ごみは、言葉が無ければ決めない
    return "curbside", None


def is_category_header(text):
    """表の見出しのマスが品目の名前か (ごみ・資源の言葉か、辞書のどれかに当たる)"""
    t = nfkc(text)
    if re.search(r"ごみ|ゴミ|資源|リサイクル", t):
        return True
    return coarse(t) not in (["other"], ["recyclable"])


def category(label, context=""):
    """名前 → {"official_label", "coarse", "mode", "mode_evidence"}"""
    m, ev = mode(label, context)
    return {"official_label": official_label(label), "coarse": coarse(label), "mode": m, "mode_evidence": ev}


GENERIC_WORDS = ("ごみ", "ゴミ", "資源")                 # 品目を名指ししない言葉 (「ごみ・資源収集」「資源・ごみの収集日程」)


def named_in(text, cats, generic_labels=True):
    """文・見出しが名指しする品目 → (品目の id の集まり, 品目を名指ししない言葉の集まり {"ごみ", "資源"})。
    cats は {品目の id: {"coarse": [...], "official_label": "…"}}。名指しは、公式の名前がそのまま入っているか
    (generic_labels が False なら、「資源」「資源ごみ」のように分類が recyclable・other だけの名前は使わない)、
    辞書の分類 (中身の分からない資源 recyclable を除く) に当たるものだけ。当たった所を消したあとに「ごみ」「資源」が
    単独で残れば (「燃やすごみ」の「ごみ」は数えない)、その言葉を返す。呼ぶ側が、止める向き (deferred・但し書き) なら
    全部の品目に広げ、値を当てる向き (祝日・休み) なら決めない"""
    t = re.sub(r"\s+", "", nfkc(text))
    ids = set()
    labels = []
    for cid, c in cats.items():
        lab = re.sub(r"\s+", "", nfkc(c.get("official_label") or ""))
        if len(lab) < 2:
            continue
        if not generic_labels and set(c.get("coarse") or ()) <= {"recyclable", "other"}:
            continue
        labels.append((lab, cid))
    for lab, cid in sorted(labels, key=lambda x: -len(x[0])):
        if lab in t:
            ids.add(cid)
            t = t.replace(lab, "□")
    keys = set()
    t2 = re.sub(r"[(（][^)）]*(?:回|曜)[^)）]*[)）]", "", t)
    for rx, ks in COARSE_RULES:
        if rx.search(t2):
            keys |= set(ks)
            t2 = rx.sub("□", t2)
    for cid, c in cats.items():
        if set(c.get("coarse") or ()) & keys:
            ids.add(cid)
    generic = {("ごみ" if w == "ゴミ" else w) for w in GENERIC_WORDS if re.search(r"(?<!□)" + w, t2)}
    return ids, generic


def same(a, b):
    """2 つの読みの品目が同じか (分類の集まりと出し方)"""
    return set(a.get("coarse") or ()) == set(b.get("coarse") or ()) and a.get("mode") == b.get("mode")


def main(argv=None):
    import json
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    for a in argv:
        print(json.dumps(category(a.replace("\\n", "\n")), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
