// パックを R2 (PACKS) に、公式ページの変更監視の結果を KV (STATUS) に入れる
//   npm run seed:local                       手元の wrangler dev 用 (.wrangler/state の中だけに書く)
//   node scripts/put-data.mjs --remote       本番の R2・KV に書く (wrangler login が要る。第1段階ではまだ使わない)
//   node scripts/put-data.mjs --status-only  変更監視の結果だけ (monitor.py を動かしたあとに)
// 先に python nationwide/packs/build_packs.py (パック) と python monitor.py (out/source_status.json) を動かしておく
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WORKER = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = (await import("../src/generated/manifest.ts")).default;
const toml = readFileSync(join(WORKER, "wrangler.toml"), "utf-8");
const bucket = /bucket_name\s*=\s*"([^"]+)"/.exec(toml)[1];

const WRANGLER = join(WORKER, "node_modules", "wrangler", "bin", "wrangler.js");
function wrangler(...args) {
  for (let i = 1; ; i++) {                                   // 届かなかったときは 3回まで取り直す (タイルが多いので途中で止めない)
    try { execFileSync(process.execPath, [WRANGLER, ...args], { cwd: WORKER, stdio: "inherit" }); return; } catch (e) {
      if (i >= 3) throw e;
      console.log(`失敗したのでもう一度 (${i}/3)`);
    }
  }
}

const where = process.argv.includes("--remote") ? "--remote" : "--local";
if (!process.argv.includes("--status-only")) {
  for (const [lg, m] of Object.entries(manifest.municipalities)) {
    for (const p of Object.values(m.packs)) {
      console.log(`R2 ${bucket}/${p.key} (${lg} ${m.short}) ${where}`);
      wrangler("r2", "object", "put", `${bucket}/${p.key}`, "--file", join("packs", p.key), "--content-type", "application/gzip", where);
    }
    for (const t of Object.values(m.tiles ?? {})) {          // 緯度経度の判定のタイル (金沢市 151 枚)
      console.log(`R2 ${bucket}/${t.prefix}* (${lg} ${m.short} タイル ${t.count} 枚) ${where}`);
      for (const [name, sha] of Object.entries(t.files)) {
        const key = `${t.prefix}${name}.${sha.slice(0, 12)}.bin.gz`;
        wrangler("r2", "object", "put", `${bucket}/${key}`, "--file", join("packs", key), "--content-type", "application/gzip", where);
      }
    }
  }
}

// out/source_status.json (monitor.py の結果) を自治体ごとに分けて status:<lg_code> に置く
const statusPath = join(WORKER, "..", "out", "source_status.json");
if (existsSync(statusPath)) {
  const st = JSON.parse(readFileSync(statusPath, "utf-8"));
  const tmp = join(WORKER, ".wrangler", "seed");
  mkdirSync(tmp, { recursive: true });
  for (const [lg, m] of Object.entries(manifest.municipalities)) {
    const part = { checked_at: st.checked_at, simulated: st.simulated ?? false, sources: (st.sources || []).filter((s) => s.municipality === m.short) };
    const f = join(tmp, `status-${lg}.json`);
    writeFileSync(f, JSON.stringify(part));
    console.log(`KV ${m.status_key}: ${part.sources.length} ページ (確認 ${st.checked_at})`);
    wrangler("kv", "key", "put", m.status_key, "--path", f, "--binding", "STATUS", where);
  }
} else {
  console.log("out/source_status.json が無いので KV は入れない (答えは answerable=false・freshness=unchecked になる)");
}
