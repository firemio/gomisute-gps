// 緯度経度 → 答え (resolve.resolve の移植)
// 入力の確かめ方 → gps2city で市区町村を引く → 精度が低すぎる・町の区域の外・対応していない自治体 → 金沢市の中なら locate (kanazawa/locate.ts) で
// 地区を決めて、住所のときと同じ組み立て (Kanazawa.answer) で日ごとの答え・出典・要約を作る。文面も Python と同じ
import { dayToIso, jstToday, pyDate } from "./dates.ts";
import { KANAZAWA, MANIFEST, kanazawa, kanazawaLoc, kanazawaTile, type Deps } from "./deps.ts";
import { locate, searchRadius } from "./kanazawa/locate.ts";
import { pf, pyFloatRepr, pyRepr, pyRound, pyRoundInt, pySlice, pyStrip, toFloat, deepClone, truthy } from "./py.ts";

const ACC_CONF = 0.68;
const MAX_SEARCH_M = 300;

export { searchRadius };

const asFloat = (v: number) => pf(v);

/** 金沢市の町の区域 (国勢調査の小地域) を囲む四角の外か (外なら市の区域の外と言い切れる。パックを読まずに済む) */
function outsideBbox(lat: number, lng: number): boolean {
  const b = MANIFEST.municipalities[KANAZAWA].bbox;
  return lng < b[0] || lat < b[1] || lng > b[2] || lat > b[3];
}

/** 地点を平面に直し、市の区域の中か・locate の結果を出す (四角の外なら何も読まない) */
async function where(deps: Deps, lat: number, lng: number) {
  if (outsideBbox(lat, lng)) return null;
  const L = await kanazawaLoc(deps);
  const [x, y] = L.proj(lat, lng);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { L, x, y, tile: await kanazawaTile(deps, L.tileName(x, y)) };
}

export async function resolveLocation(deps: Deps, latV: unknown, lngV: unknown, date: string | null, days: number,
  accuracyM: unknown, accuracyConf: unknown): Promise<any> {
  const now = deps.now();
  const k = await kanazawa(deps, false);
  const base: any = k.base(now);
  const bad = (q: any, msg: string) => ({ ...base, query: q, status: "invalid_input", answerable: false, summary_ja: msg });
  const lat = toFloat(latV), lng = toFloat(lngV);                         // check_args で有限の数と確かめてある
  let d: number | null = null;
  if (date !== null) {
    try { d = pyDate(date, pyStrip); } catch {
      return bad({ lat: asFloat(lat), lng: asFloat(lng), date: pySlice(pyRepr(date), 0, 40) }, "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。");
    }
  }
  const q: any = { lat: asFloat(lat), lng: asFloat(lng), timezone: "Asia/Tokyo", days, date: dayToIso(d ?? jstToday(now)), date_basis: d !== null ? "user" : "server_today_jst",
    accuracy_m: accuracyM === null || accuracyM === undefined ? null : typeof accuracyM === "string" ? accuracyM : pySlice(pyRepr(accuracyM), 0, 20) };
  base.query = q;
  let conf = ACC_CONF;
  if (accuracyConf !== null && accuracyConf !== undefined) {
    const c = toFloat(accuracyConf);
    if (!(c >= 0.5 && c <= 0.99)) {
      q.accuracy_confidence = pySlice(pyFloatRepr(c), 0, 20);
      return bad(q, "accuracy_confidence (accuracy_m の半径に本当の位置が入る確率) は 0.5〜0.99 で指定してください (Android・Chrome は 0.68)。");
    }
    conf = c || ACC_CONF;
  }
  q.accuracy_confidence = asFloat(conf);
  let acc: number | null = null;
  if (accuracyM !== null && accuracyM !== undefined) {
    acc = toFloat(accuracyM);
    if (!(acc >= 0 && acc <= 1000)) return bad(q, "accuracy_m (位置の誤差) は 0〜1000 の数値 (m) で指定してください。");
    q.accuracy_m = asFloat(acc);
    q.search_radius_m = asFloat(pyRound(searchRadius(acc, conf), 1));
  }
  if (lat >= 122 && lat <= 154 && lng >= 20 && lng <= 46) return bad(q, "緯度と経度が入れ替わっている可能性があります (日本では緯度 20〜46、経度 122〜154 付近)。");
  if (!(lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180)) return bad(q, "緯度は -90〜90、経度は -180〜180 で指定してください。");
  let muni: any;
  try { muni = await deps.gps2city(lat, lng); } catch (e) { muni = { error: e instanceof Error ? e.message : String(e) }; }
  if (!muni || typeof muni !== "object" || Array.isArray(muni)) muni = { error: "gps2city の返事が読めない" };
  const inCity = muni.lg_code === k.LG;
  const radius = q.search_radius_m ? toFloat(q.search_radius_m) : 0;
  const unresolved = (method: string, checkedBy: string, blocked: any, summary: string) => deepClone({ ...base, status: "unresolved", answerable: false,
    location: { municipality: { lg_code: k.LG, name: k.MUNI, checked_by: checkedBy }, collection_district: null, district_confidence: pf(0),
      method, place_name: null, decision_blocked_by: blocked },
    candidates: [], consensus: null, town_rows: [], notes: [], holiday_policy: k.core.holiday_policy, calendar: null, official_notes: [],
    sources: [deepClone(k.core.rules_source), deepClone(k.core.put_out_rules)], summary_ja: summary });
  const w = await where(deps, lat, lng);
  if (radius > MAX_SEARCH_M && (inCity || (w !== null && w.tile !== null && w.L.inCity(w.x, w.y)))) {
    return unresolved("accuracy_too_low", inCity ? "gps2city" : "census_polygons", { reason: "accuracy_too_low", search_radius_m: q.search_radius_m },
      `位置の精度が低すぎて (本当の位置が約${pyRoundInt(radius)}m の範囲のどこか)、収集地区を絞れません。位置の精度を上げるか、住所を教えてください。`);
  }
  const ka = w !== null && w.tile !== null && w.L.inCity(w.x, w.y) ? await kanazawa(deps, true) : null;
  const loc = ka ? locate(ka, w!.L, w!.tile, w!.x, w!.y, lat, lng, acc, conf) : null;
  if (loc === null && inCity) {
    // gps2city (国土数値情報の行政区域) では金沢市だが、国勢調査の町の区域の外 (境界のずれ・海岸など)
    const C = k.core.contact;
    return unresolved("outside_town_polygons", "gps2city", { reason: "outside_town_polygons", town: null },
      `この場所は${k.MUNI}内ですが、町の区域 (国勢調査の小地域) の外にあり、収集地区を判定できません。${C.name} (${C.phone}) に確認してください。`);
  }
  if (loc === null || (truthy(muni.lg_code) && muni.lg_code !== k.LG)) {
    const name = muni.city ? `${muni.prefecture ?? ""}${muni.city ?? ""}${muni.ward ?? ""}` : null;
    return deepClone({ ...base, status: "out_of_coverage", answerable: false,
      location: { municipality: name ? { lg_code: muni.lg_code ?? null, name } : null },
      coverage: [{ lg_code: k.LG, name: k.MUNI, calendar: k.core.coverage_calendar }],
      summary_ja: name ? `この場所は${name}で、gomisute-gps はまだ対応していません (対応: ${k.MUNI})。${name}の公式サイトで確認してください。`
        : `この場所は対応地域 (${k.MUNI}) の町の区域 (国勢調査の小地域) の外です (市外か、市内の海岸など)。` });
  }
  return ka!.answer(loc, d ?? jstToday(now), days, base, inCity ? "gps2city" : "census_polygons", now, await deps.status(KANAZAWA));
}
