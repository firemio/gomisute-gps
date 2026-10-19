// 答えを作るのに外から要るもの: 時刻・パック (R2)・変更監視の結果 (KV)・gps2city。
// Worker では env から作り (index.ts)、テストでは手元のファイルと golden の記録から作る
import { Kanazawa } from "./kanazawa/engine.ts";
import { LocIndex, LocTile } from "./kanazawa/locate.ts";
import { Onagawa } from "./onagawa.ts";
import manifest from "./generated/manifest.ts";
import { PyRuntimeError } from "./py.ts";

export interface Deps {
  now(): Date;
  /** パックの鍵 (manifest の key) → 読んだ JSON */
  pack(key: string): Promise<any>;
  /** バイナリのパック (緯度経度の判定の loc とタイル) の鍵 → gunzip して sha256 を確かめたバイト列 */
  bytes(key: string, sha256: string): Promise<Uint8Array>;
  /** 変更監視の結果 (monitor.py の source_status.json と同じ形。無ければ null) */
  status(lgCode: string): Promise<any | null>;
  /** gps2city の /location の返事 (取れなければ {error: "..."}) */
  gps2city(lat: number, lng: number): Promise<any>;
}

export const MANIFEST: any = manifest;
export const KANAZAWA = "172014";
export const ONAGAWA = "045811";

// 同じパック (鍵は内容のハッシュ) から作った索引は、isolate の中で使い回す
const engines = new Map<string, Promise<any>>();

function cached<T>(key: string, make: () => Promise<T>): Promise<T> {
  let p = engines.get(key);
  if (!p) {
    p = make().catch((e) => { engines.delete(key); throw e; });
    engines.set(key, p);
  }
  return p as Promise<T>;
}

function loadError(key: string, e: unknown): PyRuntimeError {
  return new PyRuntimeError(`データの読み込みに失敗しました: ${key} (${e instanceof Error ? e.message : String(e)})`.slice(0, 400));
}

async function load(deps: Deps, key: string): Promise<any> {
  try { return await deps.pack(key); } catch (e) { throw loadError(key, e); }
}

async function loadBytes(deps: Deps, key: string, sha256: string): Promise<Uint8Array> {
  try { return await deps.bytes(key, sha256); } catch (e) { throw loadError(key, e); }
}

/** 金沢市 (住所の索引つき。core だけなら withAddr=false) */
export function kanazawa(deps: Deps, withAddr = true): Promise<Kanazawa> {
  const m = MANIFEST.municipalities[KANAZAWA];
  const key = withAddr ? `${m.packs.core.key}+${m.packs.addr.key}` : m.packs.core.key;
  return cached(key, async () => new Kanazawa(await load(deps, m.packs.core.key), withAddr ? await load(deps, m.packs.addr.key) : null));
}

/** 金沢市の緯度経度の判定の loc (区域・町・市の区域・境界・表) */
export function kanazawaLoc(deps: Deps): Promise<LocIndex> {
  const p = MANIFEST.municipalities[KANAZAWA].packs.loc;
  return cached(p.key, async () => new LocIndex(await loadBytes(deps, p.key, p.sha256)));
}

// タイルは最近使った 16 枚まで (1枚 最大 0.9MB と、平面に直した点の座標 最大 0.7MB)
const TILE_CACHE = 16;
const tiles = new Map<string, Promise<LocTile>>();

/** 金沢市のタイル (名前 "tx_ty")。市の区域にかからないタイルは無い (null) */
export function kanazawaTile(deps: Deps, name: string): Promise<LocTile | null> {
  const t = MANIFEST.municipalities[KANAZAWA].tiles.loc;
  const sha: string | undefined = Object.prototype.hasOwnProperty.call(t.files, name) ? t.files[name] : undefined;
  if (!sha) return Promise.resolve(null);
  const key = `${t.prefix}${name}.${sha.slice(0, 12)}.bin.gz`;
  let p = tiles.get(key);
  if (p) { tiles.delete(key); tiles.set(key, p); return p; }
  p = loadBytes(deps, key, sha).then((b) => new LocTile(b));
  p.catch(() => tiles.delete(key));
  tiles.set(key, p);
  while (tiles.size > TILE_CACHE) tiles.delete(tiles.keys().next().value!);
  return p;
}

export function onagawa(deps: Deps): Promise<Onagawa> {
  const m = MANIFEST.municipalities[ONAGAWA];
  return cached(m.packs.core.key, async () => new Onagawa(await load(deps, m.packs.core.key)));
}

/** テスト用: 使い回している索引を捨てる */
export function resetEngines(): void { engines.clear(); tiles.clear(); }
