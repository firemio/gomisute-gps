#!/usr/bin/env python3
"""Worker 用の自治体ごとのデータパックを作る: 金沢市 (172014)・女川町 (045811)

  python nationwide/packs/build_packs.py [--fetch-missing]

出力 (worker/ の下):
  packs/<lg_code>/<名前>.<元の JSON の sha256 の頭12桁>.json.gz
      R2 (binding PACKS) に置くパック。名前は core (地区・収集日・出典・女川町の分別表など) と addr (金沢市の住所の索引)
  packs/172014/loc.<sha>.bin.gz・packs/172014/loc/<tx>_<ty>.<sha>.bin.gz
      金沢市の緯度経度の判定 (resolve.Kanazawa.locate) に使う多角形・境界と、2km 四方 + 余白 450m のタイル (build_loc.py)
  src/generated/manifest.ts   自治体 → パックの鍵・ハッシュ・大きさ、タイルの一覧 (Worker に同梱する)
  src/generated/mcp_meta.ts   mcp_server.py のツールの定義・RULES の文など (同じ文面を使うため、mcp_server.py から抜き出す)

判定に要る計算は、ここで Python の実装 (resolve.py・kzaddress.py・kzrules.py・onagawa.py) をそのまま呼んで済ませる。
Worker (TypeScript) は、住所の文字を読んで、ここで作った結果を引くだけ:
  - 金沢市の住所: 国土地理院の住居表示住所 (町・街区・住居番号)・街区・ABR の地番 (町・小字・番号) すべてについて
    kzaddress.AddressIndex.evaluate() と同じ結果 (地区・区域表の行・実在するか)
  - 収集日: 地区 × 品目ごとの日付 (RRULE を展開したもの)、規則と違う日の印、年末年始の最後と最初の日
  - 女川町: 行政区・地名の索引、見出しの注 (label_note) の有効期間、品目の照合に使う名前 (onagawa.item_names) など
作ったあと、パックから組み立て直した収集日が Python の答えと全部同じかを確かめる (違えば止まる)。

元のデータの取り直し方は INPUTS に URL ごと書いた。--fetch-missing で、無いものだけ取り直す
(polite.py: robots.txt を守り、1ホスト 1秒に1回まで、User-Agent で名乗る)。
"""
import ast, collections, datetime as dt, gzip, hashlib, io, json, os, re, subprocess, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError): pass

WORKER = os.path.join(ROOT, "worker")
ABR = "https://data.address-br.digital.go.jp"

# 元のデータ: (置き場所, 取り直す URL, 作り方)。out/ のファイルはリポジトリに入っている (作り方の欄のスクリプトで作り直せる)
INPUTS = [
    ("data/abr/mt_rsdtdsp_blk_pref17.csv", f"{ABR}/mt_rsdtdsp_blk/pref/mt_rsdtdsp_blk_pref17.csv.zip", "ABR 住居表示 街区 (fetch_abr.py)"),
    ("data/abr/mt_rsdtdsp_blk_pos_pref17.csv", f"{ABR}/mt_rsdtdsp_blk_pos/pref/mt_rsdtdsp_blk_pos_pref17.csv.zip", "ABR 住居表示 街区の代表点 (fetch_abr.py)"),
    ("data/abr/mt_parcel_city172014.csv", f"{ABR}/mt_parcel/city/mt_parcel_city172014.csv.zip", "ABR 地番 (fetch_abr.py)"),
    ("data/abr/mt_parcel_pos_city172014.csv", f"{ABR}/mt_parcel_pos/city/mt_parcel_pos_city172014.csv.zip", "ABR 地番の代表点 (fetch_abr.py)"),
    ("data/gsi_jukyo_17201.csv", "https://saigai.gsi.go.jp/jusho/download/data/17201.zip", "国土地理院 住居表示住所 (geo_peek.py で CSV にする)"),
    ("data/estat_17201/r2ka17201.shp", "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code=17201&coordSys=1&format=shape&downloadType=5&datum=2011",
     "e-Stat 国勢調査2020 小地域 (fetch_geo.py 17201)"),
    ("data/kanazawa_rules.csv", "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/20102.html",
     "金沢市の区域表 (fetch.py → kz_tables.py → kz_rules.py)"),
    ("out/kanazawa_schedules.json", "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/20102.html", "金沢市の地区別収集カレンダー (kz_calendar.py)"),
    ("out/kanazawa_town_parts.geojson", "", "区域 (split_blocks.py)"),
    ("out/kanazawa_land_border.geojson", "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_17_GML.zip",
     "隣の市町との境界 (国土数値情報 N03-2024 石川県の金沢市の区域の線のうち、海岸線を除く部分)"),
    ("out/kanazawa_town_alias.csv", "", "旧町名 (census_lineage.py ほか)"),
    ("out/kanazawa_town_alias_target.csv", "", "旧町名の後継 (census_lineage.py ほか)"),
    ("out/kanazawa_town_collisions.json", "https://data.address-br.digital.go.jp/mt_town_fullset/mt_town_fullset_all.csv.zip", "ほかの市区町村にもある町名 (build_town_collisions.py)"),
    ("out/onagawa_data.json", "https://www.town.onagawa.miyagi.jp/pdf/calendar/r08_chomin_calendar.pdf", "女川町のカレンダー・分別表 (build_onagawa.py。出典は onagawa_data.json の sources)"),
]

def _get(url):
    """robots.txt を守り、1ホストに 1秒に1回まで (polite.py)"""
    import polite
    try: return polite.get(url)
    except polite.RobotsDisallowed as e: sys.exit(f"{e}\n手で取るか、作り方の欄のスクリプトで作ってください")

def _csv_from_zip(b):
    """配布の zip (zip の中に zip があることもある) → [(名前, 中身)]"""
    out = []
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for n in z.namelist():
            data = z.read(n)
            if n.endswith(".zip"): out += _csv_from_zip(data)
            elif n.endswith(".csv"): out.append((os.path.basename(n), data))
    return out

def fetch_missing():
    for path, url, how in INPUTS:
        if os.path.exists(path): continue
        print("取り直す:", path, url or "(URL なし)", "/", how)
        if path.startswith("data/abr/"):
            os.makedirs("data/abr", exist_ok=True)
            for name, data in _csv_from_zip(_get(url)): open(os.path.join("data/abr", name), "wb").write(data)
        elif path == "data/gsi_jukyo_17201.csv":
            b = _get(url)
            open("data/gsi_jukyo_17201.zip", "wb").write(b)
            subprocess.run([sys.executable, "geo_peek.py"], check=True)
        elif path.startswith("data/estat_17201/"):
            os.makedirs("data/estat_17201", exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(_get(url))) as z: z.extractall("data/estat_17201")
        elif path == "data/kanazawa_rules.csv":
            os.makedirs("data", exist_ok=True)
            open("data/kanazawa_rules.html", "wb").write(_get(url))
            subprocess.run([sys.executable, "kz_tables.py"], check=True)
            subprocess.run([sys.executable, "kz_rules.py"], check=True)
        else:
            sys.exit(f"{path} が無い。{how} で作る")

def check_inputs():
    missing = [(p, u, h) for p, u, h in INPUTS if not os.path.exists(p)]
    for p, u, h in missing: print("無い:", p, "←", u or "(URL なし)", "/", h)
    if missing: sys.exit("--fetch-missing で取り直すか、作り方の欄のスクリプトを動かしてください")

# ---------------------------------------------------------------- 共通
def write_pack(lg, name, obj):
    """JSON → gzip。鍵は <lg>/<名前>.<元の JSON の sha256 の頭12桁>.json.gz。古い同じ名前のパックは消す"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    key = f"{lg}/{name}.{sha[:12]}.json.gz"
    d = os.path.join(WORKER, "packs", lg)
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        if f.startswith(name + ".") and f.endswith(".json.gz") and f != os.path.basename(key): os.remove(os.path.join(d, f))
    open(os.path.join(WORKER, "packs", key), "wb").write(gz)
    print(f"  {key}: {len(raw):,} B (gzip {len(gz):,} B)")
    return {"key": key, "sha256": sha, "bytes": len(raw), "gzip_bytes": len(gz)}

def write_bin_pack(lg, name, raw, quiet=False):
    """バイト列 (build_loc.bin_pack) → gzip。鍵は <lg>/<名前>.<gzip する前の sha256 の頭12桁>.bin.gz"""
    sha = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    key = f"{lg}/{name}.{sha[:12]}.bin.gz"
    path = os.path.join(WORKER, "packs", key)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    base = os.path.basename(name)
    for f in os.listdir(d):
        if f.startswith(base + ".") and f.endswith(".bin.gz") and f != os.path.basename(key): os.remove(os.path.join(d, f))
    open(path, "wb").write(gz)
    if not quiet: print(f"  {key}: {len(raw):,} B (gzip {len(gz):,} B)")
    return {"key": key, "sha256": sha, "bytes": len(raw), "gzip_bytes": len(gz)}

def kanazawa_loc(k):
    """緯度経度の判定のパック (loc) とタイル (build_loc.py)"""
    import resolve, build_loc
    print("金沢市: 緯度経度の判定のタイルを作る")
    lh, la, tiles = build_loc.build(k, resolve)
    loc = write_bin_pack("172014", "loc", build_loc.bin_pack(lh, la))
    tdir = os.path.join(WORKER, "packs", "172014", "loc")
    os.makedirs(tdir, exist_ok=True)
    files, keep, nb, ng, big = {}, set(), 0, 0, []
    for (tx, ty), (h, a) in sorted(tiles.items()):
        info = write_bin_pack("172014", f"loc/{tx}_{ty}", build_loc.bin_pack(h, a), quiet=True)
        files[f"{tx}_{ty}"] = info["sha256"]
        keep.add(os.path.basename(info["key"]))
        nb += info["bytes"]; ng += info["gzip_bytes"]; big.append((info["bytes"], info["gzip_bytes"], info["key"]))
    for f in os.listdir(tdir):
        if f not in keep: os.remove(os.path.join(tdir, f))
    big.sort()
    print(f"  タイル {len(files)} 枚 ({build_loc.TILE_M:.0f}m 四方 + 余白 {build_loc.HALO_M:.0f}m): 合計 {nb:,} B (gzip {ng:,} B)、"
          f"中央 {big[len(big) // 2][0]:,} B (gzip {big[len(big) // 2][1]:,} B)、最大 {big[-1][0]:,} B (gzip {big[-1][1]:,} B) {big[-1][2]}")
    return loc, {"prefix": "172014/loc/", "size_m": build_loc.TILE_M, "halo_m": build_loc.HALO_M, "count": len(files), "bytes": nb, "gzip_bytes": ng,
                 "files": files}

def write_ts(path, obj, comment):
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    open(path, "w", encoding="utf-8", newline="\n").write(f"// {comment}\n// nationwide/packs/build_packs.py が作る。手で直さない\nexport default {s};\n")

def mcp_meta():
    """mcp_server.py からツールの定義と文面を抜き出す (import すると読み込みと監視のスレッドが動くので、代入文だけを評価する)"""
    want = ("PROTOCOL", "SUPPORTED", "SERVER", "RULES", "INSTRUCTIONS", "DATE", "DAYS", "DETAIL", "TOOLS", "COVERAGE", "OTHER_TOWN_KEYS")
    ns = {}
    for node in ast.parse(open("mcp_server.py", encoding="utf-8").read()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in want:
            exec(compile(ast.Module([node], []), "mcp_server.py", "exec"), {}, ns)
    meta = {k: ns[k] for k in want if k not in ("DATE", "DAYS", "DETAIL")}
    def no_int_float(o):          # JavaScript では 2.0 と 2 を見分けられないので、整数の値の float は無いことを確かめる
        if isinstance(o, float): assert not o.is_integer(), o
        elif isinstance(o, dict): [no_int_float(v) for v in o.values()]
        elif isinstance(o, (list, tuple)): [no_int_float(v) for v in o]
    no_int_float(meta)
    return meta

# ---------------------------------------------------------------- 金沢市
def kanazawa():
    import geopandas as gpd
    import resolve, kzaddress
    from kzrules import table_town, match_row, match_parcel
    print("金沢市: Python の索引を作る (約5秒)")
    k = resolve.Kanazawa()
    ax = kzaddress.AddressIndex(k)

    # ---- core: 地区ごとの収集日・出典
    districts = {}
    for d, ss in k.sched.items():
        lo, hi = k.calendar_range(d)
        last, first = k._year_end(d)
        scheds = []
        for s in ss:
            occ = sorted(k.occ[(d, s["category"])])
            rule = re.sub(r"\s+", "", s["rule_text"])
            exc = []
            for x in occ:
                mm = [m for m in s.get("rule_mismatches", []) if m["month"] == f"{x.year}-{x.month:02d}"]
                if mm and x.day not in mm[0]["rule"]: exc.append((x - lo).days)
            scheds.append({"category": s["category"], "official_label": s["official_label"], "rule": rule,
                           "rule_text": rule.split("年末")[0] if s["category"] == "burnable" else rule,
                           "dates": [(x - lo).days for x in occ], "exceptions": exc})
        districts[d] = {"valid_from": str(lo), "valid_to": str(hi), "year_end": [str(last) if last else None, str(first) if first else None],
                        "source": k.sources(d)[1], "schedules": scheds}
    c = gpd.read_file("data/estat_17201/r2ka17201.shp").to_crs(4326).total_bounds
    m = 0.005                       # 区域の縁の曲がり分 (約500m) の余白。この外は金沢市の町の区域の外
    core = {"format": "gomisute-pack/1", "kind": "core", "engine": "district_table", "lg_code": resolve.LG, "name": resolve.MUNI, "short": "金沢市",
            "data_version": k.data_version, "coverage_calendar": "2026-04-01〜2027-03-31",
            "bbox": [round(c[0] - m, 4), round(c[1] - m, 4), round(c[2] + m, 4), round(c[3] + m, 4)],
            "rules_source": resolve.RULES_SOURCE, "contact": resolve.CONTACT, "not_covered": resolve.NOT_COVERED,
            "put_out_rules": resolve.PUT_OUT_RULES, "put_out_by": resolve.PUT_OUT_BY, "holiday_policy": resolve.HOLIDAY_POLICY,
            "districts": districts}
    verify_days(k, core)

    # ---- addr: 住所の索引と evaluate() の結果
    rid = {id(r): i for i, r in enumerate(k.rules)}
    sp = lambda x: re.sub(r"\s+", " ", x).strip()
    rows = [[r["town"], sp(r["raw_blk"]), sp(r["raw_ban"]), r["district"], r["note"], r["kind"], r["blk_from"], r["blk_to"], r["ban_from"], r["ban_to"]]
            for r in k.rules]
    by_town = {t: [rid[id(r)] for r in rs] for t, rs in k.by_town.items() if rs}
    via_old = {t: [[a["alias_name"], a["confidence"], rid[id(r)]] for a, r in v] for t, v in k.via_old.items() if v}
    towns = collections.defaultdict(dict)
    rs_of = lambda t: k.by_town.get(table_town(t, k.by_town), []) if table_town(t, k.by_town) else []
    ids = lambda rows_: [rid[id(r)] for r in rows_]

    def runs(vals):
        out = []
        for v in vals:
            if out and out[-1] == v: out[-2] += 1
            else: out += [1, v]
        return out

    def deltas(nums):
        return [b - a for a, b in zip([0] + nums[:-1], nums)]

    jb = collections.defaultdict(dict)
    for (t, blk), nums in ax.jukyo_pts.items():
        if nums: jb[t][blk] = sorted(nums)
    for t, blocks in jb.items():
        rs = rs_of(t)
        j = []
        for blk in sorted(blocks):
            nums = blocks[blk]
            vals = []
            for n in nums:
                row = match_row(rs, blk, n) if rs else None
                vals.append(rid[id(row)] if row else -1)
            j.append([blk, deltas(nums), runs(vals)])
        towns[t]["j"] = j
    # 街区: 住居表示住所のある街区と ABR の街区。evaluate(block) の結果をそのまま (文は種類の番号にして、Worker で同じ文を作る)
    blocks = collections.defaultdict(set)
    for (t, blk), nums in ax.jukyo_pts.items():
        if nums: blocks[t].add(blk)
    for t, blk in ax.abr_blocks: blocks[t].add(blk)
    n_eval = 0
    for t, bs in blocks.items():
        outcomes, b = [], []
        for blk in sorted(bs):
            ds, rws, desc, ex = ax.evaluate({"kind": "block", "town": t, "blk": blk})
            variant = block_variant(t, blk, desc)
            o = [ex, sorted(ds) if ds else None, ids(rws), variant]
            if o not in outcomes: outcomes.append(o)
            b += [blk, outcomes.index(o)]
            n_eval += 1
        towns[t]["b"], towns[t]["bo"] = b, outcomes
    # 地番: 町 → 小字ごとの番号と、kzrules.match_parcel の行
    n_parcel = 0
    for t, pairs in ax.parcels.items():
        if not pairs: continue
        rs = rs_of(t)
        bykz = collections.defaultdict(list)
        for kz, n in pairs: bykz[kz].append(n)
        p = []
        for kz in sorted(bykz):
            nums = sorted(bykz[kz])
            vals = []
            for n in nums:
                row = match_parcel(rs, kz, n) if rs else None
                vals.append(rid[id(row)] if row else -1)
            p.append([kz, deltas(nums), runs(vals)])
            n_parcel += len(nums)
        towns[t]["p"] = p
    addr = {"format": "gomisute-pack/1", "kind": "addr", "lg_code": resolve.LG, "data_version": k.data_version,
            "known": sorted(ax.known), "block_towns": sorted(ax.block_towns), "collisions": ax.collisions,
            "rows": rows, "by_town": by_town, "via_old": via_old, "towns": dict(sorted(towns.items()))}
    print(f"  住居表示住所 {sum(len(v) for v in ax.jukyo_pts.values()):,}・街区 {n_eval:,}・地番 {n_parcel:,}・町 {len(towns)}・区域表の行 {len(rows):,}")
    return core, addr, k

def block_variant(t, blk, desc):
    """evaluate(block) の文 → 種類の番号 (Worker は同じ文を番号から作る)。番号と文が合うことをここで確かめる"""
    texts = [f"{t} {blk}番 (区域表に {t} の行が無い)", f"{t} {blk}番 (街区)",
             f"{t} {blk}番 (街区。区域表の行では言い切れない)", f"{t} {blk}番 (街区。住居番号が無いので街区の中の行をすべて見る)"]
    assert desc in texts, desc
    return texts.index(desc)

def verify_days(k, core):
    """パックから組み立て直した日ごとの答え・次の収集日が、resolve.Kanazawa と全部同じか (Worker の組み立てと同じやり方で)"""
    WDJ = "月火水木金土日"
    n = 0
    for d, dd in core["districts"].items():
        lo, hi = dt.date.fromisoformat(dd["valid_from"]), dt.date.fromisoformat(dd["valid_to"])
        last, first = [dt.date.fromisoformat(x) if x else None for x in dd["year_end"]]
        occ = {s["category"]: {lo + dt.timedelta(days=o) for o in s["dates"]} for s in dd["schedules"]}
        exc = {s["category"]: {lo + dt.timedelta(days=o) for o in s["exceptions"]} for s in dd["schedules"]}
        start = lo - dt.timedelta(days=3)
        want = k.days(d, start, (hi - lo).days + 7)
        for i, w in enumerate(want):
            x = start + dt.timedelta(days=i)
            got = {"date": str(x), "weekday": WDJ[x.weekday()], "in_calendar_range": lo <= x <= hi}
            if not got["in_calendar_range"]:
                got["collections"] = None
                got["note"] = f"金沢市の{lo.year}年度カレンダー ({lo}〜{hi}) の範囲外で分かりません (「収集なし」という意味ではありません)"
            else:
                cols = []
                for s in dd["schedules"]:
                    if x in occ[s["category"]]:
                        moved = x in exc[s["category"]]
                        cols.append({"category": s["category"], "official_label": s["official_label"], "rule_text": s["rule_text"], "is_exception": moved,
                                     **({"exception_note": f"通常の「{s['rule']}」とは違う日 (カレンダーの表の日付。違いがあるのは2026年12月・2027年1月だけ)"} if moved else {})})
                got["collections"] = cols
                if last and first and last < x < first:
                    got["note"] = f"年末年始: 燃やすごみは {resolve_fmt(last)} まで、{resolve_fmt(first)} から"
            assert got == w, (d, x, got, w)
            n += 1
    print(f"  収集日をパックから組み立て直して {n:,} 日分を確かめた (全部同じ)")

def resolve_fmt(d):
    return f"{d.year}年{d.month}月{d.day}日({'月火水木金土日'[d.weekday()]})"

# ---------------------------------------------------------------- 女川町
def onagawa_pack():
    import onagawa
    o = onagawa.Onagawa()
    d = o.d
    bur = [{k: g[k] for k in ("color", "color_short", "weekdays_short", "weekdays_text", "gyoseiku", "dates")} for g in d["burnable"]["groups"]]
    rrows = [{"label": r["label"], "gyoseiku": r["gyoseiku"], "by_column": r["by_column"]} for r in d["resource"]["rows"]]
    gk = [[key, v["name"], d["burnable"]["groups"].index(v["burnable"]) if "burnable" in v else -1,
           d["resource"]["rows"].index(v["resource"]) if "resource" in v else -1] for key, v in o.gk.items()]
    places = [[key, v["gyoseiku"], v["certainty"], v["basis"]] for key, v in o.places.items()]
    unknown_new = [key for key in o.places if key in o.unknown_new]
    no_dates = {key: d["burnable"]["groups"].index(g) for key, g in o.no_dates.items()}
    rules = []
    for r in d.get("sorting_rules", []):
        item = r["item"]
        core = onagawa._qnorm(re.sub(r"[（(].*?[）)]", "", item))
        cm, cb = onagawa.MATERIAL.search(core), onagawa.CONTAINER.search(core)
        im = cm or onagawa.MATERIAL.search("".join(re.findall(r"[（(](.*?)[）)]", item)))
        rules.append({**{k: r.get(k) for k in ("item", "destination", "how", "valid_from", "valid_to", "source")},
                      "_names": [list(x) for x in onagawa.item_names(item)], "_core": core, "_core_mat": cm[0] if cm else None,
                      "_item_mat": im[0] if im else None, "_core_box": bool(cb), "_how_sentences": re.split(r"(?<=。)", r.get("how") or "")})
    return {"format": "gomisute-pack/1", "kind": "core", "engine": "gyoseiku", "lg_code": onagawa.LG, "name": onagawa.MUNI, "short": "女川町",
            "data_version": o.data_version, "fiscal_year": d["fiscal_year"], "valid_from": d["valid_from"], "valid_to": d["valid_to"],
            "coverage_calendar": "2026-04-01〜2027-03-31",
            **{k: d.get(k) for k in ("calendar_title", "sorting_source_url", "put_out_by", "put_out_texts", "not_covered", "contact", "holiday_policy", "sources")},
            "burnable": {"label": d["burnable"]["label"], "groups": bur},
            "resource": {"columns": [{"label": c["label"]} for c in d["resource"]["columns"]], "rows": rrows},
            "gk": gk, "places": places, "unknown_new": unknown_new, "no_dates": no_dates,
            "label_notes": {lab: [list(x) for x in v] for lab, v in o.label_notes.items()},
            "sorting_rules": rules}

# ---------------------------------------------------------------- 本体
def main():
    if "--fetch-missing" in sys.argv: fetch_missing()
    check_inputs()
    kz_core, kz_addr, k = kanazawa()
    print("金沢市のパック:")
    kz = {"core": write_pack("172014", "core", kz_core), "addr": write_pack("172014", "addr", kz_addr)}
    kz["loc"], kz_tiles = kanazawa_loc(k)
    ona = onagawa_pack()
    print("女川町のパック:")
    on = {"core": write_pack("045811", "core", ona)}
    manifest = {"format": "gomisute-manifest/1", "builder": "nationwide/packs/build_packs.py",
                "municipalities": {
                    "172014": {"name": kz_core["name"], "short": kz_core["short"], "engine": "district_table", "data_version": kz_core["data_version"],
                               "status_key": "status:172014", "bbox": kz_core["bbox"], "packs": kz, "tiles": {"loc": kz_tiles}},
                    "045811": {"name": ona["name"], "short": ona["short"], "engine": "gyoseiku", "data_version": ona["data_version"],
                               "status_key": "status:045811", "packs": on}},
                "inputs": [{"path": p, "url": u, "how": h} for p, u, h in INPUTS]}
    write_ts(os.path.join(WORKER, "src", "generated", "manifest.ts"), manifest, "自治体 → R2 のパックの鍵 (内容のハッシュつき)・状態の KV の鍵")
    write_ts(os.path.join(WORKER, "src", "generated", "mcp_meta.ts"), mcp_meta(), "mcp_server.py のツールの定義・RULES・対応する自治体 (mcp_server.py から抜き出したもの)")
    print("書いた: worker/src/generated/manifest.ts, worker/src/generated/mcp_meta.ts")

if __name__ == "__main__":
    main()
