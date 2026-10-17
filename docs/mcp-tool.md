# LLM 向けツール (MCP)

gomisute-gps の目的は「LLM が唯一確かな情報源として使える」こと。[`mcp_server.py`](../mcp_server.py) が、標準入出力の MCP サーバー
(JSON-RPC 2.0、MCP SDK なし) として次の3つのツールを出す。リポジトリの [`.mcp.json`](../.mcp.json) で Claude Code から使える
(`claude mcp add gomisute -- python /path/to/mcp_server.py` でもよい)。

Claude Desktop では、設定 → 開発者 → 「構成を編集」で開く `claude_desktop_config.json` (Windows は `%APPDATA%\Claude\`、macOS は
`~/Library/Application Support/Claude/`) の `mcpServers` に足して、Claude Desktop を再起動する。Python は 3.11 以上で、
pandas・geopandas・shapely 2・python-dateutil が入っているもの (README の「再現手順」でデータを作った環境)。
`python` が PATH に無ければ python の実行ファイルを絶対パスで書く:

```json
{
  "mcpServers": {
    "gomisute": {
      "command": "python",
      "args": ["C:\\path\\to\\gomisute-gps\\mcp_server.py"]
    }
  }
}
```

起動してからデータを読み込み終わるまで約5秒かかる (その間に来た呼び出しは、読み込みが終わるまで待つ)。

| ツール | 入力 | 対応 |
|---|---|---|
| `garbage_collection_by_address` | `address` (住所の文字列のまま、市町村名から。「金沢市笠舞2丁目29番5号」「金沢市笠舞2-29-5」「金沢市福久町ロ21番地」「女川町針浜」)・`date`・`days`・`detail` | 金沢市 (住居表示の住所・地番)、女川町 (行政区の名前・大字などの地名) |
| `garbage_collection_by_location` | `latitude`・`longitude`・`date`・`days`・`accuracy_m`・`accuracy_confidence`・`detail` | 金沢市 (女川町の地点なら、行政区を聞いて住所のツールを使うよう返す) |
| `garbage_sorting` | `municipality`・`item`・`date` (日付で分け方が変わる自治体がある) | 女川町 |

- 既定では、答えに要る項目だけの要約 JSON を返す (`status`・`answerable`・`summary_ja`・`location`・`days[]` (ok のときだけ。候補には根拠と確かめる行だけ)・`next_after_date`・`candidates[]` (全部。確かめる行も削らない。ok でないときは候補ごとの日付も)・
  `consensus`・`put_out_by`・`contact`・`sources`・`freshness` など。収集日は1回 約3千文字、分別は約1千2百文字)。`detail="full"` で下の表の JSON を全部返す (約4〜5千文字)
- `days` の既定は MCP では 1 (その日だけ)。`resolve.py` をコマンドで使うときの既定は 2
- 住所は区域表の行に直接当てる (位置から推定するより確か)。読み方は [`kzaddress.py`](../kzaddress.py):
  住居表示 (「29番5号」「29-5」「2-29-5」「二十九番五号」「29番地5」「29番地の5」「29 5」)、地番 (「ロ21番地」「字南291番地」)、街区だけ (「29番」)。
  郵便番号・建物名・部屋番号は落とす。読んだ住所が国土地理院・ABR の住所データに実在するかを確かめる (街区が無ければ確定しない。
  街区はあるが住居番号が国土地理院の住所に無いときは、街区の中がすべて同じ地区のときだけ確定し、そのことを notes に書く)。
  「泉野出町3-16」のように「3丁目16番」とも「3番16号」とも「X丁目3番16号」とも読める書き方、丁目や小字が抜けた書き方、町名だけ
  (町全体が1つの地区だと区域表に書いてある町だけ確定) は、読み方で地区が変わるなら候補にして聞き返す。
  市町村名が無く、同じ町名が金沢市の外にもある (例: 押野2丁目は野々市市にもある) ときは確定しない
- 女川町は、「女川町」のあとがカレンダーの凡例の行政区の名前そのもの (「女川町針浜」「女川町清水区」。「女川針浜」も可) なら確定、大字などの地名 (「浦宿浜」) は町の資料から推定した行政区を候補にして聞き返す
  (どの候補でも同じ日は `consensus` で答えられる)。江島は町のカレンダーに日程が無いので `schedule_not_published`
- `freshness`: 元にした公式ページを [`monitor.py`](../monitor.py) で取り直した結果。変わっていたら (消えたページを含む) `status=unresolved`・
  `decision_blocked_by.reason=source_changed` にして、作り直して確かめるまで確かな答えとして返さない (候補の日付も返さない)。
  確認が 7日より古い (`stale`)、一部のページを取れなかった・監視していない (`partly_unchecked`)、監視の結果が無い (`unchecked`) ときは
  `answerable=false` にして、要約に「念のため公式ページでも確かめて」と添える (分別の `garbage_sorting` も同じ)
- 計測 (同じ質問を、ウェブ検索だけの AI と、このツールだけの AI に聞く) は [docs/mcp-bench.md](mcp-bench.md)

以下は金沢市の答え (`resolve.py` の JSON、`detail="full"`) の定義。女川町 (`onagawa.py`) も同じ形で、地区の代わりに行政区を返す。

## モデルに渡す文

ツールの説明 (`tools/list` の `description`) と、`initialize` の `instructions` に、同じ「答え方」を入れている (文面は [`mcp_server.py`](../mcp_server.py) の `RULES`)。要点:

1. `status=ok` かつ `answerable=true` のときだけ、`days[].collections[].official_label` を確かな答えとして言い、`sources[]` の題名を出典として添える。
   `label_note` がある品目は、見出しと一緒にその内容 (今の出し方) を伝える
2. `status=ambiguous`: 地区を1つに決めない。`candidates[].how_to_confirm` (区域表の行、女川町はカレンダーの凡例) を使って、街区・番号・地番・行政区を利用者に聞き返す。
   候補が2つ以上で `consensus` があれば、どの候補でも同じ答えだけは言ってよい
3. `status=unresolved`: `decision_blocked_by.reason` のとおりに理由を伝え、`contact` を案内する。候補は確かな答えではなく、正しい地区が候補に無いこともあるので「どの候補でも」とは言わない
4. `in_calendar_range=false` や `collections=null` の日は「分からない」と答え、**「収集なし」と言わない**。`collections=[]` のときだけ「収集なし」
5. `is_exception=true` の品目は「通常の規則 (`rule_text`) と違う日」と添え、理由を推測しない。2026年12月・2027年1月は規則から曜日を計算せず、返った日付だけを使う
6. 出す時刻は `put_out_by`。`not_covered` にあること (金沢市の分別方法・集積所の場所など) は推測しない
7. `freshness.status=source_changed` なら、日付を言わずに公式ページを案内する

`decision_blocked_by.reason` の意味:

| reason | 意味 |
|---|---|
| `address_not_found` | 住所の町名を、金沢市の町名として見つけられない |
| `place_town_not_in_rules_table` | この場所 (住所) の町名が市の区域表に載っていない |
| `census_town_not_in_rules_table` | この地点を含む町 (国勢調査の町名) が区域表に無い |
| `nearby_town_not_in_rules_table` | 近く (位置の誤差の範囲) に区域表に無い町の住所・地番・町があり、どちらの町か決められない |
| `near_municipal_boundary` | 隣の市町との境界のそばで、金沢市かどうかも言い切れない |
| `rules_table_note` | 区域表の行に注記があり (主計町)、収集地区を言い切れない |
| `no_official_row` | 区域表の根拠が見つからない |
| `outside_town_polygons` | 金沢市内だが町の区域 (国勢調査の小地域) の外 |
| `accuracy_too_low` | 位置の精度が低すぎる (住所を聞く) |
| `place_not_found` / `place_not_mapped` | (女川町) 地名が見つからない / 地名は分かるが行政区との対応が町の資料に無い |
| `schedule_not_published` | (女川町) 町のカレンダーにその地区の日程が無い (江島) |
| `item_not_found` / `not_in_official_table` | (分別) 品目が分別表に無い / 分別表に出し先が書かれていない |
| `source_changed` | 元にした公式ページが変わった (消えた) |

`status=out_of_coverage` は未対応の自治体 (`coverage[]` に対応している自治体。住所・緯度経度のツールでは `location.municipality.name` に、その場所の自治体。ほかの町の出す時刻・問い合わせ先は付けない)、`invalid_input` は入力の誤り (MCP では `isError=true`)、
`out_of_range` (分別) はデータの期間 (2026年度) の外の日付。

## 入力 (`garbage_collection_by_location`。`garbage_collection_by_address` は `latitude`・`longitude`・`accuracy_*` の代わりに `address`)

| 名前 | 型 | 必須 | 説明 |
|---|---|---|---|
| `latitude` | number (20〜46) | ○ | 緯度 (WGS84)。範囲は MCP の入力スキーマで弾く (resolve.py は ±90 と緯度経度の入れ替えだけを見て、日本の外は out_of_coverage) |
| `longitude` | number (122〜154) | ○ | 経度 |
| `date` | `YYYY-MM-DD` (2000〜2099年) | | 日本時間の日付。省略で今日。時刻つきの日時は受け付けない |
| `days` | integer (1〜14) | | 何日分返すか (MCP の既定 1。`resolve.py` のコマンドの既定 2 = 今日と明日) |
| `accuracy_m` | number (0〜1000) | | 端末が返す位置の精度 (m) をそのまま渡す。省略時は 10m 以内とみなす。10m を超えるのに渡さないと誤ることがある (Android 式 30m で 0.83%) |
| `accuracy_confidence` | number (0.5〜0.99) | | `accuracy_m` の半径に本当の位置が入る確率。省略・null で 0.68 (Android の `Location.getAccuracy`。Android の Chrome もこの値をそのまま `coords.accuracy` に渡す)。95% の半径だと分かっている端末だけ 0.95。サーバーは 99% の半径を求め、すべての根拠の距離に足して探す。99% の半径が 300m を超えると `accuracy_too_low` |

## 出力 (`resolve.py` の JSON)

| フィールド | 意味 |
|---|---|
| `status` | `ok` (公式の根拠が1つの地区だけを指し、その地区にこの場所を直接指す根拠がある) / `ambiguous` (公式の根拠が複数の地区を指す、または近くの根拠しか無い) / `unresolved` (区域表に無い町が関わる・公式の根拠が無い) / `out_of_coverage` / `invalid_input` |
| `answerable` | 地区が1つに決まり、かつ全日程がカレンダーの範囲内のときだけ `true` |
| `query` | 緯度経度・日付・`timezone` (Asia/Tokyo)・`date_basis` (`user` / `server_today_jst`)・`accuracy_m`・`accuracy_confidence`・`search_radius_m` (99% の半径) |
| `location` | 市区町村 (`lg_code`、gps2city で確認)、`collection_district` (ok のときだけ。確定していないときに「最有力」は出さない)、`district_confidence` (位置→地区の確信度。収集日の確かさではない)、`method`、`place_name` (この場所の町: 最寄りの住所・地番のうち近い方の町、無ければ国勢調査の町)、`nearest_address` (25m 以内の最寄りの住居表示住所と距離。無ければ null)、`nearest_parcel` (30m 以内の最寄りの地番と距離。ABR 地番代表点、住居表示の無い筆)、`decision_blocked_by` (unresolved のときの理由: `reason`、理由によって `town`・`source`・`label`・`distance_m`・`note` など)、`distance_to_municipal_border_m` (隣の市町との境界までの距離)、`evidence_radii_m` (住所・地番・区域の境目を探した半径) |
| `candidates[]` | `collection_district`・`official` (区域表の行による根拠が1つでもあるか)・`basis[]` (`{kind, official, label, detail}`。女川町は `gyoseiku` 行政区の名前 / `place_to_gyoseiku` 地名から推定した行政区。金沢市の kind は `given_address_row` 利用者が伝えた住所 (住居表示・地番) が区域表の行に当たる / `address_row` 10m 以内の最寄りの住所が区域表の行に当たる / `parcel_row` 30m 以内の最寄りの地番が区域表の行に当たる / `zone_polygon` 区域表から作った区域 / `near_address` 25m 以内の住所の地区 (行が決まらない住所はその町の地区を全部) / `near_parcel` 80m 以内の地番の地区 (地区が分かれる町の地番なら、その町の地区を全部) / `near_boundary` 10m 以内に別の地区の区域、または位置の誤差の範囲にかかる国勢調査の町 (どの半径も `accuracy_m` を渡すと 99% の半径を足す) / `town_row` 町単位の行 / `census_town_row` 地点を含む国勢調査の町の行 / `town_row_prefix` よく似た町名の町全体の行 / `unindexed_row` 住居表示住所の無い街区 (ABR の街区代表点、120m 以内) / `conditional_similar_row` よく似た町名の条件付きの行 (非公式) / `inferred_lineage` 旧町名の系譜からの推定 (非公式))・`evidence`・`how_to_confirm` (区域表の行の原文)・`days` |
| `days[]` | `date`・`weekday`・`in_calendar_range`・`collections` (`null` = 分からない、`[]` = 公式に収集なし)・`note` (年末年始など) |
| `days[].collections[]` | `category`・`official_label` (表の見出しの原文)・`rule_text` (例: 「第2・4木曜日」)・`is_exception` (規則と違う表の日付。2026年12月・2027年1月だけ)・`exception_note` |
| `days[]` (トップ) / `next_after_date[]` | ok のときだけ。確定した地区の日ごとの答えと、品目ごとの次の収集日 (無ければ `null_reason`)。ok でないときは `candidates[].days` |
| `consensus[]` | `ambiguous` で候補が2つ以上のとき、どの候補でも同じになる品目 (候補が1つのとき、`unresolved` のときは `null`。unresolved では正しい地区が候補に無いことがある) |
| `town_rows` | 関係する町の区域表の行 (原文、最大12行)。曖昧なときに利用者へ確認する材料 |
| `calendar` | 使ったカレンダーの有効期間 (`valid_from`〜`valid_to`) |
| `official_notes` | 区域表の注記を、意味を補わずにそのまま伝える文 (例: 主計町「(注意)実際の地区とは異なります」) |
| `notes` | 区域表の行が街区・地番では引けない、など判定の補足 |
| `location.inference` | 確定したが推定を含む場合の説明 (例: 「区域表に 新竪町 の行は無く、よく似た町名 新竪町3丁目 の行 (町全体) から推定」)。近くの住所・地番の行で決まったときは null |
| `put_out_by` | 出す時刻: `deadline` (収集日の午前8時30分まで)・`official_text`・`source` (市の「ごみの出し方のルール」のページ) |
| `not_covered` | データに無いこと (分別方法・出し方、集積所の場所) |
| `holiday_policy` | 祝日も表のとおり収集 (根拠: 市の「ごみの出し方のルール」の「年末年始を除く月曜日～金曜日（祝日含む）」と、2026-11-03 文化の日の収集)。2026年12月・2027年1月は燃やすごみ以外の品目も規則と違う日がある |
| `contact` | ごみ減量推進課 076-220-2302 (公式ページの記載) |
| `sources[]` | 区域表 (題名・発行者・URL)、カレンダー (題名・節・URL・発行者・取得日時・表の SHA-256)、出し方のルール (題名・発行者・URL・取得日時・ページの SHA-256) |
| `data_version` / `generated_at` | データの版と生成時刻 |
| `summary_ja` | そのまま読み上げられる要約。データに無いことは言わない。候補は3つまで並べ、残りは `candidates[]` |
| `freshness` | 公式ページの変更監視の結果: `unchanged` (確認日時つき) / `source_changed` (変わった・消えたページ。この答えは unresolved になる) / `stale` (確認が 7日より古い。`answerable=false`) / `partly_unchecked` (一部のページを取れなかった、監視していない。`answerable=false`) / `unchecked` (監視の結果が無い・読めない。`answerable=false`) |
| `location.address` | 住所で引いたとき: `input`・`label`・`readings[]` (読み方ごとの `kind` (jukyo 住居表示 / chiban 地番 / block 街区だけ / town 町名だけ / needs_chome 丁目が抜けている / unparsed 番地を読めない)・町名・街区・番号・小字・地番)・`in_rules_table`・`municipality_stated` (市町村名が書かれていたか) |
| `location.place_match` / `place_basis` | (女川町) 地名から行政区をどう当てたか (「行政区「針浜」」「地名「浦宿浜」→ 行政区の候補 大沢・浦宿全区」) と、その根拠の資料 |
| `days[].collections[].label_note` | (女川町) カレンダーの見出し (`official_label`) が4月印刷のままで、年度の途中で中身が変わった品目の注。その日の出し方を書く (`next_after_date[]` にも付ける) |
| `garbage_sorting` の出力 | `matches[]` (その日に有効な分別表の品目・出し先・出し方・有効期間・出典)、`other_periods[]` (ほかの期間のルール)、`related[]` (同じ語を含む別の品目で出し先が違うもの)、`related_notes[]` (広い区分の例として当たったとき、ほかの品目の出し方の注記にその語があれば)、`freshness`、`contact` |

## 実装メモ (次)

- Worker: gps2city (Service Binding) で `lg_code` を引き、市区町村ごとの区域パック (gps2city と同じタイル形式) と
  スケジュール JSON で `resolve` を Rust/TS に移す。MCP は Streamable HTTP で `tools/list` と `tools/call` を出す
- 日次で公式ページを取り直し、表の SHA-256 が変わったら差分検証 → 再生成 (`last_verified_at` を付ける)
