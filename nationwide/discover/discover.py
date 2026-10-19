#!/usr/bin/env python3
"""全国の自治体の公式サイトから、家庭ごみの収集のページと資料を探して分類する

  python nationwide/discover/discover.py --pref 17 --limit 10 --no-llm      # 石川県の始めの 10 自治体で試す
  python nationwide/discover/discover.py --codes 172014,045811 --no-llm      # 金沢市・女川町だけ
  python nationwide/discover/discover.py --resume                           # 全国 (済んだ自治体は飛ばす。LLM の判定つき)
  python nationwide/discover/discover.py --codes 045811,172014 --replay --no-llm   # キャッシュだけで探し直す (ネットに出ない)
                                                                            # → out/discover/replay/ (パイロットの結果は変えない)
  → out/discover/<lg_code>.json (自治体ごとの候補)、out/discover/summary.csv (1 行 1 自治体)、
    out/discover/run_log.json (実行ごとのホスト別の取得数)

--resume で飛ばすのは「済んだ」結果だけ: complete で、同じ探し方の値 (params) と VERSION で、unreachable でない
(つながらなかった自治体はやり直す。--no-retry-unreachable で飛ばす。blocked はやり直さない)。
--offline でキャッシュに無いものがあった結果は complete にしない。前の結果があればそれを残し、サイトに入れなければ書かない。
--replay の結果には "replay": true を付け、本番の結果 (out/discover/、または replay の印の無い結果) は上書きしない。
公式サイトが 2 つ以上あれば (台帳の J-LIS の URL など)、前のサイトにつながらないか収集日の資料が見つからなければ次を見る。
結果の start_url は、つながったトップの URL。

入力は out/registry/municipalities.json の official_sites。検索エンジンは使わない。
礼儀 (robots.txt・1 ホスト 1 秒 1 回・同時接続の上限・キャッシュ・ブロックされたら止める) は fetcher.py、
探し方は crawler.py、点数と形式は classify.py、LLM の判定は judge.py。説明は README.md。
キャッシュ・判定の記録・取得の記録 (1 回ごとの URL) は data/discover/ (git に入れない)。
"""
import argparse, concurrent.futures as cf, csv, json, os, pathlib, random, re, sys, time, urllib.parse
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import classify, crawler, fetcher                     # noqa: E402

VERSION = 4
JUDGE_TYPES = ("html", "pdf", "csv", "xlsx", "ics")
OUT_CANDIDATES = 60        # 出力する候補の数 (収集日の資料らしい中を見た候補と、いちばんの候補の資料は数えずに残す)
OUT_SCHEDULE_MAX = 120     # 上の「数えずに残す」候補の上限
OUT_SHAPE_MAX = 3          # 同じ問い合わせの形 (kana.html?kana=… など) のページは、出力ではこの数までにまとめる
SUMMARY_COLS = ["lg_code", "pref", "name", "format_class", "formats_seen", "best_url", "best_score", "best_kinds", "best_judge",
                "best_fiscal_year", "schedule_candidates", "candidates", "apps", "related", "site_status", "start_url",
                "stop_reason", "pages", "hub_pages", "related_pages", "requests", "blocked_url", "blocked_where", "llm",
                "finished_at"]


def say(*a):
    print(*a, flush=True)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="自治体の公式サイトからごみ収集のページ・資料を探す")
    ap.add_argument("--pref", help="都道府県コード (2 桁) か名前。カンマ区切り (例: 17,04)")
    ap.add_argument("--codes", help="自治体コード (6 桁、または検査数字なしの 5 桁)。カンマ区切り")
    ap.add_argument("--limit", type=int, help="選んだ自治体のうち始めの N だけ (コード順)")
    ap.add_argument("--sample", type=int, help="選んだ自治体から N を無作為に選ぶ (パイロット用。--seed で再現できる)")
    ap.add_argument("--seed", type=int, default=20261019, help="--sample の種 (既定 20261019)")
    ap.add_argument("--no-llm", action="store_true", help="LLM の判定をしない")
    ap.add_argument("--resume", action="store_true",
                    help="済んだ自治体は飛ばす (探し方の値・VERSION が違う結果、unreachable の結果、"
                         "LLM を使う実行での LLM なし・判定に失敗した結果はやり直す)")
    ap.add_argument("--no-retry-unreachable", action="store_true", help="--resume で unreachable の結果もやり直さない")
    ap.add_argument("--include-no-service", action="store_true", help="北方領土の村も入れる (公式サイトが無いので not_found になる)")
    g = ap.add_argument_group("探し方")
    g.add_argument("--depth", type=int, default=3, help="リンクをたどる深さ (既定 3。資料はその 1 つ先まで)")
    g.add_argument("--budget", type=int, default=150, help="1 サイトで取るページ・資料の数の上限 (既定 150)")
    g.add_argument("--max-docs", type=int, default=25, help="1 サイトで取る PDF・Excel・CSV・iCalendar の上限 (既定 25)")
    g.add_argument("--after-strong", type=int, default=60, help="収集日の資料らしいものが見つかってから取る数の上限 (既定 60)")
    g.add_argument("--site-minutes", type=float, default=30.0, help="1 サイトにかける時間の上限 (分、既定 30)")
    g.add_argument("--no-site-search", action="store_true", help="サイト内検索 (GET のフォーム) を使わない")
    g.add_argument("--hub-budget", type=int, default=40,
                   help="案内のページ (収集日・地区の言葉のあるもの) の子を取る数の上限 (1 サイト、--budget とは別。既定 40、0 で広げない)")
    g.add_argument("--related-budget", type=int, default=15,
                   help="ほかのサイト (一部事務組合・広域連合・都道府県などの関係する発行元) で取る数の上限 "
                        "(1 サイト、--budget とは別。既定 15、0 で行かない)")
    g.add_argument("--judge-top", type=int, default=5,
                   help="LLM に判定させる候補の数 (自治体ごと、既定 5)。ごみの収集のページのある PDF・収集日らしい表の HTML・"
                        "カレンダー/収集/ごみ の資料 のそれぞれのいちばんは、この数を超えても入れる")
    g.add_argument("--llm-parallel", type=int, default=8, help="LLM に同時に聞く数 (既定 8)")
    g.add_argument("--offline", action="store_true",
                   help="ネットに出ず、キャッシュだけで探し直す (点数の付け直しや、あとから LLM の判定を足すとき)")
    g.add_argument("--replay", action="store_true",
                   help="--offline で探し直し、--out (既定 out/discover/replay/) に書く。キャッシュに無いものがあっても書く "
                        "(complete は false のまま)。パイロットの結果と比べるため。out/discover/ と、replay の印の無い結果には書かない")
    g = ap.add_argument_group("礼儀")
    g.add_argument("--workers", type=int, default=32, help="同時に探す自治体の数 (既定 32)")
    g.add_argument("--max-concurrency", type=int, default=16, help="全体で同時に開く接続の上限 (既定 16)")
    g.add_argument("--gap", type=float, default=1.0, help="1 ホストに何秒に 1 回まで (既定 1。robots.txt の Crawl-delay が長ければそれ)")
    g.add_argument("--ip-gap", type=float, default=0.25,
                   help="同じ IP アドレス (共用のサーバー) への取り始めの間 (秒、既定 0.25 = 1 秒に 4 回まで。0 で見ない)")
    g.add_argument("--max-crawl-delay", type=float, default=60.0,
                   help="robots.txt の Crawl-delay がこれより長いホストは取らない (秒、既定 60)")
    g.add_argument("--timeout", type=float, default=30.0, help="1 回の接続・読み込みの待ち時間 (秒)")
    g.add_argument("--retries", type=int, default=2, help="時間切れ・5xx の取り直しの回数")
    g.add_argument("--backoff", type=float, default=5.0, help="取り直しまでの間 (秒。回ごとに 2 倍)")
    g.add_argument("--max-age-hours", type=float, default=72.0, help="キャッシュがこれより新しければ取りに行かない (既定 72 時間)")
    g.add_argument("--max-requests", type=int, help="この実行で送る取得の数の上限 (届いたら止める。途中の自治体は書かない)")
    g = ap.add_argument_group("場所")
    g.add_argument("--registry", default=str(ROOT / "out" / "registry" / "municipalities.json"))
    g.add_argument("--out", help="結果を書く所 (既定 out/discover/、--replay なら out/discover/replay/)")
    g.add_argument("--data", default=str(ROOT / "data" / "discover"), help="キャッシュ・判定・取得の記録を置く所 (git に入れない)")
    return ap.parse_args(argv)


def select(munis, args):
    rows = [m for m in munis if args.include_no_service or not m.get("no_service")]
    if args.pref:
        want = {p.strip() for p in args.pref.split(",") if p.strip()}
        want |= {p.zfill(2) for p in want if p.isdigit()}
        rows = [m for m in rows if m["lg_code"][:2] in want or m["pref"] in want]
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        rows = [m for m in rows if m["lg_code"] in want or m["lg_code"][:5] in want]
    rows.sort(key=lambda m: m["lg_code"])
    if args.sample and args.sample < len(rows):
        rows = sorted(random.Random(args.seed).sample(rows, args.sample), key=lambda m: m["lg_code"])
    return rows[:args.limit] if args.limit else rows


def _clean(rec, anchors=None):
    """出力する形 (中で使うだけの項目と、ローカルの場所は書かない)"""
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    if "http" in out:
        out["http"] = {k: v for k, v in out["http"].items() if k != "from_cache" and v is not None}
    if anchors:
        out["anchors"] = anchors[:5]
    return out


def scrub_all(obj):
    """書き出す前に、すべての文字から手元の場所を消す (念のため)"""
    if isinstance(obj, str):
        return fetcher.scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_all(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_all(v) for v in obj]
    return obj


def params_of(opts):
    return {**opts.as_dict(), "version": VERSION}


def judgeable(c):
    """LLM に渡せる候補か (中を見ていて、渡す抜粋か画像がある)"""
    return bool(c.get("fetched") and c["file_type"] in JUDGE_TYPES
                and ((c.get("_excerpt") or "").strip() or c.get("format_class") == "pdf_image"))


def pick_for_judge(cands, top, anchors=None, min_score=20):
    """LLM に判定させる候補を選ぶ。必ず入れるもの (点数 10 以上):
      1. ごみの収集のページ (page_hits) のある PDF のいちばん
      2. 収集日らしい表のある HTML のいちばん
      3. URL かリンクの文字に カレンダー・収集・ごみ のある資料 (HTML 以外) のいちばん (1・2 で選んだもの以外)
    残りは classify.judge_rank (点数 + 中身の手がかり − 案内のページ) の順に、点数 min_score 以上を top まで足す。
    必ず入れるものが top より多ければ、それだけにする"""
    if top <= 0:
        return []
    anchors = anchors or {}
    ok = [c for c in cands if judgeable(c)]
    rank = {id(c): classify.judge_rank(c, anchors.get(c["url"], ())) for c in ok}
    ordered = sorted(ok, key=lambda c: (-rank[id(c)], -classify.newest_year(c), c["url"]))
    tests = (lambda c: c["file_type"] == "pdf" and c["features"].get("page_hits"),
             lambda c: c["file_type"] == "html" and c["features"].get("schedule_tables"),
             lambda c: c["file_type"] != "html" and classify.is_cal_doc(c, anchors.get(c["url"], ())))
    must = []
    for t in tests:
        b = next((c for c in ordered if t(c) and c["score"] >= 10 and all(c is not x for x in must)), None)
        if b is not None:
            must.append(b)
    rest = [c for c in ordered if c["score"] >= min_score and all(c is not x for x in must)]
    return must + rest[:max(0, top - len(must))]


INDEX_PARAM = re.compile(r"kana|keyword|^kw$|word|search|query|^q$|initial|moji|^gyou?$|aiueo|^page$|^pg$|^pageno$|sort|order|"
                         r"^mode$|^cate", re.I)


def index_shape(url):
    """出力でまとめてよい問い合わせの形 (無ければ None): 日付で切り替わるもの、五十音・検索・並べ替え・ページ送り・分類の名前の
    あるもの、値がどれも 2 字までの文字 (kana=あ など) のもの。CMS のページの番号 (?content=123・?page_id=5) はまとめない"""
    shape, datey = crawler.variant_shape(url)
    if shape is None:
        return None
    params = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    if datey or any(INDEX_PARAM.search(k) for k, _ in params) or             all(len(v) <= 2 and not v.isdigit() for _, v in params):
        return shape
    return None


def output_candidates(keep, best):
    """出力する候補 (並べ方の順のまま) と、まとめた問い合わせの形 {形: まとめた数}。
    同じ問い合わせの形の索引らしいページ (kana.html?kana=あ など。index_shape) は 3 つまでにまとめる。収集日の資料らしい中を見た候補
    (calendar・districts で点数 35 以上。LLM が「違う」と答えたものも) と、いちばんの候補の資料の一覧にあるものは、
    60 の上限で落とさない (120 まで)"""
    best_docs = {d.get("url") for d in (best or {}).get("documents") or []}
    shapes, merged, rest = Counter(), Counter(), []
    for c in keep:
        shape = index_shape(c["url"])
        if shape is not None and c is not best and (c.get("judge") or {}).get("is_collection_schedule") is not True:
            shapes[shape] += 1
            if shapes[shape] > OUT_SHAPE_MAX:
                merged[shape] += 1
                continue
        rest.append(c)
    must = [c for c in rest if c is best or c["url"] in best_docs
            or (c.get("fetched") and set(c.get("kinds") or []) & set(classify.SCHEDULE_KINDS) and c["score"] >= classify.SCHEDULE_MIN)]
    must = must[:OUT_SCHEDULE_MAX]
    chosen = {id(c) for c in must}
    chosen |= {id(c) for c in [c for c in rest if id(c) not in chosen][:max(0, OUT_CANDIDATES - len(must))]}
    info = {f"{nl}{path}?{'&'.join(names)}": n for (nl, path, names, _), n in sorted(merged.items())}
    return [c for c in rest if id(c) in chosen], info


def discover_one(m, f, opts, judge=None, judge_top=5):
    """1 自治体を探す → 結果 (dict)。止める指示 (Halted) が来たら None"""
    t0 = time.time()
    sites, records, anchors, apps = [], {}, {}, {}
    with f.counting() as cnt:
        for start in m.get("official_sites") or []:
            if any(s.strong_at is not None for s in sites):
                break                                         # 前のサイトで見つかったら、ほかの公式サイトは見ない
            u = fetcher.norm_url(start)
            if not u or any(s.in_scope(fetcher.netloc_of(u)) for s in sites):
                continue
            prev = list(sites)
            s = crawler.SiteCrawl(u, f, opts, skip_scope=lambda nl: any(x.in_scope(nl) for x in prev)).run()
            sites.append(s)
            if s.status == "halted":
                return None
            for url, rec in s.records.items():
                old = records.get(url)
                if old is None or (rec.get("fetched"), rec["score"]) > (old.get("fetched"), old["score"]):
                    records[url] = rec
                    anchors[url] = s.anchors.get(url, []) or s.anchors.get(rec.get("requested_url") or "", [])
            apps.update(s.apps)
        for rec in records.values():                          # 組合などのサイトの、ほかの構成市町村のページは下げる
            if rec.get("related_publisher"):
                others = classify.other_munis(" ".join([rec.get("title") or ""] + list(rec.get("headings") or [])[:3]), m["name"])
                if others:
                    rec["features"]["other_muni"] = others[:5]
                    classify.update(rec, anchors.get(rec["url"], []))
        cands = sorted(records.values(), key=lambda c: (-c["score"], c["url"]))
        judged = errors = 0
        if judge is not None:
            todo = pick_for_judge(cands, judge_top, anchors)      # 渡すものがあるものだけ
            for c in todo:
                c["judge"] = judge.judge(f"{m['pref']}{m['name']}", c)
                judged += 1
                errors += "error" in c["judge"]
        fmt, best, formats = classify.decide(cands + list(apps.values()), [s.status for s in sites])
    cands = sorted(cands, key=classify.rank_key)
    keep = [c for c in cands if c["score"] >= 10 or c.get("kinds") or c["features"].get("linked_from_schedule")]
    sched = [c for c in cands if classify.is_schedule(c)]
    out, merged = output_candidates(keep, best)
    ok = [s for s in sites if s.status == "ok"]
    main = (ok or sites)[0].summary() if sites else {}               # つながったサイト (無ければ始めのサイト)
    misses = cnt["offline_miss"]
    related = [r for s in sites for r in s.related.values()]
    return {
        "lg_code": m["lg_code"], "pref": m["pref"], "county": m.get("county", ""), "name": m["name"], "kind": m.get("kind"),
        "official_sites": m.get("official_sites") or [],
        "start_url": main.get("start_used") if ok else None,
        "format_class": fmt, "formats_seen": formats,
        "best": _clean(best, anchors.get(best["url"])) if best else None,
        "candidates": [_clean(c, anchors.get(c["url"])) for c in out],
        "candidates_total": len(keep), "schedule_candidates": len(sched),
        **({"candidates_merged": merged} if merged else {}),
        "apps": [_clean(a) for a in sorted(apps.values(), key=lambda a: a["url"])],
        "related": related,
        "sites": [s.summary() for s in sites],
        "site_status": main.get("status", "no_site"),
        "requests": cnt["requests"],
        "llm": {"used": judge is not None, "judged": judged, "errors": errors},
        "params": params_of(opts),
        "started_at": fetcher.iso(t0), "finished_at": fetcher.iso(), "seconds": round(time.time() - t0, 1),
        "complete": not misses,                       # --offline でキャッシュに無いものがあれば済んでいない
        **({"offline_misses": misses} if misses else {}),
    }


def write_json(path, data):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(scrub_all(data), ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def done_already(path, want_llm, params=None, retry_unreachable=True):
    """--resume で飛ばしてよいか: complete で、同じ探し方の値と VERSION で、(既定では) unreachable でなく、
    LLM を使う実行なら LLM の判定が済んでいる"""
    d = read_json(path)
    if not d or not d.get("complete"):
        return False
    if params is not None and d.get("params") != params:
        return False                                   # 探し方の値・VERSION が変わった
    if retry_unreachable and d.get("format_class") == "unreachable":
        return False                                   # つながらなかった (こちらのネットの落ちも含む) はやり直す
    llm = d.get("llm") or {}
    return not want_llm or bool(llm.get("used") and not llm.get("errors"))


def should_write(res, path, f, replay=False):
    """書くか (書かないときはわけ)。--offline でキャッシュに足りないもの・ネットが落ちていた間のつながらない結果は書かない。
    --replay ではサイトに入れたものは書く (キャッシュに無いものがあっても。complete は false のまま)。
    ただし --replay で、replay の印の無い結果 (本番の結果) は上書きしない"""
    if f.halt_reason == "network_down" and res["format_class"] == "unreachable":
        return False, "こちらのネットが落ちていたようなので書かない"
    if replay:
        old = read_json(path)
        if old is not None and not old.get("replay"):
            return False, "本番の結果 (replay の印が無い) は --replay で上書きしない"
    if res.get("complete"):
        return True, None
    if replay and any(s.get("status") == "ok" or (s.get("top") or {}).get("result") == "ok" for s in res.get("sites") or []):
        return True, None                                  # トップに入れたもの (途中でブロックされたものも)
    old = read_json(path)
    if old and old.get("complete"):
        return False, "キャッシュに無いものがあるので、前の結果を残す"
    if not any(s.get("status") == "ok" for s in res.get("sites") or []):
        return False, "キャッシュに無いのでサイトに入れない (書かない)"
    return True, None


def summary_row(d):
    b = d.get("best") or {}
    j = b.get("judge") or {}
    sites = d.get("sites") or [{}]
    site = next((s for s in sites if s.get("status") == "ok"), sites[0])      # つながったサイト (無ければ始めのサイト)
    return {"lg_code": d["lg_code"], "pref": d["pref"], "name": d["name"], "format_class": d["format_class"],
            "formats_seen": " ".join(d.get("formats_seen") or []), "best_url": b.get("url", ""), "best_score": b.get("score", ""),
            "best_kinds": " ".join(b.get("kinds") or []),
            "best_judge": "" if not j or "error" in j else {True: "yes", False: "no"}.get(j.get("is_collection_schedule"), "?"),
            "best_fiscal_year": j.get("fiscal_year") or "", "schedule_candidates": d.get("schedule_candidates", 0),
            "candidates": d.get("candidates_total", 0), "apps": " ".join(sorted({a.get("vendor", "") for a in d.get("apps") or []})),
            "related": " ".join(r.get("host", "") for r in d.get("related") or []),
            "site_status": d.get("site_status", ""), "start_url": d.get("start_url") or "", "stop_reason": site.get("stop_reason") or "",
            "pages": sum(s.get("fetched", 0) for s in d.get("sites") or []),
            "hub_pages": sum(s.get("hub_fetched", 0) for s in d.get("sites") or []),
            "related_pages": sum(s.get("offsite", 0) for s in d.get("sites") or []), "requests": d.get("requests", 0),
            "blocked_url": (site.get("blocked") or {}).get("url", ""), "blocked_where": (site.get("blocked") or {}).get("where", ""),
            "llm": "yes" if (d.get("llm") or {}).get("used") else "no",
            "finished_at": d.get("finished_at", "")}


def write_summary(out):
    rows = []
    for p in sorted(out.glob("*.json")):
        if not re.fullmatch(r"\d{6}\.json", p.name):
            continue
        try:
            rows.append(summary_row(json.loads(p.read_text("utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    tmp = out / "summary.csv.part"
    with open(tmp, "w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, SUMMARY_COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out / "summary.csv")
    return rows


def append_run_log(out, run):
    p = out / "run_log.json"
    try:
        log = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        log = {"runs": []}
    log["runs"].append(run)
    write_json(p, log)


def main(argv=None, ask=None):
    """ask: テスト用に LLM の呼び出しを差し替える (llm.ask_json と同じ形)"""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    args = parse_args(argv)
    if args.replay:
        args.offline = True                               # 探し直すだけ (ネットに出ない)
    out = pathlib.Path(args.out or (ROOT / "out" / "discover" / ("replay" if args.replay else "")))
    if args.replay and out.resolve() == (ROOT / "out" / "discover").resolve():
        say("止めた: --replay の結果は out/discover/ (本番の結果) には書かない。--out で別の所を選ぶ")
        return 2
    data = pathlib.Path(args.data)
    out.mkdir(parents=True, exist_ok=True)
    munis = json.loads(pathlib.Path(args.registry).read_text("utf-8"))["municipalities"]
    rows = select(munis, args)
    judge = None
    if not args.no_llm:
        import judge as judge_mod
        try:
            judge = judge_mod.Judge(data / "judge", ask=ask, max_parallel=args.llm_parallel)
        except RuntimeError as e:
            say(f"止めた: {e}")
            return 2
    opts = crawler.Options(depth=args.depth, budget=args.budget, max_docs=args.max_docs, after_strong=args.after_strong,
                           site_time=args.site_minutes * 60, site_search=not args.no_site_search, hub_budget=args.hub_budget,
                           related_budget=args.related_budget)
    params = params_of(opts)
    todo = [m for m in rows if not (args.resume and done_already(out / f"{m['lg_code']}.json", judge is not None, params,
                                                                  retry_unreachable=not args.no_retry_unreachable))]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    f = fetcher.Fetcher(data / "cache", gap=args.gap, max_concurrency=args.max_concurrency, timeout=args.timeout,
                        retries=args.retries, backoff=args.backoff, max_age=args.max_age_hours * 3600,
                        max_requests=args.max_requests, log_path=data / "logs" / f"requests_{stamp}.jsonl", offline=args.offline,
                        ip_gap=args.ip_gap, max_crawl_delay=args.max_crawl_delay)
    say(f"自治体 {len(rows)} (済み {len(rows) - len(todo)}、これから {len(todo)})。LLM: {'使う' if judge else '使わない'}")
    t0, done, classes, halted, not_written = time.time(), 0, Counter(), False, 0
    ex = cf.ThreadPoolExecutor(max_workers=max(1, args.workers))
    futs = {ex.submit(discover_one, m, f, opts, judge, args.judge_top): m for m in todo}
    pending = set(futs)
    try:
        while pending:
            fin, pending = cf.wait(pending, timeout=1.0, return_when=cf.FIRST_COMPLETED)
            for fu in fin:
                m = futs[fu]
                try:
                    res = fu.result()
                except Exception as e:                        # 1 自治体の失敗で全体を止めない
                    say(f"  失敗 {m['lg_code']} {m['name']}: {type(e).__name__}: {e}")
                    continue
                if res is None:
                    halted = True
                    continue
                path = out / f"{m['lg_code']}.json"
                if args.replay:
                    res["replay"] = True                      # 探し直しの結果の印 (本番の結果と取り違えない)
                ok, why = should_write(res, path, f, replay=args.replay)
                if not ok:
                    not_written += 1
                    say(f"  書かない {m['lg_code']} {m['name']}: {why}")
                    continue
                write_json(path, res)
                done += 1
                classes[res["format_class"]] += 1
                b = res.get("best") or {}
                say(f"[{done}/{len(todo)}] {m['lg_code']} {m['pref']}{m['name']}: {res['format_class']}"
                    f"{' ' + b['url'] + ' (' + str(b['score']) + ')' if b else ''} 取得 {res['requests']} 回 {res['seconds']} 秒")
    except KeyboardInterrupt:
        say("止めます (途中の自治体は書きません。--resume で続きから)")
        f.halt()
        halted = True
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        halted = halted or f.halted
        f.close()
        summary = write_summary(out)
        run = {"started_at": fetcher.iso(t0), "finished_at": fetcher.iso(), "seconds": round(time.time() - t0, 1),
               "version": VERSION, "user_agent": fetcher.UA,
               "args": {k: v for k, v in vars(args).items() if k not in ("registry", "out", "data")},
               "selected": len(rows), "skipped_by_resume": len(rows) - len(todo), "done": done, "not_written": not_written,
               "halted": halted, "halt_reason": f.halt_reason if halted else None,
               "requests": f.requests, "hosts_contacted": len(f.hosts), "by_class": dict(sorted(classes.items())),
               "llm": judge.stats() if judge else None, "hosts": f.host_stats()}
        append_run_log(out, run)
    why = {"network_down": " (こちらのネットが落ちたようなので止めた。--resume で続きから)",
           "max_requests": " (--max-requests に届いた)"}.get(f.halt_reason, " (途中で止めた)") if halted else ""
    say(f"できた {done} 自治体、取得 {f.requests} 回、{round(time.time() - t0)} 秒。形式: {dict(sorted(classes.items()))}"
        f"{why}。summary.csv は {len(summary)} 行")
    return 1 if halted else 0


if __name__ == "__main__":
    sys.exit(main())
