#!/usr/bin/env python3
"""パイロット: 女川町の町民カレンダー (PDF) を、ほかのモデル (既定 DeepSeek-V4.1-Flash) に読ませ、確かめ済みの 324 マスと比べる

  python nationwide/pilot/deepseek_onagawa.py <r08_chomin_calendar.pdf> [--months 2026-12,2027-01] [--readers A,B]
  → out/pilot/onagawa_<model>/{A,B}_<月>.json と summary.json

読み取りの手順は、2026-10-17 に Opus で作ったときと同じ: 表の構造 (行・列・結合したマス・色の凡例) は確かめ済みのものを渡し、
各月の「表のページ」(資源ごみ・燃やせないごみ・粗大ごみ) と「カレンダーのページ」(燃やせるごみの色) の日付だけを読ませる。
読み取り役は 2 人 (問いかけ方を変える)。正解は out/onagawa_reading.json の final (Opus 2 人の読みが全マスで一致したもの)
"""
import argparse, concurrent.futures as cf, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "nationwide", "llmread"))
sys.stdout.reconfigure(encoding="utf-8")
import fitz
import llm

READING = json.load(open(os.path.join(ROOT, "out", "onagawa_reading.json"), encoding="utf-8"))
ST = READING["structure"]
ROWS = [r["label"] for r in ST["resource_rows"]]
COLS = [c["label"] for c in ST["resource_columns"]]
COLORS = [g["color"] for g in ST["burnable_groups"]]

SYSTEM = ("あなたは自治体のごみ収集カレンダーを正確に読み取る係です。画像に印字された数字と色だけを読み、推測で補いません。"
          "読めない・はっきりしないマスは空の配列にし、unsure に書きます。答えは JSON だけで返します。")

def structure_text():
    cols = "\n".join(f"- 列{i + 1}「{c['label']}」: {c.get('contents', '')[:400]}" for i, c in enumerate(ST["resource_columns"]))
    rows = "\n".join(f"- 行{i + 1}「{r}」" for i, r in enumerate(ROWS))
    return f"表の列 (左から):\n{cols}\n表の行 (上から):\n{rows}"

def ask_table(png, month, style):
    y, m = month.split("-")
    if style == "A":
        q = (f"女川町の町民カレンダー {y}年{int(m)}月の「表のページ」です。左下の表「資源ごみと燃やせないごみ、粗大ごみの収集日」を読んでください。\n"
             f"{structure_text()}\n結合したマスは、結合しているすべての列に同じ日を入れます。"
             '答え: {"cells": {"<行の見出し>|<列の見出し>": [日の数字]}, "unsure": "はっきりしなかった所"} (行・列の見出しは上の文字のまま。24 マスすべて)')
    else:
        q = (f"これは宮城県女川町のごみカレンダー ({y}年{int(m)}月) の左ページです。下の表を、行ごとに右から左の順に確かめながら読み取ってください。"
             f"まず各行で、枠で囲まれた数字がどの列の下にあるか (結合したマスがどの列にまたがるか) を確かめてから答えます。\n{structure_text()}\n"
             '答え: {"rows": [{"row": "<行の見出し>", "values": {"<列の見出し>": [日の数字]}}], "unsure": "..."} (6 行 × 4 列。結合したマスはまたがる列すべてに同じ日)')
    ans, usage = llm.ask_json(SYSTEM, [{"type": "text", "text": q}, llm.image_part(png)])
    cells = {}
    if style == "A":
        for k, v in (ans.get("cells") or {}).items():
            cells["resource|" + k] = sorted(int(x) for x in v)
    else:
        for r in ans.get("rows") or []:
            for c, v in (r.get("values") or {}).items():
                cells[f"resource|{r.get('row')}|{c}"] = sorted(int(x) for x in v)
    return cells, ans.get("unsure"), usage

def ask_grid(png, month, style):
    y, m = month.split("-")
    legend = "、".join(COLORS)
    if style == "A":
        q = (f"女川町の町民カレンダー {y}年{int(m)}月の「カレンダーのページ」です。日付のマスの背景の色で、燃やせるごみの日が分けられています"
             f" (凡例: {legend})。それぞれの色の日を、すべて答えてください。色の無い日は入れません。"
             '答え: {"黄色の日": [日], "水色の日": [日], "unsure": "..."}')
    else:
        q = (f"{y}年{int(m)}月のカレンダーです。1日から月末まで1日ずつ、そのマスの背景が黄色・水色・色なしのどれかを確かめてください。"
             '最後に、黄色の日と水色の日の一覧を答えます。答え: {"days": {"1": "黄色|水色|なし", ...}, "黄色の日": [日], "水色の日": [日], "unsure": "..."}')
    ans, usage = llm.ask_json(SYSTEM, [{"type": "text", "text": q}, llm.image_part(png)])
    cells = {f"burnable|{c}": sorted(int(x) for x in (ans.get(c) or [])) for c in ("黄色の日", "水色の日")}
    cells["burnable|色分けなし（江島地区）"] = []
    return cells, ans.get("unsure"), usage

def norm_key(k):
    return k.replace(" ", "").replace("　", "")

def compare(got, truth):
    tk = {norm_key(k): v for k, v in truth.items()}
    gk = {norm_key(k): v for k, v in got.items()}
    res = {}
    for k, v in tk.items():
        res[k] = {"truth": v, "got": gk.get(k), "ok": gk.get(k) == v}
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--months", default="")
    ap.add_argument("--readers", default="A,B")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    doc = fitz.open(a.pdf)
    pages = {mp["month"]: mp for mp in ST["month_pages"]}
    months = a.months.split(",") if a.months else list(pages)
    tag = llm.MODEL.replace("/", "_")
    out = os.path.join(ROOT, "out", "pilot", f"onagawa_{tag}"); os.makedirs(out, exist_ok=True)
    png = lambda p: doc[p - 1].get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    jobs = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for mo in months:
            for r in a.readers.split(","):
                jobs.append((mo, r, ex.submit(lambda mo=mo, r=r: (ask_table(png(pages[mo]["table_page"]), mo, r), ask_grid(png(pages[mo]["grid_page"]), mo, r)))))
        summary = {"model": llm.MODEL, "months": {}, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}}
        for mo, r, fut in jobs:
            try:
                (tc, tu, tus), (gc, gu, gus) = fut.result()
            except Exception as e:
                summary["months"].setdefault(mo, {})[r] = {"error": str(e)[:300]}; print(mo, r, "ERROR", e); continue
            got = {**tc, **gc}
            cmp = compare(got, READING["final"][mo]["cells"])
            for u in (tus, gus):
                summary["usage"]["prompt_tokens"] += u.get("prompt_tokens", 0); summary["usage"]["completion_tokens"] += u.get("completion_tokens", 0); summary["usage"]["calls"] += 1
            okn = sum(x["ok"] for x in cmp.values())
            summary["months"].setdefault(mo, {})[r] = {"ok": okn, "cells": len(cmp), "unsure": [tu, gu], "wrong": {k: v for k, v in cmp.items() if not v["ok"]}}
            json.dump({"month": mo, "reader": r, "got": got, "compare": cmp, "usage": [tus, gus]}, open(os.path.join(out, f"{r}_{mo}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(mo, r, f"{okn}/{len(cmp)}", flush=True)
    for r in a.readers.split(","):
        ok = sum(v[r]["ok"] for v in summary["months"].values() if r in v and "ok" in v[r]); n = sum(v[r]["cells"] for v in summary["months"].values() if r in v and "cells" in v[r])
        summary[f"reader_{r}"] = {"ok": ok, "cells": n}
    # 2 人の読みが一致したマスだけを採るとき: 採ったマスのうち正しい数と、採らなかった (食い違い) 数
    agree = agree_ok = disagree = 0
    for mo, v in summary["months"].items():
        if not all(r in v and "ok" in v[r] for r in ("A", "B")): continue
        A = json.load(open(os.path.join(out, f"A_{mo}.json"), encoding="utf-8"))["compare"]; B = json.load(open(os.path.join(out, f"B_{mo}.json"), encoding="utf-8"))["compare"]
        for k in A:
            if A[k]["got"] == B[k]["got"]:
                agree += 1; agree_ok += A[k]["ok"]
            else: disagree += 1
    summary["two_reader"] = {"agreed_cells": agree, "agreed_and_correct": agree_ok, "agreed_but_wrong": agree - agree_ok, "disagreed_cells": disagree}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: summary[k] for k in summary if k != "months"}, ensure_ascii=False))

if __name__ == "__main__":
    main()
