# gomisute-gps (ごみ収集日GPS) — 引き継ぎ (2026-10-16 → 10-17 更新)

元セッション: https://claude.ai/code/session_01X7VLku4og8FfR6suhQ1WNy (claude.ai上)
ローカル作業場: C:\firemio\gomi-gps (2026-10-16 夜〜。プロジェクト名は 2026-10-17 に **gomisute-gps** へ改名、フォルダ名は未変更)
GitHub: https://github.com/firemio/gomisute-gps (public) — Built with Claude: Japan 2026 応募作品 (応募締切 10/26、開発期間 11/2〜11/10)

## 構想
- **目的: LLM が唯一確かな情報源として使えるごみ収集データと API を作る** (2026-10-17 作者 firemio の言葉)。すべての答えに出典と確信度を付ける
- そもそもの問題: ごみ出しのルールは、各自治体が別々の事業者とアプリ・カレンダー・Web を個別に IT 契約して配っているので、まとまりがない。LLM が参照できる確かな情報源が無い (README 冒頭に明記)
- 行政が統一しないので、民間側で全国1,741自治体のごみ収集日データをクローリング→LLM/GISで構造化→GPS一発回答するサービス。正確性と最新性が最優先。工数はLLMで吸収。
- 作者: firemio (GitHub @firemio)。関連する自作: gps2city (public)、address2houmukyoku (https://address2houmukyoku.souzoku-tsumugiai.net/ で公開中、ソースは非公開)。第三者にも同じ作者と分かるよう、文書では「同じ作者」ではなく firemio と書く

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
- townnorm.py — 町名正規化 norm_town (表示用) / match_key (照合キー、ノ/の も畳む)
- fetch_abr.py [lg_code] [--all] — ABR 取得 / fetch_geo.py <code> [year] — 国勢調査 小地域 (2000〜2020)
- abr_check.py — 32町+27町の ABR 照合 → out/abr_check.{json,md}
- census_lineage.py — 国勢調査差分で旧町名→後継 → out/census_lineage.json
- lineage_report.py — 系譜 → out/kanazawa_town_alias*.csv, docs/abr_check.md

## ローカル実行メモ
- win-cli コネクタは ; | $ 等のシェル演算子を弾く。処理は .py を置いて `python xxx.py` 単発で回す

## 追加決定 (2026-10-16 22:50)
- ごみGPS化には旧地名の正規化が前提として必要と判明
- address2houmukyoku (C:\firemio\address2houmukyoku / 市区町村レベルの旧名→現名・管轄法務局) は流用元。拡張しない。作者 firemio のサービスとして https://address2houmukyoku.souzoku-tsumugiai.net/ で公開中 (ソースは private repo)。知見は docs/prior-art-address2houmukyoku.md
- 新規に address-normalize Worker を別デプロイ:
  1. 市区町村層: address2houmukyoku の旧市町村対応表を共有
  2. 町字層: デジタル庁ABR町字マスター (廃止町字・効力発生日/廃止日) + norm_town (漢数字丁目・異体字)
     → 2026-10-17 の検証で ABR に廃止町字・廃止日は無いと判明。旧町名は国勢調査差分＋文献調査で自前に持つ (下の「最初のタスクの結果」)
- 連携は Service Binding。URL統一は将来ルート振り分けで
- 字・地番型は法務省 登記所備付地図 (G空間) の筆ポリゴンで別途

## 最初のタスク (Claude Code)
ABR町字マスターを取得し、out/kanazawa_report.json の missing_poly 32町が
旧町字/廃止町字として引けるか検証。結果で address-normalize のスキーマを決める。

## 最初のタスクの結果 (2026-10-17, Claude Code)
詳細: docs/abr_check.md / スキーマ: docs/address-normalize-schema.md

### ABR は旧町字を引けない (上の「追加決定」2. の前提は誤りだった)
- ABR の配布元は catalog.registries.digital.go.jp (消滅) → **data.address-br.digital.go.jp** (索引 dataset.address-br.digital.go.jp/api/feed/dcat-us/1.1.json)。fetch_abr.py
- 全国 727,467 行で `ablt_date` 0件・`alias_*` 0件。`efct_date` は 98.6% が 1947-04-17、8,384 行が 1970-01-01 (どちらも仮置き)。**ABR は現行台帳であって履歴台帳ではない**
- しかも **ABR に無い ≠ 廃止**: 郵便番号が現役の北安江町・西念町、地番登記の無い残存地 (西堀川町・八幡町・笠舞町・糸田町・神田町) が ABR に無い
- 大字行の無い小字行 (幽霊大字) が全国 383自治体・5,379件。地番マスターに筆がある時だけ本物の残存 (窪町 31筆 ○ / 入江町ヘ 0筆 ×)
- ノ/の・ヶ/ケ を畳むと同一自治体内で 17件衝突 → 照合キー専用 (townnorm.match_key)。表示名には使わない

### 32町の内訳
ABR にも国勢調査2020にも無い 15 / 小字行だけ残る 3 / 区画整理の仮称 1 / 区域表が丁目省略 3 / ABR に現存・国勢調査2020に無い 10

### 旧町名の系譜
- 国勢調査小地域 2000/2005/2010/2015/2020 の差分を面積按分 → 消えた町名と後継 (金沢10件)。census_lineage.py
- 2000年以前は Claude 調査 (市の換地図PDF・県公報告示番号・郵便番号簿・旧町名一覧PDF) → 別エージェントで反証検証: 19町 確認13 / 訂正5 / 根拠不足1。out/old_town_lineage.json → lineage_report.py → out/kanazawa_town_alias*.csv (後継91件すべて ABR machiaza_id に解決)
- 一番の一次資料は市の換地図PDF (区画整理の外に残った「北安江町ト」等の帯が描かれている)。換地処分公告日+1日 = 町名変更の効力日 (4件で一致)
- 町名は段階的に消え (柳町 1965→1992→1993)、主な転換日と最終消滅日がずれ (寺地町 1979/1990)、中間町名を経て (神田町5丁目)、別の範囲で復活する (観音町 2019) → **町名→町名の表ではなく変更イベントの履歴で持つ**
- ごみ区域表の旧町名行は「その時点でまだ旧町名だった土地」= 最後のイベントの後継を指す (玉井町→木ノ新保町)。どの後継に当てるかは gomisute-gps 側の責務

### gps2city (緯度経度→市区町村) を入口に使う
- https://gps2city.firemio.workers.dev/ (repo firemio/gps2city, 2026-10-17 public 化)。N03 を 0.1°タイル化した Rust Worker
- タイルの5桁コード+検査数字 = ABR lg_code: 1,898/1,905 一致 (7 = 所属未定地)
- 2026-10-17 改修・デプロイ: `/location` が `code`・`lg_code`・`ward` (政令市の区) を返す。`/location2` は各行に `areas` (区ごとの code/lg_code/境界距離)
- 使い道: /resolve の入口 / 収集地区ポリゴンも同じタイル方式で配る / `nearby()` の境界距離を confidence に
- 2026-10-17: 全国タイルを1本のパック (約23MB) にして Worker に埋め込み (44bfaee、初回地点 TTFB 250〜310ms → 11ms)。
  main を Cloudflare 版だけに整理 (dccad44): worker/ (Rust→Wasm) + tools/build_tiles/ (N03 → パック)。旧 Docker/actix 版は削除 (履歴は 44bfaee まで)。
  同じ N03-20240101.geojson から作ったパックがデプロイ中のものと SHA-256 一致。gps2city は今も全部 Rust
- 2026-10-17: 行政区域データを N03-2026 (2026-01-01 時点) に更新・デプロイ (1a51281)。市区町村の増減・改称は無し。全国619万点比較で458点の所属変化。
  能登半島地震の隆起で陸化した輪島市 +2.78km²・珠洲市 +1.72km² が国土地理院の公表値と一致。手元の worker/data/tiles.pack も 2026 版 (708d497f…)

## 次にやること (2026-10-17 更新。上の「次にやること」を置き換え)
1. address-normalize Worker を D1 で実装。設計は docs/address-normalize-schema.md (v0.2、16表、§13 に 11/2〜10 の工程)、DDL は docs/address-normalize-schema.sql。
   試作 prototypes/address-normalize/ (build_check.py → cases_check.py) が全国 ABR 749,603行・296MB と金沢10ケース (30通り、cases_out.txt) を再現する。
   未決事項は同文書 §14 (Workers Paid、旧市町村表の公開可否、調査レコードの公開、HTTP 匿名公開、事前成果物の申告、自動採用の閾値、筆0の扱い)
2. ABR 地番マスター位置参照 (金沢 28.7万筆の代表点、登記所備付地図由来) で字・地番型 unresolved 27町を分割 → 登記所備付地図の筆ポリゴン取得より先にこれを試す
3. 収集地区ポリゴンを gps2city と同じタイル形式にして `/resolve` (gps2city → 地区タイル → 境界距離 confidence)
4. 金沢の校下→収集曜日 schedule 化 / 長野 町丁目→地区 PDF の VLM 構造化
5. 自治体公式ドメイン 1,741件シード → 取得パイプライン (address2houmukyoku のハッシュ差分抽出を流用)
6. ハッカソン: 11/2 開始時点で `pre-hackathon` タグを打ち、期間中の作業と区別できるようにする
