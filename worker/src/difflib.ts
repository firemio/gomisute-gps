// difflib.SequenceMatcher(None, a, b).ratio() の移植 (onagawa.item_match が「似ている」を決めるのに使う)
// 文字はコードポイントで数える。isjunk は無し。b が 200文字以上のときの autojunk (よく出る文字を外す) も Python と同じにする

export function ratio(a0: string, b0: string): number {
  const a = Array.from(a0), b = Array.from(b0);
  const b2j = new Map<string, number[]>();
  b.forEach((ch, i) => { if (!b2j.has(ch)) b2j.set(ch, []); b2j.get(ch)!.push(i); });
  const n = b.length;
  if (n >= 200) {
    const ntest = Math.floor(n / 100) + 1;
    for (const [ch, idx] of [...b2j]) if (idx.length > ntest) b2j.delete(ch);
  }
  const longest = (alo: number, ahi: number, blo: number, bhi: number): [number, number, number] => {
    let besti = alo, bestj = blo, bestsize = 0;
    let j2len = new Map<number, number>();
    for (let i = alo; i < ahi; i++) {
      const newj2len = new Map<number, number>();
      for (const j of b2j.get(a[i]) ?? []) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) ?? 0) + 1;
        newj2len.set(j, k);
        if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
      }
      j2len = newj2len;
    }
    // isjunk が無いので bjunk は空 (autojunk で外したよく出る文字は bjunk に入らない)。同じ文字が続く限り両端を伸ばす
    const junk = (_ch: string) => false;
    while (besti > alo && bestj > blo && !junk(b[bestj - 1]) && a[besti - 1] === b[bestj - 1]) { besti--; bestj--; bestsize++; }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && !junk(b[bestj + bestsize]) && a[besti + bestsize] === b[bestj + bestsize]) bestsize++;
    while (besti > alo && bestj > blo && junk(b[bestj - 1]) && a[besti - 1] === b[bestj - 1]) { besti--; bestj--; bestsize++; }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && junk(b[bestj + bestsize]) && a[besti + bestsize] === b[bestj + bestsize]) bestsize++;
    return [besti, bestj, bestsize];
  };
  let matches = 0;
  const queue: [number, number, number, number][] = [[0, a.length, 0, b.length]];
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop()!;
    const [i, j, k] = longest(alo, ahi, blo, bhi);
    if (k) {
      matches += k;
      if (alo < i && blo < j) queue.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
    }
  }
  const len = a.length + b.length;
  return len ? (2.0 * matches) / len : 1.0;
}
