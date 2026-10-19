// 金沢市: 利用者が伝えた住所の文字列 → 読み方 → 区域表の行 (kzaddress.py と townnorm.py の移植)
// 文字の読み方 (clean・readings・norm_town) はそのまま移し、「その住所が実在するか・区域表のどの行か」は
// build_packs.py が Python の evaluate() で先に計算したパック (addr) を引く
import { D, END, PyError, WS, cmpPy, cps, isDigit, pyInt, sortedPy, truthy } from "../py.ts";

// ---------------------------------------------------------------- townnorm.norm_town
const KAN: Record<string, number> = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };

/** townnorm.kan2num (Python と同じく、読めない形は None、壊れた形は例外) */
export function kan2num(s: string): number | null {
  if (s in KAN) return KAN[s];
  if (s.startsWith("十")) return 10 + (KAN[cps(s).slice(1).join("")] ?? 0);
  if (s.includes("十")) {
    const parts = s.split("十");
    if (parts.length !== 2) throw new PyError("ValueError");      // a, b = s.split("十") の数が合わない
    const [a, b] = parts;
    if (!(a in KAN)) throw new PyError("KeyError");
    return KAN[a] * 10 + (KAN[b] ?? 0);
  }
  return null;
}
const nn = (v: number | null) => (v === null ? "None" : String(v));

const FW_DIGITS = /[０-９]/g;
const NT1 = new RegExp(`(${D}+)丁${END}`, "gu");
const NT2 = new RegExp(`([^${D}丁番])(${D}+)${END}`, "gu");
const NT3 = /([一二三四五六七八九十]+)丁目/gu;
const NT4 = /([一二三四五六七八九十]+)番丁/gu;
const VARIANT: Record<string, string> = { 舘: "館", 薮: "藪", 刈: "苅", 嶽: "岳", 傅: "伝" };

const _ntCache = new Map<string, string>();
/** townnorm.norm_town: 町名の表記ゆれをそろえる */
export function normTown(s0: string): string {
  const hit = _ntCache.get(s0);
  if (hit !== undefined) return hit;
  let s = s0.replace(FW_DIGITS, (c) => String.fromCharCode(c.charCodeAt(0) - 0xff10 + 48)).replaceAll(" ", "").replaceAll("　", "");
  s = s.replaceAll("ヶ", "ケ").replaceAll("ヵ", "ケ").replaceAll("之", "の");
  s = s.replace(/[舘薮刈嶽傅]/gu, (c) => VARIANT[c]);
  s = s.replace(NT1, "$1丁目");
  s = s.replace(NT2, "$1$2丁目");
  s = s.replace(NT3, (_m, g: string) => `${nn(kan2num(g))}丁目`);
  s = s.replace(NT4, (_m, g: string) => `${nn(kan2num(g))}番丁`);
  if (_ntCache.size > 50000) _ntCache.clear();
  _ntCache.set(s0, s);
  return s;
}

// ---------------------------------------------------------------- kzaddress.clean
const KANJI = "〇一二三四五六七八九";
/** kzaddress.kanji_int → 文字列 (str(kanji_int(t))) */
export function kanjiIntStr(t: string): string {
  const cs = cps(t);
  if (cs.every((ch) => KANJI.includes(ch))) {
    const digits = cs.map((ch) => String(KANJI.indexOf(ch))).join("");
    return BigInt(digits).toString();
  }
  let n = 0, cur = 0;
  for (const ch of cs) {
    if (KANJI.includes(ch)) cur = KANJI.indexOf(ch);
    else if (ch === "十") { n += (cur || 1) * 10; cur = 0; }
    else if (ch === "百") { n += (cur || 1) * 100; cur = 0; }
  }
  return String(n + cur);
}

const ANY = "[^\\n]";                       // Python の . (改行以外)
const NUMEND = `${D}+(?:番地?${D}*(?:号)?|号|-${D}+(?:-${D}+){0,2})`;
const C_DASH = /[‐‑‒–—―─━−－]/gu;
const C_POSTAL = new RegExp(`^${WS}*(日本国?)?${WS}*〒?${WS}*${D}{3}-?${D}{4}${WS}*`, "u");
const C_JAPAN = new RegExp(`^${WS}*日本国?${WS}*`, "u");
const C_KAN1 = /[一二三四五六七八九十百〇]+(?=番|号|-)/gu;
const C_KAN2 = new RegExp(`(?<=番地|${ANY}番)[一二三四五六七八九十百〇]+(?=号?(${WS}|$))`, "gu");
const C_SEP = new RegExp(`(?<=${D})${WS}*[-ーの]${WS}*(?=${D})`, "gu");
const C_BANCHI_NO = new RegExp(`(?<=${D}番地)の(?=${D})`, "gu");
const C_ROOM = new RegExp(`^(${ANY}*?${NUMEND})${WS}${ANY}*${END}`, "u");
const C_SPACE_NUM = new RegExp(`(?<=${D})${WS}+(?=${D})`, "gu");
const C_SPACES = new RegExp(`${WS}+`, "gu");
const C_GO = new RegExp(`^(${ANY}*?${D}+番地?${D}+号)${ANY}*${END}`, "u");
const C_PREF = new RegExp(`^(北海道|東京都|(?:京都|大阪)府|${ANY}{2,3}県)`, "u");
const C_CITY_AFTER_PREF = new RegExp(`^(${ANY}{1,4}郡${ANY}{1,4}?[町村]|${ANY}{1,5}?市(?:${ANY}{1,4}?区)?|${ANY}{1,4}?区)`, "u");
const C_CITY = new RegExp(`^(${ANY}{1,4}郡${ANY}{1,4}?[町村]|${ANY}{1,4}市)`, "u");
const C_AZA = /^(大字|字)/u;

/** kzaddress.clean: 住所の文字列 → [本体, 市町村名が書かれていたか, 対象外の自治体名 or null] */
export function clean(text: string, strip: (s: string) => string): [string, boolean, string | null] {
  let s = strip(text.normalize("NFKC"));
  s = s.replace(C_DASH, "-");
  s = s.replace(C_POSTAL, "");
  s = s.replace(C_JAPAN, "");
  s = s.replace(C_KAN1, (x) => kanjiIntStr(x));
  s = s.replace(C_KAN2, (x) => kanjiIntStr(x));
  s = s.replace(C_SEP, "-");
  s = s.replace(C_BANCHI_NO, "");
  let m = C_ROOM.exec(s);
  if (m) s = m[1];
  s = s.replace(C_SPACE_NUM, "-");
  s = s.replace(C_SPACES, "");
  s = s.replace(C_GO, "$1");
  m = C_PREF.exec(s);
  if (m && m[1] !== "石川県") {
    const c = C_CITY_AFTER_PREF.exec(s.slice(m[1].length));
    return [s, true, m[1] + (c ? c[1] : "")];
  }
  if (m) s = s.slice(m[1].length);
  if (s.startsWith("金沢市")) return [s.slice(3).replace(C_AZA, ""), true, null];
  m = C_CITY.exec(s);
  return [s.replace(C_AZA, ""), false, m ? m[1] : null];
}

// ---------------------------------------------------------------- パック (addr) の索引
type Row = { town: string; blk: string; ban: string; district: string; note: string; kind: string;
  blk_from: number | null; blk_to: number | null; ban_from: number | null; ban_to: number | null; id: number };
type Num = number | bigint;
export type Reading = { kind: string; town: string; blk?: Num | null; num?: Num | null; koaza?: string; rest?: string };
type TownData = {
  jukyo: Map<number, Map<number, number>>;                  // 街区 → 住居番号 → 行 (なければ -1)
  blocks: Map<number, [unknown, string[] | null, number[], number]>;   // 街区 → evaluate(block) の結果
  parcelsByNum: Map<number, [string, number][]>;            // 番号 → [小字, 行] (小字の順)
};
export type Eval = [string[] | null, Row[], string, true | false | "undecided" | "no_row" | "needs_koaza" | "needs_chome"];

export class AddressIndex {
  rows: Row[];
  byTown: Map<string, Row[]>;
  known: Set<string>;
  knownList: string[];
  blockTowns: Set<string>;
  collisions: Record<string, { count: number; examples: string[] }>;
  viaOld: Record<string, [string, string, number][]>;
  uniform: Map<string, string | null>;
  private raw: Record<string, any>;
  private towns = new Map<string, TownData>();
  private sibCache = new Map<string, string[]>();

  constructor(pack: any) {
    this.rows = pack.rows.map((r: any[], id: number) => ({ town: r[0], blk: r[1], ban: r[2], district: r[3], note: r[4], kind: r[5],
      blk_from: r[6], blk_to: r[7], ban_from: r[8], ban_to: r[9], id }));
    this.byTown = new Map(Object.entries(pack.by_town as Record<string, number[]>).map(([t, ids]) => [t, ids.map((i) => this.rows[i])]));
    this.known = new Set(pack.known);
    this.knownList = pack.known;
    this.blockTowns = new Set(pack.block_towns);
    this.collisions = pack.collisions;
    this.viaOld = pack.via_old;
    this.raw = pack.towns;
    this.uniform = new Map();
    for (const [t, rs] of this.byTown) this.uniform.set(t, new Set(rs.map((r) => r.district)).size === 1 ? rs[0].district : null);
  }

  /** kzrules.table_town: ABR の町名 → 区域表の町名 (「町」の有無だけ違うもの) */
  tableTown(t: string): string | null {
    if (this.byTown.has(t)) return t;
    if (t && this.byTown.has(t + "町")) return t + "町";
    return null;
  }
  rowsOf(tt: string | null): Row[] { return (tt && this.byTown.get(tt)) || []; }

  private town(t: string): TownData | null {
    if (this.towns.has(t)) return this.towns.get(t)!;
    const r = Object.prototype.hasOwnProperty.call(this.raw, t) ? this.raw[t] : null;
    if (!r) return null;
    const runs = (rr: number[]) => { const out: number[] = []; for (let i = 0; i < rr.length; i += 2) for (let k = 0; k < rr[i]; k++) out.push(rr[i + 1]); return out; };
    const undelta = (d: number[]) => { let x = 0; return d.map((v) => (x += v)); };
    const td: TownData = { jukyo: new Map(), blocks: new Map(), parcelsByNum: new Map() };
    for (const [blk, d, rr] of r.j ?? []) {
      const nums = undelta(d), vals = runs(rr), m = new Map<number, number>();
      nums.forEach((n, i) => m.set(n, vals[i]));
      td.jukyo.set(blk, m);
    }
    const b: number[] = r.b ?? [];
    for (let i = 0; i < b.length; i += 2) td.blocks.set(b[i], r.bo[b[i + 1]]);
    for (const [kz, d, rr] of r.p ?? []) {
      const nums = undelta(d), vals = runs(rr);
      nums.forEach((n, i) => {
        if (!td.parcelsByNum.has(n)) td.parcelsByNum.set(n, []);
        td.parcelsByNum.get(n)!.push([kz, vals[i]]);
      });
    }
    for (const v of td.parcelsByNum.values()) v.sort((x, y) => cmpPy(x[0], y[0]));
    this.towns.set(t, td);
    return td;
  }
  private jukyoNums(t: string, blk: Num | null | undefined): Map<number, number> | undefined {
    return typeof blk === "number" ? this.town(t)?.jukyo.get(blk) : undefined;
  }
  private block(t: string, blk: Num | null | undefined) {
    return typeof blk === "number" ? this.town(t)?.blocks.get(blk) : undefined;
  }
  private parcelsAt(t: string, num: Num | null | undefined): [string, number][] {
    return (typeof num === "number" ? this.town(t)?.parcelsByNum.get(num) : undefined) ?? [];
  }
  private row(id: number): Row | null { return id >= 0 ? this.rows[id] : null; }

  /** town_of: 頭から一番長く当たる町名 → [町名, 残り] */
  townOf(t: string): [string | null, string] {
    const c = cps(t);
    for (let i = c.length; i > 0; i--) {
      const nt = normTown(c.slice(0, i).join(""));
      if (this.known.has(nt)) {
        const rest = c.slice(i).join("");
        if (rest && /^(?:町|丁目|通|区)/u.test(rest)) continue;
        const r1 = c[i] ?? "";
        if ((isDigit(c[i - 1]) && (r1 === "-" || r1 === "番")) || (isDigit(c[i - 1]) && r1 !== "" && isDigit(r1))) continue;
        return [nt, rest];
      }
    }
    return [null, t];
  }

  chomeSiblings(town: string): string[] {
    const hit = this.sibCache.get(town);
    if (hit) return hit;
    const re = new RegExp(`^${D}+丁目$`, "u");
    const out = sortedPy(this.knownList.filter((x) => x.startsWith(town) && re.test(x.slice(town.length))));
    this.sibCache.set(town, out);
    return out;
  }
  private siblingChiban(base: string, num: Num): Reading[] {
    return this.chomeSiblings(base).filter((ct) => this.parcelsAt(ct, num).length > 0).map((ct) => ({ kind: "chiban", town: ct, koaza: "", num }));
  }
  private siblingJukyo(base: string, blk: Num, num: Num): Reading[] {
    return this.chomeSiblings(base).filter((ct) => typeof num === "number" && (this.jukyoNums(ct, blk)?.has(num) ?? false))
      .map((ct) => ({ kind: "jukyo", town: ct, blk, num }));
  }

  // ---------------- 読み方
  readings(s: string): [Reading[], string | null] {
    const out: Reading[] = [];
    let m = new RegExp(`^(${ANY}+?)(${D}+)-(${D}+)-(${D}+)(?:-${D}+)?$`, "u").exec(s);       // 笠舞2-29-5
    if (m) {
      const base = m[1].replace(new RegExp(`(大字|字)${END}`, "u"), "");
      const ct = normTown(`${base}${m[2]}丁目`);
      if (this.known.has(ct)) {
        out.push({ kind: "jukyo", town: ct, blk: pyInt(m[3]), num: pyInt(m[4]) });
        out.push({ kind: "chiban", town: ct, koaza: "", num: pyInt(m[3]) });
        return [out, ct];
      }
    }
    const [town, rest0] = this.townOf(s);
    m = new RegExp(`^(${ANY}+?)(${D}+)-(${D}+)$`, "u").exec(s);                            // 笠舞2-29
    if (m && !town) {
      const base = m[1].replace(new RegExp(`(大字|字)${END}`, "u"), "");
      const ct = normTown(`${base}${m[2]}丁目`);
      if (this.known.has(ct)) {
        const o: Reading[] = [{ kind: "block", town: ct, blk: pyInt(m[3]) }, { kind: "chiban", town: ct, koaza: "", num: pyInt(m[3]) }];
        return [[...o, ...this.siblingJukyo(base, pyInt(m[2]), pyInt(m[3])), ...this.siblingChiban(base, pyInt(m[2]))], ct];
      }
    }
    if (!town) return [out, null];
    const rest = rest0.replace(/^(大字|字)/u, "");
    if (!rest) {
      const o: Reading[] = [{ kind: "town", town }];
      if (this.chomeSiblings(town).length) o.push({ kind: "needs_chome", town, blk: null, num: null });
      return [o, town];
    }
    m = new RegExp(`^(${D}+)-(${D}+)-(${D}+)(?:-${D}+)?$`, "u").exec(rest);                 // 丁目-街区-住居番号
    if (m) {
      const ct = normTown(`${town}${m[1]}丁目`);
      if (this.known.has(ct)) {
        out.push({ kind: "jukyo", town: ct, blk: pyInt(m[2]), num: pyInt(m[3]) });
        out.push({ kind: "chiban", town: ct, koaza: "", num: pyInt(m[2]) });
      }
      out.push({ kind: "chiban", town, koaza: "", num: pyInt(m[1]) });
      return [out, town];
    }
    m = new RegExp(`^(${D}+)(番地?|-)(${D}+)(号)?$`, "u").exec(rest);                       // 29番5号 / 29-5 / 29番地5
    if (m) {
      const a = pyInt(m[1]), sep = m[2], b = pyInt(m[3]), go = m[4];
      out.push({ kind: "jukyo", town, blk: a, num: b });
      if (!go) out.push({ kind: "chiban", town, koaza: "", num: a });
      if (!go && sep === "-") {
        const ct = normTown(`${town}${a}丁目`);
        if (this.known.has(ct)) out.push({ kind: "block", town: ct, blk: b });
        out.push(...this.siblingJukyo(town, a, b));
      }
      if (!go && this.chomeSiblings(town).length) out.push(...this.siblingChiban(town, a));
      if (go && !this.blockTowns.has(town) && this.chomeSiblings(town).length) out.push({ kind: "needs_chome", town, blk: a, num: b });
      return [out, town];
    }
    m = new RegExp(`^(${D}+)(番地?)?$`, "u").exec(rest);                                     // 7番 / 291番地 / 7
    if (m) {
      out.push({ kind: "block", town, blk: pyInt(m[1]) });
      out.push({ kind: "chiban", town, koaza: "", num: pyInt(m[1]) });
      return [out, town];
    }
    m = new RegExp(`^([^${D}\\-]+?)(${D}+)(?:(?:番地?|-)${D}*)?(?:号)?$`, "u").exec(rest);    // 南291番地4 / ロ21-3
    if (m) {
      const kz = m[1].replace(/^(大字|字)/u, "");
      out.push({ kind: "chiban", town, koaza: kz, num: pyInt(m[2]) });
      return [out, town];
    }
    return [[{ kind: "unparsed", town, rest }], town];
  }

  // ---------------- 1つの読み方 → (地区, 区域表の行, 説明, 実在するか)
  evaluate(r: Reading): Eval {
    const t = r.town;
    const tt = this.tableTown(t);
    const rs = this.rowsOf(tt);
    const whole = rs.find((x) => x.kind === "town" || x.kind === "default") ?? null;
    const dset = (rows: Row[]) => sortedPy(new Set(rows.map((x) => x.district)));
    const uni = tt !== null && truthy(this.uniform.get(tt));
    if (r.kind === "town") {
      const ds = dset(rs);
      if (!rs.length) return [null, [], `町 ${t} は区域表に無い`, false];
      const sure = whole !== null && ds.length === 1;
      return [ds, rs, `町 ${t} の区域表の行` + (sure ? "" : " (町の一部の行しか無い、または地区が分かれる。番地まで要る)"), sure ? true : "undecided"];
    }
    if (r.kind === "jukyo") {
      const desc = `住居表示 ${t} ${r.blk}番${r.num}号`;
      const nums = this.jukyoNums(t, r.blk);
      const numKnown = typeof r.num === "number" && (nums?.has(r.num) ?? false);
      const blkKnown = (nums?.size ?? 0) > 0 || this.block(t, r.blk) !== undefined;
      if (!(numKnown || blkKnown)) return [null, [], `${desc} は見つからない`, false];
      if (!rs.length) return [null, [], `${desc} (区域表に ${t} の行が無い)`, "no_row"];
      if (uni && whole) return [[whole.district], [whole], desc, true];
      if (!numKnown) {
        const [bds, brows, , bex] = this.evaluate({ kind: "block", town: t, blk: r.blk });
        const note = `${desc} (住居番号 ${r.num} は国土地理院の住所に無い。街区 ${r.blk}番 はある)`;
        return [bds, brows, note, bex === true && bds && bds.length === 1 ? true : "undecided"];
      }
      const row = this.row(nums!.get(r.num as number)!);
      if (!row) return [dset(rs), rs, `${desc} (区域表の行では言い切れない)`, "undecided"];
      return [[row.district], [row], desc, true];
    }
    if (r.kind === "block") {
      const o = this.block(t, r.blk);
      if (!o) return [null, [], `${t} ${r.blk}番 は見つからない`, false];
      const [ex, ds, ids, variant] = o;
      const texts = [`${t} ${r.blk}番 (区域表に ${t} の行が無い)`, `${t} ${r.blk}番 (街区)`,
        `${t} ${r.blk}番 (街区。区域表の行では言い切れない)`, `${t} ${r.blk}番 (街区。住居番号が無いので街区の中の行をすべて見る)`];
      return [ds, ids.map((i) => this.rows[i]), texts[variant], ex as Eval[3]];
    }
    if (r.kind === "chiban") {
      const at = this.parcelsAt(t, r.num);                      // この番号の [小字, 行] (小字の順)
      const koaza = r.koaza ?? "";
      const others = !koaza ? at.filter(([kz]) => kz).map(([kz]) => kz) : [];
      const hit = at.find(([kz]) => kz === koaza);
      if (!hit || others.length) {
        if (!koaza && at.length) {                              // 小字が抜けている
          const kzs = at.map(([kz]) => kz);
          const ds = new Set<string>();
          for (const [, id] of at) {
            const row = rs.length ? this.row(id) : null;
            if (row) ds.add(row.district); else for (const x of rs) ds.add(x.district);
          }
          return [ds.size ? sortedPy(ds) : null, rs, `地番 ${t}${r.num}番地 (小字が書かれていない。ありうる小字: ${kzs.slice(0, 6).map((x) => x || "(小字なし)").join("・")})`, "needs_koaza"];
        }
        return [null, [], `地番 ${t}${koaza}${r.num}番地 は見つからない`, false];
      }
      if (!rs.length) return [null, [], `地番 ${t}${koaza}${r.num}番地 (区域表に ${t} の行が無い)`, "no_row"];
      if (uni && whole) return [[whole.district], [whole], `地番 ${t}${koaza}${r.num}番地`, true];
      const row = this.row(hit[1]);
      return [row ? [row.district] : dset(rs), row ? [row] : rs, `地番 ${t}${koaza}${r.num}番地` + (row ? "" : " (区域表の行では言い切れない)"), row ? true : "undecided"];
    }
    if (r.kind === "needs_chome") {
      const sib = this.chomeSiblings(t);
      const ds = new Set<string>();
      for (const ct of sib) {
        const rs2 = this.rowsOf(this.tableTown(ct));
        if (r.blk === null || r.blk === undefined) { for (const x of rs2) ds.add(x.district); continue; }
        const row = rs2.length ? matchRow(rs2, r.blk, r.num!) : null;
        if (row) ds.add(row.district);
      }
      return [ds.size ? sortedPy(ds) : null, [], `${t} の丁目が書かれていない (${sib.join("・")})`, "needs_chome"];
    }
    return [null, [], `住所の「${r.rest ?? ""}」の部分を、番地・号として読めない`, false];
  }
}

/** kzrules.match_row: 住居表示 (街区 blk, 住居番号 num) に最も具体的に当たる区域表の行 */
export function matchRow(rs: Row[], blk: Num, num: Num): Row | null {
  let best: [number, Row] | null = null;
  for (const r of rs) {
    if (r.kind === "banchi" || r.kind === "block") {
      if (r.blk_from !== null && !(r.blk_from <= blk && blk <= r.blk_to!)) continue;
      if (r.ban_from !== null) {
        if (r.blk_from === null) continue;
        if (!(r.ban_from <= num && num <= r.ban_to!)) continue;
      }
      const spec = (r.blk_from !== null ? 1 : 0) + (r.ban_from !== null ? 1 : 0);
      if (best === null || spec > best[0]) best = [spec, r];
    }
  }
  if (best) return best[1];
  for (const r of rs) if (r.kind === "default" || r.kind === "town") return r;
  return null;
}
export type { Row };
