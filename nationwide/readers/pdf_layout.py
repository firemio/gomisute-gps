"""文字の PDF の読み取り: 型 D (罫線のある表) (DESIGN.md §3.5 D・§3.6・§3.11)

  python nationwide/readers/pdf_layout.py <URL (キャッシュ) か PDF のファイル> [--page N]   # ページの型・表の形・日付の表の読み

- 文字は PyMuPDF の rawdict から 1 字ずつ (位置・大きさ・色)。回転したページは表示の向きの座標にそろえる。
  見えない字 (3pt より小さい・透明・白い地の白い字) は読まない。壊れた字 (私用の字・U+2B00〜U+2BFF の矢印の字・制御文字) は
  「読めない」にする (日高のコース番号 10〜20)。推し量りで字を当てない
- 表の形は 2 通りで作って比べる (§3.11): (1) PyMuPDF の find_tables、(2) 罫線と塗りの矩形の辺を自分で束ねた格子。
  結合したマス・行と列の数・マスの文字が違えば structure の裁定へ (LLM の票では決めない)
- 日付の表 (§3.5 D): 片方の軸に月の見出し (4月〜3月) が 6 つ以上並び、もう片方の軸に品目の名前が 2 つ以上ある表。
  マスの「8・22」「10日・24日」をその月の日にする。月をまたいで結合したマス (「月曜日」「市では収集しません。…」) は規則のマス。
  空のマスは「収集なし」と読まない (§3.6 の空のマスの決まり。日付の表の空のマスは型の決まり (b) に入らない)
- ページの型 (§3.1 の段 1): date_table (型 D の日付の表) / rule_table / month_grid (型 F の月表) / pdf_outline (G) / pdf_image (H) /
  translation (外国語の版) / other。M1 で読むのは date_table だけ
"""
import collections, io, re, sys, unicodedata

import categories, content, rules

MIN_PT = 3.0                       # これより小さい字は見えない (version.MIN_VISIBLE_PT と同じ)
TOL = 2.0                          # 罫線の位置を束ねる幅 (pt)
READER_VERSION = "code.pdf_layout@1"
FT_VERSION = "code.pdf_find_tables@1"
RULING_VERSION = "code.pdf_rulings@1"


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def squash(s):
    return re.sub(r"\s+", "", nfkc(s))


# ---- 壊れた字 ----
def broken_char(c):
    """文字の対応が壊れた字か: 私用の字・割り当ての無い字・U+2B00〜U+2BFF (日高のコース番号の矢印の字)・U+FFFD・制御文字"""
    o = ord(c)
    if c in "\t\n\r ":
        return False
    if 0x2B00 <= o <= 0x2BFF or o == 0xFFFD or o < 0x20:
        return True
    return unicodedata.category(c) in ("Co", "Cn", "Cs")


def has_broken(text):
    return any(broken_char(c) for c in text or "")


# ---- ページの字・塗り・画像 (表示の向きの座標) ----
class Char:
    __slots__ = ("c", "x0", "y0", "x1", "y1", "size", "color", "visible", "why")

    def __init__(self, c, r, size, color, visible, why):
        self.c, self.size, self.color, self.visible, self.why = c, size, color, visible, why
        self.x0, self.y0, self.x1, self.y1 = r.x0, r.y0, r.x1, r.y1

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


def _whiteish(color):
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return min(r, g, b) >= 240


class PageInfo:
    """1 ページの字・行・塗り・画像・罫線 (どれも表示の向きの座標)"""

    def __init__(self, page):
        import fitz
        self.page = page
        self.number = page.number
        self.rect = page.rect
        M = page.rotation_matrix
        self.fills, self.segments, self.occluders, self.n_curves = [], [], [], 0
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        self.n_drawings = len(drawings)
        for seq, dr in enumerate(drawings):          # 描いた順 (後の塗りが先の線を隠す)
            fill = dr.get("fill")
            stroke = dr.get("color")
            items = dr.get("items") or []
            has_curve = any(it[0] == "c" for it in items)
            self.n_curves += sum(1 for it in items if it[0] == "c")
            opaque = fill is not None and (dr.get("fill_opacity") in (None, 1, 1.0))
            for it in items:
                if it[0] in ("re", "qu"):
                    r = (fitz.Rect(it[1]) if it[0] == "re" else fitz.Quad(it[1]).rect) * M
                    if fill and min(fill) < 0.94:
                        self.fills.append((r, tuple(round(x, 3) for x in fill)))
                    if opaque and not has_curve:
                        self.occluders.append((r, seq))
                    if (fill or stroke) and not has_curve:
                        self._rect_edges(r, seq)
                elif it[0] == "l" and stroke is not None and not has_curve:
                    p1, p2 = fitz.Point(it[1]) * M, fitz.Point(it[2]) * M
                    self._segment(p1.x, p1.y, p2.x, p2.y, seq)
        self.images = []
        try:
            for im in page.get_image_info():
                self.images.append(fitz.Rect(im["bbox"]) * M)
        except Exception:
            pass
        self.chars, self.lines = [], []
        self.n_overlapped = 0
        seen = set()
        raw = page.get_text("rawdict")
        for b in raw.get("blocks", []):
            for ln in b.get("lines", []):
                lchars = []
                sizes = []
                for sp in ln.get("spans", []):
                    size, color, alpha = sp.get("size", 0), sp.get("color", 0), sp.get("alpha", 255)
                    sizes.append(size)
                    for ch in sp.get("chars", []):
                        r = fitz.Rect(ch["bbox"]) * M
                        k = (ch["c"], round(r.x0 * 2), round(r.y0 * 2))
                        if ch["c"].strip() and k in seen:         # 同じ位置に重なった同じ字は 1 つにする (国立・滑川「可燃ごみ可燃ごみ」)
                            self.n_overlapped += 1
                            continue
                        seen.add(k)
                        why = None
                        if alpha == 0:
                            why = "transparent"
                        elif size < MIN_PT:
                            why = "too_small"
                        elif not r.intersects(self.rect):
                            why = "off_page"
                        elif _whiteish(color):
                            if not self.fill_at((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2):
                                why = "white_on_white"
                        c = Char(ch["c"], r, size, color, why is None, why)
                        self.chars.append(c)
                        lchars.append(c)
                if lchars:
                    vis = [c for c in lchars if c.visible]
                    text = "".join(c.c for c in lchars)
                    if not text.strip():
                        continue
                    bb = fitz.Rect(min(c.x0 for c in lchars), min(c.y0 for c in lchars),
                                   max(c.x1 for c in lchars), max(c.y1 for c in lchars))
                    self.lines.append({"text": text, "bbox": bb, "size": max(sizes) if sizes else 0,
                                       "visible": len(vis) >= len([c for c in lchars if c.c.strip()]) / 2,
                                       "visible_text": "".join(c.c for c in vis)})
        self.lines.sort(key=lambda l: (round(l["bbox"].y0 / 3), l["bbox"].x0))

    BUCKET = 64.0

    def fill_at(self, x, y):
        """点 (x, y) の下に白くない塗りがあるか (塗りを 64pt の升に分けて引く)"""
        if not hasattr(self, "_fill_grid"):
            g = collections.defaultdict(list)
            for r, _ in self.fills:
                for i in range(int(r.x0 // self.BUCKET), int(r.x1 // self.BUCKET) + 1):
                    for j in range(int(r.y0 // self.BUCKET), int(r.y1 // self.BUCKET) + 1):
                        g[(i, j)].append((r.x0, r.y0, r.x1, r.y1))
            self._fill_grid = g
        for x0, y0, x1, y1 in self._fill_grid.get((int(x // self.BUCKET), int(y // self.BUCKET)), ()):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return True
        return False

    def _segment(self, x0, y0, x1, y1, seq):
        if abs(y1 - y0) <= 1.0 and abs(x1 - x0) >= 4:
            self.segments.append(("h", (y0 + y1) / 2, min(x0, x1), max(x0, x1), seq))
        elif abs(x1 - x0) <= 1.0 and abs(y1 - y0) >= 4:
            self.segments.append(("v", (x0 + x1) / 2, min(y0, y1), max(y0, y1), seq))

    def _rect_edges(self, r, seq):
        if r.height <= 2.0 and r.width >= 4:                     # 細い矩形は横線
            self.segments.append(("h", (r.y0 + r.y1) / 2, r.x0, r.x1, seq))
            return
        if r.width <= 2.0 and r.height >= 4:                     # 細い矩形は縦線
            self.segments.append(("v", (r.x0 + r.x1) / 2, r.y0, r.y1, seq))
            return
        if r.width < 4 or r.height < 4:
            return
        self.segments += [("h", r.y0, r.x0, r.x1, seq), ("h", r.y1, r.x0, r.x1, seq), ("v", r.x0, r.y0, r.y1, seq),
                          ("v", r.x1, r.y0, r.y1, seq)]

    def visible_segments(self, box):
        """矩形の中の線のうち、後に描いた塗りに隠れていない部分 → [("h"|"v", 位置, 始め, 終わり)]"""
        occ = [(r, s) for r, s in self.occluders if r.intersects(box)]
        out = []
        for k, p, a, b, seq in self.segments:
            if k == "h" and not (box.y0 <= p <= box.y1 and b > box.x0 and a < box.x1):
                continue
            if k == "v" and not (box.x0 <= p <= box.x1 and b > box.y0 and a < box.y1):
                continue
            parts = [(a, b)]
            for r, s in occ:
                if s <= seq:
                    continue
                lo, hi, inside = (r.x0, r.x1, r.y0 + 0.5 < p < r.y1 - 0.5) if k == "h" else (r.y0, r.y1, r.x0 + 0.5 < p < r.x1 - 0.5)
                if not inside:
                    continue
                nxt = []
                for s0, s1 in parts:
                    if hi <= s0 or lo >= s1:
                        nxt.append((s0, s1))
                        continue
                    if lo > s0:
                        nxt.append((s0, lo))
                    if hi < s1:
                        nxt.append((hi, s1))
                parts = nxt
                if not parts:
                    break
            out += [(k, p, s0, s1) for s0, s1 in parts if s1 - s0 >= 1.0]
        return out

    def visible_text(self):
        return "\n".join(l["visible_text"] for l in self.lines if l["visible_text"].strip())

    def text_in(self, rect, visible_only=True):
        """矩形の中 (字の真ん中) の字 → 文字 (行は y で分ける)"""
        cs = [c for c in self.chars if (c.visible or not visible_only) and rect.x0 <= c.cx <= rect.x1 and rect.y0 <= c.cy <= rect.y1]
        return chars_text(cs)

    def broken_in(self, rect):
        return [c.c for c in self.chars if c.visible and rect.x0 <= c.cx <= rect.x1 and rect.y0 <= c.cy <= rect.y1 and broken_char(c.c)]


def chars_text(cs):
    """字 → 文字 (y の近い字を 1 行に、x の順。行の間は改行)"""
    if not cs:
        return ""
    cs = sorted(cs, key=lambda c: (c.cy, c.x0))
    rows, cur, base = [], [], None
    for c in cs:
        h = max(c.y1 - c.y0, 1)
        if base is None or abs(c.cy - base) <= h * 0.5:
            cur.append(c)
            base = c.cy if base is None else base
        else:
            rows.append(cur)
            cur, base = [c], c.cy
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r.sort(key=lambda c: c.x0)
        s = ""
        for i, c in enumerate(r):
            if i and c.x0 - r[i - 1].x1 > max(c.size, 1) * 0.6 and not s.endswith(" "):
                s += " "
            s += c.c
        out.append(s.strip())
    return "\n".join(x for x in out if x)


# ---- 表の形 ----
class Cell:
    __slots__ = ("r0", "c0", "r1", "c1", "bbox", "text")

    def __init__(self, r0, c0, r1, c1, bbox, text=""):
        self.r0, self.c0, self.r1, self.c1, self.bbox, self.text = r0, c0, r1, c1, bbox, text

    @property
    def span(self):
        return (self.r0, self.c0, self.r1, self.c1)

    def __repr__(self):
        return f"Cell({self.r0},{self.c0}-{self.r1},{self.c1} {self.text[:12]!r})"


class Grid:
    """表の格子: 行と列の境目、マス (結合したマスは 1 つ。at[r][c] で覆うマスを引く)"""

    def __init__(self, method, bbox, ys, xs, cells):
        self.method, self.bbox, self.ys, self.xs, self.cells = method, bbox, ys, xs, cells
        self.n_rows, self.n_cols = len(ys) - 1, len(xs) - 1
        self.at = [[None] * self.n_cols for _ in range(self.n_rows)]
        for c in cells:
            for r in range(c.r0, c.r1):
                for k in range(c.c0, c.c1):
                    if 0 <= r < self.n_rows and 0 <= k < self.n_cols:
                        self.at[r][k] = c

    def anchor(self, r, c):
        """(r, c) のマスが結合したマスの左上なら、そのマス。覆われているだけなら None"""
        x = self.at[r][c]
        return x if x is not None and (x.r0, x.c0) == (r, c) else None

    def rows_text(self):
        return [[(self.anchor(r, c).text if self.anchor(r, c) else None) for c in range(self.n_cols)] for r in range(self.n_rows)]


def _bound_index(vals, v, tol=TOL):
    for i, x in enumerate(vals):
        if abs(x - v) <= tol:
            return i
    return min(range(len(vals)), key=lambda i: abs(vals[i] - v))


def grid_find_tables(pi, table):
    """(1) find_tables の表 → Grid。マスの文字は find_tables 自身の extract (やり方 1 の文字)"""
    import fitz
    rows = table.rows
    xs, ys = set(), set()
    anchors = []
    for row in rows:
        for cb in row.cells:
            if cb is None:
                continue
            r = fitz.Rect(cb)
            xs |= {round(r.x0, 1), round(r.x1, 1)}
            ys |= {round(r.y0, 1), round(r.y1, 1)}
            anchors.append(r)
    xs, ys = _cluster(sorted(xs)), _cluster(sorted(ys))
    try:
        ext = table.extract()
    except Exception:
        ext = [[None] * len(r.cells) for r in rows]
    cells = []
    for ri, row in enumerate(rows):
        for ci, cb in enumerate(row.cells):
            if cb is None:
                continue
            r = fitz.Rect(cb)
            r0, r1 = _bound_index(ys, r.y0), _bound_index(ys, r.y1)
            c0, c1 = _bound_index(xs, r.x0), _bound_index(xs, r.x1)
            if r1 <= r0 or c1 <= c0:
                continue
            t = ext[ri][ci] if ri < len(ext) and ci < len(ext[ri]) else None
            cells.append(Cell(r0, c0, r1, c1, r, t or ""))
    return Grid("find_tables", fitz.Rect(table.bbox), ys, xs, cells)


def _cluster(vals, tol=TOL):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _covered(segs, pos, a, b, tol=TOL):
    """pos の位置の線が区間 [a, b] の 6 割以上を覆うか"""
    need = (b - a) * 0.6
    got = 0.0
    for p, s, e in segs:
        if abs(p - pos) <= tol:
            lo, hi = max(a, s), min(b, e)
            if hi > lo:
                got += hi - lo
    return got >= need


def grid_rulings(pi, bbox):
    """(2) 罫線と塗りの矩形の辺を自分で束ねた格子 → Grid か None。マスの文字は見える字を真ん中の位置で当てる"""
    import fitz
    box = fitz.Rect(bbox) + (-3, -3, 3, 3)
    segs = pi.visible_segments(box)
    hs = [(p, max(s, box.x0), min(e, box.x1)) for k, p, s, e in segs if k == "h"]
    vs = [(p, max(s, box.y0), min(e, box.y1)) for k, p, s, e in segs if k == "v"]
    W, H = bbox.width, bbox.height
    # 束ねて、表の幅・高さの 2 割以上を覆う位置だけを境目にする (挿し絵の線を外す)
    def lines_of(segs, full):
        by = collections.defaultdict(float)
        for g in _groups(segs):
            p = sum(x[0] for x in g) / len(g)
            spans = sorted((s, e) for _, s, e in g)
            tot, cur = 0.0, None
            for s, e in spans:
                if cur is None or s > cur[1]:
                    if cur:
                        tot += cur[1] - cur[0]
                    cur = [s, e]
                else:
                    cur[1] = max(cur[1], e)
            if cur:
                tot += cur[1] - cur[0]
            by[p] += tot
        return sorted(p for p, t in by.items() if t >= 0.2 * full)
    ys = lines_of(hs, W)
    xs = lines_of(vs, H)
    ys = _cluster(sorted(set([round(bbox.y0, 1), round(bbox.y1, 1)] + ys)), tol=3.0)
    xs = _cluster(sorted(set([round(bbox.x0, 1), round(bbox.x1, 1)] + xs)), tol=3.0)
    ys = [y for y in ys if bbox.y0 - 3 <= y <= bbox.y1 + 3]
    xs = [x for x in xs if bbox.x0 - 3 <= x <= bbox.x1 + 3]
    if len(ys) < 2 or len(xs) < 2:
        return None
    nr, nc = len(ys) - 1, len(xs) - 1
    parent = {(r, c): (r, c) for r in range(nr) for c in range(nc)}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    vsegs = [(p, s, e) for p, s, e in vs]
    hsegs = [(p, s, e) for p, s, e in hs]
    for r in range(nr):
        for c in range(nc - 1):
            if not _covered(vsegs, xs[c + 1], ys[r], ys[r + 1], tol=3.0):
                union((r, c), (r, c + 1))
    for r in range(nr - 1):
        for c in range(nc):
            if not _covered(hsegs, ys[r + 1], xs[c], xs[c + 1], tol=3.0):
                union((r, c), (r + 1, c))
    comps = collections.defaultdict(list)
    for k in parent:
        comps[find(k)].append(k)
    cells = []
    for ks in comps.values():
        r0, r1 = min(k[0] for k in ks), max(k[0] for k in ks) + 1
        c0, c1 = min(k[1] for k in ks), max(k[1] for k in ks) + 1
        if len(ks) != (r1 - r0) * (c1 - c0):
            return None                                   # 矩形にならない結合 (形が決まらない)
        rect = fitz.Rect(xs[c0], ys[r0], xs[c1], ys[r1])
        cells.append(Cell(r0, c0, r1, c1, rect, pi.text_in(rect)))
    return Grid("rulings", fitz.Rect(bbox), ys, xs, cells)


def _groups(segs, tol=3.0):
    out = []
    for s in sorted(segs):
        if out and s[0] - out[-1][-1][0] <= tol:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def compare_grids(g1, g2):
    """2 通りの格子 → {"ok", "issues"}。結合したマスの形 (行・列の範囲) と、マスの文字 (NFKC・空白なし) を比べる"""
    if g2 is None:
        return {"ok": False, "issues": ["罫線から格子を組めない (結合が矩形にならない・線が足りない)"]}
    issues = []
    if (g1.n_rows, g1.n_cols) != (g2.n_rows, g2.n_cols):
        issues.append(f"行と列の数が違う: find_tables {g1.n_rows}×{g1.n_cols}・罫線 {g2.n_rows}×{g2.n_cols}")
    import fitz
    for c in g1.cells:
        best, iou = None, 0.0
        for d in g2.cells:
            inter = fitz.Rect(c.bbox) & fitz.Rect(d.bbox)
            if inter.is_empty:
                continue
            u = c.bbox.get_area() + d.bbox.get_area() - inter.get_area()
            v = inter.get_area() / u if u else 0
            if v > iou:
                best, iou = d, v
        if best is None or iou < 0.8:
            issues.append(f"マス {c.span} ({squash(c.text)[:12]!r}) に当たるマスが罫線の格子に無い")
            continue
        if squash(c.text) != squash(best.text):
            issues.append(f"マス {c.span} の文字が違う: {squash(c.text)[:20]!r} / {squash(best.text)[:20]!r}")
    return {"ok": not issues, "issues": issues[:20], "n_issues": len(issues)}


# ---- 日付の表 ----
MONTH_RE = re.compile(r"^(\d{1,2})月")
DAY_LIST = re.compile(r"^(\d{1,2})日?(?:[・･,、，/](\d{1,2})日?)*$")
WD_TEXT = re.compile(r"[月火水木金土日](?:曜日?)")
KEY_DISTRICT = re.compile(r"^(?:行政区名?|地区名?|地域名?|町名|町丁名|対象地区|収集地区|自治会名?)$")
KEY_COURSE = re.compile(r"^(コース番号|コース|地区番号|区分)(.*)$")
ASCII_LINE = re.compile(r"^[A-Za-z0-9 &()'.,/\-:;]+$")


def month_of(text):
    t = squash(text)
    m = MONTH_RE.match(t)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    return None


def japanese_lines(text):
    """マスの文字の、英語の訳の行を除いた行 (三鷹「火曜日/金曜日\\nEvery week\\nTuesday & Friday」) → (日本語の行, 外した行)"""
    keep, drop = [], []
    for ln in (text or "").split("\n"):
        s = nfkc(ln).strip()
        if not s:
            continue
        english = ASCII_LINE.match(s) and re.search(r"[A-Za-z]{2}", s)
        punct = re.fullmatch(r"[&/・,.\-()（）\s]+", s)            # 訳の行の間の「&」だけの行
        (drop if english or punct else keep).append(ln.strip())
    return keep, drop


def parse_days(text):
    """日のマスの文字 → 日のリスト / [] (空) / None (日の並びでない)"""
    t = squash(text)
    if not t:
        return []
    if not DAY_LIST.match(t):
        return None
    ds = [int(x) for x in re.findall(r"\d{1,2}", t)]
    if any(not 1 <= d <= 31 for d in ds):
        return None
    return ds


def _fiscal_seq(ms):
    """月の並びが続いた月 (12 の次は 1) か"""
    return all((b - a) % 12 == 1 for a, b in zip(ms, ms[1:]))


def month_axis(g):
    """格子 → 月の軸 ("row", 行の番号, {列: 月}) か ("col", 列の番号, {行: 月}) か None。月の見出しが 6 つ以上、続いた月で並ぶもの"""
    best = None
    for r in range(g.n_rows):
        ms = {}
        for c in range(g.n_cols):
            a = g.anchor(r, c)
            if a and a.c1 - a.c0 == 1:
                m = month_of(a.text)
                if m:
                    ms[c] = m
        if len(ms) >= 6 and len(set(ms.values())) == len(ms) and _fiscal_seq([ms[c] for c in sorted(ms)]):
            if best is None or len(ms) > len(best[2]):
                best = ("row", r, ms)
    for c in range(g.n_cols):
        ms = {}
        for r in range(g.n_rows):
            a = g.anchor(r, c)
            if a and a.r1 - a.r0 == 1:
                m = month_of(a.text)
                if m:
                    ms[r] = m
        if len(ms) >= 6 and len(set(ms.values())) == len(ms) and _fiscal_seq([ms[r] for r in sorted(ms)]):
            if best is None or len(ms) > len(best[2]):
                best = ("col", c, ms)
    return best


def _label_text(text):
    """見出しのマスの文字 → 品目の名前 (英語の訳の行を除き、品目の名前に当たる行だけ。三鷹「指定収集袋\\n燃やせるごみ」の
    「指定収集袋」の札は名前に入れない)。品目に当たる行が無ければ日本語の行のすべて"""
    keep, _ = japanese_lines(text)
    names = [k for k in keep if categories.is_category_header(k)]
    return " ".join(names or keep).strip()


def read_date_table(g, pi=None):
    """格子 → 日付の表の読み (dict) か None (日付の表でない)。
    {"orientation": "months_in_row" / "months_in_col", "months": {軸の位置: 月}, "categories": [{"label", "pos", "note", "cells": [...]}],
     "rule_cells": [...], "header": {...}, "blank_cells": [...], "unparsed_cells": [...], "broken_cells": [...]}"""
    ax = month_axis(g)
    if ax is None:
        return None
    kind, pos, months = ax
    out = {"orientation": "months_in_row" if kind == "row" else "months_in_col", "axis": pos, "months": months,
           "categories": [], "rule_cells": [], "header": {"course": None, "district": None, "other": []},
           "blank_cells": [], "unparsed_cells": [], "broken_cells": [], "translations": []}
    first_m = min(months)
    last_m = max(months)

    def cell_rec(a, month=None):
        rec = {"span": list(a.span), "bbox": [round(v, 1) for v in a.bbox], "text": a.text}
        if month is not None:
            rec["month"] = month
        return rec

    if kind == "row":
        hr = pos
        # 見出しの行より上: コース番号・行政区名など
        for r in range(hr):
            row = [g.anchor(r, c) for c in range(g.n_cols)]
            cells = [a for a in row if a and squash(a.text)]
            i = 0
            while i < len(cells):
                t = squash(cells[i].text)
                mk = KEY_COURSE.match(t)
                if KEY_DISTRICT.match(t) and i + 1 < len(cells):
                    out["header"]["district"] = {"key": t, **cell_rec(cells[i + 1]), "broken": has_broken(cells[i + 1].text)}
                    i += 2
                    continue
                if mk:
                    out["header"]["course"] = {"key": mk.group(1), **cell_rec(cells[i]), "broken": has_broken(cells[i].text),
                                               "value": mk.group(2) or (squash(cells[i + 1].text) if i + 1 < len(cells) and
                                                                         not KEY_DISTRICT.match(squash(cells[i + 1].text)) else "")}
                    i += 1
                    continue
                out["header"]["other"].append(cell_rec(cells[i]))
                i += 1
        # 月の列より左の列: 品目の名前 (行ごと) と、行をまたぐ規則のマス (見出しの行のその列の名前の品目)
        left = [c for c in range(g.n_cols) if c < min(months)]
        for r in range(hr + 1, g.n_rows):
            lab = None
            for c in reversed(left):
                a = g.at[r][c]
                if a is None or not squash(a.text):
                    continue
                if a.r1 - a.r0 == 1:
                    lab = a
                break
            data = [(c, months[c], g.at[r][c]) for c in sorted(months)]
            if lab is None:
                continue
            _cat_from(out, lab, r, data, g, cell_rec)
        for c in left:
            head = g.anchor(hr, c)
            if not head or not squash(head.text):
                continue
            below = g.at[hr + 1][c] if hr + 1 < g.n_rows else None
            if below is not None and below.r1 - below.r0 >= 2 and categories.is_category_header(_label_text(head.text)):
                out["rule_cells"].append({"category_label": _label_text(head.text), **cell_rec(below), "head": cell_rec(head)})
    else:
        mc = pos
        fr = min(months)
        right = [c for c in range(g.n_cols) if c > mc]
        for c in right:
            heads = []
            for r in range(fr):
                a = g.at[r][c]
                if a is not None and (a.r0, a.c0) == (r, a.c0) and squash(a.text):
                    heads.append((r, a))
            lab = next((a for r, a in heads if categories.is_category_header(_label_text(a.text))), None)
            if lab is None:
                continue
            note = heads[-1][1] if heads and heads[-1][1] is not lab else None
            data = [(r, months[r], g.at[r][c]) for r in sorted(months)]
            _cat_from(out, lab, c, data, g, cell_rec, note=note)
    if not out["categories"] and not out["rule_cells"]:
        return None
    if len({x["label"] for x in out["categories"]} | {x["category_label"] for x in out["rule_cells"]}) < 2:
        return None
    return out


def _cat_from(out, lab, pos, data, g, cell_rec, note=None):
    """品目の 1 行 (か 1 列) を読む。月ごとのマスが日の並びか、月をまたぐ規則のマスか"""
    label = _label_text(lab.text)
    if not categories.is_category_header(label):
        return
    keep_l, drop_l = japanese_lines(lab.text)
    rec = {"label": label, "pos": pos, "label_cell": cell_rec(lab), "label_broken": has_broken(lab.text), "cells": [],
           "note": None, "translations": drop_l}
    if note is not None:
        nk, nd = japanese_lines(note.text)
        rec["note"] = {**cell_rec(note), "japanese": " ".join(nk), "translations": nd}
    spans = collections.Counter()
    for k, m, a in data:
        if a is None:
            continue
        span_months = (a.r1 - a.r0) if out["orientation"] == "months_in_col" else (a.c1 - a.c0)
        spans[id(a)] = span_months
    seen = set()
    for k, m, a in data:
        if a is None:
            rec["cells"].append({"month": m, "status": "missing"})
            continue
        if spans[id(a)] >= 2:
            if id(a) not in seen:
                seen.add(id(a))
                keep, drop = japanese_lines(a.text)
                out["rule_cells"].append({"category_label": label, **cell_rec(a), "japanese": "\n".join(keep), "translations": drop,
                                          "months_spanned": spans[id(a)], "pos": pos})
            rec["cells"].append({"month": m, "status": "merged", "span": list(a.span)})
            continue
        if has_broken(a.text):
            rec["cells"].append({**cell_rec(a, m), "status": "broken"})
            out["broken_cells"].append({"label": label, "month": m, "text": a.text})
            continue
        ds = parse_days(a.text)
        if ds == []:
            rec["cells"].append({**cell_rec(a, m), "status": "blank", "days": []})
            out["blank_cells"].append({"label": label, "month": m, "span": list(a.span)})
        elif ds is None:
            rec["cells"].append({**cell_rec(a, m), "status": "text"})
            out["unparsed_cells"].append({"label": label, "month": m, "text": a.text})
        else:
            rec["cells"].append({**cell_rec(a, m), "status": "days", "days": ds})
    out["categories"].append(rec)


# ---- ページの型 (段 1) ----
KANA = re.compile(r"[ぁ-んァ-ヶー]")
CJK = re.compile(r"[一-鿿]")
WEEK_ROW = re.compile(r"日\s*月\s*火\s*水\s*木\s*金\s*土|月\s*火\s*水\s*木\s*金\s*土\s*日|SUN\s*MON\s*TUE|Sun\s*Mon\s*Tue", re.I)


def is_japanese(text):
    """日本語の版か (仮名が漢字と仮名の 3% 以上)。中国語・韓国語・英語の版を外す"""
    t = text or ""
    k, c = len(KANA.findall(t)), len(CJK.findall(t))
    return (k + c) > 20 and k >= 0.03 * (k + c)


def classify_page(pi, tables=None):
    """ページ → {"kind", "why", ...}。tables は find_tables の表 (無ければここで探す)"""
    text = pi.visible_text()
    n_chars = len(squash(text))
    img_area = sum(r.get_area() for r in pi.images)
    page_area = pi.rect.get_area() or 1
    if n_chars < 20:
        if img_area > 0.4 * page_area:
            return {"kind": "pdf_image", "why": "文字層がほとんど無く、画像がページの 4 割以上"}
        if pi.n_drawings > 200:
            return {"kind": "pdf_outline", "why": "文字層がほとんど無く、図形が多い (輪郭線の字)"}
        return {"kind": "other", "why": "文字層がほとんど無い"}
    if not is_japanese(text):
        return {"kind": "translation", "why": "仮名がほとんど無い (外国語の版)"}
    nums = {int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", nfkc(text)) if 1 <= int(x) <= 31}
    if tables:
        for t in tables:
            if t.get("date_table"):
                return {"kind": "date_table", "why": "月の見出しと品目の名前の軸がある罫線の表"}
    weekday_header = bool(WEEK_ROW.search(re.sub(r"\s+", "", nfkc(text)))) or \
        sum(1 for w in "日月火水木金土" if re.search(rf"(?:^|\s){w}(?:曜日?)?(?:\s|$)", nfkc(text), re.M)) >= 6
    if weekday_header and len(nums) >= 28:
        return {"kind": "month_grid", "why": "曜日の見出しと 1〜31 の日の数が並ぶ (月表。型 F)"}
    n_month_heads = len(set(re.findall(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)", nfkc(text))))
    if weekday_header and n_month_heads >= 2:
        return {"kind": "month_grid", "why": "曜日の見出しと月の見出しが並ぶ (日の数は字でない。輪郭線・色の月表。型 F・G)"}
    if len(nums) >= 28 and re.search(r"\d{1,2}\s*月", nfkc(text)):
        return {"kind": "month_grid", "why": "月の見出しと 1〜31 の日の数が並ぶ (月表か日の並び。型 F)"}
    if tables and any(t.get("rule_like") for t in tables):
        return {"kind": "rule_table", "why": "曜日・第N の規則の表 (M1 ではまだ読まない)"}
    return {"kind": "other", "why": "日程の表が見つからない"}


def analyze_page(page, want_rulings=True):
    """1 ページ → {"page", "kind", "tables": [{"index", "bbox", "grid", "rulings", "structure", "date_table"}], "info"}"""
    pi = PageInfo(page)
    tabs = []
    # 月の見出しが 6 つ以上無いページは、日付の表になりえないので表を探さない (速さのため)
    if len(set(re.findall(r"(?<!\d)(\d{1,2})\s*月", nfkc(pi.visible_text())))) >= 6:
        try:
            tabs = page.find_tables().tables
        except Exception:
            tabs = []
    res = []
    for i, t in enumerate(tabs):
        try:
            g1 = grid_find_tables(pi, t)
        except Exception:
            continue
        # 読みに使う字は、やり方 1 の形に見える字を当て直したもの (壊れた字・見えない字を自分で見るため)
        g1_mine = Grid("find_tables+chars", g1.bbox, g1.ys, g1.xs,
                       [Cell(c.r0, c.c0, c.r1, c.c1, c.bbox, pi.text_in(c.bbox)) for c in g1.cells])
        dtab = read_date_table(g1_mine, pi)
        rule_like = False
        if dtab is None:
            txt = " ".join(c.text for c in g1_mine.cells)
            rule_like = len(re.findall(r"[月火水木金土日]曜", nfkc(txt))) >= 3
        rec = {"index": i, "bbox": g1.bbox, "grid": g1_mine, "grid_ft": g1, "date_table": dtab, "rule_like": rule_like}
        if dtab is not None and want_rulings:
            g2 = grid_rulings(pi, g1.bbox)
            rec["rulings"] = g2
            rec["structure"] = compare_grids(g1, g2)
            if g2 is not None:
                rec["structure_text"] = compare_grids(g1_mine, g2)
        res.append(rec)
    kind = classify_page(pi, res)
    return {"page": page.number, "kind": kind["kind"], "why": kind["why"], "tables": res, "info": pi}


# ---- 表のまわりの文 (注意の枠) ----
def notes_for(pi, tables):
    """表の外の見える行 → [(行, 表の番号か None)]。表のすぐ下 (40pt 以内) で横が重なる行はその表の注、ほかはページの文"""
    import fitz
    out = []
    boxes = [(t["index"], t["bbox"]) for t in tables]
    for ln in pi.lines:
        if not ln["visible"] or not ln["visible_text"].strip():
            continue
        bb = ln["bbox"]
        c = fitz.Point((bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2)
        if any(b.contains(c) for _, b in boxes):
            continue
        owner, dist = None, 1e9
        for i, b in boxes:
            ov = min(bb.x1, b.x1) - max(bb.x0, b.x0)
            if ov <= 0.5 * min(bb.width, b.width):
                continue
            d = bb.y0 - b.y1
            if -2 <= d <= 40 and d < dist:
                owner, dist = i, d
        out.append((ln, owner))
    return out


# ---- 2 つ目の読みに渡す画像 (番号つきの枠) ----
def render_grid_png(page, grid, dpi=150, margin=36, extra=None):
    """表の画像に、マスの区切り (赤い線) と、上の余白に列の番号 C1…、左の余白に行の番号 R1… を描く → PNG のバイト列。
    番号は表の外に書くので、マスの数字を隠さない (§3.10 の番号つきの枠)。extra (表の上の題の矩形) があれば、そこまで写す"""
    import fitz
    from PIL import Image, ImageDraw, ImageFont
    clip = fitz.Rect(grid.bbox) + (-2, -2, 2, 2)
    if extra is not None:
        clip |= fitz.Rect(extra) + (-2, -2, 2, 2)
    clip &= page.rect
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    s = dpi / 72.0
    W, H = im.size
    canvas = Image.new("RGB", (W + margin, H + margin), "white")
    canvas.paste(im, (margin, margin))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    X = lambda x: margin + (x - clip.x0) * s
    Y = lambda y: margin + (y - clip.y0) * s
    for c in grid.cells:
        d.rectangle([X(c.bbox.x0), Y(c.bbox.y0), X(c.bbox.x1), Y(c.bbox.y1)], outline=(220, 0, 0), width=1)
    for j in range(grid.n_cols):
        cx = (X(grid.xs[j]) + X(grid.xs[j + 1])) / 2
        d.text((cx - 8, 6), f"C{j + 1}", fill=(0, 0, 200), font=font)
    for i in range(grid.n_rows):
        cy = (Y(grid.ys[i]) + Y(grid.ys[i + 1])) / 2
        d.text((2, cy - 7), f"R{i + 1}", fill=(0, 0, 200), font=font)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def merged_desc(grid):
    """結合したマスの説明 (文で渡す。§3.10「表の形は、確かめ済みのものを文で渡す」)"""
    out = []
    for c in grid.cells:
        if c.r1 - c.r0 > 1 or c.c1 - c.c0 > 1:
            out.append(f"R{c.r0 + 1}〜R{c.r1}・C{c.c0 + 1}〜C{c.c1} は 1 つのマス (R{c.r0 + 1}C{c.c0 + 1} として読む)")
    return out


# ---- 入口 ----
def open_pdf(body):
    import fitz
    fitz.TOOLS.mupdf_display_errors(False)
    return fitz.open(stream=body, filetype="pdf")


def analyze(body, max_pages=40):
    """PDF のバイト列 → (ページの解析のリスト, 文書)"""
    doc = open_pdf(body)
    pages = []
    for p in list(doc)[:max_pages]:
        pages.append(analyze_page(p))
    return pages, doc


def summary(pages):
    """ページの解析 → 見るための要約 (dict)"""
    out = []
    for pg in pages:
        tabs = []
        for t in pg["tables"]:
            d = t["date_table"]
            tabs.append({"index": t["index"], "bbox": [round(v) for v in t["bbox"]],
                         "size": [t["grid"].n_rows, t["grid"].n_cols],
                         "structure": (t.get("structure") or {}).get("ok"),
                         "date_table": None if d is None else {
                             "orientation": d["orientation"], "months": sorted(d["months"].values()),
                             "categories": [c["label"] for c in d["categories"]],
                             "rule_cells": [(r["category_label"], squash(r.get("japanese") or r["text"])[:20]) for r in d["rule_cells"]],
                             "course": (d["header"]["course"] or {}).get("text"), "district": (d["header"]["district"] or {}).get("text"),
                             "blank": len(d["blank_cells"]), "unparsed": len(d["unparsed_cells"]), "broken": len(d["broken_cells"])}})
        out.append({"page": pg["page"] + 1, "kind": pg["kind"], "why": pg["why"], "tables": tabs})
    return out


def main(argv=None):
    import argparse, json, pathlib
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="文字の PDF の型 D の読み取り (表の形・日付の表)")
    ap.add_argument("src")
    ap.add_argument("--page", type=int, default=None, help="1 から")
    a = ap.parse_args(argv)
    if a.src.startswith("http"):
        import fetch_docs
        body = fetch_docs.cached(a.src)
        if body is None:
            print("キャッシュに無い:", a.src)
            return 1
    else:
        body = pathlib.Path(a.src).read_bytes()
    doc = open_pdf(body)
    pages = []
    for p in doc:
        if a.page and p.number + 1 != a.page:
            continue
        pages.append(analyze_page(p))
    print(json.dumps(summary(pages), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
