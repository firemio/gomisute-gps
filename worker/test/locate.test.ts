// 緯度経度の判定の部品: 平面直角座標系の式 (pyproj と同じ値か)・バイナリのパック・タイルの余白
import { test } from "node:test";
import assert from "node:assert/strict";
import { MANIFEST, kanazawaLoc, kanazawaTile } from "../src/deps.ts";
import { type Polys, contains, readBinPack, segDist, tmerc } from "../src/geo.ts";
import { LocTile } from "../src/kanazawa/locate.ts";
import { goldenDeps, readBytes } from "./helpers.ts";

const deps = goldenDeps("fresh");

test("tmerc (PROJ の exact) が pyproj と 1e-6 m 以内で同じ (確かめる点 500)", async () => {
  const L = await kanazawaLoc(deps);
  const proj = tmerc(L.h.crs);
  let worst = 0;
  for (const [lat, lng, x, y] of L.h.crs.check) {
    const [gx, gy] = proj(lat, lng);
    worst = Math.max(worst, Math.abs(gx - x), Math.abs(gy - y));
  }
  console.log(`  pyproj との差の最大 ${worst.toExponential(2)} m (${L.h.crs.check.length} 点)`);
  assert.ok(worst < 1e-6, `差が大きい: ${worst}`);
});

test("距離の式は GEOS と同じ形 (端・線分の内側・長さ 0 の線分)", () => {
  assert.equal(segDist(0, 0, 3, 4, 3, 4), 5);
  assert.equal(segDist(0, 1, -1, 0, 1, 0), 1);
  assert.equal(segDist(5, 0, -1, 0, 1, 0), 4);
  assert.equal(segDist(-3, 4, 0, 0, 10, 0), 5);
});

test("GPK1 のパック: ヘッダーと配列 (8バイト境界でない場所からも読める)", async () => {
  const p = MANIFEST.municipalities["172014"].packs.loc;
  const raw = await deps.bytes(p.key, p.sha256);
  const shifted = new Uint8Array(raw.length + 3).subarray(3);
  shifted.set(raw);
  const a = readBinPack(raw), b = readBinPack(shifted);
  assert.equal(a.header.kind, "loc");
  assert.deepEqual([...b.f64("border_xy").slice(0, 6)], [...a.f64("border_xy").slice(0, 6)]);
  assert.throws(() => readBinPack(new TextEncoder().encode("{}")), /GPK1/);
  assert.throws(() => a.i32("border_xy"), /border_xy/);
});

test("タイル: 市の区域にかかる 2km 四方ごと。点・区域は余白 450m の中、無いタイルは null", async () => {
  const L = await kanazawaLoc(deps);
  const t = MANIFEST.municipalities["172014"].tiles.loc;
  assert.equal(Object.keys(t.files).length, t.count);
  assert.ok(L.H > L.C.BLIND_M + L.C.MAX_SEARCH_M);
  const names = Object.keys(t.files).sort();
  for (const name of [names[0], names[Math.floor(names.length / 2)], names[names.length - 1]]) {
    const tile = (await kanazawaTile(deps, name))!;
    assert.equal(tile.name, name);
    const [tx, ty] = name.split("_").map(Number);
    for (const ps of [tile.j, tile.p]) {
      for (let a = 0; a < ps.lng.length; a += 97) {
        const [x, y] = L.proj((tile.lat0 + ps.lat[a]) / 1e9, (tile.lng0 + ps.lng[a]) / 1e9);
        assert.ok(x >= tx * L.S - L.H - 1 && x <= (tx + 1) * L.S + L.H + 1 && y >= ty * L.S - L.H - 1 && y <= (ty + 1) * L.S + L.H + 1, `${name} の点が余白の外`);
      }
      for (let a = 1; a < ps.i.length; a++) assert.ok(ps.i[a - 1] < ps.i[a], "点は全体の番号の順");
    }
  }
  assert.equal(await kanazawaTile(deps, "0_0"), null);
  assert.equal(await kanazawaTile(deps, "__proto__"), null);
});

// ---------------------------------------------------------------- タイルの余白で足りるか (全タイル・全点・全多角形)
// golden の location は余白のうち約 250m までしか使わない (それより外の点を抜いても全件同じだった) ので、余白 450m で足りることはここで確かめる。
// 地点が 2km 四方の中で、誤差の半径が最大 (search_radius_m を 0.1m に丸めて MAX_SEARCH_M 以下 → MAX_SEARCH_M + 0.05m 未満) のとき、
// 根拠を探す半径 (住居表示住所 ADDR_FAR_M + 誤差・地番 PARCEL_NEAR_M + 誤差・区域と町 NEAR_M + 誤差) 以内にあるものが、すべてそのタイルにあること。
// 住居表示住所の無い区域表の行の点 (BLIND_M + 誤差) は loc に全部入っているので、タイルには関係しない

/** 点と四角 [x0, y0, x1, y1] の距離 (中なら 0) */
const rectDist = (x: number, y: number, r: number[]) => Math.hypot(Math.max(r[0] - x, 0, x - r[2]), Math.max(r[1] - y, 0, y - r[3]));

/** 線分 AB が線分 CD と交わるか (端の上も交わるとみなす) */
function crosses(ax: number, ay: number, bx: number, by: number, cx: number, cy: number, dx: number, dy: number): boolean {
  const o = (px: number, py: number, qx: number, qy: number, rx: number, ry: number) => Math.sign((qx - px) * (ry - py) - (qy - py) * (rx - px));
  return o(ax, ay, bx, by, cx, cy) * o(ax, ay, bx, by, dx, dy) <= 0 && o(cx, cy, dx, dy, ax, ay) * o(cx, cy, dx, dy, bx, by) <= 0;
}

/** 線分 AB と四角の距離 (凸なもの同士なので、交わらなければ、どちらかの頂点ともう一方の辺の距離の最小) */
function segRectDist(ax: number, ay: number, bx: number, by: number, r: number[]): number {
  const corners = [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]];
  if (rectDist(ax, ay, r) === 0 || rectDist(bx, by, r) === 0) return 0;
  for (let k = 0; k < 4; k++) {
    const [c, d] = [corners[k], corners[(k + 1) % 4]];
    if (crosses(ax, ay, bx, by, c[0], c[1], d[0], d[1])) return 0;
  }
  return Math.min(rectDist(ax, ay, r), rectDist(bx, by, r), ...corners.map(([cx, cy]) => segDist(cx, cy, ax, ay, bx, by)));
}

/** 多角形と四角の距離 (重なれば 0) */
function polyRectDist(xy: Float64Array, g: Polys, r: number[]): number {
  if (contains(xy, g, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)) return 0;
  let best = Infinity;
  for (const rings of g.polys) for (const [s, n] of rings) {
    for (let i = s + 1; i < s + n; i++) best = Math.min(best, segRectDist(xy[2 * i - 2], xy[2 * i - 1], xy[2 * i], xy[2 * i + 1], r));
  }
  return best;   // 辺が四角にかからず、四角の真ん中も外なら、四角は多角形の外にある
}

test("タイルの余白 450m で足りる: 2km 四方の中の地点から根拠を探す半径以内の点・区域・町は、すべてそのタイルにある", async () => {
  const L = await kanazawaLoc(deps);
  const C = L.C;
  const t = MANIFEST.municipalities["172014"].tiles.loc;
  const acc = C.MAX_SEARCH_M + 0.05;
  const need = { j: C.ADDR_FAR_M + acc, p: Math.max(C.PARCEL_M, C.PARCEL_NEAR_M + acc), poly: C.NEAR_M + acc };
  assert.ok(Math.max(need.j, need.p, need.poly) < L.H, "根拠を探す半径が余白より大きい");
  const tiles = new Map<string, { j: Set<number>; p: Set<number>; parts: Set<number>; towns: Set<number> }>();
  const pts = { j: new Map<number, [number, number]>(), p: new Map<number, [number, number]>() };
  for (const [name, sha] of Object.entries<string>(t.files)) {
    const tile = new LocTile(readBytes(`${t.prefix}${name}.${sha.slice(0, 12)}.bin.gz`, sha));
    const sets = { j: new Set<number>(), p: new Set<number>(), parts: new Set<number>(tile.parts), towns: new Set<number>(tile.towns) };
    for (const s of ["j", "p"] as const) {
      const ps = tile[s];
      for (let a = 0; a < ps.i.length; a++) {
        sets[s].add(ps.i[a]);
        if (!pts[s].has(ps.i[a])) pts[s].set(ps.i[a], L.proj((tile.lat0 + ps.lat[a]) / 1e9, (tile.lng0 + ps.lng[a]) / 1e9));
      }
    }
    tiles.set(name, sets);
  }
  const core = (name: string) => { const [tx, ty] = name.split("_").map(Number); return [tx * L.S, ty * L.S, (tx + 1) * L.S, (ty + 1) * L.S]; };
  const counts: Record<string, number> = {};
  for (const s of ["j", "p"] as const) {
    let top = -1;
    for (const i of pts[s].keys()) top = Math.max(top, i);
    assert.equal(top + 1, pts[s].size, `${s}: どのタイルにも無い点がある`);
    counts[s] = 0;
    for (const [i, [x, y]] of pts[s]) {
      for (let tx = Math.floor((x - need[s]) / L.S); tx <= Math.floor((x + need[s]) / L.S); tx++) {
        for (let ty = Math.floor((y - need[s]) / L.S); ty <= Math.floor((y + need[s]) / L.S); ty++) {
          const name = `${tx}_${ty}`;
          const set = tiles.get(name);
          if (!set || rectDist(x, y, core(name)) > need[s]) continue;   // タイルが無い = 市の区域にかからない (地点が市の中なら引かない)
          counts[s]++;
          if (!set[s].has(i)) assert.fail(`${s} ${i} がタイル ${name} に無い (2km 四方から ${rectDist(x, y, core(name)).toFixed(1)}m)`);
        }
      }
    }
  }
  for (const [kind, polys, xy] of [["parts", L.parts, L.partXy], ["towns", L.towns, L.townXy]] as const) {
    counts[kind] = 0;
    for (const [name, set] of tiles) {
      const r = core(name), wide = [r[0] - need.poly, r[1] - need.poly, r[2] + need.poly, r[3] + need.poly];
      polys.forEach((q, i) => {
        const [x0, y0, x1, y1] = q.g.bbox;
        if (x1 < wide[0] || x0 > wide[2] || y1 < wide[1] || y0 > wide[3] || set[kind].has(i)) return;
        counts[kind]++;
        const d = polyRectDist(xy, q.g, r);
        assert.ok(d > need.poly, `${kind} ${i} がタイル ${name} に無い (2km 四方から ${d.toFixed(1)}m)`);
      });
    }
  }
  console.log(`  半径 住居表示住所 ${need.j}m・地番 ${need.p}m・区域と町 ${need.poly}m (余白 ${L.H}m)。確かめた組: 住所 ${counts.j}・地番 ${counts.p}・` +
    `タイルに無い区域 ${counts.parts}・町 ${counts.towns} (四角が近いもの)`);
});
