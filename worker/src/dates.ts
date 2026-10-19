// 日付: 1970-01-01 からの日数 (日本時間の暦の日) で扱う。resolve.py の fmt・_date と同じ書き方
import { D, PyError, WS } from "./py.ts";

export const WDJ = "月火水木金土日";
const DAY_MS = 86400000;
const JST_MS = 9 * 3600000;

export const isoToDay = (s: string): number => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / DAY_MS;
export function dayToIso(n: number): string {
  const d = new Date(n * DAY_MS);
  return `${String(d.getUTCFullYear()).padStart(4, "0")}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
/** 曜日 (月曜 0。Python の weekday) */
export const weekday = (n: number): number => (((n + 3) % 7) + 7) % 7;
export const yearOf = (n: number): number => new Date(n * DAY_MS).getUTCFullYear();
export const monthOf = (n: number): number => new Date(n * DAY_MS).getUTCMonth() + 1;
export const dateOf = (n: number): number => new Date(n * DAY_MS).getUTCDate();

/** resolve.fmt: 「2026年10月20日(火)」(year=false なら年なし) */
export function fmt(n: number, year = false): string {
  return (year ? `${yearOf(n)}年` : "") + `${monthOf(n)}月${dateOf(n)}日(${WDJ[weekday(n)]})`;
}

/** 日本時間の今日 (日数) */
export const jstToday = (now: Date): number => Math.floor((now.getTime() + JST_MS) / DAY_MS);
/** now.isoformat(timespec="seconds") (日本時間) */
export function jstIso(now: Date): string {
  const t = new Date(Math.floor(now.getTime() / 1000) * 1000 + JST_MS);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${String(t.getUTCFullYear()).padStart(4, "0")}-${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())}T${p(t.getUTCHours())}:${p(t.getUTCMinutes())}:${p(t.getUTCSeconds())}+09:00`;
}

const DATE_RE = new RegExp(`^${WS}*${D}{4}-${D}{2}-${D}{2}${WS}*$`, "u");
const ASCII_DATE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;

/** resolve._date: 日付の文字列 → 日数。形が違えば TypeError、暦にない日・2000〜2099年の外は ValueError */
export function pyDate(v: string, strip: (s: string) => string): number {
  if (!DATE_RE.test(v)) throw new PyError("TypeError");
  const m = ASCII_DATE.exec(strip(v));
  if (!m) throw new PyError("ValueError");                // date.fromisoformat は ASCII の数字だけ読む
  const y = +m[1], mo = +m[2], d = +m[3];
  if (y < 1 || mo < 1 || mo > 12) throw new PyError("ValueError");
  const dim = [31, (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1];
  if (d < 1 || d > dim) throw new PyError("ValueError");
  if (y < 2000 || y > 2099) throw new PyError("ValueError");
  return isoToDay(m[0]);
}

/** datetime.fromisoformat(checked_at) → エポックからのミリ秒 (時差のない形は Python と同じく引き算できない) */
export function parseIsoDatetime(s: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if (!m) throw new PyError("ValueError");
  if (!m[8]) throw new PyError("TypeError");
  const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) + (m[7] ? +(m[7].padEnd(6, "0")) / 1000 : 0);
  if (m[8] === "Z") return ms;
  const sign = m[8][0] === "-" ? -1 : 1;
  const hh = +m[8].slice(1, 3), mm = +m[8].slice(-2);
  return ms - sign * (hh * 60 + mm) * 60000;
}
