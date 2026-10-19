"""N03-2026 の行政区域から「陸のかたまり」(島) を作り、自治体ごとに島の種類を分ける

  python nationwide/islands.py

入力 (nationwide/fetch_registry_sources.py で取得):
  data/registry/n03/N03-20260101.geojson          国土数値情報 行政区域
  data/registry/abr/mt_town_pos/*.csv.zip          ABR 町字の代表点 (47 都道府県)
  data/registry/abr/mt_town_all.csv.zip            ABR 町字 (名前)
  data/registry/abr/mt_city_all.csv.zip            ABR 市区町村 (政令市の区 → 市)

やり方:
  1. N03 のポリゴンを 1 枚ずつにばらし、0.000002° (約 20cm) 以内で接するものをつないで陸のかたまりにする。
     面積 (EPSG:6933 の正積図法) の大きい順に番号 (comp_id) を振る。番号はこの N03 の版の中でだけ有効
  2. 本州・北海道・九州・四国・沖縄本島 (主な 5 島) は、県庁所在地の点を含むかたまりで決める
  3. ABR 町字の代表点を、同じ市区町村コードのポリゴンに置く (入っていなければ同じコードのいちばん近いポリゴン)
  4. 市区町村 (政令市は区) ごとに分ける
       all_island               主な 5 島に陸地が無い (全域が島)
       main+addressed_islands   主な島に陸地があり、ほかに町字の点がある島もある
       main+unaddressed_islets  主な島のほかに島があるが、町字の点は無い (無人の岩礁など)
       main_only                主な島だけ
     政令市は区をまとめて市の種類にする (全部の区が all_island なら all_island、どこかに島があればそれ)
  注意: 湖の島 (近江八幡市 沖島) は湖が市のポリゴンに含まれるので別のかたまりにならない。
        「町字がある島」は「人が住む島」ではない (十島村 臥蛇島、根室市 志発島など)。

出力:
  out/registry/islands.csv            島のかたまり (主な 5 島、町字のある島、0.01km² 以上の島)
  out/registry/municipality_islands.csv  市区町村 (区を含む) ごとの島の種類と島の一覧
  data/registry/work/n03_components.csv  全部のかたまり (no_service_areas.json を作るのに使う)
  data/registry/work/n03_parts.csv       ポリゴン 1 枚ずつ (コード・かたまり・範囲・内側の点)
  data/registry/work/town_components.csv 町字ごとの置き場所
"""
import sys, io, csv, json, time, zipfile, pathlib, collections
import numpy as np
import pandas as pd
import pyogrio
import shapely
from shapely import STRtree

ROOT = pathlib.Path(__file__).resolve().parent.parent
REG = ROOT / "data" / "registry"
WORK = REG / "work"
OUT = ROOT / "out" / "registry"
N03 = REG / "n03" / "N03-20260101.geojson"
N03_VERSION = "N03-20260101"

TOUCH_DEG = 2e-6          # これ以内なら同じ陸のかたまり
MAIN_POINTS = {           # 主な 5 島を決める点 (県庁の近く)
    "本州": (139.6917, 35.6895),
    "北海道": (141.3469, 43.0642),
    "九州": (130.4017, 33.5904),
    "四国": (133.5311, 33.5597),
    "沖縄本島": (127.6809, 26.2124),
}
CLASSES = ["all_island", "main+addressed_islands", "main+unaddressed_islets", "main_only"]


def log(*a):
    print(f"[{time.time() - T0:6.1f}s]", *a, flush=True)


def check_digit(code5):
    """全国地方公共団体コードの検査数字"""
    s = sum(int(c) * w for c, w in zip(code5, (6, 5, 4, 3, 2)))
    return code5 + str((11 - s % 11) % 10)


def zip_csv_rows(path):
    """配布 zip (入れ子の zip もある) の中の CSV を 1 行ずつ dict で返す"""
    def walk(b):
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for n in z.namelist():
                d = z.read(n)
                if n.endswith(".zip"):
                    yield from walk(d)
                elif n.endswith(".csv"):
                    yield d
    for d in walk(pathlib.Path(path).read_bytes()):
        yield from csv.DictReader(io.StringIO(d.decode("utf-8-sig")))


def load_parts():
    """N03 をポリゴン 1 枚ずつにばらす"""
    df = pyogrio.read_dataframe(N03)
    log("N03 features", len(df))
    df = df.explode(index_parts=False).reset_index(drop=True)
    df["code5"] = df["N03_007"].fillna("")
    df["name"] = (df["N03_001"].fillna("") + df["N03_003"].fillna("")
                  + df["N03_004"].fillna("") + df["N03_005"].fillna(""))
    df["area_km2"] = df.geometry.to_crs(6933).area.values / 1e6
    log("parts", len(df))
    return df


def components(df, tree):
    """接しているポリゴンをつなぐ (union-find)"""
    geoms = df.geometry.values
    pairs = tree.query(geoms, predicate="dwithin", distance=TOUCH_DEG)
    parent = np.arange(len(df))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in zip(*pairs):
        if a < b:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    root = np.array([find(i) for i in range(len(df))])
    # 面積の大きい順に 1 から番号を振る
    area = pd.Series(df["area_km2"].values).groupby(root).sum().sort_values(ascending=False)
    rank = {r: i + 1 for i, r in enumerate(area.index)}
    return np.array([rank[r] for r in root])


def place_towns(df, tree):
    """ABR 町字の代表点を同じ市区町村コードのポリゴンに置く"""
    names = {}
    for r in zip_csv_rows(REG / "abr" / "mt_town_all.csv.zip"):
        names[(r["lg_code"], r["machiaza_id"])] = (r["oaza_cho"] + r["chome"] + r["koaza"]).strip()
    pts = []
    for z in sorted((REG / "abr" / "mt_town_pos").glob("mt_town_pos_pref*.csv.zip")):
        for r in zip_csv_rows(z):
            if r["rep_lon"] and r["rep_lat"]:
                pts.append((r["lg_code"], r["machiaza_id"], float(r["rep_lon"]), float(r["rep_lat"])))
    log("町字の代表点", len(pts))
    P = shapely.points(np.array([[p[2], p[3]] for p in pts]))
    code_of_part = df["code5"].values
    ip, ig = tree.query(P, predicate="within")
    hit = np.full(len(pts), -1)
    same = code_of_part[ig] == np.array([pts[i][0][:5] for i in ip])
    hit[ip[same]] = ig[same]
    in_other = len(set(ip[~same]) - set(ip[same]))
    dist = np.zeros(len(pts))
    miss = np.where(hit < 0)[0]
    n_in = len(pts) - len(miss)
    by_code = collections.defaultdict(list)
    for k in miss:
        by_code[pts[k][0][:5]].append(k)
    geoms = df.geometry.values
    idx_by_code = collections.defaultdict(list)
    for i, c in enumerate(code_of_part):
        idx_by_code[c].append(i)
    unplaced = 0
    for c, ks in by_code.items():
        cand = np.array(idx_by_code.get(c, []))
        if len(cand) == 0:
            unplaced += len(ks)
            continue
        for k in ks:
            d = shapely.distance(P[k], geoms[cand])
            j = int(np.argmin(d))
            hit[k], dist[k] = cand[j], float(d[j])
    log(f"町字: 同じコードのポリゴン内 {n_in}, 同じコードの近くに置いた {len(miss) - unplaced}"
        f" (うち 0.01° 超 {int((dist > 0.01).sum())}), 他の市区町村のポリゴン内にあった {in_other}, 置けない {unplaced}")
    T = pd.DataFrame(pts, columns=["lg_code", "machiaza_id", "lon", "lat"])
    T["part"] = hit
    T["comp"] = np.where(hit >= 0, df["comp"].values[np.maximum(hit, 0)], -1)
    T["snap_deg"] = np.round(dist, 6)
    T["name"] = [names.get((a, b), "") for a, b in zip(T.lg_code, T.machiaza_id)]
    log("名前が引けない点 (代表点の町字 ID が町字マスターに無い)", int((T["name"] == "").sum()))
    return T


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_parts()
    tree = STRtree(df.geometry.values)
    df["comp"] = components(df, tree)
    log("陸のかたまり", df["comp"].nunique())

    # 主な 5 島
    main_comp = {}
    for nm, (lon, lat) in MAIN_POINTS.items():
        ids = tree.query(shapely.Point(lon, lat), predicate="within")
        main_comp[int(df["comp"].values[ids[0]])] = nm
    carea = df.groupby("comp")["area_km2"].sum()
    log("主な島", {v: round(float(carea[k]), 1) for k, v in main_comp.items()})
    df["is_main"] = df["comp"].isin(list(main_comp))

    T = place_towns(df, tree)
    T.to_csv(WORK / "town_components.csv", index=False, encoding="utf-8")

    # ポリゴン 1 枚ずつの範囲と内側の点 (no_service_areas.json を作るのに使う)
    bounds = shapely.bounds(df.geometry.values)
    rp_all = shapely.point_on_surface(df.geometry.values)
    B = pd.DataFrame(np.round(bounds, 6), columns=["min_lon", "min_lat", "max_lon", "max_lat"])
    B.insert(0, "part", np.arange(len(df)))
    B.insert(1, "n03_code", df["code5"].values)
    B.insert(2, "comp", df["comp"].values)
    B.insert(3, "area_km2", np.round(df["area_km2"].values, 6))
    B["rep_lon"] = np.round(shapely.get_x(rp_all), 6)
    B["rep_lat"] = np.round(shapely.get_y(rp_all), 6)
    B.to_csv(WORK / "n03_parts.csv", index=False, encoding="utf-8")

    # かたまりごとの情報 (面積・範囲・代表点 = いちばん大きいポリゴンの内側の点)
    g = B.groupby("comp")
    mn, mx = g[["min_lon", "min_lat"]].min(), g[["max_lon", "max_lat"]].max()
    biggest = df.groupby("comp")["area_km2"].idxmax()
    codes = df.groupby("comp")["code5"].agg(lambda s: " ".join(sorted(set(s))))
    pnames = df.groupby("comp")["name"].agg(lambda s: " ".join(sorted(set(s))))
    nparts = df.groupby("comp").size()
    # 町字の名前 (重複なし)。代表点の ID が町字マスターに無い点 (小字の一部) は名前が空なので除く
    town_names = T[(T.comp > 0) & (T.name != "")].groupby("comp")["name"].agg(lambda s: list(dict.fromkeys(s)))
    n_towns = T[T.comp > 0].groupby("comp").size()
    rows = []
    for k, c in enumerate(biggest.index):
        t = town_names.get(c, [])
        rows.append({
            "comp_id": int(c), "main": main_comp.get(c, ""), "area_km2": round(float(carea[c]), 4),
            "n_parts": int(nparts[c]),
            "min_lon": mn.at[c, "min_lon"], "min_lat": mn.at[c, "min_lat"],
            "max_lon": mx.at[c, "max_lon"], "max_lat": mx.at[c, "max_lat"],
            "rep_lon": B.at[biggest[c], "rep_lon"], "rep_lat": B.at[biggest[c], "rep_lat"],
            "n03_codes": codes[c], "n03_names": "" if c in main_comp else pnames[c],
            "n_towns": int(n_towns.get(c, 0)), "towns": "" if c in main_comp else "/".join(t),
        })
    C = pd.DataFrame(rows).sort_values("comp_id")
    C.to_csv(WORK / "n03_components.csv", index=False, encoding="utf-8")
    keep = C[(C["main"] != "") | (C["n_towns"] > 0) | (C["area_km2"] >= 0.01)].copy()
    keep["towns"] = keep["towns"].map(lambda s: "/".join(s.split("/")[:30]) + ("/…" if s.count("/") >= 30 else ""))
    keep.to_csv(OUT / "islands.csv", index=False, encoding="utf-8")
    log("かたまり 全部", len(C), " islands.csv", len(keep),
        " 0.01km²以上", int((C.area_km2 >= 0.01).sum()), " 0.1km²以上", int((C.area_km2 >= 0.1).sum()),
        " 1km²以上", int((C.area_km2 >= 1).sum()),
        " 主な島以外で町字あり", int(((C.main == "") & (C.n_towns > 0)).sum()))

    # 市区町村 (区) ごと
    tc = T[T.comp > 0].copy()
    tc["code5"] = tc.lg_code.str[:5]
    towns_by_code = dict(list(tc.groupby("code5")))
    empty = tc.iloc[:0]
    prof = []
    for code, sub in df.groupby("code5"):
        off = sub[~sub.is_main]
        own = towns_by_code.get(code, empty)
        bb = B.loc[sub.index]
        big = sub["area_km2"].idxmax()
        addressed = sorted(set(own[~own.comp.isin(list(main_comp))].comp.astype(int)))
        mains = sorted({main_comp[c] for c in sub.comp if c in main_comp})
        if not mains:
            cls = "all_island"
        elif addressed:
            cls = "main+addressed_islands"
        elif len(off):
            cls = "main+unaddressed_islets"
        else:
            cls = "main_only"
        prof.append({
            "n03_code": code, "lg_code": check_digit(code) if code else "", "name": sub["name"].iloc[0],
            "class": cls, "mains": "/".join(mains), "area_km2": round(float(sub.area_km2.sum()), 3),
            "off_area_km2": round(float(off.area_km2.sum()), 3), "off_components": int(off.comp.nunique()),
            "n_towns": len(own), "n_towns_off": int((~own.comp.isin(list(main_comp))).sum()),
            "addressed_islands": " ".join(map(str, addressed)),
            "min_lon": bb.min_lon.min(), "min_lat": bb.min_lat.min(), "max_lon": bb.max_lon.max(),
            "max_lat": bb.max_lat.max(), "rep_lon": B.at[big, "rep_lon"], "rep_lat": B.at[big, "rep_lat"],
        })
    Pf = pd.DataFrame(prof)
    # 政令市の区 → 市
    ward_city = {}
    city_rows = list(zip_csv_rows(REG / "abr" / "mt_city_all.csv.zip"))
    city_code = {(r["pref"], r["city"]): r["lg_code"] for r in city_rows if not r["ward"]}
    for r in city_rows:
        if r["ward"]:
            ward_city[r["lg_code"]] = city_code[(r["pref"], r["city"])]
    Pf["muni_lg_code"] = Pf["lg_code"].map(lambda c: ward_city.get(c, c))
    Pf.to_csv(OUT / "municipality_islands.csv", index=False, encoding="utf-8")

    def agg(cls):
        s = set(cls)
        if s == {"all_island"}:
            return "all_island"
        if "main+addressed_islands" in s or "all_island" in s:
            return "main+addressed_islands"
        if "main+unaddressed_islets" in s:
            return "main+unaddressed_islets"
        return "main_only"
    M = Pf[~Pf.n03_code.str.endswith("000")].groupby("muni_lg_code")["class"].agg(agg)
    nt = {"016951", "016969", "016977", "016985", "016993", "017001"}
    log("自治体 (北方領土を除く)", int((~M.index.isin(nt)).sum()), M[~M.index.isin(nt)].value_counts().to_dict())
    log("所属未定地", Pf[Pf.n03_code.str.endswith("000")][["n03_code", "name", "area_km2"]].to_dict("records"))
    json.dump({"n03": N03_VERSION, "main_components": {str(k): v for k, v in main_comp.items()},
               "classes": M.to_dict()},
              open(WORK / "muni_island_class.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    T0 = time.time()
    main()
