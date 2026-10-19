# gomisute-gps Worker

`mcp_server.py` と同じ答えを返す MCP サーバー (Cloudflare Worker、Streamable HTTP)。設計と、Python と同じ答えかの確かめ方は [DESIGN.md](DESIGN.md)。

## テストを動かすまで

自治体ごとのデータ (パック、`worker/packs/`、約 9MB) は git に入れていない。テストの前に Python で作る。

1. Python の用意 (リポジトリの一番上で。Python 3.11 以上と pandas・numpy・geopandas・shapely 2・pyproj・python-dateutil)
2. パックを作る (約15秒)

   ```bash
   python nationwide/packs/build_packs.py                  # worker/packs/ と src/generated/ を作る
   python nationwide/packs/build_packs.py --fetch-missing  # data/ に元データが無いときは、無いものだけ取り直してから作る
   ```

   元データ (ABR・国土地理院の住居表示住所・国勢調査の小地域・金沢市の区域表) は `data/` に置く (git に入れない、`build_packs.py` の `INPUTS` に URL)。
   `--fetch-missing` は robots.txt を守り、1つのホストに 1秒に1回まで、User-Agent で名乗って取る (`nationwide/packs/polite.py`)。
   `out/` のファイル (区域・カレンダー・女川町など) はリポジトリに入っている
3. テスト

   ```bash
   cd worker
   npm ci          # package-lock.json のとおりに入れる
   npm test        # Python の答え (test/golden) と全件比べる + HTTP・タイルのテスト (約10秒)
   ```

`data/` の中身が作った時と同じなら、パックは `src/generated/manifest.ts` (git に入っている) の sha256 と1バイトも違わない。
元データが更新されて中身が変わると `manifest.ts` も書き換わるので、正解も作り直す (`python nationwide/packs/golden.py`、約1分) → `npm test`。
パックが無いまま `npm test` を動かすと、作り方を書いたエラーで止まる。

## ほかのコマンド

```bash
npm run build       # 型の確認と wrangler のバンドル (dist/。デプロイはしない)
npm run seed:local  # 手元の R2・KV にパックと変更監視の結果を入れる
npm run dev         # wrangler dev --local (http://localhost:8787/mcp)
```
