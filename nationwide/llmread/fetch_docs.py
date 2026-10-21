"""読む資料の一覧と、足りない資料の取得 (資料の読み込み。意味は読まない)

  python nationwide/llmread/fetch_docs.py --codes 112275,131024            # 一覧だけ作る (ネットに出ない)
  python nationwide/llmread/fetch_docs.py --codes 112275 --fetch [--max-requests 40]
      # 一覧にあって、キャッシュに無い資料だけを取る (discover/fetcher.py: robots.txt・1 ホスト 1 秒 1 回・名乗る User-Agent)

入力: out/discover/<lg_code>.json (発見の結果。書き終わったもの (complete で、書いてから 2 分たったもの) だけを読む)、
      data/discover/cache/ (取得のキャッシュ)
出力: data/readers/<lg_code>/docs.json (git に入れない)
      data/readers/boilerplate/<ホスト>.json (使い回しの塊の sha256。htmlbody.py が本文の範囲から外す)
      (data/readers/ の名前は前の読み取りのときのまま。キャッシュの場所を変えないため)

決まり:
  - 取るのは発見の結果に載っている資料 (candidates と、案内のページの documents) だけ。案内のページの子を新しくたどらない
  - 上限: 1 自治体 120 本・1GB。ただし 1 つの案内のページから同じ形のファイル名・リンクの文字で並ぶ地区ごとの資料の組 (5 本以上) は上限の外
  - 取れなかった資料 (robots.txt で禁止・ブロック) は doc_not_fetched として一覧に残す (黙って落とさない)
  - リンクごとに anchor_context (見出しの道すじと、リンクを含む塊の HTML。タグつき) を作る
  - 日ごとに変わるページ (鶴ヶ島 garbage.php?mode=cal) は、年・月をつけた source_key を並べる
"""
import argparse, gzip, hashlib, json, os, pathlib, re, sys, time, urllib.parse
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "discover"))
import decode, htmlbody                                       # noqa: E402
import fetcher                                                # noqa: E402

MAX_DOCS, MAX_BYTES = 120, 1 << 30
SET_MIN = 5                     # 同じ形で並ぶ資料がこの数以上なら「地区ごとの資料の組」
SETTLE_SECONDS = 120            # 発見の結果を書いてからこれだけたっていないものは読まない (書いている途中かもしれない)
BLOCK_HTML_MAX = 8000
BLOCK_TEXT_MAX = 1500
NOT_FETCHABLE = ("robots_disallowed", "blocked")
# 日ごとに変わるページ: パス → 年・月のパラメータの名前
DYNAMIC = {"garbage.php": ("year", "month")}


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def load_discover(lg, results=ROOT / "out" / "discover", settle=SETTLE_SECONDS):
    """発見の結果 → dict。書き終わっていない・読めないものは (None, わけ)"""
    p = pathlib.Path(results) / f"{lg}.json"
    try:
        st = p.stat()
    except OSError:
        return None, "no_result"
    if time.time() - st.st_mtime < settle:
        return None, "being_written"
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None, "unreadable"
    if not d.get("complete"):
        return None, "incomplete"
    return d, None


CACHE_DIR = ROOT / "data" / "discover" / "cache"


def cached(url, cache_dir=None):
    """取得のキャッシュ (data/discover/cache/) にある中身 (bytes)。無ければ None。ネットには出ない (コマンドの試し・テスト用)"""
    cache = fetcher.Cache(cache_dir or CACHE_DIR)
    return body_of(cache, fetcher.norm_url(url) or url)[0]


def body_of(cache, url):
    """キャッシュの中身 (bytes) と記録。無ければ (None, 記録)"""
    meta = cache.meta(url)
    p = cache.body_path(url, meta) if meta else None
    if not p:
        return None, meta
    b = p.read_bytes()
    return (gzip.decompress(b) if str(p).endswith(".gz") else b), meta


def shape(s):
    """組を見つけるための形: 数字・漢数字・丸数字を # にする"""
    s = urllib.parse.unquote(s or "")
    s = re.sub(r"[0-9０-９]+|[一二三四五六七八九十]+|[①-⑳]", "#", s)
    return re.sub(r"\s+", "", s)


def dynamic_keys(url, fy=2026):
    """日ごとに変わるページなら、年・月をつけた source_key のリスト (4 月〜翌 3 月)。違えば []"""
    sp = urllib.parse.urlsplit(url)
    name = sp.path.rsplit("/", 1)[-1]
    if name not in DYNAMIC:
        return []
    q = urllib.parse.parse_qs(sp.query)
    ky, km = DYNAMIC[name]
    if ky in q and km in q:
        return []
    out = []
    for i in range(12):
        y, m = (fy, 4 + i) if i < 9 else (fy + 1, i - 8)
        out.append(f"{url}{'&' if sp.query else '?'}{ky}={y}&{km}={m}")
    return out


def anchor_contexts(html_text, page_url):
    """案内のページ → {リンク先の URL (そろえたもの): {"anchor_text", "heading_path", "block_html", "following_text", "selector"}}"""
    soup = htmlbody.parse(html_text)
    for el in soup.find_all(list(htmlbody.SKIP_TAGS)):
        el.decompose()
    out = {}
    heads = []
    for el in soup.find_all(True):
        if el.name in htmlbody.HEADINGS:
            lv = htmlbody.HEADINGS[el.name]
            while heads and heads[-1][0] >= lv:
                heads.pop()
            t = htmlbody.clean(el.get_text(" "))
            if t:
                heads.append((lv, t))
            continue
        if el.name != "a" or not el.get("href"):
            continue
        u = fetcher.norm_url(urllib.parse.urljoin(page_url, el["href"]))
        if not u or u in out:
            continue
        atext = htmlbody.clean(el.get_text(" "))
        block = el
        for _ in range(4):                    # リンクの文字より文字の多い、いちばん小さな塊 (大きすぎれば 1 つ手前)
            up = block.find_parent(["li", "p", "td", "tr", "dd", "dt", "div", "table", "ul", "ol", "dl", "section"])
            if up is None:
                break
            n = len(htmlbody.squash(up.get_text(" ")))
            if n > BLOCK_TEXT_MAX and block is not el:
                break
            block = up
            if n > len(htmlbody.squash(atext)) + 10:
                break
        nxt = block.find_next_sibling()
        out[u] = {"anchor_text": atext, "heading_path": [h for _, h in heads], "block_html": str(block)[:BLOCK_HTML_MAX],
                  "following_text": htmlbody.clean(nxt.get_text(" "))[:500] if nxt is not None else "",
                  "selector": htmlbody.css_path(el)}
    return out


def collect(d, cache):
    """発見の結果 → 資料の一覧 (URL ごとに 1 つ。候補と、案内のページの documents をまとめる)"""
    docs = {}

    def get(url):
        u = fetcher.norm_url(url) or url
        return docs.setdefault(u, {"url": u, "listed_in": [], "anchors": [], "found_from": [], "not_fetched": None})

    best = fetcher.norm_url((d.get("best") or {}).get("url") or "")
    for c in d.get("candidates") or []:
        x = get(c["url"])
        x["listed_in"].append("candidates")
        x.update(role=c.get("role"), kinds=c.get("kinds"), format_class=c.get("format_class"), file_type=c.get("file_type"),
                 score=c.get("score"), title=c.get("title"), http=c.get("http"), best=(x["url"] == best))
        x["anchors"] += [a for a in c.get("anchors") or [] if a not in x["anchors"]]
        if c.get("found_from") and c["found_from"] not in x["found_from"]:
            x["found_from"].append(c["found_from"])
        x["not_fetched"] = x["not_fetched"] or c.get("not_fetched")
        for doc in c.get("documents") or []:
            y = get(doc["url"])
            if "documents" not in y["listed_in"]:
                y["listed_in"].append("documents")
            if doc.get("text") and doc["text"] not in y["anchors"]:
                y["anchors"].append(doc["text"])
            if c["url"] not in y["found_from"]:
                y["found_from"].append(c["url"])
            y.setdefault("format_class", doc.get("format_class"))
            y["not_fetched"] = y["not_fetched"] or doc.get("not_fetched")
    for x in docs.values():
        x.setdefault("best", False)
        meta = cache.meta(x["url"])
        if meta and meta.get("status") == 200 and meta.get("body"):
            x["cache"] = "cached"
            x["sha256"], x["size"] = meta.get("sha256"), meta.get("size")
            x["last_modified"] = (meta.get("headers") or {}).get("last-modified")
            x["fetched_at"] = meta.get("fetched_at")
            x["content_type"] = (meta.get("headers") or {}).get("content-type")
        elif meta and meta.get("blocked"):
            x["cache"] = "blocked"
        elif meta and meta.get("status") and meta.get("status") != 200:
            x["cache"] = f"http_{meta['status']}"
        elif x["not_fetched"] in NOT_FETCHABLE:
            x["cache"] = x["not_fetched"]
        else:
            x["cache"] = "missing"
        keys = dynamic_keys(x["url"])
        x["source_key"] = x["url"]
        if keys:
            x["dynamic_month"] = True
            x["source_keys"] = keys
    return docs


def mark_sets(docs):
    """1 つの案内のページから同じ形で並ぶ資料の組 (SET_MIN 本以上) に印を付ける → 組の数"""
    groups = defaultdict(list)
    for x in docs.values():
        if "documents" not in x["listed_in"] or not x["found_from"]:
            continue
        name = urllib.parse.urlsplit(x["url"]).path.rsplit("/", 1)[-1]
        groups[(x["found_from"][0], shape(name), shape(x["anchors"][0] if x["anchors"] else ""))].append(x)
    n = 0
    for (hub, _, _), xs in groups.items():
        if len(xs) >= SET_MIN:
            n += 1
            for x in xs:
                x["district_set"] = f"set{n}"
    return n


def apply_limits(docs, max_docs=MAX_DOCS, max_bytes=MAX_BYTES):
    """上限 (組の資料は数えない)。上限を超えるものに over_limit の印 (点数の高い順に残す)"""
    used_n, used_b = 0, 0
    for x in sorted(docs.values(), key=lambda x: (-(x.get("score") or 0), x["url"])):
        if x.get("district_set"):
            continue
        size = x.get("size") or ((x.get("http") or {}).get("size")) or 0
        if used_n + 1 > max_docs or used_b + size > max_bytes:
            x["over_limit"] = True
            continue
        used_n += 1
        used_b += size
    return used_n, used_b


def add_anchor_contexts(docs, cache):
    """案内のページ (キャッシュにある HTML) から、そこにリンクされた資料の anchor_context を作る"""
    pages = {f for x in docs.values() for f in x["found_from"]}
    ctx = {}
    for p in pages:
        u = fetcher.norm_url(p) or p
        body, meta = body_of(cache, u)
        if body is None or b"<" not in body[:2000]:
            continue
        try:
            html = decode.decode(body, (meta.get("headers") or {}).get("content-type", "")).text
        except decode.DecodeError:
            continue
        ctx[u] = anchor_contexts(html, u)
    n = 0
    for x in docs.values():
        for f in x["found_from"]:
            c = ctx.get(fetcher.norm_url(f) or f, {}).get(x["url"])
            if c:
                x["anchor_context"] = {**c, "page": f}
                n += 1
                break
    return n


def host_boilerplate(docs, cache, out_dir):
    """自治体の資料のうち、キャッシュにある HTML をホストごとに数えて、使い回しの塊を data/readers/boilerplate/ に書く。
    ページごとに、body の塊 (pages) と本文の範囲のリンクでない行 (regions。ページどうしの関係の有る無しに使う) の sha256 を残す"""
    by_host, by_host_reg = defaultdict(dict), defaultdict(dict)
    for x in docs.values():
        if x.get("cache") != "cached" or "html" not in (x.get("content_type") or ""):
            continue
        body, meta = body_of(cache, x["url"])
        try:
            html = decode.decode(body, x.get("content_type") or "").text
        except (decode.DecodeError, TypeError):
            continue
        host = urllib.parse.urlsplit(x["url"]).netloc
        by_host[host][x["url"]] = htmlbody.block_hashes(html)
        by_host_reg[host][x["url"]] = htmlbody.region_hashes(html)
    out = {}
    for host, pages in by_host.items():
        p = pathlib.Path(out_dir) / f"{re.sub(r'[^A-Za-z0-9.-]', '_', host)}.json"
        old, old_reg = {}, {}
        try:
            d = json.loads(p.read_text("utf-8"))
            old, old_reg = d.get("pages", {}), d.get("regions", {})
        except (OSError, ValueError):
            pass
        allp = {**{k: set(v) for k, v in old.items()}, **pages}
        # 前の版のファイル (regions が無い) から残ったページは、本文の範囲の塊が無いので、枠を除いた比べ方だけで見る
        allr = {**{k: set(v) for k, v in old_reg.items() if k not in pages}, **by_host_reg[host]}
        hs = htmlbody.boilerplate_hashes(allp, regions=allr)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        tmp.write_text(json.dumps({"host": host, "pages": {k: sorted(v) for k, v in allp.items()},
                                   "regions": {k: sorted(v) for k, v in allr.items()},
                                   "boilerplate": sorted(hs)}, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
        out[host] = {"pages": len(allp), "boilerplate_blocks": len(hs)}
    return out


def load_boilerplate(host, out_dir=ROOT / "data" / "readers" / "boilerplate"):
    """ホストの使い回しの塊の sha256 の集まり (htmlbody.content_region に渡す)"""
    p = pathlib.Path(out_dir) / f"{re.sub(r'[^A-Za-z0-9.-]', '_', host)}.json"
    try:
        return set(json.loads(p.read_text("utf-8")).get("boilerplate", []))
    except (OSError, ValueError):
        return set()


def fetch_missing(docs, f, max_requests):
    """一覧にあってキャッシュに無い資料を取る (上限の外の組も取る。上限を超えたものは取らない) → 取った数"""
    n = 0
    for x in sorted(docs.values(), key=lambda x: (-(x.get("score") or 0), x["url"])):
        if x.get("cache") != "missing" or x.get("over_limit") or x.get("dynamic_month"):
            continue
        if n >= max_requests:
            break
        r = f.get(x["url"])
        n += 1
        if r.ok:
            x.update(cache="cached", sha256=r.sha256, size=r.size, fetched_at=r.fetched_at,
                     last_modified=r.headers.get("last-modified"), content_type=r.headers.get("content-type"))
        elif r.kind in ("robots_disallowed", "blocked"):
            x["cache"] = r.kind
        else:
            x["cache"] = "fetch_failed"
            x["fetch_error"] = fetcher.scrub(f"{r.kind} {r.status or ''} {r.error or ''}".strip())
    return n


def run(lg, cache_dir, out_root, fetch=False, max_requests=40, f=None, results=ROOT / "out" / "discover", settle=SETTLE_SECONDS):
    d, why = load_discover(lg, results, settle)
    if d is None:
        return {"lg_code": lg, "skipped": why}
    cache = fetcher.Cache(cache_dir)
    docs = collect(d, cache)
    sets = mark_sets(docs)
    used_n, used_b = apply_limits(docs)
    fetched = 0
    if fetch:
        own = f is None
        f = f or fetcher.Fetcher(cache_dir, gap=1.0, max_concurrency=1, retries=2)
        try:
            fetched = fetch_missing(docs, f, max_requests)
        finally:
            if own:
                f.close()
    n_ctx = add_anchor_contexts(docs, cache)
    bp = host_boilerplate(docs, cache, pathlib.Path(out_root) / "boilerplate")
    for x in docs.values():
        if x["cache"] in ("robots_disallowed", "blocked", "fetch_failed") or x["cache"].startswith("http_"):
            x["blocked_by"] = ["doc_not_fetched"]
    summary = {"docs": len(docs), "cache": dict(Counter(x["cache"] for x in docs.values())),
               "district_sets": sets, "counted_docs": used_n, "counted_bytes": used_b,
               "over_limit": sum(1 for x in docs.values() if x.get("over_limit")),
               "dynamic_month": sum(1 for x in docs.values() if x.get("dynamic_month")),
               "anchor_contexts": n_ctx, "fetched_now": fetched, "boilerplate": bp}
    out = {"lg_code": lg, "name": d.get("name"), "discover_finished_at": d.get("finished_at"),
           "format_class": d.get("format_class"), "summary": summary,
           "docs": sorted(docs.values(), key=lambda x: (not x["best"], -(x.get("score") or 0), x["url"]))}
    p = pathlib.Path(out_root) / lg / "docs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("docs.json.part")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    return {"lg_code": lg, **summary}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="読む資料の一覧と、足りない資料の取得")
    ap.add_argument("--codes", required=True, help="lg_code をカンマ区切りで")
    ap.add_argument("--fetch", action="store_true", help="一覧にあってキャッシュに無い資料を取る (礼儀を守って)")
    ap.add_argument("--max-requests", type=int, default=40, help="1 自治体で取る数の上限")
    ap.add_argument("--cache", default=str(ROOT / "data" / "discover" / "cache"))
    ap.add_argument("--out", default=str(ROOT / "data" / "readers"))
    a = ap.parse_args(argv)
    for lg in [c.strip() for c in a.codes.split(",") if c.strip()]:
        r = run(lg, a.cache, a.out, a.fetch, a.max_requests)
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
