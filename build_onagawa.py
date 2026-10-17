#!/usr/bin/env python3
"""女川町のデータを組み立てる: 読み取りワークフローの結果 (JSON) → out/onagawa_data.json

  python build_onagawa.py <ワークフローの結果.json>

読み取りは、Opus のエージェント2人が公式資料を別々に読み (構成と、2か月ずつの日付)、
一致したものはそのまま、食い違ったマスだけ3人目が資料で確かめて決めたもの。
"""
import datetime as dt, json, os, re, sys

JST = dt.timezone(dt.timedelta(hours=9))

def cells(m):
    """1か月分の読み → マスの鍵 (燃やせるごみの色の組、表の行 × 列)"""
    if not m: return []
    return [f"burnable|{b['color']}" for b in m["burnable"]] + [f"resource|{r['row']}|{r['column']}" for r in m["resource"]]

def redact(o):
    """読み取りの記録から手元のパス (ユーザー名を含む) を伏せる"""
    home = os.path.expanduser("~")
    if isinstance(o, str):
        for h in (home, home.replace("\\", "/"), home.replace(":\\", "--").replace("\\", "-")): o = o.replace(h, "~")
        return o
    if isinstance(o, dict): return {k: redact(v) for k, v in o.items()}
    if isinstance(o, list): return [redact(v) for v in o]
    return o

def main(path):
    wf = json.load(open(path, encoding="utf-8"))
    wf = redact(wf.get("result", wf))
    json.dump(wf, open("out/onagawa_reading.json", "w", encoding="utf-8"), ensure_ascii=False)     # 読み取りの記録 (2人の読み・照合役の判断)
    st, final = wf["structure"], wf["final"]
    def iso(month, day):
        y, m = map(int, month.split("-"))
        try: return str(dt.date(y, m, int(day)))
        except ValueError: return None
    groups = []
    for g in st["burnable_groups"]:
        dates = sorted(x for mo, v in final.items() for d in v["cells"].get(f"burnable|{g['color']}", []) if (x := iso(mo, d)))
        groups.append({"color": g["color"], "weekdays_text": g["weekdays_text"], "gyoseiku": g["gyoseiku"], "dates": dates})
    rows = []
    for r in st["resource_rows"]:
        by = {}
        for c in st["resource_columns"]:
            by[c["label"]] = sorted(x for mo, v in final.items() for d in v["cells"].get(f"resource|{r['label']}|{c['label']}", []) if (x := iso(mo, d)))
        rows.append({"label": r["label"], "gyoseiku": r["gyoseiku"], "by_column": by})
    put = None
    for p in st["put_out_rules"]:
        m = re.search(r"(午前|朝)?\s*\d{1,2}\s*時(\s*\d{1,2}\s*分)?\s*(まで|までに)", p["text"])
        if m:
            url = re.match(r"https?://\S+?\.(?:html|pdf)", p["source"])
            title = next((x["title"].split("｜")[0] for x in st["sources"] if url and x["url"] == url[0]), p["source"])
            put = {"deadline": f"収集日の{m[0].replace('までに', 'まで').replace(' ', '')}", "official_text": p["text"],
                   "source_title": title, "source_url": url[0] if url else None}
            break
    keep = ("05_05.html", "chomin_calendar", "gomi_itiran", "gomi_bunbetsu", "plastic_gomi", "plastic_bunbetsu", "gyouseiku")
    srcs = [{"url": s["url"], "title": s["title"], "kind": s["what"]} for s in st["sources"] if any(k in s["url"] for k in keep)]
    sort_src = next((s["url"] for s in st["sources"] if "分別" in s["what"] or "分別" in s["title"]), "https://www.town.onagawa.miyagi.jp/05_05.html")
    disagree = wf.get("disagreements") or {}
    data = {
        "municipality": "宮城県牡鹿郡女川町", "lg_code": "045811", "fiscal_year": 2026, "valid_from": "2026-04-01", "valid_to": "2027-03-31",
        "calendar_title": st["calendar_title"],
        "burnable": {"label": st["burnable_label"], "groups": groups},
        "resource": {"columns": st["resource_columns"], "rows": rows},
        "place_to_gyoseiku": st["place_to_gyoseiku"],
        "sorting_rules": st["sorting_rules"], "sorting_source_url": sort_src,
        "put_out_by": put, "put_out_texts": st["put_out_rules"], "year_end_note": st["year_end_note"],
        "holiday_policy": "祝日もカレンダーの色・表のとおり収集する。燃やせるごみは 12/31・1/1 は収集なし。資源ごみ・燃やせないごみ・粗大ごみは 12/29〜1/3 に収集なし (行政区ごとの日付は、行政区を指定した答えの holiday_policy)",
        "not_covered": ["ごみ集積所の場所", "粗大ごみの申し込み方法"],
        "contact": {"name": "女川町クリーンセンター", "phone": "0225-53-3549", "note": "町の「環境・ごみ」のページ: (ごみに関して) 女川町クリーンセンター",
                    "source_url": "https://www.town.onagawa.miyagi.jp/05_05.html"},
        "sources": srcs,
        "notes": st["notes"] + [n for v in final.values() for n in v.get("notes", [])][:80],
        "build": {"method": "Opus のエージェント2人が公式資料を別々に読み、一致したマスはそのまま、食い違ったマスは3人目が資料で確かめて決めた",
                  "months_read_by_both": sorted(m for m, v in (wf.get("raw_months") or {}).items() if {"A", "B"} <= set(v)),
                  "date_cells_compared_per_month": {m: len(set(cells(v.get("A"))) | set(cells(v.get("B")))) for m, v in (wf.get("raw_months") or {}).items()},
                  "date_cells_disagreed": {m: len(disagree.get(m, [])) for m in sorted((wf.get("raw_months") or {}))},
                  "adjudications": [{"month": a["mo"], "decisions": a["r"]["decisions"]} for a in wf.get("adjudications", []) if a.get("r")],
                  "built_at": dt.datetime.now(JST).isoformat(timespec="seconds")},
    }
    json.dump(data, open("out/onagawa_data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = sum(len(g["dates"]) for g in groups) + sum(len(v) for r in rows for v in r["by_column"].values())
    print(f"out/onagawa_data.json: {len(groups)} 色の組・{len(rows)} 行 × {len(st['resource_columns'])} 列、日付 {n} 件、"
          f"食い違ったマス {sum(len(v) for v in disagree.values())}、分別のルール {len(st['sorting_rules'])} 件")

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1])
