# address-normalize スキーマ v0.1 (draft)

ごみGPS (gomi-gps) など下流サービスが「自治体の資料に書かれた住所・町名」を**現行の町字ID**に寄せるための正規化層。
Cloudflare Worker として別デプロイし、gomi-gps からは Service Binding で呼ぶ。

## 前提になった検証結果 (2026-10-16, 金沢市 32町 + 全国 ABR)

1. **ABR 町字マスターは現行台帳であって履歴台帳ではない**
   - 全国 727,467 行で `ablt_date` (廃止日) 0件、`alias_oaza/alias_koaza` 0件
   - `efct_date` は 98.6% が 1947-04-17、さらに 8,384 行が 1970-01-01 (どちらもプレースホルダ)。実日付は 2010年代以降の約2千行のみ
   - → 旧町名の履歴は **自前で持つ**。ABR は「現在形の正」として ID と名称だけを採る
2. **旧町名は ABR から完全に消えるとは限らない**: 住居表示で町名が消えても、未実施の地番が「旧町名＋イロハ小字」で残る
   (金沢 窪町イ・入江町ヘ など)。ABR 上は大字行 (type=1) を持たない小字行だけの「幽霊大字」で、全国 383 自治体・5,379 件
3. **国勢調査小地域 2000→2020 の差分**で、消えた町名と後継町を面積比つきで機械抽出できる (金沢 10件)。それより前は文献調査
4. **表記の同一視は「照合キー」に留める**: ノ/の・ヶ/ケ を畳むと全国で 17 件、同一自治体内の別大字が衝突する
5. 自治体のごみ資料には「区画整理の仮称」(湖南第2区画整理地) のような**町字でない地名**も混じる

## 層

```
L1 市区町村   muni / muni_alias        ← ABR mt_city + address2houmukyoku の旧市町村対応表
L2 町字       town / town_alias        ← ABR mt_town (現行) + 国勢調査差分 / ABR スナップショット差分 / 文献調査
L3 地番・街区  (v0.1 では持たない。town に has_parcel_pos / has_rsdt_blk フラグだけ)
```

## テーブル (D1 / SQLite)

### muni — 現行の市区町村 (ABR mt_city)
| col | type | note |
|---|---|---|
| lg_code | TEXT PK | 6桁 (検査数字つき)。N03 の5桁とは `substr(lg_code,1,5)` で突合 |
| pref, county, city, ward | TEXT | |
| name_key | TEXT | 照合キー (正規化済み「都道府県+郡+市+区」) |
| abr_efct_date, abr_ablt_date | TEXT | ABR の生値 (信用しない) |

### muni_alias — 旧市町村名 → 現 lg_code
| col | type | note |
|---|---|---|
| alias_full | TEXT | 例: 石川県石川郡鶴来町 |
| alias_key | TEXT | 照合キー |
| lg_code | TEXT | 後継 (分割なら複数行) |
| event | TEXT | 合併 / 編入 / 市制施行 / 名称変更 / 区設置 |
| effective_date | TEXT | |
| source | TEXT | `address2houmukyoku@<lastUpdated>` など |
| confidence | REAL | LLM 抽出由来は 0.8 から、突合済みは 1.0 |

### town — 現行の町字 (ABR mt_town のスナップショット)
| col | type | note |
|---|---|---|
| lg_code, machiaza_id | TEXT PK | ABR の ID をそのまま使う |
| machiaza_type | INT | 1 大字・町 / 2 丁目 / 3 小字 / 4 なし / 5 道路名 |
| oaza_cho, chome, koaza | TEXT | ABR 表記そのまま (全角数字) |
| name | TEXT | 表示名 `oaza_cho+chome(+koaza)` |
| name_key | TEXT | 照合キー (下記 norm_rule 適用後) |
| phantom_parent | INT | 大字行を持たない小字 = 旧町名の残存地番区域 |
| rsdt_addr_flg, status_flg | INT | ABR の生値 |
| first_seen, last_seen | TEXT | **自前の**スナップショット日付。ABR の efct/ablt は使わない |
| rep_lon, rep_lat | REAL | mt_town_pos の代表点 |
| census_key | TEXT | 国勢調査 2020 小地域 KEY_CODE (幾何の取得先) |
| has_parcel_pos, has_rsdt_blk | INT | 地番位置参照 / 住居表示街区の有無 |

### town_alias — 旧町名・別表記 → 現行町字
| col | type | note |
|---|---|---|
| alias_id | INTEGER PK | |
| lg_code | TEXT | |
| alias_name | TEXT | 資料に書かれた表記そのまま |
| alias_key | TEXT | 照合キー |
| relation | TEXT | `renamed` 1:1 / `split` 1:N / `merged` N:1 / `remnant` 旧町名＋小字が一部残る / `variant` 表記ゆれ / `provisional` 区画整理の仮称など町字でない名称 |
| event | TEXT | 住居表示 / 土地区画整理 / 町名地番変更 / 合併 / unknown |
| valid_until | TEXT | 廃止日 (`YYYY-MM-DD` / `YYYY-MM` / `YYYY`) |
| precision | TEXT | day / month / year / decade / unknown |
| source | TEXT | `census_diff:2000-2005` / `abr_diff:<snap>` / `abr_phantom` / `municipal_doc` / `research` / `user_report` |
| evidence | TEXT(JSON) | [{source, finding}] |
| confidence | REAL | |

### town_alias_target — 旧町名の行き先 (1:N)
| col | type | note |
|---|---|---|
| alias_id | INTEGER | |
| lg_code, machiaza_id | TEXT | 後継の現行町字 |
| share | REAL NULL | 旧町域に占める面積比 (国勢調査差分から) |
| part | TEXT | whole / part |

### norm_rule — 正規化規則 (バージョン管理)
| col | type | note |
|---|---|---|
| rule_id | TEXT PK | |
| kind | TEXT | `canonical` (表示名も寄せる: 漢数字丁目→算用数字、全角→半角) / `key_only` (照合キーだけ畳む: ノ↔の, ヶ↔ケ, 舘↔館) |
| pattern, replacement | TEXT | |
| since | TEXT | |

`key_only` で畳んだ結果、1キーに複数の町字が当たったら**候補を全部返す** (勝手に1件に決めない)。

## API (Worker)

```
GET /v1/normalize?addr=石川県金沢市西念町12
→ { muni: {lg_code, name, via_alias?},
    town: {lg_code, machiaza_id, name} | null,
    via:  {relation, alias_name, valid_until, confidence} | null,
    targets: [{machiaza_id, name, share}],   // 旧町名が複数の現行町字に割れたとき
    rest: "12", candidates: [...], warnings: [...] }

GET /v1/town?lg_code=172014&name=笠舞町     // 町名だけを引く (ごみ区域表の取込用)
RPC (Service Binding): resolveTown(lg_code, name) → 上と同じ
```

町名が割れた旧町名をどの後継に当てるかは**下流が決める** (ごみの区域判定は gomi-gps の責務)。address-normalize は後継と面積比を返すだけ。

## 更新
- ABR: 月次でスナップショットを取り、差分から `town.first_seen/last_seen` と `town_alias(source=abr_diff)` を生成
- 国勢調査: 5年ごと (2025年調査の小地域公開時) に差分を追加
- 文献調査: Claude による調査＋反証検証で `confidence` 付きで登録。利用者からの指摘は `user_report`
