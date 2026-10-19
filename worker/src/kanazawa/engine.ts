// 金沢市 (区域表と住所の索引で地区を決める型): 住所の文字列 + 日付 → 答えの JSON
// resolve.py の locate_address・_resolve_address・_answer・days・next_after・summarize の移植。文面も同じ
import { consensus, sentences } from "../common.ts";
import { WDJ, dayToIso, fmt, isoToDay, jstIso, jstToday, pyDate, weekday, yearOf } from "../dates.ts";
import { applyFreshness, freshness } from "../freshness.ts";
import { NWS, pf, pyFloatRepr, pyRepr, pyRoundInt, pyRstrip, pyStrip, pySlice, cps, deepClone, sortedPy, cmpPy, toFloat } from "../py.ts";
import { AddressIndex, clean, type Eval, type Reading, type Row } from "./address.ts";

export const BASIS_JA: Record<string, string> = {
  given_address_row: "伝えられた住所が区域表の行に当たる", address_row: "最寄りの住所が区域表の行に当たる", parcel_row: "最寄りの地番が区域表の行に当たる",
  zone_polygon: "区域表から作った区域", near_address: "近くの住所の区域表の行", near_parcel: "近くの地番の区域表の行",
  near_boundary: "近くに別の地区の区域", unindexed_row: "住居表示住所の無い区域表の行 (位置は ABR の街区代表点など)",
  town_row: "区域表のこの町の行 (一部の場合あり)", census_town_row: "この地点を含む国勢調査の町の行 (一部の場合あり)",
  town_row_prefix: "よく似た町名の区域表の行 (町全体) からの推定", conditional_similar_row: "よく似た町名の条件付きの行",
  inferred_lineage: "旧町名の系譜からの推定",
};
const RANK: Record<string, number> = { given_address_row: 110, address_row: 100, zone_polygon: 90, parcel_row: 85, census_town_row: 75, town_row: 70, town_row_prefix: 60,
  unindexed_row: 55, near_address: 50, near_parcel: 45, near_boundary: 40, conditional_similar_row: 35, inferred_lineage: 30 };
const SHOW_DETAIL = new Set(["near_address", "near_parcel", "unindexed_row", "conditional_similar_row", "inferred_lineage"]);
const MAX_CANDS_TEXT = 3;

type Sched = { category: string; official_label: string; rule: string; rule_text: string; occ: Set<number>; exc: Set<number>; sorted: number[] };
type District = { lo: number; hi: number; last: number | null; first: number | null; source: any; schedules: Sched[] };

export class Kanazawa {
  core: any;
  ax: AddressIndex | null;
  LG: string;
  MUNI: string;
  private dist = new Map<string, District>();

  constructor(core: any, addr: any | null) {
    this.core = core;
    this.ax = addr ? new AddressIndex(addr) : null;
    this.LG = core.lg_code;
    this.MUNI = core.name;
  }

  private district(d: string): District {
    let x = this.dist.get(d);
    if (x) return x;
    const p = this.core.districts[d];
    const lo = isoToDay(p.valid_from);
    x = {
      lo, hi: isoToDay(p.valid_to), last: p.year_end[0] ? isoToDay(p.year_end[0]) : null, first: p.year_end[1] ? isoToDay(p.year_end[1]) : null,
      source: p.source,
      schedules: p.schedules.map((s: any) => ({ category: s.category, official_label: s.official_label, rule: s.rule, rule_text: s.rule_text,
        occ: new Set(s.dates.map((o: number) => lo + o)), exc: new Set(s.exceptions.map((o: number) => lo + o)), sorted: s.dates.map((o: number) => lo + o) })),
    };
    this.dist.set(d, x);
    return x;
  }

  base(now: Date): any {
    const c = this.core;
    return { data_version: c.data_version, generated_at: jstIso(now), put_out_by: deepClone(c.put_out_by), not_covered: deepClone(c.not_covered), contact: deepClone(c.contact) };
  }

  // ---------------- 収集日
  calendarRange(d: string): [number, number] { const x = this.district(d); return [x.lo, x.hi]; }

  days(d: string, date: number, n: number): any[] {
    const x = this.district(d);
    const out: any[] = [];
    for (let k = 0; k < n; k++) {
      const day = date + k;
      const o: any = { date: dayToIso(day), weekday: WDJ[weekday(day)], in_calendar_range: x.lo <= day && day <= x.hi };
      if (!o.in_calendar_range) {
        o.collections = null;
        o.note = `金沢市の${yearOf(x.lo)}年度カレンダー (${dayToIso(x.lo)}〜${dayToIso(x.hi)}) の範囲外で分かりません (「収集なし」という意味ではありません)`;
      } else {
        const cols: any[] = [];
        for (const s of x.schedules) {
          if (s.occ.has(day)) {
            const moved = s.exc.has(day);
            cols.push({ category: s.category, official_label: s.official_label, rule_text: s.rule_text, is_exception: moved,
              ...(moved ? { exception_note: `通常の「${s.rule}」とは違う日 (カレンダーの表の日付。違いがあるのは2026年12月・2027年1月だけ)` } : {}) });
          }
        }
        o.collections = cols;
        if (x.last !== null && x.first !== null && x.last < day && day < x.first) {
          o.note = `年末年始: 燃やすごみは ${fmt(x.last, true)} まで、${fmt(x.first, true)} から`;
        }
      }
      out.push(o);
    }
    return out;
  }

  nextAfter(d: string, date: number): any[] {
    const x = this.district(d);
    const res: any[] = [];
    for (const s of x.schedules) {
      const item = { category: s.category, official_label: s.official_label };
      if (date + 1 < x.lo) {
        res.push({ ...item, date: null, weekday: null, null_reason: `${dayToIso(x.lo)} より前の期間のカレンダーは未取得なので、次の収集日は分からない` });
        continue;
      }
      let nxt: number | null = null;
      for (const v of s.sorted) if (v > date && (nxt === null || v < nxt)) nxt = v;
      res.push({ ...item, date: nxt !== null ? dayToIso(nxt) : null, weekday: nxt !== null ? WDJ[weekday(nxt)] : null,
        ...(nxt !== null ? {} : { null_reason: `${yearOf(x.lo)}年度のカレンダー (${dayToIso(x.hi)} まで) にこれより後の日が無い (${yearOf(x.lo) + 1}年度版は未取得)` }) });
    }
    return res.map((v, i) => [v, i] as [any, number]).sort((a, b) => cmpPy(a[0].date ?? "9999", b[0].date ?? "9999") || a[1] - b[1]).map((v) => v[0]);
  }

  sources(d: string): any[] {
    return [deepClone(this.core.rules_source), deepClone(this.district(d).source), deepClone(this.core.put_out_rules)];
  }

  // ---------------- 区域表の行の見せ方
  static row(r: Row): any {
    return { 町名: r.town, 街区: r.blk, "番号・番地": r.ban, 地区: r.district, ...(r.note ? { 注記: "(注意)" + r.note } : {}) };
  }
  rowsOf(town: string): any[] { return (this.ax!.byTown.get(town) ?? []).map(Kanazawa.row); }

  // ---------------- 住所の文字列 → 地区
  locateAddress(text: string): any {
    const ax = this.ax!;
    const [s, stated, other] = clean(text, pyStrip);
    if (other && (stated || !ax.townOf(s)[0])) return { out_of_coverage: other };
    const [rs0, town] = ax.readings(s);
    const seen = new Map<string, Reading>();                    // 同じ読み方は1つに (最初の位置に、後の方を入れる。Python の dict と同じ)
    for (const r of rs0) {
      seen.set(JSON.stringify(Object.entries(r).sort((a, b) => cmpPy(a[0], b[0])).map(([k, v]) => [k, typeof v === "bigint" ? `big:${v}` : v])), r);
    }
    const readings = [...seen.values()];
    const label = (stated ? "金沢市" : "") + s;
    const cands = new Map<string, any>();
    const notes: string[] = [];
    const add = (d: string, kind: string, evidence: string, rows: Row[] = [], official = true) => {
      let c = cands.get(d);
      if (!c) { c = { collection_district: d, basis: [], official: false, evidence: [], how_to_confirm: [] }; cands.set(d, c); }
      c.basis.push({ kind, official, label: BASIS_JA[kind], detail: evidence });
      c.official = c.basis.some((b: any) => b.official);
      c.evidence.push(evidence);
      for (const r of rows) {
        const h = Kanazawa.row(r);
        const hk = JSON.stringify(h);
        if (!c.how_to_confirm.some((x: any) => JSON.stringify(x) === hk) && c.how_to_confirm.length < 8) c.how_to_confirm.push(h);
      }
    };
    const tt = town ? ax.tableTown(town) : null;
    const addr = { input: text, label, readings: readings.map((r) => ({ ...r })), in_rules_table: !!tt, municipality_stated: stated };
    let status = "ambiguous", district: string | null = null, conf: any = 0.4, method = "address_candidates", blocked: any = null;
    const evals: [Reading, ...Eval][] = readings.map((r) => [r, ...ax.evaluate(r)]);
    const col = town && !stated ? (ax.collisions[town] ?? ax.collisions[tt ?? ""] ?? null) : null;
    const collision = col ? `市町村名が書かれていません。${town} は金沢市のほか ${col.examples.slice(0, 3).join("・")}${col.count > 3 ? " など" : ""} にもあります`
      + ` (全国で ${col.count + 1} か所)。` : null;
    const valid = evals.filter(([, ds, , , ex]) => ds && ds.length && ex);
    const noRow = evals.filter(([, , , , ex]) => ex === "no_row").map(([r, , , desc]) => [r, desc] as [Reading, string]);
    if (!town) {
      status = "unresolved"; conf = pf(0); method = "address_not_found";
      blocked = { reason: "address_not_found", town: null, label };
      const head = new RegExp("^[^\\p{Nd}\\-]{2,}", "u").exec(s);
      const similar = head ? ax.knownList.filter((t) => t.startsWith(head[0]) && t !== head[0])
        .sort((a, b) => cps(a).length - cps(b).length || cmpPy(a, b)).slice(0, 10) : [];
      if (similar.length) blocked.similar_towns = similar;
    } else if (!tt) {
      status = "unresolved"; conf = pf(0); method = "place_town_not_in_rules_table";
      blocked = { reason: "place_town_not_in_rules_table", town, source: "住所", label, distance_m: pf(0) };
      for (const [aname, confidence, rid] of ax.viaOld[town] ?? []) {
        add(ax.rows[rid].district, "inferred_lineage", `区域表に ${town} の行が無い。旧町名 ${aname} の行から推定 (系譜の確信度 ${confidence})`, [], false);
      }
    } else if (!valid.length && noRow.length) {
      const nt = noRow[0][0].town;
      status = "unresolved"; conf = pf(0); method = "place_town_not_in_rules_table";
      blocked = { reason: "place_town_not_in_rules_table", town: nt, source: "住所", label, distance_m: pf(0) };
      notes.push(...noRow.map(([, d]) => d));
    } else if (!valid.length) {
      method = "address_not_in_registry";
      notes.push(`住所 ${label} は、国土地理院・デジタル庁 ABR の住所データに見つかりません (${evals.map((e) => e[3]).join("; ")})`);
      const rs = ax.byTown.get(tt)!;
      for (const dd of sortedPy(new Set(rs.map((r) => r.district)))) add(dd, "town_row", `区域表で ${tt} の一部が ${dd}`, rs.filter((r) => r.district === dd));
    } else {
      for (const [r, ds, rows, desc, ex] of valid) {
        const kind = ex === true && ds!.length === 1 && (r.kind === "jukyo" || r.kind === "chiban") ? "given_address_row" : "town_row";
        for (const dd of ds!) {
          const rr = rows.filter((x) => x.district === dd);
          add(dd, kind, desc + (ds!.length > 1 ? ` → ${dd}` : ""), rr.length ? rr : rows.slice(0, 2));
        }
        if (ex !== true || desc.includes("国土地理院の住所に無い")) notes.push(desc);
      }
      const alld = new Set(valid.flatMap(([, ds]) => ds!));
      const sure = valid.every(([, , , , ex]) => ex === true) && !noRow.length;
      if (noRow.length) notes.push(...noRow.map(([, d]) => d + "。この読み方の地区は区域表で決められない"));
      if (valid.length > 1 && alld.size > 1) {
        notes.unshift(`住所 ${label} は ` + valid.map(([, , , d]) => `「${d}」`).join(" とも ") + " とも読めます。番地・号まで正確に書いてください");
      }
      if (alld.size === 1 && sure) {
        district = [...alld][0];
        const rowsd = valid.flatMap(([, , rows]) => rows.filter((x) => x.district === district));
        const noted = rowsd.find((x) => x.note) ?? null;
        if (noted) {
          status = "unresolved"; conf = pf(0); method = "rules_table_note";
          blocked = { reason: "rules_table_note", town: noted.town, district: noted.district, note: "(注意)" + noted.note };
          district = null;
        } else if (collision) {
          method = "municipality_not_stated";
          notes.unshift(collision + `金沢市の${town}なら ${district} 地区です`);
          district = null;
        } else {
          status = "ok"; conf = 0.98;
          method = ({ jukyo: "address_table_row", chiban: "address_parcel_row", block: "address_block", town: "address_town_whole" } as Record<string, string>)[valid[0][0].kind];
        }
      } else {
        method = valid.some(([, , , , ex]) => ex === "needs_koaza" || ex === "needs_chome") ? "address_needs_detail"
          : valid.length > 1 ? "address_two_readings" : "address_row_not_decided";
      }
    }
    if (collision && method !== "municipality_not_stated" && status !== "ok") {
      notes.unshift(pyRstrip(collision, "。") + "。金沢市の住所かどうかも確かめてください");
    }
    const rank = (c: any) => Math.max(...c.basis.map((b: any) => RANK[b.kind] * (b.official ? 1 : 0.5)));
    const ordered = [...cands.values()].map((c, i) => [c, rank(c), i] as [any, number, number]).sort((a, b) => b[1] - a[1] || a[2] - b[2]).map((x) => x[0]);
    return { status, collection_district: district, district_confidence: conf, method, census_town: null, place_name: town, inference: null,
      nearest_address: null, nearest_parcel: null, address: addr, decision_blocked_by: blocked, distance_to_municipal_border_m: null,
      evidence_radii_m: null, candidates: ordered, notes, town_rows: (tt ? this.rowsOf(tt) : []).slice(0, 12) };
  }

  // ---------------- resolve._resolve_address
  resolveAddress(address: string, date: string | null, days: number, now: Date, status: any): any {
    const base: any = this.base(now);
    const bad = (q: any, msg: string) => ({ ...base, query: q, status: "invalid_input", answerable: false, summary_ja: msg });
    if (typeof address !== "string" || !pyStrip(address) || cps(address).length > 200) {
      return bad({ address: pySlice(pyRepr(address), 0, 60) }, "address は住所の文字列 (200文字まで) で指定してください。");
    }
    let d: number | null = null;
    if (date !== null) {
      try { d = pyDate(date, pyStrip); } catch {
        return bad({ address, date: pySlice(pyRepr(date), 0, 40) }, "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。");
      }
    }
    const q = { address, timezone: "Asia/Tokyo", days, date: dayToIso(d ?? jstToday(now)), date_basis: d !== null ? "user" : "server_today_jst" };
    base.query = q;
    const loc = this.locateAddress(address);
    if (loc.out_of_coverage) {
      const name = loc.out_of_coverage;
      return { ...base, status: "out_of_coverage", answerable: false, location: { municipality: { lg_code: null, name } },
        coverage: [{ lg_code: this.LG, name: this.MUNI, calendar: this.core.coverage_calendar }],
        summary_ja: `この住所は${name}で、gomisute-gps はまだ対応していません (対応: ${this.MUNI})。${name}の公式サイトで確認してください。` };
    }
    return this.answer(loc, d ?? jstToday(now), days, base, "address_text", now, status);
  }

  // ---------------- resolve._answer
  answer(loc: any, date: number, days: number, base: any, checkedBy: string, now: Date, status: any): any {
    for (const c of loc.candidates) c.days = this.days(c.collection_district, date, days);
    const anyd = loc.collection_district || (loc.candidates.length ? loc.candidates[0].collection_district : null);
    const [lo, hi] = anyd ? this.calendarRange(anyd) : [null, null];
    const pickLoc: any = {};
    for (const x of ["collection_district", "district_confidence", "method", "census_town", "place_name", "inference", "nearest_address", "nearest_parcel",
      "address", "decision_blocked_by", "distance_to_municipal_border_m", "evidence_radii_m"]) pickLoc[x] = x in loc ? loc[x] : null;
    const res: any = { ...base, status: loc.status,
      location: { municipality: { lg_code: this.LG, name: this.MUNI, checked_by: checkedBy }, ...pickLoc },
      candidates: loc.candidates, town_rows: loc.town_rows, notes: loc.notes, holiday_policy: this.core.holiday_policy,
      calendar: { valid_from: lo !== null ? dayToIso(lo) : null, valid_to: hi !== null ? dayToIso(hi) : null } };
    const pool = loc.candidates.filter((c: any) => loc.status !== "ok" || c.collection_district === loc.collection_district);
    res.official_notes = [...new Set(pool.flatMap((c: any) => c.how_to_confirm.filter((h: any) => h["注記"])
      .map((h: any) => `公式の区域表は ${h["町名"]} を ${h["地区"]}地区に割り当て、「${h["注記"]}」と注記しています (注記の意味は公式ページに説明なし)`)))];
    if (loc.status === "ok") {
      const dd = loc.collection_district;
      res.days = loc.candidates.find((c: any) => c.collection_district === dd).days;
      res.next_after_date = this.nextAfter(dd, date);
      res.sources = this.sources(dd);
      res.answerable = res.days.every((x: any) => x.in_calendar_range);
    } else {
      res.answerable = false;
      res.consensus = loc.status === "ambiguous" ? consensus(loc.candidates) : null;
      res.sources = [deepClone(this.core.rules_source), ...loc.candidates.map((c: any) => this.sources(c.collection_district)[1]), deepClone(this.core.put_out_rules)];
    }
    applyFreshness(res, freshness(status, res.sources.map((x: any) => x.url), now));
    res.summary_ja = this.summarize(res);
    return deepClone(res);
  }

  // ---------------- 要約 (resolve.summarize)
  summarize(res: any): string {
    let text = this._summarize(res);
    const fr = res.freshness?.status;
    if (fr === "stale") text += "公式ページの変更の確認が古いので、念のため公式ページでも確かめてください。";
    if (fr === "partly_unchecked") text += "元にした公式ページの一部を確認できなかったので、念のため公式ページでも確かめてください。";
    if (fr === "unchecked") text += "元にした公式ページが変わっていないかを確かめられていないので、念のため公式ページでも確かめてください。";
    return text;
  }

  private _summarize(res: any): string {
    const st = res.status, loc = res.location;
    const C = this.core.contact;
    const b0 = loc.decision_blocked_by || {};
    if (b0.reason === "source_changed") {
      const ch = b0.changed.slice(0, 3).map((x: any) => `「${x.title}」` + (x.gone ? "(ページが無くなった)" : "")).join("、");
      const before = loc.collection_district_before_change;
      return `このデータの元にした公式ページ ${ch} が、${b0.checked_at.slice(0, 10)} の確認で、データを作ったときから変わっていました。`
        + `作り直して確かめるまでは確かな答えとして返しません` + (before ? ` (変更前のデータでは ${before}地区)` : "") + "。"
        + `公式ページで確かめるか、${C.name} (${C.phone}) に確認してください。`;
    }
    const days0: any[] = (res.days && res.days.length ? res.days : null) ?? (res.candidates && res.candidates.length ? (res.candidates[0].days ?? []) : []);
    const outOfRange = days0.some((d) => !d.in_calendar_range);
    const year = days0.length > 0 && (outOfRange || new Set(days0.map((d) => d.date.slice(0, 4))).size > 1);
    const onotes = (res.official_notes ?? []).map((n: string) => `${n}。`).join("");
    const fy = outOfRange && res.calendar?.valid_from ? `地区は ${res.calendar.valid_from.slice(0, 4)}年度の区域表によります。` : "";
    if (st === "ok") {
      const inf = loc.inference ? ` (${loc.inference})` : "";
      const where = this.where(loc);
      return sentences(`この場所${where ? ` (${where}) ` : ""}は金沢市の${loc.collection_district}地区 (収集カレンダー上の地区名) です${inf}。`,
        daystext(res.days, year),
        res.days.some((d: any) => d.collections && d.collections.length) ? "出すのは収集日の午前8時30分まで (金沢市「ごみの出し方のルール」)。" : "",
        `出典: 金沢市「${res.sources[1].title}」。地区の確信度 ${confText(loc.district_confidence)}。`, fy, onotes);
    }
    const cands: any[] = res.candidates || [];
    const candText = candsText(cands);
    let perDay = "";
    if (cands.length === 1) perDay = `${cands[0].collection_district}地区なら、` + daystext(cands[0].days, year);
    (res.consensus || []).forEach((cs: any, i: number) => {
      const d = isoToDay(cs.date);
      if (cs.all_candidates_agree === null) perDay += `${fmt(d, true)}は金沢市のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)。`;
      else if (cs.all_candidates_agree) perDay += `${fmt(d, year)}はどの候補でも${labels(cands[0].days[i].collections)}。`;
      else perDay += `${fmt(d, year)}は候補によって違います (` + cands.slice(0, MAX_CANDS_TEXT).map((c) => `${c.collection_district}: ${labels(c.days[i].collections)}`).join(" / ")
        + (cands.length > MAX_CANDS_TEXT ? " / ほか" : "") + ")。";
    });
    const ye = [...new Set(cands.slice(0, MAX_CANDS_TEXT).flatMap((c) => c.days.filter((d: any) => (d.note || "").startsWith("年末年始"))
      .map((d: any) => `${c.collection_district}地区は${d.note.replaceAll("年末年始: ", "")}`)))];
    const yeText = ye.length && cands.length > 1 ? "年末年始: " + ye.join("、") + "。" : "";
    const rows: any[] = res.town_rows || [];
    const tip = rows.length ? "区域表の行 (町名・街区・番号): " + rows.slice(0, 6).map((r) =>
      `${r["町名"]}${r["街区"] ? " 街区" + r["街区"] : ""}${pyStrip(r["番号・番地"]) ? " " + pyStrip(r["番号・番地"]) : ""}→${r["地区"]}`).join("、")
      + (rows.length > 6 ? "、ほか" : "") + "。" : "";
    const confirm = "お住まいの住所の街区・番号 (地番) を確かめてください。";
    if (st === "ambiguous" && cands.length === 1 && loc.method === "area_only_evidence") {
      return sentences(`この地点の近くに住所・地番の位置データが無く、町の区域 (国勢調査の小地域) だけでは地区を確定できません。候補は ${candText} です。`, perDay, tip, confirm, fy, onotes);
    }
    if (st === "ambiguous" && cands.length === 1 && loc.address) {
      const why = (res.notes ?? []).slice(0, 2).map((n: string) => pyRstrip(n, "。") + "。").join("");
      return sentences(why, `地区を確定できません。候補は ${candText} です。`, perDay, tip, confirm, fy, onotes);
    }
    if (st === "ambiguous" && cands.length === 1) {
      return sentences(`この地点を直接示す区域表の根拠が無く、地区を確定できません。近くの根拠からの候補は ${candText} だけです。`, perDay, tip, confirm, fy, onotes);
    }
    if (st === "ambiguous") {
      const m = loc.method ?? "";
      const why = m.startsWith("address_") || m === "municipality_not_stated" ? res.notes.slice(0, 2).map((n: string) => pyRstrip(n, "。") + "。").join("") : "";
      return sentences(why, `地区を1つに決められません。候補: ${candText}。`, perDay, yeText, tip, confirm, fy, onotes);
    }
    if (st === "unresolved") {
      const b = loc.decision_blocked_by || {};
      const contact = `${C.name} (${C.phone}) に確認してください。`;
      let head: string;
      if (b.reason === "source_changed") {
        const ch = b.changed.slice(0, 3).map((x: any) => `「${x.title}」`).join("、");
        head = `このデータの元にした公式ページ ${ch} が、${b.checked_at.slice(0, 10)} の確認で、データを作ったときから変わっていました。`
          + `作り直して確かめるまでは確かな答えとして返しません (変更前のデータでは ${loc.collection_district_before_change ?? "None"}地区)。公式ページで確かめてください。`;
      } else if (b.reason === "address_not_found") {
        head = `住所 (${b.label ?? "None"}) の町名を、金沢市の町名として見つけられませんでした。`
          + (b.similar_towns && b.similar_towns.length ? `よく似た町名は ${b.similar_towns.join("・")} です。丁目まで含めて町名を確かめてください。` : "町名の書き方を確かめてください。");
      } else if (b.reason === "place_town_not_in_rules_table" && b.source === "住所") {
        head = `住所 ${b.label} の町名 ${b.town} は金沢市の収集区域表に載っていません。`;
      } else if (b.reason === "nearby_town_not_in_rules_table") {
        const w = this.where(loc);
        head = (w.startsWith("最寄り") ? `${w.replaceAll("から約", " (約").replace("m", "m)")} の町は区域表にありますが、` : "")
          + (b.source === "国勢調査の町" ? `約${pyRoundInt(toFloat(b.distance_m))}m 先に、区域表に載っていない町 (${b.town}、国勢調査の町名) があり、`
            : `約${pyRoundInt(toFloat(b.distance_m))}m 先に区域表に載っていない町名 (${b.town}) の${b.source} (${b.label}) があり、`)
          + "この場所がどちらの町か決められないため、地区を確定できません。";
      } else if (b.reason === "near_municipal_boundary") {
        head = `この場所は金沢市と隣の市町の境界から約${pyRoundInt(toFloat(b.distance_m))}m (位置の誤差の範囲) にあり、金沢市かどうかも含めて地区を確定できません。`;
      } else if (b.reason === "rules_table_note") {
        head = `公式の区域表は ${b.town} を ${b.district}地区に割り当てていますが、「${b.note}」と注記があり、収集地区を確定できません (注記の意味は公式ページに説明なし)。`;
      } else if (b.reason === "census_town_not_in_rules_table") {
        head = `この地点を含む町 (${b.town}、国勢調査の町名) は金沢市の収集区域表に載っていません。`;
      } else if (b.reason === "place_town_not_in_rules_table") {
        head = `この場所 (${b.source} ${b.label}、約${pyRoundInt(toFloat(b.distance_m))}m) の町名 ${b.town} は金沢市の収集区域表に載っていません。`;
      } else {
        head = `この場所 (${loc.place_name || "不明"}) について、金沢市の収集区域表の根拠が見つかりません。`;
      }
      const off = cands.filter((c) => c.official);
      let guess = "";
      if (cands.length) {
        const per = cands.slice(0, MAX_CANDS_TEXT).map((c) => `${c.collection_district}地区なら、` + daystext(c.days, year)).join("");
        guess = (off.length ? "区域表の行による候補 (確定ではない): " : "参考 (公式ではない) の候補: ") + candText + "。" + per;
      }
      return sentences(head, guess, tip, contact, b.reason !== "rules_table_note" ? onotes : "");
    }
    return "";
  }

  where(loc: any): string {
    if (loc.address) return `住所 ${loc.address.label}`;
    const a = loc.nearest_address, p = loc.nearest_parcel;
    const xs = [a, p].filter((x) => x);
    const here = xs.length ? xs.reduce((m, x) => (toFloat(x.distance_m) < toFloat(m.distance_m) ? x : m)) : null;
    if (here) return `最寄りの${here === a ? "住所" : "地番"} ${here.label} から約${pyRoundInt(toFloat(here.distance_m))}m`;
    return loc.place_name ? `${loc.place_name}の町域` : "";
  }
}

const confText = (v: any) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : pyFloatRepr(v)) : pyFloatRepr(v.v));

function labels(cols: any[]): string {
  return cols.map((x) => x.official_label + (x.is_exception ? `(通常の「${x.rule_text}」とは違う日。カレンダーの表のとおり)` : "")).join("・") || "収集なし";
}

function daytext(day: any, year: boolean, seen: Set<string> | null): string {
  const d = isoToDay(day.date);
  if (!day.in_calendar_range) return `${fmt(d, true)}は金沢市のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)`;
  let note: string | null = day.note || null;
  if (note && seen) {
    if (seen.has(note)) note = null; else seen.add(note);
  }
  return `${fmt(d, year)}は${labels(day.collections)}` + (note ? ` (${note})` : "");
}

function daystext(days: any[], year: boolean): string {
  const seen = new Set<string>();
  return days.map((d) => daytext(d, year, seen)).join("。") + "。";
}

const OLD_RE = new RegExp(`旧町名 (${NWS}+) の行`, "u");
function candText(c: any): string {
  const olds = c.basis.filter((b: any) => b.kind === "inferred_lineage").map((b: any) => OLD_RE.exec(b.detail)![1]);
  const why: string[] = c.basis.filter((b: any) => b.kind !== "inferred_lineage").map((b: any) => (SHOW_DETAIL.has(b.kind) ? b.detail : b.label));
  if (olds.length) why.push(`旧町名 ${[...new Set(olds)].join("・")} の行からの推定`);
  const w = [...new Set(why.map((x) => x.replaceAll("。", "、")))].join("、");
  return `${c.collection_district}地区 (${w}${c.official ? "" : "、公式の記載ではない"})`;
}

function candsText(cands: any[]): string {
  const t = cands.slice(0, MAX_CANDS_TEXT).map(candText).join("、");
  return t + (cands.length > MAX_CANDS_TEXT ? `、ほか ${cands.length - MAX_CANDS_TEXT} 地区 (candidates に全部)` : "");
}

