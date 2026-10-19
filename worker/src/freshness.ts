// 公式ページの変更監視の結果 → 答えに付ける freshness (resolve.freshness・apply_freshness と同じ)
// 監視の結果は KV (binding STATUS) の status:<lg_code> に、monitor.py の out/source_status.json と同じ形で置く
import { parseIsoDatetime } from "./dates.ts";
import { pf } from "./py.ts";

export const STATUS_MAX_AGE_DAYS = 7;

type Src = { url: string; title: string; status: string; recorded_at: string };
type Status = { checked_at?: string; simulated?: boolean; sources?: Src[] } | null;

export function freshness(status: Status, urls: string[], now: Date): Record<string, unknown> {
  if (!status || typeof status !== "object" || !Array.isArray(status.sources) || !status.checked_at) {
    return { status: "unchecked", note: "公式ページの変更監視 (monitor.py) の結果がありません" };
  }
  const by = new Map<string, Src>();
  for (const x of status.sources) by.set(x.url, x);
  const want = [...new Set(urls.map((u) => u.split("#")[0]))];
  const used = want.filter((u) => by.has(u)).map((u) => by.get(u)!);
  const untracked = want.filter((u) => !by.has(u));
  const gone = (x: Src) => x.status.startsWith("error") && ["404", "410"].some((c) => x.status.includes(c));
  const changed = used.filter((x) => x.status === "changed" || gone(x))
    .map((x) => ({ url: x.url, title: x.title, recorded_at: x.recorded_at, ...(gone(x) ? { gone: true } : {}) }));
  const errors = used.filter((x) => x.status.startsWith("error") && !gone(x)).map((x) => x.url);
  if (changed.length) return { status: "source_changed", checked_at: status.checked_at, changed, simulated: status.simulated ?? false };
  const age = (now.getTime() - parseIsoDatetime(status.checked_at)) / 1000 / 86400;
  if (age > STATUS_MAX_AGE_DAYS) {
    return { status: "stale", checked_at: status.checked_at, note: `公式ページの確認が ${Math.trunc(age)} 日前で古い (monitor.py を毎日動かす前提)` };
  }
  if (!used.length) {
    return { status: "unchecked", checked_at: status.checked_at, note: "この答えの元にした公式ページは、変更監視の対象に入っていません", untracked };
  }
  return { status: errors.length || untracked.length ? "partly_unchecked" : "unchanged", checked_at: status.checked_at, sources_checked: used.length,
           ...(errors.length ? { errors } : {}), ...(untracked.length ? { untracked } : {}) };
}

/** 元のページが変わっていたら確かな答えとして返さない。確認が古い・取れなかった・結果が無いときは answerable=false */
export function applyFreshness(res: any, fr: any): any {
  res.freshness = fr;
  if (fr.status === "source_changed" && ["ok", "ambiguous", "unresolved"].includes(res.status)) {
    if (!("location" in res)) res.location = {};
    const loc = res.location;
    if (res.status === "ok") {
      loc.collection_district_before_change = loc.collection_district ?? null;
      loc.collection_district = null;
    }
    const before = loc.decision_blocked_by;
    res.status = "unresolved";
    res.answerable = false;
    loc.decision_blocked_by = { reason: "source_changed", town: null, changed: fr.changed, checked_at: fr.checked_at, ...(before && Object.keys(before).length ? { also: before } : {}) };
    loc.district_confidence = pf(0);
    for (const k of ["days", "next_after_date", "consensus", "groups"]) delete res[k];
    for (const c of res.candidates ?? []) delete c.days;
    if ("holiday_policy" in res) res.holiday_policy = "元の公式資料が変わったため、日付は返しません";
  } else if (["stale", "partly_unchecked", "unchecked"].includes(fr.status)) {
    res.answerable = false;
  }
  return res;
}
