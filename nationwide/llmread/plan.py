"""本番の読みの計画 (資料の一覧) → out/llmread/plan.json

  jgd       : japan-gomi-data tier A の自治体ごとに、正解が読んだ資料 (source_url・pdf_url) のうちキャッシュにあるもの。PDF は全ページ
  onagawa   : 女川の町民カレンダー 12 か月。1 か月 = 表のページと暦のページの 2 ページ
  kanazawa  : 金沢の地区・校下別収集カレンダーから 5 地区 (ページのうち、その地区の見出しの節だけ)
"""
import collections

from common import OUT, ROOT, read_json, sha256, write_json
import fetch_docs, jgd
import prep

ONAGAWA_PDF = "https://www.town.onagawa.miyagi.jp/pdf/calendar/r08_chomin_calendar.pdf"
# 金沢の見本の 5 地区 (燃やすごみが月・木と火・金の両方、1 月の (※) の変更、規則と表の違い (rule_mismatches) があるもの・無いもの)
KANAZAWA_SAMPLE = ("三和", "南小立野", "森山", "三谷", "内川")
SMOKE = ("jgd:112275", "onagawa:2026-04", "onagawa:2026-12", "kanazawa:三和")   # 金沢は採点の道すじの確かめ


def jgd_docs():
    tiers = jgd.tiers()
    out = []
    for lg, t in sorted(tiers.items()):
        if t["tier"] != "A":
            continue
        gt = jgd.load_gt(lg)
        urls = collections.OrderedDict()
        for c in gt["courses"]:
            for s in c["source"].values():
                for role in ("source_url", "pdf_url"):
                    if s.get(role):
                        urls.setdefault(s[role], {"roles": set(), "courses": set()})
                        urls[s[role]]["roles"].add(role)
                        urls[s[role]]["courses"].add(c["course"])
        docs = []
        for u, v in urls.items():
            body = fetch_docs.cached(u)
            doc = {"doc_id": f"{lg}-{sha256(u)[:8]}", "target": "jgd", "lg_code": lg, "name": gt["name_ja"], "url": u,
                   "roles": sorted(v["roles"]), "courses": sorted(v["courses"], key=str), "cached": body is not None}
            if body is not None:
                k = prep.kind_of(body, u)
                doc["kind"] = k
                if k == "pdf":
                    doc["pages_total"], doc["text_chars"] = prep.pdf_page_count(body)
                else:
                    try:
                        text = (prep.html_text(body, "", u)[0] if k == "html" else
                                prep.csv_text(body)[0] if k == "csv" else prep.excel_text(body, k)[0])
                        doc["text_chars"] = len(text)
                    except Exception as e:
                        doc["error"] = f"{type(e).__name__}: {e}"
            docs.append(doc)
        cov = {c for d in docs if d["cached"] for c in d["courses"]}
        out.append({"target": "jgd", "lg_code": lg, "name": gt["name_ja"], "format_class": t.get("our_format_class"),
                    "courses": len(gt["courses"]), "courses_with_cached_doc": len(cov), "docs": docs})
    return out


def onagawa_docs():
    st = read_json(ROOT / "out" / "onagawa_reading.json")["structure"]
    body = fetch_docs.cached(ONAGAWA_PDF)
    import fitz
    docs = []
    with fitz.open(stream=body, filetype="pdf") as d:
        for mp in st["month_pages"]:
            pages = [mp["table_page"], mp["grid_page"]]
            docs.append({"doc_id": f"045811-{mp['month']}", "target": "onagawa", "month": mp["month"], "url": ONAGAWA_PDF,
                         "pages": pages, "kind": "pdf", "cached": True, "pages_total": 2,
                         "text_chars": sum(len(d[p - 1].get_text("text").strip()) for p in pages)})
    return [{"target": "onagawa", "lg_code": "045811", "name": "女川町", "docs": docs}]


def kanazawa_docs():
    v01 = read_json(ROOT / "out" / "kanazawa_schedules.json")
    seen = {}
    for s in v01["schedules"]:
        seen.setdefault(s["district"], s)
    docs = []
    for name in KANAZAWA_SAMPLE:
        s = seen[name]
        page = s["source"]["url"].split("#")[0].split("/")[-1]
        doc = {"doc_id": f"172014-{name}", "target": "kanazawa", "district": name, "path": f"data/kanazawa_cal/{page}",
               "url_original": s["source"]["url"], "section": s["source"]["section"], "kind": "html", "cached": True}
        text = prep.section(prep.html_text((ROOT / doc["path"]).read_bytes())[0], doc["section"])
        doc["text_chars"] = len(text or "")
        docs.append(doc)
    return [{"target": "kanazawa", "lg_code": "172014", "name": "金沢市", "docs": docs}]


def make():
    targets = jgd_docs() + onagawa_docs() + kanazawa_docs()
    tot = collections.Counter()
    for t in targets:
        for d in t["docs"]:
            if not d.get("cached"):
                tot["docs_not_cached"] += 1
                continue
            tot["docs"] += 1
            tot[f"docs_{t['target']}"] += 1
            if d.get("kind") == "pdf":
                tot["pdf_pages"] += d.get("pages_total") or 0
            tot["text_chars"] += d.get("text_chars") or 0
    plan = {"about": "LLM が資料を読む本番の計画 (nationwide/llmread/)。資料はキャッシュと手元のファイルだけ。PDF はページの画像 "
                     f"({prep.DPI}dpi) と文字層、HTML は本文の範囲の文字 (表は簡単な HTML)、CSV・Excel は markdown の表",
            "models": ["deepseek-flash (llmread/llm.py)", "claude-opus-5-5 (claude -p。道具は Read だけ)"],
            "decision_rule": "同じ入力で Opus と DeepSeek を比べ、どちらも正しければ DeepSeek を使う (作者 firemio)",
            "smoke": list(SMOKE), "totals": dict(tot), "targets": targets}
    write_json(OUT / "plan.json", plan)
    return plan


def docs_of(plan):
    """計画 → {doc_id: 資料}"""
    return {d["doc_id"]: d for t in plan["targets"] for d in t["docs"]}


def select(plan, spec):
    """"jgd:112275" / "onagawa:2026-04" / "kanazawa:三和" / doc_id → 資料のリスト"""
    out = []
    for t in plan["targets"]:
        for d in t["docs"]:
            if not d.get("cached"):
                continue
            if d["doc_id"] == spec or (spec.startswith(t["target"] + ":") and spec.split(":", 1)[1] in
                                       (t.get("lg_code"), d.get("month"), d.get("district"))):
                out.append(d)
    return out
