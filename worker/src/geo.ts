// 緯度経度 → 平面直角座標系 (PROJ の tmerc の移植) と、shapely (GEOS) と同じ式の距離・点が多角形の中か
// resolve.py は geopandas (pyproj) で EPSG:6675 に直し、shapely で距離を測る。同じ式で計算すると、距離の値・並べ方・丸めが Python と同じになる
// (pyproj との差は 1e-9 m ほど。build_loc.py が入れた確かめる点で test/locate.test.ts が確かめる)

// ---------------------------------------------------------------- 「GPK1」の入れ物 (nationwide/packs/build_loc.py の bin_pack)
export interface BinPack {
  header: any;
  f64(name: string): Float64Array;
  i32(name: string): Int32Array;
  i16(name: string): Int16Array;
  u16(name: string): Uint16Array;
}

const LITTLE = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

export function readBinPack(bytes: Uint8Array): BinPack {
  if (bytes.length < 8 || String.fromCharCode(...bytes.subarray(0, 4)) !== "GPK1") throw new Error("GPK1 の形式ではない");
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const hlen = dv.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + hlen)));
  const start = Math.ceil((8 + hlen) / 8) * 8;
  // 型つき配列は 8バイト境界から作る (ずれていれば写す)
  const buf = bytes.byteOffset % 8 === 0 ? bytes : new Uint8Array(bytes);
  const view = <T>(name: string, type: string, size: number, make: (b: ArrayBuffer, off: number, n: number) => T, get: (d: DataView, o: number) => number, mk: (n: number) => any): T => {
    const s = header.sections[name];
    if (!s || s[0] !== type) throw new Error(`パックに ${name} (${type}) が無い`);
    const off = buf.byteOffset + start + s[1];
    if (LITTLE) return make(buf.buffer as ArrayBuffer, off, s[2]);
    const out = mk(s[2]);                                     // ビッグエンディアンの環境 (まず無い) では1つずつ読む
    const d = new DataView(buf.buffer, off, s[2] * size);
    for (let i = 0; i < s[2]; i++) out[i] = get(d, i * size);
    return out as T;
  };
  return {
    header,
    f64: (n) => view(n, "f64", 8, (b, o, k) => new Float64Array(b, o, k), (d, o) => d.getFloat64(o, true), (k) => new Float64Array(k)),
    i32: (n) => view(n, "i32", 4, (b, o, k) => new Int32Array(b, o, k), (d, o) => d.getInt32(o, true), (k) => new Int32Array(k)),
    i16: (n) => view(n, "i16", 2, (b, o, k) => new Int16Array(b, o, k), (d, o) => d.getInt16(o, true), (k) => new Int16Array(k)),
    u16: (n) => view(n, "u16", 2, (b, o, k) => new Uint16Array(b, o, k), (d, o) => d.getUint16(o, true), (k) => new Uint16Array(k)),
  };
}

// ---------------------------------------------------------------- 横メルカトル (PROJ src/projections/tmerc.cpp の「exact」= Poder/Engsager)
const DEG = 0.017453292519943295;

function gatg(p: number[], B: number, cos2B: number, sin2B: number): number {
  let h = 0, h2 = 0;
  const two = 2 * cos2B;
  let i = p.length;
  let h1 = p[--i];
  while (i) {
    h = -h2 + two * h1 + p[--i];
    h2 = h1;
    h1 = h;
  }
  return B + h * sin2B;
}

function clens(a: number[], arg: number): number {
  const r = 2 * Math.cos(arg);
  let i = a.length;
  let hr1 = 0, hr2: number;
  let hr = a[--i];
  while (i) {
    hr2 = hr1;
    hr1 = hr;
    hr = -hr2 + r * hr1 + a[--i];
  }
  return Math.sin(arg) * hr;
}

function clenS(a: number[], sr: number, cr: number, si: number, ci: number): [number, number] {
  let r = 2 * cr * ci, im = -2 * sr * si;
  let hi1 = 0, hr1 = 0, hi = 0, hr2: number, hi2: number;
  let i = a.length;
  let hr = a[--i];
  while (i) {
    hr2 = hr1; hi2 = hi1; hr1 = hr; hi1 = hi;
    hr = -hr2 + r * hr1 - im * hi1 + a[--i];
    hi = -hi2 + im * hr1 + r * hi1;
  }
  r = sr * ci; im = cr * si;
  return [r * hr - im * hi, r * hi + im * hr];
}

export type Projector = (lat: number, lng: number) => [number, number];

/** crs (build_loc.py の loc.crs: lat_0・lon_0・k_0・a・rf) → 緯度経度 (度) から [x (東), y (北)] (m) を返す関数 */
export function tmerc(crs: { lat_0: number; lon_0: number; k_0: number; a: number; rf: number }): Projector {
  const f = 1 / crs.rf, es = 2 * f - f * f;
  const n = Math.tan(Math.asin(Math.sqrt(es)) / 2) ** 2;      // PROJ と同じく角離心率から第3扁平率
  const cbg = [
    n * (-2 + n * (2 / 3.0 + n * (4 / 3.0 + n * (-82 / 45.0 + n * (32 / 45.0 + n * (4642 / 4725.0)))))),
    0, 0, 0, 0, 0];
  let np = n * n;
  cbg[1] = np * (5 / 3.0 + n * (-16 / 15.0 + n * (-13 / 9.0 + n * (904 / 315.0 + n * (-1522 / 945.0)))));
  np *= n;
  cbg[2] = np * (-26 / 15.0 + n * (34 / 21.0 + n * (8 / 5.0 + n * (-12686 / 2835.0))));
  np *= n;
  cbg[3] = np * (1237 / 630.0 + n * (-12 / 5.0 + n * (-24832 / 14175.0)));
  np *= n;
  cbg[4] = np * (-734 / 315.0 + n * (109598 / 31185.0));
  np *= n;
  cbg[5] = np * (444337 / 155925.0);
  np = n * n;
  const Qn = crs.k_0 / (1 + n) * (1 + np * (1 / 4.0 + np * (1 / 64.0 + np / 256.0)));
  const gtu = [n * (0.5 + n * (-2 / 3.0 + n * (5 / 16.0 + n * (41 / 180.0 + n * (-127 / 288.0 + n * (7891 / 37800.0)))))), 0, 0, 0, 0, 0];
  gtu[1] = np * (13 / 48.0 + n * (-3 / 5.0 + n * (557 / 1440.0 + n * (281 / 630.0 + n * (-1983433 / 1935360.0)))));
  np *= n;
  gtu[2] = np * (61 / 240.0 + n * (-103 / 140.0 + n * (15061 / 26880.0 + n * (167603 / 181440.0))));
  np *= n;
  gtu[3] = np * (49561 / 161280.0 + n * (-179 / 168.0 + n * (6601661 / 7257600.0)));
  np *= n;
  gtu[4] = np * (34729 / 80640.0 + n * (-3418889 / 1995840.0));
  np *= n;
  gtu[5] = np * (212378941 / 319334400.0);
  const phi0 = crs.lat_0 * DEG, lam0 = crs.lon_0 * DEG, a = crs.a;
  const Z = gatg(cbg, phi0, Math.cos(2 * phi0), Math.sin(2 * phi0));
  const Zb = -Qn * (Z + clens(gtu, 2 * Z));
  return (lat: number, lng: number) => {
    const phi = lat * DEG, lam = lng * DEG - lam0;
    let Cn = gatg(cbg, phi, Math.cos(2 * phi), Math.sin(2 * phi));
    const sinCn = Math.sin(Cn), cosCn = Math.cos(Cn), sinCe = Math.sin(lam), cosCe = Math.cos(lam);
    const cosCnCosCe = cosCn * cosCe;
    Cn = Math.atan2(sinCn, cosCnCosCe);
    const inv = 1 / Math.hypot(sinCn, cosCnCosCe);
    const tanCe = sinCe * cosCn * inv;
    let Ce = Math.asinh(tanCe);
    const twoInv = 2 * inv, twoInvSq = twoInv * inv;
    const tmpR = cosCnCosCe * twoInvSq;
    const [dCn, dCe] = clenS(gtu, sinCn * tmpR, cosCnCosCe * tmpR - 1, tanCe * twoInv, twoInvSq - 1);
    Cn += dCn;
    Ce += dCe;
    if (!(Math.abs(Ce) <= 2.623395162778)) return [Infinity, Infinity];
    return [a * (Qn * Ce) + 0, a * (Qn * Cn + Zb) + 0];
  };
}

// ---------------------------------------------------------------- 距離 (GEOS: Coordinate::distance・Distance::pointToSegment と同じ式)
export function ptDist(px: number, py: number, ax: number, ay: number): number {
  const dx = px - ax, dy = py - ay;
  return Math.sqrt(dx * dx + dy * dy);
}

export function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  if (ax === bx && ay === by) return ptDist(px, py, ax, ay);
  const len2 = (bx - ax) * (bx - ax) + (by - ay) * (by - ay);
  const r = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / len2;
  if (r <= 0.0) return ptDist(px, py, ax, ay);
  if (r >= 1.0) return ptDist(px, py, bx, by);
  const s = ((ay - py) * (bx - ax) - (ax - px) * (by - ay)) / len2;
  return Math.abs(s) * Math.sqrt(len2);
}

/** 多角形 (Polygon / MultiPolygon)。座標は xy の [x0, y0, x1, y1, ...]、リングは [始めの点の番号, 点の数] (閉じた形) */
export interface Polys {
  polys: [number, number][][];          // 多角形ごとに [外側, 穴...]
  bbox: [number, number, number, number];
}

/** build_loc.py の形 (多角形ごとのリングの点の数) と、座標の配列の中の始め (点の番号) → Polys と、次の始め */
export function polysOf(shape: number[][], xy: Float64Array, start: number): [Polys, number] {
  let at = start;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const polys = shape.map((lens) => lens.map((n) => {
    for (let i = at; i < at + n; i++) {
      const x = xy[2 * i], y = xy[2 * i + 1];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    const ring: [number, number] = [at, n];
    at += n;
    return ring;
  }));
  return [{ polys, bbox: [x0, y0, x1, y1] }, at];
}

/** 点がリングの中か (GEOS の RayCrossingCounter と同じ数え方。境界の上は考えない) */
function inRing(xy: Float64Array, [s, n]: [number, number], px: number, py: number): boolean {
  let crossings = 0;
  for (let i = s + 1; i < s + n; i++) {
    const x1 = xy[2 * i - 2], y1 = xy[2 * i - 1], x2 = xy[2 * i], y2 = xy[2 * i + 1];
    if (x1 < px && x2 < px) continue;
    if ((y1 > py && y2 <= py) || (y2 > py && y1 <= py)) {
      let orient = Math.sign((x2 - x1) * (py - y1) - (y2 - y1) * (px - x1));   // 左 (反時計回り) なら 1
      if (orient === 0) return false;                         // 線の上 (内側とは数えない)
      if (y2 < y1) orient = -orient;
      if (orient > 0) crossings++;
    }
  }
  return (crossings & 1) === 1;
}

/** shapely の contains (点が多角形の内側か) */
export function contains(xy: Float64Array, g: Polys, px: number, py: number): boolean {
  const [x0, y0, x1, y1] = g.bbox;
  if (px < x0 || px > x1 || py < y0 || py > y1) return false;
  return g.polys.some((rings) => inRing(xy, rings[0], px, py) && !rings.slice(1).some((h) => inRing(xy, h, px, py)));
}

/** shapely の distance (点と多角形。内側なら 0) */
export function polyDistance(xy: Float64Array, g: Polys, px: number, py: number): number {
  if (contains(xy, g, px, py)) return 0;
  let best = Infinity;
  for (const rings of g.polys) {
    for (const [s, n] of rings) {
      for (let i = s + 1; i < s + n; i++) {
        const d = segDist(px, py, xy[2 * i - 2], xy[2 * i - 1], xy[2 * i], xy[2 * i + 1]);
        if (d < best) best = d;
      }
    }
  }
  return best;
}

/** 多角形までの距離が limit 以下か (四角の外側でまず振り落とす) */
export function polyWithin(xy: Float64Array, g: Polys, px: number, py: number, limit: number): number | null {
  const [x0, y0, x1, y1] = g.bbox;
  if (px < x0 - limit || px > x1 + limit || py < y0 - limit || py > y1 + limit) return null;
  const d = polyDistance(xy, g, px, py);
  return d <= limit ? d : null;
}

/** 線 (MultiLineString。lens は線ごとの点の数) までの距離 */
export function linesDistance(xy: Float64Array, lens: number[], px: number, py: number): number {
  let best = Infinity, at = 0;
  for (const n of lens) {
    for (let i = at + 1; i < at + n; i++) {
      const d = segDist(px, py, xy[2 * i - 2], xy[2 * i - 1], xy[2 * i], xy[2 * i + 1]);
      if (d < best) best = d;
    }
    at += n;
  }
  return best;
}
