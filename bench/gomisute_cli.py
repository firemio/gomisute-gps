#!/usr/bin/env python3
"""gomisute-gps の MCP ツールと同じ答え (同じ JSON) を返すコマンド。計測で、MCP を使えない環境のエージェントに渡す

  python bench/gomisute_cli.py address "<住所>" [--date YYYY-MM-DD] [--days N]
  python bench/gomisute_cli.py location <緯度> <経度> [--date YYYY-MM-DD] [--days N] [--accuracy M]
  python bench/gomisute_cli.py sorting "<自治体>" "<品目>" [--date YYYY-MM-DD]
  (先に python bench/serve_local.py [port] を起動しておく。--port で合わせる。既定 8765)
"""
import json, sys, time, urllib.error, urllib.request
a = sys.argv[1:]
def opt(k, cast=str):
    if k in a:
        i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return cast(v)
date, days, acc, port = opt("--date"), opt("--days", int), opt("--accuracy", float), opt("--port", int) or 8765
cmd = a[0]
if cmd == "address": name, args = "garbage_collection_by_address", {"address": a[1]}
elif cmd == "location": name, args = "garbage_collection_by_location", {"latitude": float(a[1]), "longitude": float(a[2])}
elif cmd == "sorting": name, args = "garbage_sorting", {"municipality": a[1], "item": a[2]}
else: sys.exit("usage: address|location|sorting ...")
for k, v in (("date", date), ("days", days), ("accuracy_m", acc)):
    if v is not None: args[k] = v
req = urllib.request.Request(f"http://127.0.0.1:{port}", data=json.dumps({"name": name, "arguments": args}, ensure_ascii=False).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
for i in range(60):                       # サーバーがデータを読み込み中なら待つ (最大 約60秒)
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120)); break
    except (urllib.error.URLError, ConnectionError):
        if i == 59: sys.exit(f"エラー: 127.0.0.1:{port} のサーバーにつながりません (python bench/serve_local.py {port} を起動してください)")
        time.sleep(1)
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(r["result"], ensure_ascii=False) if r["ok"] else "エラー: " + r["error"])
