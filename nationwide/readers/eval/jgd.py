"""japan-gomi-data (tecolicom、CC BY 4.0) を正解にした採点 (DESIGN.md §4.2・§6.2 の J1〜J7)

  python nationwide/readers/eval/jgd.py fetch            # 固定したコミットの tar.gz を 1 回だけ取る (GitHub。礼儀を守って)
  python nationwide/readers/eval/jgd.py fetch --pages    # 広げ方の確かめに使う、先方が公開している 8 コースの JSON も取る
  python nationwide/readers/eval/jgd.py convert          # course-*.yaml を広げる → data/readers/eval/jgd/<lg_code>.json
  python nationwide/readers/eval/jgd.py check-port       # 広げ方が先方の公開の JSON と 1 日も違わないか (8 コース)
  python nationwide/readers/eval/jgd.py selftest         # 正解を v0.2 にして採点 → 誤り 0、わざと壊すと誤りが出るか
  python nationwide/readers/eval/jgd.py score [--schedules out/schedules] [--tier A]   # こちらの v0.2 を採点する (J1・J2・J5・J7)
  python nationwide/readers/eval/jgd.py oracle [--drafts]   # 札 (eval/oracle/) と突き合わせる (J4。人が確かめた札だけ数える)

終わりの値: 0 = 合格、1 = 誤りがある、2 = 測れない (答えるとした系列・確かめた札が無い。合格ではない)

比べた相手: japan-gomi-data (https://github.com/tecolicom/japan-gomi-data、作者 tecolicom、CC BY 4.0)。
コミット d786a985569efcf747adce721578a819d79ae891 (2026-10-05) に固定し、比べるためだけに使う。取ったものは data/readers/eval/ (git に入れない)。
先方のコードは動かさない。広げ方は tools/_lib/schedule.mjs (categoriesOn / expandRange) を手で Python に移したもの:
  weekly は days の曜日、monthly_nth は月の何回目 ((日 - 1) // 7 + 1) が occurrences に入る日、monthly_specific は dates の日。
  その日の overrides に cancelled が 1 つでもあれば、その日は全品目なし。cancelled でない overrides は、毎週の品目 + その品目 (月ごとの品目は落ちる)。
  unknown_periods の日と period (月の閉区間) の外は出さない (「不明」で「収集なし」ではない)

見出しの数 (§4.2): 答えるとした系列 (answerable。地区 × 品目 × 期間) のうち、1 年分の日付が正解と 1 日でも違うものの数と、
その 95% の上限 (誤り 0 なら 3/n、誤りがあれば Clopper-Pearson の片側)。マスの率 (A)・場所ごとの日 (B)・地区の当て方 (C) は測るだけ。
"""
import argparse, collections, copy, datetime as dt, io, json, math, os, pathlib, re, sys, tarfile, unicodedata

HERE = pathlib.Path(__file__).resolve().parent
READERS = HERE.parent
ROOT = READERS.parent.parent
sys.path.insert(0, str(READERS))
sys.path.insert(0, str(READERS.parent / "discover"))
import schema                                                # noqa: E402

REPO = "https://github.com/tecolicom/japan-gomi-data"
COMMIT = "d786a985569efcf747adce721578a819d79ae891"
TARBALL_URL = f"https://codeload.github.com/tecolicom/japan-gomi-data/tar.gz/{COMMIT}"
PAGES = "https://tecolicom.github.io/japan-gomi-data/ics"
LICENSE = "CC BY 4.0"
ATTRIBUTION = ("正解: japan-gomi-data (https://github.com/tecolicom/japan-gomi-data、作者 tecolicom、CC BY 4.0)。"
               f"コミット {COMMIT[:12]} に固定し、比べるためだけに使っている")
DATA = ROOT / "data" / "readers" / "eval"
TIERS_FILE = HERE / "jgd_tiers.json"
STALE_FILE = HERE / "gt_stale.json"
FY_FROM, FY_TO = dt.date(2026, 4, 1), dt.date(2027, 3, 31)
DOW = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
VOCAB = ("burnable", "kitchen", "non_burnable", "plastic", "pet_bottle", "beverage_can", "glass_bottle", "paper_cloth",
         "cloth", "paper", "hazardous", "spray_can", "oversized", "metal")
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
        raise SystemExit("tar.gz がキャッシュに無い。python nationwide/readers/eval/jgd.py fetch で取る")
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
    """固定した使える度合いの表 (eval/jgd_tiers.json) → {lg_code: 行}"""
    t = json.loads(TIERS_FILE.read_text("utf-8"))
    return {r["lg_code"]: r for r in t["municipalities"]}


# ---- 採点 ----
def nk(s):
    """名前を比べる形 (NFKC、空白なし、ヶ→ケ、漢数字の丁目)"""
    s = unicodedata.normalize("NFKC", str(s or "")).replace(" ", "").replace("　", "")
    s = s.replace("ヶ", "ケ").replace("ｹ", "ケ").replace("之", "の")
    s = re.sub(r"[〜～~∼]", "~", s)                      # 波ダッシュの字の違い (「1～3丁目」「1〜3丁目」)
    for i, k in enumerate("〇一二三四五六七八九"):
        s = re.sub(k + "丁目", f"{i}丁目", s)
    return s


def keys_for(coarse, gt_keys):
    """こちらの品目の分類 → 先方の鍵のうち比べるもの。先方が「古紙・古布」を 1 つの鍵 paper_cloth で持ち、paper・cloth を
    持たないときは、こちらの paper と cloth の両方を持つ品目を paper_cloth と比べる (比べないで済ませない)"""
    keys = [k for k in coarse if k in gt_keys]
    if "paper_cloth" in gt_keys and {"paper", "cloth"} <= set(coarse) and not {"paper", "cloth"} & set(gt_keys):
        keys.append("paper_cloth")
    return keys


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


def upper95(errors, n):
    """95% の片側の上限: 誤り 0 なら 3/n (rule of three)、誤りがあれば Clopper-Pearson"""
    if n == 0:
        return None
    if errors == 0:
        return round(3 / n, 6)
    lo, hi = errors / n, 1.0
    cdf = lambda p: sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(errors + 1))
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return round(hi, 6)


CHOME = re.compile(r"^(.+?)(\d+(?:[・,~〜\-]\d+)*)丁目$")


def _chome_set(s):
    """「1・3~5」→ {1, 3, 4, 5}"""
    out = set()
    for part in re.split(r"[・,]", s):
        m = re.fullmatch(r"(\d+)[~〜\-](\d+)", part)
        if m:
            out |= set(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def member_cover(m):
    """こちらの中身 → (町の名前, 丁目の集まりか None (町の全体)) か None (丁目の範囲で表せない中身)。§2.3 の「丁目ごとに広げる」"""
    if m.get("unit") not in ("machiaza",):
        return None
    conds = m.get("conditions") or []
    if any(c.get("kind") not in ("range", "note") or (c.get("kind") == "range" and c.get("unit") != "chome") for c in conds):
        return None
    name = nk((m.get("extra") or {}).get("name_verbatim") or m.get("text"))
    name = re.sub(r"全域$", "", re.sub(r"\(.*?\)", "", name))
    rng = set()
    for c in conds:
        if c.get("kind") == "range" and str(c.get("from")).isdigit() and str(c.get("to") or c.get("from")).isdigit():
            rng |= set(range(int(c["from"]), int(c.get("to") or c["from"]) + 1))
    mm = CHOME.match(name)
    if mm:
        name = mm.group(1)
        rng |= _chome_set(mm.group(2))
    return (name, rng or None)


def area_map_expanded(gt, doc):
    """area_map に、§2.3 の「丁目ごとに広げる」当て方を足したもの: 先方の「銀座1丁目」「大蔵5・6丁目」を、こちらの
    「銀座」(町の全体・「全域」) や「銀座」+ 丁目の範囲の中身に当てる。名前で当たったものはそのまま"""
    base = area_map(gt, doc)
    covers = collections.defaultdict(list)
    for d in doc["districts"]:
        for m in d["members"]:
            c = member_cover(m)
            if c:
                covers[c[0]].append((c[1], d["district_id"]))
    out = {}
    for key, ds in base.items():
        if ds:
            out[key] = ds
            continue
        name = re.sub(r"全域$", "", nk(key[1]))
        mm = CHOME.match(name)
        town, want = (mm.group(1), _chome_set(mm.group(2))) if mm else (name, None)
        hit = set()
        for rng, did in covers.get(town, []):
            if rng is None or (want and want <= rng):
                hit.add(did)
        out[key] = hit
    return out


def area_map(gt, doc):
    """先方の地区 (コースの area) → こちらの地区の id の集まり。名前 (割れ町は判別子つき)・町字 ID・地区の名前・コースの名前で引く"""
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


def series_window(p, s):
    """こちらの系列の比べる日: 系列の期間 ∩ 2026 年度、unknown_ranges を除く (ISO の日付の集まり)"""
    w = set(daterange(max(_d(p["valid_from"]), FY_FROM), min(_d(p["valid_to"] or str(FY_TO)), FY_TO)))
    return w - {d.isoformat() for d in schema.unknown_days(s)}


def load_stale(path=None):
    """gt_stale (正解が古い) と分けた食い違いの記録 (eval/gt_stale.json) → 項目のリスト"""
    p = pathlib.Path(path) if path else STALE_FILE
    try:
        return json.loads(p.read_text("utf-8")).get("items", [])
    except (OSError, ValueError):
        return []


def stale_evidence_ok(item):
    """gt_stale の項目に、正解の側 (extracted_at・edition) とこちらの側 (資料の sha256・版の証拠) の両方と、
    何が違うか (differs) が書いてあるか (§4.2・J7)"""
    ev = item.get("evidence") or {}
    gt_side = bool(ev.get("gt_extracted_at") or ev.get("gt_edition"))
    ours = bool(ev.get("our_source_sha256") or ev.get("our_version"))
    return gt_side and ours and bool(ev.get("differs"))


def check_stale(items):
    """J7: gt_stale に分けた食い違いのすべてに証拠があるか → {"n", "with_evidence", "without", "ok"}"""
    bad = [i.get("series") for i in items if not stale_evidence_ok(i)]
    return {"n": len(items), "with_evidence": len(items) - len(bad), "without": bad, "ok": not bad}


def score_series(gt, doc, answerable_only=True, stale=()):
    """系列の単位の採点 (J1) → {"n", "wrong", "upper95", "wrong_series", "uncomparable", "answered_outside_gt", "gt_stale", …}。
    こちらの地区に当たる先方のコース (割れ町を除く) のどれとも、品目の鍵ごとに日付が 1 日も違わないこと。
    食い違った系列は、stale (gt_stale の記録) に証拠つきで載っているときだけ gt_stale に分け、ほかは誤りに数える"""
    amap = area_map(gt, doc)
    split = split_names(gt)
    courses_of = collections.defaultdict(set)
    for (course, name), ds in amap.items():
        if nk(name) in split:
            continue
        for d in ds:
            courses_of[d].add(course)
    gtc = {c["course"]: c for c in gt["courses"]}
    gt_keys = {k for c in gt["courses"] for k in c["by_category"]}
    cats = {c["category_id"]: c for c in doc["categories"]}
    per = {p["period_id"]: p for p in doc["periods"]}
    res = {"lg_code": gt["lg_code"], "n": 0, "wrong": 0, "wrong_agreed": 0, "wrong_series": [], "uncomparable": [],
           "answered_outside_gt": 0, "not_answered": 0, "gt_stale": [], "gt_stale_rejected": []}
    stale_by = {(i.get("lg_code"), tuple(i.get("series") or ())): i for i in stale}
    for s in doc["schedules"]:
        if answerable_only and not s.get("answerable"):
            res["not_answered"] += 1
            continue
        keys = keys_for(cats[s["category_id"]]["coarse"], gt_keys)
        if not keys:
            res["uncomparable"].append({"series": schema.series_key(s), "why": "category_not_in_gt"})
            continue
        cs = sorted(courses_of.get(s["district_id"], ()))
        if not cs:
            res["uncomparable"].append({"series": schema.series_key(s), "why": "district_not_in_gt"})
            continue
        ours_window = series_window(per[s["period_id"]], s)
        mine = set(s["dates"])
        bad = []
        for c in cs:
            co = gtc[c]
            win = gt_window(gt, co) & ours_window
            res["answered_outside_gt"] += len((mine & ours_window) - gt_window(gt, co))   # 先方の不明・期間の外 (誤りにしない)
            for k in keys:
                g = set(co["by_category"].get(k, [])) & win
                o = mine & win
                if g != o:
                    bad.append({"course": c, "category": k, "only_gt": sorted(g - o)[:5], "only_ours": sorted(o - g)[:5]})
        res["n"] += 1
        st = stale_by.get((gt["lg_code"], tuple(schema.series_key(s))))
        if bad and st is not None:
            if stale_evidence_ok(st):
                res["gt_stale"].append({"series": schema.series_key(s), "diffs": bad[:3]})
                continue
            res["gt_stale_rejected"].append(schema.series_key(s))          # 証拠の無い gt_stale は誤りに数える
        if bad:
            res["wrong"] += 1
            res["wrong_agreed"] += (s.get("provenance") or {}).get("status") in ("agreed", "agreed_weak")
            res["wrong_series"].append({"series": schema.series_key(s), "diffs": bad[:3]})
    res["upper95"] = upper95(res["wrong"], res["n"])
    return res


def score_areas(gt, doc, answerable_only=True):
    """場所ごとの日 (J2) と地区の当て方 (J5): 先方の地区ごとに、当たったこちらの地区の答えるとした系列の日付が合うか。
    系列ごとに、その系列の期間の中の日だけを比べる (期間を 2 つに分けた地区 (女川・西東京) の、ほかの期間の日を誤りにしない)"""
    amap = area_map(gt, doc)
    cats = {c["category_id"]: c for c in doc["categories"]}
    per = {p["period_id"]: p for p in doc["periods"]}
    by_d = collections.defaultdict(list)
    for s in doc["schedules"]:
        if s.get("answerable") or not answerable_only:
            by_d[s["district_id"]].append(s)
    gtc = {c["course"]: c for c in gt["courses"]}
    gt_keys = {k for c in gt["courses"] for k in c["by_category"]}
    split = split_names(gt)
    n = found = ambiguous = wrong = 0
    wrong_areas = []
    for (course, name), ds in amap.items():
        n += 1
        if not ds:
            continue
        found += 1
        if len(ds) > 1 or nk(name) in split:
            ambiguous += 1                                   # 聞き返す (誤りに数えない)
            continue
        d = next(iter(ds))
        co = gtc[course]
        win = gt_window(gt, co)
        for s in by_d.get(d, []):
            keys = keys_for(cats[s["category_id"]]["coarse"], gt_keys)
            w = win & series_window(per[s["period_id"]], s)
            if any(set(co["by_category"].get(k, [])) & w != set(s["dates"]) & w for k in keys):
                wrong += 1
                wrong_areas.append({"area": name, "course": course, "district": d, "category": s["category_id"]})
                break
    return {"gt_areas": n, "area_recall": round(found / n, 4) if n else None, "ambiguous": ambiguous,
            "areas_wrong": wrong, "wrong_areas": wrong_areas[:20]}


def score(gt, doc, answerable_only=True, stale=()):
    return {"series": score_series(gt, doc, answerable_only, stale), "areas": score_areas(gt, doc, answerable_only),
            "attribution": ATTRIBUTION}


# ---- 札 (eval/oracle/) との突き合わせ (J4) ----
def oracle_expected(cat, district_id):
    """札の品目と地区 → "answerable" / "not_answerable" / "unknown"。district_overrides は district_ids (こちらの地区の id)
    があるときだけ地区に当てる。district_ids の無い上書きが品目の札と違えば、どの地区か決まらないので unknown"""
    unresolved = False
    for o in cat.get("district_overrides") or []:
        ids = o.get("district_ids")
        if ids is not None:
            if district_id in ids:
                return o.get("expected", "unknown")
        elif o.get("expected") != cat.get("expected"):
            unresolved = True
    return "unknown" if unresolved else cat.get("expected", "unknown")


def score_oracle(oracle, doc, include_drafts=False):
    """J4: 札が「答えられる」とした系列のうち答えるとしたもの、札が「答えられない」とした系列のうち答えるとしたもの。
    札の品目とこちらの品目は coarse が重なるもので当てる。人が確かめていない札 (human_checked: false) は数えない
    (include_drafts で見るだけ。draft: true のまま)"""
    res = {"lg_code": oracle["lg_code"], "counted": bool(oracle.get("human_checked")) or include_drafts,
           "draft": not oracle.get("human_checked"), "answerable_expected": 0, "answered_of_expected": 0,
           "not_answerable_expected": 0, "answered_of_not": 0, "wrong_series": [], "unknown": 0, "unlabeled": 0}
    if not res["counted"]:
        res["why"] = "人が確かめていない札 (下書き) は数えない"
        return res
    cats = {c["category_id"]: c for c in doc["categories"]}
    per = {p["period_id"]: p for p in doc["periods"]}
    poi = oracle.get("period_of_interest") or {}
    for s in doc["schedules"]:
        p = per[s["period_id"]]
        if poi and ((p.get("valid_to") or "9999-12-31") < poi["valid_from"] or p["valid_from"] > poi["valid_to"]):
            continue
        labs = [c for c in oracle["categories"] if set(c["coarse"]) & set(cats[s["category_id"]]["coarse"])]
        if not labs:
            res["unlabeled"] += 1
            continue
        exp = {oracle_expected(c, s["district_id"]) for c in labs}
        if len(exp) != 1 or "unknown" in exp:
            res["unknown"] += 1
            continue
        if exp.pop() == "answerable":
            res["answerable_expected"] += 1
            res["answered_of_expected"] += bool(s.get("answerable"))
        else:
            res["not_answerable_expected"] += 1
            if s.get("answerable"):
                res["answered_of_not"] += 1
                res["wrong_series"].append(schema.series_key(s))
    a = res["answerable_expected"]
    res["answered_rate"] = round(res["answered_of_expected"] / a, 4) if a else None
    res["ok"] = (a == 0 or res["answered_of_expected"] >= 0.9 * a) and res["answered_of_not"] == 0
    return res


def load_oracles():
    """eval/oracle/<lg_code>.json → {lg_code: 札}"""
    out = {}
    for p in sorted((HERE / "oracle").glob("*.json")):
        o = json.loads(p.read_text("utf-8"))
        out[o["lg_code"]] = o
    return out


# ---- 正解 → v0.2 (selftest) ----
def runs(days):
    """日付 (ISO) の集まり → 続いた日のまとまり [(始め, 終わり)]"""
    out = []
    for x in sorted(days):
        if out and _d(out[-1][1]) + dt.timedelta(days=1) == _d(x):
            out[-1][1] = x
        else:
            out.append([x, x])
    return [tuple(r) for r in out]


def gt_to_v02(gt):
    """正解をそのまま v0.2 にする (地区 = コース、品目 = 先方の鍵 1 つずつ、answerable・agreed)"""
    lg = gt["lg_code"]
    periods = [{"period_id": "fy2026", "valid_from": gt["window"]["from"], "valid_to": gt["window"]["to"],
                "basis": {"text": "japan-gomi-data", "kind": "stated"},
                "year_evidence": {"token": {"text": "2026", "where": "in_file"}, "time": [{"kind": "last_modified", "value": "2026-04-01"}],
                                  "conflict": None, "ok": True}}]
    keys = sorted({k for c in gt["courses"] for k in c["by_category"]})
    cats = [{"category_id": k, "official_label": k, "coarse": [k], "mode": "curbside"} for k in keys]
    dists, scheds = [], []
    for i, co in enumerate(gt["courses"]):
        did = f"{lg}-c{i}"
        per = sorted(co["areas_by_period"])[-1]
        members = [{"text": a.get("name") or "", "unit": "machiaza", "machiaza_id": (a.get("machiaza_id") if isinstance(a.get("machiaza_id"), str) else None),
                    "match": "unchecked", "conditions": [], "geometry": "polygon"} for a in co["areas_by_period"][per]]
        dists.append({"district_id": did, "partition_id": "all", "label": co.get("course_name_ja") or co["course"],
                      "label_kind": "official", "members": members, "place_resolution": "polygon"})
        unknown = [{"from": max(u["from"], periods[0]["valid_from"]), "to": min(u["to"], periods[0]["valid_to"]), "reason": "deferred"}
                   for u in co.get("unknown") or []
                   if u["to"] >= periods[0]["valid_from"] and u["from"] <= periods[0]["valid_to"]]      # 期間の中に切る
        win = gt_window(gt, co)
        out_days = set(daterange(periods[0]["valid_from"], periods[0]["valid_to"])) - win
        out_days -= {d for u in unknown for d in daterange(u["from"], u["to"])}
        ur = unknown + [{"from": a, "to": b, "reason": "deferred", "note": "先方の期間の外"} for a, b in runs(out_days)]
        for k in keys:
            ds = [x for x in co["by_category"].get(k, [])]
            scheds.append({"district_id": did, "category_id": k, "period_id": "fy2026", "dates_origin": "printed", "rule": None,
                           "dates": ds, "unknown_ranges": list(ur), "answerable": True, "blocked_by": [], "gate": {"unit": "B", "open": False},
                           "published": False, "caveats": [], "expanded_with": [], "rule_mismatches": [],
                           "provenance": {"readers": [{"reader": "gt", "kind": "code"}], "status": "agreed", "independence": "code_llm"}})
    return {"schema": schema.SCHEMA, "lg_code": lg, "name": gt.get("name_ja") or lg, "periods": periods,
            "partitions": [{"partition_id": "all", "category_ids": keys, "basis": "unknown"}], "districts": dists,
            "categories": cats, "schedules": scheds, "exceptions": [], "sources": [],
            "watch": {"sources": []}, "coverage": {"answer_type": "rule_contact"}, "build": {"from": "selftest"}}


def selftest(gt):
    """正解をそのまま入れて誤り 0、わざと壊して誤りが出るか"""
    base = gt_to_v02(gt)
    out = {"identity": score(gt, base)["series"]}
    p = copy.deepcopy(base)
    s = next(x for x in p["schedules"] if x["dates"])
    s["dates"] = s["dates"][1:]                                    # 1 日落とす
    out["drop_one_day"] = score(gt, p)["series"]
    p = copy.deepcopy(base)
    if len(p["districts"]) > 1:                                     # 2 地区の中身を入れ替える
        a, b = p["districts"][0], p["districts"][1]
        a["members"], b["members"] = b["members"], a["members"]
        a["label"], b["label"] = b["label"], a["label"]
    out["swap_districts"] = score(gt, p)
    p = copy.deepcopy(base)
    for s in p["schedules"]:
        s["answerable"], s["blocked_by"] = False, ["single_reading"]
    out["nothing_answered"] = score(gt, p)["series"]
    return out


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
    ap = argparse.ArgumentParser(description="japan-gomi-data を正解にした採点")
    ap.add_argument("cmd", choices=["fetch", "convert", "check-port", "selftest", "score", "oracle"])
    ap.add_argument("--pages", action="store_true", help="fetch: 先方の公開の JSON (8 コース) と index.csv も取る")
    ap.add_argument("--offline", action="store_true", help="fetch: キャッシュだけを使う")
    ap.add_argument("--schedules", default=str(ROOT / "out" / "schedules"))
    ap.add_argument("--tier", default="A")
    ap.add_argument("--all", action="store_true", help="score: answerable でない系列も採点する (読み取りだけの成績)")
    ap.add_argument("--drafts", action="store_true", help="oracle: 人が確かめていない札も見る (J4 の数には入れない)")
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
    if a.cmd == "check-port":
        ok, n, diffs = check_port()
        print(f"広げ方: {ok}/{n} コースが先方の公開の JSON と 1 日も違わない")
        for x in diffs:
            print("  ", x)
        return 0 if ok == n == len(PORT_COURSES) else 1
    if a.cmd == "selftest":
        bad = 0
        for lg in sorted(tiers()):
            gt = load_gt(lg)
            if gt is None:
                continue
            r = selftest(gt)
            line = (f"{lg} {gt['name_ja']}: そのまま n={r['identity']['n']} 誤り {r['identity']['wrong']} 上限 {r['identity']['upper95']}・"
                    f"1 日落とす 誤り {r['drop_one_day']['wrong']}・入れ替え 誤り {r['swap_districts']['series']['wrong']}・"
                    f"答えない n={r['nothing_answered']['n']}")
            bad += r["identity"]["wrong"] != 0 or r["drop_one_day"]["wrong"] != 1 or r["nothing_answered"]["n"] != 0
            print(line)
        return 1 if bad else 0
    if a.cmd == "oracle":
        return oracle_main(a)
    stale = load_stale()
    j7 = check_stale(stale)
    tot_n = tot_w = tot_stale = 0
    for lg, t in sorted(tiers().items()):
        if a.tier != "all" and t["tier"] != a.tier:
            continue
        p = pathlib.Path(a.schedules) / f"{lg}.json"
        gt = load_gt(lg)
        if not p.exists() or gt is None:
            print(f"{lg} {t['name_ja']}: {'こちらの出力が無い' if not p.exists() else '正解を広げていない (convert)'}")
            continue
        r = score(gt, json.loads(p.read_text("utf-8")), not a.all, stale)
        s = r["series"]
        tot_n += s["n"]
        tot_w += s["wrong"]
        tot_stale += len(s["gt_stale"])
        print(f"{lg} {t['name_ja']}: 答えるとした系列 {s['n']}・誤り {s['wrong']} (一致したのに誤り {s['wrong_agreed']})・"
              f"比べられない {len(s['uncomparable'])}・答えなかった {s['not_answered']}・area_recall {r['areas']['area_recall']}・"
              f"違う地区 {r['areas']['areas_wrong']}")
    print(f"計: 答えるとした系列 n={tot_n}・誤り {tot_w}・95% の上限 {upper95(tot_w, tot_n)}・gt_stale {tot_stale}")
    print(f"J7: gt_stale の記録 {j7['n']} 件のうち証拠のあるもの {j7['with_evidence']}{'' if j7['ok'] else ' (証拠の無いものがある)'}")
    if tot_w or not j7["ok"]:
        return 1
    if tot_n == 0:
        print("J1・J2: 答えるとした系列が無いので測れない (合格ではない)")
        return 2
    return 0


def oracle_main(a):
    """jgd.py oracle: J4 (人が確かめた札だけを数える)"""
    keys = ("answerable_expected", "answered_of_expected", "not_answerable_expected", "answered_of_not")
    cnt = dict.fromkeys(keys, 0)
    counted = 0
    for lg, o in load_oracles().items():
        p = pathlib.Path(a.schedules) / f"{lg}.json"
        if not p.exists():
            print(f"{lg} {o['name']}: こちらの出力が無い ({'下書きの札' if not o.get('human_checked') else '確かめた札'})")
            continue
        r = score_oracle(o, json.loads(p.read_text("utf-8")), a.drafts)
        if not r["counted"]:
            print(f"{lg} {o['name']}: {r['why']}")
            continue
        print(f"{lg} {o['name']}{' (下書き。数に入れない)' if r['draft'] else ''}: 答えられる札 {r['answerable_expected']} のうち答えた "
              f"{r['answered_of_expected']}・答えられない札 {r['not_answerable_expected']} のうち答えた {r['answered_of_not']}・"
              f"決まらない {r['unknown']}・札なし {r['unlabeled']}")
        if not r["draft"]:
            counted += 1
            for k in keys:
                cnt[k] += r[k]
    if counted == 0:
        print("J4: 人が確かめた札とこちらの出力の組が無いので測れない (合格ではない)")
        return 2
    ae = cnt["answerable_expected"]
    ok = (ae == 0 or cnt["answered_of_expected"] >= 0.9 * ae) and cnt["answered_of_not"] == 0
    print(f"J4: 答えられる札のうち答えた {cnt['answered_of_expected']}/{ae}・答えられない札のうち答えた "
          f"{cnt['answered_of_not']}/{cnt['not_answerable_expected']} → {'合格' if ok else '不合格'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
