"""japan-gomi-data (tecolicom、CC BY 4.0) を正解にする: 取る・広げる・読み込む (採点は score.py)

  python nationwide/llmread/jgd.py fetch            # 固定したコミットの tar.gz を 1 回だけ取る (GitHub。礼儀を守って)
  python nationwide/llmread/jgd.py fetch --pages    # 広げ方の確かめに使う、先方が公開している 8 コースの JSON も取る
  python nationwide/llmread/jgd.py convert          # course-*.yaml を広げる → data/readers/eval/jgd/<lg_code>.json
  python nationwide/llmread/jgd.py check-port       # 広げ方が先方の公開の JSON と 1 日も違わないか (8 コース)

比べた相手: japan-gomi-data (https://github.com/tecolicom/japan-gomi-data、作者 tecolicom、CC BY 4.0)。
コミット d786a985569efcf747adce721578a819d79ae891 (2026-10-05) に固定し、比べるためだけに使う。取ったものは data/readers/eval/ (git に入れない。
場所の名前は前の読み取りのときのまま)。先方のコードは動かさない。広げ方は tools/_lib/schedule.mjs (categoriesOn / expandRange) を手で Python に移したもの:
  weekly は days の曜日、monthly_nth は月の何回目 ((日 - 1) // 7 + 1) が occurrences に入る日、monthly_specific は dates の日。
  その日の overrides に cancelled が 1 つでもあれば、その日は全品目なし。cancelled でない overrides は、毎週の品目 + その品目 (月ごとの品目は落ちる)。
  unknown_periods の日と period (月の閉区間) の外は出さない (「不明」で「収集なし」ではない)
"""
import argparse, collections, datetime as dt, io, json, pathlib, re, sys, tarfile, unicodedata

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (HERE, ROOT / "nationwide" / "discover"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

REPO = "https://github.com/tecolicom/japan-gomi-data"
COMMIT = "d786a985569efcf747adce721578a819d79ae891"
TARBALL_URL = f"https://codeload.github.com/tecolicom/japan-gomi-data/tar.gz/{COMMIT}"
PAGES = "https://tecolicom.github.io/japan-gomi-data/ics"
LICENSE = "CC BY 4.0"
ATTRIBUTION = ("正解: japan-gomi-data (https://github.com/tecolicom/japan-gomi-data、作者 tecolicom、CC BY 4.0)。"
               f"コミット {COMMIT[:12]} に固定し、比べるためだけに使っている")
DATA = ROOT / "data" / "readers" / "eval"
TIERS_FILE = HERE / "jgd_tiers.json"
FY_FROM, FY_TO = dt.date(2026, 4, 1), dt.date(2027, 3, 31)
DOW = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
# 広げ方の確かめに使う、先方が公開しているコース (仕組みを 1 つずつ含む: 毎週・第N・日付の並び・休み・品目の足し・不明の期間・期間 2 つ・祝日)
PORT_COURSES = [("hanno", "a1"), ("hatoyama-town", "a"), ("higashichichibu-vill", "1"), ("itabashi", "e1"),
                ("kawasaki", "asao1"), ("mitaka", "095664"), ("nishitokyo", "1"), ("sabae", "1")]


def say(*a):
    print(*a, file=sys.stderr, flush=True)


# ---- 取る ----
def _fetcher(offline=False):
    import fetcher
    return fetcher.Fetcher(DATA / "cache", gap=1.0, max_concurrency=1, retries=2, offline=offline,
                           max_age=365 * 86400, ip_gap=0.0)


def fetch(pages=False, offline=False):
    """固定したコミットの tar.gz (と、--pages なら公開の JSON 8 本と index.csv) を取る → 取った記録"""
    f = _fetcher(offline)
    out = []
    try:
        urls = [TARBALL_URL] + ([f"{PAGES}/index.csv"] + [f"{PAGES}/{h}/{s}.json" for h, s in PORT_COURSES] if pages else [])
        for u in urls:
            r = f.get(u)
            out.append({"url": u, "kind": r.kind, "status": r.status, "sha256": r.sha256, "from_cache": r.from_cache,
                        "error": r.error})
            if not r.ok:
                say(f"取れない: {u} ({r.kind} {r.status or ''} {r.error or ''})")
    finally:
        f.close()
    return out


def _cached_body(url):
    import fetcher, fetch_docs
    c = fetcher.Cache(DATA / "cache")
    body, _ = fetch_docs.body_of(c, fetcher.norm_url(url))
    return body


def load_tar():
    """キャッシュの tar.gz → {リポジトリの中の道すじ: bytes} (YAML・LICENSE だけ。展開してファイルには書かない)"""
    body = _cached_body(TARBALL_URL)
    if body is None:
        raise SystemExit("tar.gz がキャッシュに無い。python nationwide/llmread/jgd.py fetch で取る")
    out = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/", 1)
            if len(parts) != 2 or ".." in parts[1].split("/"):
                continue
            p = parts[1]
            if (p.startswith("municipalities/") and p.endswith(".yaml")) or p in ("LICENSE", "schema/categories.yaml"):
                out[p] = tf.extractfile(m).read()
    return out


# ---- 広げる ----
def _d(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def check_digit(code5):
    s = sum(int(c) * w for c, w in zip(code5, (6, 5, 4, 3, 2)))
    return code5 + str((11 - s % 11) % 10)


def period_bounds(p):
    a, b = p.split("--")
    fy, fm = map(int, a.split("-"))
    ty, tm = map(int, b.split("-"))
    return dt.date(fy, fm, 1), dt.date(ty + (tm == 12), (tm % 12) + 1, 1) - dt.timedelta(days=1)


def categories_on(day, rules, overrides):
    """その日の品目 (先方の categoriesOn) → (品目のリスト, "cancelled" / "override" / None)"""
    dow, occ = day.weekday(), (day.day - 1) // 7 + 1
    weekly, monthly = [], []
    for r in rules:
        days = {DOW[x] for x in (r.get("days") or [])}
        if r["pattern"] == "weekly" and dow in days:
            weekly.append(r["category"])
        elif r["pattern"] == "monthly_nth" and dow in days and occ in (r.get("occurrences") or []):
            monthly.append(r["category"])
        elif r["pattern"] == "monthly_specific" and day in {_d(x) for x in (r.get("dates") or [])}:
            monthly.append(r["category"])
    ovs = [o for o in (overrides or []) if _d(o["date"]) == day]
    if any(o.get("cancelled") for o in ovs):
        return [], "cancelled"
    if not ovs:
        return list(dict.fromkeys(weekly + monthly)), None
    return list(dict.fromkeys(weekly + [o["category"] for o in ovs if o.get("category")])), "override"


def convert(files):
    """tar の中身 → {lg_code: 正解} (地区 = コース → 品目 → 2026 年度の日付。期間 ∩ 年度、不明の日を除く)"""
    import yaml
    load = lambda p: yaml.safe_load(files[p].decode("utf-8"))
    handles = sorted({"/".join(p.split("/")[1:3]) for p in files if p.startswith("municipalities/") and p.count("/") >= 3})
    out = {}
    for h in handles:
        base = f"municipalities/{h}"
        if f"{base}/meta.yaml" not in files:
            continue
        meta = load(f"{base}/meta.yaml")
        tax = load(f"{base}/taxonomy.yaml") if f"{base}/taxonomy.yaml" in files else {}
        code5 = str(meta.get("code") or "").zfill(5)
        if not code5.strip("0"):
            continue
        lg = check_digit(code5)
        courses = {}
        periods = sorted({p.split("/")[3] for p in files if p.startswith(base + "/") and p.count("/") == 4 and "--" in p.split("/")[3]})
        for per in periods:
            ps, pe = period_bounds(per)
            lo, hi = max(ps, FY_FROM), min(pe, FY_TO)
            for p in sorted(x for x in files if x.startswith(f"{base}/{per}/course-") and x.endswith(".yaml")):
                c = load(p)
                m, rules = c["metadata"], c["rules"]
                ovs, ups = c.get("overrides") or [], c.get("unknown_periods") or []
                key = str(m["course"])
                rec = courses.setdefault(key, {"course": key, "course_name_ja": m.get("course_name_ja"), "periods": [],
                                               "areas_by_period": {}, "by_category": collections.defaultdict(list),
                                               "unknown": [], "source": {}})
                rec["periods"].append(per)
                rec["areas_by_period"][per] = [{k: a.get(k) for k in ("name", "yomi", "machiaza_id", "note") if a.get(k)}
                                               for a in m.get("areas") or []]
                rec["source"][per] = {k: str(m["source"].get(k)) for k in ("pdf_url", "source_url", "edition_ja", "extracted_at")
                                      if m.get("source", {}).get(k)}
                rec["unknown"] += [{"from": str(_d(u["from"])), "to": str(_d(u["to"]))} for u in ups]
                day = lo
                while day <= hi:
                    if not any(_d(u["from"]) <= day <= _d(u["to"]) for u in ups):
                        for cat in categories_on(day, rules, ovs)[0]:
                            rec["by_category"][cat].append(str(day))
                    day += dt.timedelta(days=1)
        for rec in courses.values():
            rec["by_category"] = {k: sorted(set(v)) for k, v in sorted(rec["by_category"].items())}
        out[lg] = {"lg_code": lg, "handle": h.split("/")[1], "pref_dir": h.split("/")[0], "name_ja": meta.get("name_ja"),
                   "ground_truth": {"repo": REPO, "commit": COMMIT, "license": LICENSE, "path": f"{base}/"},
                   "window": {"from": str(FY_FROM), "to": str(FY_TO)}, "periods": periods,
                   "taxonomy": {"categories": tax.get("categories"), "overrides": tax.get("overrides"), "groups": tax.get("groups")},
                   "meta_notes": meta.get("notes"), "courses": [courses[k] for k in sorted(courses)]}
    return out


def write_gt(gts):
    d = DATA / "jgd"
    d.mkdir(parents=True, exist_ok=True)
    for lg, g in gts.items():
        (d / f"{lg}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1, default=str), "utf-8")
    return d


def load_gt(lg):
    p = DATA / "jgd" / f"{lg}.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def tiers():
    """固定した使える度合いの表 (jgd_tiers.json) → {lg_code: 行}"""
    t = json.loads(TIERS_FILE.read_text("utf-8"))
    return {r["lg_code"]: r for r in t["municipalities"]}


# ---- 名前の当て (score.py が使う) ----
def nk(s):
    """名前を比べる形 (NFKC、空白なし、ヶ→ケ、漢数字の丁目)"""
    s = unicodedata.normalize("NFKC", str(s or "")).replace(" ", "").replace("　", "")
    s = s.replace("ヶ", "ケ").replace("ｹ", "ケ").replace("之", "の")
    s = re.sub(r"[〜～~∼]", "~", s)                      # 波ダッシュの字の違い (「1～3丁目」「1〜3丁目」)
    for i, k in enumerate("〇一二三四五六七八九"):
        s = re.sub(k + "丁目", f"{i}丁目", s)
    return s


def daterange(a, b):
    d, e = _d(a), _d(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def gt_window(gt, course):
    """コースの比べる日: 期間 ∩ 2026 年度、先方の unknown_periods を除く"""
    days = set()
    for per in course["periods"]:
        lo, hi = period_bounds(per)
        days |= set(daterange(max(lo, _d(gt["window"]["from"])), min(hi, _d(gt["window"]["to"]))))
    for u in course.get("unknown") or []:
        days -= set(daterange(u["from"], u["to"]))
    return days


def area_map(gt, doc):
    """先方の地区 (コースの area) → こちらの地区の id の集まり。名前 (割れ町は判別子つき)・町字 ID・地区の名前・コースの名前で引く。
    doc は {"districts": [{"district_id", "label", "members": [{"text"}]}]} (score.py が LLM の答えから作る)"""
    by_name, by_id, by_label = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    for d in doc["districts"]:
        by_label[nk(d["label"])].add(d["district_id"])
        for m in d["members"]:
            by_name[nk(m["text"])].add(d["district_id"])
            if m.get("machiaza_id"):
                by_id[m["machiaza_id"]].add(d["district_id"])
    out = {}
    for co in gt["courses"]:
        per = sorted(co["areas_by_period"])[-1]
        for a in co["areas_by_period"][per]:
            ids = a.get("machiaza_id")
            ids = ids if isinstance(ids, list) else [ids] if ids else []
            hit = set(by_name.get(nk(a.get("name")), set()))
            if not hit and ids:
                hit = set().union(*(by_id.get(i, set()) for i in ids))
            if not hit:
                hit = set(by_label.get(nk(a.get("name")), set()))
            if not hit:
                hit = set(by_label.get(nk(co.get("course_name_ja")), set()))
            out[(co["course"], a.get("name"))] = set(hit)
    return out


def split_names(gt):
    """先方の 2 つ以上のコースに同じ名前で出る地区 (判別子の無い割れ町)。どのコースか決まらないので、系列の当てに使わない"""
    cnt = collections.Counter()
    for co in gt["courses"]:
        per = sorted(co["areas_by_period"])[-1]
        cnt.update({nk(a.get("name")) for a in co["areas_by_period"][per]})
    return {n for n, c in cnt.items() if c > 1}


def check_port():
    """広げ方を、先方が公開している ics/<handle>/<slug>.json の days と比べる → (同じだったコース数, 比べた数, 違い)"""
    gts = {g["handle"]: g for g in convert(load_tar()).values()}
    slug = lambda c: c.lower().replace("-", "", 1)
    ok = n = 0
    diffs = []
    for h, s in PORT_COURSES:
        body = _cached_body(f"{PAGES}/{h}/{s}.json")
        if body is None:
            diffs.append({"course": f"{h}/{s}", "why": "公開の JSON がキャッシュに無い (fetch --pages)"})
            continue
        pub = json.loads(body)
        co = next(c for c in gts[h]["courses"] if slug(c["course"]) == s)
        a = collections.defaultdict(set)
        for cat, ds in co["by_category"].items():
            for x in ds:
                a[x].add(cat)
        b = {k: set(v) for k, v in pub["days"].items() if str(FY_FROM) <= k <= str(FY_TO)}
        bad = sorted(k for k in set(a) | set(b) if a.get(k, set()) != b.get(k, set()))
        n += 1
        ok += not bad
        if bad:
            diffs.append({"course": f"{h}/{s}", "days": bad[:5]})
    return ok, n, diffs


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="japan-gomi-data を正解にする (取る・広げる・広げ方の確かめ)")
    ap.add_argument("cmd", choices=["fetch", "convert", "check-port"])
    ap.add_argument("--pages", action="store_true", help="fetch: 先方の公開の JSON (8 コース) と index.csv も取る")
    ap.add_argument("--offline", action="store_true", help="fetch: キャッシュだけを使う")
    a = ap.parse_args(argv)
    print(ATTRIBUTION)
    if a.cmd == "fetch":
        for r in fetch(a.pages, a.offline):
            print(json.dumps(r, ensure_ascii=False))
        return 0
    if a.cmd == "convert":
        gts = convert(load_tar())
        d = write_gt(gts)
        print(f"{len(gts)} 自治体を広げた → {d.relative_to(ROOT).as_posix()}/")
        return 0
    ok, n, diffs = check_port()
    print(f"広げ方: {ok}/{n} コースが先方の公開の JSON と 1 日も違わない")
    for x in diffs:
        print("  ", x)
    return 0 if ok == n == len(PORT_COURSES) else 1


if __name__ == "__main__":
    sys.exit(main())
