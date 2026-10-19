// 宮城県女川町 (行政区のカレンダーの型): 行政区・地名 + 日付 → 出せるごみ、品目 + 日付 → 分け方
// onagawa.py の移植。行政区・地名の索引、品目の照合に使う名前 (item_names) などは build_packs.py が先に作ったものを使う
import { consensus, sentences } from "./common.ts";
import { WDJ, dayToIso, fmt, isoToDay, jstIso, jstToday, pyDate, weekday, yearOf } from "./dates.ts";
import { ratio } from "./difflib.ts";
import { applyFreshness, freshness } from "./freshness.ts";
import { D, WS, cmpPy, cps, deepClone, pf, pyRstrip, pyStrip, sortedPy } from "./py.ts";

export const PREFIX = `^(日本国?)?(〒?${D}{3}-?${D}{4})?(宮城県)?(牡鹿郡)?(女川町|おながわ町)`;
const PREFIX_RE = new RegExp(PREFIX, "u");
const FRESH_NOTE: Record<string, string> = {
  stale: "公式資料の変更の確認が古いので、念のため町の公式資料でも確かめてください。",
  partly_unchecked: "元にした公式資料の一部を確認できなかったので、念のため町の公式資料でも確かめてください。",
  unchecked: "元にした公式資料が変わっていないかを確かめられていないので、念のため町の公式資料でも確かめてください。",
};
const NO_DEST = ["印字なし", "記載なし", "対象外", "入れない"];
const WS_ALL = new RegExp(`${WS}+`, "gu");

/** onagawa._n: NFKC にして空白を消し、ヶ・ヵ をケに */
export const n_ = (s: string): string => s.normalize("NFKC").replace(WS_ALL, "").replaceAll("ヶ", "ケ").replaceAll("ヵ", "ケ");
/** onagawa.is_onagawa */
export const isOnagawa = (text: unknown): boolean => typeof text === "string" && PREFIX_RE.test(n_(text));

const MATERIAL = /プラスチック|金属|陶器|陶磁器|ガラス|アルミ|スチール|ステンレス|ゴム/u;
const MATERIAL_FULL = /^(?:プラスチック|金属|陶器|陶磁器|ガラス|アルミ|スチール|ステンレス|ゴム)$/u;
const CONTAINER = /パック|ボトル|容器|びん/u;
const SYNONYM: [string, string][] = [["トレー", "トレイ"], ["てんぷら", "天ぷら"], ["蛍光灯", "蛍光管"], ["発砲", "発泡"], ["ふとん", "布団"], ["ペットボトルの蓋", "ペットボトルのふた"]];
const GENERIC_SUFFIX = ["紙", "類", "など", "全般"];

/** onagawa._qnorm: 品目の表記ゆれをそろえる */
export function qnorm(s0: string): string {
  let s = n_(s0);
  for (const [x, y] of SYNONYM) s = s.replaceAll(x, y);
  s = s.replaceAll("蓋", "ふた").replaceAll("フタ", "ふた").replaceAll("付き", "つき").replaceAll("ついた", "つき").replaceAll("白い", "白色");
  return s.replace(/[のや・製等]|など/gu, "");
}

const len = (s: string) => cps(s).length;
const replace1 = (s: string, x: string, y: string) => { const i = s.indexOf(x); return i < 0 ? s : s.slice(0, i) + y + s.slice(i + x.length); };

type Rule = { item: string; destination: string; how: string | null; valid_from: string | null; valid_to: string | null; source: string | null;
  _names: [string, string][]; _core: string; _core_mat: string | null; _item_mat: string | null; _core_box: boolean; _how_sentences: string[] };

/** onagawa.item_match: 品目の近さ → [点, 当たり方] (品目の側の名前・材質は build_packs.py で先に作った) */
export function itemMatch(query: string, r: Rule): [number, string | null] {
  const core = r._core, coreMat = r._core_mat, itemMat = r._item_mat, coreBox = r._core_box;
  let best: [number, string | null] = [0.0, null];
  const up = (sc: number, how: string) => { if (sc > best[0]) best = [sc, how]; };
  for (const q of [...new Set([query, query.replaceAll("キャップ", "ふた"), query.replaceAll("ふた", "キャップ")])]) {
    const qm = MATERIAL.exec(q);
    const qMat = qm ? qm[0] : null;
    if (qMat && itemMat && qMat !== itemMat) continue;
    const qRest = qMat ? replace1(q, qMat, "") : null;
    const qBare = qMat && coreMat && qMat === coreMat ? replace1(q, qMat, "") : null;
    for (const [n, kind] of r._names) {
      if (q === n) {
        if (kind === "full" || kind === "main") up(1.0, "name");
        else if (coreMat && !qMat) up(0.6, "example_without_material");
        else if (coreBox && !CONTAINER.test(q)) up(0.6, "example_is_contents");
        else up(0.9, "example");
        continue;
      }
      if (qBare && kind === "example" && len(qBare) >= 2 && (qBare === n || (n.endsWith(qBare) && ["ポリ"].includes(cps(n).slice(0, len(n) - len(qBare)).join(""))))) {
        up(0.9, "example"); continue;
      }
      if ((len(n) >= 2 || kind === "main" || kind === "full") && q.includes(n)) {
        const rest = replace1(q, n, "").replace(/(用|空き|空)/gu, "");
        const restMat = MATERIAL.exec(rest);
        if (!rest || (coreMat && rest === coreMat) || (kind === "example" && coreBox && core.endsWith(rest) && !MATERIAL.test(n))) {
          up(0.9, kind !== "example" ? "name_in_query" : "example");
        } else if (!MATERIAL_FULL.test(n) && !(restMat && coreMat && restMat[0] !== coreMat)) {
          up(0.6, "name_in_query_partly");
        }
        continue;
      }
      if (qRest && len(qRest) >= 2 && n.endsWith(qRest)) { up(0.6, "same_kind"); continue; }
      if (len(q) >= 2 && n.includes(q)) {
        const okSuffix = n.startsWith(q) && GENERIC_SUFFIX.includes(n.slice(q.length)) && kind !== "example";
        up(okSuffix && !(coreMat && !qMat) ? 0.9 : 0.6, okSuffix ? "name" : "query_in_name");
        continue;
      }
      const rt = ratio(q, n);
      const qc = cps(q), nc = cps(n);
      let suffix = 0;
      while (suffix < Math.min(qc.length, nc.length) && qc[qc.length - 1 - suffix] === nc[nc.length - 1 - suffix]) suffix++;
      if (len(n) >= 2 && ((suffix >= 2 && rt >= 0.7) || rt >= 0.85)) up(0.6, "similar");
    }
  }
  return best;
}

export class Onagawa {
  d: any;
  LG: string;
  MUNI: string;
  lo: number;
  hi: number;
  gk = new Map<string, { name: string; burnable?: any; resource?: any }>();
  places = new Map<string, { gyoseiku: string[]; certainty: string; basis: string }>();
  unknownNew: string[];
  noDates = new Map<string, any>();
  private sets = new WeakMap<object, Set<string>>();

  constructor(pack: any) {
    this.d = pack;
    this.LG = pack.lg_code;
    this.MUNI = pack.name;
    this.lo = isoToDay(pack.valid_from);
    this.hi = isoToDay(pack.valid_to);
    for (const [key, name, bi, ri] of pack.gk) {
      this.gk.set(key, { name, ...(bi >= 0 ? { burnable: pack.burnable.groups[bi] } : {}), ...(ri >= 0 ? { resource: pack.resource.rows[ri] } : {}) });
    }
    for (const [key, gyoseiku, certainty, basis] of pack.places) this.places.set(key, { gyoseiku, certainty, basis });
    this.unknownNew = pack.unknown_new;
    for (const [key, gi] of Object.entries(pack.no_dates as Record<string, number>)) this.noDates.set(key, pack.burnable.groups[gi]);
  }
  private has(list: string[], x: string): boolean {
    let s = this.sets.get(list);
    if (!s) { s = new Set(list); this.sets.set(list, s); }
    return s.has(x);
  }

  // ---------------- 場所
  matchPlace(text: string): [string[], string | null, string | null, string | null] {
    const s = n_(text).replace(PREFIX_RE, "");
    const m = /^([^\n]+?)(行政区|地区|区)?$/u.exec(s);
    for (const [k, v] of this.gk) {
      if (s === k || (m && m[1] === k && m[2])) return [[v.name], `行政区「${v.name}」`, "sure", "カレンダーの凡例"];
    }
    const hits: string[] = [];
    const unknown = new Set(this.unknownNew);
    for (const k of this.places.keys()) {
      if (!s.startsWith(k)) continue;
      const rest = s.slice(k.length);
      if (rest && !new RegExp(`^(字|大字|${D}|丁目|-)`, "u").test(rest) && !unknown.has(k)) continue;
      hits.push(k);
    }
    for (const k of this.gk.keys()) {
      if (s.startsWith(k) && new RegExp(`^(字|大字|${D}|-)`, "u").test(s.slice(k.length) || "x")) hits.push(k);
    }
    if (!hits.length) return [[], null, null, null];
    const k = hits.reduce((a, b) => (len(b) > len(a) ? b : a));
    const rest = s.slice(k.length).replace(/^(大字|字)/u, "");
    const u = this.unknownNew.find((x) => rest.startsWith(x));
    if (u !== undefined) return [[], `地名「${k}字${u}」`, "unknown", this.places.get(u)!.basis];
    if (this.gk.has(k) && !this.places.has(k)) {
      const name = this.gk.get(k)!.name;
      return [[name], `行政区「${name}」(後ろに字・番地)`, "likely", "行政区の名前の後ろに字・番地がある"];
    }
    const v = this.places.get(k)!;
    return [v.gyoseiku, `地名「${k}」→ 行政区の候補 ${v.gyoseiku.join("・") || "(不明)"}`, v.certainty, v.basis];
  }

  private labelNote(label: string, day: string): string | null {
    for (const [lo, hi, note] of this.d.label_notes[label] ?? []) if ((lo === null || lo <= day) && (hi === null || day <= hi)) return note;
    return null;
  }

  private day(gyoseiku: string, dn: number): any {
    const g = this.gk.get(n_(gyoseiku))!;
    const ds = dayToIso(dn);
    const day: any = { date: ds, weekday: WDJ[weekday(dn)], in_calendar_range: this.lo <= dn && dn <= this.hi };
    if (!day.in_calendar_range) {
      day.collections = null;
      day.note = `女川町の${yearOf(this.lo)}年度カレンダー (${dayToIso(this.lo)}〜${dayToIso(this.hi)}) の範囲外で分かりません (「収集なし」という意味ではありません)`;
      return day;
    }
    const cols: any[] = [];
    const b = g.burnable;
    if (b && this.has(b.dates, ds)) cols.push({ category: "burnable", official_label: this.d.burnable.label, rule_text: `${b.color} (${b.weekdays_short})` });
    const r = g.resource;
    if (r) {
      for (const c of this.d.resource.columns) {
        if (this.has(r.by_column[c.label] ?? [], ds)) {
          const x: any = { category: "resource", official_label: c.label };
          const note = this.labelNote(c.label, ds);
          if (note) x.label_note = note;
          cols.push(x);
        }
      }
    }
    day.collections = cols;
    return day;
  }

  private next(gyoseiku: string, dn: number): any[] {
    const g = this.gk.get(n_(gyoseiku))!;
    const ds = dayToIso(dn);
    const labels = [...(g.burnable ? [this.d.burnable.label] : []), ...(g.resource ? this.d.resource.columns.map((c: any) => c.label) : [])];
    if (dn + 1 < this.lo) return labels.map((l) => ({ official_label: l, date: null, null_reason: `${dayToIso(this.lo)} より前の期間のカレンダーは未取得なので、次の収集日は分からない` }));
    const out: any[] = [];
    const nul = `${yearOf(this.lo)}年度のカレンダー (${dayToIso(this.hi)} まで) にこれより後の日が無い`;
    const minAfter = (xs: string[]) => { let m: string | null = null; for (const x of xs) if (x > ds && (m === null || x < m)) m = x; return m; };
    if (g.burnable) {
      const nx = minAfter(g.burnable.dates);
      out.push({ official_label: this.d.burnable.label, date: nx, ...(nx ? {} : { null_reason: nul }) });
    }
    if (g.resource) {
      for (const c of this.d.resource.columns) {
        const nx = minAfter(g.resource.by_column[c.label] ?? []);
        out.push({ official_label: c.label, date: nx, ...(nx ? {} : { null_reason: nul }) });
      }
    }
    for (const x of out) if (x.date) x.weekday = WDJ[weekday(isoToDay(x.date))];
    for (const x of out) {
      const note = x.date ? this.labelNote(x.official_label, x.date) : null;
      if (note) x.label_note = note;
    }
    return out.map((v, i) => [v, i] as [any, number]).sort((a, b) => cmpPy(a[0].date ?? "9999", b[0].date ?? "9999") || a[1] - b[1]).map((v) => v[0]);
  }

  yearEnd(gyoseiku: string): string {
    const g = this.gk.get(n_(gyoseiku))!, fy = yearOf(this.lo);
    const edge = (dates: string[]): [string | null, string | null] => {
      let l: string | null = null, f: string | null = null;
      for (const x of dates) {
        if (x <= `${fy}-12-31` && (l === null || x > l)) l = x;
        if (x >= `${fy + 1}-01-01` && (f === null || x < f)) f = x;
      }
      return [l, f];
    };
    const out: string[] = [];
    if (g.burnable && g.burnable.dates.length) {
      const [l, f] = edge(g.burnable.dates);
      out.push(`${this.d.burnable.label}は ${fmt(isoToDay(l!), true)} まで、${fmt(isoToDay(f!), true)} から`);
    }
    if (g.resource) {
      const by = new Map<string, [string, string, string[]]>();
      for (const c of this.d.resource.columns) {
        const [l, f] = edge(g.resource.by_column[c.label] ?? []);
        if (l && f) {
          const key = `${l}\u0000${f}`;
          if (!by.has(key)) by.set(key, [l, f, []]);
          by.get(key)![2].push(c.label);
        }
      }
      for (const [l, f, labels] of [...by.values()].sort((a, b) => cmpPy(a[0], b[0]) || cmpPy(a[1], b[1]))) {
        out.push(`${labels.join("・")}は ${fmt(isoToDay(l), true)} まで、${fmt(isoToDay(f), true)} から`);
      }
    }
    return out.length ? "祝日もカレンダーの色・表のとおり収集する。品目ごとの年内最後と年明け最初の収集日 (この行政区): " + out.join("、") : this.d.holiday_policy;
  }

  private contactText(): string {
    const c = this.d.contact || {};
    return `${c.name ?? "女川町役場"}${c.phone ? " (" + c.phone + ")" : ""}`;
  }

  private base(q: any, now: Date): any {
    return { data_version: this.d.data_version, generated_at: jstIso(now), query: q, put_out_by: deepClone(this.d.put_out_by),
      not_covered: deepClone(this.d.not_covered ?? []), contact: deepClone(this.d.contact), holiday_policy: this.d.holiday_policy };
  }

  // ---------------- onagawa.resolve_address
  resolveAddress(address: string, date: string | null, days: number, now: Date, status: any): any {
    const today = jstToday(now);
    const q = { address, timezone: "Asia/Tokyo", date: date ? date : dayToIso(today), date_basis: date ? "user" : "server_today_jst", days };
    const base = this.base(q, now);
    const bad = (msg: string) => deepClone({ ...base, status: "invalid_input", answerable: false, summary_ja: msg });
    if (typeof address !== "string" || !pyStrip(address) || len(address) > 200) return bad("address は住所の文字列 (200文字まで) で指定してください。");
    let d0: number;
    try { d0 = date !== null ? pyDate(date, pyStrip) : today; } catch { return bad("date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。"); }
    if (!(Number.isInteger(days) && days >= 1 && days <= 14)) return bad("days は 1〜14 の整数で指定してください。");
    const [gys, how, cert, basis] = this.matchPlace(address);
    const loc: any = { municipality: { lg_code: this.LG, name: this.MUNI, checked_by: "address_text" }, place_name: address, method: null,
      collection_district: null, district_confidence: pf(0), decision_blocked_by: null, place_match: how };
    const res: any = { ...base, location: loc, sources: deepClone(this.d.sources), calendar: { valid_from: dayToIso(this.lo), valid_to: dayToIso(this.hi) },
      town_rows: sortedPy(new Set([...this.gk.values()].map((v) => v.name))) };
    if (gys.length === 1 && this.noDates.has(n_(gys[0]))) {
      const g = this.noDates.get(n_(gys[0]));
      const notes = (this.d.put_out_texts ?? []).filter((p: any) => p.text.includes("江島")).map((p: any) => p.text);
      const quote = [g.weekdays_text, ...notes].reduce((a: string, b: string) => (len(b) > len(a) ? b : a));
      Object.assign(res, { status: "unresolved", answerable: false, candidates: [] });
      Object.assign(loc, { method: "schedule_not_published", decision_blocked_by: { reason: "schedule_not_published", town: gys[0], official_text: quote } });
      res.summary_ja = `${gys[0]}地区は、カレンダーに収集日の日付が載っていません。カレンダーには「${quote}」とあります。`
        + `日付ごとの答えは出せないので、区長さんか${this.contactText()}に確かめてください。`;
      return this.finish(res, false, now, status);
    }
    if (!gys.length) {
      const reason = how ? "place_not_mapped" : "place_not_found";
      Object.assign(res, { status: "unresolved", answerable: false, candidates: [] });
      Object.assign(loc, { method: reason, decision_blocked_by: { reason, town: null, label: address, ...(basis ? { basis } : {}) } });
      res.summary_ja = (how ? `${how} は、どの行政区に入るか公式資料に書かれていません。` : `住所 (${address}) から女川町の行政区を決められませんでした。`)
        + `お住まいの行政区 (${res.town_rows.slice(0, 8).join("・")} など) を教えてください。${this.contactText()}にも確認できます。`;
      return this.finish(res, false, now, status);
    }
    const cands = gys.map((g) => ({
      collection_district: g, official: cert === "sure",
      basis: [{ kind: cert === "sure" ? "gyoseiku" : "place_to_gyoseiku", label: how, detail: basis || "" }],
      how_to_confirm: [{ 行政区: g, 燃やせるごみ: this.gk.get(n_(g))!.burnable?.color ?? null, 資源ごみの欄: this.gk.get(n_(g))!.resource?.label ?? null }],
      days: Array.from({ length: days }, (_, i) => this.day(g, d0 + i)),
    }));
    if (cert === "sure" && gys.length === 1) {
      const g = gys[0];
      Object.assign(res, { status: "ok", answerable: cands[0].days.every((x: any) => x.in_calendar_range) });
      Object.assign(loc, { collection_district: g, method: "gyoseiku", district_confidence: 0.98 });
      res.days = cands[0].days;
      res.next_after_date = this.next(g, d0);
      res.holiday_policy = this.yearEnd(g);
      res.groups = { burnable: this.gk.get(n_(g))!.burnable?.color ?? null, resource_row: this.gk.get(n_(g))!.resource?.label ?? null };
    } else {
      Object.assign(res, { status: "ambiguous", answerable: false, candidates: cands, consensus: consensus(cands) });
      Object.assign(loc, { method: "place_to_gyoseiku_candidates", place_basis: basis });
    }
    return this.finish(res, true, now, status);
  }

  private finish(res: any, summarize: boolean, now: Date, status: any): any {
    applyFreshness(res, freshness(status, this.d.sources.map((s: any) => s.url), now));
    const b = (res.location || {}).decision_blocked_by || {};
    if (summarize || b.reason === "source_changed") res.summary_ja = this.summary(res);
    const fr = res.freshness.status;
    if (b.reason !== "source_changed" && fr in FRESH_NOTE) res.summary_ja += FRESH_NOTE[fr];
    return deepClone(res);
  }

  private summary(res: any): string {
    const loc = res.location;
    if (res.status === "ok") {
      const days = res.days.map((d: any) => d.in_calendar_range
        ? `${fmt(isoToDay(d.date), true)}は` + (d.collections.map((c: any) => c.official_label).join("・") || "収集なし")
        : `${fmt(isoToDay(d.date), true)}は女川町のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)`).join("。") + "。";
      const put = res.put_out_by && Object.keys(res.put_out_by).length && res.days.some((d: any) => d.collections && d.collections.length)
        ? `出すのは${res.put_out_by.deadline} (女川町「${res.put_out_by.source_title}」)。` : "";
      const allCols = res.days.flatMap((d: any) => d.collections || []);
      const first = allCols.find((c: any) => c.label_note);
      const lnote = first ? `注: 「${first.official_label}」の${first.label_note}。` : "";
      return sentences(`女川町の行政区 ${loc.collection_district} は、燃やせるごみが「${res.groups.burnable ?? "None"}」、資源ごみが「${res.groups.resource_row ?? "None"}」の欄です。`,
        days, put, lnote, `出典: 女川町「${this.d.calendar_title.split("（")[0]}」。`);
    }
    const b = loc.decision_blocked_by || {};
    if (res.status === "unresolved" && b.reason === "source_changed") {
      return `このデータの元にした公式資料が ${b.checked_at.slice(0, 10)} の確認で変わっていました。作り直して確かめるまでは確かな答えとして返しません。`
        + `女川町の公式ページか${this.contactText()}で確かめてください。`;
    }
    const same = new Set(res.candidates.map((c: any) => JSON.stringify([c.how_to_confirm[0]["燃やせるごみ"], c.how_to_confirm[0]["資源ごみの欄"]]))).size === 1;
    const head = same ? `${loc.place_match ?? "None"} は町の資料から推定したもので、確かな対応表はありません。`
      : `${loc.place_match ?? "None"} は行政区によって収集日が違うため決められません。`;
    const per = res.candidates.slice(0, 3).map((c: any) => `行政区 ${c.collection_district} なら、` + c.days.map((d: any) =>
      `${fmt(isoToDay(d.date), true)}は` + (d.in_calendar_range ? (d.collections.map((x: any) => x.official_label).join("・") || "収集なし") : "分かりません")).join("。") + "。").join("");
    return sentences(head, per, `お住まいの行政区 (町民カレンダーの凡例の名前) を確かめてください。${this.contactText()}にも確認できます。`);
  }

  // ---------------- 分け方 (onagawa.sorting)
  sorting(item: string, date: string | null, now: Date, status: any): any {
    const today = jstToday(now);
    const q = { municipality: this.MUNI, item, date: date ? date : dayToIso(today), date_basis: date ? "user" : "server_today_jst" };
    const base: any = { data_version: this.d.data_version, generated_at: jstIso(now), query: q, contact: deepClone(this.d.contact) };
    if (typeof item !== "string" || !pyStrip(item) || len(item) > 200) {
      return deepClone({ ...base, status: "invalid_input", answerable: false, summary_ja: "item は品目の文字列 (200文字まで) で指定してください。" });
    }
    let d0: number;
    try { d0 = date !== null ? pyDate(date, pyStrip) : today; } catch {
      return deepClone({ ...base, status: "invalid_input", answerable: false, summary_ja: "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。" });
    }
    const fr = freshness(status, this.d.sources.map((x: any) => x.url), now) as any;
    base.freshness = fr;
    if (!(this.lo <= d0 && d0 <= this.hi)) {
      return deepClone({ ...base, status: "out_of_range", answerable: false, matches: [],
        summary_ja: `女川町の分別のデータは ${dayToIso(this.lo)}〜${dayToIso(this.hi)} の分です。${fmt(d0, true)}の分け方は分かりません。${this.contactText()}に確かめてください。` });
    }
    if (fr.status === "source_changed") {
      return deepClone({ ...base, status: "unresolved", answerable: false, matches: [],
        decision_blocked_by: { reason: "source_changed", changed: fr.changed, checked_at: fr.checked_at },
        summary_ja: `このデータの元にした公式資料が変わっていました。作り直して確かめるまでは確かな答えとして返しません。${this.contactText()}に確かめてください。` });
    }
    const res = this.sortingAnswer(item, d0, base);
    if (fr.status in FRESH_NOTE) {
      res.answerable = false;
      res.summary_ja += FRESH_NOTE[fr.status];
    }
    return deepClone(res);
  }

  private sortingAnswer(item: string, d0: number, base: any): any {
    const s = qnorm(item);
    const rules: Rule[] = this.d.sorting_rules ?? [];
    const ds = dayToIso(d0);
    const inside = (r: Rule) => (!r.valid_from || r.valid_from <= ds) && (!r.valid_to || ds <= r.valid_to);
    const scored = rules.map((r) => [...itemMatch(s, r), r] as [number, string | null, Rule]);
    const nowRules = scored.filter(([, , r]) => inside(r));
    const top = nowRules.reduce((m, [sc]) => Math.max(m, sc), 0);
    const strong = nowRules.filter(([sc]) => sc >= 0.9 && sc >= Math.min(top, 0.95)).map(([, how, r]) => [how, r] as [string | null, Rule]);
    const pick = (rs: Rule[]) => rs.map((r) => ({ item: r.item, destination: r.destination, how: r.how, valid_from: r.valid_from, valid_to: r.valid_to, source: r.source }));
    const other = scored.filter(([sc, , r]) => sc >= 0.9 && !inside(r)).map(([, , r]) => r);
    if (!strong.length) {
      const weak = nowRules.map((x, i) => [x, i] as [typeof x, number]).sort((a, b) => b[0][0] - a[0][0] || a[1] - b[1]).map((x) => x[0])
        .filter(([sc]) => sc > 0).map(([, , r]) => r).slice(0, 6);
      if (weak.length) {
        return { ...base, status: "ambiguous", answerable: false, matches: pick(weak),
          summary_ja: `「${item}」とそのまま同じ品目は分別表に無く、近い品目があります: ` + weak.map((r) => `「${r.item}」→「${r.destination}」`).join("、")
            + "。どれに当たるか (材質・大きさ・中身など) を確かめてください。" };
      }
      const periods = sortedPy(new Set(other.map((r) => (r.valid_from || "") + "〜" + (r.valid_to || ""))));
      return { ...base, status: "unresolved", answerable: false, matches: [], other_periods: pick(other), decision_blocked_by: { reason: "item_not_found" },
        summary_ja: `「${item}」は ${fmt(d0, true)}時点の女川町の分別表のデータに見つかりません` + (other.length ? ` (${periods.join("、")} の分別表にはあります)` : "")
          + `。女川町の分別表 (${this.d.sorting_source_url}) か${this.contactText()}で確かめてください。` };
    }
    const rs = strong.map(([, r]) => r);
    if (new Set(rs.map((r) => r.destination)).size > 1) {
      return { ...base, status: "ambiguous", answerable: false, matches: pick(rs.slice(0, 6)),
        summary_ja: `「${item}」に当てはまる品目が分別表に複数あり、出し方が違います: ` + rs.slice(0, 6).map((r) => `「${r.item}」→「${r.destination}」`).join("、")
          + "。どれに当たるか (材質・大きさなど) を確かめてください。" };
    }
    const [how0, b] = strong[0];
    const related = nowRules.filter(([, h, r]) => h === "query_in_name" && !rs.includes(r) && r.destination !== b.destination).map(([, , r]) => r).slice(0, 4);
    const mentions: any[] = [];
    const it = pyStrip(item);
    if (how0 === "example" && len(it) >= 2) {
      for (const r of rules) {
        if (r === b || !inside(r)) continue;
        for (const sent of r._how_sentences) if (sent.includes(it) && mentions.length < 2) mentions.push({ item: r.item, destination: r.destination, text: pyStrip(sent) });
      }
    }
    const res: any = { ...base, matches: pick(rs), other_periods: pick(other), ...(related.length ? { related: pick(related) } : {}), ...(mentions.length ? { related_notes: mentions } : {}) };
    if (NO_DEST.some((x) => b.destination.includes(x))) {
      Object.assign(res, { status: "unresolved", answerable: false, decision_blocked_by: { reason: "not_in_official_table" },
        summary_ja: `「${b.item}」の ${fmt(d0, true)}時点の出し先は、女川町の公式資料に書かれていません (${b.destination})。${this.contactText()}に確かめてください。` });
      return res;
    }
    const start = this.d.valid_from;
    let period = "";
    if (b.valid_from && b.valid_from > start) period = other.length ? ` (${b.valid_from} から)` : ` (${b.valid_from} からの一覧表に載っている品目)`;
    period += b.valid_to ? ` (${b.valid_to} まで)` : "";
    const ch = other.map((c) => (c.valid_to && c.valid_to < ds ? `なお ${c.valid_from || ""}〜${c.valid_to || ""} は「${c.item}」→「${c.destination}」でした。`
      : `なお ${c.valid_from ?? "None"} からは「${c.item}」→「${c.destination}」になります。`)).join("");
    const rel = related.map((r) => `ただし「${r.item}」は「${r.destination}」。`).join("");
    const men = mentions.map((m) => `「${m.item}」の出し方には「${m.text}」ともあります。`).join("");
    const head = b.destination.startsWith("収集しない")
      ? `女川町では${fmt(d0, true)}時点で、「${b.item}」は町のごみ収集に出せません (${b.destination})${period}。`
      : `女川町では${fmt(d0, true)}時点で、「${b.item}」は「${b.destination}」に出します${period}。`;
    Object.assign(res, { status: "ok", answerable: true,
      summary_ja: sentences(head, b.how ? pyRstrip(b.how, "。") + "。" : "", rel, men, ch, `出典: ${pyRstrip(b.source ?? "", "。")}。`) });
    return res;
  }
}
