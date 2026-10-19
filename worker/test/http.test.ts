// HTTP の層 (Streamable HTTP の決まり) と、Worker の env から作る Deps (R2・KV・Service Binding) を確かめる
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gunzipJson, handleRequest, makeDeps, type Env } from "../src/index.ts";
import { MANIFEST } from "../src/deps.ts";
import { WORKER, goldenDeps, readBytes, readPack, META as GMETA } from "./helpers.ts";

const deps = goldenDeps("fresh");
const env: Env = {};
const post = (body: string | Uint8Array, headers: Record<string, string> = {}) =>
  handleRequest(new Request("https://gomisute.example/mcp", { method: "POST", body: body as BodyInit, headers: { "Content-Type": "application/json", ...headers } }), env, deps);

test("GET / は短い案内", async () => {
  const r = await handleRequest(new Request("https://gomisute.example/"), env, deps);
  assert.equal(r.status, 200);
  assert.match(r.headers.get("Content-Type")!, /text\/html/);
  const t = await r.text();
  assert.match(t, /gomisute-gps/);
  assert.match(t, /garbage_collection_by_address/);
});

test("GET・DELETE /mcp は 405、ほかのパスは 404", async () => {
  for (const method of ["GET", "DELETE", "PUT"]) {
    const r = await handleRequest(new Request("https://gomisute.example/mcp", { method }), env, deps);
    assert.equal(r.status, 405, method);
    assert.match(r.headers.get("Allow")!, /POST/);
  }
  assert.equal((await handleRequest(new Request("https://gomisute.example/sse"), env, deps)).status, 404);
});

test("initialize・tools/list は 200 の JSON", async () => {
  const r = await post('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}');
  assert.equal(r.status, 200);
  assert.match(r.headers.get("Content-Type")!, /application\/json/);
  const j = await r.json() as any;
  assert.equal(j.result.protocolVersion, "2025-03-26");
  assert.equal(j.result.serverInfo.name, "gomisute-gps");
  const t = await (await post('{"jsonrpc":"2.0","id":2,"method":"tools/list"}')).json() as any;
  assert.deepEqual(t.result.tools.map((x: any) => x.name), ["garbage_collection_by_address", "garbage_collection_by_location", "garbage_sorting"]);
});

test("通知・応答だけなら 202 (本文なし)", async () => {
  for (const body of ['{"jsonrpc":"2.0","method":"notifications/initialized"}', '[{"jsonrpc":"2.0","method":"notifications/initialized"}]', '{"jsonrpc":"2.0","id":5,"result":{}}']) {
    const r = await post(body);
    assert.equal(r.status, 202, body);
    assert.equal(await r.text(), "");
  }
});

test("パースできない本文・空の本文は 400", async () => {
  for (const body of ["{", "", "   ", new Uint8Array([0xff, 0xfe])]) {
    const r = await post(body);
    assert.equal(r.status, 400);
    const j = await r.json() as any;
    assert.equal(j.error.code, -32700);
  }
});

test("Origin: 知らない Origin は 403、Origin なし・localhost・claude.ai は通す", async () => {
  const ping = '{"jsonrpc":"2.0","id":1,"method":"ping"}';
  assert.equal((await post(ping, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await post(ping)).status, 200);
  const r = await post(ping, { Origin: "http://localhost:6274" });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("Access-Control-Allow-Origin"), "http://localhost:6274");
  assert.equal((await post(ping, { Origin: "https://claude.ai" })).status, 200);
  const pre = await handleRequest(new Request("https://gomisute.example/mcp", { method: "OPTIONS", headers: { Origin: "http://localhost:6274" } }), env, deps);
  assert.equal(pre.status, 204);
  assert.match(pre.headers.get("Access-Control-Allow-Headers")!, /Mcp-Protocol-Version/i);
  const allowEnv: Env = { ALLOWED_ORIGINS: "https://evil.example" };
  const r2 = await handleRequest(new Request("https://gomisute.example/mcp", { method: "POST", body: ping, headers: { Origin: "https://evil.example" } }), allowEnv, deps);
  assert.equal(r2.status, 200);
});

test("MCP-Protocol-Version: 対応していない版は 400、対応している版は通す", async () => {
  const ping = '{"jsonrpc":"2.0","id":1,"method":"ping"}';
  assert.equal((await post(ping, { "MCP-Protocol-Version": "1999-01-01" })).status, 400);
  for (const v of ["2025-06-18", "2025-03-26", "2024-11-05"]) assert.equal((await post(ping, { "MCP-Protocol-Version": v })).status, 200);
});

test("大きすぎる本文は 413", async () => {
  const r = await post("[" + "1,".repeat(600000) + "1]");
  assert.equal(r.status, 413);
});

test("Content-Length の無い (chunked) 本文: 1MB を超えた所で読むのをやめて 413、小さければふつうに答える", async () => {
  const stream = (next: () => Uint8Array | null) => new ReadableStream<Uint8Array>({ pull(c) { const b = next(); if (b) c.enqueue(b); else c.close(); } });
  const postStream = (body: ReadableStream<Uint8Array>) =>
    handleRequest(new Request("https://gomisute.example/mcp", { method: "POST", body, duplex: "half" } as RequestInit), env, deps);
  let pulled = 0;
  const chunk = new Uint8Array(64 * 1024).fill(0x20);
  const r = await postStream(stream(() => (++pulled <= 1600 ? chunk : null)));          // 全部読めば 100MB
  assert.equal(r.status, 413);
  assert.ok(pulled < 40, `1MB を超えても読み続けた (${pulled} 塊)`);
  const parts = ['{"jsonrpc":"2.0",', '"id":1,"method":"ping"}'].map((s) => new TextEncoder().encode(s));
  const r2 = await postStream(stream(() => parts.shift() ?? null));
  assert.equal(r2.status, 200);
  assert.equal(await r2.text(), '{"jsonrpc": "2.0", "id": 1, "result": {}}');
});

test("ツールの答えは mcp_server.py と同じ文字列 (HTTP を通しても)", async () => {
  const body = '{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "garbage_collection_by_address", "arguments": {"address": "金沢市笠舞2丁目29番5号", "date": "2026-10-20"}}}';
  const r = await post(body);
  assert.equal(r.status, 200);
  const j = await r.json() as any;
  const o = JSON.parse(j.result.content[0].text);
  assert.equal(o.status, "ok");
  assert.equal(o.location.collection_district, "南小立野");
  assert.equal(o.answerable, true);
});

test("緯度経度でも答える (金沢市の中の地点: 区域と最寄りの住所で地区を決める)", async () => {
  const body = '{"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "garbage_collection_by_location", "arguments": {"latitude": 36.5547, "longitude": 136.6714, "date": "2026-10-20"}}}';
  const r = await post(body);
  assert.equal(r.status, 200);
  const j = await r.json() as any;
  const o = JSON.parse(j.result.content[0].text);
  assert.equal(o.status, "ok");
  assert.equal(o.location.collection_district, "小立野");
  assert.equal(o.location.method, "address_and_zone_agree");
  assert.deepEqual(o.location.nearest_address, { label: "石引一丁目7番15号", distance_m: 7.1 });
});

// ---------------------------------------------------------------- env から作る Deps
function fakeR2(): Env["PACKS"] {
  return { async get(key: string) {
    try { const b = readFileSync(join(WORKER, "packs", key)); return { arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) }; }
    catch { return null; }
  } };
}

test("R2 のパックを gzip から読み、sha256 を確かめる", async () => {
  const d = makeDeps({ PACKS: fakeR2() });
  for (const m of Object.values<any>(MANIFEST.municipalities)) {
    for (const p of Object.values<any>(m.packs)) {
      if (p.key.endsWith(".bin.gz")) assert.deepEqual(await d.bytes(p.key, p.sha256), readBytes(p.key, p.sha256));
      else assert.deepEqual(await d.pack(p.key), readPack(p.key));
    }
  }
  const t = MANIFEST.municipalities["172014"].tiles.loc;
  const [name, sha] = Object.entries<string>(t.files)[0];
  const tkey = `${t.prefix}${name}.${sha.slice(0, 12)}.bin.gz`;
  assert.deepEqual(await d.bytes(tkey, sha), readBytes(tkey, sha));
  await assert.rejects(d.bytes(tkey, "0".repeat(64)), /ハッシュ/);
  await assert.rejects(d.pack("172014/core.000000000000.json.gz"));
  await assert.rejects(makeDeps({}).pack("045811/core.000000000000.json.gz"));
  const b = readFileSync(join(WORKER, "packs", MANIFEST.municipalities["045811"].packs.core.key));
  const buf = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
  assert.deepEqual(await gunzipJson(buf, MANIFEST.municipalities["045811"].packs.core.sha256), readPack(MANIFEST.municipalities["045811"].packs.core.key));
  await assert.rejects(gunzipJson(buf, MANIFEST.municipalities["172014"].packs.core.sha256), /ハッシュ/);
});

test("KV の変更監視の結果: 鍵は status:<lg_code>、無い・読めないときは null", async () => {
  const seen: string[] = [];
  const d = makeDeps({ STATUS: { async get(key: string) { seen.push(key); return key === "status:172014" ? GMETA.status.fresh : null; } } });
  assert.deepEqual(await d.status("172014"), GMETA.status.fresh);
  assert.equal(await d.status("045811"), null);
  assert.deepEqual(seen, ["status:172014", "status:045811"]);
  const throwing = makeDeps({ STATUS: { async get() { throw new Error("KV down"); } } });
  assert.equal(await throwing.status("172014"), null);
  assert.equal(await makeDeps({}).status("172014"), null);
});

test("gps2city: Service Binding を使い、動いていなければ公開の URL に聞く", async () => {
  const calls: string[] = [];
  const binding = { async fetch(input: RequestInfo) { calls.push(String(input)); return new Response(JSON.stringify({ lg_code: "131016", city: "千代田区", prefecture: "東京都", ward: "" })); } };
  assert.equal((await makeDeps({ GPS2CITY: binding }).gps2city(35.68, 139.76)).lg_code, "131016");
  assert.deepEqual(calls, ["https://gps2city/location"]);
  const realFetch = globalThis.fetch;
  try {
    const urls: string[] = [];
    globalThis.fetch = (async (u: RequestInfo, init?: RequestInit) => { urls.push(String(u)); assert.equal(init?.method, "POST"); return new Response(null, { status: 404 }); }) as typeof fetch;
    const down = { async fetch() { return new Response("no local dev session", { status: 503 }); } };
    const r = await makeDeps({ GPS2CITY: down, GPS2CITY_URL: "https://gps2city.example" }).gps2city(30, 140);
    assert.deepEqual(r, { error: "HTTP Error 404" });
    assert.deepEqual(urls, ["https://gps2city.example/location"]);
  } finally { globalThis.fetch = realFetch; }
});
