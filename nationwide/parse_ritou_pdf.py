"""国交省「離島振興対策実施地域一覧」の PDF を島ごとの行にする (designated_islands.csv の下書きと照合用)

  python nationwide/parse_ritou_pdf.py            # 下書きの CSV を標準出力へ
  python nationwide/parse_ritou_pdf.py --check    # nationwide/designated_islands.csv の 離島振興法 の行と比べる

入力: data/registry/islands/mlit_001886615_ritou_ichiran.pdf (令和8年4月1日現在)
PDF の表は 1 文字ずつ空白で区切られているので、文字の x 座標で列を分け、y 座標で行にまとめる。
「〃」は上の行と同じ市町村。「２町」「１市２町」の島は、続く (括弧) の行がその島の市町村。
県の見出し行 (例: 北海道 ５地域 ６島 ６町) の島の数と、合計行 (255 島) で読み取りを確かめる。
"""
import sys, re, csv, pathlib, collections
import pdfplumber

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "registry" / "islands" / "mlit_001886615_ritou_ichiran.pdf"
CSV = ROOT / "nationwide" / "designated_islands.csv"
URL = "https://www.mlit.go.jp/kokudoseisaku/chirit/content/001886615.pdf"
LAW = "離島振興法"
# 列の左端の x (pt)。PDF の表の罫線の位置
COLS = [("pref", 0), ("region", 130), ("island", 215), ("muni", 296), ("area", 380), ("pop", 470)]
ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def col_of(x):
    name = COLS[0][0]
    for n, x0 in COLS:
        if x >= x0 - 2:
            name = n
    return name


def lines():
    with pdfplumber.open(PDF) as pdf:
        for page in pdf.pages:
            rows = []     # top の差が 3pt 以内の文字を同じ行にする
            for w in sorted(page.extract_words(), key=lambda w: w["top"]):
                if rows and w["top"] - rows[-1][0]["top"] <= 3:
                    rows[-1].append(w)
                else:
                    rows.append([w])
            for row in rows:
                cells = collections.defaultdict(str)
                for w in sorted(row, key=lambda w: w["x0"]):
                    cells[col_of(w["x0"])] += w["text"]
                yield {k: v.translate(ZEN) for k, v in cells.items()}


def parse():
    out, pref, region, prev_muni = [], None, "", ""
    expect, counted = {}, collections.Counter()
    pending = None    # 「２町」のような島: 続く (括弧) の行で市町村を埋める
    for c in lines():
        if c.get("pref", "").endswith(("都", "道", "府", "県")) and "地域" in c.get("region", ""):
            pref = c["pref"]
            expect[pref] = int(re.sub(r"\D", "", c["island"]))
            region, prev_muni = "", ""
            continue
        if pref is None or c.get("pref", "").startswith(("都道県名", "(注)", "合計")) or "合計" in "".join(c.values()):
            continue
        if not any(c.get(k) for k in ("island", "muni")):
            continue
        if c.get("island", "").startswith(("(うち", "-")) or re.fullmatch(r"-?\d*-?", c.get("muni", "x")):
            continue    # 表の 2 行目の見出しと、ページ番号 (- 1 -)
        if c.get("region"):
            region = c["region"]
        muni = c.get("muni", "")
        paren = muni.startswith(("(", "（"))
        if paren and pending:
            name = muni.strip("()（）")
            out.append(dict(pending, municipality=name))
            prev_muni = name
            continue
        pending = None
        island = c.get("island", "")
        if island:
            counted[pref] += 1
        row = {"island": island, "pref": pref, "municipality": "", "law": LAW, "region": region,
               "inhabited": "yes" if island else "", "area_km2": c.get("area", "").strip("()"),
               "population_2020": c.get("pop", "").replace(",", "").strip("()"), "source_url": URL, "note": ""}
        if re.fullmatch(r"(\d+[市町村])+", muni):
            pending = row          # 次の (括弧) 行で 1 行ずつ出す
            continue
        if muni == "〃":
            muni = prev_muni
        if not island:            # 市町村名だけの行 (広島 安芸群島の江田島市)
            row["note"] = "指定地域に含まれるが、一覧に有人島の行が無い"
            row["inhabited"] = ""
        row["municipality"] = muni
        prev_muni = muni
        out.append(row)
    return out, expect, counted


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    rows, expect, counted = parse()
    bad = {p: (counted[p], n) for p, n in expect.items() if counted[p] != n}
    munis = {(r["pref"], r["municipality"]) for r in rows}
    print(f"島 {sum(counted.values())} (行 {len(rows)})、市町村 {len(munis)}、県 {len(expect)}、見出しと合わない県 {bad}",
          file=sys.stderr)
    if "--check" in sys.argv:
        have = {(r["pref"], r["island"], r["municipality"]) for r in csv.DictReader(open(CSV, encoding="utf-8"))
                if r["law"] == LAW}
        new = {(r["pref"], r["island"], r["municipality"]) for r in rows}
        print("CSV に無い:", sorted(new - have), file=sys.stderr)
        print("PDF に無い:", sorted(have - new), file=sys.stderr)
        sys.exit(1 if (new != have or bad) else 0)
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main()
