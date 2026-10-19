#!/usr/bin/env python3
"""金沢市の緯度経度の判定 (resolve.Kanazawa.locate) に使うデータを、Worker 用のパックとタイルにする (build_packs.py から呼ぶ)

  loc   172014/loc.<sha>.bin.gz             区域 (955) と国勢調査の町 (901) の多角形、市の区域 (国勢調査の町を合わせたもの)、
                                            隣の市町との境界 (N03、海岸を除く)、住居表示住所の無い区域表の行の点 (STRtree の順)、町名の表、判定の定数
  タイル 172014/loc/<tx>_<ty>.<sha>.bin.gz   平面直角座標系 VII 系 (EPSG:6675) の 2km 四方に余白 450m を足した範囲の、住居表示住所の点・
                                            ABR の地番の点 (元の緯度経度。1e-9 度の整数) と、その範囲にかかる区域・町の番号

多角形と線の座標は、Python (geopandas・pyproj) が計算した EPSG:6675 の値を float64 のまま入れる。点は元の緯度経度 (国土地理院・ABR の
10進9桁まで) を入れ、Worker が Python と同じ式 (PROJ の tmerc) で平面に直す (pyproj との差は最大 1.4e-9 m ほど)。距離は shapely (GEOS) と
同じ式で測る (座標の差のぶん、境目から 1e-9 m 以内の距離だけは丸め・「何m 以内」が Python と違いうる。worker/DESIGN.md)。
1つの答えに要るのは loc と、地点のあるタイル1つだけ。タイルの点を探す半径は最大でも 地番 80m + 誤差 300m = 380m で、余白 450m に収まる
(住居表示住所の無い区域表の行の点 (120m + 誤差) は loc に全部入れる)。全タイル・全点で足りることは worker/test/locate.test.ts で確かめる。
隣の市町との境界までの距離は、gps2city の /location2 では Python と同じ値にならない (境界の線の版と間引き方が違う。
境界の近くの 60地点で差の中央値 0.21m・最大 23.7m、0.1m まで同じは 11地点) ので、境界の線 (4,540点) を loc に入れる。

形式 (gomisute-binpack/1): "GPK1"・ヘッダーの長さ (u32 LE)・ヘッダー (UTF-8 の JSON)・8バイト境界まで 0・配列 (LE、8バイト境界)。
ヘッダーの sections に 名前 → [型 (f64/i32/i16/u16), データの頭からの位置 (バイト), 要素の数]。sha256 は gzip する前のバイト列。
"""
import collections, json, math, re, struct
import numpy as np, shapely

TILE_M = 2000.0     # タイルの大きさ (m)。約 0.02°
HALO_M = 450.0      # タイルの余白 (m)。タイルの点を探す半径の最大 (PARCEL_NEAR_M + MAX_SEARCH_M = 380m) より大きく (BLIND_M + MAX_SEARCH_M = 420m よりも)
PURE = re.compile(r"[1-9][0-9]*")

def bin_pack(header, arrays):
    """ヘッダー (dict) と配列 (名前 → numpy 配列) → バイト列"""
    secs, chunks, off = {}, [], 0
    for name, arr in arrays.items():
        dt = {"float64": "f64", "int32": "i32", "int16": "i16", "uint16": "u16"}[arr.dtype.name]
        b = arr.astype(arr.dtype.newbyteorder("<"), copy=False).tobytes()
        pad = -len(b) % 8
        secs[name] = [dt, off, int(arr.size)]
        chunks.append(b + b"\0" * pad)
        off += len(b) + pad
    h = json.dumps({**header, "sections": secs}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    head = b"GPK1" + struct.pack("<I", len(h)) + h
    return head + b"\0" * (-len(head) % 8) + b"".join(chunks)

def _polys(g):
    """Polygon / MultiPolygon → ([[リングの点の数, ...] (多角形ごと)], 座標 [x0, y0, x1, y1, ...])。リングは閉じた形 (最初の点を最後にも) のまま"""
    shape, xy = [], []
    for poly in getattr(g, "geoms", [g]):
        lens = []
        for ring in [poly.exterior, *poly.interiors]:
            c = shapely.get_coordinates(ring)
            lens.append(len(c))
            xy.append(c.ravel())
        shape.append(lens)
    return shape, np.concatenate(xy)

def _lines(g):
    shape, xy = [], []
    for line in getattr(g, "geoms", [g]):
        c = shapely.get_coordinates(line)
        shape.append(len(c))
        xy.append(c.ravel())
    return shape, np.concatenate(xy)

def _nano(strs):
    """緯度経度の文字列 → 1e-9 度の整数 (n / 1e9 が float(文字列) と同じ値になることを確かめる)"""
    from decimal import Decimal
    out = np.array([int(Decimal(v) * 10 ** 9) for v in strs], np.int64)
    for v, n in zip(strs, out): assert Decimal(v) * 10 ** 9 == int(n) and int(n) / 1e9 == float(v), v
    return out

def build(k, resolve):
    """resolve.Kanazawa (build_packs.py と同じもの。区域表の行の番号を addr のパックとそろえる) → (loc のヘッダー, loc の配列, {(tx, ty): (ヘッダー, 配列)})"""
    from townnorm import norm_town
    from kzrules import table_town
    rid = {id(r): i for i, r in enumerate(k.rules)}
    assert list(k.parts.index) == list(range(len(k.parts))), "区域の番号 (part.name) が 0 から順でない"
    assert k.parts.crs.to_epsg() == 6675 and k.towns.crs.to_epsg() == 6675

    # ---- 町名などの表
    jtowns, jt_of = [], {}
    for t, nt in zip(k.jukyo.town, k.jukyo.ntown):
        if t not in jt_of: jt_of[t] = len(jtowns); jtowns.append([t, nt])
    ptowns, pt_of = [], {}
    for t, nt in zip(k.parcels.town, k.parcels.ntown):
        if t not in pt_of:
            assert nt == norm_town(t)
            pt_of[t] = len(ptowns); ptowns.append([t, table_town(nt, k.by_town)])
    koaza, kz_of = [""], {"": 0}
    for z in k.parcels.koaza:
        if z not in kz_of: kz_of[z] = len(koaza); koaza.append(z)

    # ---- 住居表示住所の点 (国土地理院。resolve.py は経度・緯度の文字列を float にして EPSG:6668 → 6675)
    jlng, jlat = _nano(list(k.jukyo.lon)), _nano(list(k.jukyo.lat))
    jx, jy = shapely.get_x(k.jgeom), shapely.get_y(k.jgeom)
    j_t = np.array([jt_of[t] for t in k.jukyo.town], np.int64)
    j_b = np.array([int(b) for b in k.jukyo.blk], np.int64)               # 街区はすべて数字だけ (下で確かめる)
    j_n = np.array([int(n) if PURE.fullmatch(n) else -1 for n in k.jukyo.num], np.int64)
    j_r = np.array([rid[id(r)] if r is not None else -1 for r in (k._addr_district(i)[0] for i in range(len(k.jukyo)))], np.int64)
    for i, (t, b, n) in enumerate(zip(k.jukyo.town, k.jukyo.blk, k.jukyo.num)):
        assert k.jlabel[i] == f"{t}{b}番{n}号" and PURE.fullmatch(b), (i, k.jlabel[i])
        assert k._addr_district(i)[1] == bool(k.by_town.get(norm_town(t))), i

    # ---- 地番の点 (ABR 地番マスターの代表点)
    pc = k.parcels
    plng, plat = _nano(list(pc.rep_lon)), _nano(list(pc.rep_lat))
    px, py = shapely.get_x(k.pgeom), shapely.get_y(k.pgeom)
    p_t = np.array([pt_of[t] for t in pc.town], np.int64)
    p_k = np.array([kz_of[z] for z in pc.koaza], np.int64)
    p_n1 = np.array([int(n) for n in pc.prc_num1], np.int64)
    p_n2 = np.array([0 if n == "" else int(n) if PURE.fullmatch(n) else -1 for n in pc.prc_num2], np.int64)
    p_r = np.array([rid[id(r)] if r is not None else -1 for r, _ in k.prow], np.int64)
    for i, (t, z, n1, n2) in enumerate(zip(pc.town, pc.koaza, pc.prc_num1, pc.prc_num2)):
        assert k.plabel[i] == f"{t}{z}{n1}{('-' + n2) if n2 else ''}番地" and PURE.fullmatch(n1), (i, k.plabel[i])
        assert k.prow[i][1] == ptowns[pt_of[t]][1] and k.ptownname[i] == t, i

    # ---- 住居表示住所の無い区域表の行の点: STRtree の順 (Python は木を引いた順に見て、同じ距離なら先に見た方を先に並べる)
    order = k.ui_tree.query(shapely.box(-1e12, -1e12, 1e12, 1e12))
    assert sorted(order) == list(range(len(k.ui_geom)))
    ui = [[rid[id(k.ui_rows[i][0])], k.ui_rows[i][1]] for i in order]
    ui_xy = np.array([[k.ui_geom[i].x, k.ui_geom[i].y] for i in order], np.float64).ravel()

    # ---- 多角形と線 (EPSG:6675 の float64 のまま)
    city_shape, city_xy = _polys(k.city)
    border_shape, border_xy = _lines(k.land_border)
    parts, part_xy = [], []
    for i, (t, d, m, g) in enumerate(zip(k.parts.town, k.parts.district, k.parts.method, k.parts.geometry)):
        sh, xy = _polys(g)
        parts.append([t, norm_town(t), d, m, int(i in k.enclave_parts), sh]); part_xy.append(xy)
    towns, town_xy = [], []
    for t, h, g in zip(k.towns.ntown, k.towns.HCODE, k.towns.geometry):
        sh, xy = _polys(g)
        towns.append([t, int(h), sh]); town_xy.append(xy)
    unindexed = {t: [rid[id(r)] for r in rs] for t, rs in sorted(k.unindexed.items()) if rs}
    rows_raw = [[r["raw_blk"], r["raw_ban"]] for r in k.rules]
    R = resolve
    const = {"ADDR_M": R.ADDR_M, "ADDR_SURE_M": R.ADDR_SURE_M, "ADDR_FAR_M": R.ADDR_FAR_M, "ACC_CONF": R.ACC_CONF, "MAX_SEARCH_M": R.MAX_SEARCH_M,
             "AREA_ALONE_OK": R.AREA_ALONE_OK, "PARCEL_M": R.PARCEL_M, "PARCEL_NEAR_M": R.PARCEL_NEAR_M, "NEAR_M": R.NEAR_M, "BLIND_M": R.BLIND_M,
             "SRC_JUKYO": R.SRC_JUKYO, "SRC_PARCEL": R.SRC_PARCEL, "DECIDING": sorted(R.DECIDING), "RANK": R.RANK, "BASIS_JA": R.BASIS_JA,
             "SHOW_DETAIL": sorted(R.SHOW_DETAIL)}
    assert HALO_M > R.BLIND_M + R.MAX_SEARCH_M + 1 and HALO_M > R.PARCEL_NEAR_M + R.MAX_SEARCH_M + 1
    # 平面直角座標系 VII 系の式 (Worker の tmerc) を確かめる点: pyproj の値と、住居表示住所・地番の点の Python の座標
    import pyproj
    tr = pyproj.Transformer.from_crs(4326, 6675, always_xy=True)
    rng = np.random.default_rng(6675)
    check = []
    for lat, lng in zip(rng.uniform(36.2, 36.8, 300), rng.uniform(136.4, 137.0, 300)):
        x, y = tr.transform(float(lng), float(lat))
        check.append([float(lat), float(lng), x, y])
    for i in rng.choice(len(jx), 100, replace=False): check.append([int(jlat[i]) / 1e9, int(jlng[i]) / 1e9, float(jx[i]), float(jy[i])])
    for i in rng.choice(len(px), 100, replace=False): check.append([int(plat[i]) / 1e9, int(plng[i]) / 1e9, float(px[i]), float(py[i])])
    loc_header = {"format": "gomisute-binpack/1", "kind": "loc", "lg_code": R.LG, "data_version": k.data_version,
                  "crs": {"epsg": 6675, "proj": "tmerc", "lat_0": 36, "lon_0": 137.166666666667, "k_0": 0.9999, "a": 6378137.0, "rf": 298.257222101,
                          "check": check},
                  "tile": {"size_m": TILE_M, "halo_m": HALO_M}, "const": const,
                  "city": city_shape, "border": border_shape, "parts": parts, "towns": towns, "ui": ui, "unindexed": unindexed,
                  "jtowns": jtowns, "ptowns": ptowns, "koaza": koaza, "rows_raw": rows_raw}
    loc_arrays = {"city_xy": city_xy, "border_xy": border_xy, "ui_xy": ui_xy, "part_xy": np.concatenate(part_xy), "town_xy": np.concatenate(town_xy)}

    # ---- タイル: 市の区域にかかる 2km 四方ごと。点は余白の中のもの全部、区域・町はその範囲にかかるものの番号
    x0, y0, x1, y1 = k.city.bounds
    S, H = TILE_M, HALO_M
    keys = [(tx, ty) for tx in range(math.floor(x0 / S), math.floor(x1 / S) + 1) for ty in range(math.floor(y0 / S), math.floor(y1 / S) + 1)
            if k.city.intersects(shapely.box(tx * S, ty * S, (tx + 1) * S, (ty + 1) * S))]
    keyset = set(keys)

    def members(xs, ys):
        out = collections.defaultdict(list)
        for i, (x, y) in enumerate(zip(xs, ys)):
            for tx in range(math.floor((x - H) / S), math.floor((x + H) / S) + 1):
                if not (tx * S - H <= x <= (tx + 1) * S + H): continue
                for ty in range(math.floor((y - H) / S), math.floor((y + H) / S) + 1):
                    if ty * S - H <= y <= (ty + 1) * S + H and (tx, ty) in keyset: out[(tx, ty)].append(i)
        return out
    jm, pm = members(jx, jy), members(px, py)
    part_tree, town_tree = shapely.STRtree(k.parts.geometry.values), shapely.STRtree(k.towns.geometry.values)
    tiles = {}
    for tx, ty in keys:
        box = shapely.box(tx * S - H, ty * S - H, (tx + 1) * S + H, (ty + 1) * S + H)
        hdr = {"format": "gomisute-binpack/1", "kind": "loc_tile", "lg_code": R.LG, "tile": [tx, ty],
               "parts": sorted(int(i) for i in part_tree.query(box, predicate="intersects")),
               "towns": sorted(int(i) for i in town_tree.query(box, predicate="intersects"))}
        arr = {}
        j = np.array(sorted(jm.get((tx, ty), [])), np.int64)
        p = np.array(sorted(pm.get((tx, ty), [])), np.int64)
        lng0 = int(min([*jlng[j], *plng[p]], default=0))
        lat0 = int(min([*jlat[j], *plat[p]], default=0))
        hdr["origin_nano"] = [lng0, lat0]
        for name, idx, lng, lat in (("j", j, jlng, jlat), ("p", p, plng, plat)):
            d_lng, d_lat = lng[idx] - lng0, lat[idx] - lat0
            assert len(idx) == 0 or (d_lng.max() < 2 ** 31 and d_lat.max() < 2 ** 31)
            arr[f"{name}_lng"], arr[f"{name}_lat"] = d_lng.astype(np.int32), d_lat.astype(np.int32)
            arr[f"{name}_i"] = idx.astype(np.int32)
        arr.update({"j_t": j_t[j].astype(np.uint16), "j_b": j_b[j].astype(np.int32), "j_n": j_n[j].astype(np.int32), "j_r": j_r[j].astype(np.int16)})
        hdr["j_nstr"] = {str(a): k.jukyo.num.iat[int(i)] for a, i in enumerate(j) if j_n[i] < 0}
        arr.update({"p_t": p_t[p].astype(np.uint16), "p_k": p_k[p].astype(np.uint16), "p_n1": p_n1[p].astype(np.int32),
                    "p_n2": p_n2[p].astype(np.int32), "p_r": p_r[p].astype(np.int16)})
        hdr["p_n2str"] = {str(a): pc.prc_num2.iat[int(i)] for a, i in enumerate(p) if p_n2[i] < 0}
        tiles[(tx, ty)] = (hdr, arr)
    assert len(jtowns) < 65536 and len(ptowns) < 65536 and len(koaza) < 65536 and len(k.rules) < 32768
    assert len(k.jukyo) < 2 ** 31 and len(k.parcels) < 2 ** 31
    return loc_header, loc_arrays, tiles
