// 金沢市: 緯度経度 → 地区の候補と判定 (resolve.Kanazawa.locate の移植)。根拠の集め方・順番・文面・数の丸めも Python と同じ
// データは build_loc.py が作った loc (区域・町の多角形、市の区域、隣の市町との境界、住居表示住所の無い区域表の行の点、表) と、
// 地点のあるタイル (2km 四方 + 余白 450m の住居表示住所・地番の点) 1つ
import { type Polys, type Projector, contains, linesDistance, polyWithin, polysOf, ptDist, readBinPack, tmerc } from "../geo.ts";
import { cmpPy, cps, pf, pyFloatRepr, pyRound, pyRoundInt, sortedPy, truthy } from "../py.ts";
import { normTown, type AddressIndex, type Row } from "./address.ts";
import { Kanazawa } from "./engine.ts";

type Part = { town: string; ntown: string; district: string; method: string; enclave: boolean; g: Polys };
type Town = { ntown: string; hcode: number; g: Polys };
const DEG = 0.017453292519943295;

export class LocIndex {
  h: any;
  C: any;
  proj: Projector;
  S: number;
  H: number;
  cityXy: Float64Array;
  city: Polys;
  borderXy: Float64Array;
  borderLens: number[];
  partXy: Float64Array;
  parts: Part[];
  townXy: Float64Array;
  towns: Town[];
  uiXy: Float64Array;
  ui: [number, string][];
  unindexed: Record<string, number[]>;
  jtowns: [string, string][];
  ptowns: [string, string | null][];
  koaza: string[];
  rowsRaw: [string, string][];
  deciding: Set<string>;

  constructor(bytes: Uint8Array) {
    const b = readBinPack(bytes);
    const h = b.header;
    if (h.kind !== "loc") throw new Error("loc のパックではない");
    this.h = h;
    this.C = h.const;
    this.proj = tmerc(h.crs);
    this.S = h.tile.size_m;
    this.H = h.tile.halo_m;
    this.cityXy = b.f64("city_xy");
    this.city = polysOf(h.city, this.cityXy, 0)[0];
    this.borderXy = b.f64("border_xy");
    this.borderLens = h.border;
    this.partXy = b.f64("part_xy");
    let at = 0;
    this.parts = h.parts.map(([town, ntown, district, method, enclave, shape]: any[]) => {
      const [g, next] = polysOf(shape, this.partXy, at);
      at = next;
      return { town, ntown, district, method, enclave: !!enclave, g };
    });
    this.townXy = b.f64("town_xy");
    at = 0;
    this.towns = h.towns.map(([ntown, hcode, shape]: any[]) => {
      const [g, next] = polysOf(shape, this.townXy, at);
      at = next;
      return { ntown, hcode, g };
    });
    this.uiXy = b.f64("ui_xy");
    this.ui = h.ui;
    this.unindexed = h.unindexed;
    this.jtowns = h.jtowns;
    this.ptowns = h.ptowns;
    this.koaza = h.koaza;
    this.rowsRaw = h.rows_raw;
    this.deciding = new Set(this.C.DECIDING);
    if (this.H <= this.C.BLIND_M + this.C.MAX_SEARCH_M) throw new Error("タイルの余白が根拠を探す半径より小さい");
  }

  /** 平面の座標 → タイルの名前 */
  tileName(x: number, y: number): string { return `${Math.floor(x / this.S)}_${Math.floor(y / this.S)}`; }

  /** resolve の k.city.contains (国勢調査の町を合わせた市の区域の中か) */
  inCity(x: number, y: number): boolean { return Number.isFinite(x) && contains(this.cityXy, this.city, x, y); }
}

type PointSet = {
  lng: Int32Array; lat: Int32Array; i: Int32Array; r: Int16Array; t: Uint16Array;
  xy: Float64Array; done: Uint8Array;
};

export class LocTile {
  name: string;
  lng0: number;
  lat0: number;
  parts: number[];
  towns: number[];
  j: PointSet & { b: Int32Array; n: Int32Array; nstr: Record<string, string> };
  p: PointSet & { k: Uint16Array; n1: Int32Array; n2: Int32Array; n2str: Record<string, string> };

  constructor(bytes: Uint8Array) {
    const b = readBinPack(bytes);
    const h = b.header;
    if (h.kind !== "loc_tile") throw new Error("loc のタイルではない");
    this.name = `${h.tile[0]}_${h.tile[1]}`;
    [this.lng0, this.lat0] = h.origin_nano;
    this.parts = h.parts;
    this.towns = h.towns;
    const set = (p: string) => {
      const lng = b.i32(`${p}_lng`);
      return { lng, lat: b.i32(`${p}_lat`), i: b.i32(`${p}_i`), r: b.i16(`${p}_r`), t: b.u16(`${p}_t`),
        xy: new Float64Array(lng.length * 2), done: new Uint8Array(lng.length) };
    };
    this.j = { ...set("j"), b: b.i32("j_b"), n: b.i32("j_n"), nstr: h.j_nstr };
    this.p = { ...set("p"), k: b.u16("p_k"), n1: b.i32("p_n1"), n2: b.i32("p_n2"), n2str: h.p_n2str };
  }

  /** 地点から r (m) 以内の点 → [距離, 全体の番号, タイルの中の番号] を (距離, 番号) の順に (resolve の STRtree + sorted と同じ) */
  near(ps: PointSet, proj: Projector, qx: number, qy: number, qlat: number, qlng: number, r: number): [number, number, number][] {
    // 緯度経度の四角で先に絞る (1度 = 10万m として広めに。平面の距離はこれより必ず長い)
    const dLat = Math.ceil(r * 1e4) + 1, dLng = Math.ceil((r * 1e4) / Math.cos(qlat * DEG)) + 1;
    const cLat = Math.round(qlat * 1e9) - this.lat0, cLng = Math.round(qlng * 1e9) - this.lng0;
    const out: [number, number, number][] = [];
    for (let a = 0; a < ps.lng.length; a++) {
      if (Math.abs(ps.lat[a] - cLat) > dLat || Math.abs(ps.lng[a] - cLng) > dLng) continue;
      if (!ps.done[a]) {
        const [x, y] = proj((this.lat0 + ps.lat[a]) / 1e9, (this.lng0 + ps.lng[a]) / 1e9);
        ps.xy[2 * a] = x; ps.xy[2 * a + 1] = y; ps.done[a] = 1;
      }
      const d = ptDist(ps.xy[2 * a], ps.xy[2 * a + 1], qx, qy);
      if (d <= r) out.push([d, ps.i[a], a]);
    }
    return out.sort((u, v) => u[0] - v[0] || u[1] - v[1]);
  }
}

const f1 = (v: number) => pyFloatRepr(pyRound(v, 1));        // f"{round(v, 1)}"
const rnd1 = (v: number) => pf(pyRound(v, 1));                 // round(v, 1) (float)

/** resolve.search_radius */
export function searchRadius(acc: number | null, confidence: number, target = 0.99): number {
  if (!acc) return 0.0;
  return acc * Math.sqrt(Math.log(1 - target) / Math.log(1 - confidence));
}

/**
 * resolve.Kanazawa.locate: 平面の座標 (x, y) と緯度経度 → 地区の判定 (市の区域の外なら null)。
 * tile は地点のあるタイル (無ければ市の外)
 */
export function locate(k: Kanazawa, L: LocIndex, tile: LocTile | null, x: number, y: number, lat: number, lng: number,
  accuracyM: number | null, accuracyConf: number | null): any {
  if (!tile || !L.inCity(x, y)) return null;
  const C = L.C;
  const ax: AddressIndex = k.ax!;
  const byTown = ax.byTown;
  const rowsOf = (t: string | null) => (t && byTown.get(t)) || [];
  const R = (r: Row) => Kanazawa.row(r);
  const rowsD = (t: string, d?: string) => rowsOf(t).filter((r) => d === undefined || r.district === d).map(R);
  const districts = (t: string) => sortedPy(new Set(rowsOf(t).map((r) => r.district)));
  const uniform = (t: string | null) => (t ? truthy(ax.uniform.get(t)) : false);
  const raw = (r: Row) => L.rowsRaw[r.id];

  const acc = searchRadius(accuracyM, accuracyConf || C.ACC_CONF);
  const addrR = C.ADDR_M + acc, farR = C.ADDR_FAR_M + acc, nbR = C.NEAR_M + acc;
  const pnR = C.PARCEL_NEAR_M + acc, blindR = C.BLIND_M + acc;
  if (blindR > L.H || farR > L.H || pnR > L.H) throw new Error("根拠を探す半径がタイルの余白より大きい");
  const cands = new Map<string, any>();
  const notes: string[] = [];
  const unlisted: any[] = [];
  const add = (d: string, kind: string, evidence: string, official = true, rows: any[] = []) => {
    let c = cands.get(d);
    if (!c) { c = { collection_district: d, basis: [], official: false, evidence: [], how_to_confirm: [] }; cands.set(d, c); }
    c.basis.push({ kind, official, label: C.BASIS_JA[kind], detail: evidence });
    c.official = c.basis.some((b: any) => b.official);
    c.evidence.push(evidence);
    for (const r of rows) {
      const rk = JSON.stringify(r);
      if (!c.how_to_confirm.some((h: any) => JSON.stringify(h) === rk) && c.how_to_confirm.length < 8) c.how_to_confirm.push(r);
    }
  };
  const has = (d: string, kinds: string[]) => cands.has(d) && cands.get(d).basis.some((b: any) => kinds.includes(b.kind));

  // (1) 区域ポリゴン (区域の順で最初に当たるもの)
  let part: (Part & { idx: number }) | null = null;
  for (const i of tile.parts) {
    if (contains(L.partXy, L.parts[i].g, x, y)) { part = { ...L.parts[i], idx: i }; break; }
  }
  const ptown = part ? normTown(part.town) : null;
  if (part) {
    if (part.method === "points_single" && !uniform(ptown)) {
      for (const dd of districts(ptown!)) add(dd, "town_row", `区域表で ${part.town} の一部が ${dd}`, true, rowsD(ptown!, dd));
    } else {
      const how = ({ chome: "町丁目まるごと同じ地区", voronoi: "住居表示住所の点で町を分けた区域", points_single: "町内の行がすべて同じ地区" } as Record<string, string>)[part.method];
      add(part.district, "zone_polygon", `${part.town} の区域 (${how})`, true, rowsD(ptown!, part.district));
    }
  }

  // (2) 近くの住居表示住所 → 区域表の行
  const J = tile.j;
  const jlabel = (a: number) => `${L.jtowns[J.t[a]][0]}${J.b[a]}番${J.n[a] >= 0 ? J.n[a] : J.nstr[String(a)]}号`;
  const jtown = (a: number) => L.jtowns[J.t[a]][0];
  const jntown = (a: number) => L.jtowns[J.t[a]][1];
  const jrow = (a: number): Row | null => (J.r[a] >= 0 ? ax.rows[J.r[a]] : null);
  const jinTable = (a: number) => (byTown.get(jntown(a)) ?? []).length > 0;
  const nearAll = tile.near(J, L.proj, x, y, lat, lng, farR);
  const near = nearAll.filter(([d]) => d <= addrR);
  let farAddr: any = null;
  if (nearAll.length && !near.length) {
    const [d, , a] = nearAll[0];
    farAddr = { label: jlabel(a), town: jtown(a), distance_m: rnd1(d), in_table: jinTable(a) };
  }
  let addr: any = null;
  if (near.length) {
    const [d0, i0, a0] = near[0];
    const row = jrow(a0), inTable = jinTable(a0);
    addr = { label: jlabel(a0), town: jtown(a0), distance_m: rnd1(d0), source: C.SRC_JUKYO, in_rules_table: row === null ? inTable : true };
    if (!inTable) { /* 町が区域表に無い */ }
    else if (row === null) {
      addr.in_rules_table = "town_only";
      notes.push(`区域表の ${addr.town} の行は住居表示の街区・番号では引けません (小字・地番で分かれているか、表の抜け)`);
      for (const dd of districts(jntown(a0))) add(dd, "town_row", `区域表で ${addr.town} の一部が ${dd}`, true, rowsD(jntown(a0), dd));
    } else {
      addr.matched_district = row.district;
      if (d0 <= C.ADDR_SURE_M) add(row.district, "address_row", `最寄りの住所 ${addr.label} (${f1(d0)}m) が区域表の行に当たる`, true, [R(row)]);
    }
    for (const [d, i, a] of near) {
      const r2 = jrow(a), t2 = jinTable(a);
      if (!t2) { unlisted.push({ town: jtown(a), source: "住居表示住所", label: jlabel(a), distance_m: rnd1(d) }); continue; }
      if (i === i0 && d0 <= C.ADDR_SURE_M) continue;
      const nt = jntown(a);
      for (const dd of r2 ? [r2.district] : districts(nt)) {
        if (has(dd, ["address_row", "near_address"])) continue;
        add(dd, "near_address", `${f1(d)}m 先の住所 ${jlabel(a)} が区域表で ${dd}` + (r2 ? "" : " (の場合あり)"), true, r2 ? [R(r2)] : rowsD(nt, dd).slice(0, 2));
      }
    }
  }

  // (3) 近くの地番 (ABR 地番代表点、住居表示の無い筆) → 区域表の行
  const P = tile.p;
  const plabel = (a: number) => `${L.ptowns[P.t[a]][0]}${L.koaza[P.k[a]]}${P.n1[a]}${P.n2[a] === 0 ? "" : "-" + (P.n2[a] > 0 ? P.n2[a] : P.n2str[String(a)])}番地`;
  const ptownName = (a: number) => L.ptowns[P.t[a]][0];
  const ptt = (a: number) => L.ptowns[P.t[a]][1];
  const prow = (a: number): Row | null => (P.r[a] >= 0 ? ax.rows[P.r[a]] : null);
  const Rp = Math.max(C.PARCEL_M, pnR);
  const pnear = tile.near(P, L.proj, x, y, lat, lng, Rp);
  let parcel: any = null;
  if (pnear.length && pnear[0][0] <= C.PARCEL_M) {
    const [d0, , a0] = pnear.shift()!;
    const rowP = prow(a0), tt = ptt(a0);
    parcel = { label: plabel(a0), town: ptownName(a0), distance_m: rnd1(d0), source: C.SRC_PARCEL, in_rules_table: rowP === null ? !!tt : true };
    if (!tt) {
      if (d0 <= addrR) unlisted.push({ town: parcel.town, source: "地番", label: parcel.label, distance_m: parcel.distance_m });
    } else if (rowP === null) {
      parcel.in_rules_table = "town_only";
      notes.push(`区域表の ${tt} の行では、地番 ${parcel.label} の地区を言い切れません (条件付きの行・小字の書き方の違いなど)`);
      for (const dd of districts(tt)) add(dd, "town_row", `区域表で ${tt} の一部が ${dd}`, true, rowsD(tt, dd));
    } else {
      parcel.matched_district = rowP.district;
      add(rowP.district, "parcel_row", `最寄りの地番 ${parcel.label} (${f1(d0)}m) が区域表の行に当たる`, true, [R(rowP)]);
      if (!uniform(tt)) {
        notes.push(`${tt} は区域表で地区が分かれます。住居表示の無い地域では小字ごと位置情報が無いことがあり、`
          + "最寄りの位置つきの地番が利用者の地番とは限らないため、地番だけでは確定しません");
        for (const dd of districts(tt)) add(dd, "town_row", `区域表で ${tt} の一部が ${dd}`, true, rowsD(tt, dd));
      }
    }
  }
  for (const [d, , a] of pnear) {
    if (d > pnR) break;
    const r2 = prow(a), t2 = ptt(a);
    if (!t2) {
      if (d <= addrR) unlisted.push({ town: ptownName(a), source: "地番", label: plabel(a), distance_m: rnd1(d) });
      continue;
    }
    for (const dd of uniform(t2) ? [r2!.district] : districts(t2)) {
      if (has(dd, ["parcel_row", "near_parcel"])) continue;
      const sure = r2 !== null && r2.district === dd;
      add(dd, "near_parcel", `${f1(d)}m 先の地番 ${plabel(a)} ` + (sure ? `が区域表で ${dd}` : `の町 ${t2} の一部が区域表で ${dd}`), true,
        sure ? [R(r2!)] : rowsD(t2, dd).slice(0, 2));
    }
  }

  // (4) 区域ポリゴンが無い場所: この地点を含む国勢調査の町 (水面 8154 は町として扱わない)
  let town: string | null = null, water: string | null = null;
  for (const i of tile.towns) {
    const t = L.towns[i];
    if ((t.hcode === 8101 ? town : water) !== null) continue;
    if (contains(L.townXy, t.g, x, y)) { if (t.hcode === 8101) town = t.ntown; else water = t.ntown; }
  }
  const waterName = water !== null && !town ? `${water} (水面)` : null;
  let inference: string | null = null, censusMissing = false;
  if (!part && town) {
    if (rowsOf(town).length) {
      for (const dd of districts(town)) add(dd, "census_town_row", `この地点を含む国勢調査の町 ${town} は区域表で ${dd} (一部の場合あり)`, true, rowsD(town, dd));
    } else {
      const t0 = town;
      const rel = sortedPy([...byTown.keys()].filter((kk) => kk !== t0 && cps(kk).length >= 2 && (t0.startsWith(kk) || kk.startsWith(t0))));
      const whole: [string, Row][] = rel.flatMap((kk) => byTown.get(kk)!.filter((r) => r.kind === "town").map((r) => [kk, r] as [string, Row]));
      for (const [kk, r] of whole) add(r.district, "town_row_prefix", `区域表に ${town} の行は無く、よく似た町名 ${kk} の行 (町全体) から推定`, true, [R(r)]);
      for (const kk of rel) {
        for (const r of byTown.get(kk)!) {
          if (r.kind === "town") continue;
          add(r.district, "conditional_similar_row", `よく似た町名の行: ${kk} 街区${raw(r)[0]} ${raw(r)[1]} → ${r.district}`.replaceAll("  ", " "), false, [R(r)]);
        }
      }
      if (whole.length) inference = `区域表に ${town} の行は無く、よく似た町名 ${sortedPy(new Set(whole.map(([kk]) => kk))).join("・")} の行 (町全体) から推定`;
      else censusMissing = true;
    }
  }

  // (5) 住居表示住所が1つも無い区域表の行 (STRtree を引いた順に見て、説明ごとに一番近いもの)
  const ui = new Map<string, [number, number]>();
  for (let u = 0; u < L.ui.length; u++) {
    const d = ptDist(L.uiXy[2 * u], L.uiXy[2 * u + 1], x, y);
    const [rid, desc] = L.ui[u];
    if (d <= blindR && (!ui.has(desc) || d < ui.get(desc)![0])) ui.set(desc, [d, rid]);
  }
  for (const [desc, [d, rid]] of [...ui.entries()].sort((u, v) => u[1][0] - v[1][0])) {
    const r = ax.rows[rid];
    if (has(r.district, ["unindexed_row"])) continue;
    add(r.district, "unindexed_row", `${pyRoundInt(d)}m 先に ${desc} → ${r.district}`.replaceAll("  ", " "), true, [R(r)]);
  }
  for (const t of [...new Set([ptown, town].filter((v): v is string => !!v))]) {
    for (const rid of L.unindexed[t] ?? []) {
      const r = ax.rows[rid];
      add(r.district, "unindexed_row", `区域表の行 ${r.town} 街区${raw(r)[0]} ${raw(r)[1]} → ${r.district} (住居表示住所も位置情報も無い)`.replaceAll("  ", " "), true, [R(r)]);
    }
  }

  // (6) 地区境界のそば
  if (part) {
    const nb = new Set<string>();
    for (const i of tile.parts) {
      const q = L.parts[i];
      if (q.district !== part.district && polyWithin(L.partXy, q.g, x, y, nbR) !== null) nb.add(q.district);
    }
    for (const dd of sortedPy(nb)) add(dd, "near_boundary", `${pyRoundInt(nbR)}m 以内に ${dd} 地区の区域がある`);
  }
  if (acc > 0) {                                              // 誤差の範囲にかかる国勢調査の町
    const ct: [string, number][] = [];
    for (const i of tile.towns) {
      const t = L.towns[i];
      if (t.hcode !== 8101 || t.ntown === town) continue;
      const d = polyWithin(L.townXy, t.g, x, y, nbR);
      if (d !== null) ct.push([t.ntown, d]);
    }
    ct.sort((u, v) => cmpPy(u[0], v[0]) || u[1] - v[1]);
    for (const [t, dist] of ct.filter((v, i) => !i || v[0] !== ct[i - 1][0] || v[1] !== ct[i - 1][1])) {
      if (rowsOf(t).length) {
        for (const dd of districts(t)) {
          if (!has(dd, ["near_boundary"])) add(dd, "near_boundary", `誤差の範囲 (約${pyRoundInt(nbR)}m) に国勢調査の町 ${t} (${dd} を含む)`);
        }
      } else {
        const whole: [string, Row][] = [];
        for (const [k2, rs] of byTown) {
          if (k2 === t || cps(k2).length < 2 || !(t.startsWith(k2) || k2.startsWith(t))) continue;
          for (const r of rs) if (r.kind === "town") whole.push([k2, r]);
        }
        for (const [k2, r] of whole) add(r.district, "near_boundary", `誤差の範囲に国勢調査の町 ${t} (区域表には無く、よく似た町名 ${k2} の行 (町全体) が ${r.district})`);
        if (!whole.length) unlisted.push({ town: t, source: "国勢調査の町", label: t, distance_m: rnd1(dist) });
      }
    }
  }

  // (7) この場所の町: 最寄りの住所 (ADDR_M 以内) か地番 (PARCEL_M 以内) のうち近い方。無ければ国勢調査の町
  const dm = (o: any) => o.distance_m.v as number;
  const hereCands = [addr && dm(addr) <= C.ADDR_M ? addr : null, parcel].filter((o) => o);
  const here: any = hereCands.length ? hereCands.reduce((m, o) => (dm(o) < dm(m) ? o : m)) : null;
  let placeName: string | null = (here ? here.town : null) || town || waterName;
  const dBorder = linesDistance(L.borderXy, L.borderLens, x, y);
  let blocked: any = null;
  if (here && here.in_rules_table === false) {
    blocked = { reason: "place_town_not_in_rules_table", town: here.town, source: here === addr ? "住居表示住所" : "地番", label: here.label, distance_m: here.distance_m };
  } else if (!here && !addr && farAddr && !farAddr.in_table) {
    blocked = { reason: "place_town_not_in_rules_table", town: farAddr.town, source: "住居表示住所", label: farAddr.label, distance_m: farAddr.distance_m };
    placeName = farAddr.town;
  } else if (censusMissing) {
    blocked = { reason: "census_town_not_in_rules_table", town, source: "国勢調査の町 (この地点を含む)" };
  } else if (dBorder <= nbR) {
    blocked = { reason: "near_municipal_boundary", town: null, source: "国土数値情報 行政区域 (N03)", distance_m: rnd1(dBorder) };
  } else if (unlisted.length || (farAddr && !farAddr.in_table)) {
    const us = [...unlisted, ...(farAddr && !farAddr.in_table ? [{ town: farAddr.town, source: "住居表示住所", label: farAddr.label, distance_m: farAddr.distance_m }] : [])];
    const u = us.reduce((m, o) => (dm(o) < dm(m) ? o : m));
    blocked = { reason: "nearby_town_not_in_rules_table", ...u };
  }
  if (blocked && blocked.town) {
    for (const [aname, confidence, rid] of ax.viaOld[normTown(blocked.town)] ?? []) {
      add(ax.rows[rid].district, "inferred_lineage", `区域表に ${blocked.town} の行が無い。旧町名 ${aname} の行から推定 (系譜の確信度 ${confidence})`, false);
    }
  }

  // ---- 判定
  const rankOf = (c: any) => Math.max(...c.basis.map((b: any) => C.RANK[b.kind] * (b.official ? 1 : 0.5)));
  const ordered = [...cands.values()].map((c) => [c, rankOf(c)] as [any, number]).sort((u, v) => v[1] - u[1]).map((u) => u[0]);
  const official = ordered.filter((c) => c.official);
  const kinds = (c: any) => new Set<string>(c.basis.filter((b: any) => b.official).map((b: any) => b.kind));
  const deciding = (c: any) => [...kinds(c)].some((kk) => L.deciding.has(kk));
  let status = "ambiguous", district: string | null = null, conf = official.length === 2 ? 0.6 : 0.4, method = "candidates";
  if (blocked) {
    status = "unresolved"; conf = 0.0; method = blocked.reason;
  } else if (!official.length) {
    status = "unresolved"; conf = 0.0; method = "no_official_row";
    blocked = { reason: "no_official_row", town: null };
  } else if (official.length === 1 && deciding(official[0]) && official[0].how_to_confirm.some((h: any) => h["注記"])) {
    const h = official[0].how_to_confirm.find((hh: any) => hh["注記"]);
    status = "unresolved"; conf = 0.0; method = "rules_table_note";
    blocked = { reason: "rules_table_note", town: h["町名"], district: h["地区"], note: h["注記"] };
  } else if (official.length === 1 && deciding(official[0]) && !C.AREA_ALONE_OK && addr === null && parcel === null
    && [...kinds(official[0])].every((kk) => ["zone_polygon", "census_town_row", "town_row", "town_row_prefix", "near_boundary", "unindexed_row"].includes(kk))) {
    method = "area_only_evidence";
  } else if (official.length === 1 && deciding(official[0])) {
    const c = official[0], ks = kinds(c);
    district = c.collection_district; status = "ok";
    const clean = part !== null && part.method === "chome" && !part.enclave;
    if (ks.has("address_row") && ks.has("zone_polygon")) {
      conf = clean && normTown(addr.town) === ptown ? 0.97 : 0.93; method = "address_and_zone_agree";
    } else if (ks.has("parcel_row") && ks.has("zone_polygon")) {
      conf = clean ? 0.93 : 0.9; method = "parcel_and_zone_agree";
    } else if (ks.has("zone_polygon")) {
      conf = ({ chome: clean ? 0.95 : 0.9, points_single: 0.9, voronoi: 0.85 } as Record<string, number>)[part!.method]; method = "zone_polygon";
    } else if (ks.has("address_row") || ks.has("parcel_row")) {
      conf = 0.85; method = ks.has("address_row") ? "address_row" : "parcel_row";
    } else if (ks.has("town_row") || ks.has("census_town_row")) {
      conf = 0.8; method = "town_row";
    } else {
      conf = 0.7; method = "town_row_prefix";
    }
  } else if (official.length === 1) {
    method = "only_nearby_evidence";
  }
  const rowsTown = [here ? normTown(here.town) : null, ptown, town].find((t) => t && rowsOf(t).length)
    || (parcel ? ax.tableTown(normTown(parcel.town)) : null) || "";
  const simRows = censusMissing ? ordered.flatMap((c) => c.how_to_confirm) : [];
  const tr = rowsD(rowsTown);
  return {
    status, collection_district: district, district_confidence: pf(pyRound(conf, 2)), method,
    census_town: town, place_name: placeName, inference: status === "ok" && method === "town_row_prefix" ? inference : null,
    nearest_address: addr && dm(addr) <= C.ADDR_M ? addr : null, nearest_parcel: parcel,
    decision_blocked_by: blocked, distance_to_municipal_border_m: rnd1(dBorder),
    evidence_radii_m: { address: rnd1(addrR), parcel: rnd1(pnR), zone_boundary: rnd1(nbR) },
    candidates: ordered, notes, town_rows: (tr.length ? tr : simRows).slice(0, 12),
  };
}

