"""問いかけ (日本語) と答えの JSON の形。DeepSeek と Opus に同じものを使う

違うのは画像の渡し方だけ: DeepSeek は画像を問いかけの部品として添える。Opus (Claude Code の CLI) は Read でファイルを開く。
system・指示・答えの形・資料の文字はまったく同じ (prompt_sha256 で確かめられる)。
"""
import json

from common import INPUTS, sha256

PROMPT_VERSION = "llmread.p1"

SYSTEM = ("あなたは日本の自治体のごみ収集の資料を読み、書かれていることだけを決まった形の JSON に書き写す係です。"
          "推測で補いません。資料に書かれていない・読み取れない・決まった形に収まらないことは、答えに入れずに unsure に書きます。"
          "答えは JSON だけを返します (前後に説明や ``` を付けない)。")

_WD = {"enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_DATE_OR_NULL = {"anyOf": [_DATE, {"type": "null"}]}
_TEXT = {"type": ["string", "null"]}
_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1}
_INTS = lambda lo, hi: {"type": "array", "items": {"type": "integer", "minimum": lo, "maximum": hi}, "minItems": 1}

SCHEMA = {
    "type": "object",
    "required": ["municipality", "period", "districts", "categories", "schedules", "exceptions", "unsure"],
    "additionalProperties": False,
    "properties": {
        "municipality": _TEXT,
        "period": {"type": "object", "required": ["printed", "from", "to"], "additionalProperties": False,
                   "properties": {"printed": _TEXT, "from": _DATE_OR_NULL, "to": _DATE_OR_NULL}},
        "districts": {"type": "array", "items": {
            "type": "object", "required": ["id", "label", "members"], "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "label": {"type": "string"},
                           "members": {"type": "array", "items": {"type": "string"}}, "note": _TEXT}}},
        "categories": {"type": "array", "items": {
            "type": "object", "required": ["id", "label", "coarse"], "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "label": {"type": "string"},
                           "coarse": {"type": "array", "items": {"enum": [
                               "burnable", "kitchen", "non_burnable", "plastic", "pet_bottle", "beverage_can", "glass_bottle",
                               "paper_cloth", "cloth", "paper", "hazardous", "spray_can", "oversized", "metal", "other"]}},
                           "note": _TEXT}}},
        "schedules": {"type": "array", "items": {
            "type": "object", "required": ["district", "category", "rules"], "additionalProperties": False,
            "properties": {"district": {"type": "string"}, "category": {"type": "string"}, "text": _TEXT,
                           "rules": {"type": "array", "items": {
                               "type": "object", "required": ["type"], "additionalProperties": False,
                               "properties": {
                                   "type": {"enum": ["weekly", "nth_weekday", "last_weekday", "monthly_day", "every_n_weeks", "dates"]},
                                   "weekdays": {"type": "array", "items": _WD, "minItems": 1},
                                   "nth": _INTS(1, 5), "days": _INTS(1, 31), "n": {"type": "integer", "minimum": 1, "maximum": 8},
                                   "anchor": _DATE, "dates": {"type": "array", "items": _DATE},
                                   "months": _INTS(1, 12), "from": _DATE, "to": _DATE, "text": _TEXT}}}}}},
        "exceptions": {"type": "array", "items": {
            "type": "object", "required": ["kind", "scope"], "additionalProperties": False,
            "properties": {
                "kind": {"enum": ["holiday", "closure", "year_end_normal", "nth_off", "months_off", "cancelled", "moved", "added"]},
                "scope": {"type": "object", "required": ["districts", "categories"], "additionalProperties": False,
                          "properties": {"districts": _IDS, "categories": _IDS}},
                "policy": {"enum": ["collected", "not_collected", "moved"]},
                "moved_to": {"enum": ["next_day", "next_weekday", "next_same_weekday", "prev_weekday"]},
                "from": _DATE, "to": _DATE, "year_end": {"type": "boolean"},
                "nth": _INTS(1, 5), "months": _INTS(1, 12), "dates": {"type": "array", "items": _DATE}, "text": _TEXT}}},
        "unsure": {"type": "array", "items": {
            "type": "object", "required": ["about", "text"], "additionalProperties": False,
            "properties": {"about": {"type": "string"}, "district": _TEXT, "category": _TEXT, "text": _TEXT}}},
    },
}

INSTRUCTIONS = """# 仕事
下の「資料」(自治体の公式のページ・表・PDF のページの画像) から家庭ごみの収集日を読み取り、最後に示す JSON の形で答えてください。

# 守ること
1. 資料に書かれていることだけを書く。推測・一般常識・ほかの自治体の例で補わない。
2. 迷ったもの、読めないもの、形に収まらないもの (例: 「年末年始を除く」とだけあって日付が無い、隔週の起点が分からない、地区の境が文で説明されていて名前に分けられない) は、その部分を答えに入れず unsure に書く。答えを 1 つに決められないときも unsure。
3. 名前 (自治体・地区・町名・品目) は資料の字のまま書く。言い換えない・まとめない・略さない。
4. 日付は YYYY-MM-DD。和暦 (令和8年 = 2026年) と年度 (令和8年度 = 2026-04-01〜2027-03-31) は西暦に直す。月日だけの印字は資料の期間から年を決める (決まらなければ unsure)。
5. 曜日は MO TU WE TH FR SA SU。
6. PDF の文字層は機械で取り出したもので、字が崩れたり順番が入れ替わったりしていることがある。画像を正とする。
7. 収集日でないもの (持ち込みの受付日、窓口の時間、行事) は入れない。日の決まった収集の無い品目 (持ち込みだけ、申し込んでから日が決まるもの) は categories に入れてよいが schedules には入れない。

# 書き方
- municipality: 資料に書かれた自治体の名前 (無ければ null)。
- period: 資料が言う有効な期間。印字の文字 (printed) と西暦の from・to。期間の印字が無ければ 3 つとも null にし、unsure に書く。
- districts: 収集日の分け方の地区 (区域・コース・地区・色の名前など)。id は "d1" "d2" … と付ける。label は印字の名前。地区の中身として町名・丁目・行政区などが並んでいれば members に 1 つずつ (印字のまま)。地区の分けが無く全域が同じなら、地区を 1 つだけ作り label を "全域" にする。
- categories: 収集の品目。id は "c1" "c2" …。label は資料の品目の名前そのまま。coarse は、資料が言う中身が当てはまる大まかな分類を下の語彙からすべて (分からなければ [])。品目の一部 (例: ○○のうちペットボトルだけ) にだけ別の収集日があるときは、その一部を別の品目に分ける。
- schedules: 地区 × 品目ごとの収集の規則。rules はどれかに当たる日が収集日 (和)。
  - weekly 毎週: {"type":"weekly","weekdays":["MO","TH"]}
  - nth_weekday 月の第 N 回目の曜日: {"type":"nth_weekday","weekdays":["WE"],"nth":[2,4]} (第2・4水曜日)。「第N週」が月の N 回目の曜日のことか暦の N 行目のことか資料から決まらなければ unsure
  - last_weekday 月の最後の曜日: {"type":"last_weekday","weekdays":["FR"]}
  - monthly_day 毎月の日: {"type":"monthly_day","days":[1,15]}
  - every_n_weeks N 週ごと: {"type":"every_n_weeks","weekdays":["TU"],"n":2,"anchor":"2026-04-07"}。anchor は資料に印字された収集日 (無ければ unsure)
  - dates 印字された日付の一覧: {"type":"dates","dates":["2026-04-03"]}。カレンダーや月ごとの表のように日付が印字されているときは dates を使う (規則の文より印字の日付を優先する)。dates は最終の日付として扱われ、下の exceptions は当てられない
  - どの型にも months (その月だけ) と from・to (その期間だけ。両端を含む) を付けられる。例「5〜10月は第1水曜日も出せる」→ {"type":"nth_weekday","weekdays":["WE"],"nth":[1],"months":[5,6,7,8,9,10]}
  - text: 元の文字 (印字のまま)
- exceptions: dates 以外の規則に当てる例外。scope で当てる地区と品目を id で示す (すべてなら ["*"])。text に元の文字。
  - holiday 祝日・振替休日の扱い: policy は collected (祝日も収集) / not_collected (祝日は収集しない) / moved (別の日に移す。moved_to は next_day / next_weekday / next_same_weekday / prev_weekday)
  - closure 収集しない期間 (両端を含む): {"kind":"closure","from":"2026-12-29","to":"2027-01-03","year_end":true}。「年末は12月28日まで、年始は1月4日から」なら from は 12月29日、to は 1月3日。年末年始のものは year_end を true
  - year_end_normal: 年末年始も通常どおり収集すると書いてある
  - nth_off: 月の第 N 回目の曜日は収集しない (「第5週は休み」→ "nth":[5])
  - months_off: その月 (months) は収集しない
  - cancelled: その日 (dates) は収集しない
  - moved: from の日の収集を to の日に移す
  - added: 規則のほかに収集する日 (dates)
  資料に祝日の扱いや年末年始の扱いが書いていなければ、exceptions に作らず、unsure に「書いていない」と書く。
- unsure: 迷ったこと・入れなかったこと。about (何について)、district・category (関係する id。無ければ null)、text (元の文字)。

# 大まかな分類の語彙 (coarse)
burnable 燃やす・可燃ごみ / kitchen 生ごみ / non_burnable 燃やさない・不燃ごみ / plastic プラスチック / pet_bottle ペットボトル / beverage_can 飲料の缶 / glass_bottle びん / paper_cloth 紙と布を分けずに 1 つで / cloth 古布・衣類 / paper 古紙 / hazardous 有害ごみ (電池・蛍光管など) / spray_can スプレー缶 / oversized 粗大ごみ / metal 金属類 / other ほか

# 答えの JSON の形 (JSON Schema)
""" + json.dumps(SCHEMA, ensure_ascii=False, separators=(",", ":"))

KIND_JA = {"html": "HTML のページ (本文の範囲を文字にした。表は rowspan・colspan だけ残した簡単な HTML の表)",
           "csv": "CSV (表を markdown の表にした)", "xlsx": "Excel (シートごとに markdown の表にした。結合したマスは左上の値で埋めた)",
           "xls": "Excel (シートごとに markdown の表にした。結合したマスは左上の値で埋めた)",
           "pdf": "PDF (ページの画像と、あればページの文字層)"}


def prompt_sha256():
    return sha256(SYSTEM + "\0" + INSTRUCTIONS)


def material(m):
    """manifest → 資料の文字 (2 つのモデルで同じ)"""
    d = INPUTS / m["doc_id"]
    out = ["# 資料", f"種類: {KIND_JA[m['kind']]}", f"出どころ: {m.get('url') or m.get('path')}"]
    if m.get("section"):
        out.append(f"(このページのうち、見出し「{m['section']}」の節だけを渡す)")
    if m.get("text"):
        out += ["", (d / m["text"]).read_text("utf-8")]
    for p in m.get("pages") or []:
        out += ["", f"## ページ {p['page']}", f"画像: {p['png']}"]
        if p.get("text"):
            out += ["文字層:", "<<<", (d / p["text"]).read_text("utf-8"), ">>>"]
        else:
            out.append("文字層: なし")
    if m.get("pages"):
        out += ["", "# 画像", "資料のページの画像 (上の「画像:」の名前のファイル) を添える。"]
    return "\n".join(out)


def build(m):
    """manifest → {"system", "text" (指示 + 資料), "images": [(名前, 道すじ)], "prompt_sha256"}"""
    d = INPUTS / m["doc_id"]
    return {"system": SYSTEM, "text": INSTRUCTIONS + "\n\n" + material(m),
            "images": [(p["png"], d / p["png"]) for p in m.get("pages") or []], "prompt_sha256": prompt_sha256()}
