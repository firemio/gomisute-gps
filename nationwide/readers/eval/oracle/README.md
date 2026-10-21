# 「答えられるか」の札 (eval/oracle/)

japan-gomi-data の tier A のうち、M1 の型 (A・B・C・D) の資料を持つ自治体について、品目ごとに
「資料に条件が全部書いてあるか」「同じ期間の印字の日付があるか」を付けた札。
DESIGN.md §6.2 の J4 (札が「答えられる」とした系列のうち答えるとしたもの 90% 以上、「答えられない」とした系列のうち答えるとしたもの 0) に使う。

作者: firemio ([GitHub @firemio](https://github.com/firemio))

- 1 自治体 1 ファイル `<lg_code>.json`。資料はキャッシュ (`data/discover/cache/`) の写しを読み、sha256 を書く
- いまの札は **下書き** (`labeled_by: "claude-draft"`、`human_checked: false`)。人 (firemio) が資料を見て確かめたら
  `human_checked: true` にして、確かめた日と直したところを `review` に書く。確かめていない札は J4 の数に入れない
- 分からないことは `"unknown"` にして、推し量りで埋めない

## 形

```json
{
 "format": "gomisute-oracle/0.1", "lg_code": "112275", "name": "埼玉県朝霞市",
 "labeled_by": "claude-draft", "human_checked": false, "labeled_at": "2026-10-19", "review": null,
 "documents": [{"doc_id": "d1", "url": "…", "sha256": "…", "format": "html_table", "role": "rules",
                "m1_type": "B", "time_evidence": ["page_updated 2024-06-06"]}],
 "categories": [
  {"label": "燃やすごみ", "coarse": ["burnable"], "mode": "curbside",
   "printed_dates": "no",
   "conditions": {"rule": "yes", "period": "no", "version": "no", "holiday_policy": "yes", "holiday_covers": ["national"],
                  "year_end": "no", "qualifiers_unresolved": "no"},
   "all_conditions_written": false,
   "expected": "not_answerable",
   "expected_blocked_by": ["period_unknown", "year_end_missing"],
   "evidence": [{"doc_id": "d1", "quote": "※祝日も収集します（年末年始を除く）"}],
   "district_overrides": [],
   "note": "…"}
 ]
}
```

| 項目 | 値 | 意味 |
|---|---|---|
| `printed_dates` | `yes` / `no` / `unknown` | 同じ期間の、その品目の印字の日付 (カレンダー・日付の表・日付の一覧) が資料にあるか |
| `printed_type` | `A`〜`J` | 印字の日付の資料の型 (§3.5)。M1 で読むのは A〜D だけ |
| `conditions.rule` | `yes` / `no` / `ambiguous` | 規則が書いてあり 1 つに決まるか (「第N週」は `ambiguous`) |
| `conditions.period` | `yes` / `no` | 有効期間が資料に書いてあるか |
| `conditions.version` | `yes` / `no` / `unknown` | 版の証拠 (年の印と時の証拠。§3.3) がそろうか |
| `conditions.holiday_policy` | `yes` / `no` / `caveat_only` | 祝日の扱いが本文に書いてあるか (`caveat_only` は「変更になることがあります」だけ) |
| `conditions.year_end` | `yes` / `no` / `deferred` | 年末年始の扱い (休みの期間・日付) が書いてあるか。`deferred` は名前のある期間を後で知らせると書いたもの |
| `conditions.qualifiers_unresolved` | `yes` / `no` / `unknown` | 使い道の決まっていない但し書きがあるか |
| `expected` | `answerable` / `not_answerable` / `unknown` | M1 の読み取りが答えるとすべきか (型が M1 に無い印字があれば `pending_printed` で答えない) |
| `district_overrides` | `[{"districts": "…", "expected": "…", "why": "…", "district_ids": […]}]` | 一部の地区だけ違うとき。`district_ids` (こちらの地区の id) は、読み取りの出力ができてから人が書く。無いうちは、品目の札と違う上書きがあれば、その品目の系列は J4 で「決まらない」になり数えない |

`expected_blocked_by` は §2.5 の理由の名前を使う。ほかに `mode_dropoff`・`mode_reservation` (持ち込み・申し込み制の品目は
`mode` で日付を答えない。blocked_by ではない) と、`unconsumed_qualifier` (中身の id は読み取りが決めるので書かない) を使う。
`evidence` の引用は、テストがキャッシュの資料の中にあるかを確かめる (`tests/test_jgd.py`)。
J4 の数は `python nationwide/readers/eval/jgd.py oracle` (札の品目とこちらの品目は `coarse` が重なるもので当てる)。

`category` が `"*"` の行は、その自治体のすべての品目に同じ札を付ける (品目の名前まで確かめていないとき)。
