// テスト用の Deps: パックは worker/packs/ から、時刻・変更監視の結果・gps2city の返事は golden (nationwide/packs/golden.py が記録したもの) から
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MANIFEST, type Deps } from "../src/deps.ts";
import { pyFloatRepr } from "../src/py.ts";

export const WORKER = join(dirname(fileURLToPath(import.meta.url)), "..");

// パックは nationwide/packs/build_packs.py が worker/packs/ に作る (git には入れない)。無ければ作り方を出して止める
const missing = Object.values<any>(MANIFEST.municipalities).flatMap((m) => Object.values<any>(m.packs).map((p) => p.key))
  .filter((k) => !existsSync(join(WORKER, "packs", k)));
if (missing.length) {
  throw new Error(`worker/packs/${missing[0]} が無い。先にリポジトリの一番上で python nationwide/packs/build_packs.py を動かしてパックを作ってください (worker/README.md)`);
}
// GOLDEN_DIR で別の場所の golden も使える (大きな件数で確かめるとき: golden.py --out <場所>)
export const GOLDEN = process.env.GOLDEN_DIR || join(WORKER, "test", "golden");

export const META = JSON.parse(readFileSync(join(GOLDEN, "meta.json"), "utf-8"));

export function goldenGroups(): { group: string; cases: any[] }[] {
  return readdirSync(GOLDEN).filter((f) => f.endsWith(".json.gz")).sort()
    .map((f) => JSON.parse(gunzipSync(readFileSync(join(GOLDEN, f))).toString("utf-8")));
}

const packs = new Map<string, any>();
export function readPack(key: string): any {
  if (!packs.has(key)) packs.set(key, JSON.parse(gunzipSync(readFileSync(join(WORKER, "packs", key))).toString("utf-8")));
  return packs.get(key);
}

/** バイナリのパック (loc・タイル): gunzip して sha256 を確かめる */
export function readBytes(key: string, sha256: string): Uint8Array {
  const raw = gunzipSync(readFileSync(join(WORKER, "packs", key)));
  const got = createHash("sha256").update(raw).digest("hex");
  if (got !== sha256) throw new Error(`パックのハッシュが manifest と違う: ${key}`);
  return new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
}

/** golden と同じ条件の Deps (status は場面の名前) */
export function goldenDeps(statusName: string): Deps {
  const fixed = new Date(META.fixed_now);
  return {
    now: () => fixed,
    pack: async (key) => readPack(key),
    bytes: async (key, sha256) => readBytes(key, sha256),
    status: async () => structuredClone(META.status[statusName] ?? null),
    gps2city: async (lat, lng) => {
      const g = META.gps2city[`${pyFloatRepr(lat)},${pyFloatRepr(lng)}`];
      if (!g) throw new Error(`gps2city の記録が無い: ${lat},${lng}`);
      if (g.status !== 200) return { error: `HTTP Error ${g.status}` };
      return structuredClone(g.body);
    },
  };
}

export function caseBytes(c: any, full = false): Uint8Array {
  if (c.req_b64) return new Uint8Array(Buffer.from(c.req_b64, "base64"));
  return new TextEncoder().encode(full ? c.req_full : c.req);
}
