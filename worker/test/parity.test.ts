// Python (mcp_server.py) と同じ答えを返すかを、golden の全件で確かめる
//   要約 (既定): 返った行が Python と1文字も違わないこと
//   detail="full": 返った行の sha256 が同じこと (違えば、比べる項目のどれが違うかを出す)
// golden は nationwide/packs/golden.py が作る (時刻・変更監視の結果・gps2city の返事も固定して記録してある)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { handleBody } from "../src/mcp.ts";
import { caseBytes, goldenDeps, goldenGroups } from "./helpers.ts";

function excerpt(resText: string | null): any {
  try {
    const r = JSON.parse(resText!);
    const o = JSON.parse(r.result.content[0].text);
    if (o === null || typeof o !== "object" || Array.isArray(o)) return null;
    const loc = o.location || {};
    const b = loc.decision_blocked_by || o.decision_blocked_by || {};
    const day = (d: any) => ({ date: d.date ?? null, collections: d.collections == null ? null : d.collections.map((c: any) => [c.official_label ?? null, c.is_exception ?? null]) });
    return { status: o.status ?? null, answerable: o.answerable ?? null, collection_district: loc.collection_district ?? null,
      candidates: (o.candidates || []).map((c: any) => c.collection_district ?? null), days: (o.days || []).map(day),
      candidate_days: (o.candidates || []).map((c: any) => (c.days || []).map(day)), consensus: o.consensus ?? null, reason: b.reason ?? null,
      summary_ja: o.summary_ja ?? null, matches: (o.matches || []).map((m: any) => [m.item ?? null, m.destination ?? null]) };
  } catch { return null; }
}

const sha = (s: string | null) => createHash("sha256").update(s ?? "", "utf8").digest("hex");
const FIELDS = ["status", "answerable", "collection_district", "candidates", "days", "candidate_days", "consensus", "reason", "summary_ja", "matches"];

const totals = { cases: 0, same: 0 };
const report: string[] = [];

for (const { group, cases } of goldenGroups()) {
  test(`parity: ${group} (${cases.length} 件)`, async () => {
    let same = 0;
    const bad: string[] = [];
    for (const c of cases) {
      const deps = goldenDeps(c.status);
      const got = await handleBody(deps, caseBytes(c));
      const want = c.res ?? null;
      let ok = got === want;
      const why: string[] = [];
      if (!ok) why.push(`要約の行が違う\n    py: ${String(want).slice(0, 600)}\n    ts: ${String(got).slice(0, 600)}`);
      if (c.req_full) {
        const gotFull = await handleBody(deps, caseBytes(c, true));
        if (sha(gotFull) !== c.full_sha256) {
          ok = false;
          const ex = excerpt(gotFull);
          const diff = FIELDS.filter((k) => JSON.stringify(ex?.[k]) !== JSON.stringify(c.full?.[k]));
          why.push(`full の行が違う (項目: ${diff.join(",") || "比べる項目は同じ"})` + (c.full_res ? `\n    py: ${c.full_res.slice(0, 600)}\n    ts: ${String(gotFull).slice(0, 600)}` : ""));
          for (const k of diff.slice(0, 3)) why.push(`    ${k}: py=${JSON.stringify(c.full?.[k])?.slice(0, 300)} ts=${JSON.stringify(ex?.[k])?.slice(0, 300)}`);
        } else if (c.full_res && gotFull !== c.full_res) {
          ok = false; why.push("full の行が違う (sha256 は同じなのに文字列が違う)");
        }
      }
      if (ok) same++; else bad.push(`${c.id} [${c.status}] ${c.req ? c.req.slice(0, 160) : "(bytes)"}\n  ` + why.join("\n  "));
    }
    totals.cases += cases.length; totals.same += same;
    const line = `${group}: ${same}/${cases.length} 同じ (${((100 * same) / cases.length).toFixed(2)}%)`;
    report.push(line);
    console.log(line);
    if (bad.length) console.log(bad.slice(0, 8).join("\n") + (bad.length > 8 ? `\n... ほか ${bad.length - 8} 件` : ""));
    assert.equal(same, cases.length, `${group}: ${cases.length - same} 件が Python と違う`);
  });
}

test("parity: 合計", () => {
  console.log(`合計 ${totals.same}/${totals.cases} 同じ (${((100 * totals.same) / Math.max(1, totals.cases)).toFixed(2)}%)`);
  assert.equal(totals.same, totals.cases);
});
