#!/usr/bin/env python3
"""discover.py の結果を、japan-gomi-data (tecolicom) の triage.csv と比べる

  python nationwide/discover/eval_jgd.py                       # out/discover/ の結果と比べる
      → out/discover/eval_jgd.csv (1 行 1 自治体)・out/discover/eval_jgd.md (まとめ)
  python nationwide/discover/eval_jgd.py --results out/discover/replay --offline   # 探し直し (--replay) の結果と比べる
  python nationwide/discover/eval_jgd.py --codes-from-triage   # triage.csv の自治体の lg_code をカンマ区切りで出す
  python nationwide/discover/discover.py --codes <上の出力> --no-llm   # その自治体だけを探す (取得はこちらで走らせる人が)

triage.csv は https://github.com/tecolicom/japan-gomi-data の docs/triage/triage.csv (CC BY 4.0、作者 tecolicom)。
fetcher.Fetcher (robots.txt・User-agent・条件つき GET) で 1 回だけ取り、data/discover/eval/ に置く
(7 日のうちは取りに行かない。--offline ならキャッシュだけ。--triage で手元のファイルも使える)。

比べ方:
  - 先方の 5 桁の code → こちらの 6 桁の lg_code (検査数字を足す。JIS X 0402)
  - URL は形をそろえて比べる (http と https・先頭の www.・末尾の / と index.html・# 以降・% の書き方・パスの空白
    (「28 栃谷.pdf」と「28栃谷.pdf」)・+ と %20 を問わない。問い合わせは名前の順にそろえる)
  - 同じページの別の URL も当たりとする: こちらの候補の canonical (<link rel="canonical">・og:url)、
    同じホストで番号のファイル名が同じページ (/page/page003543.html と /a/b/page003543.html)、
    業者のアプリは同じホストでパスの始めの 2 つが同じもの (admin.gomisuke.jp/app/0017/…)
  - 先方の URL が古いもの (stale: 404 や、今のページからリンクが無いと確かめたもの。nationwide/discover/eval_stale.csv と、
    こちらのキャッシュで 404・410 だったもの) は、見つかったかの割合から外す (unverified は外さず、一覧にだけ出す)
  - found_schedule_url: 先方の schedule_url がこちらの候補 (candidates の url・requested_url・canonical、best、apps) にあるか。
    yes / linked (収集日のページの documents の一覧にだけある) / no / stale (古い URL) / - (先方に URL が無い)
  - found_calendar: 先方の calendar_urls のどれかが候補にあるか (同じ yes / linked / no / stale / -)。calendar_hits は数
  - top1: こちらの best が、先方の schedule_url か calendar_urls のどれか (yes / no / -)
  - top1_near: top1 か、こちらの best が先方のページからリンクされた資料か、先方の資料と同じフォルダのもの
    (先方は案内のページ、こちらはその中の地区ごとの PDF、のように細かさだけが違う)
  - format_match: こちらの format_class が先方の source_type と合うか (yes / no / - (比べられない形式))
"""
import argparse, csv, json, os, pathlib, re, sys, unicodedata, urllib.parse
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import fetcher                                         # noqa: E402

TRIAGE_URL = "https://raw.githubusercontent.com/tecolicom/japan-gomi-data/main/docs/triage/triage.csv"
SOURCE_REPO = "https://github.com/tecolicom/japan-gomi-data"
ATTRIBUTION = (f"比べた相手: [japan-gomi-data]({SOURCE_REPO}) (tecolicom) の `docs/triage/triage.csv`。"
               "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) で公開されているものを、比べるためだけに使っている"
               " (この表の URL・形式は先方のデータから)。")
MAX_AGE_DAYS = 7
STALE_FILE = HERE / "eval_stale.csv"
# 先方の source_type → 合うとみなすこちらの format_class (json・txt などは比べない)
FORMAT_MAP = {
    "pdf": {"pdf_text", "pdf_image", "pdf_unchecked"},
    "pdf-text": {"pdf_text"},
    "image-pdf": {"pdf_image"},
    "html": {"html_table", "html_text", "html_unchecked"},
    "csv": {"xlsx/csv"}, "xls": {"xlsx/csv"}, "xlsx": {"xlsx/csv"},
    "web-app": {"app_only"},
    "none": {"not_found"},
}
CSV_COLS = ["lg_code", "code5", "name", "their_pref", "their_status", "their_source_type", "our_format_class", "format_match",
            "found_schedule_url", "found_calendar", "calendar_hits", "calendar_total", "top1", "top1_near", "stale_urls",
            "our_best_url", "their_schedule_url"]
ID_PAGE = re.compile(r"/((?:page)?\d{4,}\.html?)$")    # 番号のファイル名のページ (CMS の別名のパスでも同じ番号)
APP_HOSTS = re.compile(r"(^|\.)(gomisuke\.jp|5374\.jp|delight-system\.(com|co\.jp)|gomisaku\.jp)$")
SKIP = ("-", "stale")                                  # 割合の分母に入れない値


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def check_digit(code5):
    """5 桁の団体コード → 検査数字つきの 6 桁 (JIS X 0402)"""
    s = sum(int(c) * w for c, w in zip(code5, (6, 5, 4, 3, 2)))
    return code5 + str((11 - s % 11) % 10)


def to_lg_code(code):
    """先方の code (5 桁、または 6 桁) → 6 桁の lg_code。読めなければ None"""
    c = re.sub(r"\D", "", str(code or ""))
    if len(c) == 5:
        return check_digit(c)
    if len(c) == 6 and check_digit(c[:5]) == c:
        return c
    return None


def norm_url(u):
    """比べるための URL の形 (スキーム・先頭の www.・末尾の /・index.html・#・% の書き方・パスの空白・+ を問わない)"""
    u = unicodedata.normalize("NFKC", (u or "").strip())
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    try:
        sp = urllib.parse.urlsplit(u)
        host = (sp.hostname or "").lower().rstrip(".")
        port = sp.port
    except ValueError:
        return ""
    if not host:
        return ""
    host = host[4:] if host.startswith("www.") else host
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    path = unicodedata.normalize("NFKC", urllib.parse.unquote(sp.path or "/"))
    path = re.sub(r"\s+", "", path)                          # 「28 栃谷.pdf」と「28栃谷.pdf」(書き写しで空白が落ちる)
    path = re.sub(r"/(?:index|default)\.(?:html?|php|aspx?|jsp)$", "/", path, flags=re.I)
    path = path.rstrip("/") or "/"
    q = "&".join(sorted(re.sub(r"\s+", "", p) for p in urllib.parse.unquote_plus(sp.query).split("&") if p))
    return netloc + path + ("?" + q if q else "")


def alias_keys(n):
    """形をそろえた URL (norm_url) の別名の鍵: 番号のファイル名のページ (同じホスト) と、業者のアプリ (ホストとパスの始めの 2 つ)"""
    host, _, rest = n.partition("/")
    path = "/" + rest.split("?")[0]
    out = []
    m = ID_PAGE.search(path)
    if m:
        out.append(f"id:{host}/{m.group(1).lower()}")
    if APP_HOSTS.search(host.split(":")[0]):
        segs = [s for s in path.split("/") if s][:2]
        if len(segs) == 2:
            out.append(f"app:{host}/{'/'.join(segs)}")
    return out


def split_urls(s):
    """calendar_urls の区切り (；・;・|・空白・改行) で分ける"""
    return [x for x in re.split(r"[；;|\s]+", s or "") if x.startswith(("http://", "https://"))]


def read_triage(path):
    """triage.csv → 行のリスト (lg_code・calendar_urls のリストを足す)。code の読めない行は飛ばす"""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            lg = to_lg_code(r.get("code"))
            if not lg:
                continue
            r = {k: (v or "").strip() for k, v in r.items() if k}
            r["lg_code"] = lg
            r["calendar_list"] = split_urls(r.get("calendar_urls"))
            rows.append(r)
    return rows


def read_stale(path=STALE_FILE):
    """古い (stale) と確かめた先方の URL と、確かめていない (unverified) URL → {形をそろえた URL: (status, reason)}"""
    out = {}
    try:
        with open(path, encoding="utf-8", newline="") as fp:
            for r in csv.DictReader(fp):
                n = norm_url(r.get("url"))
                if n:
                    out[n] = ((r.get("status") or "").strip(), (r.get("reason") or "").strip())
    except OSError:
        pass
    return out


def cache_stale(urls, data_dir):
    """こちらのキャッシュ (data/discover/cache/) で 404・410 だった URL (取りに行かない) → {形をそろえた URL: (stale, わけ)}"""
    cache = fetcher.Cache(pathlib.Path(data_dir) / "cache")
    out = {}
    for u in urls:
        nu = fetcher.norm_url(u)
        meta = cache.meta(nu) if nu else None
        if meta and meta.get("status") in (404, 410):
            out[norm_url(u)] = ("stale", f"こちらのキャッシュで {meta['status']}")
    return out


def fetch_triage(data_dir, offline=False, refresh=False):
    """triage.csv を取る (fetcher.Fetcher で。キャッシュは data_dir/eval/)。→ (中身の bytes, 記録)"""
    f = fetcher.Fetcher(pathlib.Path(data_dir) / "eval", gap=1.0, max_concurrency=1, retries=2, offline=offline,
                        max_age=0 if refresh else MAX_AGE_DAYS * 86400, ip_gap=0.0)
    try:
        r = f.get(TRIAGE_URL)
    finally:
        f.close()
    if not r.ok:
        raise SystemExit(f"triage.csv を取れない ({r.kind} {r.status or ''} {r.error or ''})。--triage で手元のファイルを渡せる")
    return r.body(), {"url": TRIAGE_URL, "fetched_at": r.fetched_at, "from_cache": r.from_cache, "sha256": r.sha256}


def load_triage(args):
    """--triage のファイルか、取った triage.csv → (行, 記録)"""
    if args.triage:
        p = pathlib.Path(args.triage)
        return read_triage(p), {"url": p.name, "fetched_at": None, "from_cache": "local"}
    body, info = fetch_triage(args.data, offline=args.offline, refresh=args.refresh)
    tmp = pathlib.Path(args.data) / "eval" / "triage.csv"            # 読むための写し (git に入れない場所)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(body)
    return read_triage(tmp), info


def _keys(u):
    n = norm_url(u)
    return [n] + alias_keys(n) if n else []


def our_urls(d):
    """こちらの結果 (JSON) の候補の URL の鍵 (形をそろえたもの・canonical・別名) と、収集日のページの documents にだけある URL"""
    cands = set()
    for c in (d.get("candidates") or []) + ([d["best"]] if d.get("best") else []) + (d.get("apps") or []):
        for k in ("url", "requested_url", "canonical"):
            if c.get(k):
                cands.update(_keys(c[k]))
    linked = set()
    for c in d.get("candidates") or []:
        for doc in c.get("documents") or []:
            for n in _keys(doc.get("url")):
                if n not in cands:
                    linked.add(n)
    cands.discard("")
    return cands, linked


def found(urls, cands, linked, stale=None):
    """先方の URL (のどれか) がこちらにあるか → (yes / linked / no / stale / -, 当たった数)。古い URL は数えない"""
    stale = stale or {}
    ns = [norm_url(u) for u in urls if norm_url(u)]
    if not ns:
        return "-", 0
    live = [n for n in ns if stale.get(n, ("",))[0] != "stale"]
    if not live:
        return "stale", 0
    hit = sum(1 for n in live if any(k in cands for k in [n] + alias_keys(n)))
    if hit:
        return "yes", hit
    nl = sum(1 for n in live if any(k in linked for k in [n] + alias_keys(n)))
    return ("linked", nl) if nl else ("no", 0)


def _folder(n):
    return n.split("?")[0].rsplit("/", 1)[0]


def compare(t, d, stale=None):
    """先方の 1 行と、こちらの結果 (JSON) → 表の 1 行"""
    stale = stale or {}
    cands, linked = our_urls(d)
    sched, _ = found([t.get("schedule_url")], cands, linked, stale)
    cal, hits = found(t["calendar_list"], cands, linked, stale)
    b = d.get("best") or {}
    best = b.get("url") or ""
    theirs = {norm_url(u) for u in [t.get("schedule_url")] + t["calendar_list"] if norm_url(u)}
    their_keys = theirs | {k for n in theirs for k in alias_keys(n)}
    mine = set()
    for k in ("url", "requested_url", "canonical"):
        if b.get(k):
            mine.update(_keys(b[k]))
    top1 = "-" if not theirs else ("yes" if mine & their_keys else "no")
    near = top1
    if top1 == "no" and best:
        nb = norm_url(best)
        their_pages = [c for c in d.get("candidates") or [] if set(_keys(c.get("url"))) & their_keys]
        their_doc_folders = {_folder(n) for n in theirs if "/" in _folder(n)
                             and pathlib.PurePosixPath(n.split("?")[0]).suffix.lower() in (".pdf", ".xlsx", ".xls", ".csv")}
        if norm_url(b.get("found_from") or "") in theirs or any(
                nb in {norm_url(x.get("url")) for x in c.get("documents") or []} for c in their_pages) or \
                _folder(nb) in their_doc_folders:
            near = "yes"
    st = (t.get("source_type") or "").lower()
    ok = FORMAT_MAP.get(st)
    fmt = d.get("format_class") or ""
    n_stale = sum(1 for n in theirs if stale.get(n, ("",))[0] == "stale")
    return {"lg_code": t["lg_code"], "code5": t["lg_code"][:5], "name": t.get("name_ja") or d.get("name", ""),
            "their_pref": t.get("pref", ""), "their_status": t.get("status", ""), "their_source_type": st,
            "our_format_class": fmt, "format_match": "-" if ok is None else ("yes" if fmt in ok else "no"),
            "found_schedule_url": sched, "found_calendar": cal, "calendar_hits": hits, "calendar_total": len(t["calendar_list"]),
            "top1": top1, "top1_near": near, "stale_urls": n_stale, "our_best_url": best,
            "their_schedule_url": t.get("schedule_url", "")}


def rate(rows, col, ok=("yes",)):
    """col が ok の数 / 比べられる数 ("-" と "stale" を除く)"""
    xs = [r[col] for r in rows if r[col] not in SKIP]
    n = sum(1 for x in xs if x in ok)
    return n, len(xs)


def pct(n, m):
    return f"{n} / {m} ({100 * n / m:.0f}%)" if m else "0 / 0"


def summary_md(rows, triage, info, results_dir, missing, stale=None):
    stale = stale or {}
    lines = ["# japan-gomi-data との比べ (eval_jgd)", "", ATTRIBUTION, "",
             f"- triage.csv: {len(triage)} 自治体 (取った日時 {info.get('fetched_at') or '-'}、キャッシュ {info.get('from_cache') or '-'})",
             f"- こちらの結果: `{results_dir}` にある {len(rows)} 自治体と比べた "
             f"(triage.csv の自治体のうち、まだ探していないもの {len(missing)})", ""]
    if not rows:
        lines += ["こちらの結果のある自治体がまだ無い。次のようにして、triage.csv の自治体だけを探してから比べ直す:", "",
                  "```", "python nationwide/discover/eval_jgd.py --codes-from-triage > codes.txt",
                  "python nationwide/discover/discover.py --codes $(cat codes.txt) --no-llm",
                  "python nationwide/discover/eval_jgd.py", "```", ""]
        return "\n".join(lines)
    lines += ["| 見るもの | 数 |", "|---|---|",
              f"| schedule_url が候補にある | {pct(*rate(rows, 'found_schedule_url'))} |",
              f"| schedule_url が候補か、収集日のページの資料の一覧にある | {pct(*rate(rows, 'found_schedule_url', ('yes', 'linked')))} |",
              f"| calendar_urls のどれかが候補にある | {pct(*rate(rows, 'found_calendar'))} |",
              f"| calendar_urls のどれかが候補か資料の一覧にある | {pct(*rate(rows, 'found_calendar', ('yes', 'linked')))} |",
              f"| top1 (こちらの best が先方の URL のどれか) | {pct(*rate(rows, 'top1'))} |",
              f"| top1_near (top1 か、best が先方のページの資料・同じフォルダの資料) | {pct(*rate(rows, 'top1_near'))} |",
              f"| 形式が合う (format_class と source_type) | {pct(*rate(rows, 'format_match'))} |", "",
              "URL は canonical・番号のファイル名の別名・業者のアプリのパスの始めでも当たりとする。"
              "先方の URL のうち古いと確かめたもの (stale) は割合から外す。", ""]
    cross = Counter((r["their_source_type"] or "-", r["our_format_class"] or "-") for r in rows)
    ours = sorted({o for _, o in cross})
    lines += ["## 形式の組み合わせ (行: 先方の source_type、列: こちらの format_class)", "",
              "| source_type | " + " | ".join(ours) + " |", "|---|" + "---|" * len(ours)]
    for st in sorted({s for s, _ in cross}):
        lines.append(f"| {st} | " + " | ".join(str(cross.get((st, o), "")) for o in ours) + " |")
    miss = [r for r in rows if r["found_schedule_url"] in ("no", "stale") and r["found_calendar"] in ("no", "-", "stale")
            and not (r["found_schedule_url"] == "stale" and r["found_calendar"] in ("-", "stale"))]
    lines += ["", f"## 先方の URL がどれもこちらの候補に無い自治体 ({len(miss)})", ""]
    if miss:
        lines += ["| lg_code | 名前 | 先方の形式 | こちらの形式 | 先方の schedule_url |", "|---|---|---|---|---|"]
        lines += [f"| {r['lg_code']} | {r['name']} | {r['their_source_type']} | {r['our_format_class']} | {r['their_schedule_url']} |"
                  for r in miss[:40]]
        if len(miss) > 40:
            lines.append(f"| … | ほか {len(miss) - 40} |  |  |  |")
    codes = {r["lg_code"] for r in rows}
    old = [(t["lg_code"], t.get("name_ja", ""), u, stale[norm_url(u)]) for t in triage if t["lg_code"] in codes
           for u in [t.get("schedule_url")] + t["calendar_list"] if norm_url(u) in stale]
    if old:
        lines += ["", f"## 先方の URL のうち古いもの・確かめていないもの ({len(old)})", "",
                  "stale は割合から外し、unverified は外さない (nationwide/discover/eval_stale.csv と、こちらのキャッシュの 404・410)。", "",
                  "| lg_code | 名前 | 状態 | わけ | URL |", "|---|---|---|---|---|"]
        lines += [f"| {lg} | {nm} | {st} | {why} | {u} |" for lg, nm, u, (st, why) in old]
    return "\n".join(lines) + "\n"


def write_text(path, text):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, "utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(description="discover.py の結果を japan-gomi-data の triage.csv と比べる")
    ap.add_argument("--codes-from-triage", action="store_true",
                    help="triage.csv の自治体の lg_code をカンマ区切りで出すだけ (discover.py --codes に渡す)")
    ap.add_argument("--triage", help="手元の triage.csv を使う (取りに行かない)")
    ap.add_argument("--results", default=str(ROOT / "out" / "discover"), help="こちらの結果 (<lg_code>.json) のある所")
    ap.add_argument("--out", help="eval_jgd.csv・eval_jgd.md を書く所 (既定 --results と同じ)")
    ap.add_argument("--data", default=str(ROOT / "data" / "discover"),
                    help="triage.csv のキャッシュ (eval/) と、古い URL を見るための取得のキャッシュ (cache/) のある所")
    ap.add_argument("--stale", default=str(STALE_FILE), help="古いと確かめた先方の URL の一覧 (CSV)")
    ap.add_argument("--offline", action="store_true", help="triage.csv はキャッシュだけを使う")
    ap.add_argument("--refresh", action="store_true", help="triage.csv を取り直す (条件つき GET)")
    args = ap.parse_args(argv)
    triage, info = load_triage(args)
    if args.codes_from_triage:
        print(",".join(sorted({t["lg_code"] for t in triage})))
        say(f"{len(triage)} 自治体 (triage.csv)")
        return 0
    results = pathlib.Path(args.results)
    out = pathlib.Path(args.out or results)
    out.mkdir(parents=True, exist_ok=True)
    stale = cache_stale([u for t in triage for u in [t.get("schedule_url")] + t["calendar_list"] if u], args.data)
    stale.update(read_stale(args.stale))                 # 一覧に書いたものを先に (unverified もある)
    rows, missing = [], []
    for t in sorted(triage, key=lambda t: t["lg_code"]):
        p = results / f"{t['lg_code']}.json"
        try:
            d = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            missing.append(t["lg_code"])
            continue
        rows.append(compare(t, d, stale))
    tmp = out / "eval_jgd.csv.part"
    with open(tmp, "w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out / "eval_jgd.csv")
    try:
        shown = results.resolve().relative_to(ROOT).as_posix()       # 手元の場所は書かない
    except ValueError:
        shown = results.name
    write_text(out / "eval_jgd.md", summary_md(rows, triage, info, shown, missing, stale))
    say(f"triage.csv {len(triage)} 自治体、こちらの結果と比べた {len(rows)}、まだ探していない {len(missing)}。"
        f"schedule_url が候補にある {pct(*rate(rows, 'found_schedule_url'))}、top1 {pct(*rate(rows, 'top1'))}、"
        f"top1_near {pct(*rate(rows, 'top1_near'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
