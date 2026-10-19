# ごみ収集のページ探し (nationwide/discover/)

全国の自治体台帳 (`out/registry/municipalities.json`) の公式サイトから、家庭ごみの収集のページと資料を探して、
どの形式で公開されているかを分けます。検索エンジンは使いません。公式サイトの中だけをたどります。

作者: firemio ([GitHub @firemio](https://github.com/firemio))

探すもの:

| 種類 (`kinds`) | 中身 |
|---|---|
| `calendar` | 収集日カレンダー・収集日程・曜日の表 |
| `districts` | 収集区域・地区 (町名) ごとの収集日の一覧 |
| `how_to` | ごみの出し方・分け方 |
| `year_end` | 年末年始の収集 |
| `change_notice` | 収集日の変更のお知らせ |
| `app` | アプリの案内 (5374・さんあ～る・ごみスケなど) |

自治体ごとの形式 (`format_class`):

| 形式 | 意味 |
|---|---|
| `html_table` | 収集日が HTML の表 (曜日や日付の入った表) |
| `html_text` | 収集日が HTML の文 (表ではない) |
| `html_unchecked` | HTML だが中を見ていない (5MB を超える。リンクの文字だけで候補にした) |
| `pdf_text` | 文字の入った PDF |
| `pdf_image` | 文字の無い PDF (画像・輪郭線)、または文字が壊れていて読めない PDF |
| `pdf_unchecked` | PDF だが中を見ていない・読めない (50MB を超える、上限に届いて取らなかった、0 バイト、壊れている、など) |
| `xlsx/csv` | Excel・CSV |
| `ics` | iCalendar |
| `app_only` | 収集日の資料は見つからず、アプリへのリンクだけある |
| `blocked` | 403・429・ボット対策の確認ページ、または robots.txt で禁止されていて入れない (資料 1 件でも、robots.txt で禁止・ブロックで取らなかったものはこの形式) |
| `unreachable` | つながらない・時間切れ・トップが 404 など (表にない種類。人が確かめる。`--resume` でやり直す) |
| `not_found` | サイトには入れたが、収集日の資料が見つからない |

いちばんの候補 (`best`) の形式を自治体の形式にします。`best` は中を見た候補から先に選びます (中を見ていない資料は、中を見た候補が無いときだけ)。
見つかった形式はすべて `formats_seen` に並べます (例: HTML の表の案内ページから、地区ごとの PDF にリンクしている)。

## 使い方 (パイロット)

```
pip install pymupdf openpyxl            # PDF と Excel を見るため (無ければ「見ていない」と記録して続ける)
python -m unittest nationwide/discover/test_discover.py      # ネットにつながないテスト (74 件、約 40 秒。pytest でも走る)

# 1. 小さく試す (LLM なし)。石川県の 10 自治体。取得は 1,000 回くらい、10 分くらい
python nationwide/discover/discover.py --pref 17 --limit 10 --no-llm

# 2. 全国から無作為に 50 自治体 (--seed で同じ選び方をやり直せる)
python nationwide/discover/discover.py --sample 50 --no-llm

# 3. あとから LLM の判定を足す (ネットには出ずキャッシュだけで探し直す。キーは環境変数で)
#    GOMISUTE_LLM_API_KEY を設定してから
python nationwide/discover/discover.py --sample 50 --offline --resume

# 4. 全国 (途中で止めても --resume で続きから)
python nationwide/discover/discover.py --no-llm --resume

# 5. 探し方を変えたあと、キャッシュだけで探し直して比べる (ネットに出ない。out/discover/ の結果は変えない)
python nationwide/discover/discover.py --codes 045811,172014 --replay --no-llm      # → out/discover/replay/ (git に入れない)
python nationwide/discover/eval_jgd.py --results out/discover/replay --offline       # 探し直しの結果を triage.csv と比べる
```

ほかの選び方: `--pref 17,04` (都道府県コードか名前)、`--codes 172014,045811` (自治体コード)、`--limit N` (コード順の始めの N)。
探し方の細かい値: `--depth 3`、`--budget 150`、`--max-docs 25`、`--after-strong 60`、`--hub-budget 40`、`--related-budget 15`、
`--site-minutes 30`、`--no-site-search`、`--judge-top 5`。
礼儀の値: `--gap 1`、`--ip-gap 0.25`、`--max-crawl-delay 60`、`--max-concurrency 16`、`--workers 32`、`--timeout 30`、`--retries 2`、
`--max-age-hours 72`。`--max-requests N` を付けると、その回数で止めます (途中の自治体は書きません)。

Ctrl+C で止めると、終わった自治体だけを書き、`summary.csv` と `run_log.json` を書いてから終わります (待っている間も止まります)。

`--resume` で飛ばすのは「済んだ」結果だけです:

- `complete` で、探し方の値 (`params`) と `VERSION` が今回と同じ (値を変えたらやり直す)
- `unreachable` でない (こちらのネットや DNS が一時落ちただけかもしれないので、やり直す。`--no-retry-unreachable` で飛ばす。`blocked` はやり直さない)
- LLM を使う実行なら、LLM の判定が済んでいて失敗が無い

`--offline` でキャッシュに無いものがあった結果は `complete` にしません (`offline_misses` に数)。前の結果があればそれを残し、
サイトに入れなかったものは書きません。

`--replay` は `--offline` で探し直し、`--out` (既定 `out/discover/replay/`、git に入れない) に書きます。キャッシュに無いものがあっても書きます
(`complete` は false、`offline_misses` に数。トップに入れたあとでブロックされたサイトも書く)。探し方を変えたとき、パイロットの結果と比べるためのものです。
探し方が変わると、前に取っていない URL (案内のページの子・深いリンク・組合のサイトなど) を取りに行こうとして、キャッシュに無いと数えます
(キャッシュに無いものも、取った数 (上限) には数える。ネットで取るときと同じ数え方)。
結果には `"replay": true` を付けます。`out/discover/` (本番の結果) には書かず (止める)、ほかの所でも `replay` の印の無い結果は上書きしません。

## 礼儀 (fetcher.py)

すべての取得は 1 つの取得係 (`Fetcher`) を通ります。

- **User-Agent**: `gomisute-gps-discover (+https://github.com/firemio/gomisute-gps)`
- **robots.txt を守る**: ホストごとに 1 回だけ取って覚えます (キャッシュは 24 時間)。取ってよいかは RFC 9309 の読み方
  (いちばん長く一致する規則、`*` と `$`) で決めます。`urllib.robotparser` でも読み、`Crawl-delay`・`Request-rate`・`Sitemap` に使います。
  robotparser は始めに当たった規則で決めるので、「`Disallow: /`」のあとの「`Allow: /kurashi/`」を禁止と読みますが、
  そこは RFC に従って取ります (その数は `run_log.json` の `robots_rfc_only`)。
  自分に当たるグループ (名前が `gomisute-gps-discover` の一部。例: `gomisute-gps`) があればそれに、無ければ `*` に従います。
  robots.txt が 404 なら規則なし、401 なら全部禁止、403・429 ならそのホストはブロック、5xx・つながらないなら全部禁止 (RFC 9309) とみなします
- **間隔**: 1 ホストに 1 秒に 1 回まで。前の取得が**終わってから** 1 秒空けます。robots.txt の `Crawl-delay`・`Request-rate` が長ければそれに従います (小数も)。
  `Crawl-delay` が 60 秒 (`--max-crawl-delay`) を超えるホストは、守りきれないので取りません (robots.txt で禁止と同じ扱い)
- **同じ IP (共用のサーバー)**: いくつもの自治体が同じサーバーにあるときのため、同じ IP アドレスへは取り始めを 0.25 秒 (`--ip-gap`) 空けます
  (1 IP に 1 秒 4 回まで)。CDN では別のサイトが同じ IP になることもあり、そのときは少し遅くなるだけです
- **同時接続**: 全体で 16 まで (`--max-concurrency`)。1 ホストには同時に 1 つだけ。同じ URL を 2 つのスレッドが同時に取ることはしません
  (あとのものは先のもののキャッシュを使う。一部事務組合の資料を構成町村が同時にリンクしているときなど)
- **転送 (リダイレクト)**: 自分で 1 回ずつ追い (5 回まで)、転送先のホストでも robots.txt と間隔を守ります
- **ボット対策は回避しない**: 403・429、または確認ページ (Cloudflare の「Just a moment...」、Incapsula、AWS WAF など) が返ったら、
  そのホストを「ブロック」と記録して、その実行ではもう取りに行きません。キャッシュにも残るので、やり直しても 72 時間はたたきません。
  Cloudflare・Incapsula はふつうのページにもスクリプト (`/cdn-cgi/challenge-platform/…/jsd/main.js` など) を差し込むので、
  これらの弱い印は 200 以外の応答 (403・503・202 など) のときだけ見ます
- **キャッシュ**: `data/discover/cache/` に条件つき GET (ETag・Last-Modified) のキャッシュを置きます。取ってから 72 時間のうちは取りに行かず、
  それより古ければ条件つき GET (変わっていなければ 304 で中身は送られない) にします。HTML などの文字は gzip で縮めて置きます
- **取り直し**: 時間切れ・5xx は 5 秒・10 秒と間を空けて 2 回まで取り直します。`Retry-After` はその URL だけでなく**ホスト全体**に守ります
  (120 秒を超える `Retry-After` は待たず、その実行ではそのホストを取りません)。503 の確認ページは取り直しません。
  つながらない・5xx の URL が 3 つ続いたホスト、証明書の誤りのホストはあきらめます (5xx を返し続けるサーバーには、robots.txt を入れて 10 回まで)
- **こちらのネットが落ちたとき**: 成功をはさまずに 20 のホストで続けてつながらなければ、こちらのネット (や DNS) が落ちたとみなして止めます。
  そのとき `unreachable` になった自治体は書きません (`--resume` で続きから)
- **大きさの上限**: HTML 5MB・PDF 50MB・ほか 20MB。`Content-Length` で分かれば本文を読まずに、大きさだけ記録します。
  gzip のサイトマップも広げるのは 20MB まで
- **業者には行かない**: 5374・さんあ～る (delight-system)・ごみスケ・スマートごみアプリ・アプリストア・LINE などへのリンクは記録だけします
- **ほかのサイト**: 一部事務組合・広域連合・都道府県などの公の団体のサイトは、自分のサイトを探し終えてから、別の上限
  (`--related-budget 15`) の内で、同じ礼儀 (robots.txt・間隔・キャッシュ) で取ります (下の「探し方」の 10)。業者・アプリのホストへは行きません
- **手元の場所を書かない**: PDF・Excel はキャッシュのファイルを bytes で読んでから開きます (PyMuPDF の誤りの文にはファイルの場所が入るため)。
  誤りの文は手元の場所を消してから記録し、`out/discover/` に書く前にも念のためすべての文字から消します

## 探し方 (crawler.py)

1. 公式サイトのトップを取る。転送や `meta refresh` で別のホストへ移っていれば、そのホストもサイトの内とする。
   公式サイトの URL のパスが古くて 404 なら、ホストの `/` から始める。
   つながらない (名前が引けない・つながらない・時間切れ) ときは、`www` の有る無しと https・http を入れ替えて 2 つまで試す
   (名前が引けないときは `www` だけ)。試した URL は `sites[].start_attempts`、つながった URL は `start_used` と結果の `start_url`。
   台帳に公式サイトが 2 つ以上あれば (Wikidata と J-LIS のホストが違う 9 自治体。北本市など)、前のサイトにつながらないか収集日の資料が
   見つからなければ次のサイトを見る (転送で前のサイトの内に入るものは `same_site` として探さない)。
   トップが障害の臨時ページ (題名・説明・見出しに「臨時案内」「システム障害」「閲覧できません」など、サイトの内へのリンクが 10 未満。
   小平市) なら、`unreachable` とする (`blocked.where` は `top`。`--resume` でやり直す)。
   サイトの内 = 同じホストか、先頭の `www`・`www4` などを外した名前の下のホスト (`www4.city.kanazawa.lg.jp` → `city.kanazawa.lg.jp` の下)
2. robots.txt の `Sitemap:`、無ければ `/sitemap.xml` を読み、URL にごみの言葉があるものを候補に入れる。
   サイトマップの索引は、ごみの言葉がある子だけ (5 つまで) 読む (子が 5 つ以下なら全部)
3. サイト内検索が素直な GET のフォームなら「ごみ 収集日」で 1 回だけ検索する (Google のサイト内検索は使わない)
4. リンクを手がかりの強い順にたどる (深さ 3 まで、1 サイト 150 件まで)。手がかり:
   リンクの文字 (ごみ・ゴミ・塵芥・分別・リサイクル・清掃・粗大 > 収集・資源・カレンダー > 環境・くらし・生活)、
   URL (gomi・recycle・bunbetsu など > calendar・shushu・kankyo など > kurashi・seikatsu・life)、
   リンク元がごみのページか、「サイトマップ」のリンクか。入札・議会・求人などの言葉は下げる。
   リンクの文字が URL だけのとき (「https://…/schedule/hasuda/（外部サイトへリンク）」) は URL の言葉 (schedule・nittei なども) で数える。
   手がかりの無いリンクはトップからのもの (深さ 1) だけを、最後に取る。
   同じページに道が 2 つ以上あれば、知っているいちばん浅い深さで数える (サイトマップの URL は深さ 1)。
   **深いリンク**: 手がかりのとても強いリンク (点 5 以上、または収集日・カレンダー・日程表・収集計画表・地区の言葉で点 4 以上) は、
   深さ 5 まで (上限の 2 つ先) たどる (東松山市: 深さ 4 の「分別収集計画表」→ 深さ 5 の地区ごとの PDF。上尾市・吉見町も同じ形)。
   収集日・カレンダーの言葉の無いもの (「燃やすごみの出し方」など) は 1 段ごとに点を 2 下げ、収集日の資料が見つかったあとは取らない。
   深さの上限を超えて取るページは 1 サイト 30 まで (`sites[].deep_fetched`。150 件の内)
5. PDF・Excel・CSV・iCalendar は、ごみの言葉があるか、ごみのページからのリンクなら取る (深さ 5 までのページの資料。1 サイト 25 件まで)
6. 収集日の資料らしいもの (点数 60 以上の calendar・districts) が見つかったら、手がかりの薄いリンクは追わず、そこから 60 件で止める
   (下の「案内のページの子」を取り終えてから数える。案内のページの子は 60 件にも 150 件にも数えない)
7. **案内のページ (hub)**: ごみのページ (題名などに ごみ・分別・燃やせる などの言葉があるか、種類がある) で、ごみのページ・資料への
   リンクが 4 つ以上 (または 収集日・カレンダー・地区 の言葉のリンクが 2 つ以上) あり、自分には収集日の中身 (曜日・日付の表、
   曜日と日付が合わせて 8 以上) が無い HTML を `role: hub` とする (例: 女川町の「環境・ごみ」、金沢市の「収集日(地区別収集カレンダー)」)。
   収集日・カレンダー・地区の言葉のある案内のページは、その子を**ほかより先に**、別の上限 (`--hub-budget 40`、資料は 15 まで) で取る。
   深さの上限 (3) を 2 つまで超えてよい (金沢市: 深さ 3 の案内のページ → 深さ 4 の町名と校下の表 → 深さ 5 の地区ごとのカレンダー)。
   広げる子:
   - 自分が収集日・地区のページ (`own`): 収集日・カレンダー・地区の言葉のリンク、地区の名前だけ・言葉の無い資料
     (「東地区（PDF）」)、同じフォルダ (2 段以上の深さ) の地区らしいページ (12 まで。平らなサイトのメニューは広げない)
   - そうでなく、収集日・カレンダー・地区の言葉のリンクだけがある (`links`): その言葉のリンクだけ
   - 申請書・入札・イベント・「町長挨拶」などは広げない。子も案内のページなら、その子も広げる (上限の内)
   - 問い合わせの形 (`kana.html?kana=あ` など) ごとに 3 つまで (行田市・吉見町: 五十音の索引 44 ページで上限 40 を使い切っていた)。
     上限に届いたら子を増やさない (列も 300 まで)。資料の上限 (15) を超えた子の資料は、ふつうの列 (`--max-docs` の内) に渡す
   - 子の深さは、知っているいちばん浅い道の深さにする。案内のページから深く取ったページでも、ふつうの道の方が浅ければ、
     そこからのリンクと資料をふつうの列に入れる (岩沼市: カレンダーのページを案内のページの子として深さ 4 で取ると、地区の PDF を取り残していた)
8. 問い合わせ (`?…`) つきの URL は「形」(ホスト・パス・問い合わせの名前の組) ごとに数える。
   日付で切り替わるもの (`?ym=202610`・`?year=2026&month=10` など、イベントカレンダーのわな) は同じ形で 3 つまで (ごみの手がかりのあるものは 14 まで)。
   ほかの形 (WordPress の `/?page_id=5`・`/?p=123`、`hpkiji/pub/detail.aspx?c_id=10&id=77`、`dd.aspx?menuid=…` など、
   問い合わせでページを分ける CMS) は、ごみの手がかりのあるリンクは数えずに通し、無いリンクだけ同じ形で 60 まで。
   ただし五十音の索引 (リンクの文字が「あ」「か行」だけ) は同じ形で 3 つまで (入間市: 2 つの索引で 90 ページ)。
   `<meta name="robots" content="nofollow">` のページのリンクはたどらない
9. 収集日のページ (calendar・districts の候補) には、そこからリンクしている資料の一覧 (`documents`、80 件まで。リンクの文字・形式つき) を付ける。
   その資料には `features.linked_from_schedule` を付けて少し加点し、リンクの文字が地区の名前だけ (「東地区（PDF：1.2MB）」) のような資料には
   リンク元の種類を渡す (`features.kinds_from_parent`)。地区ごとの画像の PDF (隠岐の島町のような形) が候補から落ちないようにするため。
   申請書・様式・計画などは受け継がない
10. **関係する発行元 (related)**: ほかのホストへのリンクで、相手が公の団体 (ホスト名に kumiai・kouiki・eisei・seiso・union など、
   市町村でない lg.jp (一部事務組合・広域連合)、都道府県とその部局、またはリンクの文字が「〇〇衛生組合」「〇〇広域行政事務組合」
   「〇〇クリーンセンター」など) で、リンクの文字か URL にごみの言葉があるもの (点 3 以上、ごみのページからなら点 2 以上) は、
   自分のサイトを探し終えてから、別の上限 (`--related-budget 15`、資料も含む。150 件にも 25 件にも数えない) で取る。
   収集日・カレンダーの言葉のリンクを先に (お知らせの記事より先)。組合などは入口から 3 段まで、2 段目からは同じホストの
   とても強いリンクだけをたどる (秩父広域市町村圏組合: 「家庭のごみ」→「ごみカレンダー」)。都道府県・ほかの市町村は入口と資料だけ。
   会社らしいホスト (.co.jp・.com など)、協同組合・協会・連合会、業者・アプリのホストは入れない。
   そのほかの .jp のサイトは、前と同じく文字がはっきりごみのもの (点 4 以上) だけ 1 ページ取り、そこから資料だけを見る (同じ上限の内)。
   記録: 結果の `related` (ホスト・種類・入口の URL・リンクの文字・リンク元・取った数)、候補の `related_publisher`
   (蓮田市・白岡市: 蓮田白岡衛生組合のサイトの収集日程表)。組合のサイトのページで、題名・見出しにほかの市町村の名前だけがある
   (「収集日程（白岡市）」を蓮田市で見たとき) ものは `features.other_muni` を付けて点数を 25 下げる
11. 業者のホスト (ごみスケなど) から読み込むスクリプト (`<script src>`) も、埋め込みのアプリとして記録する (さいたま市)。取りに行かない
12. ページの `<link rel="canonical">` (無ければ `og:url`) が別の URL なら、候補の `canonical` に残す (比べるとき同じページとみなす)

## 点数と形式 (classify.py)

ページ・資料ごとに、次の特徴から 0〜100 の点数を付けます (コードだけ。LLM は使わない)。

| 特徴 | 中身 |
|---|---|
| 種類 | 題名・見出し・リンクの文字・URL・PDF の始めの文字から (calendar 30、districts 22、how_to 10 など)。「収集表」「ごみの日」「資源回収日」「収集計画表」なども calendar |
| `linked_from_schedule` | 収集日のページからリンクされた資料 (加点 6。地区の名前だけの資料はリンク元の種類を受け継ぐ) |
| `gomi_label` | 題名・見出し・リンクの文字のごみの言葉の数 |
| `gomi_words` | 本文のごみの言葉 (燃やせる・不燃・資源・びん・缶など) の数 |
| `schedule_tables` | 曜日 (「月・木」「第1・3水」など) や日付の入った表の数 (HTML) |
| `weekday_tokens` `date_tokens` `dated_tokens` | 本文の曜日・日付の数と、年のついた日付 (「2026年9月2日更新」) の数。案内のページかを決める「収集日の中身」には、年のついた日付 (新着情報の並び) を数えない (上尾市の「ごみ・衛生・動物」) |
| `district_tokens` | 地区・町名らしい言葉 (○○町・○丁目・○○地区・自治会・行政区・集落) の数 |
| `fiscal_years` `label_years` | 令和8年度・2026年度・R8 などの年。今年度の 6 年前から 2 年先までだけを、今年度・来年度に近い順に 5 つまで (町民カレンダーの年表・計画の沿革の昔の年で今年度が押し出されないように)。PDF は始めの 3 ページと、ごみの言葉の多いページ (6 つまで) から。`label_years` は題名・見出し・リンクの文字の年度で、並べ方の「新しい年度」は `label_years` を先に見る。どちらかに今年度・来年度があれば加点 |
| `pdf_kind` | `text` / `image` / `vector` (輪郭線) / `broken_text` (文字化け) / `empty` / `unreadable` (0 バイト・壊れている・パスワード) / `not_pdf` (PyMuPDF、無ければ pdfplumber)。文字があるかは始めの 3 ページで決める (3 ページに無くても、うしろに文字があり、画像だけのページが半分より少なければ `text`) |
| `page_hits` | PDF の**全ページ** (80 ページ・30 万字まで。上限で止めたときの `pages_read` は文字を読んだページの数) のうち、ごみの収集のページの番号 (1 から)。ページごとに 燃やせる・燃やす・可燃・資源・収集日・収集・地区・行政区・曜日 と月の見出し (「4 月」「April」) と日付の並びを数え、燃やせる・可燃・資源などが 2 つ以上と、収集・地区・曜日か月の見出しか日付の並びがあるページ |
| `calendar_pages` `page_months` `gomi_months` `multi_page_calendar` | カレンダーらしいページの数 (月の見出しが 2 つ以上か日付の並び (曜日の見出しと 1〜31 の数) のある `page_hits` のページと、その隣の日付の面)、そこに出る月の数、ごみの言葉のあるページに出る月の数、何ページにもわたるカレンダー。町民カレンダー (表紙は町の行事、中のページにごみの表) を calendar にする (何ページにもわたるか、月が 2 つ以上か、ページの半分以上のとき。広報の 1 ページだけの行事カレンダーは入れない)。8 ページを超える資料は、カレンダーのページが 2 つ以上で、ごみの言葉のあるページに月が 2 つ以上出るときだけ (処理計画・清掃事業の冊子の中の 1 ページの月別の表 (気仙沼市・杉並区) を入れない)。加点は 2 × ページ数 (10 まで) + 6。表紙の言葉 (「教育委員会」「事業系」など) で下げないのは、何ページにもわたるカレンダーか、8 ページまでのカレンダーだけ (長い冊子は表紙の言葉で下げる) |
| `image_only_pages` `image_only_first` | 文字がほとんど無く画像だけのページの数と、始めのページ番号 (LLM の判定に縮小画像を添えるだけ) |
| `role` `hub_links` `hub_docs` `hub_cal_links` `hub_place_links` | HTML の役目 (`hub` = 案内のページ、`schedule` = 収集日の資料) と、そのもとになるリンクの数 (ごみのページ・ごみの資料・収集日/カレンダー/地区の言葉・地区の名前らしいもの)。hub も候補の並べ方は変えない (パイロットでは、地区の説明のある案内のページを LLM が収集日の資料と答えたものが多い) |
| `negative` | 入札・議会・計画・統計などの言葉 (題名・見出し・PDF の始め。減点) |
| `other_muni` | 関係する発行元 (組合など) のページで、題名・見出しに出るほかの市町村の名前 (自分の名前が無いとき。減点 25) |

点数 35 以上の calendar・districts を「収集日の資料」とみなし、その中でいちばんのものを `best` にします
(LLM の判定があれば、「収集日の資料」と答えたものを先にします。「違う」と答えたものは `best` にしませんが、候補の並べ方では
点数を 25 下げるだけにします。LLM の見落とし (ふじみ野市の地区の PDF) で候補の一覧から落ちないように)。
曜日・日付・地区らしい言葉などの本文の数は、PDF では始めの 3 ページとカレンダーらしいページから数えます
(分け方の冊子の全ページで数えると多くなりすぎるため)。

## LLM の判定 (judge.py、使わないときは --no-llm)

候補 5 つ (`--judge-top`) について、短い抜粋だけを [`nationwide/readers/llm.py`](../readers/llm.py) で LLM に渡します。
モデルは `GOMISUTE_LLM_MODEL`、キーは環境変数 `GOMISUTE_LLM_API_KEY` (リポジトリには書かない)。

- 選び方: 次の 3 つは必ず入れる (点数 10 以上。`--judge-top` を超えても入れる)
  1. ごみの収集のページ (`page_hits`) のある PDF のいちばん
  2. 収集日らしい表のある HTML のいちばん
  3. URL かリンクの文字に カレンダー・収集・ごみ のある資料 (HTML 以外) のいちばん (1・2 と別のもの)

  残りは「点数 + 中身の手がかり (表 +10、PDF のカレンダーのページ +10、何ページものカレンダー +5、カレンダー/収集/ごみ の資料 +5)
  − 案内のページ 10」の順に、点数 20 以上のものを `--judge-top` まで足す
- 渡すもの: 題名・見出し・収集日らしい表の中身 (1 行ずつ、800 字まで)・本文の始め (約 2,000 字)、PDF なら 1 ページ目の文字、
  文字の無い PDF なら 1 ページ目の画像。本文は `<main>` (や `id="contents"`・`id="tmp_contents"` など) から、無ければ題名に含まれる
  ページ名の `h1` から始めます (メニューの多い CMS で、ヘッダーやメニューの文字で 2,000 字を使い切らないため)
- ごみの収集のページ (`page_hits`) のある PDF は、ページの一覧・1 ページ目の始め 400 字・言葉の多いページ 4 つの 700 字ずつ
  (ページの中のごみの言葉の少し前から) を渡します (表紙だけでは町民カレンダーと分からないため)
- 文字のある PDF に画像だけのページがあり、ごみの収集のページが無ければ、その始めのページの縮小画像 (長い辺 800px) を添えます。
  画像は判定に添えるだけで、ファイルには残しません
- 答え: `{is_collection_schedule, covers_whole_municipality, fiscal_year, districts_named, notes}`
- 判定は `data/discover/judge/` に覚え、同じモデル・同じ中身ではもう聞きません。失敗した判定は `--resume` でやり直します。
  失敗の記録は誤りの種類と HTTP の状態だけです (本文やキーの切れ端は書かない)
- 抜粋の中の文字は指示として扱わないように、system の指示に書いています
- 渡すものの無い候補 (0 バイトの PDF など) は聞きません

## できるもの

| ファイル | 中身 |
|---|---|
| `out/discover/<lg_code>.json` | 1 自治体。`format_class`・`formats_seen`・`start_url` (つながったトップ)・`best`・`candidates` (並べ方の順に 60 まで。収集日の資料らしい中を見た候補と `best` の資料は 120 まで数えずに残し、索引らしい同じ問い合わせの形 (五十音・検索・ページ送り・分類・日付。`kana.html?kana=あ` など。CMS のページの番号 `?content=123` は別) のページは 3 つにまとめて `candidates_merged` に数)・`apps`・`related` (関係する発行元)・`sites` (サイトごとの状態・止まったわけ・試したトップ `start_attempts`・トップが転送・meta refresh で移った先 `moved_to` (台帳の URL が古いかもしれない印)・サイトマップ・ブロックされた URL とどこで分かったか `blocked.where` (`robots` / `sitemap` / `top` / `page`)・深さの上限を超えて取った数 `deep_fetched`・案内のページの子の数 `hub_fetched`・`hub_docs`・`hub_left`・子を広げた案内のページ `hubs`・ほかのサイトで取った数 `offsite`・`related_left`)・`requests`・`params`・`complete` (`--offline` でキャッシュに無いものがあれば false と `offline_misses`)・`replay` (`--replay` の結果だけ) |
| `out/discover/summary.csv` | 1 行 1 自治体 (`out/discover/` の JSON から毎回作り直す) |
| `out/discover/replay/` | `--replay` の結果 (同じ形。パイロットの結果と比べるため。git に入れない) |
| `out/discover/eval_jgd.csv` `eval_jgd.md` | japan-gomi-data の triage.csv との比べ (下の「japan-gomi-data との比べ」) |
| `data/discover/eval/` | triage.csv のキャッシュ (git に入れない) |
| `out/discover/run_log.json` | 実行ごとの記録 (引数、かかった時間、形式ごとの数、書かなかった数、止めたわけ (`halt_reason`)、**ホストごとの取得の数**・状態コード・キャッシュ・304・待った秒数・robots.txt で取らなかった数・止めたわけ、LLM のトークン) |
| `data/discover/cache/` | 取得のキャッシュ (git に入れない) |
| `data/discover/judge/` | LLM の判定 (git に入れない) |
| `data/discover/logs/requests_<日時>.jsonl` | 1 回ごとの取得 (URL・状態・時間・大きさ) (git に入れない) |

候補 1 件の項目: `url`・`requested_url` (転送前)・`file_type`・`format_class`・`kinds`・`score`・`features`・`title`・`headings`・
`anchors` (このページへのリンクの文字)・`depth`・`found_from`・`fetched`・`not_fetched` (取らなかったわけ: `too_large`・`max_docs`・
`not_reached`・`robots_disallowed`・`blocked` など)・`offsite`・`http` (状態・大きさ・sha256・Last-Modified・取った日時)・`judge`・
`documents` (収集日のページだけ: リンクしている資料の一覧)・`role` (HTML の `hub` / `schedule`)・`canonical` (HTML の canonical が別の URL のとき)・
`related_publisher` (関係する発行元のサイトのもの)。
`not_fetched` には、案内のページの子で上限に届いて取らなかった資料の `hub_budget`・`hub_max_docs` もある。
候補に残すのは、点数 10 以上か、種類があるか、収集日のページからリンクされた資料です。

`summary.csv` の列: `lg_code, pref, name, format_class, formats_seen, best_url, best_score, best_kinds, best_judge, best_fiscal_year,
schedule_candidates, candidates, apps, related, site_status, start_url, stop_reason, pages, hub_pages, related_pages, requests, blocked_url,
blocked_where, llm, finished_at` (`hub_pages` は案内のページの子として、`related_pages` はほかのサイトで取った数。どちらも `pages` の内。
`site_status`・`stop_reason`・`blocked_url` は、つながったサイト (無ければ始めのサイト) のもの)。

やり直しても同じになります: 同じキャッシュからは同じ候補・同じ点数が出ます (点数の年度の加点は実行した日の年度で決まる)。
`--resume` で飛ばすもの・やり直すものは「使い方」のとおりです。

## 見込み (1,741 自治体、既定の値)

金沢市での試し (2026-10-19、15 回) では、1 回の取得は待ちを入れて約 1.3 秒でした (サーバーの応答 0.1〜0.3 秒 + 1 秒の間隔)。

| | 見込み |
|---|---|
| 1 自治体の取得 | robots.txt 1 + サイトマップ 1〜6 + 検索 0〜1 + ページと資料 (見つかれば 60〜100、見つからなければ 150 まで) + 案内のページの子 (0〜40、収集日・地区の案内のページがあるときだけ) + ほかのサイト (0〜15 と、そのホストの robots.txt) + トップの書き直し (つながらないときだけ 0〜2) + 転送。パイロット (180 自治体、2026-10-19) では平均 87 回 (10% 点 44、中ほど 84、90% 点 152、いちばん多い 188)。上限は約 225 (公式サイトが 2 つの 9 自治体はその 2 倍まで) |
| 全国の取得 | 約 15 万〜17 万回の見込み (平均 87〜95 回 × 1,741。探し直しで、深いリンクと組合のサイトの分は 1 自治体あたり 3〜6 回増えた)。すべての自治体がすべての上限に届いたときの上限は約 39 万回 |
| 1 自治体の時間 | 2〜4 分 (応答の遅いサイトは 5〜6 分、`Crawl-delay` の長いサイトは 30 分で打ち切り) |
| 全国の時間 | `--workers 32` で 2〜4 時間、`--workers 16` で 4〜8 時間 (1 ホストへは 1 秒 1 回のまま。全体では毎秒 20〜25 回。遅いサイトがあると延びる) |
| 取る量 | HTML 1 自治体 5MB 前後 + PDF など 0〜25 件。全国で 10〜40GB の見込み (確かめていない)。キャッシュは HTML を縮めて置くので、ほぼ PDF の分 |
| LLM (使うとき) | 最大 5 × 1,741 = 約 8,700 回。1 回 2〜3 千トークンの入力。DeepSeek-V4.1-Flash の定価で $10〜30 の見込み |

数は見込みです。まずパイロットで `run_log.json` の数を確かめてください。
同じ IP の間隔 (`--ip-gap 0.25`) は、ふつうは時間に効きません (1 自治体は 1 秒 1 回なので、同じ IP に 4 自治体より多く集まるときだけ待つ)。
5xx を返し続けるホストは 10 回ほどで、`Crawl-delay` が 60 秒を超えるホストは robots.txt の 1 回であきらめるので、上の数より増えることはありません。

## 限界

- JavaScript で中身を作るページ、ログインの要るページは読めません (`not_found` になりやすい)
- ブロック・robots.txt で禁止のサイトは取りに行きません。人が見るか、自治体に問い合わせる (台帳の待ち行列) ことになります
- 証明書の中間証明書が足りないサイトは、Python では「つながらない」になることがあります (`sites[].blocked.reason` に `ssl` と出る)
- 点数は手がかりの言葉で付けるので、イベントカレンダーの表を収集日の表と取り違えることがあります。LLM の判定で確かめます
- 民間アプリのデータは使いません (リンクを記録するだけ)
- 同じ IP の間隔は、DNS で引いた始めの IP で数える目安です (CDN や DNS の負荷分散では別の IP に当たることがある)
- robots.txt の User-agent は、robotparser と同じく名前の一部が当たれば自分のグループとみなします (きびしい側に倒す)
- 古い Excel (`.xls`) は中を見ません (リンクの文字だけで候補にする)
- 案内のページの見分け方・子の広げ方は言葉とリンクの数の目安です。メニューの多い CMS では、ごみのページのほとんどが案内のページになり、
  メニューの「収集日」「地区」のリンクも子として取ります (上限 40 の内。同じ URL は 1 回だけ)
- 公の団体かは、ホスト名とリンクの文字の目安です。一部事務組合のサイトに構成のほかの町の収集日程もあると、そちらを取ることがあります
  (2 段目からはごみの手がかりのとても強いリンクだけ、上限 15)。組合が 23 区の清掃一部事務組合のように大きいと、上限まで取ります
- `--replay` では、前に取っていない URL (深いリンク・組合のサイト) はキャッシュに無いので、ネットで取るときより見つかるものが少なくなります
- PDF のページごとの見分けは文字のある PDF だけです (画像だけのページは数と縮小画像だけ)。ページの中で表の文字の順が崩れていると、
  月の見出しや日付の並びを見落とすことがあります

## ファイル

| ファイル | 役目 |
|---|---|
| `discover.py` | 入口 (自治体を選ぶ、並べて探す、書く) |
| `fetcher.py` | 礼儀を守る取得係とキャッシュ |
| `crawler.py` | 1 つの公式サイトの探し方 |
| `pages.py` | HTML・PDF・CSV・Excel・iCalendar を読む |
| `classify.py` | 種類・特徴・点数・形式 |
| `judge.py` | LLM の判定 |
| `eval_jgd.py` | japan-gomi-data の triage.csv との比べ |
| `eval_stale.csv` | triage.csv の URL のうち、古いと確かめたもの・確かめていないもの (比べるときに使う) |
| `test_discover.py` | ネットにつながないテスト (手元に偽の自治体サイトを立てる) |

## japan-gomi-data との比べ (eval_jgd.py)

[japan-gomi-data](https://github.com/tecolicom/japan-gomi-data) (tecolicom) の `docs/triage/triage.csv` は、自治体ごとの
収集日の資料の URL (`schedule_url`・`calendar_urls`) と形式 (`source_type`) を人が調べた表です。
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) で公開されていて、ここでは比べるためだけに使います
(リポジトリには入れず、`data/discover/eval/` に置く。`eval_jgd.csv`・`eval_jgd.md` の先方の URL・形式は triage.csv から)。

```
python nationwide/discover/eval_jgd.py --codes-from-triage > codes.txt        # triage.csv の自治体の lg_code (カンマ区切り)
python nationwide/discover/discover.py --codes $(cat codes.txt) --no-llm         # その自治体だけを探す
python nationwide/discover/eval_jgd.py                                         # → out/discover/eval_jgd.csv・eval_jgd.md
```

- triage.csv は `fetcher.Fetcher` (robots.txt・User-Agent・条件つき GET) で 1 回だけ取り、7 日のうちは取りに行きません
  (`--refresh` で取り直し、`--offline` でキャッシュだけ、`--triage` で手元のファイル)
- 先方の 5 桁の `code` に検査数字を足して 6 桁の `lg_code` にします (JIS X 0402。17201 → 172014)
- URL は形をそろえて比べます (http と https、先頭の `www.`、末尾の `/` と `index.html`、`#` 以降、`%` の書き方、問い合わせの順、
  パスの空白 (先方の「28栃谷.pdf」とこちらの「28 栃谷.pdf」)、`+` と `%20` を問わない)
- 同じページの別の URL も当たりとします: こちらの候補の `canonical` (幸手市: 13630.html の canonical が先方の 13626.html)、同じホストで
  番号のファイル名が同じページ (鶴ヶ島市: `/page/page003543.html` と `…/shushu-calendar/page003543.html`)、業者のアプリはホストと
  パスの始めの 2 つ (さいたま市: `admin.gomisuke.jp/app/0017/…`)
- 先方の URL が古いもの (stale) は割合から外します: [`eval_stale.csv`](eval_stale.csv) (404 や、今のページからリンクが無いと
  確かめたもの。蕨市・行田市・白岡市) と、こちらのキャッシュで 404・410 だったもの。確かめていないもの (`unverified`。桶川市・三郷市) は
  外さず、`eval_jgd.md` の一覧にだけ出します
- 1 行 1 自治体 (こちらの結果のあるものだけ): `found_schedule_url` (先方の schedule_url がこちらの候補にあるか。`yes` / `linked`
  (収集日のページの資料の一覧にだけある) / `no` / `stale` (先方の URL が古い) / `-`)、`found_calendar` と `calendar_hits` (calendar_urls のどれか・いくつ)、
  `top1` (こちらの best が先方の URL のどれか)、`top1_near` (top1 か、こちらの best が先方のページからリンクされた資料か、先方の資料と
  同じフォルダのもの。先方は案内のページ、こちらはその中の地区ごとの PDF、のように細かさだけが違うとき)、`stale_urls` (先方の古い URL の数)、
  `format_match` (こちらの format_class と先方の source_type。pdf → pdf_*、
  pdf-text → pdf_text、image-pdf → pdf_image、html → html_*、csv・xls → xlsx/csv、web-app → app_only、none → not_found。
  json・txt は比べない)
- `eval_jgd.md` は、それぞれの割合、形式の組み合わせの表、先方の URL がどれも候補に無い自治体の一覧

2026-10-19 の triage.csv は埼玉県・東京都・岡山県の 127 自治体で、パイロット (石川県・宮城県) とは重なりません。
まず `--codes-from-triage` の自治体で discover.py を走らせてから比べます。探し方を変えたあとは、`--replay` の結果と比べられます
(`eval_jgd.py --results out/discover/replay --offline`。`eval_jgd.csv`・`eval_jgd.md` は `--results` の所に書く)。
