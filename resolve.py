#!/usr/bin/env python3
"""緯度経度 + 日付 → 収集地区 → その日に出せるごみ (金沢市。参照実装 v5)

  python resolve.py 36.481532 136.703003 [2026-10-20] [--days 2] [--accuracy 30 [--confidence 0.95]] [--offline]
  python resolve.py --address 金沢市笠舞2丁目29番5号 [2026-10-20] [--days 2]

目的: LLM が「今日ここで何のごみを出せるか」を答えるときの、唯一確かな情報源にする。
地区の決め方:
  - 公式の根拠 (区域表の行) から出てくる地区が1つだけで、しかもその地区がこの場所を直接指す根拠 (最寄りの住所・地番の行、区域、町の行)
    を持つときに限り「確定 (ok)」。2つ以上なら候補を全部返す (ambiguous)
  - 候補にする根拠: 最寄りの住居表示住所の行 / 最寄りの地番 (ABR 地番代表点、住居表示の無い地域) の行 / 区域表から作った区域 /
    25m 以内の別の住所・80m 以内の別の地番 / 10m 以内の別の地区の区域 / 住居表示住所が1つも無い街区 (ABR の街区代表点、120m 以内) /
    区域が無い場所では国勢調査の町の行。地区が分かれる町では、最寄りの地番だけでは確定しない (小字ごと位置が無いことがある)
  - この場所の町 (最寄りの住所・地番の町、国勢調査の町。住所は 50m 以内) や 25m 以内の住所・地番の町が区域表に無ければ確定しない
    (unresolved)。理由は decision_blocked_by に入れ、区域表の行による候補は official のまま返す
  - 位置の誤差は 10m 以内を前提にする。accuracy_m (端末が返す精度) と accuracy_confidence (その半径に入る確率。既定 0.68 = Android。
    Android の Chrome もそのまま渡す) を渡すと、本当の位置が 99% の確率で入る半径を、すべての根拠の距離に足して探す
  - 隣の市町との境界 (国土数値情報 N03、海岸線を除く) のそばや、決め手の行に注記がある所 (主計町) でも確定しない
日付: 日ごとに in_calendar_range を付け、範囲外を「収集なし」と言わない。出す時刻は市の「ごみの出し方のルール」(午前8時30分まで)。
  分別方法・集積所の場所などデータに無いことは not_covered に明示。
評価: python eval_resolve.py (区域も作り直す完全ホールドアウト・街区ホールドアウト・地番ホールドアウト・GPS のずれ)
"""
import collections, copy, csv, datetime as dt, hashlib, json, math, numbers, re, sys, unicodedata, urllib.request
import numpy as np, pandas as pd, geopandas as gpd, shapely
from shapely.geometry import Point
from dateutil.rrule import rrulestr
from townnorm import norm_town, kan2num
from kzrules import load_rules, load_jukyo, match_row, load_parcels, table_town, match_parcel, mark_stale_rows

JST = dt.timezone(dt.timedelta(hours=9))
LG, MUNI = "172014", "石川県金沢市"
WDJ = "月火水木金土日"
ADDR_M = 25        # 住所を根拠に使う距離。区域表に無い町の住所・地番がこれより近ければ確定しない
ADDR_SURE_M = 10   # 最寄りの住所としてポリゴンと突き合わせる距離
ADDR_FAR_M = 50    # 最寄りの住居表示住所がこれより近く、その町が区域表に無ければ確定しない (住所の間隔が 25m を超える所)
ACC_CONF = 0.68    # accuracy_m の確率の既定。Android の Location.getAccuracy は 68%。Android の Chrome もこの値をそのまま coords.accuracy に渡す
MAX_SEARCH_M = 300 # 99% の半径がこれより大きい (精度が低すぎる) ときは地区を絞らない
AREA_ALONE_OK = 1  # 近くに住所 (ADDR_M) も地番 (PARCEL_M) も無いとき、区域・町の行だけで確定してよいか。
                   # 0 にすると小字ホールドアウトの誤り 47→28 だが確定率 86%→56% (誤りの残りは隣の町の地番も区域と一致する所) なので 1
PARCEL_M = 30      # 最寄りの地番代表点 (ABR) を「この場所の地番」として使う距離
PARCEL_NEAR_M = 80 # 別の地区の地番がこれより近ければ候補に入れる (地番ホールドアウトのシード 7 で 30m: 誤り12、50m: 2、80m: 0)
NEAR_M = 10        # 他の地区の区域がこれより近ければ候補に入れる (完全ホールドアウトで 10〜30m の誤りは同数、確定率が最も高い 10m)
BLIND_M = 120      # 住居表示住所の無い区域表の行 (ABR の街区代表点など) がこれより近ければ候補に入れる (町を問わない)
RULES_SOURCE = {"title": "2026年度地区別収集カレンダー(トップページ) — 町名一覧表 (区域表)", "publisher": "金沢市 ごみ減量推進課",
                "url": "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/20102.html"}
CONTACT = {"name": "金沢市 ごみ減量推進課", "phone": "076-220-2302", "source_url": RULES_SOURCE["url"]}
NOT_COVERED = ["品目ごとの分別方法・出し方 (袋・容器など)", "ごみ集積所の場所"]
PUT_OUT_RULES = {"title": "ごみの出し方のルール", "publisher": "金沢市 ごみ減量推進課", "url": "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/3/7608.html",
                 "retrieved_at": "2026-10-17T13:12:00+09:00", "page_sha256": "1b65095f46535df092e642abb13f6fb9dfa87f5029f0edf93edf277d4b082292"}
PUT_OUT_BY = {"deadline": "収集日の午前8時30分まで", "official_text": "収集日の午前8時30分までに出してください。", "source": PUT_OUT_RULES}
HOLIDAY_POLICY = ("市の「ごみの出し方のルール」に「収集は年末年始を除く月曜日～金曜日（祝日含む）に行います」とあり、"
                  "祝日も表のとおり収集する (例: 浅野町地区の資源は 2026-11-03 文化の日に収集)。"
                  "2026年12月・2027年1月は、燃やすごみ以外の品目も第N曜日の規則と違う日がある (例: 2027-01-29) ので、"
                  "その2か月は days・next_after_date の日付だけを使う。"
                  "変更があるのは年末年始だけで、年末は12月28日または29日まで、年始は1月4日または5日から (地区による)")
GPS2CITY = "https://gps2city.firemio.workers.dev/location"
UA = {"Content-Type": "application/json", "User-Agent": "gomisute-gps/0.5 (+https://github.com/firemio/gomisute-gps)"}
SRC_JUKYO = "国土地理院 電子国土基本図 (地名情報) 住居表示住所"
SRC_PARCEL = "デジタル庁 アドレス・ベース・レジストリ 地番マスター (代表点)"
BASIS_JA = {"given_address_row": "伝えられた住所が区域表の行に当たる", "address_row": "最寄りの住所が区域表の行に当たる", "parcel_row": "最寄りの地番が区域表の行に当たる", "zone_polygon": "区域表から作った区域",
            "near_address": "近くの住所の区域表の行", "near_parcel": "近くの地番の区域表の行",
            "near_boundary": "近くに別の地区の区域", "unindexed_row": "住居表示住所の無い区域表の行 (位置は ABR の街区代表点など)",
            "town_row": "区域表のこの町の行 (一部の場合あり)", "census_town_row": "この地点を含む国勢調査の町の行 (一部の場合あり)",
            "town_row_prefix": "よく似た町名の区域表の行 (町全体) からの推定", "conditional_similar_row": "よく似た町名の条件付きの行",
            "inferred_lineage": "旧町名の系譜からの推定"}
DECIDING = {"given_address_row", "address_row", "parcel_row", "zone_polygon", "town_row", "census_town_row", "town_row_prefix"}   # この場所を直接指す根拠
RANK = {"given_address_row": 110, "address_row": 100, "zone_polygon": 90, "parcel_row": 85, "census_town_row": 75, "town_row": 70, "town_row_prefix": 60,
        "unindexed_row": 55, "near_address": 50, "near_parcel": 45, "near_boundary": 40, "conditional_similar_row": 35, "inferred_lineage": 30}
SHOW_DETAIL = {"near_address", "near_parcel", "unindexed_row", "conditional_similar_row", "inferred_lineage"}

STATUS_MAX_AGE_DAYS = 7     # 変更監視の確認がこれより古ければ「確かめていない」とみなす

def load_source_status(path="out/source_status.json"):
    """monitor.py が書いた公式ページの変更監視の結果 (無ければ None)。呼ぶたびに読み直す (動いているサーバーにも変更が届く)"""
    try: return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError): return None

def freshness(status, urls):
    """答えの元にした公式ページが、データを作ったときから変わっていないか。
    source_changed (変わった・消えた) / stale (確認が古い) / partly_unchecked (取れなかった・監視していないページがある) / unchanged /
    unchecked (監視の結果が無い・読めない)"""
    if not status or not isinstance(status.get("sources"), list) or not status.get("checked_at"):
        return {"status": "unchecked", "note": "公式ページの変更監視 (monitor.py) の結果がありません"}
    by = {x["url"]: x for x in status["sources"]}
    want = list(dict.fromkeys(u.split("#")[0] for u in urls))
    used = [by[u] for u in want if u in by]
    untracked = [u for u in want if u not in by]
    gone = lambda x: x["status"].startswith("error") and any(c in x["status"] for c in ("404", "410"))
    changed = [{"url": x["url"], "title": x["title"], "recorded_at": x["recorded_at"], **({"gone": True} if gone(x) else {})}
               for x in used if x["status"] == "changed" or gone(x)]
    errors = [x["url"] for x in used if x["status"].startswith("error") and not gone(x)]
    if changed: return {"status": "source_changed", "checked_at": status["checked_at"], "changed": changed, "simulated": status.get("simulated", False)}
    age = (dt.datetime.now(JST) - dt.datetime.fromisoformat(status["checked_at"])).total_seconds() / 86400
    if age > STATUS_MAX_AGE_DAYS:
        return {"status": "stale", "checked_at": status["checked_at"], "note": f"公式ページの確認が {int(age)} 日前で古い (monitor.py を毎日動かす前提)"}
    if not used:
        return {"status": "unchecked", "checked_at": status["checked_at"], "note": "この答えの元にした公式ページは、変更監視の対象に入っていません", "untracked": untracked}
    return {"status": "unchanged" if not (errors or untracked) else "partly_unchecked", "checked_at": status["checked_at"], "sources_checked": len(used),
            **({"errors": errors} if errors else {}), **({"untracked": untracked} if untracked else {})}

def apply_freshness(res, fr):
    """元のページが変わっていたら、確かな答えとして返さない (作り直して確かめるまで)。候補で返す答え、もともと決められない答えの日付も返さない。
    確認が古い・取れなかったページがある・監視の結果が無いときは answerable を false にする"""
    res["freshness"] = fr
    if fr["status"] == "source_changed" and res.get("status") in ("ok", "ambiguous", "unresolved"):
        loc = res.setdefault("location", {})
        if res.get("status") == "ok":
            loc["collection_district_before_change"] = loc.get("collection_district")
            loc["collection_district"] = None
        before = loc.get("decision_blocked_by")
        res["status"], res["answerable"] = "unresolved", False
        loc["decision_blocked_by"] = {"reason": "source_changed", "town": None, "changed": fr["changed"], "checked_at": fr["checked_at"],
                                      **({"also": before} if before else {})}
        loc["district_confidence"] = 0.0
        for k in ("days", "next_after_date", "consensus", "groups"): res.pop(k, None)
        for c in res.get("candidates", []): c.pop("days", None)
        if "holiday_policy" in res: res["holiday_policy"] = "元の公式資料が変わったため、日付は返しません"
    elif fr["status"] in ("stale", "partly_unchecked", "unchecked"):
        res["answerable"] = False
    return res

def search_radius(accuracy_m, confidence=ACC_CONF, target=0.99):
    """端末の精度 (確率 confidence の半径) → 本当の位置が確率 target で入る半径。誤差を2次元正規分布とみなす
    (半径 r に入る確率 1-exp(-r²/2σ²))。68% → 99% は約2.0倍、95% → 99% は約1.24倍"""
    if not accuracy_m: return 0.0
    return float(accuracy_m) * math.sqrt(math.log(1 - target) / math.log(1 - confidence))

def fmt(d, year=False): return (f"{d.year}年" if year else "") + f"{d.month}月{d.day}日({WDJ[d.weekday()]})"

class Kanazawa:
    def __init__(self, parts_path="out/kanazawa_town_parts.geojson", exclude_addresses=None, exclude_parcels=None):
        """exclude_addresses / exclude_parcels: 索引から外す住居表示点・地番の行番号 (評価用。区域は parts_path で差し替える)"""
        self.parts = gpd.read_file(parts_path).to_crs(6675)
        c = gpd.read_file("data/estat_17201/r2ka17201.shp").to_crs(6675)
        c["ntown"] = c.S_NAME.map(norm_town)
        self.towns = c.dissolve(by=["ntown", "HCODE"], as_index=False)[["ntown", "HCODE", "geometry"]]
        self.city = self.towns.union_all()                       # 水面 (HCODE 8154, 金沢港など) も市域に含める
        self.land_border = gpd.read_file("out/kanazawa_land_border.geojson").to_crs(6675).geometry.iloc[0]   # 隣の市町との境界 (海岸線を除く)
        self.rules, self.by_town = load_rules()
        self.uniform = {t: (rs[0]["district"] if len({r["district"] for r in rs}) == 1 else None) for t, rs in self.by_town.items()}
        j = load_jukyo()
        if exclude_addresses is not None: j = j.drop(index=list(exclude_addresses))
        self.jukyo = j.reset_index(drop=True)
        self.jlabel = [f"{t}{b}番{n}号" for t, b, n in zip(self.jukyo.town, self.jukyo.blk, self.jukyo.num)]
        self.jgeom = np.array(gpd.GeoSeries(gpd.points_from_xy(self.jukyo.lon.astype(float), self.jukyo.lat.astype(float)), crs=6668).to_crs(6675).values)
        self.jtree = shapely.STRtree(self.jgeom)
        self._row_cache = {}
        self._build_parcels(exclude_parcels)
        self._build_unindexed()
        self._build_enclaves()
        alias = {r["alias_id"]: r for r in csv.DictReader(open("out/kanazawa_town_alias.csv", encoding="utf-8"))}
        self.via_old = collections.defaultdict(list)          # 現行町 → 旧町名の行 (系譜調査で「ごみの行が当てはまる」とした後継だけ)
        for t in csv.DictReader(open("out/kanazawa_town_alias_target.csv", encoding="utf-8")):
            if t["gomi"] != "True": continue
            a = alias[t["alias_id"]]
            for r in self.by_town.get(norm_town(a["alias_name"]), []):
                self.via_old[norm_town(t["name"])].append((a, r))
        for k in self.via_old: self.via_old[k].sort(key=lambda x: -float(x[0]["confidence"]))
        raw = open("out/kanazawa_schedules.json", "rb").read()
        data = json.loads(raw)
        self.sched = collections.defaultdict(list)
        for s in data["schedules"]: self.sched[s["district"]].append(s)
        self.occ = {(s["district"], s["category"]): self._occurrences(s) for s in data["schedules"]}
        self.data_version = f"kanazawa-fy{data['fiscal_year']}-{hashlib.sha256(raw).hexdigest()[:12]}"

    def _build_parcels(self, exclude):
        """ABR 地番代表点 (住居表示の無い筆) → 区域表の行。住居表示の無い地域の2つ目の索引"""
        mark_stale_rows(self.by_town)
        pc = load_parcels()
        if exclude is not None: pc = pc.drop(index=list(exclude))
        # 区域表に無く、ABR でも位置つきの筆が5未満の町名は ABR の誤記とみなす (中村町８丁目: 御影町の中に2筆)
        nit = pc[[table_town(t, self.by_town) is None for t in pc.ntown]].ntown.value_counts()
        pc = pc[~pc.ntown.isin(nit[nit < 5].index)]
        self.parcels = pc = pc.reset_index(drop=True)
        self.plabel = [f"{t}{k}{n1}{('-' + n2) if n2 else ''}番地" for t, k, n1, n2 in zip(pc.town, pc.koaza, pc.prc_num1, pc.prc_num2)]
        self.ptownname = list(self.parcels.town)
        self.pgeom = np.array(gpd.GeoSeries(gpd.points_from_xy(self.parcels.rep_lon.astype(float), self.parcels.rep_lat.astype(float)), crs=6668).to_crs(6675).values)
        self.ptree = shapely.STRtree(self.pgeom)
        cache, self.prow = {}, []
        for t, k, n in zip(self.parcels.ntown, self.parcels.koaza, self.parcels.num1):
            key = (t, k, None if n != n else int(n))
            if key not in cache:
                tt = table_town(t, self.by_town)
                cache[key] = (match_parcel(self.by_town[tt], k, key[2]) if tt else None, tt)
            self.prow.append(cache[key])                     # (区域表の行 or None, 区域表の町 or None)

    def _build_unindexed(self):
        """住居表示住所が1つも当たらない区域表の行 (街区つき)。位置は ABR 住居表示 街区代表点 + 同じ街区の他の住所。
        どれも無ければ町全体の候補にする。位置のある行は町を問わず1つの索引で引く"""
        hits = collections.Counter()
        for i in range(len(self.jukyo)):
            r, _ = self._addr_district(i)
            if r is not None: hits[id(r)] += 1
        b = pd.read_csv("data/abr/mt_rsdtdsp_blk_pref17.csv", dtype=str).fillna("")
        bp = pd.read_csv("data/abr/mt_rsdtdsp_blk_pos_pref17.csv", dtype=str).fillna("")
        m = bp[bp.lg_code == LG].merge(b[b.lg_code == LG][["machiaza_id", "blk_id", "oaza_cho", "chome", "blk_num"]], on=["machiaza_id", "blk_id"])
        m = m[m.rep_lon != ""]
        m["ntown"] = (m.oaza_cho + m.chome).map(norm_town); m["blk"] = pd.to_numeric(m.blk_num, errors="coerce")
        xy = gpd.GeoSeries(gpd.points_from_xy(m.rep_lon.astype(float), m.rep_lat.astype(float)), crs=4612).to_crs(6675)
        abr = collections.defaultdict(list)
        for t, bl, g in zip(m.ntown, m.blk, xy): abr[(t, bl)].append(g)
        jb = collections.defaultdict(list)
        for i, (t, bl) in enumerate(zip(self.jukyo.ntown, self.jukyo.blk_i)): jb[(t, bl)].append(i)
        self.unindexed = collections.defaultdict(list)       # 町 → 位置の無い行
        ui_pts, self.ui_rows = [], []                         # 位置 → (区域表の行, 説明)
        for t, rs in self.by_town.items():
            if self.uniform.get(t):
                # 町全体が1地区: 住居表示住所の無い街区の代表点を、その町の位置として使う (国勢調査の境界と住居表示の町境がずれる所)
                for (t2, bl), gs in abr.items():
                    if t2 != t or jb.get((t, bl)): continue
                    for g in gs: ui_pts.append(g); self.ui_rows.append((rs[0], f"{rs[0]['town']} {int(bl) if bl == bl else '?'}番の街区 (住居表示住所なし、ABR 街区代表点)"))
                continue
            for r in rs:
                if r["kind"] not in ("block", "banchi") or r["blk_from"] is None: continue
                blocks = range(r["blk_from"], min(r["blk_to"], r["blk_from"] + 200) + 1)
                if r["ban_from"] is None:
                    # 街区の行: 行の中の「住所が1つも無い街区」ごとに、ABR の街区代表点で位置を出す
                    found = False
                    for bl in blocks:
                        if jb.get((t, bl)) or not abr.get((t, bl)): continue
                        for g in abr[(t, bl)]: ui_pts.append(g); self.ui_rows.append((r, f"区域表の行 {r['town']} 街区{r['raw_blk']} の {bl}番 (住居表示住所なし、ABR 街区代表点)"))
                        found = True
                    if not hits[id(r)] and not found: self.unindexed[t].append(r)
                elif not hits[id(r)]:
                    # 番号で分けた行 (1軒だけの例外など) で、当たる住所が無い: 街区の代表点と同じ街区の住所を位置にする
                    pts = [g for bl in blocks for g in abr.get((t, bl), []) + [self.jgeom[i] for i in jb.get((t, bl), [])]]
                    for g in pts: ui_pts.append(g); self.ui_rows.append((r, f"区域表の行 {r['town']} 街区{r['raw_blk']} {r['raw_ban'].strip()} (当たる住居表示住所なし)"))
                    if not pts: self.unindexed[t].append(r)
        self.ui_geom = np.array(ui_pts)
        self.ui_tree = shapely.STRtree(self.ui_geom)

    def _build_enclaves(self):
        """区域の中に別の町の住居表示住所がある (飛び地・境界のずれ) 区域。確信度を上げすぎない"""
        jp = gpd.GeoDataFrame({"ntown": self.jukyo.ntown}, geometry=self.jgeom, crs=6675)
        sj = gpd.sjoin(jp, self.parts[["town", "geometry"]], predicate="within")
        self.enclave_parts = set(sj.index_right[sj.ntown != sj.town.map(norm_town)])

    # ---------------- 地区 ----------------
    @staticmethod
    def _row(r):
        sp = lambda x: re.sub(r"\s+", " ", x).strip()
        return {"町名": r["town"], "街区": sp(r["raw_blk"]), "番号・番地": sp(r["raw_ban"]), "地区": r["district"],
                **({"注記": "(注意)" + r["note"]} if r["note"] else {})}

    def _rows(self, town, district=None):
        return [self._row(r) for r in self.by_town.get(town, []) if district is None or r["district"] == district]

    def _addr_district(self, i):
        """住所 i (索引の行番号) → (区域表の行 or None, 町が表にあるか)"""
        if i in self._row_cache: return self._row_cache[i]
        if not self._row_cache: self._jcols = (list(self.jukyo.ntown), self.jukyo.blk_i.to_numpy(float), self.jukyo.num_i.to_numpy(float))
        t, b, n = self._jcols[0][i], self._jcols[1][i], self._jcols[2][i]
        rs = self.by_town.get(t)
        if not rs: res = (None, False)
        elif np.isnan(b) or np.isnan(n): res = (None, True)
        else: res = (match_row(rs, int(b), int(n)), True)
        self._row_cache[i] = res
        return res

    def locate(self, lat, lng, accuracy_m=None, accuracy_confidence=None):
        """accuracy_m: 端末が返す位置の精度 (m)、accuracy_confidence: その半径に本当の位置が入る確率 (既定 ACC_CONF)。
        省略時は 10m 以内を前提にする。渡すと 99% の半径 (search_radius) まで広げ、その範囲の根拠をすべて候補に入れる"""
        p = gpd.GeoSeries([Point(lng, lat)], crs=4326).to_crs(6675).iloc[0]
        if not self.city.contains(p): return None
        acc = search_radius(accuracy_m, accuracy_confidence or ACC_CONF)
        addr_r, far_r, nb_r = ADDR_M + acc, ADDR_FAR_M + acc, NEAR_M + acc
        pn_r, blind_r = PARCEL_NEAR_M + acc, BLIND_M + acc
        cands, notes, unlisted = collections.OrderedDict(), [], []
        def add(d, kind, evidence, official=True, rows=()):
            c = cands.setdefault(d, {"collection_district": d, "basis": [], "official": False, "evidence": [], "how_to_confirm": []})
            c["basis"].append({"kind": kind, "official": official, "label": BASIS_JA[kind], "detail": evidence})
            c["official"] = any(b["official"] for b in c["basis"])
            c["evidence"].append(evidence)
            for r in rows:
                if r not in c["how_to_confirm"] and len(c["how_to_confirm"]) < 8: c["how_to_confirm"].append(r)
        def has(d, kinds): return d in cands and any(b["kind"] in kinds for b in cands[d]["basis"])
        # (1) 区域ポリゴン
        hit = self.parts[self.parts.contains(p)]
        part = hit.iloc[0] if len(hit) else None
        ptown = norm_town(part.town) if part is not None else None
        if part is not None:
            if part.method == "points_single" and not self.uniform.get(ptown):
                # 町内の住居表示住所はすべて同じ地区だが、区域表には別の地区の行もある (兼六町1番 → 小立野 など)
                for dd in sorted({r["district"] for r in self.by_town[ptown]}):
                    add(dd, "town_row", f"区域表で {part.town} の一部が {dd}", rows=self._rows(ptown, dd))
            else:
                how = {"chome": "町丁目まるごと同じ地区", "voronoi": "住居表示住所の点で町を分けた区域", "points_single": "町内の行がすべて同じ地区"}[part.method]
                add(part.district, "zone_polygon", f"{part.town} の区域 ({how})", rows=self._rows(ptown, part.district))
        # (2) 近くの住居表示住所 → 区域表の行
        near_all = sorted((float(self.jgeom[i].distance(p)), int(i)) for i in self.jtree.query(p.buffer(far_r)))
        near_all = [(d, i) for d, i in near_all if d <= far_r]
        near = [(d, i) for d, i in near_all if d <= addr_r]
        far_addr = None          # 最寄りの住所が addr_r より遠い (住所がまばらな所): 町が区域表に無いかどうかだけ見る
        if near_all and not near:
            d, i = near_all[0]
            far_addr = {"label": self.jlabel[i], "town": self.jukyo.town.iat[i], "distance_m": round(d, 1), "in_table": self._addr_district(i)[1]}
        addr, row = None, None
        if near:
            d0, i0 = near[0]; a = self.jukyo.iloc[i0]
            row, in_table = self._addr_district(i0)
            addr = {"label": self.jlabel[i0], "town": a.town, "distance_m": round(d0, 1), "source": SRC_JUKYO,
                    "in_rules_table": in_table if row is None else True}
            if not in_table:
                pass
            elif row is None:
                addr["in_rules_table"] = "town_only"
                notes.append(f"区域表の {a.town} の行は住居表示の街区・番号では引けません (小字・地番で分かれているか、表の抜け)")
                for dd in sorted({r["district"] for r in self.by_town[a.ntown]}):
                    add(dd, "town_row", f"区域表で {a.town} の一部が {dd}", rows=self._rows(a.ntown, dd))
            else:
                addr["matched_district"] = row["district"]
                if d0 <= ADDR_SURE_M:
                    add(row["district"], "address_row", f"最寄りの住所 {addr['label']} ({addr['distance_m']}m) が区域表の行に当たる", rows=[self._row(row)])
            for d, i in near:
                r2, t2 = self._addr_district(i)
                if not t2: unlisted.append({"town": self.jukyo.town.iat[i], "source": "住居表示住所", "label": self.jlabel[i], "distance_m": round(d, 1)}); continue
                if i == i0 and d0 <= ADDR_SURE_M: continue
                nt = self.jukyo.ntown.iat[i]          # 行が決まらない住所 (町は表にある) は、その町の地区を全部候補にする
                for dd in ([r2["district"]] if r2 else sorted({r["district"] for r in self.by_town[nt]})):
                    if has(dd, ("address_row", "near_address")): continue
                    add(dd, "near_address", f"{round(d, 1)}m 先の住所 {self.jlabel[i]} が区域表で {dd}" + ("" if r2 else " (の場合あり)"),
                        rows=[self._row(r2)] if r2 else self._rows(nt, dd)[:2])
        # (3) 近くの地番 (ABR 地番代表点、住居表示の無い筆) → 区域表の行
        R = max(PARCEL_M, pn_r)
        pnear = sorted((float(self.pgeom[i].distance(p)), int(i)) for i in self.ptree.query(p.buffer(R)))
        pnear = [(d, i) for d, i in pnear if d <= R]
        parcel = None
        if pnear and pnear[0][0] <= PARCEL_M:      # 最寄りの地番 = この場所の地番
            d0, i0 = pnear.pop(0); row_p, tt = self.prow[i0]
            parcel = {"label": self.plabel[i0], "town": self.ptownname[i0], "distance_m": round(d0, 1), "source": SRC_PARCEL,
                      "in_rules_table": bool(tt) if row_p is None else True}
            if not tt:
                if d0 <= addr_r: unlisted.append({"town": parcel["town"], "source": "地番", "label": parcel["label"], "distance_m": parcel["distance_m"]})
            elif row_p is None:
                parcel["in_rules_table"] = "town_only"
                notes.append(f"区域表の {tt} の行では、地番 {parcel['label']} の地区を言い切れません (条件付きの行・小字の書き方の違いなど)")
                for dd in sorted({r["district"] for r in self.by_town[tt]}):
                    add(dd, "town_row", f"区域表で {tt} の一部が {dd}", rows=self._rows(tt, dd))
            else:
                parcel["matched_district"] = row_p["district"]
                add(row_p["district"], "parcel_row", f"最寄りの地番 {parcel['label']} ({parcel['distance_m']}m) が区域表の行に当たる", rows=[self._row(row_p)])
                if not self.uniform.get(tt):
                    # 地区が分かれる町: 住居表示の無い筆の半分ほどは ABR に位置が無いので、最寄りの地番が利用者の地番とは限らない
                    notes.append(f"{tt} は区域表で地区が分かれます。住居表示の無い地域では小字ごと位置情報が無いことがあり、"
                                 f"最寄りの位置つきの地番が利用者の地番とは限らないため、地番だけでは確定しません")
                    for dd in sorted({r["district"] for r in self.by_town[tt]}):
                        add(dd, "town_row", f"区域表で {tt} の一部が {dd}", rows=self._rows(tt, dd))
        for d, i in pnear:                          # 近くの別の地番
            if d > pn_r: break
            r2, t2 = self.prow[i]
            if not t2:
                if d <= addr_r: unlisted.append({"town": self.ptownname[i], "source": "地番", "label": self.plabel[i], "distance_m": round(d, 1)})
                continue
            # 地区が分かれる町の地番が近くにあれば、その町の地区を全部候補にする (位置の無い筆が近くにあるかもしれない)
            for dd in ([r2["district"]] if self.uniform.get(t2) else sorted({r["district"] for r in self.by_town[t2]})):
                if has(dd, ("parcel_row", "near_parcel")): continue
                sure = r2 is not None and r2["district"] == dd
                add(dd, "near_parcel", f"{round(d, 1)}m 先の地番 {self.plabel[i]} " + (f"が区域表で {dd}" if sure else f"の町 {t2} の一部が区域表で {dd}"),
                    rows=[self._row(r2)] if sure else self._rows(t2, dd)[:2])
        # (4) 区域ポリゴンが無い場所: この地点を含む国勢調査の町
        census = self.towns[self.towns.contains(p) & (self.towns.HCODE == 8101)]   # 水面 (8154 金沢港など) は町として扱わない
        town = census.iloc[0].ntown if len(census) else None
        water = self.towns[self.towns.contains(p) & (self.towns.HCODE != 8101)]
        water_name = f"{water.iloc[0].ntown} (水面)" if len(water) and not town else None
        inference, census_missing = None, False
        if part is None and town:
            if self.by_town.get(town):
                for dd in sorted({r["district"] for r in self.by_town[town]}):
                    add(dd, "census_town_row", f"この地点を含む国勢調査の町 {town} は区域表で {dd} (一部の場合あり)", rows=self._rows(town, dd))
            else:
                rel = sorted(k for k in self.by_town if k != town and len(k) >= 2 and (town.startswith(k) or k.startswith(town)))
                whole = [(k, r) for k in rel for r in self.by_town[k] if r["kind"] == "town"]
                for k, r in whole:   # 国勢調査の 諸江町中丁 ⇔ 区域表の 諸江町 (町全体の行)。町名の推定を含む唯一の確定 (inference に明記)
                    add(r["district"], "town_row_prefix", f"区域表に {town} の行は無く、よく似た町名 {k} の行 (町全体) から推定", rows=[self._row(r)])
                for k, r in ((k, r) for k in rel for r in self.by_town[k] if r["kind"] != "town"):
                    # 太陽が丘区画整理地内 ⇔ 太陽が丘 街区3から26 → 田上 (条件付きなので確定には使わない)
                    add(r["district"], "conditional_similar_row", f"よく似た町名の行: {k} 街区{r['raw_blk']} {r['raw_ban']} → {r['district']}".replace("  ", " "),
                        official=False, rows=[self._row(r)])
                if whole: inference = f"区域表に {town} の行は無く、よく似た町名 {'・'.join(sorted({k for k, _ in whole}))} の行 (町全体) から推定"
                else: census_missing = True
        # (5) 住居表示住所が1つも無い区域表の行 (町を問わず近ければ候補。位置の無い行は町が合えば候補)
        ui = {}
        for i in self.ui_tree.query(p.buffer(blind_r)):
            d = float(self.ui_geom[i].distance(p))
            if d <= blind_r and (self.ui_rows[i][1] not in ui or d < ui[self.ui_rows[i][1]][0]): ui[self.ui_rows[i][1]] = (d, self.ui_rows[i][0])
        for desc, (d, r) in sorted(ui.items(), key=lambda x: x[1][0]):
            if has(r["district"], ("unindexed_row",)): continue
            add(r["district"], "unindexed_row", f"{round(d)}m 先に {desc} → {r['district']}".replace("  ", " "), rows=[self._row(r)])
        for t in {x for x in (ptown, town) if x}:
            for r in self.unindexed.get(t, []):
                add(r["district"], "unindexed_row", f"区域表の行 {r['town']} 街区{r['raw_blk']} {r['raw_ban']} → {r['district']} (住居表示住所も位置情報も無い)".replace("  ", " "), rows=[self._row(r)])
        # (6) 地区境界のそば
        if part is not None:
            nb = self.parts[(self.parts.district != part.district) & (self.parts.distance(p) <= nb_r)]
            for dd in sorted(set(nb.district)):
                add(dd, "near_boundary", f"{round(nb_r)}m 以内に {dd} 地区の区域がある")
        if acc > 0:               # 誤差の範囲にかかる国勢調査の町
            ct = self.towns[(self.towns.HCODE == 8101) & (self.towns.distance(p) <= nb_r)]
            for t, dist in sorted({(t, float(g.distance(p))) for t, g in zip(ct.ntown, ct.geometry) if t != town}):
                if self.by_town.get(t):
                    for dd in sorted({r["district"] for r in self.by_town[t]}):
                        if not has(dd, ("near_boundary",)): add(dd, "near_boundary", f"誤差の範囲 (約{round(nb_r)}m) に国勢調査の町 {t} ({dd} を含む)")
                else:
                    whole = [(k2, r) for k2 in self.by_town if k2 != t and len(k2) >= 2 and (t.startswith(k2) or k2.startswith(t))
                             for r in self.by_town[k2] if r["kind"] == "town"]
                    for k2, r in whole:
                        add(r["district"], "near_boundary", f"誤差の範囲に国勢調査の町 {t} (区域表には無く、よく似た町名 {k2} の行 (町全体) が {r['district']})")
                    if not whole:
                        unlisted.append({"town": t, "source": "国勢調査の町", "label": t, "distance_m": round(dist, 1)})
        # (7) この場所の町: 最寄りの住所・地番のうち近い方。無ければ国勢調査の町
        # この場所の町は、最寄りの住所 (ADDR_M 以内) か地番 (PARCEL_M 以内) のうち近い方から。誤差を広げても遠い住所からは決めない
        here = min([x for x in (addr if addr and addr["distance_m"] <= ADDR_M else None, parcel) if x], key=lambda x: x["distance_m"], default=None)
        place_name = (here["town"] if here else None) or town or water_name
        d_border = float(self.land_border.distance(p))
        blocked = None
        if here and here["in_rules_table"] is False:
            blocked = {"reason": "place_town_not_in_rules_table", "town": here["town"], "source": "住居表示住所" if here is addr else "地番",
                       "label": here["label"], "distance_m": here["distance_m"]}
        elif not here and not addr and far_addr and not far_addr["in_table"]:     # 住所がまばらな所で、最寄りの住所が区域表に無い町
            blocked = {"reason": "place_town_not_in_rules_table", "town": far_addr["town"], "source": "住居表示住所",
                       "label": far_addr["label"], "distance_m": far_addr["distance_m"]}
            place_name = far_addr["town"]
        elif census_missing:
            blocked = {"reason": "census_town_not_in_rules_table", "town": town, "source": "国勢調査の町 (この地点を含む)"}
        elif d_border <= nb_r:      # 隣の市町との境界のそば: 金沢市かどうかも言い切れない
            blocked = {"reason": "near_municipal_boundary", "town": None, "source": "国土数値情報 行政区域 (N03)", "distance_m": round(d_border, 1)}
        elif unlisted or (far_addr and not far_addr["in_table"]):
            u = min(unlisted + ([{"town": far_addr["town"], "source": "住居表示住所", "label": far_addr["label"], "distance_m": far_addr["distance_m"]}]
                                if far_addr and not far_addr["in_table"] else []), key=lambda x: x["distance_m"])
            blocked = {"reason": "nearby_town_not_in_rules_table", **u}
        if blocked and blocked["town"]:
            for a_, r in self.via_old.get(norm_town(blocked["town"]), []):
                add(r["district"], "inferred_lineage", f"区域表に {blocked['town']} の行が無い。旧町名 {a_['alias_name']} の行から推定 (系譜の確信度 {a_['confidence']})", official=False)
        # ---- 判定 ----
        for c in cands.values():
            c["rank"] = max(RANK[b["kind"]] * (1 if b["official"] else 0.5) for b in c["basis"])
        ordered = sorted(cands.values(), key=lambda c: -c["rank"])
        official = [c for c in ordered if c["official"]]
        kinds = lambda c: {b["kind"] for b in c["basis"] if b["official"]}
        status, district, conf, method = "ambiguous", None, (0.6 if len(official) == 2 else 0.4), "candidates"
        if blocked:
            status, conf, method = "unresolved", 0.0, blocked["reason"]
        elif not official:
            status, conf, method = "unresolved", 0.0, "no_official_row"
            blocked = {"reason": "no_official_row", "town": None}
        elif len(official) == 1 and kinds(official[0]) & DECIDING and any(h.get("注記") for h in official[0]["how_to_confirm"]):
            # 主計町: 区域表は馬場地区に割り当てつつ「(注意)実際の地区とは異なります」と注記している。どちらが収集地区か表から言えない
            h = next(h for h in official[0]["how_to_confirm"] if h.get("注記"))
            status, conf, method = "unresolved", 0.0, "rules_table_note"
            blocked = {"reason": "rules_table_note", "town": h["町名"], "district": h["地区"], "note": h["注記"]}
        elif (len(official) == 1 and kinds(official[0]) & DECIDING and not AREA_ALONE_OK and addr is None and parcel is None
              and kinds(official[0]) <= {"zone_polygon", "census_town_row", "town_row", "town_row_prefix", "near_boundary", "unindexed_row"}):
            method = "area_only_evidence"          # 町の区域だけが根拠: 近くの土地がどの町のものか確かめられない
        elif len(official) == 1 and kinds(official[0]) & DECIDING:
            c, ks = official[0], kinds(official[0])
            district, status = c["collection_district"], "ok"
            clean = part is not None and part.method == "chome" and part.name not in self.enclave_parts
            if {"address_row", "zone_polygon"} <= ks:
                conf, method = (0.97 if clean and norm_town(addr["town"]) == ptown else 0.93), "address_and_zone_agree"
            elif {"parcel_row", "zone_polygon"} <= ks:
                conf, method = (0.93 if clean else 0.9), "parcel_and_zone_agree"
            elif "zone_polygon" in ks:
                conf, method = {"chome": 0.95 if clean else 0.9, "points_single": 0.9, "voronoi": 0.85}[part.method], "zone_polygon"
            elif ks & {"address_row", "parcel_row"}:
                conf, method = 0.85, ("address_row" if "address_row" in ks else "parcel_row")
            elif ks & {"town_row", "census_town_row"}:
                conf, method = 0.8, "town_row"
            else:
                conf, method = 0.7, "town_row_prefix"
        elif len(official) == 1:
            method = "only_nearby_evidence"          # 近くの住所・区域だけが根拠: この場所を直接指していないので確定しない
        for c in ordered: c.pop("rank")
        rows_town = next((t for t in (norm_town(here["town"]) if here else None, ptown, town) if t and self.by_town.get(t)), None) \
            or (table_town(norm_town(parcel["town"]), self.by_town) if parcel else None) or ""
        sim_rows = [h for c in ordered for h in c["how_to_confirm"]] if census_missing else []
        return {"status": status, "collection_district": district, "district_confidence": round(conf, 2), "method": method,
                "census_town": town, "place_name": place_name, "inference": inference if status == "ok" and method == "town_row_prefix" else None,
                "nearest_address": addr if addr and addr["distance_m"] <= ADDR_M else None, "nearest_parcel": parcel,
                "decision_blocked_by": blocked, "distance_to_municipal_border_m": round(d_border, 1),
                "evidence_radii_m": {"address": round(addr_r, 1), "parcel": round(pn_r, 1), "zone_boundary": round(nb_r, 1)},
                "candidates": ordered, "notes": notes,
                "town_rows": (self._rows(rows_town) or sim_rows)[:12]}

    # ---------------- 住所の文字列 ----------------
    def locate_address(self, text):
        """利用者が伝えた住所の文字列 → locate() と同じ形の dict (読み方は kzaddress)。金沢市以外なら {"out_of_coverage": 名前}"""
        import kzaddress
        if not hasattr(self, "_addr_index"): self._addr_index = kzaddress.AddressIndex(self)
        ax = self._addr_index
        s, stated, other = kzaddress.clean(text)
        if other and (stated or not ax.town_of(s)[0]): return {"out_of_coverage": other}
        readings, town = ax.readings(s)
        readings = list({tuple(sorted(r.items())): r for r in readings}.values())          # 同じ読み方は1つに
        label = ("金沢市" if stated else "") + s           # 市町村名が書かれていなければ、勝手に「金沢市」を付けない
        cands, notes = collections.OrderedDict(), []
        def add(d, kind, evidence, rows=(), official=True):
            c = cands.setdefault(d, {"collection_district": d, "basis": [], "official": False, "evidence": [], "how_to_confirm": []})
            c["basis"].append({"kind": kind, "official": official, "label": BASIS_JA[kind], "detail": evidence})
            c["official"] = any(b["official"] for b in c["basis"])
            c["evidence"].append(evidence)
            for r in rows:
                h = self._row(r)
                if h not in c["how_to_confirm"] and len(c["how_to_confirm"]) < 8: c["how_to_confirm"].append(h)
        tt = table_town(town, self.by_town) if town else None
        addr = {"input": str(text), "label": label, "readings": [{k: v for k, v in r.items()} for r in readings], "in_rules_table": bool(tt),
                "municipality_stated": stated}
        status, district, conf, method, blocked = "ambiguous", None, 0.4, "address_candidates", None
        evals = [(r, *ax.evaluate(r)) for r in readings]
        c = (ax.collisions.get(town) or ax.collisions.get(tt or "")) if town and not stated else None
        collision = (f"市町村名が書かれていません。{town} は金沢市のほか {'・'.join(c['examples'][:3])}{' など' if c['count'] > 3 else ''} にもあります"
                     f" (全国で {c['count'] + 1} か所)。") if c else None
        valid = [(r, ds, rows, desc, ex) for r, ds, rows, desc, ex in evals if ds and ex]
        no_row = [(r, desc) for r, ds, rows, desc, ex in evals if ex == "no_row"]          # 実在するが区域表に町の行が無い読み方: 確定させない
        if not town:
            status, conf, method = "unresolved", 0.0, "address_not_found"
            blocked = {"reason": "address_not_found", "town": None, "label": label}
            head = re.match(r"^[^\d\-]{2,}", s)                                   # 笠舞29番5号 → 笠舞1丁目・笠舞2丁目… (丁目が抜けている)
            similar = sorted((t for t in ax.known if head and t.startswith(head[0]) and t != head[0]), key=lambda t: (len(t), t))[:10] if head else []
            if similar: blocked["similar_towns"] = similar
        elif not tt:
            status, conf, method = "unresolved", 0.0, "place_town_not_in_rules_table"
            blocked = {"reason": "place_town_not_in_rules_table", "town": town, "source": "住所", "label": label, "distance_m": 0.0}
            for a_, r in self.via_old.get(town, []):
                add(r["district"], "inferred_lineage", f"区域表に {town} の行が無い。旧町名 {a_['alias_name']} の行から推定 (系譜の確信度 {a_['confidence']})", official=False)
        elif not valid and no_row:
            nt = no_row[0][0]["town"]
            status, conf, method = "unresolved", 0.0, "place_town_not_in_rules_table"
            blocked = {"reason": "place_town_not_in_rules_table", "town": nt, "source": "住所", "label": label, "distance_m": 0.0}
            notes += [d for _, d in no_row]
        elif not valid:
            # どの読み方でも実在しない (住居表示の番号が無い、など): 町の行を候補にして聞き返す
            method = "address_not_in_registry"
            notes.append(f"住所 {label} は、国土地理院・デジタル庁 ABR の住所データに見つかりません ({'; '.join(d for _, _, _, d, _ in evals)})")
            for dd in sorted({r["district"] for r in self.by_town[tt]}):
                add(dd, "town_row", f"区域表で {tt} の一部が {dd}", rows=[r for r in self.by_town[tt] if r["district"] == dd])
        else:
            for r, ds, rows, desc, ex in valid:
                kind = "given_address_row" if ex is True and len(ds) == 1 and r["kind"] in ("jukyo", "chiban") else "town_row"
                for dd in sorted(ds):
                    add(dd, kind, desc + (f" → {dd}" if len(ds) > 1 else ""), rows=[x for x in rows if x["district"] == dd] or rows[:2])
                if ex is not True or "国土地理院の住所に無い" in desc: notes.append(desc)
            alld = {d for _, ds, _, _, _ in valid for d in ds}
            sure = all(ex is True for *_, ex in valid) and not no_row
            if no_row: notes += [d + "。この読み方の地区は区域表で決められない" for _, d in no_row]
            if len(valid) > 1 and len(alld) > 1:
                notes.insert(0, f"住所 {label} は " + " とも ".join(f"「{d}」" for _, _, _, d, _ in valid) + " とも読めます。番地・号まで正確に書いてください")
            if len(alld) == 1 and sure:
                district = next(iter(alld))
                rowsd = [x for _, _, rows, _, _ in valid for x in rows if x["district"] == district]
                noted = next((x for x in rowsd if x["note"]), None)
                if noted:
                    status, conf, method = "unresolved", 0.0, "rules_table_note"
                    blocked = {"reason": "rules_table_note", "town": noted["town"], "district": noted["district"], "note": "(注意)" + noted["note"]}
                    district = None
                elif collision:
                    method = "municipality_not_stated"
                    notes.insert(0, collision + f"金沢市の{town}なら {district} 地区です")
                    district = None
                else:
                    status, conf = "ok", 0.98
                    method = {"jukyo": "address_table_row", "chiban": "address_parcel_row", "block": "address_block", "town": "address_town_whole"}[valid[0][0]["kind"]]
            else:
                method = "address_needs_detail" if any(ex in ("needs_koaza", "needs_chome") for *_, ex in valid) else (
                    "address_two_readings" if len(valid) > 1 else "address_row_not_decided")
        if collision and method != "municipality_not_stated" and status != "ok":
            notes.insert(0, collision.rstrip("。") + "。金沢市の住所かどうかも確かめてください")      # 押野2丁目1番1号 (番号が無い・決まらない場合も)
        for c in cands.values(): c["rank"] = max(RANK[b["kind"]] * (1 if b["official"] else 0.5) for b in c["basis"])
        ordered = sorted(cands.values(), key=lambda c: -c["rank"])
        for c in ordered: c.pop("rank")
        return {"status": status, "collection_district": district, "district_confidence": conf, "method": method,
                "census_town": None, "place_name": town, "inference": None, "nearest_address": None, "nearest_parcel": None,
                "address": addr, "decision_blocked_by": blocked, "distance_to_municipal_border_m": None, "evidence_radii_m": None,
                "candidates": ordered, "notes": notes, "town_rows": (self._rows(tt) if tt else [])[:12]}

    # ---------------- 収集日 ----------------
    @staticmethod
    def _occurrences(s):
        if s.get("dates"): return {dt.date.fromisoformat(x) for x in s["dates"]}
        lo, hi = dt.date.fromisoformat(s["valid_from"]), dt.date.fromisoformat(s["valid_to"])
        if s["rule_kind"] == "rrule":
            st = lo + dt.timedelta(days=7 if "PHASE" in s["rrule"] else 0)
            got = {x.date() for x in rrulestr(s["rrule"].replace(";PHASE=+7d", ""), dtstart=dt.datetime.combine(st, dt.time()))
                   .between(dt.datetime.combine(lo, dt.time()), dt.datetime.combine(hi, dt.time()), inc=True)}
            return (got - set(map(dt.date.fromisoformat, s["exdates"]))) | set(map(dt.date.fromisoformat, s["rdates"]))
        got = set()
        for sg in s.get("segments", []):
            got |= {x.date() for x in rrulestr(sg["rrule"] + ";UNTIL=" + sg["until"].replace("-", "") + "T235959",
                                               dtstart=dt.datetime.fromisoformat(sg["dtstart"]))}
            got -= set(map(dt.date.fromisoformat, sg["exdates"]))
        return got

    def _year_end(self, district):
        b = next(s for s in self.sched[district] if s["category"] == "burnable")
        fy = int(b["valid_from"][:4]); t = re.sub(r"\s+", "", b["rule_text"])
        e, s = re.search(r"年末は(\d+)月(\d+)日", t), re.search(r"年始は(\d+)月(\d+)日", t)
        return (dt.date(fy, int(e[1]), int(e[2])) if e else None, dt.date(fy + 1, int(s[1]), int(s[2])) if s else None)

    def calendar_range(self, district):
        s = self.sched[district][0]
        return dt.date.fromisoformat(s["valid_from"]), dt.date.fromisoformat(s["valid_to"])

    def days(self, district, date, n=2):
        ss = self.sched[district]
        lo, hi = self.calendar_range(district)
        last, first = self._year_end(district)
        out = []
        for k in range(n):
            d = date + dt.timedelta(days=k)
            day = {"date": str(d), "weekday": WDJ[d.weekday()], "in_calendar_range": lo <= d <= hi}
            if not day["in_calendar_range"]:
                day["collections"] = None
                day["note"] = f"金沢市の{lo.year}年度カレンダー ({lo}〜{hi}) の範囲外で分かりません (「収集なし」という意味ではありません)"
            else:
                cols = []
                for s in ss:
                    if d in self.occ[(district, s["category"])]:
                        exc = [m for m in s.get("rule_mismatches", []) if m["month"] == f"{d.year}-{d.month:02d}"]
                        moved = bool(exc) and d.day not in exc[0]["rule"]
                        rule = re.sub(r"\s+", "", s["rule_text"])
                        cols.append({"category": s["category"], "official_label": s["official_label"],
                                     "rule_text": rule.split("年末")[0] if s["category"] == "burnable" else rule,
                                     "is_exception": moved, **({"exception_note": f"通常の「{rule}」とは違う日 (カレンダーの表の日付。違いがあるのは2026年12月・2027年1月だけ)"} if moved else {})})
                day["collections"] = cols
                if last and first and last < d < first:
                    day["note"] = f"年末年始: 燃やすごみは {fmt(last, True)} まで、{fmt(first, True)} から"
            out.append(day)
        return out

    def next_after(self, district, date):
        lo, hi = self.calendar_range(district)
        res = []
        for s in self.sched[district]:
            item = {"category": s["category"], "official_label": s["official_label"]}
            if date + dt.timedelta(days=1) < lo:      # 前年度のカレンダーは持っていないので、それより前の収集日は分からない
                res.append({**item, "date": None, "weekday": None,
                            "null_reason": f"{lo} より前の期間のカレンダーは未取得なので、次の収集日は分からない"}); continue
            nxt = min((d for d in self.occ[(district, s["category"])] if d > date), default=None)
            res.append({**item, "date": str(nxt) if nxt else None, "weekday": WDJ[nxt.weekday()] if nxt else None,
                        **({} if nxt else {"null_reason": f"{lo.year}年度のカレンダー ({hi} まで) にこれより後の日が無い ({lo.year + 1}年度版は未取得)"})})
        return sorted(res, key=lambda x: x["date"] or "9999")

    def sources(self, district):
        src = self.sched[district][0]["source"]
        title = src["page_title"].split("／")[0] + (f" — {src['section']}" if src.get("section") else f" — {district}")
        return [RULES_SOURCE, {"title": title, **{k: src[k] for k in ("url", "publisher", "retrieved_at", "table_sha256")}},
                PUT_OUT_RULES]

def gps2city(lat, lng):
    req = urllib.request.Request(GPS2CITY, method="POST", data=json.dumps({"latitude": lat, "longitude": lng}).encode(), headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=5))

def _number(v):
    """緯度経度の値 → float。真偽値・バイト列・NaN・無限大・桁あふれは受け付けない"""
    if isinstance(v, (bool, np.bool_, bytes, bytearray)) or v is None: raise TypeError
    x = float(v.strip() if isinstance(v, str) else v)
    if not math.isfinite(x): raise ValueError
    return x

def _date(v):
    """日付 → datetime.date。日時 (時刻つき)・それ以外の型・2000〜2099年の外は受け付けない"""
    if isinstance(v, dt.datetime): raise TypeError
    d = v if isinstance(v, dt.date) else dt.date.fromisoformat(v.strip()) if isinstance(v, str) and re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", v) else None
    if d is None: raise TypeError
    if not 2000 <= d.year <= 2099: raise ValueError
    return d

def resolve(k, lat, lng, date=None, days=2, online=True, accuracy_m=None, accuracy_confidence=None):
    """緯度経度・日付 → LLM 向けの JSON。結果は呼び出しごとに新しいオブジェクト (定数を共有しない)"""
    return copy.deepcopy(_resolve(k, lat, lng, date, days, online, accuracy_m, accuracy_confidence))

def _resolve(k, lat, lng, date, days, online, accuracy_m, accuracy_confidence):
    now = dt.datetime.now(JST)
    base = {"data_version": k.data_version, "generated_at": now.isoformat(timespec="seconds"), "put_out_by": PUT_OUT_BY,
            "not_covered": NOT_COVERED, "contact": CONTACT}
    bad = lambda q, msg: {**base, "query": q, "status": "invalid_input", "answerable": False, "summary_ja": msg}
    try:
        lat, lng = _number(lat), _number(lng)
    except (TypeError, ValueError, OverflowError):
        return bad({"lat": repr(lat)[:40], "lng": repr(lng)[:40]}, "緯度・経度を数値で指定してください。")
    if date is not None:
        try: date = _date(date)
        except (TypeError, ValueError):
            return bad({"lat": lat, "lng": lng, "date": repr(date)[:40]}, "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。")
    days_ok = isinstance(days, numbers.Integral) and not isinstance(days, (bool, np.bool_)) and 1 <= int(days) <= 14
    q = {"lat": lat, "lng": lng, "timezone": "Asia/Tokyo", "days": int(days) if days_ok else repr(days)[:20],
         "date": str(date or now.date()), "date_basis": "user" if date else "server_today_jst",
         "accuracy_m": accuracy_m if accuracy_m is None or isinstance(accuracy_m, str) else repr(accuracy_m)[:20]}
    base["query"] = q
    date = date or now.date()
    if not days_ok:
        return bad(q, "days は 1〜14 の整数で指定してください。")
    days = int(days)
    if accuracy_confidence is not None:          # 省略 (None) は既定の 0.68
        try:
            accuracy_confidence = _number(accuracy_confidence)
            if not 0.5 <= accuracy_confidence <= 0.99: raise ValueError
        except (TypeError, ValueError, OverflowError):
            q["accuracy_confidence"] = repr(accuracy_confidence)[:20]
            return bad(q, "accuracy_confidence (accuracy_m の半径に本当の位置が入る確率) は 0.5〜0.99 で指定してください (Android・Chrome は 0.68)。")
    accuracy_confidence = accuracy_confidence or ACC_CONF
    q["accuracy_confidence"] = accuracy_confidence
    if accuracy_m is not None:
        try:
            accuracy_m = _number(accuracy_m)
            if not 0 <= accuracy_m <= 1000: raise ValueError
        except (TypeError, ValueError, OverflowError):
            return bad(q, "accuracy_m (位置の誤差) は 0〜1000 の数値 (m) で指定してください。")
        q["accuracy_m"] = accuracy_m
        q["search_radius_m"] = round(search_radius(accuracy_m, accuracy_confidence), 1)
    if 122 <= lat <= 154 and 20 <= lng <= 46:
        return bad(q, "緯度と経度が入れ替わっている可能性があります (日本では緯度 20〜46、経度 122〜154 付近)。")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return bad(q, "緯度は -90〜90、経度は -180〜180 で指定してください。")
    muni = None
    if online:
        try: muni = gps2city(lat, lng)
        except Exception as e: muni = {"error": str(e)}
    in_city = bool(muni and muni.get("lg_code") == LG)
    if q.get("search_radius_m", 0) > MAX_SEARCH_M and (in_city or k.city.contains(gpd.GeoSeries([Point(lng, lat)], crs=4326).to_crs(6675).iloc[0])):
        return {**base, "status": "unresolved", "answerable": False,
                "location": {"municipality": {"lg_code": LG, "name": MUNI, "checked_by": "gps2city" if in_city else "census_polygons"},
                             "collection_district": None, "district_confidence": 0.0, "method": "accuracy_too_low", "place_name": None,
                             "decision_blocked_by": {"reason": "accuracy_too_low", "search_radius_m": q["search_radius_m"]}},
                "candidates": [], "consensus": None, "town_rows": [], "notes": [], "holiday_policy": HOLIDAY_POLICY,
                "calendar": None, "official_notes": [], "sources": [RULES_SOURCE, PUT_OUT_RULES],
                "summary_ja": f"位置の精度が低すぎて (本当の位置が約{round(q['search_radius_m'])}m の範囲のどこか)、収集地区を絞れません。"
                              "位置の精度を上げるか、住所を教えてください。"}
    loc = k.locate(lat, lng, accuracy_m, accuracy_confidence)
    if loc is None and in_city:
        # gps2city (国土数値情報の行政区域) では金沢市だが、国勢調査の町の区域の外 (境界のずれ・海岸など)
        return {**base, "status": "unresolved", "answerable": False,
                "location": {"municipality": {"lg_code": LG, "name": MUNI, "checked_by": "gps2city"}, "collection_district": None,
                             "district_confidence": 0.0, "method": "outside_town_polygons", "place_name": None,
                             "decision_blocked_by": {"reason": "outside_town_polygons", "town": None}},
                "candidates": [], "consensus": None, "town_rows": [], "notes": [], "holiday_policy": HOLIDAY_POLICY,
                "calendar": None, "official_notes": [], "sources": [RULES_SOURCE, PUT_OUT_RULES],
                "summary_ja": f"この場所は{MUNI}内ですが、町の区域 (国勢調査の小地域) の外にあり、収集地区を判定できません。"
                              f"{CONTACT['name']} ({CONTACT['phone']}) に確認してください。"}
    if loc is None or (muni and muni.get("lg_code") and muni["lg_code"] != LG):
        name = f"{muni.get('prefecture', '')}{muni.get('city', '')}{muni.get('ward', '')}" if muni and muni.get("city") else None
        return {**base, "status": "out_of_coverage", "answerable": False,
                "location": {"municipality": {"lg_code": muni.get("lg_code"), "name": name} if name else None},
                "coverage": [{"lg_code": LG, "name": MUNI, "calendar": "2026-04-01〜2027-03-31"}],
                "summary_ja": (f"この場所は{name}で、gomisute-gps はまだ対応していません (対応: {MUNI})。{name}の公式サイトで確認してください。"
                               if name else f"この場所は対応地域 ({MUNI}) の町の区域 (国勢調査の小地域) の外です (市外か、市内の海岸など)。")}
    return _answer(k, loc, date, days, base, "gps2city" if in_city else "census_polygons")

def _answer(k, loc, date, days, base, checked_by):
    """地区の判定 (loc) → 日ごとの答え・出典・要約"""
    for c in loc["candidates"]:
        c["days"] = k.days(c["collection_district"], date, days)
    anyd = loc["collection_district"] or (loc["candidates"][0]["collection_district"] if loc["candidates"] else None)
    lo, hi = k.calendar_range(anyd) if anyd else (None, None)
    res = {**base, "status": loc["status"],
           "location": {"municipality": {"lg_code": LG, "name": MUNI, "checked_by": checked_by},
                        **{x: loc.get(x) for x in ("collection_district", "district_confidence", "method", "census_town", "place_name",
                                                   "inference", "nearest_address", "nearest_parcel", "address", "decision_blocked_by",
                                                   "distance_to_municipal_border_m", "evidence_radii_m")}},
           "candidates": loc["candidates"], "town_rows": loc["town_rows"], "notes": loc["notes"], "holiday_policy": HOLIDAY_POLICY,
           "calendar": {"valid_from": str(lo) if lo else None, "valid_to": str(hi) if hi else None}}
    # 区域表の注記: 確定なら確定した地区の行、そうでなければ全候補の行から
    pool = [c for c in loc["candidates"] if loc["status"] != "ok" or c["collection_district"] == loc["collection_district"]]
    res["official_notes"] = list(dict.fromkeys(f"公式の区域表は {h['町名']} を {h['地区']}地区に割り当て、「{h['注記']}」と注記しています (注記の意味は公式ページに説明なし)"
                                              for c in pool for h in c["how_to_confirm"] if h.get("注記")))
    if loc["status"] == "ok":
        dd = loc["collection_district"]
        res["days"] = next(c for c in loc["candidates"] if c["collection_district"] == dd)["days"]
        res["next_after_date"] = k.next_after(dd, date)
        res["sources"] = k.sources(dd)
        res["answerable"] = all(d["in_calendar_range"] for d in res["days"])
    else:
        res["answerable"] = False
        # unresolved では正しい地区が候補に無いことがあるので、「どの候補でも同じ」は出さない
        res["consensus"] = consensus(loc["candidates"]) if loc["status"] == "ambiguous" else None
        res["sources"] = [RULES_SOURCE] + [s for c in loc["candidates"] for s in k.sources(c["collection_district"])[1:2]] + [PUT_OUT_RULES]
    apply_freshness(res, freshness(load_source_status(), [x["url"] for x in res["sources"]]))
    res["summary_ja"] = summarize(res)
    return res

def resolve_address(k, address, date=None, days=2):
    """住所の文字列 + 日付 → LLM 向けの JSON (resolve と同じ形)。住所を区域表の行に直接当てる"""
    return copy.deepcopy(_resolve_address(k, address, date, days))

def _resolve_address(k, address, date, days):
    now = dt.datetime.now(JST)
    base = {"data_version": k.data_version, "generated_at": now.isoformat(timespec="seconds"), "put_out_by": PUT_OUT_BY,
            "not_covered": NOT_COVERED, "contact": CONTACT}
    bad = lambda q, msg: {**base, "query": q, "status": "invalid_input", "answerable": False, "summary_ja": msg}
    if not isinstance(address, str) or not address.strip() or len(address) > 200:
        return bad({"address": repr(address)[:60]}, "address は住所の文字列 (200文字まで) で指定してください。")
    if date is not None:
        try: date = _date(date)
        except (TypeError, ValueError):
            return bad({"address": address, "date": repr(date)[:40]}, "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。")
    days_ok = isinstance(days, numbers.Integral) and not isinstance(days, (bool, np.bool_)) and 1 <= int(days) <= 14
    q = {"address": address, "timezone": "Asia/Tokyo", "days": int(days) if days_ok else repr(days)[:20],
         "date": str(date or now.date()), "date_basis": "user" if date else "server_today_jst"}
    base["query"] = q
    if not days_ok: return bad(q, "days は 1〜14 の整数で指定してください。")
    date, days = date or now.date(), int(days)
    loc = k.locate_address(address)
    if loc.get("out_of_coverage"):
        name = loc["out_of_coverage"]
        return {**base, "status": "out_of_coverage", "answerable": False, "location": {"municipality": {"lg_code": None, "name": name}},
                "coverage": [{"lg_code": LG, "name": MUNI, "calendar": "2026-04-01〜2027-03-31"}],
                "summary_ja": f"この住所は{name}で、gomisute-gps はまだ対応していません (対応: {MUNI})。{name}の公式サイトで確認してください。"}
    return _answer(k, loc, date, days, base, "address_text")

def consensus(cands):
    """候補が2つ以上のとき、どの候補の地区でも同じになる答え (候補が1つなら null)"""
    if len(cands) < 2: return None
    out = []
    for i, d0 in enumerate(cands[0]["days"]):
        if not all(c["days"][i]["in_calendar_range"] for c in cands):
            out.append({"date": d0["date"], "all_candidates_agree": None}); continue
        sets = [{x["official_label"] for x in c["days"][i]["collections"]} for c in cands]
        out.append({"date": d0["date"], "all_candidates_agree": all(s == sets[0] for s in sets),
                    "collections_in_every_candidate": sorted(set.intersection(*sets))})
    return out

def _labels(cols):
    return "・".join(x["official_label"] + (f"(通常の「{x['rule_text']}」とは違う日。カレンダーの表のとおり)" if x.get("is_exception") else "") for x in cols) or "収集なし"

def _daytext(day, year, seen=None):
    """1日分の文。seen を渡すと、同じ注記 (年末年始) は最初の日にだけ付ける"""
    d = dt.date.fromisoformat(day["date"])
    if not day["in_calendar_range"]: return f"{fmt(d, True)}は金沢市のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)"
    note = day.get("note")
    if note and seen is not None:
        if note in seen: note = None
        else: seen.add(note)
    return f"{fmt(d, year)}は{_labels(day['collections'])}" + (f" ({note})" if note else "")

def _daystext(days, year):
    seen = set()
    return "。".join(_daytext(d, year, seen) for d in days) + "。"

def _cand_text(c):
    olds = [re.search(r"旧町名 (\S+) の行", b["detail"])[1] for b in c["basis"] if b["kind"] == "inferred_lineage"]
    why = [b["detail"] if b["kind"] in SHOW_DETAIL else b["label"] for b in c["basis"] if b["kind"] != "inferred_lineage"]
    if olds: why.append(f"旧町名 {'・'.join(dict.fromkeys(olds))} の行からの推定")
    why = "、".join(dict.fromkeys(w.replace("。", "、") for w in why))
    return f"{c['collection_district']}地区 ({why}{'' if c['official'] else '、公式の記載ではない'})"

MAX_CANDS_TEXT = 3

def _cands_text(cands):
    t = "、".join(_cand_text(c) for c in cands[:MAX_CANDS_TEXT])
    return t + (f"、ほか {len(cands) - MAX_CANDS_TEXT} 地区 (candidates に全部)" if len(cands) > MAX_CANDS_TEXT else "")

def _where(loc):
    if loc.get("address"): return f"住所 {loc['address']['label']}"
    a, p = loc.get("nearest_address"), loc.get("nearest_parcel")
    here = min([x for x in (a, p) if x], key=lambda x: x["distance_m"], default=None)
    if here: return f"最寄りの{'住所' if here is a else '地番'} {here['label']} から約{round(here['distance_m'])}m"
    return f"{loc['place_name']}の町域" if loc.get("place_name") else ""

def _sentences(*xs):
    s = "".join(x.strip() for x in xs if x and x.strip())
    return re.sub(r"[ 　]{2,}", " ", s)

def summarize(res):
    text = _summarize(res)
    fr = (res.get("freshness") or {}).get("status")
    if fr == "stale": text += "公式ページの変更の確認が古いので、念のため公式ページでも確かめてください。"
    if fr == "partly_unchecked": text += "元にした公式ページの一部を確認できなかったので、念のため公式ページでも確かめてください。"
    if fr == "unchecked": text += "元にした公式ページが変わっていないかを確かめられていないので、念のため公式ページでも確かめてください。"
    return text

def _summarize(res):
    st, loc = res["status"], res["location"]
    b0 = loc.get("decision_blocked_by") or {}
    if b0.get("reason") == "source_changed":          # 元の公式ページが変わった: 日付は言わない
        ch = "、".join(f"「{x['title']}」" + ("(ページが無くなった)" if x.get("gone") else "") for x in b0["changed"][:3])
        before = loc.get("collection_district_before_change")
        return (f"このデータの元にした公式ページ {ch} が、{b0['checked_at'][:10]} の確認で、データを作ったときから変わっていました。"
                f"作り直して確かめるまでは確かな答えとして返しません" + (f" (変更前のデータでは {before}地区)" if before else "") + "。"
                f"公式ページで確かめるか、{CONTACT['name']} ({CONTACT['phone']}) に確認してください。")
    days0 = res.get("days") or (res["candidates"][0].get("days", []) if res.get("candidates") else [])
    out_of_range = any(not d["in_calendar_range"] for d in days0)
    year = bool(days0) and (out_of_range or len({d["date"][:4] for d in days0}) > 1)
    onotes = "".join(f"{n}。" for n in res.get("official_notes", []))
    fy = f"地区は {res['calendar']['valid_from'][:4]}年度の区域表によります。" if out_of_range and res.get("calendar", {}).get("valid_from") else ""
    if st == "ok":
        inf = f" ({loc['inference']})" if loc.get("inference") else ""
        where = _where(loc)
        return _sentences(f"この場所{f' ({where}) ' if where else ''}は金沢市の{loc['collection_district']}地区 (収集カレンダー上の地区名) です{inf}。",
                          _daystext(res["days"], year),
                          ("出すのは収集日の午前8時30分まで (金沢市「ごみの出し方のルール」)。" if any(d.get("collections") for d in res["days"]) else ""),
                          f"出典: 金沢市「{res['sources'][1]['title']}」。地区の確信度 {loc['district_confidence']}。", fy, onotes)
    cands = res.get("candidates") or []
    cand_text = _cands_text(cands)
    per_day = ""
    if len(cands) == 1:
        c = cands[0]
        per_day = f"{c['collection_district']}地区なら、" + _daystext(c["days"], year)
    for i, cs in enumerate(res.get("consensus") or []):
        d = dt.date.fromisoformat(cs["date"])
        if cs["all_candidates_agree"] is None:
            per_day += f"{fmt(d, True)}は金沢市のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)。"
        elif cs["all_candidates_agree"]:
            per_day += f"{fmt(d, year)}はどの候補でも{_labels([x for x in cands[0]['days'][i]['collections']])}。"
        else:
            per_day += f"{fmt(d, year)}は候補によって違います (" + " / ".join(
                f"{c['collection_district']}: {_labels(c['days'][i]['collections'])}" for c in cands[:MAX_CANDS_TEXT]) + \
                (" / ほか" if len(cands) > MAX_CANDS_TEXT else "") + ")。"
    ye = list(dict.fromkeys(f"{c['collection_district']}地区は{d['note'].replace('年末年始: ', '')}" for c in cands[:MAX_CANDS_TEXT]
                            for d in c["days"] if (d.get("note") or "").startswith("年末年始")))
    ye_text = ("年末年始: " + "、".join(ye) + "。") if ye and len(cands) > 1 else ""
    rows = res.get("town_rows") or []
    tip = ("区域表の行 (町名・街区・番号): " + "、".join(
        f"{r['町名']}{(' 街区' + r['街区']) if r['街区'] else ''}{(' ' + r['番号・番地'].strip()) if r['番号・番地'].strip() else ''}→{r['地区']}" for r in rows[:6])
           + ("、ほか" if len(rows) > 6 else "") + "。") if rows else ""
    confirm = "お住まいの住所の街区・番号 (地番) を確かめてください。"
    if st == "ambiguous" and len(cands) == 1 and loc.get("method") == "area_only_evidence":
        return _sentences(f"この地点の近くに住所・地番の位置データが無く、町の区域 (国勢調査の小地域) だけでは地区を確定できません。候補は {cand_text} です。",
                          per_day, tip, confirm, fy, onotes)
    if st == "ambiguous" and len(cands) == 1 and loc.get("address"):
        why = "".join(n.rstrip("。") + "。" for n in res.get("notes", [])[:2])
        return _sentences(why, f"地区を確定できません。候補は {cand_text} です。", per_day, tip, confirm, fy, onotes)
    if st == "ambiguous" and len(cands) == 1:
        return _sentences(f"この地点を直接示す区域表の根拠が無く、地区を確定できません。近くの根拠からの候補は {cand_text} だけです。",
                          per_day, tip, confirm, fy, onotes)
    if st == "ambiguous":
        why = "".join(n.rstrip("。") + "。" for n in res["notes"][:2]) if loc.get("method", "").startswith("address_") or loc.get("method") == "municipality_not_stated" else ""
        return _sentences(why, f"地区を1つに決められません。候補: {cand_text}。", per_day, ye_text, tip, confirm, fy, onotes)
    if st == "unresolved":
        b = loc.get("decision_blocked_by") or {}
        contact = f"{CONTACT['name']} ({CONTACT['phone']}) に確認してください。"
        if b.get("reason") == "source_changed":
            ch = "、".join(f"「{x['title']}」" for x in b["changed"][:3])
            head = (f"このデータの元にした公式ページ {ch} が、{b['checked_at'][:10]} の確認で、データを作ったときから変わっていました。"
                    f"作り直して確かめるまでは確かな答えとして返しません (変更前のデータでは {loc.get('collection_district_before_change')}地区)。公式ページで確かめてください。")
        elif b.get("reason") == "address_not_found":
            head = f"住所 ({b.get('label')}) の町名を、金沢市の町名として見つけられませんでした。" + (
                f"よく似た町名は {'・'.join(b['similar_towns'])} です。丁目まで含めて町名を確かめてください。" if b.get("similar_towns") else "町名の書き方を確かめてください。")
        elif b.get("reason") == "place_town_not_in_rules_table" and b.get("source") == "住所":
            head = f"住所 {b['label']} の町名 {b['town']} は金沢市の収集区域表に載っていません。"
        elif b.get("reason") == "nearby_town_not_in_rules_table":
            w = _where(loc)
            head = ((f"{w.replace('から約', ' (約').replace('m', 'm)', 1)} の町は区域表にありますが、" if w.startswith("最寄り") else "")
                    + (f"約{round(b['distance_m'])}m 先に、区域表に載っていない町 ({b['town']}、国勢調査の町名) があり、"
                       if b["source"] == "国勢調査の町" else
                       f"約{round(b['distance_m'])}m 先に区域表に載っていない町名 ({b['town']}) の{b['source']} ({b['label']}) があり、")
                    + "この場所がどちらの町か決められないため、地区を確定できません。")
        elif b.get("reason") == "near_municipal_boundary":
            head = (f"この場所は金沢市と隣の市町の境界から約{round(b['distance_m'])}m (位置の誤差の範囲) にあり、"
                    "金沢市かどうかも含めて地区を確定できません。")
        elif b.get("reason") == "rules_table_note":
            head = (f"公式の区域表は {b['town']} を {b['district']}地区に割り当てていますが、「{b['note']}」と注記があり、"
                    "収集地区を確定できません (注記の意味は公式ページに説明なし)。")
        elif b.get("reason") == "census_town_not_in_rules_table":
            head = f"この地点を含む町 ({b['town']}、国勢調査の町名) は金沢市の収集区域表に載っていません。"
        elif b.get("reason") == "place_town_not_in_rules_table":
            head = f"この場所 ({b['source']} {b['label']}、約{round(b['distance_m'])}m) の町名 {b['town']} は金沢市の収集区域表に載っていません。"
        else:
            head = f"この場所 ({loc.get('place_name') or '不明'}) について、金沢市の収集区域表の根拠が見つかりません。"
        off = [c for c in cands if c["official"]]
        guess = ""
        if cands:      # 正しい地区が候補に無いこともあるので「どの候補でも」とは言わず、候補ごとに条件つきで言う
            per = "".join(f"{c['collection_district']}地区なら、" + _daystext(c["days"], year) for c in cands[:MAX_CANDS_TEXT])
            guess = (f"区域表の行による候補 (確定ではない): " if off else "参考 (公式ではない) の候補: ") + cand_text + "。" + per
        return _sentences(head, guess, tip, contact, onotes if b.get("reason") != "rules_table_note" else "")
    return ""

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    n = int(args[args.index("--days") + 1]) if "--days" in args else 2
    if "--days" in args: del args[args.index("--days"):args.index("--days") + 2]
    acc = float(args[args.index("--accuracy") + 1]) if "--accuracy" in args else None
    if "--accuracy" in args: del args[args.index("--accuracy"):args.index("--accuracy") + 2]
    conf = float(args[args.index("--confidence") + 1]) if "--confidence" in args else ACC_CONF
    if "--confidence" in args: del args[args.index("--confidence"):args.index("--confidence") + 2]
    if "--address" in args:
        addr = args[args.index("--address") + 1]; del args[args.index("--address"):args.index("--address") + 2]
        a = [x for x in args if not x.startswith("--")]
        print(json.dumps(resolve_address(Kanazawa(), addr, a[0] if a else None, n), ensure_ascii=False, indent=1))
        sys.exit()
    a = [x for x in args if not x.startswith("--")]
    print(json.dumps(resolve(Kanazawa(), a[0], a[1], a[2] if len(a) > 2 else None, n, online="--offline" not in args, accuracy_m=acc, accuracy_confidence=conf), ensure_ascii=False, indent=1))
