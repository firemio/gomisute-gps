"""型 B の HTML の表 (DESIGN.md §3.5 B): 2 通りの格子、表の形の確かめ、生の断片、表の読み (読み取り役 A)

  python nationwide/readers/html_tables.py <HTML のファイルか URL (キャッシュから)>     # 表ごとの形・読み・しるしを表示

表の形はコードが決める (§0 の 7)。格子は 2 通りで作る:
  (1) 仕様どおりに rowspan・colspan を広げた格子 (kz_calendar.grid と同じ)
  (2) 行ごとに、その行に書いてあるマスを頭から順に読む
2 つが食い違う (広げた格子の行の幅がそろわない、rowspan が表の外にはみ出す、行のマスと上からの結合の数が見出しの幅を超える)
なら壊れた表 (七ヶ宿) の疑いとして structure の裁定に回す (LLM の票では決めない)。
読み (読み取り役 A) は、表の向き (地区の行 × 品目の列 / 品目の行 × 指定日の列 / 曜日の行 × 品目の並び) を見出しで決め、
地区のマスは members.py、品目の見出しは categories.py、規則のマスは rules.py で読む。
しるし (§3.6) は markup.py、2 つ目の読み (LLM。生の断片) は markup.reader_b。
"""
import re, sys, unicodedata
from bs4 import Tag

import categories, content, members, rules

DISTRICT_WORDS = re.compile(r"町名|地区|区域|地域|町丁目|町丁|地名|住所|町会|自治会|行政区|集落|お住まい|収集区域")
CHOME_WORDS = re.compile(r"^丁目$|^番地$|^丁目・番地$|^丁目等$")
INDEX_WORDS = re.compile(r"^(?:音|50音|五十音|あ行|頭文字|よみ|読み)$")
CATLABEL_WORDS = re.compile(r"種別|品目|分別|種類|区分")
RULE_WORDS = re.compile(r"指定日|収集日|収集の日|収集曜日|曜日|日程|回収日|出す日")
LINK_WORDS = re.compile(r"カレンダー|PDF|pdf|calendar")
# リンクの文字の大きさの書き添え (能美「(PDF 791KB)」、板橋「（PDF 255.9KB）」、朝霞「[PDFファイル／1.9MB]」)
FILESIZE = re.compile(r"[\(（\[［]\s*(?:PDF|pdf|Excel|EXCEL|CSV|Word|その他)(?:ファイル|形式)?\s*[:：／/、,]?\s*[\d.,]+\s*[KMG]?i?B\s*[\)）\]］]|"
                      r"[\(（\[［]\s*[\d.,]+\s*[KMG]i?B\s*[\)）\]］]")


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def squash(s):
    return content.squash(s)


# ---- マスと格子 ----
class Cell:
    """表のマス: el (td・th の要素)、tag、text (見える文字。br は改行、リンクの大きさの書き添えを除く)、rowspan・colspan、
    row (行の番号)、col (広げた格子の列)、own (その行で何番目のマスか)"""

    def __init__(self, el, row, own):
        self.el, self.tag, self.row, self.own = el, el.name, row, own
        self.rowspan = _span(el.get("rowspan"))
        self.colspan = _span(el.get("colspan"))
        self.col = None
        self.text, self.stripped = cell_text(el)

    def __repr__(self):
        return f"Cell(r{self.row}c{self.col} {self.text[:20]!r})"


def _span(v):
    try:
        n = int(str(v).strip())
        return n if 1 <= n <= 200 else 1
    except (TypeError, ValueError):
        return 1


def cell_text(el):
    """マスの見える文字 → (文字, 外した書き添えのリスト)。br・ブロックの要素は改行、img の alt は入れない (しるしとして別に数える)"""
    parts = []

    def walk(n):
        for ch in n.children:
            if isinstance(ch, Tag):
                if ch.name in content.SKIP_TAGS:
                    continue
                if ch.name == "br":
                    parts.append("\n")
                elif ch.name in ("p", "div", "li", "dt", "dd"):
                    parts.append("\n")
                    walk(ch)
                    parts.append("\n")
                elif ch.name == "img":
                    continue
                else:
                    walk(ch)
            elif not isinstance(ch, content.Comment):
                parts.append(str(ch))
    walk(el)
    t = "".join(parts).replace("\xa0", " ")
    t = re.sub(r"[​‌‍﻿]", "", t)
    stripped = [m.group(0) for m in FILESIZE.finditer(t)]
    t = FILESIZE.sub("", t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r" *\n[ \n]*", "\n", t).strip()
    return t, stripped


def rows_of(table):
    """表の行 (入れ子の表の行を除く)"""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def own_cells(table):
    """(2) 行ごとの読み: 行ごとに、書いてあるマスを頭から順に"""
    out = []
    for r, tr in enumerate(rows_of(table)):
        out.append([Cell(td, r, i) for i, td in enumerate(tr.find_all(["td", "th"], recursive=False))])
    return out


def spec_grid(table, rows=None):
    """(1) 仕様どおりに rowspan・colspan を広げた格子 → (格子 [[Cell か None]], はみ出し (rowspan が表の外へ伸びたマスの数))。
    結合で埋まった所には、元のマスと同じ Cell を入れる"""
    rows = rows if rows is not None else own_cells(table)
    grid, pend = [], {}
    for r, cells in enumerate(rows):
        row, c = [], 0
        for cell in cells:
            while (r, c) in pend:
                row.append(pend.pop((r, c)))
                c += 1
            cell.col = c
            for k in range(cell.colspan):
                row.append(cell)
                for d in range(1, cell.rowspan):
                    pend[(r + d, c + k)] = cell
            c += cell.colspan
        while (r, c) in pend:
            row.append(pend.pop((r, c)))
            c += 1
        grid.append(row)
    overflow = len({id(v) for (r, _), v in pend.items() if r >= len(rows)})
    return grid, overflow


def structure(table):
    """表の形の確かめ (2 通りの格子の比べ) → {"ok", "issues", "rows", "cols", "spans"}"""
    rows = own_cells(table)
    grid, overflow = spec_grid(table, rows)
    widths = [len(r) for r in grid if r]
    issues = []
    if not widths:
        return {"ok": False, "issues": ["空の表"], "rows": 0, "cols": 0, "spans": 0}
    common = max(set(widths), key=widths.count)
    head = widths[0]
    if overflow:
        issues.append(f"rowspan が表の外へはみ出すマス {overflow}")
    ragged = [i for i, w in enumerate(widths) if w != head]
    if ragged:
        issues.append(f"広げた格子の行の幅がそろわない (見出し {head} 列、違う行 {len(ragged)}: {ragged[:5]})")
    for i, (own, g) in enumerate(zip(rows, grid)):
        from_above = sum(1 for x in g if x is not None and x.row != i)
        if own and from_above and sum(c.colspan for c in own) + from_above > head:
            issues.append(f"行 {i}: 行のマスと上からの結合の数が見出しの幅を超える (行ごとの読みと広げた格子が食い違う)")
            break
    spans = sum(1 for r in rows for c in r if c.rowspan > 1 or c.colspan > 1)
    return {"ok": not issues, "issues": issues, "rows": len(grid), "cols": common, "spans": spans}


def tables(root):
    """本文の範囲 (か soup) の表 → [(番号, 表の要素)]。入れ子の表の外側だけ"""
    out = []
    for t in root.find_all("table"):
        if t.find_parent("table") is None:
            out.append(t)
    return list(enumerate(out))


def heading_path_of(el):
    """要素の前にある見出しの並び (h1〜h6)"""
    heads = []
    for h in el.find_all_previous(list(content.HEADINGS)):
        lv = content.HEADINGS[h.name]
        if not heads or lv < heads[-1][0]:
            heads.append((lv, content._clean(h.get_text(" "))))
        if lv == 1:
            break
    return [t for _, t in reversed(heads)]


# ---- 表の読み (読み取り役 A) ----
def header_rows(grid):
    """見出しの行の数: 頭から、すべてのマスが th の行 (か、品目・地区の見出しの言葉を 2 つ以上含む最初の行)"""
    n = 0
    for row in grid:
        cells = [c for c in row if c is not None]
        if cells and all(c.tag == "th" for c in cells) and len({id(c) for c in cells}) >= 2:
            n += 1
        else:
            break
    if n == 0 and grid:
        words = [c.text for c in grid[0] if c is not None]
        if sum(1 for w in words if DISTRICT_WORDS.search(w) or categories.is_category_header(w) or RULE_WORDS.search(w)) >= 2:
            n = 1
    return n


def column_roles(header_texts, col_values):
    """列の見出しと値 → 列の役目のリスト (index / district / chome / category / catlabel / rule / weekday / catlist / link / other)"""
    roles = []
    for i, h in enumerate(header_texts):
        h0 = squash(h)
        vals = [v for v in col_values[i] if v]
        if INDEX_WORDS.match(h0) or (vals and all(re.fullmatch(r"[ぁ-んァ-ヶ]", squash(v)) for v in vals)):
            roles.append("index")
        elif (LINK_WORDS.search(h0) and not categories.is_category_header(h.split("\n")[0])) or \
                (vals and sum(1 for v in vals if re.search(r"\.pdf$|^https?://|^/", v.strip())) >= 0.8 * len(vals)):
            roles.append("link")                    # 地区ごとのカレンダーへのリンク (板橋「地域別カレンダー番号」・杉並 pdf_url)
        elif CHOME_WORDS.match(h0):
            roles.append("chome")
        elif DISTRICT_WORDS.search(h0) and not categories.is_category_header(h0):
            roles.append("district")
        elif re.fullmatch(r"曜日", h0):
            roles.append("weekday")
        elif CATLABEL_WORDS.search(h0) and not RULE_WORDS.search(h0):
            roles.append("catlist" if re.search(r"ごみ|資源", h0) and "曜日" in "".join(header_texts) else "catlabel")
        elif RULE_WORDS.search(h0) and not categories.is_category_header(h0):
            roles.append("rule")
        elif categories.is_category_header(h0) and not re.search(r"事務所|担当|管轄|出し方|主な|対象|注意|備考|料金|方法", h0):
            roles.append("category")
        else:
            roles.append("other")
    return roles


def orientation(roles):
    if ("district" in roles or "chome" in roles) and "category" in roles:
        return "district_rows"
    if "catlabel" in roles and "rule" in roles:
        return "category_rows"
    if "weekday" in roles and ("catlist" in roles or "catlabel" in roles):
        return "weekday_rows"
    return "other"


def _grid_texts(grid):
    return [[(c.text if c is not None else "") for c in row] for row in grid]


# 札のマス (地区・品目の札・曜日) が印だけ (「※」「注」) か空、か注の印で始まる (「※注意」「注：」「【注意】」) → その行は表の注の行
NOTE_ONLY = re.compile(r"[\s※*＊注:：・\-－()（）]*")
NOTE_LEAD = re.compile(r"\s*(?:[※*＊]|【?注(?:意|記)?\s*\d*\s*[】:：)]|注意)")


def _note_row(row, crow, keys, vals, empty_key=True, lead=False):
    """表の中の注の行か: 札のマスと値のマス (品目・規則) が同じマス (colspan の全幅の注「※第5週は収集しません」)、
    か札のマスが印だけ (empty_key なら空も。lead (地区の札) なら注の印で始まるものも) で値のマスに文字がある。
    CSV (crow が None) は札と値が同じ文字の 1 つだけのとき。品目の札の頭の「※」(「※びん」) は品目の名前の読み残し (注の行にしない)"""
    vt = [row[i] for i in vals if row[i]]
    if not vt:
        return False
    kt = [row[i] for i in keys if row[i]]
    if crow is not None:
        kc = {id(crow[i]) for i in keys if i < len(crow) and crow[i] is not None}
        vc = {id(crow[i]) for i in vals if i < len(crow) and crow[i] is not None}
        if kc & vc:
            return True
    elif kt and len(set(kt + vt)) == 1:
        return True
    if not kt:
        return empty_key
    return all(NOTE_ONLY.fullmatch(nfkc(t)) or (lead and NOTE_LEAD.match(nfkc(t))) for t in kt)


def _note_rec(row, r, cols):
    """注の行 → {"row", "text" (行のマスの文字。同じマスは 1 つ), "cells" [(行, 列)]}"""
    return {"row": r, "text": "\n".join(dict.fromkeys(row[i] for i in cols if row[i])), "cells": [(r, i) for i in cols if row[i]]}


def _other_cells(row, r, roles):
    """行の、規則にも名前にも使わない列 (備考・注意事項など。役目 other) のマス → [(行, 列, 文字)] (その行の系列だけの注。呼ぶ側が関門に通す)"""
    return [(r, i, row[i]) for i, x in enumerate(roles) if x == "other" and row[i]]


# 列の役目の言葉だけのマス (表の途中の見出しの行を見分ける。column_roles の言葉と区切り。閉じた並び)
ROLE_ONLY = re.compile(r"(?:町名|地区名?|区域|地域名?|町丁目|町丁|地名|住所|町会|自治会|行政区|集落|お住まいの?|収集区域|丁目|番地|"
                       r"50音|五十音|あ行|頭文字|よみ|読み|種別|品目|分別|種類|区分|指定日|収集日|収集の日|収集曜日|曜日|日程|回収日|"
                       r"出す日|カレンダー|備考|[\s・/／、,])+")


def _head_word(t):
    """マスの文字が列の役目の言葉か品目の名前だけか (曜日・日付・回の無い短い品目の名前「プラスチック」「びん・かん」)"""
    s = squash(t)
    if not s:
        return False
    if ROLE_ONLY.fullmatch(s):
        return True
    return categories.is_category_header(s) and len(s) <= 20 and not RULEISH.search(s) and not re.search(r"\d", s) and \
        not rules.is_none_mark(s)


def _mid_head(row, crow):
    """表の本文の途中の見出しの行か: すべてのマスが th (2 つ以上の別のマス) か、文字のあるマス (2 つ以上) がどれも列の役目の言葉・品目の名前"""
    if crow is not None:
        cs = [c for c in crow if c is not None]
        if cs and all(c.tag == "th" for c in cs) and len({id(c) for c in cs}) >= 2:
            return True
    ne = [x for x in row if x]
    return len(ne) >= 2 and all(_head_word(x) for x in ne)


def _single_cell(row, crow):
    """文字のあるマスが 1 つだけの行 (「※第5週は収集しません,,」・備考だけ・Excel の結合したマスの左上だけの注)。
    HTML では、同じマス (colspan・rowspan) が広がったものも 1 つと数える"""
    if crow is not None:
        ids = {id(c) for i, c in enumerate(crow) if c is not None and i < len(row) and row[i]}
        return len(ids) == 1
    return sum(1 for x in row if x) == 1


def _unparsed(p, why):
    """読みを読めないもの (unparsed) にする (読み残しは捨てない)"""
    return rules.Parsed(p.text, p.irs, "unparsed", rest=p.rest, notes=list(p.notes) + [why])


AFTER_MID_HEAD = "表の途中の見出しの行のあとの行 (列の品目・役目が変わりうるので読まない)"


def _group_key(row, dcols, ccols):
    """品目の行・曜日の行の表の、地区・丁目の列の文字 → (町名, 丁目)"""
    town = " ".join(dict.fromkeys(row[i] for i in dcols if row[i]))
    chome = " ".join(dict.fromkeys(row[i] for i in ccols if row[i]))
    return town, chome


def _group_rows(groups, dcols, ccols, n_head, lg_code, district_label, blank_labels, multi_rule):
    """地区ごと (地区・丁目の列があるとき) の品目の規則 → rows。地区の列が無ければ 1 行 (ページの地区)。
    地区の列が空の行に同じ品目があれば、その品目はどの地区のものか決まらない (すべての地区で読めないものにする)。
    規則の列が 2 つ以上で見出しが違えば (収集曜日 → A | B)・1 つの行の 2 つ以上の列に規則があれば、まとめない (multiple)"""
    out = []
    for (town, chome), g in groups.items():
        has_d = bool(dcols or ccols)
        dtext = f"{town} {chome}".strip()
        ms = None
        if has_d and dtext:
            ms = members.parse_cell(chome, lg_code, town=town) if ccols else members.parse_cell(town, lg_code)
        rec = {"row": g["rows"][0] if has_d and g["rows"] else n_head, "district_text": dtext if has_d else (district_label or ""),
               "members": ms, "rules": {}, "cells": {}, "calendar_links": [], "other_cells": g["others"],
               "src_rows": list(g["rows"])}
        if has_d:
            rec["district_from"] = "column"
            rec["district_cells"] = [(r, i) for r in g["rows"] for i in dcols + ccols]
        for label, lst in g["per"].items():
            p = _combine([x[0] for x in lst])
            if multi_rule:
                p = rules.Parsed(p.text, p.irs, "multiple", rest=p.rest, notes=list(p.notes) + ["規則の列が 2 つ以上 (見出しが違う・1 つの行の 2 つ以上の列に規則がある。まとめない)"])
            elif has_d and (label in blank_labels or not dtext):
                p = _unparsed(p, "地区の列が空の行に同じ品目がある (どの地区の規則か決まらない)")
            if any(x[2] for x in lst):
                p = _unparsed(p, AFTER_MID_HEAD)
            rec["rules"][label] = p
            rec["cells"][label] = [x[1] for x in lst]
        out.append(rec)
    return out


def read_grid(texts, n_head, cells=None, nth_def=None, lg_code=None, district_label=None):
    """文字の格子 (表でも CSV でも) と見出しの行の数 → 読み (dict)。cells は HTML の Cell の格子 (しるしと参照のため)。
    表の中の注の行 (_note_row: 地区のマスと品目のマスが同じ colspan のマス・札が印だけか空) は地区や品目の行にしない:
    notes に入れ、呼ぶ側が表のすべての系列の注として関門に通す (読めなければ表の系列すべてを止める)。
    7 回目: 地区・品目の行にならない行で文字のあるもの (品目のマスが空・文字のあるマスが 1 つだけ・品目がすべて無しのしるしで地区の
    マスが地区の文法で読めない「不燃ごみは奇数月です | － | －」) も注の行。本文の途中の見出しの行 (すべて th か列の役目の言葉だけ) は
    mid_heads に入れ、そのあとの行の規則は読めないもの (見出しを読み直すまで止める)。品目の行・曜日の行の表に地区・丁目の列があれば、
    地区の値ごとに行を分ける (district_rows と同じく members で読む。品目を 1 つにまとめて自治体全体の答えにしない)。
    規則にも名前にも使わない列 (other) のマスは、行ごとの注 (rows の other・other_cells)。ページの方針の文にしない"""
    if not texts or n_head < 1:
        return {"orientation": "other", "why": "見出しの行が無い"}
    width = max(len(r) for r in texts)
    texts = [r + [""] * (width - len(r)) for r in texts]
    head = [" ".join(dict.fromkeys(texts[h][i] for h in range(n_head) if texts[h][i])) for i in range(width)]
    body = texts[n_head:]
    cols = [[r[i] for r in body] for i in range(width)]
    roles = column_roles(head, cols)
    ori = orientation(roles)
    out = {"orientation": ori, "header": head, "roles": roles, "n_head": n_head, "categories": {}, "rows": [], "notes": [],
           "mid_heads": []}
    if ori == "other":
        return out
    crow = lambda r: cells[r] if cells is not None and r < len(cells) else None
    all_cols = list(range(width))
    dcols = [i for i, r in enumerate(roles) if r == "district"]
    ccols = [i for i, r in enumerate(roles) if r == "chome"]

    def note(row, r, cols_=None):
        out["notes"].append(_note_rec(row, r, cols_ or all_cols))

    if ori == "district_rows":
        cat_cols = [i for i, r in enumerate(roles) if r == "category"]
        for i in cat_cols:
            out["categories"][head[i]] = {"col": i, **categories.category(head[i], " ".join(cols[i][:5]))}
        lcols = [i for i, r in enumerate(roles) if r == "link"]
        for k, row in enumerate(body):
            r = n_head + k
            if not any(row):
                continue
            if _mid_head(row, crow(r)):
                out["mid_heads"].append(r)
                continue
            if not any(row[i] for i in cat_cols):
                note(row, r)                            # 品目のマスが空の行 (地区のマスだけ・備考だけ): 役目の無い行は注
                continue
            if _note_row(row, crow(r), dcols + ccols, cat_cols, lead=True):
                out["notes"].append(_note_rec(row, r, dcols + ccols + cat_cols))
                continue
            town = " ".join(dict.fromkeys(row[i] for i in dcols if row[i]))
            chome = " ".join(dict.fromkeys(row[i] for i in ccols if row[i]))
            if not members.any_place(town or chome, lg_code):
                note(row, r)                            # 地区のマスに地名として読める中身が 1 つも無い行 (「不燃ごみは奇数月です」) は注
                continue
            if all(not row[i] or rules.is_none_mark(row[i]) or rule_with_header(head[i], row[i], nth_def).status == "unparsed"
                   for i in cat_cols) and not members.readable_district(town or chome, lg_code):
                # 品目がすべて無しのしるし・空・規則として読めない文 (「不燃ごみは奇数月です | － | －」「備考 | 祝日は収集しません |」) で、
                # 地区のマスが地区の文法で読めない行は注 (表の系列すべての注として関門へ。別の地区の名前にしない)
                note(row, r)
                continue
            ms = members.parse_cell(chome, lg_code, town=town) if ccols else members.parse_cell(town, lg_code)
            rec = {"row": r, "district_text": f"{town} {chome}".strip(), "members": ms, "rules": {}, "cells": {},
                   "other_cells": _other_cells(row, r, roles)}
            for i in cat_cols:
                rec["rules"][head[i]] = rule_with_header(head[i], row[i], nth_def)
                if out["mid_heads"]:
                    rec["rules"][head[i]] = _unparsed(rec["rules"][head[i]], AFTER_MID_HEAD)
                rec["cells"][head[i]] = (r, i)
            rec["calendar_links"] = [row[i] for i in lcols if row[i]]
            if cells is not None and r < len(cells):
                # 地区のマスのリンクが資料 (PDF など。「(PDF 791KB)」の書き添え) を指す → 地区ごとのカレンダー (能美)。その行は pending_printed
                for i in dcols + ccols:
                    c = cells[r][i] if i < len(cells[r]) else None
                    if c is None or c.row != r:
                        continue
                    for a in c.el.find_all("a"):
                        href = a.get("href") or ""
                        if re.search(r"\.(?:pdf|xlsx?|jpe?g|png)(?:$|[?#])", href, re.I) or c.stripped:
                            rec["calendar_links"].append(href)
            out["rows"].append(rec)
    elif ori == "category_rows":
        lab = roles.index("catlabel")
        rc = [i for i, r in enumerate(roles) if r == "rule"]
        # 規則の列が 2 つ以上で、見出しが違う (「収集曜日」の下に「A」「B」) か、同じ見出しでも 1 つの行の 2 つ以上の規則の列に
        # 文字がある (「収集曜日 | 収集曜日」に「月・木 | 火・金」) → 列ごとに別の地区・条件かもしれないので、まとめない
        cell_id = lambda r, i: id(crow(r)[i]) if crow(r) is not None and i < len(crow(r)) and crow(r)[i] is not None else i
        multi_rule = len({squash(head[i]) for i in rc}) > 1 or \
            any(len({cell_id(n_head + k, i) for i in rc if row[i]}) > 1 for k, row in enumerate(body))
        groups, blank, last = {}, set(), None
        for k, row in enumerate(body):
            r = n_head + k
            if not any(row):
                continue
            if _mid_head(row, crow(r)):
                out["mid_heads"].append(r)
                continue
            if row[lab] and _note_row(row, crow(r), [lab], rc, empty_key=False):
                out["notes"].append(_note_rec(row, r, [lab] + rc))
                continue
            if _single_cell(row, crow(r)) or not (row[lab] or last):
                note(row, r)                            # 文字のあるマスが 1 つだけ・品目の決まらない行は注
                continue
            key = _group_key(row, dcols, ccols)
            if any(key) and not members.any_place(key[0] or key[1], lg_code):
                note(row, r)                            # 地区の列の文字が地名として読めない行は注 (別の地区にしない)
                continue
            label = row[lab] or last                    # 結合で空なら上の品目 (東秩父の資源回収の 2 行)
            last = label
            ctx = " ".join(row[i] for i in range(width) if i != lab)
            out["categories"].setdefault(label, {"col": lab, **categories.category(label, ctx)})
            if (dcols or ccols) and not any(key):
                blank.add(label)
            g = groups.setdefault(key, {"per": {}, "others": {}, "rows": []})
            if r not in g["rows"]:
                g["rows"].append(r)
            for i in rc:
                g["per"].setdefault(label, []).append((rules.parse(row[i], nth_def), (r, i), bool(out["mid_heads"])))
            g["others"].setdefault(label, []).extend(_other_cells(row, r, roles))
        if not groups:
            groups[("", "")] = {"per": {}, "others": {}, "rows": []}
        out["rows"] = _group_rows(groups, dcols, ccols, n_head, lg_code, district_label, blank, multi_rule)
        if not (dcols or ccols):
            for rec in out["rows"]:                     # 地区の列の無い表は、今までの形 (1 つのマスならその位置)
                rec["cells"] = {k: (v[0] if len(v) == 1 else v) for k, v in rec["cells"].items()}
    elif ori == "weekday_rows":
        wc = roles.index("weekday")
        lc = roles.index("catlist") if "catlist" in roles else roles.index("catlabel")
        groups, blank = {}, set()
        for k, row in enumerate(body):
            r = n_head + k
            if not any(row):
                continue
            if _mid_head(row, crow(r)):
                out["mid_heads"].append(r)
                continue
            if row[wc] and _note_row(row, crow(r), [wc], [lc], empty_key=False):
                out["notes"].append(_note_rec(row, r, [wc, lc]))
                continue
            if _single_cell(row, crow(r)) or not row[lc].strip():
                note(row, r)                            # 文字のあるマスが 1 つだけ・品目の並びの無い行は注
                continue
            key = _group_key(row, dcols, ccols)
            if any(key) and not members.any_place(key[0] or key[1], lg_code):
                note(row, r)                            # 地区の列の文字が地名として読めない行は注 (別の地区にしない)
                continue
            g = groups.setdefault(key, {"per": {}, "others": {}, "rows": []})
            if r not in g["rows"]:
                g["rows"].append(r)
            wds = rules._wds(rules.prep(row[wc]))
            # 曜日のマスが曜日だけでない (「第2月曜日」「月曜日（第2週）」「月・第2」・空) 行は読み飛ばさない: マスを規則の文法で読み
            # (第N + 曜日は monthly_nth。後ろの「（第2週）」は読み残し rest → 呼ぶ側の関門)、品目の行の頭の回と重なれば読めない
            whole = None if wds else rules.parse(row[wc], nth_def)
            carry = ""
            for line in [x for x in row[lc].split("\n") if x.strip()]:
                if re.fullmatch(r"\s*第?\s*\d{1,2}(?:\s*[・,、･]\s*\d{1,2})*(?:回目)?\s*", nfkc(line)):
                    carry = nfkc(line).strip()          # 「第2・4」だけの行は、次の行の品目に付ける (三鷹)
                    continue
                line = (carry + " " + line) if carry else line
                carry = ""
                m = re.match(r"^\s*(第?\s*\d{1,2}(?:\s*[・,、･]\s*\d{1,2})*(?:回目)?)\s*(.+)$", nfkc(line))
                label = (m.group(2) if m else line).strip()
                if wds:
                    txt = (m.group(1) if m else "") + "・".join({"MO": "月", "TU": "火", "WE": "水", "TH": "木", "FR": "金", "SA": "土",
                                                                "SU": "日"}[w] for w in wds) + "曜日"
                    p = rules.parse(txt, nth_def)
                elif m:
                    p = rules.Parsed(f"{row[wc]} / {m.group(1)}", whole.irs, "unparsed", rest=whole.rest,
                                     notes=["曜日のマスが曜日だけでなく、品目の行の頭にも回がある"])
                else:
                    p = rules.Parsed(whole.text, whole.irs, whole.status, whole.pieces, whole.rest,
                                     whole.notes + ["曜日のマスを規則の文法で読んだ (曜日だけでない)"], whole.subjects, whole.inline)
                if (dcols or ccols) and not any(key):
                    blank.add(label)
                g["per"].setdefault(label, []).append((p, (r, lc), bool(out["mid_heads"])))
                g["others"].setdefault(label, []).extend(_other_cells(row, r, roles))
        for g in groups.values():
            for label in g["per"]:
                out["categories"].setdefault(label, {"col": lc, **categories.category(label)})
        if not groups:
            groups[("", "")] = {"per": {}, "others": {}, "rows": []}
        out["rows"] = _group_rows(groups, dcols, ccols, n_head, lg_code, district_label, blank, False)
    return out


HEADER_RULE = re.compile(r"第?\d{1,2}(?:[・,、･]第?\d{1,2})*(?:週目?|回目|番目)|第\d{1,2}(?:[・,、･]\d{1,2})*|隔週|毎月")


def rule_with_header(header, cell, nth_def=None):
    """規則のマスを、品目の見出しの中の規則の切れ端 (三郷「びん、かん (第1・3週)」) と合わせて読む。
    見出しに第N・N週などがあれば、マスの曜日だけを読んで毎週と言わない: 切れ端が 1 つなら前に付けて読み、
    2 つ以上 (1 つの見出しに品目が 2 つ) なら multiple にする。「（月2回）」(世田谷) は回数の書き添えで、規則の切れ端にしない"""
    frags = HEADER_RULE.findall(nfkc(header))
    if not frags:
        return rules.parse(cell, nth_def)
    if len(frags) > 1:
        p = rules.parse(cell, nth_def)
        return rules.Parsed(cell, p.irs, "multiple", notes=[f"見出しに規則の切れ端が 2 つ以上 ({'・'.join(frags)})"])
    p = rules.parse(frags[0] + nfkc(cell), nth_def)
    p.notes.append(f"見出しの規則の切れ端「{frags[0]}」を前に付けて読んだ")
    return p


def _combine(parsed):
    """同じ品目の規則の読み (行ごと) → 1 つの Parsed (毎週どうしはまとめる。それ以外が混ざれば multiple)"""
    if len(parsed) == 1:
        return parsed[0]
    rest = "/".join(p.rest for p in parsed if p.rest)          # 読み残し (年末年始・祝日の文か、使われなかった但し書き) は捨てない
    if any(p.status not in ("ok",) for p in parsed):
        bad = [p for p in parsed if p.status != "ok"]
        st = bad[0].status if bad[0].status != "inline" else "unparsed"
        return rules.Parsed(" / ".join(p.text for p in parsed), [i for p in parsed for i in p.irs], st, rest=rest,
                            notes=["行ごとの規則に読めないものがある"])
    irs = rules.merge([i for p in parsed for i in p.irs])
    st = "ok" if len(irs) == 1 else "multiple"
    if st == "ok" and rest and len({p.rest for p in parsed}) > 1:
        # 1 つの行の読み残し (「月曜日（祝日は休み）」の注) を、まとめたほかの行の曜日に広げない (どの行の注かが規則に残らない)
        return rules.Parsed(" / ".join(p.text for p in parsed), irs, "unparsed", rest=rest,
                            notes=["行ごとの規則の読み残しが行によって違う (まとめた規則に当てられない)"])
    return rules.Parsed(" / ".join(p.text for p in parsed), irs, st, rest=rest,
                        notes=[] if st == "ok" else ["1 つの品目に 2 つ以上の規則"])


# ---- 型 C: HTML の文 (読み取り役 A) ----
# 「燃やすごみ：毎週月・木」「【可燃物（燃えるごみ）】 毎週 月曜日・木曜日」の頭の品目の名前
TEXT_LABEL = re.compile(r"^(?:【(?P<b>[^【】]{1,30})】|(?P<l>[^:：【】。]{1,24}?)\s*[:：])\s*(?P<rule>.+)$")
# 規則の行に要る字 (曜日か日付)。「申し込み」「随時」だけの文は品目の出し方の説明が多いので、型 C では規則にしない
RULEISH = re.compile(r"[月火水木金土日](?:曜|・|、|,|$)|毎週|毎月|隔週|第\s*[\d１-９一二三四五]|\d{1,2}月\d{1,2}日")
MD_LIST = re.compile(r"(\d{1,2})月(\d{1,2})日")
# 日付の一覧 (listed) として読むのは、規則の頭から続く「M月D日」の並び (曜日のかっこ書きつき。読点・中黒・空白で区切る) だけ。
# 並びの後ろ (「※7月13日は収集しません」「（8月は休みです）」「東町は翌日」) は読み残し (rest) にし、呼ぶ側が規則のマスの読み残しと
# 同じく cell_notes と関門に通す (並びの外の日付を一覧に足さない・後ろの注を黙って落とさない)
_LISTED_DAY = r"\d{1,2}月\d{1,2}日(?:\s*\((?:[月火水木金土日祝](?:曜日?)?)\))?"
LISTED_RUN = re.compile(_LISTED_DAY + r"(?:\s*[、,・]?\s*" + _LISTED_DAY + r")*")
# 並びの 1 つの日付 (月・日・かっこの曜日)。かっこの曜日は、呼ぶ側が期間で決めた年の曜日と合うか確かめる (合わなければ止める)
LISTED_ONE = re.compile(r"(\d{1,2})月(\d{1,2})日(?:\s*\(([月火水木金土日祝])(?:曜日?)?\))?")
# 地区の見出しにしない言葉 (「各地区のごみ収集日は下記のとおりです」「注意事項」)。期間・版の見出し (「上半期」「4月～9月」「以降」) も
# 地区にしない (ページの地区のまま。節ごとに違う一覧・規則は足し合わせずに止める)
GENERIC_HEAD = re.compile(r"お問い合わせ|問い合わせ|お問合せ|問合せ|ごみ|ゴミ|資源|収集|回収|分別|について|下記|とおり|通り|お知らせ|方法|"
                          r"一覧|カレンダー|日程|令和|平成|年度|注意|案内|変更|お問|問い?合|事項|その他|よくある|リンク|"
                          r"出し方|捨て方|分け方|ルール|マナー|品目|種類|曜日|ごと|別|"
                          r"上半期|下半期|半期|前期|後期|上期|下期|\d{1,2}月|期間|以降|以前|再掲|各地区|各地域|地区|地域|"
                          # 版・期間・条件の言葉 (「旧日程」「新日程」「当面」「改定後」「雨天時」)。ページの地区のまま読み、節ごとに違えば止める
                          r"追加分|追加|改定後|改定前|改定|改正後|改正|当面|試行|臨時|暫定|最新|現行|今後|従来|予備日|雨天時|振替日|"
                          r"夏季|冬季|旧|新")
# 総称の言葉を除いたあとに残ってよいもの (数字・記号・助詞・「年」「日」「現在」などのつなぎ)。地名の字が残れば地区の見出し
_GENERIC_GLUE = re.compile(r"[\W\d_年月日度第回号版改定現在各のをにはでとがもへやですます及びおよび]+")


def _generic_heading(h):
    """見出しが総称の言葉だけでできているか (閉じた形)。「西町（9月改定）」「西町地区のごみ収集日」のように地名の部分が残れば False
    (地区の見出しのまま。地名でない残りは呼ぶ側で但し書きになる。5 回目までの止め方に戻す)"""
    return not _GENERIC_GLUE.sub("", GENERIC_HEAD.sub("", nfkc(h)))


def _text_label_ok(label):
    """品目の名前として読んでよいか (辞書の分類に当たる名前か「資源」。「ごみ」だけの札・日時や場所の札は外す)"""
    t = nfkc(label).strip()
    return bool(t) and categories.is_category_header(t) and categories.coarse(t) != ["other"] and \
        not re.search(r"日時|時間|場所|対象|料金|方法|注意|問い?合", t)


# 総称の見出しのうち、版・期間・条件を言うもの (「旧日程」「4月～9月」「上半期」「雨天時」「収集日程の変更」)。ページの地区のまま読むが、
# 見出しはその節の品目の但し書きにする (節だけの規則・一覧で、自治体全体の 1 年を答えない)
COND_HEAD = re.compile(r"上半期|下半期|半期|前期|後期|上期|下期|\d{1,2}月|期間|以降|以前|追加|改定|改正|当面|試行|臨時|暫定|今後|従来|"
                       r"予備日|雨天時|振替日|夏季|冬季|旧|新|変更")


def _own_stripped(h, own_names=()):
    """見出しから自治体そのものの名前 (「青梅市」) を除く (自治体の名前は地区の名前にしない)"""
    t = nfkc(h)
    for n in own_names or ():
        if n:
            t = t.replace(nfkc(n), "")
    return t


def _heading_scope(heading_path, own_names=()):
    """行の見出しの道すじ → (地区の見出しか None (ページの地区), 但し書きにする見出しのリスト)。近い見出しから上へ見る:
    - 品目の名前の見出しは飛ばす
    - 地名の部分がある見出し (地区の文法 members.place_part。「西町」「藤蔵・中島」「西町（9月改定）」) はその地区 (地名でない残りは
      呼ぶ側で但し書きになる)
    - 総称の言葉だけの見出し (_generic_heading) は地区にしない。版・期間・条件の言葉 (COND_HEAD) があれば見出しを但し書きに
    - 地名と読めず総称でもない見出し (「祝日のある週」「変更前」) は地区にしない: 見出しを但し書きにする
    総称・地名でない見出しのあとも上の見出しを見る (「東町」の下の「収集日」「祝日のある週」は東町)。上に地区の見出しが無ければ
    ページの地区。ページの地区の同じ品目は、但し書きの見出しで止まる (節ごとに違う規則は multiple)。
    3 つ目は、地区の見出しの地名のあとの残りが条件・版の言葉か (「東町（祝日の週）」「西町（9月改定）」。呼ぶ側がページの地区の同じ品目も止める)"""
    quals = []
    for h in reversed(list(heading_path or ())):
        if _text_label_ok(h):
            continue
        t = _own_stripped(h, own_names)
        if _generic_heading(t):
            if COND_HEAD.search(t):
                quals.append(h)
            continue
        if members.place_part(t):
            rest = members.place_rest(t)
            return h, quals, bool(rest) and (not _generic_heading(rest) or bool(COND_HEAD.search(rest)))
        quals.append(h)
    return None, quals, False


def _district_heading(heading_path, own_names=()):
    """行の見出しの道すじ → 地区の見出し (地名の部分がある、いちばん近い見出し) か None (ページの地区)。_heading_scope の地区"""
    return _heading_scope(heading_path, own_names)[0]


def read_text(lines, nth_def=None, district_label=None, own_names=()):
    """型 C: 本文の範囲の行 (content.Line。表の中の行は除く) → 読み (表の読みと同じ形。向きは text_sections)。
    見出し (h1〜h6・【】・太字だけの行) か行の頭の「品目：」で区切り、規則の文法 (§3.7) で読む。
    - 地区: 品目の名前でない、いちばん近い見出しが地区の名前らしければ (川北「藤蔵・中島・三反田…」) その地区。無ければページの地区
      (district_label か自治体)。地区ごとに 1 行
    - 規則として読むのは、行が曜日・日付を含み、rules.parse が規則を返すものだけ。読み残しは rest (呼ぶ側が年末年始・祝日の文として
      使えなければ、使われなかった但し書き)。同じ地区・品目の規則が別の節から 2 つ以上来て違えば multiple (毎週どうしでもまとめない)
    - 日付の一覧「4月21日,5月19日,6月9日…」(規則の頭から続く 3 つ以上の日付の並び。LISTED_RUN) は listed (年は呼ぶ側が期間で
      決める)。読むのは並びの日付だけで、並びの後ろは listed_rest (呼ぶ側が cell_notes と関門に通す)。一覧は (月, 日) で、かっこの曜日は
      listed_wd (同じ並び。無ければ None。呼ぶ側が決めた年の曜日と合わなければ止める)。
      同じ地区・品目の一覧が別の節 (旧日程・新日程) から来て違えば足し合わせない: listed_conflict (呼ぶ側が裁定待ちにする)
    - 行の所 (spans・listed_spans) には、行 (line) と行の中の位置 (start・end) も持たせる (呼ぶ側は、その所の中の文を文の同一性で見る。
      文字の一部の一致では見ない)
    - 品目の出し方は行と見出しの言葉で決める (持込 → dropoff。日付の系列にしない)
    - 7 回目: 地区の見出しは地区の文法で地名の部分が読めるものだけ (_heading_scope)。地名と読めない見出し・版や期間の言葉の見出しの
      節の品目は heading_quals (品目 → 見出し)。呼ぶ側が資料の中のその品目の系列すべてを止める但し書きにする (ページの地区の同じ品目も)"""
    out = {"orientation": "text_sections", "header": [], "roles": [], "n_head": 0, "categories": {}, "rows": [], "grid": [],
           "structure": {"ok": True}, "selector": None, "notes": [], "heading_quals": {}}
    per, listed, spans, lspans, lrest, lsec, page_q = {}, {}, {}, {}, {}, {}, {}
    for ln in lines:
        if ln.is_heading or ln.in_table:
            continue
        dh, hq, cond = _heading_scope(ln.heading_path, own_names)
        sec = tuple(ln.heading_path or ())
        off = 0
        for part in ln.text.split("\n"):
            start, off = off, off + len(part) + 1
            if not part.strip():
                continue
            sp = {"selector": ln.selector, "text": part, "line": ln, "start": start, "end": start + len(part)}
            t = nfkc(part).strip()
            label, rule = None, None
            m = TEXT_LABEL.match(t)
            if m and _text_label_ok(m.group("b") or m.group("l")):
                label, rule = (m.group("b") or m.group("l")).strip(), m.group("rule").strip()
            elif ln.heading_path and _text_label_ok(ln.heading_path[-1]):
                label, rule = nfkc(ln.heading_path[-1]).strip(), t
            if not label or not RULEISH.search(rule):
                continue
            key = (dh, label)
            for h in hq:
                if h not in out["heading_quals"].setdefault(label, []):
                    out["heading_quals"][label].append(h)
            if dh and cond and dh not in page_q.setdefault(label, []):
                page_q[label].append(dh)                # 条件つきの地区の節: ページの地区の同じ品目も止める
            mr = LISTED_RUN.match(rule)
            if mr and len(MD_LIST.findall(mr.group(0))) >= 3:
                days = [(int(a), int(b), w or None) for a, b, w in LISTED_ONE.findall(mr.group(0))]
                listed.setdefault(key, []).extend(days)
                lsec.setdefault(key, {}).setdefault(sec, []).extend(days)
                if rule[mr.end():].strip():
                    lrest.setdefault(key, []).append(rule[mr.end():].strip())
                lspans.setdefault(key, []).append(sp)
                continue
            p = rules.parse(rule, nth_def)
            if p.status in ("unparsed", "on_demand") or not p.irs:
                continue
            per.setdefault(key, []).append((p, ln, part, sec))
            spans.setdefault(key, []).append(sp)
    rows = {}
    for key in list(per) + [k for k in listed if k not in per]:
        dh, label = key
        if dh not in rows:
            rows[dh] = {"row": len(rows), "district_text": dh or district_label or "", "district_from": "heading" if dh else "page",
                        "members": None, "rules": {}, "cells": {}, "calendar_links": [], "listed": {}, "spans": {},
                        "listed_spans": {}, "listed_rest": {}, "listed_wd": {}, "listed_conflict": {}}
        r = rows[dh]
        ctx = " ".join([x[2] for x in per.get(key, [])] + [label])
        out["categories"].setdefault(label, {"col": None, **categories.category(label, ctx)})
        r["spans"][label] = spans.get(key, [])
        if key in listed:
            days = listed[key]
            secs = lsec.get(key, {})
            if len({tuple(sorted((a, b) for a, b, _ in v)) for v in secs.values()}) > 1:
                r.setdefault("listed_conflict", {})[label] = "同じ地区・品目の日付の一覧が別の節にあり、違う (足し合わせない)"
                days = next(iter(secs.values()))
            r["listed"][label] = [(a, b) for a, b, _ in days]
            r.setdefault("listed_wd", {})[label] = [w for _, _, w in days]
            r["listed_spans"][label] = lspans.get(key, [])
            r["listed_rest"][label] = "/".join(lrest.get(key, []))
        if key in per:
            lst = per[key]
            if len({x[3] for x in lst}) > 1 and len({json_key(x[0]) for x in lst}) > 1:
                r["rules"][label] = rules.Parsed(" / ".join(x[2] for x in lst), [i for x in lst for i in x[0].irs], "multiple",
                                                 notes=["同じ地区・品目の規則が別の節にあり、違う"])
            else:
                r["rules"][label] = _combine([x[0] for x in lst])
            r["cells"][label] = [x[1].selector for x in lst]
    if None in rows:
        rows[None]["page_quals"] = {k: v for k, v in page_q.items() if k in rows[None]["rules"] or k in rows[None]["listed"]}
    out["rows"] = list(rows.values())
    if not out["rows"]:
        out["orientation"] = "other"
    return out


def json_key(parsed):
    """規則の読みを比べるための鍵 (状態と IR)"""
    import json
    return json.dumps([parsed.status, [rules.canon(i) for i in parsed.irs]], ensure_ascii=False, sort_keys=True)


def read_table(table, nth_def=None, lg_code=None, district_label=None):
    """HTML の表の要素 → 読み (dict)。形の確かめ (structure)・見出し・向き・行ごとの地区と規則・マスの格子 (grid)"""
    st = structure(table)
    grid, _ = spec_grid(table)
    n_head = header_rows(grid)
    rd = read_grid(_grid_texts(grid), n_head, grid, nth_def, lg_code, district_label)
    cap = table.find("caption")
    rd.update(structure=st, grid=grid, caption=content._clean(cap.get_text(" ")) if cap else "",
              selector=content.css_path(table))
    return rd


def main(argv=None):
    import argparse, pathlib
    import decode, markup
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="HTML の表の形・読み・しるしを表示する")
    ap.add_argument("src")
    a = ap.parse_args(argv)
    if a.src.startswith("http"):
        import fetch_docs
        body = fetch_docs.cached(a.src)
        if body is None:
            print("キャッシュに無い:", a.src)
            return 1
    else:
        body = pathlib.Path(a.src).read_bytes()
    html = decode.decode(body, "text/html").text
    reg = content.content_region(html)
    nd, _ = rules.nth_definition([s.text for s in reg.sentences()])
    for i, t in tables(reg.root):
        rd = read_table(t, nd)
        inv = markup.inventory(t, rd, reg.text())
        print(f"表 {i} {rd['caption']!r} 向き={rd['orientation']} 形={rd['structure']} 見出し={rd.get('header')} 役目={rd.get('roles')}")
        print("   しるし:", markup.summary(inv))
        for c, v in rd.get("categories", {}).items():
            print("   品目", repr(c), v["coarse"], v["mode"])
        for r in rd.get("rows", [])[:8]:
            print("   行", r["row"], repr(r["district_text"]), [(m["unit"], m["extra"]["name_verbatim"]) for m in (r["members"] or [])],
                  {k: (p.status, p.irs) for k, p in r["rules"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
