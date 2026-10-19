// Python の json.loads / json.dumps と同じ読み書き (mcp_server.py の handle_line・_dump と同じ返事の文字列にするため)
// 読み: NaN・Infinity・-Infinity も読む。整数と小数を分ける (1.0 の id は誤り、2.0 の days は 2)。大きい整数は bigint。
//       4300桁を超える整数と、深すぎる入れ子 (JSON_MAX_DEPTH) は読めない (Python と同じくパースの誤り)。
//       オブジェクトは Map (キーの順を保つ。__proto__ などのキーも普通に扱う)
// 書き: 区切りは ", " と ": "。ensure_ascii=False では " \ と制御文字だけをエスケープする。float は repr と同じ書き方
import { PyFloat, pyFloatRepr } from "./py.ts";

export type JV = null | boolean | number | bigint | string | PyFloat | JV[] | Map<string, JV>;

/** Python 3.13 (Windows) の json.loads が読める入れ子の深さ (golden.py で測った値) */
export const JSON_MAX_DEPTH = 2998;
const INT_MAX_DIGITS = 4300;

export class JsonParseError extends Error {}

export function loads(text: string): JV {
  let i = 0;
  const n = text.length;
  const ws = () => { while (i < n) { const c = text.charCodeAt(i); if (c === 32 || c === 9 || c === 10 || c === 13) i++; else break; } };
  const fail = (): never => { throw new JsonParseError(`parse error at ${i}`); };
  const value = (depth: number): JV => {
    if (i >= n) fail();
    const c = text[i];
    if (c === "{") {
      if (depth + 1 > JSON_MAX_DEPTH) fail();
      i++;
      const m = new Map<string, JV>();
      ws();
      if (text[i] === "}") { i++; return m; }
      for (;;) {
        ws();
        if (text[i] !== '"') fail();
        const k = str();
        ws();
        if (text[i] !== ":") fail();
        i++;
        ws();
        const v = value(depth + 1);
        m.set(k, v);
        ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "}") { i++; return m; }
        fail();
      }
    }
    if (c === "[") {
      if (depth + 1 > JSON_MAX_DEPTH) fail();
      i++;
      const a: JV[] = [];
      ws();
      if (text[i] === "]") { i++; return a; }
      for (;;) {
        ws();
        a.push(value(depth + 1));
        ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "]") { i++; return a; }
        fail();
      }
    }
    if (c === '"') return str();
    if (text.startsWith("null", i)) { i += 4; return null; }
    if (text.startsWith("true", i)) { i += 4; return true; }
    if (text.startsWith("false", i)) { i += 5; return false; }
    if (text.startsWith("NaN", i)) { i += 3; return new PyFloat(NaN); }
    if (text.startsWith("Infinity", i)) { i += 8; return new PyFloat(Infinity); }
    if (text.startsWith("-Infinity", i)) { i += 9; return new PyFloat(-Infinity); }
    return num();
  };
  const NUM = /(-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
  const num = (): JV => {
    NUM.lastIndex = i;
    const m = NUM.exec(text);
    if (!m) fail();
    const s = m![0];
    i += s.length;
    if (m![2] || m![3]) return new PyFloat(Number(s));
    const digits = m![1].replace("-", "");
    if (digits.length > INT_MAX_DIGITS) fail();            // Python: int の桁数の上限 (ValueError → パースの誤り)
    const v = Number(s);
    return Number.isSafeInteger(v) ? v : BigInt(s);
  };
  const str = (): string => {
    i++;                                                     // "
    let out = "";
    let start = i;
    for (;;) {
      if (i >= n) fail();
      const c = text.charCodeAt(i);
      if (c === 34) { out += text.slice(start, i); i++; return out; }
      if (c < 0x20) fail();                                  // 制御文字 (strict)
      if (c === 92) {
        out += text.slice(start, i);
        const e = text[i + 1];
        if (e === undefined) fail();
        const simple: Record<string, string> = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
        if (e in simple) { out += simple[e]; i += 2; }
        else if (e === "u") {
          const h = text.slice(i + 2, i + 6);
          if (!/^[0-9a-fA-F]{4}$/.test(h)) fail();
          out += String.fromCharCode(parseInt(h, 16));       // サロゲートの組は JavaScript の文字列でもそのまま1文字になる
          i += 6;
        } else fail();
        start = i;
        continue;
      }
      i++;
    }
  };
  ws();
  const v = value(0);
  ws();
  if (i !== n) fail();
  return v;
}

export class NanError extends Error {}

function escStr(s: string, ascii: boolean): string {
  let out = '"';
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\f") out += "\\f";
    else if (cp < 0x20) out += "\\u" + cp.toString(16).padStart(4, "0");
    else if (ascii && cp > 0x7e) {
      if (cp > 0xffff) {
        const v = cp - 0x10000;
        out += "\\u" + (0xd800 + (v >> 10)).toString(16) + "\\u" + (0xdc00 + (v & 0x3ff)).toString(16);
      } else out += "\\u" + cp.toString(16).padStart(4, "0");
    } else out += ch;
  }
  return out + '"';
}

/** json.dumps(v, ensure_ascii=ascii, allow_nan=allowNan) */
export function dumps(v: unknown, ascii = false, allowNan = false): string {
  const f = (x: unknown): string => {
    if (x === null) return "null";
    if (x === true) return "true";
    if (x === false) return "false";
    if (typeof x === "string") return escStr(x, ascii);
    if (typeof x === "bigint") return x.toString();
    if (typeof x === "number") {
      if (Number.isInteger(x)) return Object.is(x, -0) ? "0" : String(x);
      return float(x);
    }
    if (x instanceof PyFloat) return float(x.v);
    if (Array.isArray(x)) return x.length ? "[" + x.map(f).join(", ") + "]" : "[]";
    if (x === undefined) throw new Error("undefined の値は JSON にできない");
    const entries = x instanceof Map ? [...x.entries()] : Object.entries(x as object);
    return entries.length ? "{" + entries.map(([k, y]) => escStr(k, ascii) + ": " + f(y)).join(", ") + "}" : "{}";
  };
  const float = (x: number): string => {
    if (Number.isNaN(x)) { if (!allowNan) throw new NanError(); return "NaN"; }
    if (!Number.isFinite(x)) { if (!allowNan) throw new NanError(); return x > 0 ? "Infinity" : "-Infinity"; }
    return pyFloatRepr(x);
  };
  return f(v);
}

/** mcp_server._scrub: NaN・無限大の float を null に */
export function scrub(o: unknown): unknown {
  if (o instanceof PyFloat) return Number.isFinite(o.v) ? o : null;
  if (typeof o === "number" && !Number.isFinite(o)) return null;
  if (Array.isArray(o)) return o.map(scrub);
  if (o instanceof Map) return new Map([...o.entries()].map(([k, v]) => [k, scrub(v)]));
  if (o && typeof o === "object") { const r: Record<string, unknown> = {}; for (const [k, v] of Object.entries(o)) r[k] = scrub(v); return r; }
  return o;
}
