# gomisute-gps Worker の設計 (第2段階)

`mcp_server.py` (標準入出力の MCP サーバー) と同じ答えを、Cloudflare Worker から **Streamable HTTP の MCP** として返す。
claude.ai・Claude Desktop・Claude Code のカスタムコネクタ (サインインなし) から URL だけで使えるようにするのが目的。

- 答えは Python と **1文字も違わない** ことを目標にし、Python の実装から作った正解 (golden) と全件を比べて確かめる (下の「Python と同じ答えか」)
- Python は **データを作る側** に残る。区域表の照合・地番の判定・RRULE の展開など、重い計算と判断は Python で先に済ませ、
  その結果を自治体ごとの「パック」にする。Worker は住所の文字を読んで、パックを引くだけ
- 第1段階で移したもの: MCP の層、`garbage_collection_by_address` (金沢市・女川町)、`garbage_sorting` (女川町)、収集日・要約・変更監視の扱い
- 第2段階で移したもの: `garbage_collection_by_location` の全部 (`resolve.resolve`・`Kanazawa.locate`)。区域・住所の点・地番の点・
  住居表示住所の無い区域表の行・国勢調査の町・隣の市町との境界と、`accuracy_m`・`accuracy_confidence` による探す半径、候補と根拠の並べ方、
  `decision_blocked_by` の理由 (注記・境界のそば・区域表に無い町など)、consensus、要約の文。Python が多角形と点を
  **2km 四方 + 余白 450m のタイル** に分けておき、Worker は地点のあるタイル1つと共通の loc を読んで、距離を測って候補を組み立てるだけ

## 全体の形

```
手元・CI (Python)                                              Cloudflare
  data/ (ABR・地理院・国勢調査)  ┐
  out/  (区域表・カレンダー・女川町) ├─ nationwide/packs/build_packs.py ─▶ worker/packs/<lg>/<名前>.<sha>.json.gz ──▶ R2  (binding PACKS)
  resolve.py・kzaddress.py・onagawa.py ┘        │  └ build_loc.py ─────▶ worker/packs/172014/loc.<sha>.bin.gz・loc/<tx>_<ty>.<sha>.bin.gz ─▶ R2
  mcp_server.py (ツールの定義・RULES の文) ─────┴─▶ worker/src/generated/manifest.ts・mcp_meta.ts ──▶ Worker に同梱
  monitor.py ─▶ out/source_status.json ─▶ scripts/put-data.mjs (自治体ごとに分ける) ────────────────▶ KV  (binding STATUS, status:<lg>)
                                                                       Worker gomisute-gps ──Service Binding──▶ gps2city (GPS2CITY)
  nationwide/packs/golden.py (mcp_server.handle_line の答えを記録) ─▶ worker/test/golden/ ─▶ npm test (Python と同じかを全件比べる)
```

## HTTP (Streamable HTTP、状態を持たない)

| 要求 | 返事 |
|---|---|
| `POST /mcp` (要求を含む) | 200、`application/json`。本文は JSON-RPC の返事 (バッチなら配列) |
| `POST /mcp` (通知・応答だけ) | 202、本文なし |
| `POST /mcp` (パースできない・空の本文) | 400、`{"jsonrpc": "2.0", "id": null, "error": {"code": -32700, ...}}` |
| `GET` / `DELETE` ほか `/mcp` | 405 (`Allow: POST, OPTIONS`)。SSE のストリームもセッション (Mcp-Session-Id) も持たない |
| `OPTIONS /mcp` | 204 (CORS の下調べ) |
| `GET /` | 短い案内 (HTML。MCP の URL・ツール・対応する自治体) |
| ほかのパス | 404 |

- 1つの POST の本文 = `mcp_server.py` の標準入出力の1行。`handle_line` と同じ処理をする (UTF-8 でない本文、空のバッチ、壊れた JSON でも落ちない)
- `Origin` があるときは、localhost 類・同じホスト・claude.ai / claude.com・`ALLOWED_ORIGINS` (カンマ区切り、`*` で全部) だけ通し、ほかは 403。
  `Origin` の無い要求 (ブラウザ以外。Claude のサーバーからの要求など) は通す
- `MCP-Protocol-Version` ヘッダーがあって、対応していない版なら 400。`Accept` ヘッダーは見ない (curl などでも使えるように)
- 本文は 1MB まで (超えたら 413)。`Content-Length` を先に見て、無いとき (chunked) も読みながら数え、1MB を超えた所で読むのをやめる (全部をメモリに溜めない)
- Python と違う所 (HTTP だから): 空の本文は Python なら「返事なし」だが、HTTP では 400 にする。改行で区切った複数の JSON は1つの本文として読む (パースの誤り)

## JSON-RPC (mcp_server.py の `result_for` と同じ)

- `initialize` (版の取り決め: `2025-06-18` / `2025-03-26` / `2024-11-05` を言われたらその版、ほかは `2025-06-18`)、`ping`、`tools/list`、`tools/call`、バッチ
- 誤りの番号: -32700 パースできない (UTF-8 でない場合も)、-32600 要求の形が違う・空のバッチ・id が文字列か整数でない (真偽値・小数・null・オブジェクト)、
  -32602 params・arguments がオブジェクトでない・知らないツール、-32601 知らないメソッド、-32603 内部の誤り
- ツールの中の誤り: 入力の誤りは `isError: true` と `status: invalid_input`。処理できない入力は「エラー: この入力を処理できませんでした (KeyError)」のように
  Python の例外の名前まで同じにする (`townnorm.kan2num` が「二十十丁目」で ValueError を出す、など)
- 返す文字列も Python の `json.dumps` と同じにする (`src/pyjson.ts`): 区切りは `", "` と `": "`、キーの順、float の書き方 (`0.0`・`1e+16`)、
  NaN は null、孤立サロゲートを含むときは全体を `\u` でエスケープ。読む側も Python の `json.loads` に合わせる:
  `NaN`・`Infinity` を読む、整数と小数を分ける (`"id": 1.0` は誤り、`"days": 2.0` は 2)、大きい整数はそのまま (`bigint`)、
  4300桁を超える整数と入れ子 2999 段以上はパースの誤り (段の数は CPython 3.13 Windows で測った値)
- ツールの名前・入力スキーマ・説明・`RULES` の文・`instructions`・対応する自治体 (`COVERAGE`) は、`build_packs.py` が `mcp_server.py` から
  抜き出して `src/generated/mcp_meta.ts` に書く (文面を2か所で直さない)

## ツール

| ツール | 第1段階 |
|---|---|
| `garbage_collection_by_address` | 金沢市 (住居表示・地番・街区・町名、丁目や小字が抜けた形、市町村名の無い住所、ほかの県の住所) と女川町 (行政区・地名、「女川針浜」などの略した形)。金沢市で町名が見つからず、女川町の地名なら言い添える (Python と同じ) |
| `garbage_sorting` | 女川町 (日付で分け方が変わる)。ほかは out_of_coverage |
| `garbage_collection_by_location` | 第2段階で全部。入力の確かめ方・gps2city で市区町村を引く・対応していない自治体 (女川町なら行政区を聞くよう案内)・精度が低すぎる (`accuracy_too_low`、gps2city が別の市町と言っても国勢調査の町の区域の中なら)・町の区域の外 (`outside_town_polygons`)・金沢市の中の地点の地区の判定 (下の「緯度経度の判定」) |

`detail` (summary / full)、`days` (1〜14)、`date` (省略で日本時間の今日) の扱い、要約 JSON (`compact`) の形は `mcp_server.py` と同じ。

## データ

### パック (R2 `PACKS`、手元では `worker/packs/`)

鍵は `<lg_code>/<名前>.<元の JSON の sha256 の頭12桁>.json.gz` (内容で決まる鍵。中身を変えたら鍵も変わるので、古い答えと混ざらない)。
Worker に同梱する `src/generated/manifest.ts` が、自治体 → パックの鍵・sha256・大きさ・状態の KV の鍵を持つ。Worker は R2 から読んで gunzip し、
sha256 が manifest と同じかを確かめてから使う (isolate の中で使い回す)。

| パック | 中身 | 大きさ (JSON / gzip) |
|---|---|---|
| `172014/core` 金沢市 | 地区 (71) × 品目 (5) の収集日 (年度の初日からの日数)、規則と違う日 (2026年12月・2027年1月) の印、規則の文、年末年始の最後と最初の日、カレンダーの範囲、出典 (題名・URL・取得日時・表の sha256)、出す時刻、データに無いこと、問い合わせ先、祝日の扱い、data_version、市の区域を囲む四角 | 141,268 B / 9,679 B |
| `172014/addr` 金沢市の住所 | 区域表の行 (1,776)・町ごとの行、町名 (940、`kzaddress.AddressIndex.known`)、丁目のある町、ほかの市区町村にもある町名 (305)、旧町名の行 (46町)。住居表示住所 119,670 件ごとの区域表の行 (`kzrules.match_row`)、街区 4,268 ごとの `evaluate()` の結果、ABR の地番 338,484 件ごとの区域表の行 (`kzrules.match_parcel`) | 1,349,385 B / 145,930 B |
| `045811/core` 女川町 | 行政区の燃やせるごみ・資源ごみの日付、見出しの注 (label_note) と有効期間、行政区・地名の索引 (`Onagawa.gk`・`places`)、日程の無い行政区 (江島)、分別表 40 品目 (照合に使う名前 `item_names`・材質・容器の語・出し方の文を先に分けたもの)、出典・問い合わせ先など | 75,199 B / 13,996 B |

Worker の側でするのは、住所の文字を読むところ (`kzaddress.clean`・`readings`・`town_of`、`townnorm.norm_town`、`onagawa.match_place`・`_qnorm`・`item_match`
(`difflib.SequenceMatcher` も移した)) と、引いた結果から答えと要約の文を組み立てるところだけ。
`kzrules.match_row` は、丁目の抜けた住所 (「泉野出町1番1号」) で番号が実在しなくても当てるので、Worker にも移した (15行)。
`build_packs.py` は書いたあと、パックから組み立て直した収集日 (71地区 × 371日 = 26,341日分) が `Kanazawa.days()` と全部同じかを確かめる。

### 緯度経度の判定 (金沢市の loc とタイル。第2段階)

`nationwide/packs/build_loc.py` (`build_packs.py` から呼ぶ。約13秒) が、`resolve.Kanazawa` の中身をそのまま書き出す。形式は自前の小さな入れ物
(`gomisute-binpack/1`: `"GPK1"`・ヘッダーの長さ・ヘッダー (JSON)・8バイト境界にそろえた little-endian の配列)。鍵は `<名前>.<gzip 前の sha256 の頭12桁>.bin.gz`、
タイルの一覧と sha256 は `manifest.ts` の `tiles.loc` (Worker が読むたびに sha256 を確かめる)。

| パック | 中身 | 大きさ (元 / gzip) |
|---|---|---|
| `172014/loc` | 区域 955 (町・地区・作り方・飛び地の印・多角形 76,892点)、国勢調査の町 901 (町名・HCODE・多角形 60,944点)、市の区域 (町を合わせたもの、7,639点)、隣の市町との境界 (N03、海岸を除く、4,540点)、住居表示住所の無い区域表の行の点 386 (STRtree を引く順。説明の文つき)、位置も無い行 (7町)、町名・小字の表、区域表の行の元の書き方 (`raw_blk`・`raw_ban`)、判定の定数 (`ADDR_M` など。`resolve.py` から)、座標の式を確かめる点 500 | 2,619,856 B / 1,912,618 B |
| `172014/loc/<tx>_<ty>` 151枚 | 平面直角座標系 VII 系 (EPSG:6675) の 2km 四方に余白 450m を足した範囲の、住居表示住所の点 (町・街区・住居番号・区域表の行) と ABR の地番の点 (町・小字・番号・区域表の行)、その範囲にかかる区域・町の番号 | 合計 18,981,456 B / 6,868,835 B。中央 6,880 B / 2,889 B、最大 892,664 B / 320,082 B (`-24_30`、北緯 36.55°・東経 136.64° のあたりの住宅地) |

- **座標**: 多角形と線は、Python (geopandas・pyproj) が計算した EPSG:6675 の値を float64 のまま入れる。点は元の緯度経度 (国土地理院・ABR。10進9桁まで) を
  1e-9 度の整数 (タイルの左下からの差、int32) で入れ、Worker が `src/geo.ts` の `tmerc` (PROJ の `tmerc.cpp` の exact (Poder/Engsager) をそのまま移したもの) で平面に直す。
  pyproj と1ビットも違わないわけではない: 住居表示住所 120,796点・地番 239,805点の全部で比べると、同じ値は 46% (56,196点・111,199点) で、差は最大 1.43e-9 m
  (`test/locate.test.ts` は確かめる点 500 で 7e-10 m)。はじめの形 (多角形をタイルごとに丸ごと、点も float64 の平面座標) では
  タイルの合計が gzip で 16.9MB (最大 0.77MB) だったのを、多角形は loc に1回だけ、点は緯度経度の整数にして、loc 1.9MB + タイル 6.9MB (最大 0.32MB) にした
- **距離と内外**: shapely (GEOS) と同じ式 (`Coordinate::distance` = `sqrt(dx*dx+dy*dy)`、`Distance::pointToSegment`、`RayCrossingCounter` の数え方) で測る。
  式は同じでも、点の平面座標が上のとおり 1e-9 m ほど違いうるので、距離も同じくらい違いうる。距離が `round(d, 1)` の x.x5 の境目や、
  「何m 以内」の境目 (`ADDR_SURE_M` 10m・`ADDR_M` 25m・`PARCEL_M` 30m・探す半径) から 1e-9 m 以内にあると、Python と違う答えになる。
  検証で、わざと境目ちょうどに置いた 1,500地点では 14地点が違った (10地点は `distance_m` の 0.1m の桁だけ、4地点は `method` と `district_confidence`。
  例: 10m ちょうどで address_and_zone_agree 0.93 と zone_polygon 0.9、30m ちょうどで zone_polygon と parcel_and_zone_agree)。status と地区は変わらなかった。
  ふつうの入力でこうなる確率は 1件あたり 1e-8 ほど
- **1回の答えで読むもの**: loc (isolate で1回) と、地点のあるタイル1つ (最近使った 16 枚まで覚えておく)。タイルに入っているのは住居表示住所と地番の点、
  区域と国勢調査の町の番号だけで、住居表示住所の無い区域表の行の点 (`BLIND_M` 120m + 誤差) は loc に全部入っている。だからタイルの余白に要るのは、
  誤差の半径が最大 (300m。これより大きいと `accuracy_too_low`。`search_radius_m` を 0.1m に丸めて比べるので、実際は 300.05m 未満) のときの
  地番の `PARCEL_NEAR_M` 80m + 300.05m = 380.05m (住居表示住所は `ADDR_FAR_M` 50m + 300.05m、区域と町は `NEAR_M` 10m + 300.05m) で、余白 450m に収まる。
  これは golden では確かめきれない (location の golden は、2km 四方から 250m より外の点をタイルから抜いても全件同じで、200m で初めて違いが出る)。
  そこで `test/locate.test.ts` で、全タイル・全点について「2km 四方から 380.05m (住所は 350.05m) 以内の点はそのタイルにある」こと、
  「2km 四方から 310.05m 以内にかかる区域・町はタイルの番号の一覧にある」ことを確かめる (半径を余白より大きくすると失敗することも確かめた)。
  市の区域を囲む四角 (`bbox`) の外なら何も読まない。タイルの点は、緯度経度の四角で先に絞ってから、要るものだけ平面に直す
- **順番まで同じにするところ**: 最寄りの住所・地番は (距離, 全体の番号) の順 (Python の `sorted`)。住居表示住所の無い区域表の行は、Python が STRtree を
  引いた順に見て「同じ距離なら先に見た方」を残すので、木の順 (どの範囲で引いても同じ順になることを確かめた) を loc に入れた。区域は番号の順で最初に当たるもの、
  国勢調査の町は (町名, 距離) の順
- **隣の市町との境界までの距離** (`distance_to_municipal_border_m` と `near_municipal_boundary`): gps2city の `/location2` (半径つき) で代わりにできるかを
  境界の近く (400m 以内) の金沢市の中の 60地点で比べたところ、同じにはならなかった (gps2city は N03 2026 を 1m で間引いた線と球面の距離、`resolve.py` は
  `out/kanazawa_land_border.geojson` (N03-2024) の平面の距離。差の中央値 0.21m・90% 点 0.69m、gps2city が隣の市町の中と答えた地点が 4 (国勢調査の町では金沢市の中で境界から 6〜24m)、
  0.1m まで同じは 11地点)。答えには 0.1m まで出る (`decision_blocked_by.distance_m` と要約の文) ので、境界の線 (4,540点、約73KB) を loc に入れた
- 速さ (手元の Node): 最初の1回 (core・addr・loc・タイルを読む) 約37ms、同じタイルなら1件 約0.5ms、ほかのタイルを初めて読むとき 約1.4ms

### 公式ページの変更監視 (KV `STATUS`)

`status:<lg_code>` に、`monitor.py` の `out/source_status.json` と同じ形 (`checked_at`・`simulated`・`sources[]`) を自治体ごとに分けて置く
(`scripts/put-data.mjs`。`sources[].municipality` で分ける)。Worker は答えるたびに読み (`cacheTtl` 300秒)、`resolve.freshness`・`apply_freshness` と同じ判断をする:
`unchanged` / `source_changed` (変わった・消えた → unresolved、日付を返さない) / `stale` (確認が 7日より古い) / `partly_unchecked` / `unchecked`
(この3つは `answerable=false`)。KV に無い・読めないときは `unchecked`。

第2段階でも、KV に書くのは手で (`monitor.py` を動かしてから `node scripts/put-data.mjs --status-only --remote`)。
`mcp_server.py` のように自分で取り直す仕組み (Cron Trigger で公式ページを取り直してハッシュを比べる) は次の段階。

### gps2city

`env.GPS2CITY` (Service Binding) に `POST /location` `{latitude, longitude}`。binding が無い・5xx・届かないときは `GPS2CITY_URL`
(既定 `https://gps2city.firemio.workers.dev`) に聞く (手元の `wrangler dev` では gps2city が動いていないので、こちらになる)。
200 以外 (海の上の 404 など) は Python と同じく「市区町村が分からない」として扱う。
`/location2` (周りの市町と境界までの距離) は使わない (上の「緯度経度の判定」の比べた結果のとおり、Python と同じ距離にならないため)。

## Python から移したもの

| Python | TypeScript |
|---|---|
| `mcp_server.py`: `check_args`・`call`・`result_for`・`_one`・`handle_line`・`_dump`・`compact` | `src/mcp.ts`・`src/compact.ts`・`src/pyjson.ts` |
| `resolve.py`: `freshness`・`apply_freshness`、`locate_address`・`_resolve_address`・`_answer`・`days`・`next_after`・`sources`・`consensus`・`summarize` (文面すべて)、`_date`・`fmt` | `src/freshness.ts`・`src/kanazawa/engine.ts`・`src/common.ts`・`src/dates.ts` |
| `resolve.py`: `resolve` (入力の確かめ方・gps2city・`accuracy_too_low`・`outside_town_polygons`・`out_of_coverage`)、`search_radius` | `src/location.ts` |
| `resolve.py`: `Kanazawa.locate` (区域・住所の点・地番の点・住居表示住所の無い行・国勢調査の町・境界のそば・判定・確信度・`town_rows`) | `src/kanazawa/locate.ts` |
| geopandas の `to_crs(6675)` (PROJ の tmerc)、shapely の `distance`・`contains` (GEOS の式) | `src/geo.ts` |
| `kzaddress.py`: `clean`・`kanji_int`・`AddressIndex.town_of`・`readings`・`evaluate`・`chome_siblings`、`townnorm.py`: `norm_town`・`kan2num`、`kzrules.match_row` | `src/kanazawa/address.ts` |
| `onagawa.py`: `match_place`・`resolve_address`・`_day`・`_next`・`year_end`・`_summary`・`sorting`・`item_match`・`_qnorm`、`difflib.SequenceMatcher.ratio` | `src/onagawa.ts`・`src/difflib.ts` |

まだ移していないもの: `monitor.py` の取り直し。

Python の文字の扱いに合わせるため (`src/py.ts`): 文字数・切り出しはコードポイントで数える、正規表現の `\s`・`\d`・`$` を Python と同じ範囲にする
(`\d` は Unicode の数字。「笠舞2丁目٢٩番٥号」も Python と同じく 29番5号 と読む)、`str.isdigit()` (10進数字のほかに ❶・➀・⓵・፩・① なども数字とみなす。
`town_of` が「笠舞2」の次の文字を見るところ)、`str.strip()`・`repr()` (入れ子が深くても再帰しないで書く)・`round()` (偶数への丸め)・
float の書き方・`sorted()` (コードポイントの順) を同じにする。

## Python と同じ答えか (golden)

`nationwide/packs/golden.py` が `mcp_server.handle_line()` に JSON-RPC の行を渡し、返った行をそのまま記録する。時刻 (2026-10-18 21:00 JST)・
変更監視の結果 (11の場面)・gps2city の返事は固定して記録し、Worker のテスト (`test/parity.test.ts`) も同じものを使う。
ツールの呼び出しは要約 (既定) と `detail="full"` の2回で、要約は返った行がそのまま同じか、full は行の sha256 が同じか (違えば、status・answerable・
地区・候補の地区・日ごとの品目・consensus・decision_blocked_by.reason・summary_ja のどれが違うかを出す) を比べる。
組ごとに乱数と JSON-RPC の id を分けるので、`--only` で一部の組だけ作り直しても、全部作ったときと1バイトも違わない。`--only` のとき、ほかの組のファイルと
`meta.json` の記録 (件数・種・Python の版) はそのまま残し、時刻・場面・gps2city の返事が `meta.json` と違えば作らずに止める。

| 組 | 件数 | 中身 |
|---|---|---|
| kanazawa_address | 3,428 | `bench/eval_address.py` と同じ書き方 (住居表示 12通り・地番 5通り) で、実在の住居表示住所 215件・地番 165件から 3,080件。計測の 30問 (`docs/mcp-bench-*.json`) と、文書・検証役の指摘に出てくる住所・壊れた形 (漢数字のくずれ、Unicode の数字、改行、絵文字、桁あふれ) 219件。数字を `str.isdigit()` だけが数字とみなす文字 (❷・➀・⓵・፩ など) や NFKC で数字になる文字 (①・²・⑴) に置き換えた形 129件 (「金沢市大額3❷❻❷番地」の形も) |
| onagawa_address | 200 | 行政区 27 × 書き方、地名 38 × 書き方、日付 (10/1 の前後、年末年始、範囲外) |
| sorting | 669 | 品目 300 × 2026-09-30・2026-10-01、ほかの日付 60、ほかの自治体 9 |
| location | 5,295 | 金沢市の中と周りの 3,225地点 (種だけで決まる `location_points`): 住居表示住所の近く 650・地番の近く 550・市の区域の中で一様に 400・区域 (地区) の境目 500・国勢調査の町の境目 150・隣の市町との境界から 30〜450m 400・海岸 80・住居表示住所の無い区域表の行の近く 100・区域表に無い町や旧町名の町・飛び地・水面など 150・注記のある町 (主計町) 15・市の周り 6km (周りの市町・海) 150・女川町の辺り 40・日本のどこか 40。地点ごとに 1〜2件 (62%)、`accuracy_m` はなし・10・30・60・200m・小数 (`accuracy_confidence` 0.5〜0.99 もときどき)、日付と日数も変える。ほかに決めた地点 (東京・女川町・野々市市・海・精度が低すぎる 32件、金沢市の中のめったに出ない判定の例 11地点 × 3通り) |
| invalid | 101 | 入力の誤り (型・範囲・日付の形・サロゲート・NaN・桁あふれ・大きい整数など) |
| protocol | 128 | MCP の層 (版の取り決め・通知・応答・バッチ・誤った id・パースできない行・UTF-8 でない本文・4300桁・入れ子の深さ・重複したキー・`__proto__` など)。知らないツールの `name` が 2995〜2997段の入れ子 (`repr` を 60文字で切る) 6件 |
| freshness | 240 | 変更監視の 10 の場面 × 住所・分別・緯度経度 (金沢市の中の 3地点を含む) |

結果 (第1段階、2026-10-18): **4,636件すべて同じ (100%)**。別の種 (seed 7) で金沢市の住所を 33,694件に増やした確認 (`golden.py --out` で手元に作る。リポジトリには入れない) も
34,563件すべて同じ。テストが本当に違いを見つけるかは、要約の文を1文字変える・full だけに出る項目を変えると失敗することで確かめた。
`wrangler dev --local` (workerd) で動かした答えも、同じ要求への `mcp_server.py` の答えと同じ文字列だった。

結果 (第2段階、2026-10-18): **9,926件すべて同じ (100%)**。location の 5,295件も、要約の行の文字列と full の行の sha256 が全部同じ。
location の内訳は ok 2,401・ambiguous 1,572・unresolved 528 (accuracy_too_low 235・near_municipal_boundary 180・nearby_town_not_in_rules_table 41・place_town_not_in_rules_table 22・no_official_row 18・census_town_not_in_rules_table 15・outside_town_polygons 14・rules_table_note 3)・out_of_coverage 794 (女川町を住所の道具へ案内 24、海などで市区町村が無い 159)。gps2city が別の市町・海と言い、国勢調査の町の区域では金沢市の中 (`checked_by: census_polygons`) が 17件。
ほかに、gps2city の返事を偽物 (いつも金沢市・いつも野々市市・いつも 404) にして別の種 (7・11・12・31・99) で増やした緯度経度 27,324件 (detail=full。変更監視の場面 4つも) も全部同じ。
テストが違いを見つけるかは、わざと Worker を変えて確かめた: 距離の文字を 0.04m ずらすと 1,591件、住居表示住所の無い区域表の行を STRtree の逆の順に見ると 4件、市町の境界のそばの判定を 1m 広げると 2件が違いになる。同じ距離の点の並べ方 (全体の番号の順) を逆にしても違いは出ない (同じ位置の点は地番の 907か所だけで、どれも同じ番地・同じ区域表の行なので、答えに出ない)。
`wrangler dev --local` (workerd。手元の R2 にパックとタイルを置き、gps2city は本物) の答えも、同じ要求 (金沢市の ok・ambiguous・境界のそば・主計町の注記、海、女川町の 6つ) への `mcp_server.py` の答えと `generated_at` 以外同じだった。

結果 (検証役の指摘を直したあと、2026-10-19): **10,061件すべて同じ (100%)** (kanazawa_address 3,428・onagawa_address 200・sorting 669・location 5,295・invalid 101・protocol 128・freshness 240)。
直したのは、`str.isdigit()` の範囲 (前は 10進数字だけを数字とみなしていたので、「金沢市大額3❷❻❷番地」で Python は unresolved、Worker は ambiguous だった)、
`repr()` の深い入れ子 (前は再帰していたので、知らないツールの `name` が約 2,956段より深い dict だと JS のスタックがあふれ、-32602 のところが -32603 だった)。
どちらも前の実装に戻すと golden が違いを見つける (数字 7件、入れ子 4件)。onagawa_address・sorting・freshness は組ごとの乱数にしたので、中身 (選んだ住所・品目・日付) が前と変わった。

golden で確かめていない、Python と違いうる所:
- 平面直角座標系への直し方は pyproj と 1e-9 m ほど違いうる (全点で最大 1.43e-9 m、同じ値は 46%)。距離が `round(d, 1)` の x.x5 の境目や「何m 以内」の境目
  (10m・25m・30m・探す半径) から 1e-9 m 以内のときだけ、`distance_m` の桁や `method`・`district_confidence` が違いうる (上の「距離と内外」。1件あたり 1e-8 ほどの確率)
- `resolve.py` は「区域の町」と「国勢調査の町」が違い、どちらにも位置の無い区域表の行があるとき、Python の set の順 (文字列のハッシュで実行ごとに変わる) で並べる。
  Worker は区域の町 → 国勢調査の町の順。今のデータでは2つの町が重なる所は、東山1丁目の区域と東山2丁目の町 (9.7e-8 m²)、東山2丁目の区域と東山1丁目の町 (9.8e-9 m²)
  の2か所の境目のかけらだけで、まず起きない
- 点がちょうど多角形の辺の上 (1e-10 m 以内) にあるときの内外の判定 (GEOS は頑健な計算、Worker はふつうの浮動小数点)
- JSON の入れ子の上限 (2998 段) は CPython の版と OS で変わる (Windows の CPython 3.13 で測った値)
- Unicode の版の違い: Python 3.13 は Unicode 15.1、Node・workerd は Unicode 16。16 で増えた文字 (U+1CCF0〜U+1CCF9 の数字は JS の NFKC だけが ASCII にする、
  U+10D40 などのガライ文字は JS の `repr` だけが印字できる文字とみなす) を含む入力では、Worker が ok、Python が ambiguous のように違いうる
- gps2city が prefecture・ward を null で返したり、200 で JSON のオブジェクトでない本文を返したりすると、Python は「None」と書く・例外になる所を、Worker は空の文字・
  市区町村が分からないとして扱う (今の gps2city はそう返さない。記録した 3,142件の 200 の返事はすべて文字列)

## 手元で動かす

```bash
python nationwide/packs/build_packs.py     # パック・緯度経度のタイルと manifest・mcp_meta を作る (約15秒。data/ と out/ が要る。--fetch-missing で無い元データを取り直す)
python nationwide/packs/golden.py          # 正解を作る (約55秒。gps2city の返事は記録 (data/golden_gps2city.json か golden の meta.json) を使い、無い地点だけ 1秒に1回聞く)
python nationwide/packs/golden.py --prefetch-gps --seed <種>   # 新しい種の地点の gps2city の返事を先に取るだけ (3,200地点で約55分)
cd worker
npm ci                                     # package-lock.json のとおりに入れる
npm test                                   # 型を見ずに node --test で全件比べる (約10秒。先に build_packs.py でパックを作る)
npm run build                              # 型の確認 (tsc) と、wrangler のバンドル (dist/。デプロイはしない) 202 KiB / gzip 58 KiB (タイルの一覧と sha256 で +47 KiB)
npm run seed:local                         # 手元の R2・KV にパックと変更監視の結果を入れる
npm run dev                                # wrangler dev --local。http://localhost:8787/mcp
```

パック (`worker/packs/`) は git に入れない (`.gitignore`)。`build_packs.py` が作り、`data/` が同じなら `src/generated/manifest.ts` の sha256 と1バイトも違わない
(手順は `README.md`)。`--fetch-missing` と golden.py の gps2city は、robots.txt を守り (`urllib.robotparser`、ホストごとに1回だけ読む)、1ホストに 1秒に1回まで
(Crawl-delay があればそれに従う)、User-Agent で名乗って取る (`nationwide/packs/polite.py`)。`out/kanazawa_land_border.geojson` を作るスクリプトは無い
(リポジトリに入っている。出典の URL は `build_packs.py` の `INPUTS`)。

Node.js 22.18 以上 (TypeScript の型を外してそのまま動かす)。大きな件数で確かめるときは
`python nationwide/packs/golden.py --n-jukyo 2200 --n-parcel 2200 --seed 7 --out <場所> --only kanazawa_address` のあと
`GOLDEN_DIR=<場所> node --test test/parity.test.ts`。

`python nationwide/packs/golden.py --seed 7 --loc-scale 2 --out <場所> --only location` (地点の gps2city の返事が要る) でも同じように確かめられる。

速さ (手元の Node): 最初の1回 (パックを読んで索引を作る) 約21ms、2回目からは住所1件 約0.3ms。緯度経度は最初の1回 約37ms、同じタイルなら約0.5ms。

## 本番に出すとき (第2段階でもまだしない)

1. Workers Paid ($5/月。無料の 10ms の CPU では、最初にパックを読む回が足りないことがある)
2. `wrangler r2 bucket create gomisute-packs`、`wrangler kv namespace create STATUS` (出てきた id を `wrangler.toml` に書く)
3. `node scripts/put-data.mjs --remote` (パック・タイル 151枚・変更監視の結果。1つずつ wrangler で置くので数分かかる)、`wrangler deploy`。gps2city と同じアカウントなら Service Binding がつながる
4. `npx @modelcontextprotocol/inspector --cli <URL>/mcp --transport http --method tools/list` と、claude.ai のカスタムコネクタ (サインインなし) で確かめる
5. データを作り直したら: `build_packs.py` → `golden.py` → `npm test` → `put-data.mjs --remote` → `wrangler deploy` (新しい鍵のパックを先に置いてから、manifest の入った Worker を出す)

## 次 (第3段階より後)

- 変更監視を Worker の Cron Trigger で (公式ページを取り直し、本文のハッシュを比べて KV に書く。`monitor.py` の `content_hash` と同じ手順)
- 自治体を増やす: パックの形 (`engine`: 区域表と住所の索引の型 `district_table`、行政区のカレンダーの型 `gyoseiku`) はそのままで、
  自治体ごとに `build_<自治体>` を作り、manifest に足す。全国の登録表 (1,741市町村) から、対応していない自治体には `out_of_coverage` を返す
- 呼び出しの上限 (Workers Rate Limiting。claude.ai からの要求は Anthropic の同じアドレス 160.79.104.0/21 から来るので、IP ごとにしない)
