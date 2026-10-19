// Python の文字列・数の扱いに合わせる道具 (resolve.py などと同じ答えを返すため)
// - 文字の数・切り出しはコードポイントで数える (Python の len・s[:n])
// - 正規表現の \s・\d は Python (Unicode) と同じ範囲にする。必ず u フラグで使う
// - repr()・float の表し方・round() も Python と同じにする

/** Python の \s (str.isspace) と同じ文字 */
export const WS = "[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
/** Python の \S */
export const NWS = "[^\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
/** Python の \d (Unicode の 10進数字) */
export const D = "\\p{Nd}";
/** Python の $ (文字列の終わり、または最後の改行の直前) */
export const END = "(?=\\n?$)";

const WS_RE = new RegExp(WS, "u");

/** Python の例外を真似る (mcp の層で「この入力を処理できませんでした (KeyError)」のように名前を出す) */
export class PyError extends Error {
  pyName: string;
  constructor(pyName: string, message = "") {
    super(message || pyName);
    this.pyName = pyName;
  }
}

/** mcp_server.py の RuntimeError (「エラー: …」の文をそのまま返す) */
export class PyRuntimeError extends Error {}

/** Python の float (整数の値でも 2.0 と書く) */
export class PyFloat {
  v: number;
  constructor(v: number) { this.v = v; }
}
export const pf = (v: number): PyFloat => new PyFloat(v);

export function isPyFloat(v: unknown): v is PyFloat { return v instanceof PyFloat; }
/** Python の int か (パースした JSON の整数・bigint) */
export function isPyInt(v: unknown): boolean {
  return (typeof v === "number" && Number.isInteger(v)) || typeof v === "bigint";
}
/** float() と同じ値 */
export function toFloat(v: unknown): number {
  if (v instanceof PyFloat) return v.v;
  if (typeof v === "bigint") return Number(v);
  return v as number;
}

// ---------------------------------------------------------------- 文字列
export const cps = (s: string): string[] => Array.from(s);
export const pyLen = (s: string): number => { let n = 0; for (const _ of s) n++; return n; };
/** s[a:b] (コードポイント) */
export function pySlice(s: string, a: number, b?: number): string {
  const c = cps(s);
  return c.slice(a < 0 ? Math.max(0, c.length + a) : a, b === undefined ? undefined : b < 0 ? c.length + b : b).join("");
}
export function isSpace(ch: string): boolean { return WS_RE.test(ch); }
/** str.strip() */
export function pyStrip(s: string): string {
  const c = cps(s);
  let a = 0, b = c.length;
  while (a < b && isSpace(c[a])) a++;
  while (b > a && isSpace(c[b - 1])) b--;
  return c.slice(a, b).join("");
}
/** str.rstrip(chars) */
export function pyRstrip(s: string, chars: string): string {
  const set = new Set(cps(chars));
  const c = cps(s);
  let b = c.length;
  while (b > 0 && set.has(c[b - 1])) b--;
  return c.slice(0, b).join("");
}
/** 孤立サロゲートを含むか */
export function hasLoneSurrogate(s: string): boolean {
  return /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(s);
}
/** 文字列をコードポイントで比べる (Python の sorted と同じ順) */
export function cmpPy(a: string, b: string): number {
  if (a === b) return 0;
  const x = cps(a), y = cps(b);
  const n = Math.min(x.length, y.length);
  for (let i = 0; i < n; i++) {
    const p = x[i].codePointAt(0)!, q = y[i].codePointAt(0)!;
    if (p !== q) return p < q ? -1 : 1;
  }
  return x.length < y.length ? -1 : x.length > y.length ? 1 : 0;
}
export const sortedPy = (xs: Iterable<string>): string[] => [...xs].sort(cmpPy);

const ND_RE = /^\p{Nd}$/u;
// Python の str.isdigit() は、10進数字 (\p{Nd}) のほかに次の文字も数字とみなす (Unicode 15.1。上付き・下付き・丸数字・❶・➀・⓵・
// エチオピアの数字など。❶・➀・⓵・፩ などは NFKC でもそのまま残る)。Python で c.isdigit() and not c.isdecimal() の文字を全部数えたもの
const DIGIT_RE = /^[\p{Nd}\u00b2-\u00b3\u00b9\u1369-\u1371\u19da\u2070\u2074-\u2079\u2080-\u2089\u2460-\u2468\u2474-\u247c\u2488-\u2490\u24ea\u24f5-\u24fd\u24ff\u2776-\u277e\u2780-\u2788\u278a-\u2792\u{10a40}-\u{10a43}\u{10e60}-\u{10e68}\u{11052}-\u{1105a}\u{1f100}-\u{1f10a}]$/u;
/** Python の str.isdigit() (1文字) */
export const isDigit = (ch: string): boolean => DIGIT_RE.test(ch);

/** 10進数字 (Unicode のどの数字でも) の値 */
function digitValue(ch: string): number {
  const cp = ch.codePointAt(0)!;
  if (cp >= 48 && cp <= 57) return cp - 48;
  let start = cp;                                    // 数字は 10個ずつ並んでいる: 続いている数字の先頭から数える
  while (start > 0 && ND_RE.test(String.fromCodePoint(start - 1))) start--;
  return (cp - start) % 10;
}
/** int(s) (Unicode の数字も読む)。2^53 を超えたら bigint */
export function pyInt(s: string): number | bigint {
  let ascii = "";
  for (const ch of s) ascii += String(digitValue(ch));
  const n = Number(ascii);
  if (Number.isSafeInteger(n)) return n;
  return BigInt(ascii);
}

// ---------------------------------------------------------------- 数
/** repr(float) */
export function pyFloatRepr(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const [mant, expS] = x.toExponential().split("e");     // 往復できる一番短い桁 (Python の repr と同じ桁)
  const exp = parseInt(expS, 10);
  const sign = x < 0 ? "-" : "";
  const digits = mant.replace("-", "").replace(".", "");
  if (exp < -4 || exp >= 16) {
    const m = digits[0] + (digits.length > 1 ? "." + digits.slice(1) : "");
    return `${sign}${m}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  }
  if (exp >= 0) {
    if (digits.length <= exp + 1) return sign + digits + "0".repeat(exp + 1 - digits.length) + ".0";
    return sign + digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
  }
  return sign + "0." + "0".repeat(-exp - 1) + digits;
}

/** round(x) (偶数への丸め) → int */
export function pyRoundInt(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** round(x, nd) (double の正確な値を 10進で偶数への丸め) */
export function pyRound(x: number, nd: number): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const buf = new DataView(new ArrayBuffer(8));
  buf.setFloat64(0, x);
  const hi = buf.getUint32(0), lo = buf.getUint32(4);
  const neg = hi >>> 31 === 1;
  const e = (hi >>> 20) & 0x7ff;
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let exp: number;
  if (e === 0) exp = -1074; else { mant |= 1n << 52n; exp = e - 1075; }
  // x = mant * 2^exp → 10^nd 倍した正確な値を分数 num/den で
  let num = mant, den = 1n;
  if (exp >= 0) num <<= BigInt(exp); else den <<= BigInt(-exp);
  if (nd >= 0) num *= 10n ** BigInt(nd); else den *= 10n ** BigInt(-nd);
  let q = num / den;
  const r2 = (num % den) * 2n;
  if (r2 > den || (r2 === den && q % 2n === 1n)) q += 1n;
  const s = (neg ? "-" : "") + q.toString();
  const v = nd >= 0 ? Number(`${s}e-${nd}`) : Number(`${s}e${-nd}`);
  return v;
}

// ---------------------------------------------------------------- repr / str
function reprStr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (ch === quote || ch === "\\") out += "\\" + ch;
    else if (ch === "\t") out += "\\t";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (cp < 0x20 || cp === 0x7f) out += "\\x" + cp.toString(16).padStart(2, "0");
    else if (cp < 0x7f) out += ch;
    else if (!isPrintable(ch)) {
      if (cp <= 0xff) out += "\\x" + cp.toString(16).padStart(2, "0");
      else if (cp <= 0xffff) out += "\\u" + cp.toString(16).padStart(4, "0");
      else out += "\\U" + cp.toString(16).padStart(8, "0");
    } else out += ch;
  }
  return out + quote;
}
const NONPRINT = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]$/u;
function isPrintable(ch: string): boolean { return ch === " " || !NONPRINT.test(ch); }

/** pyRepr の中で、値ではなくそのまま書く文字 */
class Tok {
  s: string;
  constructor(s: string) { this.s = s; }
}
const COMMA = new Tok(", "), COLON = new Tok(": "), LIST_END = new Tok("]"), DICT_END = new Tok("}");

/** repr(v) (JSON から読んだ値: Map は dict、配列は list)。入れ子が深くても (JSON の上限 2998 段まで) スタックを使い切らないよう、再帰せずに書く */
export function pyRepr(v: unknown): string {
  const out: string[] = [];
  const todo: unknown[] = [v];                       // 後ろから取り出す (書く順の逆に積む)
  while (todo.length) {
    const x = todo.pop();
    if (x instanceof Tok) { out.push(x.s); continue; }
    if (x === null || x === undefined) out.push("None");
    else if (x === true) out.push("True");
    else if (x === false) out.push("False");
    else if (typeof x === "string") out.push(reprStr(x));
    else if (typeof x === "bigint") out.push(x.toString());
    else if (typeof x === "number") out.push(Number.isInteger(x) ? String(x) : pyFloatRepr(x));
    else if (x instanceof PyFloat) out.push(pyFloatRepr(x.v));
    else if (Array.isArray(x)) {
      out.push("[");
      todo.push(LIST_END);
      for (let i = x.length - 1; i >= 0; i--) { todo.push(x[i]); if (i) todo.push(COMMA); }
    } else {
      const entries = x instanceof Map ? [...x.entries()] : Object.entries(x as object);
      out.push("{");
      todo.push(DICT_END);
      for (let i = entries.length - 1; i >= 0; i--) { todo.push(entries[i][1], COLON, entries[i][0]); if (i) todo.push(COMMA); }
    }
  }
  return out.join("");
}
/** str(v) */
export function pyStr(v: unknown): string { return typeof v === "string" ? v : pyRepr(v); }

/** Python の真偽 (空の文字列・リスト・dict・0・None は偽) */
export function truthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === "" || v === 0 || v === 0n) return false;
  if (v instanceof PyFloat) return v.v !== 0;
  if (Array.isArray(v)) return v.length > 0;
  if (v instanceof Map || v instanceof Set) return v.size > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return true;
}

/** 正規表現の文字をエスケープ (re.escape) */
export const reEscape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\\-]/g, "\\$&");

/** copy.deepcopy (PyFloat を保つ) */
export function deepClone<T>(v: T): T {
  if (v === null || typeof v !== "object") return v;
  if (v instanceof PyFloat) return new PyFloat(v.v) as unknown as T;
  if (Array.isArray(v)) return v.map(deepClone) as unknown as T;
  if (v instanceof Map) return new Map([...v.entries()].map(([k, x]) => [k, deepClone(x)])) as unknown as T;
  const o: Record<string, unknown> = {};
  for (const [k, x] of Object.entries(v as object)) o[k] = deepClone(x);
  return o as T;
}
