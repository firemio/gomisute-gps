# japan-gomi-data との比べ (eval_jgd)

比べた相手: [japan-gomi-data](https://github.com/tecolicom/japan-gomi-data) (tecolicom) の `docs/triage/triage.csv`。[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) で公開されているものを、比べるためだけに使っている (この表の URL・形式は先方のデータから)。

- triage.csv: 127 自治体 (取った日時 2026-10-19T03:47:08+09:00、キャッシュ fresh)
- こちらの結果: `out/discover` にある 126 自治体と比べた (triage.csv の自治体のうち、まだ探していないもの 1)

| 見るもの | 数 |
|---|---|
| schedule_url が候補にある | 116 / 125 (93%) |
| schedule_url が候補か、収集日のページの資料の一覧にある | 116 / 125 (93%) |
| calendar_urls のどれかが候補にある | 101 / 115 (88%) |
| calendar_urls のどれかが候補か資料の一覧にある | 104 / 115 (90%) |
| top1 (こちらの best が先方の URL のどれか) | 65 / 125 (52%) |
| top1_near (top1 か、best が先方のページの資料・同じフォルダの資料) | 90 / 125 (72%) |
| 形式が合う (format_class と source_type) | 75 / 122 (61%) |

URL は canonical・番号のファイル名の別名・業者のアプリのパスの始めでも当たりとする。先方の URL のうち古いと確かめたもの (stale) は割合から外す。

## 形式の組み合わせ (行: 先方の source_type、列: こちらの format_class)

| source_type | app_only | blocked | html_table | html_text | not_found | pdf_image | pdf_text | unreachable | xlsx/csv |
|---|---|---|---|---|---|---|---|---|---|
| csv |  |  | 5 |  |  |  | 1 |  | 1 |
| html |  |  | 18 | 4 |  |  | 3 |  |  |
| image-pdf | 1 |  | 3 | 6 |  | 4 | 3 |  |  |
| json |  |  |  |  |  |  | 2 |  |  |
| none | 1 |  |  |  |  |  |  |  |  |
| pdf |  | 2 | 5 | 4 | 2 | 1 | 42 | 1 |  |
| pdf-text |  |  | 6 | 1 |  |  | 5 |  |  |
| txt |  |  |  |  |  | 1 | 1 |  |  |
| web-app |  |  |  | 1 |  |  |  |  |  |
| xls |  |  | 1 |  |  |  | 1 |  |  |

## 先方の URL がどれもこちらの候補に無い自治体 (3)

| lg_code | 名前 | 先方の形式 | こちらの形式 | 先方の schedule_url |
|---|---|---|---|---|
| 132110 | 小平市 | pdf | unreachable | https://www.city.kodaira.tokyo.jp/kurashi/092/092557.html |
| 132136 | 東村山市 | pdf | blocked | https://www.city.higashimurayama.tokyo.jp/kurashi/gomi/gomi/29gomi-calendar.html |
| 134210 | 小笠原村 | pdf | blocked | https://www.vill.ogasawara.tokyo.jp/kankyo/gomi/gomikaisyu/ |

## 先方の URL のうち古いもの・確かめていないもの (9)

stale は割合から外し、unverified は外さない (nationwide/discover/eval_stale.csv と、こちらのキャッシュの 404・410)。

| lg_code | 名前 | 状態 | わけ | URL |
|---|---|---|---|---|
| 112062 | 行田市 | stale | 今の 3208.html からリンクが無い (確かめた) | https://www.city.gyoda.lg.jp/material/files/group/18/syuusyuu.pdf |
| 112062 | 行田市 | stale | 今の 3208.html からリンクが無い (確かめた) | https://www.city.gyoda.lg.jp/material/files/group/18/r6-12.pdf |
| 112232 | 蕨市 | stale | 404 (確かめた) | https://www.city.warabi.saitama.jp/kurashi/gomi/shushubi/1005656.html |
| 112232 | 蕨市 | stale | 404 (確かめた) | https://www.city.warabi.saitama.jp/kurashi/gomi/shushubi/1004345.html |
| 112313 | 桶川市 | unverified | 取ったページのどれからもリンクが無い (古い版らしい。確かめていない) | https://www.city.okegawa.lg.jp/material/files/group/3/gomi_3.pdf |
| 112313 | 桶川市 | unverified | 取ったページのどれからもリンクが無い (古い版らしい。確かめていない) | https://www.city.okegawa.lg.jp/material/files/group/3/higashi.pdf |
| 112313 | 桶川市 | unverified | 取ったページのどれからもリンクが無い (R4 = 2022 年度。確かめていない) | https://www.city.okegawa.lg.jp/material/files/group/3/nisi_gomi_R4.pdf |
| 112372 | 三郷市 | unverified | 取ったページのどれからもリンクが無い (リンクの切れた古いファイルらしい。確かめていない) | https://www.city.misato.lg.jp/material/files/group/20/hppet.pdf |
| 112461 | 白岡市 | stale | 今の shiraoka/ のページからリンクが無い (別の PDF に差し替え。確かめた) | https://hs-eiseikumiai.org/wp-content/uploads/2026/03/a55dd53e085b67d9c156832f4d034863.pdf |
