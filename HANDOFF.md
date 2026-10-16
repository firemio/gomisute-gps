# ごみ収集日GPSサービス — 引き継ぎ (2026-10-16)

元セッション: https://claude.ai/code/session_01X7VLku4og8FfR6suhQ1WNy (claude.ai上)
ローカル作業場: C:\firemio\gomi-gps (2026-10-16 夜〜)

## 構想
行政が統一しないので、民間側で全国1,741自治体のごみ収集日データをクローリング→LLM/GISで構造化→GPS一発回答するサービス。正確性と最新性が最優先。工数はLLMで吸収。

## ここまでの結論
- 12自治体サンプル、9自治体判定済
  - 町丁目で完全解決 2 / 概ね解決 5 / 不可 2。収集区域ポリゴン公開は 0。機械可読オープンデータは長野zipのみ (+世田谷CSV)
- 例外は4タイプ: ①番地範囲 (横浜・大阪・金沢・札幌) ②道路境界 (宇都宮) ③建物・集積所 (世田谷団地・大阪・高知) ④自治会境界が非公開 (長野、「一部」約80件)
  - ①②はGIS処理で機械化可。③はポイント上書き。④は開示請求か住民投稿が要る
- 更新周期がバラバラ (年度4月/暦年12月/秋9月/2か月)。月次差分監視が必須
- 小規模自治体は検索エンジンで見つからない。総務省の自治体ドメイン一覧から出発してサイト内探索するクローラ設計

## スキーマ v0.1 要点
- zone: zone_id, muni_code, members(町丁目コード), exceptions[{type: banchi_range|road_side|building|unresolved_partial}], geom(MultiPolygon), confidence, source{url,fetched_at,hash}
- schedule: zone_id, category, category_label, rrule, exdates, rdates, time_window, valid_from/to, source
- rule_kind: rrule | segments(区間分割RRULE) | explicit(実日付)。「第N曜」と「N週目」の区別は自治体ごとに要確認 (世田谷は第N曜で確定)
- API: GET /resolve?lat&lng&date → zone_id, confidence, today[], tomorrow[]。confidence<0.7 は境界候補を返してユーザーに選ばせる

## ローカル実行結果 (2026-10-16 夜)

### 世田谷 (normalize.py → out/setagaya.json)
- 118行 / 275町丁目 / 472 schedule、パースエラー0。LLM不要
- 表記は「2回目・4回目の月曜日」= 第N曜日 (RRULE BYDAY=2MO,4MO) で素直に表現可。前回の「2週目・4週目」懸念は世田谷CSVには当たらない

### 長野 (normalize.py → out/nagano.json)
- zip中身 = 42カレンダー × 品目9 × 365日の**○マトリクス** + 地区対応表(182地区)。祝日・年末年始反映済の実日付 → 最良フォーマット
- 月1系(不燃・紙・缶・ビン・乾電池)は**第N曜日ではなく28日ローテーション**で、GW・年末に35日空けて位相がずれる
- 圧縮: 単一RRULE 112 / 区間分割RRULE(rule_kind=segments, 最大3区間) 250 / 明示日付 4。**全件往復一致**
- 地区対応表レベルの「一部・除く」は5件のみ (前回の約80件は 町丁目→地区 PDF側の話。GPS化にはそちらが要る)

### 金沢 (kz_rules.py → split_blocks.py → out/kanazawa_zones.geojson)
- 入力: 市区域表HTML 1,776ルール / e-Stat 2020小地域 900ポリゴン / 地理院住居表示 120,795点 (2025-07-31版)
- 914町のうち **町丁目丸ごと 793 (86.8%)**、住居表示で割れる 102、字・地番型 13、解析不能 6
- 結果: 町丁目採用 765 / ボロノイ分割 87 / 71地区 / 面積カバー 96.6% / 重なり0 / **全市4.0秒**
- **20%ホールドアウト精度 96.1%** (分割町のみ、n=9,309)。誤りは地区境界付近に集中 (笠舞・泉) → 境界距離でconfidenceを下げて利用者に選ばせる設計で吸収
- 未解決 59町の内訳:
  - 国勢調査2020後の新町名 6 (荒屋1丁目・千木1丁目等) → 新しい境界ソースが要る
  - 国勢調査に無い旧町名・地番区域 23+3 / 字・地番(イロハ・甲乙) / 東西南付き地番 / 「4の26」枝番 / 道路以北
  - → **法務省 登記所備付地図(筆ポリゴン、G空間で公開)** を地番型の幾何ソースに使うのが本命
- 区域表の「(注意)実際の地区とは異なります」は収集上の例外注記 → note列に分離

### 確定した設計判断
1. schedule の rule_kind は rrule / segments / explicit の3種。自治体が実日付を出すならそれが正、RRULEは圧縮形式にすぎない
2. 区域幾何は3層: 町丁目(e-Stat) → 住居表示点ボロノイ(地理院) → 筆ポリゴン(登記所備付地図)
3. 町名正規化は必須 (漢数字丁目、異体字 舘/館・薮/藪・刈/苅・嶽/岳、「1丁」「もりの里1」)

## 次にやること
1. 法務省 登記所備付地図(金沢市)を取得 → 字・地番型13町+旧町名を筆ポリゴンで解決
2. 境界付近 confidence: 点から他地区ポリゴンまでの距離で算出 → /resolve に組込み
3. 金沢の校下→収集曜日 (区域表は地区まで。曜日表は別ページ) を取得し schedule 化
4. 長野の 町丁目→地区 PDF をVLMで構造化 (「一部」約80件の実態確認)
5. 那覇・帯広・高知の公式ページ直接確認 / 自治体公式ドメイン1,741件シード

## ファイル
- fetch.py / fetch_geo.py — 取得 (& を含むURLは fetch_geo.py 方式で)
- normalize.py — 世田谷・長野 → schema JSON (往復検証付き)
- kz_tables.py → kz_rules.py — 金沢区域表HTML → data/kanazawa_rules.csv
- split_blocks.py [--holdout] — 金沢ポリゴン生成・評価 / render.py — 確認用PNG
- out/ — setagaya.json, nagano.json, kanazawa_zones.geojson, kanazawa_town_parts.geojson, kanazawa_report.json, kanazawa_map.png

## ローカル実行メモ
- win-cli コネクタは ; | $ 等のシェル演算子を弾く。処理は .py を置いて `python xxx.py` 単発で回す

## 追加決定 (2026-10-16 22:50)
- ごみGPS化には旧地名の正規化が前提として必要と判明
- address2houmukyoku (C:\firemio\address2houmukyoku / 市区町村レベルの旧名→現名・管轄法務局) は流用元。拡張しない
- 新規に address-normalize Worker を別デプロイ:
  1. 市区町村層: address2houmukyoku の旧市町村対応表を共有
  2. 町字層: デジタル庁ABR町字マスター (廃止町字・効力発生日/廃止日) + norm_town (漢数字丁目・異体字)
- 連携は Service Binding。URL統一は将来ルート振り分けで
- 字・地番型は法務省 登記所備付地図 (G空間) の筆ポリゴンで別途

## 最初のタスク (Claude Code)
ABR町字マスターを取得し、out/kanazawa_report.json の missing_poly 32町が
旧町字/廃止町字として引けるか検証。結果で address-normalize のスキーマを決める。
