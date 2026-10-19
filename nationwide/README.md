# 全国の自治体台帳 (nationwide/)

gomisute-gps を全国に広げるための台帳です。全国の自治体コード 1 つにつき 1 行で、
島の種類、離島の法律の指定、ABR の地番データの有無、環境省の調査の数字 (公の収集が無い地域があるか)、公式サイト、
gomisute-gps が日付で答えられるかを持ちます。あわせて、gps2city がコードを返しても gomisute-gps が答えてはいけない所
(北方領土、所属未定地、竹島・尖閣諸島・硫黄島・南鳥島・沖ノ鳥島・歯舞群島) を別のファイルにまとめます。

作者: firemio ([GitHub @firemio](https://github.com/firemio))

## できるもの (out/registry/)

| ファイル | 中身 |
|---|---|
| `municipalities.json` | 1,747 行 (効力のある 1,741 自治体 + 北方領土の 6 村)。入れ子のまま。`meta` に件数と元データの sha256 |
| `municipalities.csv` | 同じ内容を 1 行 1 自治体の表にしたもの (リストは空白区切り) |
| `no_service_areas.json` | 答えない所。理由、範囲 (bbox)、代表点、N03 の陸のかたまりの一覧 |
| `islands.csv` | N03-2026 の陸のかたまり (島) のうち、主な 5 島・町字のある島・0.01km² 以上の島 (2,234 件) |
| `municipality_islands.csv` | 市区町村 (政令市は区) ごとの島の種類・範囲・町字のある島の番号 |
| `designated_islands_n03.csv` | 指定離島 (`designated_islands.csv` の行) と N03 の陸のかたまりの対応。かたまりの面積と当て方つき。当てられない島は空 |

`municipalities.json` の 1 行の項目:

| 項目 | 意味 |
|---|---|
| `lg_code` `pref` `county` `name` | 全国地方公共団体コード (6 桁、検査数字つき)・都道府県・郡・名前 |
| `kind` | 市 / 町 / 村 / 特別区 / 北方領土 |
| `wards` | 政令市の区のコード (収集の決まりが区ごとに違うことがあるので) |
| `island_class` | `all_island` (全域が主な 5 島の外) / `main+addressed_islands` (主な島 + 町字のある島) / `main+unaddressed_islets` (主な島 + 町字の無い小島) / `main_only` |
| `island_law` | 離島振興法・沖縄振興特別措置法・奄美群島振興開発特別措置法・小笠原諸島振興開発特別措置法のうち指定のあるもの |
| `inhabited_islands` | 指定された有人島の名前 (人口、N03 のかたまりの番号 `n03_comp_id` と当て方 `n03_match`、注記)。当て方は下の「指定離島と N03 のかたまり」 |
| `n03_addressed_islands` | その自治体の町字の代表点がある、主な 5 島以外のかたまりの番号 (`islands.csv` の `comp_id`) |
| `abr_parcel_coverage` | ABR の町字 (`mt_town`)・地番 (`mt_parcel`)・地番の位置 (`mt_parcel_pos`) のファイルが全部の区にあるか。町字は `mt_town_all` に行があることも見る (行の数は `mt_town_rows`。北方領土の 6 村は索引にファイルがあるが 0 行なので false)。地番と地番の位置は配布の索引にファイルがあるかだけ |
| `env_r6` | 環境省 令和6年度の総人口・計画収集人口・自家処理人口・計画収集量・直接搬入量と印 (`flags`) |
| `official_sites` | 公式サイト (Wikidata P856 をそろえたもの。lg.jp を先頭に)。`official_sites_source` は `wikidata` / `jlis` (J-LIS の URL を使った。人が見る印つき) |
| `official_sites_check` | Wikidata の項目、外した URL (`wikidata_dropped`: 荒らし・取り違え、`non_municipal_dropped`: 観光協会など)、J-LIS の URL を使ったか (`jlis_used`)、J-LIS とホストが同じか (`jlis_same_host`)、J-LIS の URL を 2 つ目の公式サイトに足したか (`jlis_fallback`。足したときだけ true)、人が見るべきか (`needs_review`)。J-LIS の URL そのものは、使ったときと 2 つ目に足したときだけ `official_sites` に入り、ほかは書かない |
| `coverage` | `answer_type`: 金沢市 (172014) と女川町 (045811) は `dates`、ほかは `none_yet`。`source`: `gomisute-gps data` か null |
| `no_service` | 北方領土の村は true |
| `notes` | 注意書き |

`env_r6.flags` の印:
- `no_public_collection_partial`: 自家処理人口が 1 人以上 (市町村の収集が来ない人がいる)
- `collection_by_drop_off_only`: 計画収集量が 0 t (持ち込みだけ。上勝町)。東京 23 区は区ごとの計画収集量が 0 で、合計行 (13100) にだけ数があるので印を付けず `notes` に書く

## 作り直し方

```
pip install pandas numpy shapely pyogrio geopandas pyproj openpyxl pdfplumber
python nationwide/fetch_registry_sources.py      # 元データを data/registry/ に取得 (あるものは飛ばす。--force で取り直し)
python nationwide/fetch_registry_sources.py --check   # 取得せずに URL が生きているかだけ確かめる
python nationwide/islands.py                     # N03 から島を作る (約 3 分、メモリ 2〜3GB)
python nationwide/build_registry.py              # 台帳と no_service_areas.json を作る (件数が合わなければ止まる)
python nationwide/check_registry.py              # できたものを確かめる (件数、公式サイト、gps2city の答えの例 15 件、指定離島の対応、ABR の印)
python nationwide/parse_ritou_pdf.py --check     # designated_islands.csv の 離島振興法 の行を国交省の PDF と比べる
```

| ファイル | 役目 |
|---|---|
| `fetch_registry_sources.py` | 元データの取得 (すべての URL をここに書いてある) |
| `islands.py` | N03 から陸のかたまりを作り、町字の代表点を置いて島の種類を決める |
| `designated_islands.csv` | 指定離島の一覧 (手で確かめてリポジトリに入れる) |
| `parse_ritou_pdf.py` | 国交省の離島振興対策実施地域一覧の PDF を島ごとの行にする (下書きと照合) |
| `build_registry.py` | 台帳と答えない所を作る |
| `check_registry.py` | できたものを確かめる |

取得は 1 ホストにつき 1 秒に 1 回まで (robots.txt に Crawl-delay があればそれに従う)、
User-Agent は `gomisute-gps (+https://github.com/firemio/gomisute-gps)`、robots.txt を守ります。
転送 (リダイレクト) は自動では追わず、転送先でも robots.txt と間隔を守って追います。別のホストへの転送は画面に出し、
記録に転送先 (`final_url`) を残します。ファイルはいったん `.part` に書いてから名前を変えるので、途中で止まっても半端なファイルは残りません。
取得の記録 (URL・大きさ・sha256・取得日時) は `data/registry/manifest.json` に残ります。
N03 の zip は約 770MB あります (中の GeoJSON だけ残します)。

## 元データと利用条件

| データ | URL | 利用条件 | 使い方 |
|---|---|---|---|
| 総務省「都道府県コード及び市区町村コード」(令和6年1月1日更新) | [一覧](https://www.soumu.go.jp/denshijiti/code.html)・[xlsx](https://www.soumu.go.jp/main_content/000925835.xlsx) | [公共データ利用規約 (第1.0版)](https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html) | 台帳の行 (1,747 コード) と政令市の区 |
| デジタル庁 アドレス・ベース・レジストリ `mt_city_all` | [zip](https://data.address-br.digital.go.jp/mt_city/mt_city_all.csv.zip) | [ABR の利用規約](https://www.digital.go.jp/policies/base_registry_address_tos) (PDL1.0) | 総務省の一覧との突き合わせ、郡名、区 → 市 |
| 同 `mt_town_all` と `mt_town_pos_prefNN` (47 ファイル) | [町字](https://data.address-br.digital.go.jp/mt_town/mt_town_all.csv.zip)・[代表点の例](https://data.address-br.digital.go.jp/mt_town_pos/pref/mt_town_pos_pref01.csv.zip) | 同上 | 町字の代表点がどの島にあるか |
| 同 配布ファイルの索引 (DCAT) | [json](https://dataset.address-br.digital.go.jp/api/feed/dcat-us/1.1.json) | 同上 | 地番・地番位置のファイルが自治体ごとにあるか (中身は使わない) |
| 国土数値情報 行政区域 N03-2026 (2026-01-01) | [説明](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html)・[zip](https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/N03-20260101_GML.zip) | オープンデータ (CC BY 4.0) | 陸のかたまり (島)、所属未定地、答えない島の範囲 |
| Wikidata P429 (全国地方公共団体コード)・P856 (公式ウェブサイト) | 項目の一覧: [QLever](https://qlever.dev/api/wikidata)、値: `https://www.wikidata.org/wiki/Special:EntityData/Q….json` | CC0 | 公式サイト |
| J-LIS「全国自治体マップ検索」 | [一覧](https://www.j-lis.go.jp/spd/map-search/cms_1069.html) | J-LIS の著作物 (中身は転載しない) | Wikidata との突き合わせ。URL は Wikidata に自治体のサイトが無い 3 自治体で使い、ホストが違う 9 自治体では 2 つ目の公式サイトに足す (どちらも人が見る印つき)。ほかの J-LIS の URL は台帳に書かない |
| 環境省 一般廃棄物処理実態調査 令和6年度「ごみ処理概要」 | [ページ](https://www.env.go.jp/recycle/waste_tech/ippan/r6/index.html)・[xlsx](https://www.env.go.jp/recycle/waste_tech/ippan/r6/data/shori/total/01.xlsx) | [公共データ利用規約 (第1.0版)](https://www.env.go.jp/mail.html) | 人口、自家処理人口、計画収集量 |
| 国交省「離島振興対策実施地域一覧」(令和8年4月1日現在) | [PDF](https://www.mlit.go.jp/kokudoseisaku/chirit/content/001886615.pdf) | [公共データ利用規約 (第1.0版)](https://www.mlit.go.jp/link.html) | `designated_islands.csv` の 離島振興法 の 260 行 |
| 国交省「日本の島嶼の構成」(令和8年4月1日現在) | [PDF](https://www.mlit.go.jp/kokudoseisaku/chirit/content/001477518.pdf) | 同上 | 法律ごとの有人島の数 (255 / 38 / 8 / 4) |
| 沖縄県「離島関係資料」(令和8年3月) 第1章 指定離島一覧 | [ページ](https://www.pref.okinawa.jp/kensei/tokei/1016451/1038825.html)・[PDF](https://www.pref.okinawa.jp/_res/projects/default_project/_page_/001/038/825/01.chapter1.r8.pdf) | 沖縄県のサイトの利用条件 (島名・面積・人口の事実だけを使う) | 沖縄の 54 島 (有人 38・無人 16) |
| 鹿児島県「奄美群島の概況」・瀬戸内町「瀬戸内町のあらまし」・国交省「奄美群島の概要」 | [PDF](https://www.pref.kagoshima.jp/ac07/documents/105247_20230327213452-1.pdf)・[瀬戸内町](https://www.town.setouchi.lg.jp/joho/cho/chosei/gaiyo/profile.html)・[国交省](https://www.mlit.go.jp/kokudoseisaku/chitok/crd_amaoga_tk_000008.html) | 各サイトの利用条件 (事実だけを使う) | 奄美の有人 8 島と市町村 |
| 国交省「小笠原諸島の概要」 | [ページ](https://www.mlit.go.jp/kokudoseisaku/chitok/crd_amaoga_tk_000009.html) | 公共データ利用規約 (第1.0版) | 小笠原の島 |

出典の表示: 「国土数値情報（行政区域データ）」（国土交通省）、アドレス・ベース・レジストリ（デジタル庁）、
都道府県コード及び市区町村コード（総務省）、一般廃棄物処理実態調査（環境省）を加工して作成。

Wikidata の SPARQL の窓口 (query.wikidata.org) は robots.txt で `/sparql` を禁じているので使いません。
どの項目がどのコードかは、Wikidata の写しを公開している QLever (University of Freiburg、robots.txt なし) に 1 回だけ問い合わせます
(問い合わせ文は `data/registry/wikidata/query.rq`)。ただし QLever の写しは古いことがあり、2026-10-18 の取得では
荒らしで航空会社の URL に書き換えられたままの自治体が約 30 あった (郡山市 → singaporeair.com など。wikidata.org では直っている)。
そのため公式サイトの値は、項目ごとに wikidata.org の `Special:EntityData/Q….json` (robots.txt で許可されている) から今の値を取ります
(1,748 件、約 35 分)。順位が「非推奨」の値は捨て、「推奨」があればそれだけ、言語の修飾子 (P407) が日本語以外のものは日本語版があれば捨てます。

公式サイトの決め方:
1. URL をそろえる (ホストを小文字、既定のポートと `#` 以降を外す、パスが空なら `/`)。http と https、`index.html` の有無だけの違いは 1 つにする (https を残す)
2. ホスト名に自治体のローマ字名 (ABR `mt_city` の `city_roma`。ヘボン式と訓令式・長音の揺れはならす) が無く、J-LIS のホストとも違う URL は外す (`wikidata_dropped`)。
   2026-10-18 には 2 件: 御浜町の `tiktocoins.info` (荒らし) と、板柳町に入っていた鶴田町の URL
3. 自治体のホストらしい URL (lg.jp、または city. / town. / vill. を含むホスト) が Wikidata に 1 つも無いときだけ、J-LIS の URL を使う。
   残った URL があるときは J-LIS が lg.jp のときだけ (lg.jp は自治体しか登録できない)。2026-10-18 には 3 件: 板柳町 (Wikidata の URL が残らない)、南会津町・白川村
4. 自治体のホストらしい URL があれば、それ以外のホスト (.org・.com など。観光協会のことが多い) の URL は外す (`non_municipal_dropped`)。
   2026-10-18 には 2 件: 南会津町の `minamiaizu.org`、白川村の `shirakawa-go.org`。
   北海道の町などは自治体のホストらしくない公式サイト (`e-rumoi.jp` など) しか無いことが多いので、ほかに無いときは外さない
5. lg.jp を先頭に、https を先に並べる
6. Wikidata と J-LIS のホストが違い、どちらも自治体のホストらしいときは、J-LIS の URL を 2 つ目の公式サイトとして足す
   (`jlis_fallback: true`。Wikidata の URL が古く、名前が引けないことがある。北本市)。ごみの収集のページ探し (`discover/`) は、
   1 つ目につながらないか収集日の資料が見つからなければ 2 つ目を見る。`--no-jlis-fallback` で前の動き (足さない)

J-LIS のページは 2020 年の更新で古い URL が多いので、決め手にはしません (2 つ目に足すだけ)。J-LIS とホストが違う自治体 (14) と、URL を外した・J-LIS を使った自治体は `needs_review` に出します。
違う自治体の一覧は `build_registry.py` を動かしたときの画面で見ます。`fetch_registry_sources.py --check` は、この 14 自治体の公式サイトの
名前が引けるか (DNS) と HEAD も見ます (robots.txt と 1 秒の間隔を守る)。
鹿角市・北本市は Wikidata の URL (`city.kazuno.akita.jp`・`city.kitamoto.saitama.jp`) が先で、J-LIS の lg.jp を 2 つ目に足しています
(北本市の Wikidata のホストは 2026-10-19 に名前が引けなかった)。どちらが今のものかは人が確かめます。

## 指定離島の一覧 (designated_islands.csv)

手で確かめてリポジトリに入れる一覧です。1 行 = 1 島 × 1 市町村 (利尻島・小豆島・種子島・奄美大島・徳之島・沖永良部島は市町村の数だけ行があり、面積と人口は島全体の値)。

| 法律 | 行 | 中身 |
|---|---|---|
| 離島振興法 | 260 | 国交省の一覧の 255 島 (111 市町村)。`parse_ritou_pdf.py` で PDF から読み、県ごとの島の数と合計 255 島で確かめた。江田島市は指定地域 (安芸群島) にあるが有人島の行が無いので、島名の空いた行で持つ |
| 沖縄振興特別措置法 | 54 | 沖縄県の一覧の有人 38・無人 16。有人か無人かは PDF の面積の列 (有人・無人) で決めた |
| 奄美群島振興開発特別措置法 | 15 | 有人 8 島 (奄美大島・加計呂麻島・請島・与路島・喜界島・徳之島・沖永良部島・与論島)、12 市町村 |
| 小笠原諸島振興開発特別措置法 | 4 | 国交省は有人島を 4 とするが島名を載せていない。父島・母島 (住民あり) と、職員だけが住む硫黄島・南鳥島 (`inhabited` = `staff_only`) と見て数えた。確かめていない |

`inhabited`: `yes` / `no` (無人の指定離島) / `staff_only` / 空 (島の行が無い)。

## 指定離島と N03 のかたまり (designated_islands_n03.csv)

`build_registry.py` が指定離島を N03 の陸のかたまり (主な 5 島以外) に当てます。確かめられない島は当てずに空にします (まちがって当てるよりよい)。
候補はその自治体のコード (政令市は区のコード) を含むかたまりだけです。

1. 町字の名前と面積 (`町字の名前と面積`): 島の名前 (末尾の「島」を除いた形も。ヶ/ケ、ノ/の/之 の有無はならす) を含む、その自治体の町字の代表点があるかたまり。
   島の面積とかたまりの面積 (全体か、その自治体の分) の比が、2 km² 以上の島は 1/1.25〜1.25、小さい島は 1/1.6〜1.6 のときだけ当てる。
   N03 でつながった島は面積の和で比べてまとめて当てる (`…まとめて`。新上五島町の日ノ島・有福島・漁生浦島)。
   面積の値の無い父島・母島は、名前がそのままの町字があるときだけ (`町字の名前 (面積の値なし)`)
2. 既知の位置 (`既知の位置`): 硫黄島・南鳥島 (`no_service_areas.json` と同じ位置)
3. 面積だけ (`面積 (近い大きさのかたまりがほかに無い)`): 面積の比が 2 km² 以上の島は 1.04 倍、小さい島は 2 倍の幅に入るかたまりが 1 つだけで、
   その比が 1 の条件の中にあり、ほかのまだ当たっていない指定離島もそのかたまりに当たり得ないとき。
   島の名前そのものの町字があるのに 1 で当たらなかった島 (今治市の来島、宇和島市の日振島など) はここでも当てない

幅の決め方: 町字の名前で当たった島では、N03 の面積と公表の面積の食い違いは 2 km² 以上の島でほぼ 3% 以内、小さい島では 10〜40% ありました
(北九州市の馬島 1.22 倍、上天草市の湯島 1.42 倍)。倉敷市の松島 (0.08 km²) は N03 で 0.05 km² しかなく、面積だけで探すと近くの別の岩に当たります。

当てていないもの (2026-10-19 の作り直しで 70 行、うち有人島 56):
- N03 で 2 つ以上のかたまりに分かれている島: 小豆島 (土渕海峡の向こうの前島が別のかたまり)、対馬 (上島と下島)
- 同じ自治体に同じくらいの大きさの島があって面積では決められない小さい島: 塩竈市の浦戸諸島、宮古島市の池間島・来間島、佐伯市の屋形島・深島など
- 名前の町字はあるが面積が合わない島: 今治市の来島 (町字「来島」の代表点は隣の小島にある)、宇和島市の日振島 (N03 2.32 km²、公表 3.74 km²)
- 湖の島 (近江八幡市の沖島)、面積の合うかたまりが無い島 (牟岐町の出羽島など)

前の当て方 (名前の部分一致と面積 ±35%) からは 50 行が変わりました。3 行は正しいかたまりに移り (日ノ島・中甑島・下地島。前は中通島・上甑島・宮古島に当たっていた)、
8 行が新しく当たり (利島・神津島・長島・小与島・中通島・宮古島・硫黄島・南鳥島)、39 行は当てないことにしました。
確かめのため、Wikidata の島の座標 (P625) と 1 回だけ比べました (台帳を作るのには使っていません)。
前の当て方では 11 行が座標と違うかたまりで、今の当て方で座標と違うのは平戸市の大島 1 行だけです。これは Wikidata の別の「大島」の項目
(座標が平戸島のかたまりの範囲に入る) を拾ったもので、当て方 (町字「大島村…」、面積も合う) は正しいものです。

## 島の分け方 (islands.py)

1. N03 のポリゴンを 1 枚ずつにし、0.000002° (約 20cm) 以内で接するものをつないで陸のかたまりにする (122,735 個)。面積の大きい順に番号 (`comp_id`) を振る。番号は同じ N03 の版の中でだけ有効
2. 本州・北海道・九州・四国・沖縄本島を主な 5 島とする (県庁の近くの点を含むかたまり)
3. ABR 町字の代表点 337,641 個を、同じ市区町村コードのポリゴンに置く (入っていない 45 点は同じコードのいちばん近いポリゴンへ。どれも 0.01° 以内)
4. 市区町村ごとに `island_class` を決める。政令市は区をまとめる

限界:
- 湖の島 (近江八幡市の沖島) は湖が市のポリゴンに含まれるので別のかたまりにならない
- 町字の代表点が本土側にある島 (牟岐町の出羽島など) は「町字のある島」にならない
- 「町字がある島」は「人が住む島」ではない (十島村の臥蛇島、根室市の志発島など)。埋め立ての人工島 (東京湾・大阪湾・神戸) も入る
- ABR の町字の代表点 (`mt_town_pos`) のうち 128,311 点は、町字 ID が町字マスター (`mt_town_all`) に無く、名前が引けない
  (ほとんどが先頭 0000 の小字の ID)。島の判定には効かないが、`islands.csv` の町字の名前は一部だけになり、指定離島とかたまりの対応づけ (`n03_comp_id`) は
  名前で付かない島が多い (上の「指定離島と N03 のかたまり」)

## 答えない所 (no_service_areas.json)

gps2city (N03-2026) は、人の住まない島や日本の行政が及ばない所にも市区町村コードを返します。gomisute-gps はここでは答えません。

| 種類 | 所 | 見分け方 |
|---|---|---|
| `northern_territory` | 北方領土の 6 村 (016951 色丹村・016969 泊村・016977 留夜別村・016985 留別村・016993 紗那村・017001 蘂取村) | gps2city の lg_code |
| `unassigned_land` | 所属未定地 7 か所 (N03 コード 12000・13000・23000・30000・40000・46000・47000) | gps2city は県のコード (例: 130001) を返す |
| `unadministered_island` | 竹島 (隠岐の島町)、歯舞群島 (根室市: 志発島・水晶島・多楽島・勇留島・秋勇留島・貝殻島など) | その島の bbox |
| `uninhabited_island` | 尖閣諸島 (石垣市: 魚釣島・久場島・大正島・北小島・南小島など)、沖ノ鳥島 (小笠原村) | その島の bbox |
| `no_resident_island` | 硫黄島・南鳥島 (小笠原村。職員だけ) | その島の bbox |

使い方: 北方領土と所属未定地は、gps2city の `lg_code` が `gps2city_lg_code` と同じなら場所によらず答えません
(所属未定地では gps2city は県のコード、例えば 130001 を返す)。島のグループは、gps2city の `lg_code` がグループの `lg_code` と同じで、
点がグループの `bbox` に入れば答えません。グループの bbox に、同じ自治体の町字のある別の島が入らないことは `check_registry.py` で確かめています。

島のグループは、N03 の陸のかたまりごとに `bbox` と `rep_point` (かたまりの内側の点) を `components` に持ちます。
0.001km² 未満で町字も無い岩は `components` に 1 つずつは書きません (グループの bbox には入ります。数は `small_components_not_listed`)。
島の名前は ABR 町字の名前 (歯舞群島・沖ノ鳥島)、無ければ既知の位置にいちばん近い 0.01km² 以上のかたまりに付けます (`name_source`)。
名前の無いかたまりは岩礁です。

## 数の確かめ

`build_registry.py` は次が合わないと止まります: コード 1,747 (北方領土 6)、市 792・町 743・村 183・特別区 23、政令市 20・区 171、
所属未定地 7 コード。調査の下書き (N03-2024) との違いは下の表のとおりです。

| island_class (1,741 自治体) | N03-2024 の下書き | N03-2026 |
|---|---|---|
| all_island | 72 | 72 |
| main+addressed_islands | 136 | 136 |
| main+unaddressed_islets | 353 | 351 |
| main_only | 1,180 | 1,182 |

変わった 2 市:
- 袖ケ浦市: N03-2024 では沖 (北緯 35.467°・東経 139.998°) に 6〜7m² のポリゴンが 2 枚あったが、N03-2026 では無くなった
- 碧南市: N03-2024 で碧南市だった 0.48km² の島 (北緯 34.824°・東経 136.949°) が、N03-2026 では愛知県の所属未定地 (23000) になった。
  このため愛知県の所属未定地は 2.57km² から 3.08km² に増えた

台帳のほかの数 (2026-10-18 の取得):

| 項目 | 数 |
|---|---|
| 離島の法律の指定のある自治体 | 142 (離島振興法 111・沖縄 18・奄美 12・小笠原 1)。指定の有人島のある自治体 141 (江田島市は有人島の行が無い) |
| 指定のある自治体の island_class | all_island 69、main+addressed_islands 69、main+unaddressed_islets 3 (牟岐町・串間市・出水市)、main_only 1 (近江八幡市: 湖の島) |
| 全域が島だが指定の無い自治体 | 3 (洲本市・淡路市・苓北町) |
| 指定離島と N03 のかたまりの対応 (332 行) | 町字の名前と面積 177、つながった島をまとめて 3、面積だけ 78、面積の値なし 2、既知の位置 2、当てない 70 (有人島では 56) |
| 環境省 R6 の印 | `no_public_collection_partial` 32 自治体、`collection_by_drop_off_only` 1 (上勝町) |
| 公式サイト | 1,741 自治体すべてにある (Wikidata 1,738、J-LIS 3)。URL 1,750 (J-LIS の URL を 2 つ目に足した 9)。先頭が lg.jp 724。J-LIS とホストが同じ 1,727、違う 14。外した URL: 荒らし・取り違え 2、観光協会など 2。人が見るべき 15 |
| ABR のファイルが全部の区にある | 町字 1,741 (北方領土の 6 村は索引にファイルがあるが 0 行なので数えない)、地番 1,736、地番の位置 1,705 |
| 答えない所 | 19 (北方領土 6、所属未定地 7、竹島・歯舞群島・尖閣諸島・沖ノ鳥島・硫黄島・南鳥島) |

`check_registry.py` の結果: 件数・政令市・日付で答えられる 2 自治体・CSV と JSON の行がすべて一致。公式サイトの無い自治体 0。
gps2city の答えの例 15 件 (答えない 9・答える 6) がすべて期待どおり。島のグループの bbox に、同じ自治体の町字のある別の島は入らない。
指定離島の対応はどれも面積が合い (つながった島は和で)、同じかたまりに 2 つ以上当たるのは「まとめて」の 1 か所だけ。
J-LIS の URL は使った 3 自治体と 2 つ目に足した 9 自治体 (どちらも人が見る印つき) にだけあり、自治体らしいホストと観光協会らしいホストが混ざる自治体は無い。
北方領土の 6 村の ABR の印はどれも false。
