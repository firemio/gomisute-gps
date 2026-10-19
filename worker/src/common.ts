// resolve.py と onagawa.py が共有する小さな関数 (consensus・_sentences)
import { pyStrip, sortedPy } from "./py.ts";

/** resolve.consensus: 候補が2つ以上のとき、どの候補の地区でも同じになる答え (候補が1つなら null) */
export function consensus(cands: any[]): any[] | null {
  if (cands.length < 2) return null;
  const out: any[] = [];
  cands[0].days.forEach((d0: any, i: number) => {
    if (!cands.every((c) => c.days[i].in_calendar_range)) { out.push({ date: d0.date, all_candidates_agree: null }); return; }
    const sets = cands.map((c) => new Set<string>(c.days[i].collections.map((x: any) => x.official_label)));
    const same = sets.every((s) => s.size === sets[0].size && [...s].every((x) => sets[0].has(x)));
    const inter = [...sets[0]].filter((x) => sets.every((s) => s.has(x)));
    out.push({ date: d0.date, all_candidates_agree: same, collections_in_every_candidate: sortedPy(inter) });
  });
  return out;
}

/** resolve._sentences: 文をつなぎ、2つ以上続く空白を1つに */
export function sentences(...xs: (string | null | undefined | false)[]): string {
  const s = xs.filter((x): x is string => !!x && !!pyStrip(x)).map((x) => pyStrip(x)).join("");
  return s.replace(/[ 　]{2,}/gu, " ");
}
