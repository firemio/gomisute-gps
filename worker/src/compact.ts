// mcp_server.compact: LLM が答えるのに要る項目だけの要約 JSON (候補・確かめる行・規則と違う日の印・見出しの注は削らない)
import { truthy } from "./py.ts";

const LOC_KEYS = ["municipality", "collection_district", "district_confidence", "method", "place_name", "place_match",
  "inference", "decision_blocked_by", "collection_district_before_change"];
const TOP_KEYS = ["consensus", "official_notes", "put_out_by", "not_covered", "holiday_policy", "contact", "calendar", "data_version", "freshness",
  "matches", "other_periods", "decision_blocked_by", "coverage"];

const get = (o: any, k: string) => (o != null && k in o ? o[k] : null);
const pick = (o: any, keys: string[]) => Object.fromEntries(keys.map((k) => [k, get(o, k)]));

function dayCompact(d: any): any {
  return {
    date: d.date, weekday: get(d, "weekday"), in_calendar_range: get(d, "in_calendar_range"),
    collections: get(d, "collections") === null ? null
      : d.collections.map((c: any) => Object.fromEntries(["official_label", "rule_text", "is_exception", "label_note"].filter((k) => k in c).map((k) => [k, c[k]]))),
    ...(truthy(get(d, "note")) ? { note: d.note } : {}),
  };
}

const emptyish = (v: unknown) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)
  || (typeof v === "object" && !Array.isArray(v) && v !== null && v.constructor === Object && Object.keys(v).length === 0);

export function compact(res: any): any {
  const loc = truthy(get(res, "location")) ? res.location : {};
  const st = get(res, "status");
  const out: any = { status: st, answerable: get(res, "answerable"), summary_ja: get(res, "summary_ja") };
  if (truthy(loc)) {
    out.location = Object.fromEntries(LOC_KEYS.filter((k) => get(loc, k) !== null).map((k) => [k, loc[k]]));
    const a = get(loc, "address");
    if (a !== null && typeof a === "object" && !Array.isArray(a)) out.location.address = pick(a, ["label", "municipality_stated"]);
    if (truthy(get(loc, "nearest_address"))) out.location.nearest_address = pick(loc.nearest_address, ["label", "distance_m"]);
    if (truthy(get(loc, "nearest_parcel"))) out.location.nearest_parcel = pick(loc.nearest_parcel, ["label", "distance_m"]);
  }
  if (truthy(get(res, "days"))) out.days = res.days.map(dayCompact);
  if (truthy(get(res, "next_after_date"))) {
    out.next_after_date = res.next_after_date.map((x: any) =>
      Object.fromEntries(["official_label", "date", "weekday", "null_reason", "label_note"].filter((k) => truthy(get(x, k))).map((k) => [k, x[k]])));
  }
  if (truthy(get(res, "candidates"))) {
    out.candidates = res.candidates.map((c: any) => ({
      collection_district: get(c, "collection_district"), official: get(c, "official"),
      basis: (c.basis ?? []).map((b: any) => (typeof b === "string" ? b : (truthy(get(b, "label")) ? b.label : get(b, "kind")))),
      how_to_confirm: c.how_to_confirm ?? [],
      ...(truthy(get(c, "days")) && st !== "ok" ? { days: c.days.map(dayCompact) } : {}),
    }));
  } else if (truthy(get(res, "town_rows")) && st !== "ok") {
    out.town_rows = res.town_rows;
  }
  if (truthy(get(res, "notes"))) out.notes = res.notes;
  for (const k of TOP_KEYS) if (!emptyish(get(res, k))) out[k] = res[k];
  if (truthy(get(res, "sources"))) {
    out.sources = res.sources.map((s: any) => Object.fromEntries(["title", "url", "retrieved_at"].filter((k) => truthy(get(s, k))).map((k) => [k, s[k]])));
  }
  return out;
}
