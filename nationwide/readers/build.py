"""入口: 自治体を選んで段 0〜9 を通し、out/schedules/<lg_code>.json (スキーマ v0.2) と答えの種類 (coverage) を書く (DESIGN.md §3.1・§6.2)

  python nationwide/readers/build.py --codes 112429,132047 [--llm] [--offline] [--no-write]
  python nationwide/readers/build.py --tier-a [--llm]           # japan-gomi-data の tier A の 17 自治体
  python nationwide/readers/build.py --codes 172014,045811        # 金沢・女川は eval/regress.py の書き直し (今のデータ) を通す

- 入力はキャッシュ (data/discover/cache/) と段 0 の一覧 (data/readers/<lg_code>/docs.json) だけ。サイトには取りに行かない
- 型 A・B・C (CSV・Excel・HTML の表・文) は eval/stage2.py の読み (読み取り役 A と、表の生の断片の B) を使う。
  型 D (罫線のある文字の PDF の日付の表) は pdf_layout.py で読む。そのほかの型 (月表 F・輪郭線 G・画像 H・外国語の版) の資料は読まず、
  同じ期間のカレンダーがあるしるしとして、規則から作る系列を pending_printed で止める (§2.5 の 5)
- 2 つ目の読み (B) は llm_tasks.py: 型 D のマス (番号つきの枠の画像)・品目・地区・規則、どの資料も方針と但し書き (本文の範囲の全体)、
  区分けの決め方。比べは compare.py、待ち行列は adjudicate.py (書くだけ)、確かめは validate.py (§4.1)
- --llm は B を読む (キャッシュに無ければ API を呼ぶ。キーは環境変数 GOMISUTE_LLM_API_KEY)。--offline はキャッシュだけ。
  どちらも無ければ B は無く、どの値も single (答えない)
- M1 では関門 (§4.4) がまだ開かないので、published はどれも false、答えの種類はどの地区も rule_contact (種類 3)。
  answerable の系列は coverage.gate_pending に数える
- 書くもの: out/schedules/<lg_code>.json、data/readers/<lg_code>/build.json (読みの生の記録・B の答えの要約・印字の日付の生の読み)、
  data/readers/adjudications/queue/<lg_code>.json (裁定の待ち行列)
"""
import argparse, collections, datetime as dt, json, os, pathlib, re, sys, urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "eval"))
sys.path.insert(0, str(HERE.parent / "discover"))
import adjudicate, categories, compare, consume, content, decode, expand, fetch_docs, llm_tasks, markup, members, pdf_layout, rules, \
    schema, validate, version  # noqa: E402
import stage2                                                                                               # noqa: E402

BUILDER = "build.py@1"
OUT = ROOT / "out" / "schedules"
DATA = ROOT / "data" / "readers"
LLM_CACHE = DATA / "llm_cache"
TIER_A = ("112038", "112071", "112097", "112259", "112275", "112429", "113620", "113638", "113654", "113697", "131024", "131121",
          "131156", "131199", "132039", "132047", "132055")
REGRESS = ("172014", "045811")
MAX_PDF = 40
MAX_PAGES = 12
RESIDENCE = re.compile(r"お住まい|お住いの|にお住|住所(?:別|で|の)")
CIRCLED = re.compile(r"^[①-⑳㉑-㉟㊱-㊿]\s*")
LAST = {}                              # 最後に作った自治体の dict と待ち行列 (テストと確かめのため)
PRICE_IN, PRICE_OUT = 0.30, 1.20       # DeepSeek-V4.1-Flash の 100 万トークンあたりの料金 (US$。混む時間。§3.13)


def say(*a):
    print(*a, file=sys.stderr, flush=True)


# ---- LLM ----
class LLMRun:
    """B の呼び出しの記録 (自治体ごと)"""

    def __init__(self, a, m):
        import threading
        self.a, self.m = a, m
        self.recs = []
        self.lock = threading.Lock()
        self.prefetched = {}

    def ask(self, name, text, images=()):
        if not (self.a.llm or self.a.offline):
            return None, {"missing": True, "skipped": True}
        key = (name, text, tuple(images))
        if key in self.prefetched:
            ans, rec = self.prefetched.pop(key)
        else:
            ans, rec = llm_tasks.ask(name, text, LLM_CACHE, images=images, offline=self.a.offline or not self.a.llm)
        rec["task"] = name
        with self.lock:
            self.recs.append(rec)
            self.m.count_llm([rec])
        return ans, rec

    def prefetch(self, jobs, workers=8):
        """[(仕事, 文字, 画像)] をまとめて先に聞く (並べて呼ぶ。答えは覚えておき、ask で同じものを聞いたときに使う)"""
        jobs = [j for j in jobs if (j[0], j[1], tuple(j[2])) not in self.prefetched]
        jobs = list({(j[0], j[1], tuple(j[2])): j for j in jobs}.values())       # 同じ入力は 1 回だけ聞く
        if not self.a.llm or self.a.offline or not jobs:
            return
        from concurrent.futures import ThreadPoolExecutor

        def one(j):
            name, text, images = j
            try:
                return j, llm_tasks.ask(name, text, LLM_CACHE, images=images, offline=False)
            except Exception as e:                     # 呼べなかったものは ask でもう一度
                say(f"  LLM の呼び出しに失敗 ({name}): {str(e)[:120]}")
                return j, None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for j, r in ex.map(one, jobs):
                if r is not None:
                    self.prefetched[(j[0], j[1], tuple(j[2]))] = r


def llm_cost(recs):
    """記録 → {"calls_new", "cached", "missing", "prompt_tokens", "completion_tokens", "usd_new", "usd_all"} (料金は §3.13 の混む時間の値)"""
    out = collections.Counter()
    usd_new = usd_all = 0.0
    for r in recs:
        pt, ct = r.get("prompt_tokens") or 0, r.get("completion_tokens") or 0
        c = (pt * PRICE_IN + ct * PRICE_OUT) / 1e6
        usd_all += c
        if r.get("missing"):
            out["missing"] += 1
        elif r.get("cached"):
            out["cached"] += 1
        else:
            out["calls_new"] += 1
            usd_new += c
        out["prompt_tokens"] += pt
        out["completion_tokens"] += ct
    return {**dict(out), "usd_new": round(usd_new, 4), "usd_all": round(usd_all, 4)}


# ---- 資料の一覧 ----
def pdf_docs(docs):
    """段 0 の一覧のうち、キャッシュにある PDF で、収集日・地区の資料 (kinds に calendar・districts、best、role が schedule) のもの"""
    out = []
    for x in docs["docs"]:
        if x.get("cache") != "cached" or x.get("file_type") != "pdf":
            continue
        if not (set(x.get("kinds") or ()) & {"calendar", "districts"} or x.get("best") or x.get("role") == "schedule"):
            continue
        out.append(x)
    out.sort(key=lambda x: (-(x.get("score") or 0), x["url"]))
    return out[:MAX_PDF]


def calendar_like(x):
    txt = " ".join((x.get("anchors") or []) + [x.get("title") or "", urllib.parse.unquote(x["url"])])
    return bool(stage2.CAL_LINK.search(txt) or re.search(r"コース|地区|日程|収集日", txt) or "calendar" in (x.get("kinds") or []))


def printed_evidence(docs, analyses):
    """同じ自治体の、印字の日付がありうるのに読んでいない資料 → [{"url", "why"}] (§2.5 の 5。どの地区の資料か決めずに、
    規則から作る系列をすべて止める方に倒す)。読んだ型 D の資料は入れない。外国語の版は入れない"""
    out = []
    for x in docs["docs"]:
        url = x["url"]
        if x.get("file_type") not in ("pdf", "image", None) and not url.lower().endswith((".pdf", ".jpg", ".png")):
            continue
        an = analyses.get(url)
        if an is not None:
            kinds = {p["kind"] for p in an["pages"]}
            if kinds & {"month_grid"}:
                out.append({"url": url, "why": "month_grid (型 F。M1 では読まない)"})
            elif kinds & {"pdf_image", "pdf_outline"} and calendar_like(x):
                out.append({"url": url, "why": f"{'・'.join(sorted(kinds & {'pdf_image', 'pdf_outline'}))} (M1 では読まない)"})
            elif "rule_table" in kinds and calendar_like(x):
                out.append({"url": url, "why": "rule_table (M1 ではまだ読まない)"})
            continue
        if x.get("cache") != "cached" and calendar_like(x) and (x.get("file_type") in ("pdf", "image", None)):
            out.append({"url": url, "why": "段 0 で取っていない (doc_not_fetched)"})
    return out


# ---- 型 D ----
def analyze_pdf(url, max_pages=MAX_PAGES):
    body = fetch_docs.cached(url)
    if body is None:
        return None
    try:
        doc = pdf_layout.open_pdf(body)
    except Exception as e:
        return {"error": str(e)[:120], "pages": [], "body": body}
    pages = []
    for p in list(doc)[:max_pages]:
        try:
            pages.append(pdf_layout.analyze_page(p))
        except Exception as e:
            pages.append({"page": p.number, "kind": "other", "why": f"読めない: {str(e)[:80]}", "tables": [], "info": None})
    return {"pages": pages, "doc": doc, "body": body}


def district_title(pi, table_bbox):
    """表の上の行のうち、地区の名前の題 (三鷹「①大沢3丁目、深大寺、井口、野崎2・3丁目地区用」) → (文字, 矩形) か (None, None)"""
    best = None
    for ln in pi.lines:
        if not ln["visible"] or ln["bbox"].y1 > table_bbox.y0 + 2:
            continue
        t = pdf_layout.nfkc(ln["visible_text"]).strip()
        if re.search(r"(?:地区|地域)(?:用)?$", t) and len(t) <= 80:
            if best is None or ln["size"] > best["size"]:
                best = ln
    if best is None:
        return None, None
    t = pdf_layout.nfkc(CIRCLED.sub("", best["visible_text"].strip())).strip()      # 丸数字 (①) は NFKC の前に外す
    return members.strip_district_title(t), best["bbox"]


def split_names(text):
    """PDF の地区のマスの文字 (名前を空白で並べたもの) → 「、」で区切った文字 (地区の文法に渡す)"""
    parts = [p for p in re.split(r"[\s　]+", pdf_layout.nfkc(text).strip()) if p]
    return "、".join(parts)


def district_members(text, key, lg):
    """地区のマスの文字と見出し (「行政区名」) → 中身のリスト。行政区・自治会の見出しなら単位は community (当てない)"""
    mem = members.parse_cell(split_names(text), lg) if text else []
    if key and re.search(r"行政区|自治会|町内会|町会|区会", key):
        for m in mem:
            m["unit"] = "community"
            m["geometry"] = "unresolved"
            m["conditions"] = [c for c in m.get("conditions", []) if c.get("kind") == "note"]
            m.setdefault("extra", {})["name_verbatim"] = m["text"]          # 行政区の名前の数 (「旭ケ丘１・２」) は丁目ではない
    return mem


def expand_numbered(mem):
    """2 つの読みがそろったあとの行政区の名前の広げ (「旭ケ丘１・２」→「旭ケ丘1」「旭ケ丘2」)。名前は extra.name_verbatim に残す"""
    out = []
    for m in mem:
        t = pdf_layout.nfkc(m.get("text") or "")
        mm = re.fullmatch(r"(.+?)(\d{1,2}(?:・\d{1,2})+)", t)
        if m.get("unit") == "community" and mm:
            for n in mm.group(2).split("・"):
                x = json.loads(json.dumps(m))
                x["text"] = mm.group(1) + n
                x.setdefault("extra", {})["name_verbatim"] = m.get("text")
                x["extra"]["expanded_from"] = "numbered_names@1"
                out.append(x)
        else:
            out.append(m)
    return out


class PdfLine(content.Line):
    """PDF の 1 行 (content.Line に、ページと矩形を足したもの)"""

    def __init__(self, text, heading_path, selector, page, bbox, in_table=False):
        super().__init__(text, heading_path, selector, "pdf", None, False, in_table)
        self.page, self.bbox = page, bbox


def _span_page(span, lines_by_sel):
    ln = lines_by_sel.get(span.get("selector"))
    if ln is not None:
        span["page"] = ln.page + 1
        span["bbox"] = [round(v, 1) for v in ln.bbox]
    return span


def _iso(month, day, per):
    d = rules._year_for(month, day, per)
    return d.isoformat() if d else None


def pdf_title_lines(d_pages):
    """日付の表のページの大きい字の行 (題)"""
    out = []
    for pg in d_pages:
        vis = [l for l in pg["info"].lines if l["visible"]]
        mx = max((l["size"] for l in vis), default=0)
        out += [l["visible_text"] for l in vis if l["size"] >= 0.8 * mx]
    return out


def pdf_prep(an):
    """型 D の資料の B の入力 (品目・規則・地区・方針・マスの画像) を作る → dict。jobs は LLMRun.prefetch に渡す [(仕事, 文字, 画像)]"""
    d_pages = [pg for pg in an["pages"] if pg["kind"] == "date_table"]
    title_lines = pdf_title_lines(d_pages)
    # 品目 (資料の中の名前すべて) と B の品目の読み
    labels = []
    for pg in d_pages:
        for t in pg["tables"]:
            dtab = t["date_table"]
            if not dtab:
                continue
            labels += [c["label"] for c in dtab["categories"]] + [r["category_label"] for r in dtab["rule_cells"]]
    labels = list(dict.fromkeys(labels))
    cat_msg = "品目の名前 (資料の表の見出し):\n" + "\n".join(f"- {l}" for l in labels) + "\n\n資料の題: " + " ".join(title_lines)[:300]
    # B の入力を先に作り、まとめて並べて聞く (同じ入力はキャッシュから)
    rule_cells, dist_cells = [], []
    for pg in d_pages:
        for t in pg["tables"]:
            dtab = t["date_table"]
            if not dtab:
                continue
            for r in dtab["rule_cells"]:
                rule_cells.append((pg["page"], t["index"], r))
            dist = (dtab["header"].get("district") or {})
            if dist.get("text"):
                dist_cells.append((pg["page"], t["index"], dist.get("key"), dist["text"]))
            elif dtab["orientation"] == "months_in_col":
                dtext, _ = district_title(pg["info"], t["bbox"])
                if dtext:
                    dist_cells.append((pg["page"], t["index"], "地区", dtext))
    rules_msg = "\n".join(f"マス {i + 1}: 品目「{r['category_label']}」 の規則のマス: 「{(r.get('japanese') or r['text']).replace(chr(10), ' ')}」"
                          for i, (_, _, r) in enumerate(rule_cells))
    mem_msg = "\n".join(f"マス {i + 1}: 見出し「{k or '地区'}」 のマス: 「{content.nfkc(v).replace(chr(10), ' ')}」"
                        for i, (_, _, k, v) in enumerate(dist_cells))
    prep, jobs = {}, [("category", cat_msg, ())]
    if rule_cells:
        jobs.append(("rules", rules_msg, ()))
    if dist_cells:
        jobs.append(("members", mem_msg, ()))
    for pg in d_pages:
        pi = pg["info"]
        ptxt = llm_tasks.policy_text([{"text": l["visible_text"], "is_heading": False} for l in pi.lines if l["visible"]],
                                     title=" ".join(title_lines)[:200])
        prep[("policy", pg["page"])] = ptxt
        jobs += [("policy", t, ()) for t in ptxt]
        for t in pg["tables"]:
            dtab = t["date_table"]
            if not dtab:
                continue
            title_bbox = None
            if not (dtab["header"].get("district") or {}).get("text") and dtab["orientation"] == "months_in_col":
                _, title_bbox = district_title(pi, t["bbox"])
            png = pdf_layout.render_grid_png(pi.page, t["grid"], extra=title_bbox)
            msg = (f"表は R1〜R{t['grid'].n_rows} の {t['grid'].n_rows} 行、C1〜C{t['grid'].n_cols} の {t['grid'].n_cols} 列です。\n" +
                   "\n".join(pdf_layout.merged_desc(t["grid"])) +
                   ("\n表の上の題 (地区の名前) も title に書いてください。" if title_bbox is not None else ""))
            prep[("cells", pg["page"], t["index"])] = (png, msg)
            jobs.append(("cells", msg, (png,)))
    return {"labels": labels, "cat_msg": cat_msg, "rule_cells": rule_cells, "dist_cells": dist_cells, "rules_msg": rules_msg,
            "mem_msg": mem_msg, "prep": prep, "jobs": jobs}


def read_pdf_source(m, x, an, a, llm, evidence, ctx, raw):
    """型 D の資料 1 つを読む → m に系列を足す。x は段 0 の一覧の 1 件、an は analyze_pdf の結果"""
    d_pages = [pg for pg in an["pages"] if pg["kind"] == "date_table"]
    if not d_pages:
        return
    body = an["body"]
    sid = f"s{len(m.sources) + 1}"
    page_count = len(an["doc"]) if an.get("doc") is not None else None
    vis_text = "\n".join(pg["info"].visible_text() for pg in d_pages if pg.get("info"))
    src = {"source_id": sid, "url": x["url"], "source_key": x.get("source_key") or x["url"],
           "found_from": (x.get("found_from") or [None])[0], "anchor_text": (x.get("anchors") or [None])[0],
           "anchor_context": x.get("anchor_context"), "title": x.get("title"), "publisher": m.name, "format": "pdf_text",
           "pages": page_count, "sha256": x.get("sha256"),
           "content_sha256": content.sha256_text(content.squash(vis_text)),
           "last_modified": x.get("last_modified"), "fetched_at": x.get("fetched_at"),
           "extra": {"reader": pdf_layout.READER_VERSION, "date_table_pages": [pg["page"] + 1 for pg in d_pages],
                     "page_kinds": [pg["kind"] for pg in an["pages"]]}}
    m.sources.append(src)
    # 版の証拠: 題 = 日付の表のページの大きい字、本文 = 見える字、隠れた字は数えない (§3.3)
    title_lines, hidden = pdf_title_lines(d_pages), []
    for pg in an["pages"]:
        if pg.get("info"):
            hidden += [c.c for c in pg["info"].chars if not c.visible]
    title_toks = version.year_tokens(" ".join(title_lines))
    body_toks = version.year_tokens(vis_text)
    file_toks = version.filename_tokens(x["url"])
    anchor_toks = version.year_tokens(" ".join(x.get("anchors") or []))
    hidden_toks = version.year_tokens("".join(hidden))
    times = version.http_times(x.get("last_modified"), x.get("fetched_at"), sid) + version.pdf_times(body, sid)
    pr = stage2.period_of(title_toks, body_toks, file_toks)
    if pr:
        vf, vt, basis_text = pr
        ye = version.year_evidence(vf, vt, title_toks, body_toks, file_toks, anchor_toks, times, hidden=hidden_toks,
                                   token_ref={"source_id": sid, "page": d_pages[0]["page"] + 1})
        per = {"valid_from": vf, "valid_to": vt, "basis": {"text": basis_text, "ref": {"source_id": sid, "page": d_pages[0]["page"] + 1},
                                                           "kind": "stated"}, "year_evidence": ye}
    else:
        fa = (x.get("fetched_at") or dt.date.today().isoformat())[:10]
        per = {"valid_from": fa, "valid_to": None, "basis": {"text": "期間の書いていない資料", "ref": {"source_id": sid}, "kind": "undated"},
               "year_evidence": version.year_evidence(fa, None, times=times)}
    pid = m.period_id(per)
    per = m.periods[pid]
    ctx["sources"][sid] = {"kind": "pdf", "sentences": [], "unconsumed": [], "table_pages": [pg["page"] + 1 for pg in d_pages]}
    pp = an.get("prep")
    if pp is None:                                  # build_one がまとめて先に聞いていなければ、ここで聞く
        pp = pdf_prep(an)
        llm.prefetch(pp["jobs"])
    labels, cat_msg, rule_cells, dist_cells = pp["labels"], pp["cat_msg"], pp["rule_cells"], pp["dist_cells"]
    rules_msg, mem_msg, prep = pp["rules_msg"], pp["mem_msg"], pp["prep"]
    b_cat, _ = llm.ask("category", cat_msg)
    b_cat_by = {}
    for it in (b_cat or {}).get("categories") or []:
        if isinstance(it, dict) and it.get("label"):
            b_cat_by[content.squash(it["label"])] = it
    src_cats = {}
    for l in labels:
        cat = categories.category(l)
        src_cats[l] = cat
    cat_ids = {}
    for l, cat in src_cats.items():
        # ほかの資料に同じ品目 (分類と出し方が同じ) があれば、その品目にする (資料どうしの比べ。§3.11)
        hit = next((cid for cid, c in m.categories.items() if c["mode"] == cat["mode"] and set(c["coarse"]) == set(cat["coarse"])), None)
        cat_ids[l] = hit or m.category_id(l, cat, {"source_id": sid, "note": "表の見出し"})
    cat_state = {l: compare.compare_category(c, b_cat_by.get(content.squash(l))) for l, c in src_cats.items()}
    cats_for_scope = {cat_ids[l]: m.categories[cat_ids[l]] for l in labels}
    b_rules = {}
    if rule_cells:
        ans, _ = llm.ask("rules", rules_msg)
        for it in (ans or {}).get("rules") or []:
            if isinstance(it, dict) and str(it.get("id") or "").strip().isdigit():
                b_rules[int(it["id"]) - 1] = it
    b_mem = {}
    if dist_cells:
        ans, _ = llm.ask("members", mem_msg)
        for it in (ans or {}).get("rows") or []:
            if isinstance(it, dict) and str(it.get("id") or "").strip().isdigit():
                b_mem[int(it["id"]) - 1] = it
    rule_ix = {(p, ti, id(r)): i for i, (p, ti, r) in enumerate(rule_cells)}
    dist_ix = {(p, ti): i for i, (p, ti, _, _) in enumerate(dist_cells)}
    queue = m.queue
    # ページごと
    for pg in d_pages:
        pi = pg["info"]
        page = pg["page"]
        tables = [t for t in pg["tables"] if t["date_table"]]
        notes = pdf_layout.notes_for(pi, tables)
        # 方針の B (ページの見える字のすべて。表の中の字も)
        ptxt = prep[("policy", page)]
        b_pol = llm_tasks.merge_policy([llm.ask("policy", t)[0] for t in ptxt]) if (a.llm or a.offline) else None
        if b_pol is not None and b_pol.get("missing") == len(ptxt):
            b_pol = None
        pol_read = b_pol is not None and not b_pol.get("missing")      # B がページの方針の入力をすべて読んだか (一部でも無ければ single)
        pol_reader = "code_llm" if pol_read else ("code_llm_partial" if b_pol is not None else "code_only")
        # 表ごとの地区
        tinfo = {}
        for t in tables:
            dtab = t["date_table"]
            tix = t["index"]
            did = f"{m.lg}-{sid}-p{page + 1}-t{tix}"
            dist = dtab["header"].get("district") or {}
            course = dtab["header"].get("course") or {}
            dkey, dtext, dbroken = dist.get("key"), dist.get("text"), dist.get("broken")
            title_bbox = None
            if not dtext and dtab["orientation"] == "months_in_col":
                dtext, title_bbox = district_title(pi, t["bbox"])
                dkey = "地区"
            # B のマス (番号つきの枠の画像)
            png, msg = prep[("cells", page, tix)]
            b_ans, b_rec = llm.ask("cells", msg, images=[png])
            bc = compare.b_cells(b_ans)
            label = (course.get("text") or dtext or m.name).replace("\n", " ")
            states = []
            # 地区の文字 (表の中は B の画像の読みと、表の外の題は B の title と)
            if dist.get("span"):
                r0, c0 = dist["span"][0], dist["span"][1]
                states.append(("district_text", "single" if dbroken else (compare.state(compare.label_same(dtext, (bc.get((r0, c0)) or {}).get("text")),
                                                                                          b_present=(r0, c0) in bc))))
            elif title_bbox is not None:
                bt = (b_ans or {}).get("title") if isinstance(b_ans, dict) else None
                states.append(("district_text", compare.state(bool(bt) and content.squash(dtext) in content.squash(
                    re.sub(r"(?:地区|地域)用?", "", pdf_layout.nfkc(bt or ""))), b_present=b_ans is not None)))
            else:
                states.append(("district_text", "single"))
            if course.get("span"):
                r0, c0 = course["span"][0], course["span"][1]
                states.append(("course_label", "single" if course.get("broken") else compare.state(
                    compare.label_same(course["text"], (bc.get((r0, c0)) or {}).get("text")), b_present=(r0, c0) in bc)))
            # 月の見出し
            mh = []
            for k, mo in dtab["months"].items():
                rc = (dtab["axis"], k) if dtab["orientation"] == "months_in_row" else (k, dtab["axis"])
                bt = (bc.get(rc) or {}).get("text")
                mh.append(compare.state(bt is not None and pdf_layout.month_of(bt or "") == mo, b_present=rc in bc))
            states.append(("month_headers", compare.combine(mh)))
            # 地区の中身
            mem_a = district_members(dtext, dkey, m.lg) if dtext else []
            bi = dist_ix.get((page, tix))
            mem_state = compare.compare_members([dict(x, name_verbatim=(x.get("extra") or {}).get("name_verbatim")) for x in mem_a],
                                                b_mem.get(bi) if bi is not None else None) if mem_a else "single"
            states.append(("members", mem_state))
            # 表の形 (コードの 2 つのやり方)
            st = t.get("structure") or {"ok": False, "issues": ["罫線の格子を作っていない"]}
            struct_item = None
            if not st.get("ok"):
                struct_item = f"{m.lg}:{pid}:structure:{sid}:p{page + 1}:t{tix}"
                queue.append({"item_id": struct_item, "kind": "structure", "status": "open", "issues": st.get("issues"),
                              "readings": {"A": {"reader": pdf_layout.FT_VERSION}, "B": {"reader": pdf_layout.RULING_VERSION}},
                              "evidence": {"source_id": sid, "page": page + 1, "bbox": [round(v, 1) for v in t["bbox"]]},
                              "source_sha256": src["sha256"]})
            mem_final = expand_numbered(mem_a) if mem_state == "agreed" else mem_a
            for mm in mem_final:
                mm["ref"] = {"source_id": sid, "page": page + 1, "table": tix}
            if not mem_final:
                mem_final = [{"text": dtext or m.name, "unit": "unparsed", "machiaza_id": None, "match": "unchecked", "conditions": [],
                              "geometry": "unresolved", "extra": {"name_verbatim": dtext or m.name}}]
            # 同じ地区がほかの資料 (HTML の表) にあれば、その地区にする (資料どうし。§3.11)
            same = next((d for d in m.districts if d["members"] and mem_a and members.same(
                [dict(x, name_verbatim=(x.get("extra") or {}).get("name_verbatim")) for x in d["members"]],
                [dict(x, name_verbatim=(x.get("extra") or {}).get("name_verbatim")) for x in mem_a])), None)
            if same is not None:
                did = same["district_id"]
                same.setdefault("extra", {}).setdefault("also_in", []).append({"source_id": sid, "page": page + 1, "table": tix})
            else:
                m.districts.append({"district_id": did, "partition_id": "all", "label": label,
                                    "label_kind": "official",
                                    "members": [{k: v for k, v in mm.items() if k in ("text", "unit", "machiaza_id", "match", "conditions",
                                                                                     "geometry", "ref", "extra")} for mm in mem_final],
                                    "place_resolution": schema.weakest([mm["geometry"] for mm in mem_final]),
                                    "ref": {"source_id": sid, "page": page + 1, "table": tix},
                                    "extra": {"district_key": dkey, "course_label": course.get("text"),
                                              "members_state": mem_state}})
            tinfo[tix] = {"did": did, "states": states, "struct_item": struct_item, "b_rec": b_rec, "bc": bc, "dtab": dtab,
                          "label": label, "b_present": b_ans is not None}
        # 方針の文 (表の外の行と、表の下の注意の枠)
        lines, lines_by_sel = [], {}
        for i, (ln, owner) in enumerate(notes):
            hp = [tinfo[owner]["label"]] if owner is not None and owner in tinfo else []
            sel = f"page:{page + 1}/line:{i}" + (f"/table:{owner}" if owner is not None else "")
            pl = PdfLine(ln["visible_text"].strip(), hp, sel, page, ln["bbox"])
            pl.owner = owner
            lines.append(pl)
            lines_by_sel[sel] = pl
        sents = content.sentences(lines)
        ctx["sources"][sid]["sentences"] += sents
        excs_all, used_all, loose_all = [], [], []
        # 表の注 (その表の地区だけ) と、ページの文 (ページの地区すべて) を分けて読む
        page_dids = sorted({ti["did"] for ti in tinfo.values()})
        for owner in sorted({getattr(s.line, "owner", None) for s in sents}, key=lambda o: -1 if o is None else o):
            ss = [s for s in sents if getattr(s.line, "owner", None) == owner]
            excs, used, loose = expand.exceptions_from_sentences(ss, per, cats_for_scope, sid, start=m.n_exc + 1)
            m.n_exc += len(excs)
            used = expand.consume_parentheticals(ss, used)
            for e in excs:
                e.setdefault("scope", {})["district_ids"] = [tinfo[owner]["did"]] if owner is not None and owner in tinfo else list(page_dids)
                if e.get("span"):
                    _span_page(e["span"], lines_by_sel)
            excs_all += excs
            used_all += used
            loose_all += loose
        used_texts = [s.text for s in used_all]
        quals = content.scan_qualifiers(sents, own_names=(m.name,))
        unconsumed = []
        for q in quals:
            if consume.used(q["sentence"]):
                continue                            # その文そのものが使われたときだけ (文字の同じほかの文が使われても残す)
            owner = getattr(q["sentence"].line, "owner", None)
            q["scope"] = {"district_ids": [tinfo[owner]["did"]]} if owner is not None and owner in tinfo else {"district_ids": list(page_dids)}
            unconsumed.append(q)
        # B の方針と比べる (deferred・caveat・但し書きは和を取る)
        pres = compare.compare_policy(excs_all, used_texts, unconsumed, b_pol, per, sentences=sents)
        for xd in pres["deferred_b_only"]:
            p_name = (content.period_names(xd.get("period") or xd.get("span") or "") or [xd.get("period") or "年末年始"])[0]
            w = expand._window_for(p_name, None, per)
            e = {"exception_id": f"e{m.n_exc + 1}", "kind": "deferred", "period_name": p_name, "window": w, "overrides_printed": True,
                 "scope": {"category_ids": "*", "district_ids": list(page_dids)}, "test": {"period": p_name, "marker": "llm"},
                 "text": xd.get("span") or p_name, "span": {"source_id": sid, "page": page + 1, "text": xd.get("span") or p_name},
                 "extra": {"from_reader": "llm.policy.B"}}
            m.n_exc += 1
            excs_all.append(e)
        for xc in pres["caveats_b_only"]:
            e = {"exception_id": f"e{m.n_exc + 1}", "kind": "caveat", "window": None,
                 "scope": {"category_ids": "*", "district_ids": list(page_dids)}, "test": {"period": None, "marker": "llm"},
                 "text": xc.get("span") or "", "span": {"source_id": sid, "page": page + 1, "text": xc.get("span") or ""},
                 "extra": {"from_reader": "llm.policy.B"}}
            m.n_exc += 1
            excs_all.append(e)
        for xq in pres["qualifiers_b_only"]:
            t = xq.get("span") or xq.get("text") or ""
            unconsumed.append({"qualifier_id": "q" + content.sha256_text(content.squash(t))[:10], "text": t, "months":
                               sorted(content.named_months(t)), "scope": {"district_ids": list(page_dids)}, "from_reader": "llm.policy.B"})
        pol_item = None
        if pres["notes"] or any(pres[k] == "disputed" for k in ("holiday_policy", "off_days", "closure_pairs", "others", "office")):
            pol_item = f"{m.lg}:{pid}:policy:{sid}:p{page + 1}"
            queue.append({"item_id": pol_item, "kind": "policy", "status": "open", "notes": pres["notes"],
                          "policy_part": "others" if pres["others"] == "disputed" else None,
                          "evidence": {"source_id": sid, "page": page + 1}, "source_sha256": src["sha256"]})
        ver_item = None
        if pres["valid"] == "disputed":
            ver_item = f"{m.lg}:{pid}:version:{sid}"
            queue.append({"item_id": ver_item, "kind": "version", "status": "open", "notes": pres["notes"], "source_sha256": src["sha256"]})
        for q in unconsumed:
            queue.append({"item_id": f"{m.lg}:{pid}:qualifier:{sid}:{q['qualifier_id']}", "kind": "qualifier", "status": "open",
                          "text": q["text"][:200], "months": q.get("months"), "source_sha256": src["sha256"],
                          "note": "使われなかった但し書き。人が「意味が無い」「この値で使う」「印字に入っている」と決めるまで止める (§3.8 の 4)"})
        ctx["sources"][sid]["unconsumed"] += [{k: v for k, v in q.items() if k != "sentence"} for q in unconsumed]
        m.exceptions += excs_all
        src["extra"].setdefault("pages", []).append({"page": page + 1, "policy_compare": {k: v for k, v in pres.items()
                                                                                          if k in ("holiday_policy", "off_days", "closure_pairs",
                                                                                                   "others", "valid", "office")},
                                                     "policy_notes": pres["notes"], "loose": [l["text"][:80] for l in loose_all],
                                                     "qualifiers_unconsumed": [{"qualifier_id": q["qualifier_id"], "text": q["text"][:120],
                                                                                "months": q.get("months")} for q in unconsumed]})
        # 系列
        for t in tables:
            ti = tinfo[t["index"]]
            dtab, did, bc = ti["dtab"], ti["did"], ti["bc"]
            tix = t["index"]
            blank = bool(dtab["blank_cells"])
            if blank:
                queue.append({"item_id": f"{m.lg}:{pid}:markup:{sid}:p{page + 1}:t{tix}:blank", "kind": "markup", "status": "open",
                              "rule": "blank", "count": len(dtab["blank_cells"]), "source_sha256": src["sha256"],
                              "note": "日付の表 (地区 × 品目 × 月) の空のマス。型の決まり (b) に入らず、凡例の説明も無い (§3.6)。人が決まりを足すまで止める"})
            table_states = [s for k, s in ti["states"]]
            for cat in dtab["categories"]:
                label = cat["label"]
                cid = cat_ids[label]
                cobj = m.categories[cid]
                if cobj["mode"] not in schema.DATED_MODES:
                    continue
                rc_label = (cat["pos"], cat["label_cell"]["span"][1]) if dtab["orientation"] == "months_in_row" else \
                    (cat["label_cell"]["span"][0], cat["pos"])
                lab_state = "single" if cat["label_broken"] else compare.state(
                    compare.label_same(cat["label_cell"]["text"], (bc.get(rc_label) or {}).get("text")), b_present=rc_label in bc)
                day_cells = [c for c in cat["cells"] if c["status"] != "merged"]
                if not day_cells:
                    continue                                # 月をまたぐ規則のマスだけの品目 (下の rule_cells で読む)
                dates, cell_states, cell_items, raw_dates = [], [], [], []
                for c in day_cells:
                    if c["status"] == "days":
                        ds = [_iso(c["month"], d, per) for d in c["days"]]
                        if any(x is None for x in ds):
                            cell_states.append("disputed")
                            continue
                        dates += ds
                        raw_dates += ds
                        r0, c0 = c["span"][0], c["span"][1]
                        cell_states.append(compare.compare_day_cell(c["days"], bc.get((r0, c0))) if ti["b_present"] else "single")
                    elif c["status"] == "blank":
                        r0, c0 = c["span"][0], c["span"][1]
                        cell_states.append(compare.compare_day_cell([], bc.get((r0, c0))) if ti["b_present"] else "single")
                    elif c["status"] == "broken":
                        cell_states.append("single")
                    else:
                        item = f"{m.lg}:{pid}:cell:{did}:{cid}:{c['month']:02d}"
                        queue.append({"item_id": item, "kind": "cell", "status": "open", "text": c.get("text"),
                                      "note": "日の並びとして読めないマス", "source_sha256": src["sha256"]})
                        cell_items.append(item)
                        cell_states.append("disputed")
                # 印字の日付も方針の B を見る (期間・deferred・但し書きは 2 つの読みの和)。B がページを読んでいなければ single
                pol_states = compare.policy_states_for(["valid"], pres) if pol_read else ["single"]
                st_all = table_states + [lab_state, cat_state.get(label, "single")] + cell_states + pol_states + \
                    ["agreed" if not ti["struct_item"] else "disputed"]
                status = compare.combine(st_all)
                disputes = list(cell_items)
                if ti["struct_item"]:
                    disputes.append(ti["struct_item"])
                if pol_item and any(s == "disputed" for s in pol_states):
                    disputes.append(pol_item)
                disputes += _dispute_items(m, pid, sid, src, did, cid, dict(ti["states"] + [
                    ("label", lab_state), ("category", cat_state.get(label)), ("cells", compare.combine(cell_states))]),
                    {"A": {"reader": pdf_layout.READER_VERSION, "dates": sorted(dates)}, "B": {"reader": llm_tasks.reader_name("cells")}},
                    len(dates), ti["b_present"])
                if ver_item:
                    disputes.append(ver_item)
                single = status == "single"
                quals_for = [{"qualifier_id": q["qualifier_id"], "months": q.get("months") or []}
                             for q in unconsumed if did in ((q.get("scope") or {}).get("district_ids") or [did])]
                readers = [{"reader": pdf_layout.READER_VERSION, "kind": "code", "inputs": {"source_sha256": src["sha256"], "page": page + 1,
                                                                                           "table": tix}},
                           {"reader": llm_tasks.reader_name("cells"), "kind": "llm", "model": "deepseek-flash", "prompt": "cells.B@1",
                            "inputs": {"image_sha256": (ti["b_rec"].get("image_sha256") or [None])[0]}, "agree": status in compare.OK}]
                spec = {"district_id": did, "category_id": cid, "period": per, "origin": "printed",
                        "printed_dates": sorted(set(dates)), "exceptions": [], "rest_consumed": True,
                        "qualifiers": quals_for, "markup_unhandled": ["blank"] if blank else [], "disputes": disputes,
                        "provenance": {"readers": readers, "independence": "code_llm", "status": status if status != "disputed" or disputes else "disputed",
                                       "spans": [{"source_id": sid, "page": page + 1, "text": cat["label_cell"]["text"][:200],
                                                  "bbox": cat["label_cell"]["bbox"]}], "adjudication": None},
                        "ref": {"source_id": sid, "page": page + 1, "table": tix}, "gate_unit": "D",
                        "extra": {"column_note": (cat.get("note") or {}).get("japanese"), "states": dict(ti["states"] + [
                            ("label", lab_state), ("category", cat_state.get(label)), ("cells", compare.combine(cell_states)),
                            ("policy", compare.combine(pol_states))]), "policies_reader": pol_reader},
                        "extra_blocked": ["single_reading"] if single else []}
                raw.append({"district_id": did, "category_id": cid, "period_id": pid, "origin": "printed", "dates": sorted(set(raw_dates)),
                            "blank_months": [c["month"] for c in day_cells if c["status"] == "blank"], "status": status})
                _merge_or_add(m, spec, sid, excs_all)
            # 月をまたぐ規則のマス (毎週の品目・「市では収集しません」)
            for r in dtab["rule_cells"]:
                label = r["category_label"]
                cid = cat_ids.get(label)
                if cid is None:
                    continue
                cobj = m.categories[cid]
                if cobj["mode"] not in schema.DATED_MODES:
                    continue
                text = (r.get("japanese") or "\n".join(pdf_layout.japanese_lines(r["text"])[0])).replace("\n", " ").strip()
                parsed = rules.parse(text)
                bi = rule_ix.get((page, tix, id(r)))
                rule_state = compare.compare_rule(parsed, b_rules.get(bi) if bi is not None else None)
                rule_span = {"source_id": sid, "page": page + 1, "text": text or label, "bbox": r["bbox"]}
                # 資料の品目 (方針の文と同じ cats_for_scope) を渡す: ほかの品目を名指しする文は読み残し、そのマスの品目の文だけ当てる
                ce, left = expand.cell_notes(parsed.rest, per, {"category_ids": [cid], "district_ids": [did]}, dict(rule_span, text=parsed.rest),
                                             start=m.n_exc + 1, prefix="e", cats=cats_for_scope)
                m.n_exc += len(ce)
                m.exceptions += ce
                cell_quals = [{"qualifier_id": expand.rest_qualifier_id(x), "months": sorted(content.named_months(x))} for x in left]
                for xq in left:
                    queue.append({"item_id": f"{m.lg}:{pid}:qualifier:{sid}:{expand.rest_qualifier_id(xq)}", "kind": "qualifier",
                                  "status": "open", "text": xq[:200], "source_sha256": src["sha256"],
                                  "note": "規則のマスの読み残し (そのマスの地区・品目だけを止める)"})
                    ctx["sources"][sid]["unconsumed"].append({"qualifier_id": expand.rest_qualifier_id(xq), "text": xq, "months":
                                                              sorted(content.named_months(xq)),
                                                              "scope": {"category_ids": [cid], "district_ids": [did]}})
                hk = ["holiday_policy", "off_days", "closure_pairs", "others", "valid"]
                pol_states = compare.policy_states_for(hk, pres) if pol_read else ["single"]
                st_all = table_states + [rule_state, cat_state.get(label, "single")] + pol_states + \
                    ["agreed" if not ti["struct_item"] else "disputed"]
                status = compare.combine(st_all)
                disputes = []
                if ti["struct_item"]:
                    disputes.append(ti["struct_item"])
                if pol_item and any(s == "disputed" for s in pol_states):
                    disputes.append(pol_item)
                if ver_item:
                    disputes.append(ver_item)
                disputes += _dispute_items(m, pid, sid, src, did, cid, dict(ti["states"] + [
                    ("rule", rule_state), ("category", cat_state.get(label))]),
                    {"A": {"reader": "code.rules@1", "text": text, "ir": parsed.irs},
                     "B": {"reader": llm_tasks.reader_name("rules"), "value": b_rules.get(bi)}}, 0, bool(b_rules))
                quals_for = [{"qualifier_id": q["qualifier_id"], "months": q.get("months") or []}
                             for q in unconsumed if did in ((q.get("scope") or {}).get("district_ids") or [did])] + cell_quals
                readers = [{"reader": "code.rules@1", "kind": "code", "inputs": {"source_sha256": src["sha256"], "page": page + 1, "table": tix}},
                           {"reader": llm_tasks.reader_name("rules"), "kind": "llm", "model": "deepseek-flash", "prompt": "rules.B@1",
                            "inputs": {}, "agree": rule_state == "agreed"}]
                spec = {"district_id": did, "category_id": cid, "period": per, "origin": "expanded",
                        "rule": {"text": text or label, "span": rule_span, "parsed": parsed},
                        "printed_pending": bool(evidence), "exceptions": [], "rest_consumed": True,
                        "qualifiers": quals_for, "markup_unhandled": ["blank"] if blank else [], "disputes": disputes,
                        "provenance": {"readers": readers, "independence": "code_llm", "status": status, "spans": [rule_span],
                                       "adjudication": None},
                        "ref": {"source_id": sid, "page": page + 1, "table": tix}, "gate_unit": "D",
                        "extra": {"rule_status": parsed.status, "states": dict(ti["states"] + [("rule", rule_state),
                                                                                             ("policy", compare.combine(pol_states))]),
                                  "policies_reader": pol_reader,
                                  "printed_evidence": [e["url"] for e in evidence][:5]},
                        "extra_blocked": ["single_reading"] if status == "single" else []}
                _merge_or_add(m, spec, sid, excs_all + ce)


STATE_KIND = {"district_text": "label", "course_label": "label", "month_headers": "label", "label": "label", "members": "members",
              "category": "category", "cells": "cell", "rule": "rule"}
TABLE_STATES = ("district_text", "course_label", "month_headers", "members")


def _dispute_items(m, pid, sid, src, did, cid, states, readings, days, b_present=True):
    """値の状態 {名前: 状態} のうち disputed のもの → 待ち行列の項目 (表の値は表ごと、品目は資料ごと、マス・規則は系列ごと) の id のリスト。
    single (読みが 1 つ。壊れた字など) も B を読んだときは待ち行列に入れる (止めるのは single_reading。id は返さない)"""
    out = []
    for name, st in states.items():
        if name not in STATE_KIND or st not in ("disputed", "single", "both_unsure"):
            continue
        if st != "disputed":
            if b_present:
                m.queue.append({"item_id": f"{m.lg}:{pid}:{STATE_KIND[name]}:{did}:{cid}:{name}:single", "kind": STATE_KIND[name],
                                "status": "open", "source_sha256": src["sha256"], "value": name, "series": [[did, cid, pid]],
                                "note": "読みが 1 つしか無い (壊れた字・B の読み落とし)。single_reading で止めている"})
            continue
        kind = STATE_KIND[name]
        if name in TABLE_STATES:
            iid = f"{m.lg}:{pid}:{kind}:{did}:{name}"
        elif name == "category":
            iid = f"{m.lg}:{pid}:category:{sid}:{cid}"
        else:
            iid = f"{m.lg}:{pid}:{kind}:{did}:{cid}"
        m.queue.append({"item_id": iid, "kind": kind, "status": "open", "source_sha256": src["sha256"], "value": name,
                        "readings": readings if kind in ("cell", "rule") else None, "series": [[did, cid, pid]], "days": days})
        out.append(iid)
    return out


def _merge_or_add(m, spec, sid, excs):
    """同じ地区・品目・期間の系列がほかの資料にあれば 1 つにまとめる (印字があれば印字を正にし、ほかの資料の規則は印字との比べに使う。
    規則どうしは比べ、違えば rule の裁定へ)。無ければ足す"""
    key = (spec["district_id"], spec["category_id"], spec["period"]["period_id"])
    prev = next((s for s in m.schedules if (s["district_id"], s["category_id"], s["period"]["period_id"]) == key), None)
    spec["exceptions"] = list(excs)
    if prev is None:
        m.schedules.append(spec)
        return
    m.dup["merged"] += 1
    prev["exceptions"] = list(prev.get("exceptions") or []) + [e for e in excs if e not in (prev.get("exceptions") or [])]
    for q in spec.get("qualifiers", []):
        if q["qualifier_id"] not in {x["qualifier_id"] for x in prev.get("qualifiers", [])}:
            prev.setdefault("qualifiers", []).append(q)
    prev["markup_unhandled"] = sorted(set(prev.get("markup_unhandled", [])) | set(spec.get("markup_unhandled", [])))
    prev["disputes"] = list(dict.fromkeys(list(prev.get("disputes", [])) + spec.get("disputes", [])))
    prev["provenance"]["readers"] = prev["provenance"].get("readers", []) + spec["provenance"]["readers"]
    prev.setdefault("extra", {}).setdefault("also_in", []).append(spec["ref"])
    if spec["origin"] == "printed" and prev["origin"] == "expanded":
        # 印字が正。ほかの資料の規則は印字と全部の月で比べる (expand_series の rule_vs_printed)
        prev["origin"] = "printed"
        prev["item_prefix"] = f"{m.lg}:{key[2]}:rule_vs_printed"
        prev["printed_dates"] = spec["printed_dates"]
        prev["printed_pending"] = False
        prev["gate_unit"] = spec["gate_unit"]
        prev["provenance"]["status"] = compare.combine([prev["provenance"]["status"], spec["provenance"]["status"]])
        prev["extra"]["printed_from"] = spec["ref"]
        prev["extra"]["column_note"] = (spec.get("extra") or {}).get("column_note")
        prev["extra_blocked"] = [b for b in prev.get("extra_blocked", []) if b != "single_reading"] + \
            [b for b in spec.get("extra_blocked", []) if b not in prev.get("extra_blocked", [])]
        if prev["provenance"]["status"] == "single" and "single_reading" not in prev["extra_blocked"]:
            prev["extra_blocked"].append("single_reading")
    elif spec["origin"] == "expanded" and prev["origin"] == "expanded":
        same = markup._same_parsed(prev["rule"]["parsed"], spec["rule"]["parsed"])
        m.dup["agreed" if same else "disputed"] += 1
        if not same:
            item = f"{m.lg}:{key[2]}:rule:{key[0]}:{key[1]}:vs:{sid}"
            m.queue.append({"item_id": item, "kind": "rule", "status": "open",
                            "readings": {"A": {"text": prev["rule"]["text"]}, "B": {"source_id": sid, "text": spec["rule"]["text"]}},
                            "note": "同じ地区・品目の規則が、ほかの資料と違う", "series": [list(key)]})
            prev["disputes"].append(item)
        prev["printed_pending"] = prev.get("printed_pending") or spec.get("printed_pending")
        prev["provenance"]["status"] = compare.combine([prev["provenance"]["status"], spec["provenance"]["status"]])
    elif spec["origin"] == "printed" and prev["origin"] == "printed":
        same = sorted(prev.get("printed_dates") or []) == sorted(spec.get("printed_dates") or [])
        m.dup["agreed" if same else "disputed"] += 1
        if not same:
            item = f"{m.lg}:{key[2]}:cell:{key[0]}:{key[1]}:vs:{sid}"
            m.queue.append({"item_id": item, "kind": "cell", "status": "open", "note": "同じ地区・品目の印字の日付が、ほかの資料と違う",
                            "series": [list(key)]})
            prev["disputes"].append(item)
        prev["provenance"]["status"] = compare.combine([prev["provenance"]["status"], spec["provenance"]["status"]])
    else:                                            # 先が印字で、あとが規則: 規則を印字との比べに使う
        if spec["origin"] == "expanded" and prev.get("rule") is None:
            prev["rule"] = spec["rule"]
        prev["provenance"]["status"] = compare.combine([prev["provenance"]["status"], spec["provenance"]["status"]])


# ---- HTML の方針の B ----
def html_lines(x):
    """HTML の資料の本文の範囲の行 (stage2 と同じ作り方) → 行のリストか None"""
    body, meta = stage2.body_of(x["url"])
    if body is None:
        return None
    hdr = (meta or {}).get("headers") or {}
    try:
        html = decode.decode(body, x.get("content_type") or hdr.get("content-type", "")).text
    except decode.DecodeError:
        return None
    host = urllib.parse.urlsplit(x["url"]).hostname or ""
    return content.content_region(html, boilerplate=fetch_docs.load_boilerplate(host)).lines()


def html_policy_b(m, x, sid, llm, a, ctx, lines=None):
    """HTML の資料の本文の範囲を方針の B で読み、A (stage2 の例外) と比べる → その資料の系列を直す"""
    lines = lines if lines is not None else html_lines(x)
    if lines is None:
        return
    # 文は stage2 が読んだもの (使った印 consumed つき) を使う。無ければ (stage2 を通っていない) 読み直す (使った印が無いので、どの文も
    # 使っていないものとして比べる。止める方)
    sents = (getattr(m, "sentences", None) or {}).get(sid) or content.sentences(lines)
    ctx["sources"][sid] = {"kind": "html", "sentences": sents, "unconsumed": [], "table_pages": None}
    src = next(s for s in m.sources if s["source_id"] == sid)
    for q in (src.get("extra") or {}).get("qualifiers_unconsumed", []):
        ctx["sources"][sid]["unconsumed"].append({**q, "scope": "*"})
    a_excs = [e for e in m.exceptions if (e.get("span") or {}).get("source_id") == sid]
    # A が使った文: 関門 (consume) を通った文そのものと、マスの注の切れ端 (cell_notes の例外の原文)
    used_texts = [s.text for s in sents if consume.used(s)] + \
        [e.get("text", "") for e in a_excs if e.get("template", "").startswith(("cell_holiday:", "cell_year_end:"))]
    quals = content.scan_qualifiers(sents, own_names=(m.name,))
    specs = [s for s in m.schedules if (s.get("ref") or {}).get("source_id") == sid]
    if not specs:
        return
    if not (a.llm or a.offline):
        return
    per = specs[0]["period"]
    ans = [llm.ask("policy", t)[0] for t in llm_tasks.policy_text(lines, title=x.get("title") or "")]
    b = llm_tasks.merge_policy(ans)
    if b.get("missing") == len(ans):
        return
    # A の但し書き: stage2 が実際に使わなかった但し書き (文つき) と、系列ごとのマス・名前の但し書き (その地区・品目だけを止めるもの)。
    # 洗い出したすべての文ではない (名前のマスの文・読み切ったマスの文を A の但し書きに数えると、B が A の範囲の誤りを正せない)。
    # stage2 を通っていない資料 (使わなかった但し書きの記録が無い) は、使っていない文のすべて (止める方)
    left = (getattr(m, "unconsumed", None) or {}).get(sid)
    if left is None:
        a_quals = [q for q in quals if not consume.used(q["sentence"])]
    else:
        a_quals = [q for q in left if not consume.used(q["sentence"])] + \
            [{"text": q["text"]} for q in (src.get("extra") or {}).get("qualifiers_cells", [])]
    pres = compare.compare_policy(a_excs, used_texts, a_quals, b, per, sentences=sents)
    src.setdefault("extra", {})["policy_compare"] = {k: v for k, v in pres.items() if k in ("holiday_policy", "off_days", "closure_pairs",
                                                                                          "others", "valid", "office")}
    src["extra"]["policy_notes"] = pres["notes"]
    pid = per["period_id"]
    pol_item = None
    if pres["notes"] or any(pres[k] == "disputed" for k in ("holiday_policy", "off_days", "closure_pairs", "others", "office")):
        pol_item = f"{m.lg}:{pid}:policy:{sid}"
        m.queue.append({"item_id": pol_item, "kind": "policy", "status": "open", "notes": pres["notes"],
                        "policy_part": "others" if pres["others"] == "disputed" else None, "source_sha256": src.get("sha256")})
    ver_item = None
    if pres["valid"] == "disputed":
        ver_item = f"{m.lg}:{pid}:version:{sid}"
        m.queue.append({"item_id": ver_item, "kind": "version", "status": "open", "notes": pres["notes"], "source_sha256": src.get("sha256")})
    new_excs = []
    for xd in pres["deferred_b_only"]:
        p_name = (content.period_names(xd.get("period") or xd.get("span") or "") or [xd.get("period") or "年末年始"])[0]
        e = {"exception_id": f"e{m.n_exc + 1}", "kind": "deferred", "period_name": p_name, "window": expand._window_for(p_name, None, per),
             "overrides_printed": True, "scope": {"category_ids": "*"}, "test": {"period": p_name, "marker": "llm"},
             "text": xd.get("span") or p_name, "span": {"source_id": sid, "text": xd.get("span") or p_name}, "extra": {"from_reader": "llm.policy.B"}}
        m.n_exc += 1
        new_excs.append(e)
    for xc in pres["caveats_b_only"]:
        e = {"exception_id": f"e{m.n_exc + 1}", "kind": "caveat", "window": None, "scope": {"category_ids": "*"},
             "test": {"period": None, "marker": "llm"}, "text": xc.get("span") or "", "span": {"source_id": sid, "text": xc.get("span") or ""},
             "extra": {"from_reader": "llm.policy.B"}}
        m.n_exc += 1
        new_excs.append(e)
    m.exceptions += new_excs
    b_quals = []
    for xq in pres["qualifiers_b_only"]:
        t = xq.get("span") or xq.get("text") or ""
        qid = "q" + content.sha256_text(content.squash(t))[:10]
        b_quals.append({"qualifier_id": qid, "months": sorted(content.named_months(t))})
        m.queue.append({"item_id": f"{m.lg}:{pid}:qualifier:{sid}:{qid}", "kind": "qualifier", "status": "open", "text": t[:200],
                        "note": "B (LLM) だけが挙げた但し書き (2 つの読みの和。§3.8 の 3)", "source_sha256": src.get("sha256")})
        ctx["sources"][sid]["unconsumed"].append({"qualifier_id": qid, "text": t, "months": sorted(content.named_months(t)), "scope": "*"})
    for s in specs:
        s["exceptions"] = list(s.get("exceptions") or []) + new_excs
        for q in b_quals:
            if q["qualifier_id"] not in {x["qualifier_id"] for x in s.get("qualifiers", [])}:
                s.setdefault("qualifiers", []).append(q)
        kinds = ["holiday_policy", "off_days", "closure_pairs", "others", "valid"] if s["origin"] == "expanded" else ["valid"]
        ps = compare.policy_states_for(kinds, pres) + ([] if not b.get("missing") else ["single"])   # B が一部を読めていなければ single
        if all(x in compare.OK for x in ps):
            s["extra_blocked"] = [b for b in s.get("extra_blocked", []) if b != "single_reading"]
            s.setdefault("extra", {})["policies_reader"] = "code_llm"
        elif any(x == "disputed" for x in ps):
            for it in (pol_item, ver_item):
                if it and it not in s["disputes"]:
                    s["disputes"].append(it)
            s.setdefault("extra", {})["policies_reader"] = "code_llm_disputed"


# ---- 但し書きの待ち行列 ----
def _qualifier_items(m):
    """待ち行列の qualifier の項目 → {qualifier_id: 項目} (item_id の最後が qualifier_id)"""
    out = {}
    for it in m.queue:
        if it.get("kind") == "qualifier":
            out.setdefault(it["item_id"].rsplit(":", 1)[-1], it)
    return out


def _add_series(it, key):
    if list(key) not in it.setdefault("series", []):
        it["series"].append(list(key))


def enqueue_source_qualifiers(m, sid):
    """stage2 の資料 (HTML・CSV) の使われなかった但し書き (A。source.extra.qualifiers_unconsumed) → 待ち行列の qualifier (§3.8 の 4)。
    同じ qualifier_id がもう待ち行列にあれば、止める系列だけを足す。この資料の系列も当たる系列も無ければ (止めるものが無い) 入れない"""
    src = next((s for s in m.sources if s["source_id"] == sid), None)
    if src is None:
        return
    specs = [s for s in m.schedules if (s.get("ref") or {}).get("source_id") == sid]
    have = _qualifier_items(m)
    for q in (src.get("extra") or {}).get("qualifiers_unconsumed") or []:
        qid = q.get("qualifier_id")
        if not qid:
            continue
        hit = [s for s in m.schedules if qid in {x.get("qualifier_id") for x in s.get("qualifiers") or []}]
        it = have.get(qid)
        if it is None:
            if not (hit or specs):
                continue
            pid = (hit or specs)[0]["period"]["period_id"]
            it = {"item_id": f"{m.lg}:{pid}:qualifier:{sid}:{qid}", "kind": "qualifier", "status": "open",
                  "text": (q.get("text") or "")[:200], "months": q.get("months"), "source_sha256": src.get("sha256"), "series": [],
                  "note": "使われなかった但し書き (A)。人が「意味が無い」「この値で使う」「印字に入っている」と決めるまで止める (§3.8 の 4)"}
            m.queue.append(it)
            have[qid] = it
        for s in hit:
            _add_series(it, (s["district_id"], s["category_id"], s["period"]["period_id"]))


_NO_TEXT = " (文は読みの記録に無い。系列の ref の資料で探す)"


def ensure_qualifier_items(m, doc, ctx):
    """系列を止めている但し書き (材料の qualifiers・規則のマスの読み残し、出来た系列の blocked_by の unconsumed_qualifier:<id>・
    unknown_ranges の unconsumed_qualifier) のどれにも、待ち行列の qualifier があるようにする (無ければ足す)。
    文と月は読みの記録 (ctx の unconsumed)、資料の extra (qualifiers_unconsumed・地区と品目の名前のマスの書き添え
    qualifiers_name_cells)、規則の rest から引く。文の無い項目がもうあれば、文と月を埋める"""
    have = _qualifier_items(m)
    texts = {}
    for sid, sc in (ctx.get("sources") or {}).items():
        for q in sc.get("unconsumed") or []:
            texts.setdefault(q["qualifier_id"], (sid, q.get("text"), q.get("months")))
    for src in m.sources:                            # stage2 の資料の記録 (名前のマスの書き添えは ctx に無い)
        ex = src.get("extra") or {}
        for q in (ex.get("qualifiers_unconsumed") or []) + (ex.get("qualifiers_name_cells") or []):
            if q.get("qualifier_id") and q.get("text"):
                texts.setdefault(q["qualifier_id"], (src["source_id"], q["text"], q.get("months")))
    for s in m.schedules:                            # 規則のマスの読み残し (stage2 は文を残さないので、規則の rest から引く)
        rest = getattr((s.get("rule") or {}).get("parsed"), "rest", None) or ""
        for piece in [rest.strip()] + [x.strip() for x in rest.split("/")]:
            if piece:
                texts.setdefault(expand.rest_qualifier_id(piece), ((s.get("ref") or {}).get("source_id"), piece,
                                                                   sorted(content.named_months(piece))))
    need = []
    for s in m.schedules:
        key = (s["district_id"], s["category_id"], s["period"]["period_id"])
        need += [(q["qualifier_id"], key, (s.get("ref") or {}).get("source_id")) for q in s.get("qualifiers") or []]
        rest = getattr((s.get("rule") or {}).get("parsed"), "rest", None)
        if rest and not s.get("rest_consumed"):
            need.append((expand.rest_qualifier_id(rest), key, (s.get("ref") or {}).get("source_id")))
    for s in doc["schedules"]:
        key, sid = schema.series_key(s), (s.get("ref") or {}).get("source_id")
        need += [(b.split(":", 1)[1], key, sid) for b in s.get("blocked_by") or [] if b.startswith("unconsumed_qualifier:")]
        need += [(r["note"], key, sid) for r in s.get("unknown_ranges") or [] if r.get("reason") == "unconsumed_qualifier" and r.get("note")]
    for qid, key, sid in need:
        it = have.get(qid)
        tsid, text, months = texts.get(qid, (None, None, None))
        if it is not None and not it.get("text") and text:          # 文の無い項目 (先に作ったもの) に文と月を埋める
            it["text"] = text[:200]
            if it.get("months") is None and months is not None:
                it["months"] = months
            it["note"] = (it.get("note") or "").replace(_NO_TEXT, "") or None
        if it is None:
            it = {"item_id": f"{m.lg}:{key[2]}:qualifier:{tsid or sid or '-'}:{qid}", "kind": "qualifier", "status": "open",
                  "text": (text or "")[:200] or None, "months": months, "series": [],
                  "note": "系列を止めている但し書きで、待ち行列に無かったもの。人が「意味が無い」「この値で使う」「印字に入っている」と決めるまで止める"
                          + ("" if text else _NO_TEXT)}
            src = next((x for x in m.sources if x["source_id"] == (tsid or sid)), None)
            if src is not None:
                it["source_sha256"] = src.get("sha256")
            m.queue.append(it)
            have[qid] = it
        _add_series(it, key)


# ---- 区分けの決め方 ----
def basis_of(m, texts, llm):
    """A (言い回し) と B (basis.B) → (basis, span, 状態)。資料に書いていなければ unknown (§2.3。境目の見張りを掛ける)"""
    a = "unknown"
    span = None
    for sid, t in texts:
        if stage2.COLLECTION_POINT.search(content.nfkc(t)):
            a = "collection_point"
            mm = stage2.COLLECTION_POINT.search(content.nfkc(t))
            span = {"source_id": sid, "text": content.nfkc(t)[max(0, mm.start() - 30): mm.end() + 30]}
            break
    if a == "unknown":
        for sid, t in texts:
            mm = RESIDENCE.search(content.nfkc(t))
            if mm:
                a = "residence"
                span = {"source_id": sid, "text": content.nfkc(t)[max(0, mm.start() - 30): mm.end() + 30]}
                break
    cand = []
    for sid, t in texts:
        for ln in content.nfkc(t).split("\n"):
            if re.search(r"集積所|ステーション|お住まい|住所|地区|地域|町名|行政区", ln) and len(ln) < 200:
                cand.append(ln.strip())
    cand = list(dict.fromkeys(cand))[:60]
    b, _ = llm.ask("basis", "\n".join(cand)[:6000]) if cand else (None, None)
    st = compare.compare_basis(a, b) if b is not None else "single"
    return a, span, st


# ---- 1 自治体 ----
def build_one(lg, a):
    if lg in REGRESS:
        return build_regress(lg, a)
    p = DATA / lg / "docs.json"
    if not p.exists():
        return {"lg_code": lg, "error": "段 0 の一覧 (data/readers/<lg_code>/docs.json) が無い"}
    docs = json.loads(p.read_text("utf-8"))
    m = stage2.Muni(lg, docs.get("name") or lg)
    llm = LLMRun(a, m)
    ctx = {"sources": {}, "docs_not_fetched": [], "islands": is_island_muni(lg)}
    raw = []
    # 段 1: PDF の型
    analyses = {}
    pdf_kinds = collections.Counter()
    for x in pdf_docs(docs):
        an = analyze_pdf(x["url"])
        if an is None:
            continue
        analyses[x["url"]] = an
        for pg in an["pages"]:
            pdf_kinds[pg["kind"]] += 1
    evidence = printed_evidence(docs, analyses)
    ctx["docs_not_fetched"] = [e for e in evidence if "doc_not_fetched" in e["why"]]
    sel = stage2.select_docs(docs)
    # B の入力をまとめて作り、先に並べて聞く (型 D のマス・品目・規則・地区・方針、HTML の方針)
    jobs, hlines = [], {}
    for x in pdf_docs(docs):
        an = analyses.get(x["url"])
        if an and any(pg["kind"] == "date_table" for pg in an["pages"]):
            an["prep"] = pdf_prep(an)
            jobs += an["prep"]["jobs"]
    for x in sel:
        if x.get("file_type") == "html":
            hlines[x["url"]] = html_lines(x)
            if hlines[x["url"]]:
                jobs += [("policy", t, ()) for t in llm_tasks.policy_text(hlines[x["url"]], title=x.get("title") or "")]
    llm.prefetch(jobs, workers=12)
    # 型 A・B・C (stage2 の読み)
    texts = []
    for x in sel:
        n0 = len(m.sources)
        stage2.read_source(m, x, a, evidence)
        if len(m.sources) > n0:
            sid = m.sources[-1]["source_id"]
            enqueue_source_qualifiers(m, sid)               # A の使われなかった但し書きも裁定の待ち行列へ (§3.8 の 4)
            if x.get("file_type") == "html":
                html_policy_b(m, x, sid, llm, a, ctx, lines=hlines.get(x["url"]))
            else:
                ctx["sources"][sid] = {"kind": "csv", "sentences": [], "table_pages": None, "unconsumed": [
                    {**q, "scope": "*"} for q in (m.sources[-1].get("extra") or {}).get("qualifiers_unconsumed", [])]}
            if x.get("file_type") == "html":
                b, _ = stage2.body_of(x["url"])
                try:
                    texts.append((sid, content.content_region(decode.decode(b, x.get("content_type") or "").text).text()))
                except Exception:
                    pass
    # 型 D
    for x in pdf_docs(docs):
        an = analyses.get(x["url"])
        if an and any(pg["kind"] == "date_table" for pg in an["pages"]):
            read_pdf_source(m, x, an, a, llm, evidence, ctx, raw)
            sid = m.sources[-1]["source_id"]
            texts.append((sid, "\n".join(pg["info"].visible_text() for pg in an["pages"] if pg["kind"] == "date_table")))
    # 区分けの決め方
    basis, bspan, bstate = basis_of(m, texts, llm) if texts else ("unknown", None, "single")
    m.basis, m.basis_span = basis, bspan
    if bstate == "disputed":
        m.queue.append({"item_id": f"{lg}:basis", "kind": "members", "status": "open", "note": "区分けの決め方 (basis) が 2 つの読みで違う"})
    doc = stage2.build_doc(m)
    for s in doc["schedules"]:                      # 印字と規則・例外の違い (expand_series が作った rule_vs_printed・exception_vs_printed) を待ち行列へ
        for it in [x for k in ("rule_vs_printed", "exception_vs_printed") for x in (s.get("extra") or {}).get(k) or []]:
            m.queue.append({**it, "series": [list(schema.series_key(s))], "days": len(it.get("readings", {}).get("printed") or [])})
    ensure_qualifier_items(m, doc, ctx)             # 止めている但し書きのどれにも待ち行列の項目がある
    # 読んでいない資料
    unread = []
    for url, an in analyses.items():
        ks = sorted({pg["kind"] for pg in an["pages"]})
        if "date_table" not in ks:
            unread.append({"url": url, "page_kinds": ks})
    # 確かめ (§4.1)
    res = validate.run(doc, ctx)
    v_items = validate.apply(doc, res, lg)
    # 待ち行列
    q = adjudicate.normalize(m.queue + v_items)
    used = adjudicate.apply_decisions(doc, q, adjudicate.load_decisions(lg))
    for s in doc["schedules"]:
        if s["answerable"] and s["blocked_by"]:
            s["answerable"] = False
            s["dates"], s["unknown_ranges"] = [], []
    # 答えの種類 (M1: 関門はどれも閉じている → 種類 3)
    blocked = collections.Counter(b.split(":")[0] for s in doc["schedules"] for b in s["blocked_by"])
    by_d = {}
    for d in doc["districts"]:
        ss = [s for s in doc["schedules"] if s["district_id"] == d["district_id"]]
        by_d[d["district_id"]] = {"answer_type": "rule_contact",
                                  "why": "関門が閉じている (M1)" if ss and all(s["answerable"] for s in ss) else "答えない系列がある",
                                  "answerable": sum(1 for s in ss if s["answerable"]), "series": len(ss)}
    doc["partitions"][0]["basis"] = basis
    doc["partitions"][0]["basis_span"] = bspan
    doc["coverage"] = {"answer_type": "rule_contact", "by_district": by_d, "population_share": None, "population_basis": None,
                       "blocked_series": dict(blocked),
                       "gate_pending": {"series": sum(1 for s in doc["schedules"] if s["answerable"]), "population_share": None},
                       "quality": None}
    cost = llm_cost(llm.recs)
    doc["build"] = {"builder": BUILDER, "series": len(doc["schedules"]), "answerable": sum(1 for s in doc["schedules"] if s["answerable"]),
                    "published": 0, "blocked_by": dict(blocked), "queue_size": len(q),
                    "queue_kinds": dict(collections.Counter(i["kind"] for i in q)),
                    "queue_next": dict(collections.Counter(i["next"] for i in q)), "decisions_used": used,
                    "compare": dict(m.compare), "cross_source_duplicates": dict(m.dup), "llm": cost,
                    "checks": res.summary(), "pdf_page_kinds": dict(pdf_kinds), "unread_pdfs": unread[:50],
                    "printed_evidence": evidence[:50], "printed_evidence_n": len(evidence), "notes": m.notes[:50],
                    "categories_mode_undecided": m.mode_undecided, "basis_state": bstate,
                    "rule_contact_why": rule_contact_why(doc, pdf_kinds, evidence, sel, analyses),
                    "attribution": "M1 (型 A〜D)。関門 (§4.4) が開くまで published はどれも false"}
    doc["watch"] = {"sources": [{"source_id": s["source_id"], "content_sha256": s.get("content_sha256"), "sha256": s.get("sha256")}
                                for s in doc["sources"]]}
    for c in doc["categories"]:
        c.setdefault("ref", {"source_id": doc["sources"][0]["source_id"]} if doc["sources"] else None)
        if c.get("ref") is None:
            c.pop("ref")
    errs = schema.validate(doc)
    LAST.clear()
    LAST.update({"doc": doc, "queue": q, "raw": raw, "ctx": ctx, "checks": res})
    rec = {"lg_code": lg, "name": doc["name"], "series": len(doc["schedules"]),
           "answerable": doc["build"]["answerable"], "blocked_by": dict(blocked.most_common()), "queue": len(q),
           "queue_kinds": doc["build"]["queue_kinds"], "schema_errors": errs[:5], "n_schema_errors": len(errs),
           "sources": len(doc["sources"]), "districts": len(doc["districts"]), "pdf_page_kinds": dict(pdf_kinds),
           "printed_evidence": len(evidence), "llm": cost, "checks_failed": {k: v["failed"] for k, v in res.summary().items() if v["failed"]}}
    if a.write:
        (DATA / lg).mkdir(parents=True, exist_ok=True)
        write_json(DATA / lg / "build.json", {"lg_code": lg, "raw_printed": raw, "llm_records": [
            {k: v for k, v in r.items() if k in ("task", "prompt", "model", "key", "cached", "missing", "prompt_tokens",
                                                 "completion_tokens", "input_sha256", "image_sha256")} for r in llm.recs],
            "summary": rec})
        adjudicate.write_queue(lg, q)
        if not errs:
            write_json(OUT / f"{lg}.json", doc)
        else:
            write_json(DATA / lg / "schedule_rejected.json", doc)
    return rec


def rule_contact_why(doc, pdf_kinds, evidence, sel, analyses):
    """自治体が答えの種類 3 (規則と窓口) になったわけ (J6 の「理由つき」)"""
    out = {"gate_closed": "M1 では関門 (§4.4) がまだ開かない (published はどれも false)"}
    not_built = {k: v for k, v in pdf_kinds.items() if k in ("month_grid", "pdf_outline", "pdf_image", "rule_table", "translation")}
    if not_built:
        out["reader_not_built"] = {"page_kinds": not_built, "note": "M1 では型 D の日付の表のほかの PDF は読まない (型 F・G・H は M2〜M4)"}
    if evidence:
        out["printed_not_read"] = len(evidence)
    if not doc["schedules"]:
        out["no_series"] = ("M1 の型 (A〜D) の資料から系列を 1 つも作れなかった"
                            f" (読んだ HTML・CSV {len(sel)}・型を見た PDF {len(analyses)})")
    b = collections.Counter(x.split(":")[0] for s in doc["schedules"] for x in s["blocked_by"])
    if b:
        out["blocked_by"] = dict(b.most_common())
    return out


def build_regress(lg, a):
    """金沢・女川: 今のデータを v0.2 に書き直したもの (eval/regress.py) を確かめに通して書く"""
    import regress
    if lg == "172014":
        doc, queue = regress.kanazawa_v02()
    else:
        doc = regress.onagawa_v02()
        queue = []
    res = validate.run(doc, {})
    errs = schema.validate(doc)
    q = adjudicate.normalize([{"item_id": x["item_id"], "kind": "rule_vs_printed", "series": [], **{k: v for k, v in x.items()
                                                                                                    if k not in ("item_id", "kind")}}
                              for x in queue])
    if a.write:                                     # 書くものは eval/regress.py --write と同じ (確かめの結果は書き足さない)
        adjudicate.write_queue(lg, q)
        if not errs:
            regress.write_json(OUT / f"{lg}.json", doc)
    return {"lg_code": lg, "name": doc["name"], "series": len(doc["schedules"]), "answerable": sum(1 for s in doc["schedules"] if s["answerable"]),
            "queue": len(q), "n_schema_errors": len(errs), "checks_failed": {k: v["failed"] for k, v in res.summary().items() if v["failed"]}}


def is_island_muni(lg):
    """有人島のある自治体か (nationwide/designated_islands.csv)"""
    p = HERE.parent / "designated_islands.csv"
    try:
        txt = p.read_text("utf-8")
    except OSError:
        return False
    return lg in txt or lg[:5] in txt


def write_json(p, obj):
    """JSON を書く (LF の改行・最後に改行。eval/regress.py の write_json と同じ形)"""
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n", "utf-8", newline="\n")
    os.replace(tmp, p)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="自治体を選んで段 0〜9 を通し、out/schedules/<lg_code>.json を書く")
    ap.add_argument("--codes", default="")
    ap.add_argument("--tier-a", action="store_true", help="japan-gomi-data の tier A の 17 自治体")
    ap.add_argument("--llm", action="store_true", help="2 つ目の読み (LLM。キャッシュに無ければ API を呼ぶ)")
    ap.add_argument("--offline", action="store_true", help="LLM はキャッシュだけ (キーを使わない)")
    ap.add_argument("--no-write", dest="write", action="store_false")
    a = ap.parse_args(argv)
    if a.offline:
        a.llm = True                                 # キャッシュの B を読む (stage2 の表の B も)。キャッシュに無いものは呼ばずに無いことにする
    codes = [c.strip() for c in a.codes.split(",") if c.strip()] or (list(TIER_A) if a.tier_a else [])
    if a.tier_a and a.codes:
        codes = list(dict.fromkeys(codes + list(TIER_A)))
    tot = collections.Counter()
    for lg in codes:
        say(f"{lg} …")
        try:
            r = build_one(lg, a)
        except Exception as e:                       # 1 つの自治体の失敗で全体を止めない (失敗を記録する)
            import traceback
            traceback.print_exc()
            r = {"lg_code": lg, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        print(json.dumps(r, ensure_ascii=False), flush=True)
        tot["series"] += r.get("series", 0)
        tot["answerable"] += r.get("answerable", 0)
        tot["usd_new"] += (r.get("llm") or {}).get("usd_new", 0)
        tot["usd_all"] += (r.get("llm") or {}).get("usd_all", 0)
    print(json.dumps({"total": dict(tot)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
