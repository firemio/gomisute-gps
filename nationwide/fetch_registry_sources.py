"""全国の自治体台帳の元データを data/registry/ に取得する

  python nationwide/fetch_registry_sources.py            # 無いものだけ取得
  python nationwide/fetch_registry_sources.py --force    # すべて取り直す
  python nationwide/fetch_registry_sources.py --only abr_town_pos,wikidata
  python nationwide/fetch_registry_sources.py --check    # 取得せず、URL が生きているかだけ見る (HEAD)。
                                                         # 台帳 (out/registry/) の J-LIS とホストが違う自治体の公式サイトも
                                                         # (名前が引けるか (DNS) と HEAD)

礼儀: 1 ホストにつき 1 秒に 1 回まで (robots.txt に Crawl-delay があればそれに従う)。
User-Agent で名乗り、robots.txt を守る (RFC 9309 の「いちばん長く一致する規則」で判定)。
転送 (リダイレクト) は自分で追い、転送先のホストでも robots.txt と間隔を守る。別のホストへの転送は画面に出し、記録に残す。
ファイルは .part に書いてから名前を変える (途中で止まっても半端なファイルが「取得済み」にならない)。
取得の記録 (URL・大きさ・sha256・取得日時) は data/registry/manifest.json に残す。
出典と利用条件は nationwide/README.md。
"""
import sys, io, re, json, time, shutil, socket, zipfile, hashlib, pathlib, datetime, argparse
import urllib.request, urllib.parse, urllib.error

UA = "gomisute-gps (+https://github.com/firemio/gomisute-gps)"
UA_TOKEN = "gomisute-gps"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "registry"

ABR = "https://data.address-br.digital.go.jp"
MLIT_CHIRIT = "https://www.mlit.go.jp/kokudoseisaku/chirit/content"

# (名前, URL, 保存先, zip から取り出すファイル名 or None)
STATIC = [
    # 総務省「都道府県コード及び市区町村コード」(令和6年1月1日更新) 一覧ページ: https://www.soumu.go.jp/denshijiti/code.html
    ("soumu_code", "https://www.soumu.go.jp/main_content/000925835.xlsx", "soumu/000925835.xlsx", None),
    # デジタル庁 アドレス・ベース・レジストリ (配布ファイルの索引、市区町村、町字、町字の代表点)
    ("abr_feed", "https://dataset.address-br.digital.go.jp/api/feed/dcat-us/1.1.json", "abr/dcat-us-1.1.json", None),
    ("abr_city", f"{ABR}/mt_city/mt_city_all.csv.zip", "abr/mt_city_all.csv.zip", None),
    ("abr_town", f"{ABR}/mt_town/mt_town_all.csv.zip", "abr/mt_town_all.csv.zip", None),
] + [
    ("abr_town_pos", f"{ABR}/mt_town_pos/pref/mt_town_pos_pref{p:02d}.csv.zip",
     f"abr/mt_town_pos/mt_town_pos_pref{p:02d}.csv.zip", None) for p in range(1, 48)
] + [
    # 国土数値情報 行政区域 N03-2026 (2026-01-01 時点、全国、約 770MB の zip。GeoJSON だけ取り出す)
    ("n03", "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/N03-20260101_GML.zip",
     "n03/N03-20260101.geojson", "N03-20260101.geojson"),
    # 環境省 一般廃棄物処理実態調査 令和6年度 (全国の市町村別 ごみ処理概要)
    ("env_r6", "https://www.env.go.jp/recycle/waste_tech/ippan/r6/data/shori/total/01.xlsx",
     "env/r6_shori_total_01.xlsx", None),
    # 国土交通省 離島振興対策実施地域一覧 (令和8年4月1日現在) と 日本の島嶼の構成
    ("mlit_ritou", f"{MLIT_CHIRIT}/001886615.pdf", "islands/mlit_001886615_ritou_ichiran.pdf", None),
    ("mlit_tosho", f"{MLIT_CHIRIT}/001477518.pdf", "islands/mlit_001477518_tosho_kousei.pdf", None),
    # 沖縄県 離島関係資料 (令和8年3月) 第1章 指定離島一覧
    ("okinawa", "https://www.pref.okinawa.jp/_res/projects/default_project/_page_/001/038/825/01.chapter1.r8.pdf",
     "islands/okinawa_ritou_r8_ch1.pdf", None),
    # 奄美: 鹿児島県「奄美群島の概況」(島ごとの市町村) / 国交省「奄美群島の概要」/ 瀬戸内町のあらまし
    ("amami", "https://www.pref.kagoshima.jp/ac07/documents/105247_20230327213452-1.pdf",
     "islands/kagoshima_amami_gaikyou.pdf", None),
    ("amami_mlit", "https://www.mlit.go.jp/kokudoseisaku/chitok/crd_amaoga_tk_000008.html",
     "islands/mlit_amami_gaiyou.html", None),
    ("amami_setouchi", "https://www.town.setouchi.lg.jp/joho/cho/chosei/gaiyo/profile.html",
     "islands/setouchi_profile.html", None),
    # 小笠原: 国交省「小笠原諸島の概要」
    ("ogasawara_mlit", "https://www.mlit.go.jp/kokudoseisaku/chitok/crd_amaoga_tk_000009.html",
     "islands/mlit_ogasawara_gaiyou.html", None),
]

# Wikidata の P429 (全国地方公共団体コード) と P856 (公式ウェブサイト)。データは CC0。
# 1. どの項目 (Q…) がどのコードかを、Wikidata の写しを公開している QLever (University of Freiburg) に 1 回だけ聞く。
#    query.wikidata.org は robots.txt で /sparql を禁じているので使わない。
# 2. QLever の写しは古いことがある (荒らしで航空会社の URL に書き換えられたままの項目があった) ので、
#    公式サイトは項目ごとに wikidata.org の Special:EntityData/Q….json (robots.txt で許可) から今の値を取る。
WIKIDATA_ENDPOINT = "https://qlever.dev/api/wikidata"
WIKIDATA_QUERY = """PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?item ?code ?label ?dissolved WHERE {
  ?item wdt:P429 ?code .
  OPTIONAL { ?item rdfs:label ?label . FILTER(LANG(?label) = "ja") }
  OPTIONAL { ?item wdt:P576 ?dissolved }
}"""
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"

# J-LIS「全国自治体マップ検索」(都道府県ごとのリンク集。突き合わせにだけ使う)
JLIS_TOP = "https://www.j-lis.go.jp/spd/map-search/cms_1069.html"


MAX_REDIRECTS = 5          # RFC 9309 も robots.txt の転送を 5 回まで追うことを求める
REDIRECT_CODES = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib に転送を追わせない (転送先の robots.txt と間隔を守るため、自分で追う)"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


class Polite:
    """ホストごとの間隔と robots.txt を守って取る"""

    def __init__(self):
        self.last = {}
        self.robots = {}

    def _open(self, url, method=None, data=None, headers=None):
        """転送を 1 回ずつ追う。どの URL でも robots.txt を確かめ、ホストごとの間隔を空ける。
        返す応答の geturl() が最後の URL"""
        first = url
        for _ in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(url).netloc
            rules, delay = self._robots(host, url)
            if not allowed(rules, url):
                raise PermissionError(f"robots.txt で禁止: {url}")
            self._wait(host, delay)
            h = {"User-Agent": UA}
            h.update(headers or {})
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            try:
                return OPENER.open(req, timeout=600)
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location") if e.code in REDIRECT_CODES else None
                e.close()
                if not loc:
                    raise
                new = urllib.parse.urljoin(url, loc)
                if host_of(new) != host_of(url):
                    print(f"転送 (別のホスト) {e.code}: {url} → {new}", flush=True)
                if e.code == 303 or (e.code in (301, 302) and data is not None):
                    method, data = ("HEAD" if method == "HEAD" else None), None    # GET で追う
                    headers = {k: v for k, v in (headers or {}).items() if k.lower() != "content-type"}
                url = new
        raise RuntimeError(f"転送が {MAX_REDIRECTS} 回を超えた: {first}")

    def _wait(self, host, delay=1.0):
        dt = time.time() - self.last.get(host, 0)
        if dt < delay:
            time.sleep(delay - dt)
        self.last[host] = time.time()

    def _robots(self, host, url):
        if host not in self.robots:
            robots_url = f"{urllib.parse.urlsplit(url).scheme}://{host}/robots.txt"
            self.robots[host] = self._fetch_robots(robots_url)
        return self.robots[host]

    def _fetch_robots(self, robots_url):
        """robots.txt を取る。転送は 5 回まで追い (間隔は転送先のホストごと)、4xx は「規則なし」(RFC 9309)"""
        for _ in range(MAX_REDIRECTS + 1):
            self._wait(urllib.parse.urlsplit(robots_url).netloc)
            req = urllib.request.Request(robots_url, headers={"User-Agent": UA})
            try:
                with OPENER.open(req, timeout=60) as r:
                    text = r.read().decode("utf-8", "replace")
                    ctype = r.headers.get("Content-Type", "")
                # robots.txt の代わりに HTML の案内ページを返すサーバーがある → 規則なしとみなす
                return parse_robots(text) if "html" not in ctype else ([], 1.0)
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location") if e.code in REDIRECT_CODES else None
                e.close()
                if loc:
                    robots_url = urllib.parse.urljoin(robots_url, loc)
                elif 400 <= e.code < 500:
                    return [], 1.0
                else:
                    raise
        return [], 1.0                       # 転送が多すぎる: RFC 9309 では「規則なし」

    def get(self, url, **kw):
        """中身・ヘッダー・最後の URL (転送されたときは転送先)"""
        with self._open(url, **kw) as r:
            return r.read(), dict(r.headers), r.geturl()

    def download(self, url, dest):
        """大きなファイルも一時ファイル (.part) に流し込む。置き換えは呼ぶ側"""
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with self._open(url) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
                headers, final = dict(r.headers), r.geturl()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp, headers, final

    def head(self, url):
        try:
            with self._open(url, method="HEAD") as r:
                return r.status, dict(r.headers), r.geturl()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), url


def save(dest, data):
    """一時ファイル (.part) に書いてから置き換える (途中で止まっても半端なファイルを残さない)"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def parse_robots(text):
    """自分 (gomisute-gps) に当たるグループの規則と Crawl-delay を返す (無ければ * のグループ)"""
    groups, cur, in_rules = [], None, False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if cur is None or in_rules:
                cur = {"agents": [], "rules": [], "delay": None}
                groups.append(cur)
                in_rules = False
            cur["agents"].append(v.lower())
        elif cur is not None and k in ("allow", "disallow"):
            in_rules = True
            if v:
                cur["rules"].append((k == "allow", v))
        elif cur is not None and k == "crawl-delay":
            in_rules = True
            try:
                cur["delay"] = float(v)
            except ValueError:
                pass
    # RFC 9309: user-agent の値と自分の名前 (product token) を大文字小文字を区別せずに比べる。部分一致にはしない
    mine = [g for g in groups if any(a.split("/")[0].strip() == UA_TOKEN for a in g["agents"])]
    star = [g for g in groups if "*" in g["agents"]]
    chosen = mine or star
    rules = [r for g in chosen for r in g["rules"]]
    delays = [g["delay"] for g in chosen if g["delay"]]
    return rules, max([1.0] + delays)


def allowed(rules, url):
    """RFC 9309: いちばん長く一致する規則が勝つ。同じ長さなら allow"""
    u = urllib.parse.urlsplit(url)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    best = (-1, True)
    for allow, pat in rules:
        rx = "^" + re.escape(pat).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        if re.match(rx, path):
            n = len(pat)
            if n > best[0] or (n == best[0] and allow):
                best = (n, allow)
    return best[1]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def record(manifest, dest, url, headers=None, note=None, final_url=None):
    rel = dest.relative_to(OUT).as_posix()
    manifest[rel] = {
        "url": url, "bytes": dest.stat().st_size, "sha256": sha256(dest),
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "last_modified": (headers or {}).get("Last-Modified"),
    }
    if final_url and final_url != url:
        manifest[rel]["final_url"] = final_url
    if note:
        manifest[rel]["note"] = note


def fetch_static(web, manifest, name, url, rel, member, force):
    dest = OUT / rel
    if dest.exists() and not force:
        if rel not in manifest:   # 手で置いたファイルも記録だけはしておく
            record(manifest, dest, url, note="既にあったファイル (この実行では取得していない)")
        print(f"skip {rel} (あり)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if member:   # zip から 1 ファイルだけ取り出す (zip 本体は残さない)。取り出しも .part に書いてから名前を変える
        zdest = dest.parent / pathlib.Path(urllib.parse.urlsplit(url).path).name
        tmp, headers, final = web.download(url, zdest)
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            with zipfile.ZipFile(tmp) as z, z.open(member) as src, open(part, "wb") as f:
                shutil.copyfileobj(src, f, 1 << 20)
            part.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
    else:
        tmp, headers, final = web.download(url, dest)
        tmp.replace(dest)
    record(manifest, dest, url, headers, final_url=final)
    print(f"get  {rel} {dest.stat().st_size:,}B  ({name})")


def fetch_wikidata(web, manifest, force):
    city = OUT / "abr" / "mt_city_all.csv.zip"
    if not city.exists():
        sys.exit(f"Wikidata の取得には {city.relative_to(ROOT).as_posix()} が要る。"
                 "先に python nationwide/fetch_registry_sources.py --only abr_city")
    d = OUT / "wikidata"
    d.mkdir(parents=True, exist_ok=True)
    index = d / "p429_items.csv"
    if force or not index.exists():
        body = urllib.parse.urlencode({"query": WIKIDATA_QUERY}).encode()
        b, headers, final = web.get(WIKIDATA_ENDPOINT, data=body, headers={
            "Accept": "text/csv", "Content-Type": "application/x-www-form-urlencoded"})
        save(index, b)
        save(d / "query.rq", WIKIDATA_QUERY.encode("utf-8"))
        record(manifest, index, WIKIDATA_ENDPOINT, headers, note="SPARQL は wikidata/query.rq", final_url=final)
        print(f"get  wikidata/p429_items.csv {len(b):,}B")
    # 台帳に使うコード (ABR mt_city の市区町村。区と都道府県は除く) の項目だけ取る
    import csv as _csv
    zf = zipfile.ZipFile(city)
    rows = _csv.DictReader(io.StringIO(zf.read(zf.namelist()[0]).decode("utf-8-sig")))
    codes = {r["lg_code"] for r in rows if not r["ward"] and r["lg_code"][2:] != "0000"}
    want = sorted({r["item"].rsplit("/", 1)[-1] for r in _csv.DictReader(open(index, encoding="utf-8"))
                   if r["code"].strip() in codes})
    out = d / "entities.jsonl"
    have = set()
    if out.exists() and not force:
        good, broken = [], 0
        for line in open(out, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                good.append(json.loads(line))
            except json.JSONDecodeError:
                broken += 1                  # 途中で止まったときの半端な行
        if broken:
            print(f"Wikidata: 壊れた行 {broken} を捨てて書き直す")
            save(out, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in good).encode("utf-8"))
        have = {e["id"] for e in good}
    todo = [q for q in want if q not in have]
    print(f"Wikidata: 項目 {len(want)}、取得済み {len(have & set(want))}、これから {len(todo)} (1 秒に 1 件)")
    with open(out, "w" if (force or not out.exists()) else "a", encoding="utf-8") as f:
        for i, q in enumerate(todo):
            b, _, _ = web.get(ENTITY_URL.format(q))
            ent = next(iter(json.loads(b)["entities"].values()))   # 転送された項目は転送先が返る
            f.write(json.dumps(compact_entity(q, ent), ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 99:
                print(f"  {i + 1}/{len(todo)}", flush=True)
    record(manifest, out, ENTITY_URL.format("Q…"), note=f"{len(want)} 項目を 1 件ずつ取得 (必要な値だけ残す)")


def compact_entity(qid, ent):
    """必要な値だけ残す: P429・P856 (順位と言語の修飾子つき)・P576、日本語のラベル、更新日時"""
    def stmts(pid):
        out = []
        for c in ent.get("claims", {}).get(pid, []):
            v = c["mainsnak"].get("datavalue", {}).get("value")
            if isinstance(v, dict):
                v = v.get("time") or v.get("id")
            langs = [q["datavalue"]["value"]["id"] for q in c.get("qualifiers", {}).get("P407", [])
                     if "datavalue" in q]
            out.append({"value": v, "rank": c["rank"], "lang": langs})
        return out
    return {"id": qid, "resolved_id": ent.get("id"), "modified": ent.get("modified"),
            "label_ja": ent.get("labels", {}).get("ja", {}).get("value"),
            "P429": stmts("P429"), "P856": stmts("P856"), "P576": stmts("P576")}


def fetch_jlis(web, manifest, force):
    d = OUT / "jlis"
    d.mkdir(parents=True, exist_ok=True)
    top = d / "cms_1069.html"
    if force or not top.exists():
        b, h, final = web.get(JLIS_TOP)
        save(top, b)
        record(manifest, top, JLIS_TOP, h, final_url=final)
    text = top.read_text(encoding="utf-8", errors="replace")
    pages = sorted(set(re.findall(r'href="(/spd/map-search/[a-z]+/cms_\d+\.html)"', text)))
    print(f"J-LIS: {len(pages)} ページ")
    for p in pages:
        url = urllib.parse.urljoin(JLIS_TOP, p)
        dest = d / (p.split("/")[-2] + "_" + p.split("/")[-1])
        if dest.exists() and not force:
            continue
        b, h, final = web.get(url)
        save(dest, b)
        record(manifest, dest, url, h, final_url=final)
        print(f"get  jlis/{dest.name}")


def check(web):
    """取得はせず、URL が生きているか・手元と大きさが同じかを見る"""
    bad = 0
    for name, url, rel, member in STATIC:
        st, h, final = web.head(url)
        local = OUT / rel
        size = h.get("Content-Length")
        same = "" if member else (
            "" if not local.exists() else ("同じ大きさ" if str(local.stat().st_size) == size else f"手元 {local.stat().st_size:,}B"))
        moved = f"  → {final}" if final != url else ""
        print(f"{st} {name:15} {size or '?':>12} {h.get('Last-Modified', '')}  {same}  {url}{moved}")
        bad += st != 200
    return bad


def check_sites(web, registry=ROOT / "out" / "registry" / "municipalities.json"):
    """台帳の公式サイトのうち、J-LIS とホストが違う自治体 (Wikidata の URL が古いことがある) の URL が生きているか:
    名前が引けるか (DNS) と HEAD (robots.txt と間隔は同じに守る)。引けない・つながらない数を返す"""
    try:
        rows = json.loads(registry.read_text(encoding="utf-8"))["municipalities"]
    except (OSError, ValueError, KeyError):
        print("台帳が無いので公式サイトは見ない (先に build_registry.py)")
        return 0
    bad = 0
    for r in rows:
        if r["official_sites_check"].get("jlis_same_host") is not False:
            continue
        for u in r["official_sites"]:
            host = urllib.parse.urlsplit(u).hostname or ""
            try:
                socket.getaddrinfo(host, None)
            except (OSError, UnicodeError):
                print(f"DNS {r['lg_code']} {r['name']}: 名前が引けない {u}")
                bad += 1
                continue
            try:
                st, h, final = web.head(u)
            except (OSError, PermissionError, RuntimeError, urllib.error.URLError) as e:
                print(f"--- {r['lg_code']} {r['name']}: つながらない {u} ({type(e).__name__})")
                bad += 1
                continue
            moved = f"  → {final}" if final != u else ""
            print(f"{st} {r['lg_code']} {r['name']}: {u}{moved}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="あっても取り直す")
    ap.add_argument("--only", default="", help="名前をカンマ区切りで (soumu_code, abr_town_pos, n03, wikidata, jlis …)")
    ap.add_argument("--check", action="store_true", help="HEAD で URL を確かめるだけ (台帳の J-LIS とホストが違う公式サイトも)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    only = set(filter(None, a.only.split(",")))
    names = sorted({n for n, *_ in STATIC} | {"wikidata", "jlis"})
    if only - set(names):
        ap.error(f"知らない名前: {', '.join(sorted(only - set(names)))}  (使える名前: {', '.join(names)})")
    web = Polite()
    if a.check:
        bad = check(web)
        bad += check_sites(web)
        sys.exit(1 if bad else 0)
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    try:
        for name, url, rel, member in STATIC:
            if not only or name in only:
                fetch_static(web, manifest, name, url, rel, member, a.force)
        if not only or "wikidata" in only:
            fetch_wikidata(web, manifest, a.force)
        if not only or "jlis" in only:
            fetch_jlis(web, manifest, a.force)
    finally:
        save(mpath, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))


if __name__ == "__main__":
    main()
