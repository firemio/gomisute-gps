# LLM 向けツール定義 (MCP) — 案

gomisute-gps の目的は「LLM が唯一確かな情報源として使える」こと。`resolve.py` の出力をそのまま MCP ツールとして
Cloudflare Worker から出す前提の定義です (ビルドウィークで実装)。設計は独立した審査エージェントの提案を、
`resolve.py` v5 の実際の出力に合わせて直したもの。

## ツール

- **name**: `garbage_collection_by_location`
- **description** (モデルに渡す文):

> 日本の緯度経度と日付から、その場所で家庭ごみを出せる品目を、自治体の公式の収集カレンダーに基づいて返す。
> 対応は現在 石川県金沢市 (lg_code 172014)、2026年度 (2026-04-01〜2027-03-31) のみ。
> 「今日・明日・○日にこの場所で何のごみを出せるか」「次の○○ごみはいつか」を聞かれたら使う。
> 出す時刻は `put_out_by` (市の「ごみの出し方のルール」: 収集日の午前8時30分まで) で答える。分別方法・出し方・集積所の場所は**このデータに無い** (`not_covered`) ので、そう伝え、推測しない。
> 答え方:
> 1. `status=ok` かつ `answerable=true`: `days[].collections[].official_label` で答え、`sources[]` (title・url) を出典として示す
> 2. `status=ambiguous`: 地区を1つに決めない。候補が2つ以上なら `consensus` (どの候補でも同じになる答え) を先に伝え、
>    候補が1つなら「候補の X 地区なら」と条件つきで伝える。`candidates[].how_to_confirm` (区域表の行) を使って利用者に街区・番地 (地番) を聞き、確かめる
> 3. `status=unresolved`: 確定しない理由は `location.decision_blocked_by.reason`。理由どおりに伝える:
>    `place_town_not_in_rules_table` この場所の町名が市の区域表に載っていない / `census_town_not_in_rules_table` この地点を含む町 (国勢調査の町名) が区域表に無い /
>    `nearby_town_not_in_rules_table` 近く (位置の誤差の範囲) に区域表に無い町の住所・地番・町があり、どちらの町か決められない /
>    `near_municipal_boundary` 隣の市町との境界のそばで、金沢市かどうかも言い切れない / `rules_table_note` 区域表の行に注記があり (主計町)、収集地区を言い切れない /
>    `no_official_row` 区域表の根拠が見つからない / `outside_town_polygons` 金沢市内だが町の区域の外 / `accuracy_too_low` 位置の精度が低すぎる (住所を聞く)。
>    `contact` (ごみ減量推進課 076-220-2302) を案内する。`official=true` の候補も確定ではなく、`official=false` の候補は gomisute-gps の推定で市の記載ではない。
>    正しい地区が候補に無いこともあるので、「どの候補でも」とは言わない
> 4. `status=out_of_coverage`: `location.municipality.name` には未対応だと伝え、その自治体の公式サイトを案内する
> 5. `days[].in_calendar_range=false` の日は「分からない」と答え、**「収集なし」と言わない**
> 6. `days[].collections` が `[]` のときだけ「収集なし」と答える
> 7. `is_exception=true` の品目は「通常の規則 (`rule_text`) とは違う日」と添える。2026年12月・2027年1月は規則から曜日を計算せず、`days`・`next_after_date` の日付だけを使う
> 8. 確定したときは `location.nearest_address` (25m 以内) か `nearest_parcel` (30m 以内) と距離を添え、無ければ `place_name` (町名) を添えて、利用者の場所と合っているか確かめられるようにする
> 日付は日本時間。`date` を省略すると今日 (日本時間)。端末の位置の精度が分かるときは `accuracy_m` に渡す (`accuracy_confidence` は省略でよい。既定の 0.68 は Android と、Android の Chrome の `coords.accuracy` に合う)。

## 入力

| 名前 | 型 | 必須 | 説明 |
|---|---|---|---|
| `latitude` | number (20〜46) | ○ | 緯度 (WGS84)。範囲は MCP の入力スキーマで弾く (resolve.py は ±90 と緯度経度の入れ替えだけを見て、日本の外は out_of_coverage) |
| `longitude` | number (122〜154) | ○ | 経度 |
| `date` | `YYYY-MM-DD` (2000〜2099年) | | 日本時間の日付。省略で今日。時刻つきの日時は受け付けない |
| `days` | integer (1〜14) | | 何日分返すか (既定 2 = 今日と明日) |
| `accuracy_m` | number (0〜1000) | | 端末が返す位置の精度 (m) をそのまま渡す。省略時は 10m 以内とみなす。10m を超えるのに渡さないと誤ることがある (Android 式 30m で 0.83%) |
| `accuracy_confidence` | number (0.5〜0.99) | | `accuracy_m` の半径に本当の位置が入る確率。省略・null で 0.68 (Android の `Location.getAccuracy`。Android の Chrome もこの値をそのまま `coords.accuracy` に渡す)。95% の半径だと分かっている端末だけ 0.95。サーバーは 99% の半径を求め、すべての根拠の距離に足して探す。99% の半径が 300m を超えると `accuracy_too_low` |
| `address_hint` | string | | (予定) 曖昧なときに利用者から聞いた住所 (例: 「笠舞2丁目29番」)。区域表の行に直接当てる |

## 出力 (`resolve.py` の JSON)

| フィールド | 意味 |
|---|---|
| `status` | `ok` (公式の根拠が1つの地区だけを指し、その地区にこの場所を直接指す根拠がある) / `ambiguous` (公式の根拠が複数の地区を指す、または近くの根拠しか無い) / `unresolved` (区域表に無い町が関わる・公式の根拠が無い) / `out_of_coverage` / `invalid_input` |
| `answerable` | 地区が1つに決まり、かつ全日程がカレンダーの範囲内のときだけ `true` |
| `query` | 緯度経度・日付・`timezone` (Asia/Tokyo)・`date_basis` (`user` / `server_today_jst`)・`accuracy_m`・`accuracy_confidence`・`search_radius_m` (99% の半径) |
| `location` | 市区町村 (`lg_code`、gps2city で確認)、`collection_district` (ok のときだけ。確定していないときに「最有力」は出さない)、`district_confidence` (位置→地区の確信度。収集日の確かさではない)、`method`、`place_name` (この場所の町: 最寄りの住所・地番のうち近い方の町、無ければ国勢調査の町)、`nearest_address` (25m 以内の最寄りの住居表示住所と距離。無ければ null)、`nearest_parcel` (30m 以内の最寄りの地番と距離。ABR 地番代表点、住居表示の無い筆)、`decision_blocked_by` (unresolved のときの理由: `reason`、理由によって `town`・`source`・`label`・`distance_m`・`note` など)、`distance_to_municipal_border_m` (隣の市町との境界までの距離)、`evidence_radii_m` (住所・地番・区域の境目を探した半径) |
| `candidates[]` | `collection_district`・`official` (区域表の行による根拠が1つでもあるか)・`basis[]` (`{kind, official, label, detail}`。kind は `address_row` 10m 以内の最寄りの住所が区域表の行に当たる / `parcel_row` 30m 以内の最寄りの地番が区域表の行に当たる / `zone_polygon` 区域表から作った区域 / `near_address` 25m 以内の住所の地区 (行が決まらない住所はその町の地区を全部) / `near_parcel` 80m 以内の地番の地区 (地区が分かれる町の地番なら、その町の地区を全部) / `near_boundary` 10m 以内に別の地区の区域、または位置の誤差の範囲にかかる国勢調査の町 (どの半径も `accuracy_m` を渡すと 99% の半径を足す) / `town_row` 町単位の行 / `census_town_row` 地点を含む国勢調査の町の行 / `town_row_prefix` よく似た町名の町全体の行 / `unindexed_row` 住居表示住所の無い街区 (ABR の街区代表点、120m 以内) / `conditional_similar_row` よく似た町名の条件付きの行 (非公式) / `inferred_lineage` 旧町名の系譜からの推定 (非公式))・`evidence`・`how_to_confirm` (区域表の行の原文)・`days` |
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

## 実装メモ (ビルドウィーク)

- Worker: gps2city (Service Binding) で `lg_code` を引き、市区町村ごとの区域パック (gps2city と同じタイル形式) と
  スケジュール JSON で `resolve` を Rust/TS に移す。MCP は Streamable HTTP で `tools/list` と `tools/call` を出す
- `address_hint` を受けたら、区域表の行 (kzrules.match_row) に直接当てて `ambiguous` を確定させる
- 日次で公式ページを取り直し、表の SHA-256 が変わったら差分検証 → 再生成 (`last_verified_at` を付ける)
