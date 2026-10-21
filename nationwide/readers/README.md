# 収集日の読み取り (nationwide/readers/)

設計は [DESIGN.md](DESIGN.md)。ここには、できたものと動かし方だけを書く。

作者: firemio ([GitHub @firemio](https://github.com/firemio))

## いまあるもの (M1 の作る順の 1〜9)

| ファイル | 役目 |
|---|---|
| [schema.py](schema.py) | スキーマ v0.2 (`gomisute-schedule/0.2`) の型と検査。JSON Schema は [schedule-0.2.schema.json](schedule-0.2.schema.json) |
| [eval/regress.py](eval/regress.py) | 金沢・女川の今のデータを v0.2 に書き直し、K1・K3 (分け方)・O1 を確かめる |
| [eval/jgd.py](eval/jgd.py) | japan-gomi-data を正解にした系列の採点 (誤りの数と 95% の上限)。正解の取得と広げ方 |
| [eval/jgd_tiers.json](eval/jgd_tiers.json) | japan-gomi-data の 38 自治体の、正解として使える度合い (A〜D) |
| [eval/oracle/](eval/oracle/) | tier A の M1 の型の 8 自治体の「答えられるか」の札 (下書き。人が確かめる)。J4 は `jgd.py oracle` |
| [eval/gt_stale.json](eval/gt_stale.json) | 正解が古いと分けた食い違いの記録 (J7。証拠のそろわないものは誤りに数える。いまは 0 件) |
| [eval/fixtures/](eval/fixtures/) | 金沢の規則と印字の違いのうち、説明のつかない 62 か所 (人の裁定の待ち行列) |
| [fetch_docs.py](fetch_docs.py) | 段 0: 読む資料の一覧、anchor_context、使い回しの部分の数え上げ、足りない資料の取得 |
| [decode.py](decode.py) | 文字コードの判定 (BOM → HTTP → meta → UTF-8 → cp932。読めなければ止める) |
| [content.py](content.py) | 本文の範囲、方針の文のふるい (開庁時間・持ち込み)、deferred と caveat、但し書きの印 |
| [consume.py](consume.py) | 使ったことにする唯一の関門 (default-deny)。文・かっこ書き・マスの注・名前のマス・回数の書き添えは、読み取りが値を返し文の全体が閉じた見本に当たったときだけ使い、見本の名前を例外の template に書く。ほかは但し書きとして止める。洗い出しから外すのは文そのものが使われたとき (used。文字の一致では外さない) だけ |
| [version.py](version.py) | 版の証拠 (年の印・見える文字・時の証拠) と祝日の印の突き合わせ |
| [holidays.py](holidays.py) | 2020〜2027 年度の休日の表 (国民の祝日・振替休日・国民の休日) |
| [rules.py](rules.py) | 規則の文法 → IR (毎週・第N・N回目・N番目・隔週・月末・except_nth・第5週・months・seasonal・ambiguous_week・漢数字・「無し」)、IR → 日付・RRULE、方針の文の文法 (祝日・年末年始・休み・第5週) |
| [expand.py](expand.py) | 規則から系列の日付を作る条件 1〜6 (窓 W・closure_month・pending_printed・休日の種類・deferred・caveat・year_end_missing)、印字と規則の比べ (rule_vs_printed)、方針の文 → 例外 |
| [members.py](members.py) | 地区の文法 → 中身 (名前は資料のまま、条件の正規形)。町字への当て方は address-normalize の差し替え (match: unchecked) だけ |
| [categories.py](categories.py) | 品目の辞書 (公式の名前そのまま、大まかな分類、出し方) |
| [html_tables.py](html_tables.py) | 型 B: 2 通りの格子と表の形の確かめ、表の向き、行ごとの地区・品目・規則の読み。型 C: HTML の文を見出し (地区・品目) ごとに規則の文法で読む (どちらも読み取り役 A) |
| [markup.py](markup.py) | しるしの棚卸しと決まりの表 (版つき。読みを足す決まりは人が確かめるまで止める)、空のマスの決まり、生の断片、2 つ目の読み (LLM。読み取り役 B。表の生の断片 `table.B@2`、本文の範囲の全体 `text.B@1`) と A との比べ (品目・規則・地区・しるしの意味・ページの地区) |
| [tabular.py](tabular.py) | 型 A: CSV・Excel (文字コード、見出しの行を中身で探す、結合したマス・隠しシート) |
| [eval/traps.json](eval/traps.json) | 罠の再現セット (§6.2 の 21 行)。どの段で確かめるか (stage1 / stage2 / later) つき |
| [eval/traps.py](eval/traps.py) | 罠のうち、文法・広げ方・HTML の表の段のもの (14) を確かめる (金沢の K2・K3 を含む) |
| [eval/stage2.py](eval/stage2.py) | 作る順 5〜6 の読み取り (型 A・B・C) を tier A の HTML・CSV の 7 自治体に通す (data/readers/<lg_code>/stage2.json。out/schedules/ には書かない)。マスの読み残しは但し書き、資料どうしの食い違いは裁定待ち、能美の注 (inline) は地区を分ける |
| [llm.py](llm.py) | LLM の呼び出しと答えのキャッシュ (data/readers/llm_cache/。問いかけの版・モデル・入力 (画像も) の sha256 ごと) |
| [pdf_layout.py](pdf_layout.py) | 型 D: 文字の PDF の罫線のある表。字 (見えない字・壊れた字・重なった字)、表の形を 2 通り (find_tables と、罫線・塗りの辺を自分で束ねた格子。後の塗りに隠れた線は数えない) で作って比べる、日付の表 (月の軸と品目の軸) の読み、表の下の注、ページの型 (date_table / month_grid / pdf_outline / pdf_image / translation / other)、2 つ目の読みに渡す番号つきの枠の画像 |
| [llm_tasks.py](llm_tasks.py) | §3.10 の LLM の仕事 (ページの型・見出し・品目・地区・マスの数字・規則・方針と但し書き・区分けの決め方・出し方・しるし)。問いかけの版つき、答えはキャッシュ |
| [compare.py](compare.py) | 2 つの読みをそろえて比べる (agreed / agreed_weak / disputed / single / both_unsure、独立の度合い)。方針は値と span、deferred・但し書きは和 |
| [adjudicate.py](adjudicate.py) | 裁定の待ち行列 (data/readers/adjudications/queue/<lg_code>.json)。kind ごとの行き先を書くだけで、どの食い違いも自分では決めない。人の記録は資料の sha256 が合うときだけ使う |
| [validate.py](validate.py) | §4.1 の確かめ 1〜21 (当てはまる・当てはまらない・材料が無くて見られない、を分ける)。落ちた系列は待ち行列に入れて止める |
| [build.py](build.py) | 入口。段 0〜9 を通して out/schedules/<lg_code>.json (v0.2 と答えの種類) を書く。型 A・B・C は eval/stage2.py の読み、型 D は pdf_layout.py |
| [eval/m1.py](eval/m1.py) | M1 の合格の数 (J1〜J7) と、測るだけの数 (型 D の生の読みと正解の一致・待ち行列・LLM の費用) |

## 動かし方

```
python -m unittest discover -s nationwide/readers/tests -t nationwide/readers/tests   # ネットにつながないテスト (pytest でも走る)
python nationwide/readers/eval/regress.py [--write]     # 金沢・女川を v0.2 に。--write で out/schedules/ と eval/fixtures/ に書く
python nationwide/readers/schema.py check out/schedules/172014.json out/schedules/045811.json
python nationwide/readers/eval/jgd.py fetch --pages     # 正解を取る (GitHub。固定したコミットの tar.gz 1 本と公開の JSON 9 本)
python nationwide/readers/eval/jgd.py convert           # → data/readers/eval/jgd/<lg_code>.json
python nationwide/readers/eval/jgd.py check-port        # 広げ方が先方の公開の JSON と同じか (8 コース)
python nationwide/readers/eval/jgd.py selftest          # 正解をそのまま入れて誤り 0、壊すと誤りが出るか
python nationwide/readers/eval/jgd.py score             # out/schedules/ の tier A を採点 (J1・J2・J5・J7)。n=0 なら「測れない」で 2 を返す
python nationwide/readers/eval/jgd.py oracle            # 札と突き合わせる (J4)。人が確かめた札が無ければ「測れない」で 2 を返す
python nationwide/readers/fetch_docs.py --codes 112275,131024 [--fetch --max-requests 10]
python nationwide/readers/holidays.py 2026
python nationwide/readers/rules.py "第１・３・(5)火曜日"          # 規則の文 → IR
python nationwide/readers/html_tables.py <URL (キャッシュ)>      # 表の形・向き・読み・しるし
python nationwide/readers/expand.py traps                      # 罠の再現セットの stage2 の 14 (金沢の K2・K3 を含む)
python nationwide/readers/eval/stage2.py [--llm] [--offline]   # tier A の HTML・CSV に通す。--llm は 2 つ目の読み (キーは環境変数 GOMISUTE_LLM_API_KEY)
python nationwide/readers/build.py --tier-a --codes 172014,045811 [--llm | --offline]   # tier A の 17 と金沢・女川を out/schedules/ に
python nationwide/readers/pdf_layout.py <URL (キャッシュ)> [--page N]                   # PDF のページの型・表の形・日付の表
python nationwide/readers/validate.py out/schedules/112429.json                       # §4.1 の確かめの要約
python nationwide/readers/adjudicate.py summary                                       # 裁定の待ち行列の数
python nationwide/readers/eval/m1.py [--json 出力]                                     # M1 の合格の数
python nationwide/readers/eval/traps.py --stage3                                      # 罠の再現セット (stage2 と stage3)
```

- 取ったもの・作ったものは `data/readers/` (git に入れない)。`out/schedules/` と `eval/` は git に入れる
- 取得は `nationwide/discover/fetcher.py` (robots.txt・1 ホスト 1 秒 1 回・名乗る User-Agent・キャッシュ)。
  `fetch_docs.py --fetch` が取るのは、発見の結果 (`out/discover/`) に載っていてキャッシュに無い資料だけ
- 正解: [japan-gomi-data](https://github.com/tecolicom/japan-gomi-data) (作者 tecolicom、CC BY 4.0)。比べるためだけに使う

## まだ無いもの

- 関門 (§4.4) はどれも閉じている (人の監査がまだ 0)。だから published はどれも false で、答えの種類はどの地区も rule_contact
- 裁定は待ち行列を書くだけ。強いモデル (§9 の 2) とコードのやり方 3 (表の形を描いた画像の線から) はまだ呼ばない。人の記録 (data/readers/adjudications/decisions/) もまだ 0
- 型 D のうち地区 × 品目の規則の表 (仙台の「1･3金」) は、ページの型を rule_table と見分けるだけで、まだ読まない
- 型 E〜H (線の無い表・文字の月表・輪郭線・画像) と、日ごとに変わるページ (鶴ヶ島)、塩竈の anchor_context の地区の読みは読まない
- 地区のもれ (§4.1 の 9) は、町字の一覧 (ABR) と address-normalize をまだつないでいないので見ていない (重なりだけ見る)
- 人口の割合 (coverage.population_share) はまだ数えない
- eval/stage2.py の結果は data/readers/ にだけ書く (out/schedules/ は build.py が書く)
