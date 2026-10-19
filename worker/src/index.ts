// gomisute-gps の Worker: MCP を Streamable HTTP で出す (状態を持たない。JSON で返す)
//   POST /mcp     JSON-RPC (1つの本文 = mcp_server.py の標準入出力の1行)。要求には 200 + application/json、
//                 通知・応答だけなら 202 (本文なし)、パースできない本文は 400
//   GET/DELETE /mcp → 405 (SSE のストリームもセッションも持たない)
//   GET /         → 短い案内
// データ: パックは R2 (PACKS)、公式ページの変更監視の結果は KV (STATUS)、緯度経度 → 市区町村は gps2city (Service Binding GPS2CITY)
import { MANIFEST, type Deps } from "./deps.ts";
import { META, handleBody } from "./mcp.ts";
import { pyStrip } from "./py.ts";

interface R2ObjectBody { arrayBuffer(): Promise<ArrayBuffer> }
interface R2Bucket { get(key: string): Promise<R2ObjectBody | null> }
interface KVNamespace { get(key: string, opts: { type: "json"; cacheTtl?: number }): Promise<any> }
interface Fetcher { fetch(input: RequestInfo, init?: RequestInit): Promise<Response> }

export interface Env {
  PACKS?: R2Bucket;
  STATUS?: KVNamespace;
  GPS2CITY?: Fetcher;
  GPS2CITY_URL?: string;
  ALLOWED_ORIGINS?: string;
}

const MAX_BODY = 1_000_000;
const UA = "gomisute-gps (+https://github.com/firemio/gomisute-gps)";

// ---------------------------------------------------------------- パック (R2) を読む
const packCache = new Map<string, Promise<any>>();

/** gzip を解いて、sha256 が manifest と同じかを確かめたバイト列 */
export async function gunzipChecked(buf: ArrayBuffer, sha256: string): Promise<Uint8Array> {
  const stream = new Response(buf).body!.pipeThrough(new DecompressionStream("gzip"));
  const raw = new Uint8Array(await new Response(stream).arrayBuffer());
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", raw))].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (digest !== sha256) throw new Error(`パックのハッシュが manifest と違う (${digest.slice(0, 12)})`);
  return raw;
}

export async function gunzipJson(buf: ArrayBuffer, sha256: string): Promise<any> {
  return JSON.parse(new TextDecoder().decode(await gunzipChecked(buf, sha256)));
}

async function r2Get(env: Env, key: string): Promise<ArrayBuffer> {
  if (!env.PACKS) throw new Error("R2 の binding PACKS が無い");
  const obj = await env.PACKS.get(key);
  if (!obj) throw new Error("R2 にパックが無い");
  return obj.arrayBuffer();
}

function packInfo(key: string): { sha256: string } {
  for (const m of Object.values<any>(MANIFEST.municipalities)) for (const p of Object.values<any>(m.packs)) if (p.key === key) return p;
  throw new Error(`manifest に無いパック: ${key}`);
}

export function makeDeps(env: Env): Deps {
  return {
    now: () => new Date(),
    pack(key: string) {
      let p = packCache.get(key);
      if (!p) {
        p = (async () => gunzipJson(await r2Get(env, key), packInfo(key).sha256))().catch((e) => { packCache.delete(key); throw e; });
        packCache.set(key, p);
      }
      return p;
    },
    // 緯度経度の判定の loc とタイル (読んだあとの索引は deps.ts が使い回す)
    async bytes(key: string, sha256: string) { return gunzipChecked(await r2Get(env, key), sha256); },
    async status(lg: string) {
      if (!env.STATUS) return null;
      const m = MANIFEST.municipalities[lg];
      try { return await env.STATUS.get(m.status_key, { type: "json", cacheTtl: 300 }); } catch { return null; }   // 読めなければ「確認していない」
    },
    async gps2city(lat: number, lng: number) {
      const init = { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": UA }, body: JSON.stringify({ latitude: lat, longitude: lng }) };
      const viaBinding = async () => {
        if (!env.GPS2CITY) return null;
        try {
          const r = await env.GPS2CITY.fetch("https://gps2city/location", { ...init, signal: AbortSignal.timeout(5000) });
          return r.status >= 500 ? null : r;                     // 手元の開発で gps2city が動いていないときは公開の URL へ
        } catch { return null; }
      };
      const r = (await viaBinding()) ?? await fetch(`${env.GPS2CITY_URL || "https://gps2city.firemio.workers.dev"}/location`, { ...init, signal: AbortSignal.timeout(5000) });
      if (r.status !== 200) return { error: `HTTP Error ${r.status}` };
      return r.json();
    },
  };
}

// ---------------------------------------------------------------- HTTP
function originAllowed(origin: string | null, url: URL, env: Env): boolean {
  if (!origin) return true;                                          // ブラウザ以外 (Claude のサーバーなど) は Origin を付けない
  let o: URL;
  try { o = new URL(origin); } catch { return false; }
  const allow = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (allow.includes("*") || allow.includes(o.origin)) return true;
  const h = o.hostname;
  return h === url.hostname || h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h.endsWith(".localhost")
    || h === "claude.ai" || h.endsWith(".claude.ai") || h === "claude.com" || h.endsWith(".claude.com");
}

function cors(origin: string | null): Record<string, string> {
  return origin ? { "Access-Control-Allow-Origin": origin, Vary: "Origin", "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id, Authorization", "Access-Control-Max-Age": "86400" } : {};
}

const json = (body: string, status: number, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { "Content-Type": "application/json; charset=utf-8", ...extra } });
const rpcError = (code: number, message: string) => JSON.stringify({ jsonrpc: "2.0", id: null, error: { code, message } });

function infoPage(url: URL): Response {
  const tools = META.TOOLS.map((t: any) => `<li><code>${t.name}</code></li>`).join("");
  const cov = META.COVERAGE.map((c: any) => `<li>${c.name} (${c.calendar})</li>`).join("");
  const html = `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>gomisute-gps MCP</title><style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.6;background:#fff;color:#222}
code{background:#f2f2f2;padding:0 .3em}@media (prefers-color-scheme: dark){body{background:#111;color:#eee}code{background:#333}}</style></head><body>
<h1>gomisute-gps</h1><p>日本の家庭ごみの収集日と分け方を、自治体の公式資料から作ったデータで答える MCP サーバーです (サインイン不要)。</p>
<p>MCP の URL: <code>${url.origin}/mcp</code> (Streamable HTTP。POST で JSON-RPC)</p>
<h2>ツール</h2><ul>${tools}</ul><h2>対応している自治体</h2><ul>${cov}</ul>
<p>サーバー ${META.SERVER.name} ${META.SERVER.version}・ソース: <a href="https://github.com/firemio/gomisute-gps">github.com/firemio/gomisute-gps</a></p></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

/** 本文を max バイトまで読む (超えたら、そこで読むのをやめて null)。Content-Length の無い (chunked の) 本文も、全部をメモリに溜めない */
export async function readBody(request: Request, max: number): Promise<Uint8Array | null> {
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    n += value.byteLength;
    if (n > max) {
      await reader.cancel().catch(() => {});
      return null;
    }
    chunks.push(value);
  }
  if (chunks.length === 1) return chunks[0];
  const out = new Uint8Array(n);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.byteLength; }
  return out;
}

export async function handleRequest(request: Request, env: Env, deps: Deps = makeDeps(env)): Promise<Response> {
  const url = new URL(request.url);
  const origin = request.headers.get("Origin");
  if (url.pathname === "/" && (request.method === "GET" || request.method === "HEAD")) return infoPage(url);
  if (url.pathname !== "/mcp") return new Response("Not Found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  if (!originAllowed(origin, url, env)) return json(rpcError(-32600, "origin not allowed"), 403);
  const h = cors(origin);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: h });
  if (request.method !== "POST") return new Response("Method Not Allowed\n", { status: 405, headers: { Allow: "POST, OPTIONS", "Content-Type": "text/plain; charset=utf-8", ...h } });
  const pv = request.headers.get("MCP-Protocol-Version");
  if (pv && !META.SUPPORTED.includes(pv)) return json(rpcError(-32600, `unsupported MCP-Protocol-Version: ${pv.slice(0, 40)}`), 400, h);
  const len = Number(request.headers.get("Content-Length") || 0);
  if (len > MAX_BODY) return json(rpcError(-32600, "request too large"), 413, h);
  const body = await readBody(request, MAX_BODY);
  if (!body) return json(rpcError(-32600, "request too large"), 413, h);
  const reply = await handleBody(deps, body);
  if (reply === null) {
    const empty = !pyStrip(new TextDecoder().decode(body));
    return empty ? json(rpcError(-32700, "parse error (empty body)"), 400, h) : new Response(null, { status: 202, headers: h });
  }
  const parseError = reply.startsWith('{"jsonrpc": "2.0", "id": null, "error": {"code": -32700');
  return json(reply, parseError ? 400 : 200, h);
}

export default {
  fetch(request: Request, env: Env): Promise<Response> { return handleRequest(request, env); },
};
