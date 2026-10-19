// MCP (JSON-RPC 2.0) の層: mcp_server.py の check_args・call・result_for・handle_line の移植。
// 1つの POST の本文 = 標準入出力の1行。返す文字列も mcp_server.py と同じ (キーの順・区切り・float の書き方まで)
import { compact } from "./compact.ts";
import { KANAZAWA, ONAGAWA, kanazawa, onagawa, type Deps } from "./deps.ts";
import meta from "./generated/mcp_meta.ts";
import { resolveLocation } from "./location.ts";
import { n_, isOnagawa } from "./onagawa.ts";
import { PyError, PyFloat, PyRuntimeError, D, cps, deepClone, hasLoneSurrogate, isPyInt, pyStr, pySlice, pyStrip } from "./py.ts";
import { NanError, dumps, loads, scrub, type JV } from "./pyjson.ts";

export const META: any = meta;
const TOOL_NAMES = new Set<string>(META.TOOLS.map((t: any) => t.name));
const OTHER_TOWN_KEYS: string[] = META.OTHER_TOWN_KEYS;
const ONA_SHORT = new RegExp(`^(日本国?)?(〒?${D}{3}-?${D}{4})?(宮城県)?(牡鹿郡)?(女川|おながわ)(?!町|市(?!場))([^\\n]*)$`, "u");
const ONA_NAMES = new Set(["女川", "女川町", "おながわ", "おながわ町", "宮城県女川町", "宮城県牡鹿郡女川町"]);

type Args = Map<string, JV>;

/** mcp_server._num */
function isNum(v: unknown): boolean {
  if (v instanceof PyFloat) return Number.isFinite(v.v);
  if (typeof v === "bigint") return Number.isFinite(Number(v));
  return typeof v === "number" && Number.isFinite(v);
}

/** mcp_server._text */
function text(v: unknown, name: string, limit = 200): [string | null, string | null] {
  if (typeof v !== "string" || !pyStrip(v)) return [null, `${name} は文字列で指定してください。`];
  if (cps(v).length > limit) return [null, `${name} は ${limit} 文字までにしてください。`];
  if (hasLoneSurrogate(v)) return [null, `${name} に読めない文字が入っています。`];
  return [v, null];
}

/** mcp_server.check_args: 引数の型と範囲を先に確かめる → [そろえた引数, 誤りの文 or null] */
export function checkArgs(name: string, a0: Args): [Args, string | null] {
  const a = new Map(a0);
  if ((a.get("days") ?? null) === null) a.set("days", 1);
  let d = a.get("days")!;
  if (d instanceof PyFloat && Number.isFinite(d.v) && Number.isInteger(d.v)) { d = d.v === 0 ? 0 : d.v; a.set("days", d); }
  if (typeof d === "boolean" || !isPyInt(d) || !((d as number | bigint) >= 1 && (d as number | bigint) <= 14)) return [a, "days は 1〜14 の整数で指定してください。"];
  if ((a.get("date") ?? null) !== null && typeof a.get("date") !== "string") return [a, "date は日本時間の日付 (YYYY-MM-DD) の文字列で指定してください。"];
  if (name === "garbage_collection_by_address") {
    const [v, e] = text(a.get("address") ?? null, "address");
    a.set("address", v);
    return [a, e];
  }
  if (name === "garbage_collection_by_location") {
    for (const k of ["latitude", "longitude"]) if (!isNum(a.get(k))) return [a, `${k} は数 (度) で指定してください。`];
    for (const k of ["accuracy_m", "accuracy_confidence"]) {
      const v = a.get(k) ?? null;
      if (v !== null && !isNum(v)) return [a, `${k} は数で指定してください (分からなければ省略)。`];
    }
    return [a, null];
  }
  if (name === "garbage_sorting") {
    let [v, e] = text(a.get("municipality") ?? null, "municipality", 100);
    a.set("municipality", v);
    if (e) return [a, e + " (例: 女川町)"];
    [v, e] = text(a.get("item") ?? null, "item");
    a.set("item", v);
    return [a, e];
  }
  return [a, null];
}

/** mcp_server.call */
export async function call(deps: Deps, name: string, a0: Args): Promise<any> {
  const [a, bad] = checkArgs(name, a0);
  if (bad) return { status: "invalid_input", answerable: false, summary_ja: bad };
  const days = Number(a.get("days"));
  const date = (a.get("date") ?? null) as string | null;
  const now = deps.now();
  let res: any;
  if (name === "garbage_collection_by_address") {
    let addr = a.get("address") as string;
    if (!isOnagawa(addr)) {                                   // 「女川針浜」「女川市場通り」「女川」も女川町として引く
      const m = ONA_SHORT.exec(n_(addr));
      if (m) addr = "女川町" + m[6];
    }
    if (isOnagawa(addr)) {
      const ona = await onagawa(deps);
      res = ona.resolveAddress(addr, date, days, now, await deps.status(ONAGAWA));
    } else {
      const k = await kanazawa(deps);
      res = k.resolveAddress(addr, date, days, now, await deps.status(KANAZAWA));
      const b = (res.location || {}).decision_blocked_by || {};
      if (b.reason === "address_not_found") {
        const ona = await onagawa(deps);
        if (ona.matchPlace("女川町" + n_(addr))[1]) {
          const hint = `宮城県女川町の「${addr}」のことなら、garbage_collection_by_address に「女川町${addr}」で渡してください。`;
          res.summary_ja = (res.summary_ja || "") + hint;
          if (!("notes" in res)) res.notes = [];
          res.notes.push(hint);
        }
      }
    }
  } else if (name === "garbage_collection_by_location") {
    res = await resolveLocation(deps, a.get("latitude"), a.get("longitude"), date, days, a.get("accuracy_m") ?? null, a.get("accuracy_confidence") ?? null);
  } else if (name === "garbage_sorting") {
    const m = a.get("municipality") as string;
    if (isOnagawa(m) || ONA_NAMES.has(n_(m))) {
      const ona = await onagawa(deps);
      return ona.sorting(a.get("item") as string, date, now, await deps.status(ONAGAWA));
    }
    return { status: "out_of_coverage", answerable: false, coverage: deepClone(META.COVERAGE),
      summary_ja: `「${pySlice(m, 0, 40)}」の分別表はまだデータにありません (対応: 宮城県女川町)。その自治体の公式サイトの分別表で確認してください。` };
  } else {
    throw new PyError("KeyError");
  }
  if (res.status === "out_of_coverage") {
    for (const x of OTHER_TOWN_KEYS) delete res[x];
    res.coverage = deepClone(META.COVERAGE);
    res.summary_ja = (res.summary_ja || "").replaceAll("(対応: 石川県金沢市)", "(対応: 石川県金沢市、宮城県女川町)");
    const mun = (res.location || {}).municipality || {};
    if (name === "garbage_collection_by_location" && mun && typeof mun === "object" && mun.lg_code === ONAGAWA) {
      const ona = await onagawa(deps);
      res.summary_ja = "この場所は宮城県女川町です。女川町は緯度経度では引けません。お住まいの行政区 (町民カレンダーの凡例の名前) か住所を利用者に聞いて、"
        + "garbage_collection_by_address に「女川町<行政区>」で渡してください。";
      res.contact = deepClone(ona.d.contact);
    }
  }
  return a.get("detail") === "full" ? res : compact(res);
}

// ---------------------------------------------------------------- JSON-RPC
const err = (mid: unknown, code: number, message: string) => ({ jsonrpc: "2.0", id: mid ?? null, error: { code, message } });
const pyName = (e: unknown) => (e instanceof PyError ? e.pyName : e instanceof Error ? e.name || "Exception" : "Exception");

/** mcp_server.result_for: 1つのメッセージ → 返す値 (通知・応答なら null) */
export async function resultFor(deps: Deps, msg: JV): Promise<any> {
  if (!(msg instanceof Map)) return err(null, -32600, "invalid request");
  const isNote = !msg.has("id");
  const mid = msg.get("id") ?? null;
  if (!isNote && (typeof mid === "boolean" || !(typeof mid === "string" || isPyInt(mid)))) return err(null, -32600, "invalid id");
  const method = msg.get("method");
  if (msg.get("jsonrpc") !== "2.0" || typeof method !== "string") {
    if (!msg.has("method") && (msg.has("result") || msg.has("error"))) return null;
    return isNote ? null : err(mid, -32600, "invalid request");
  }
  let params = msg.has("params") ? msg.get("params")! : new Map();
  if (params === null) params = new Map();
  if (isNote) return null;
  if (!(params instanceof Map)) return err(mid, -32602, "params must be an object");
  const ok = (result: unknown) => ({ jsonrpc: "2.0", id: mid, result });
  if (method === "initialize") {
    const v = params.get("protocolVersion");
    return ok({ protocolVersion: typeof v === "string" && META.SUPPORTED.includes(v) ? v : META.PROTOCOL, capabilities: { tools: { listChanged: false } },
      serverInfo: META.SERVER, instructions: META.INSTRUCTIONS });
  }
  if (method === "ping") return ok({});
  if (method === "tools/list") return ok({ tools: META.TOOLS });
  if (method === "tools/call") {
    const name = params.has("name") ? params.get("name")! : null;
    let args = params.has("arguments") ? params.get("arguments")! : new Map();
    if (typeof name !== "string" || !TOOL_NAMES.has(name)) return err(mid, -32602, `unknown tool: ${pySlice(pyStr(name), 0, 60)}`);
    if (args === null) args = new Map();
    if (!(args instanceof Map)) return err(mid, -32602, "arguments must be an object");
    try {
      const res = await call(deps, name, args);
      return ok({ content: [{ type: "text", text: dump(res) }], isError: res.status === "invalid_input" });
    } catch (e) {
      if (e instanceof PyRuntimeError) return ok({ content: [{ type: "text", text: `エラー: ${e.message}` }], isError: true });
      return ok({ content: [{ type: "text", text: `エラー: この入力を処理できませんでした (${pyName(e)})` }], isError: true });
    }
  }
  return err(mid, -32601, `method not found: ${pySlice(method, 0, 60)}`);
}

/** mcp_server._dump: JSON の文字列。NaN は null に、孤立サロゲートがあれば全部 \u でエスケープする */
export function dump(o: unknown): string {
  let s: string;
  try { s = dumps(o, false, false); } catch (e) {
    if (!(e instanceof NanError)) throw e;
    s = dumps(scrub(o), false, true);
  }
  if (hasLoneSurrogate(s)) s = dumps(loads(s), true, true);
  return s;
}

async function one(deps: Deps, m: JV): Promise<any> {
  try { return await resultFor(deps, m); } catch (e) {
    const id = m instanceof Map ? m.get("id") : undefined;
    const mid = (typeof id === "string" || isPyInt(id)) && typeof id !== "boolean" ? id : null;
    return err(mid, -32603, `internal error (${pyName(e)})`);
  }
}

/** mcp_server.handle_line: 本文 (bytes) → 返す JSON の文字列 (返さないなら null)。どんな入力でも落ちない */
export async function handleBody(deps: Deps, raw: Uint8Array): Promise<string | null> {
  let line: string;
  try { line = pyStrip(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw)); } catch {
    return dump(err(null, -32700, "parse error (not UTF-8)"));
  }
  if (!line) return null;
  let msg: JV;
  try { msg = loads(line); } catch {                        // 壊れた JSON・4300桁を超える整数・深すぎる入れ子
    return dump(err(null, -32700, "parse error"));
  }
  try {
    if (Array.isArray(msg)) {
      if (!msg.length) return dump(err(null, -32600, "empty batch"));
      const outs: any[] = [];
      for (const m of msg) { const o = await one(deps, m); if (o !== null) outs.push(o); }
      return outs.length ? dump(outs) : null;
    }
    const out = await one(deps, msg);
    return out !== null ? dump(out) : null;
  } catch (e) {
    return dump(err(null, -32603, `internal error (${pyName(e)})`));
  }
}

