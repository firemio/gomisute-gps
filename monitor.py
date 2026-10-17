#!/usr/bin/env python3
"""公式ページの変更監視: データの元にした公式ページ・PDF を取り直し、作ったときの内容のハッシュと比べる

  python monitor.py --record     # データを作り直したときに、今の内容を基準として記録 → out/source_registry.json
  python monitor.py              # 今の内容と基準を比べる → out/source_status.json (resolve.py / onagawa.py が読む)
  python monitor.py --simulate-change <url の一部>   # 変更があったことにして書く (動作確認用)

HTML は本文 (金沢市は div#contents-in、ほかは body) の文字だけを、更新日などを除いて空白を詰めてからハッシュする。
PDF などはファイルのバイト列のハッシュ。変更があった公式ページに基づく答えは、作り直して確かめるまで「確かな答え」として返さない。
"""
import datetime as dt, hashlib, html, json, os, re, sys, urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))
JST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "gomisute-gps/0.6 (+https://github.com/firemio/gomisute-gps)"}
REGISTRY, STATUS = "out/source_registry.json", "out/source_status.json"

def sources():
    """データが使っている公式の資料"""
    out = []
    import resolve
    out.append({"url": resolve.RULES_SOURCE["url"], "municipality": "金沢市", "kind": "rules_table", "title": resolve.RULES_SOURCE["title"]})
    out.append({"url": resolve.PUT_OUT_RULES["url"], "municipality": "金沢市", "kind": "put_out_rules", "title": resolve.PUT_OUT_RULES["title"]})
    sch = json.load(open("out/kanazawa_schedules.json", encoding="utf-8"))
    for u, t in sorted({(s["source"]["url"].split("#")[0], s["source"]["page_title"].split("／")[0]) for s in sch["schedules"]}):
        out.append({"url": u, "municipality": "金沢市", "kind": "calendar", "title": t})
    if os.path.exists("out/onagawa_data.json"):
        for s in json.load(open("out/onagawa_data.json", encoding="utf-8"))["sources"]:
            out.append({"url": s["url"], "municipality": "女川町", "kind": s.get("kind", "source"), "title": s.get("title", "")})
    seen, uniq = set(), []
    for s in out:
        if s["url"] not in seen: seen.add(s["url"]); uniq.append(s)
    return uniq

def content_hash(url):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    if raw[:5] == b"%PDF-" or not url.lower().split("?")[0].endswith((".html", ".htm", "/")):
        if raw[:5] == b"%PDF-": return hashlib.sha256(raw).hexdigest(), len(raw)
    t = raw.decode("utf-8", "replace")
    i = t.find('id="contents-in"')
    if i >= 0:
        j = t.find('id="footer', i); t = t[i:j if j > 0 else None]
    else:
        m = re.search(r"<body.*?</body>", t, re.S); t = m[0] if m else t
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"(更新日|最終更新日|掲載日)\s*[:：]?\s*\S{0,20}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha256(t.encode("utf-8")).hexdigest(), len(t)

def now(): return dt.datetime.now(JST).isoformat(timespec="seconds")

def main():
    args = sys.argv[1:]
    srcs = sources()
    if "--record" in args:
        reg = {}
        for s in srcs:
            h, n = content_hash(s["url"])
            reg[s["url"]] = {**s, "content_sha256": h, "size": n, "recorded_at": now()}
            print("record", s["municipality"], s["kind"], h[:12], s["url"])
        with open(REGISTRY + ".tmp", "w", encoding="utf-8") as f: json.dump(reg, f, ensure_ascii=False, indent=1)
        os.replace(REGISTRY + ".tmp", REGISTRY)
        return
    reg = json.load(open(REGISTRY, encoding="utf-8"))
    sim = args[args.index("--simulate-change") + 1] if "--simulate-change" in args else None
    res = []
    for u, r in reg.items():
        try:
            h, n = content_hash(u)
            st = "unchanged" if h == r["content_sha256"] else "changed"
        except Exception as e:
            h, st = None, f"error: {e}"[:200]
        if sim and sim in u: st, h = "changed", "simulated"
        res.append({"url": u, "municipality": r["municipality"], "kind": r["kind"], "title": r["title"], "status": st,
                    "recorded_sha256": r["content_sha256"], "current_sha256": h, "recorded_at": r["recorded_at"]})
        print(st, r["municipality"], r["kind"], u)
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump({"checked_at": now(), "simulated": bool(sim), "sources": res}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS)                         # 書いている途中で落ちても、壊れたファイルを残さない

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
