#!/usr/bin/env python3
"""パイロット 2: 表の構造 (色の凡例と行政区、表の行・列の見出し、結合したマス) も DeepSeek に見つけさせ、確かめ済みの構造と比べる

  python nationwide/pilot/deepseek_onagawa_structure.py <r08_chomin_calendar.pdf> [--months 2026-04,2026-10]
  → out/pilot/onagawa_<model>/structure_{A,B}.json と structure_summary.json

構造は 2 人 (問いかけ方を変える) が、別々の月の表のページから読む。食い違いは裁定に回す (ここでは数えるだけ)。
そのあと、2 人が一致した構造だけを使って、日付の読み取り (deepseek_onagawa.py と同じ) を 12 か月やり直し、324 マスと比べる
"""
import argparse, json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "nationwide", "llmread")); sys.path.insert(0, os.path.join(ROOT, "nationwide", "pilot"))
sys.stdout.reconfigure(encoding="utf-8")
import fitz
import llm
import deepseek_onagawa as d1

SYSTEM = d1.SYSTEM
ASK = {
 "A": ("宮城県女川町の町民カレンダーのページです。ごみ収集の表の「構造」だけを読み取ってください (日付は不要)。\n"
       "1. 「燃やせるごみの収集日」の色分け: 色の名前ごとに、その色に属する行政区の名前をすべて。色の付かない地区があれば、その地区と注記の文。\n"
       "2. 「資源ごみと燃やせないごみ、粗大ごみの収集日」の表: 列の見出し (左から、日付の列だけ。親見出しは含めない。改行はつなげる)、行の見出し (上から、印字のまま)。\n"
       "3. 結合したマス: 日付の行ごとに、1つのマスが複数の列にまたがっていれば、その列の組。\n"
       '答え: {"burnable_groups": [{"color": "...", "gyoseiku": ["..."]}], "no_color_note": "...", "columns": ["..."], '
       '"rows": [{"label": "...", "gyoseiku": ["..."]}], "merged": [{"row": "<行の見出し>", "columns": ["<列>", "<列>"]}], "unsure": "..."}'),
 "B": ("これは女川町のごみカレンダーの左ページです。表の作りを説明する係として、次を順に確かめてから JSON で答えてください。\n"
       "(a) 上の表 (燃やせるごみ): 左端の色の欄の文字 (例: ○色の日) と、その横に並ぶ地区名。\n"
       "(b) 下の表: 見出しの段 (親見出しと子見出し) を見分け、日付が入る列の見出しだけを左から。行の見出しの文字を上から。\n"
       "(c) 下の表の日付のマスで、縦の罫線が途中で無くなって、ひとつの枠が2列以上にまたがっているものを、行ごとに。\n"
       '答え: {"burnable_groups": [{"color": "...", "gyoseiku": ["..."]}], "no_color_note": "...", "columns": ["..."], '
       '"rows": [{"label": "...", "gyoseiku": ["..."]}], "merged": [{"row": "...", "columns": ["...", "..."]}], "unsure": "..."}'),
}

def n(s): return re.sub(r"\s+", "", str(s)).replace("ヶ", "ケ")

def truth():
    st = d1.ST
    merged = {}
    for c in st["resource_columns"]:
        pass
    return {"burnable": {n(g["color"]): sorted(n(x) for x in g.get("gyoseiku", [])) for g in st["burnable_groups"] if g.get("gyoseiku")},
            "columns": [n(c["label"]) for c in st["resource_columns"]],
            "rows": [n(r["label"]) for r in st["resource_rows"]]}

TRUTH_MERGED = {  # 確かめ済みの構造 (onagawa_reading.json の列の説明): 全行で「ペットボトル以外…」と「燃やせないごみ」、5・6行目は「缶・びん…」も
    0: [1, 2], 1: [1, 2], 2: [1, 2], 3: [1, 2], 4: [0, 1, 2], 5: [0, 1, 2]}

def score(ans):
    t = truth()
    got_b = {n(g.get("color", "")): sorted(n(x) for x in g.get("gyoseiku", [])) for g in ans.get("burnable_groups", []) if g.get("gyoseiku")}
    cols = [n(c) for c in ans.get("columns", [])]; rows = [n(r.get("label", "")) for r in ans.get("rows", [])]
    merged = {}
    for m in ans.get("merged", []):
        ri = next((i for i, r in enumerate(rows) if r == n(m.get("row", ""))), None)
        idx = sorted(cols.index(n(c)) for c in m.get("columns", []) if n(c) in cols)
        if ri is not None and len(idx) > 1: merged.setdefault(ri, set()).update(idx)
    merged = {k: sorted(v) for k, v in merged.items()}
    return {"burnable_ok": got_b == t["burnable"], "columns_ok": cols == t["columns"], "rows_ok": rows == t["rows"],
            "merged_ok": merged == TRUTH_MERGED, "got": {"burnable": got_b, "columns": cols, "rows": rows, "merged": merged}}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("--months", default="2026-04,2026-10"); a = ap.parse_args()
    doc = fitz.open(a.pdf)
    pages = {mp["month"]: mp for mp in d1.ST["month_pages"]}
    out = os.path.join(ROOT, "out", "pilot", "onagawa_" + llm.MODEL.replace("/", "_")); os.makedirs(out, exist_ok=True)
    res, usage = {}, []
    for r, mo in zip(("A", "B"), a.months.split(",")):
        png = doc[pages[mo]["table_page"] - 1].get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
        ans, u = llm.ask_json(SYSTEM, [{"type": "text", "text": ASK[r]}, llm.image_part(png)]); usage.append(u)
        res[r] = {"month_page": mo, "answer": ans, "score": score(ans)}
        json.dump(res[r], open(os.path.join(out, f"structure_{r}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(r, mo, {k: v for k, v in res[r]["score"].items() if k.endswith("_ok")})
    gA, gB = res["A"]["score"]["got"], res["B"]["score"]["got"]
    agree = {k: gA[k] == gB[k] for k in gA}
    summ = {"model": llm.MODEL, "readers": {r: {k: v for k, v in res[r]["score"].items() if k.endswith("_ok")} for r in res},
            "two_reader_agree": agree, "usage": usage}
    json.dump(summ, open(os.path.join(out, "structure_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False)[:1500])

if __name__ == "__main__":
    main()
