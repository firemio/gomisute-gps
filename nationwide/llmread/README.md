# 収集日の読み取り (nationwide/llmread/)

作者: firemio ([GitHub @firemio](https://github.com/firemio))

ごみの収集日の資料 (HTML・CSV・Excel・PDF) を LLM が読み、決まった形の JSON に書き写します。
日本語の文を読んで意味を決めるのは LLM だけです。Python がするのは次のことだけです。

- 資料をキャッシュから読み込む。PDF のページを画像にする
- モデルを呼ぶ (DeepSeek と Opus に同じ入力・同じ問いかけ)
- 答えの JSON の形を確かめる
- LLM が書いた規則から日付を計算する (祝日・第N曜日・休み)
- 2 つのモデルの読みを比べ、正解と照らして採点する

決め方 (作者 firemio): 同じ入力で Opus と DeepSeek を比べ、どちらも正しければ DeepSeek を使います (安いため)。
比べた結果は [docs/llm-read-compare.md](../../docs/llm-read-compare.md) にあります。

前の読み取り (`nationwide/readers/`。Python の決まりで文を読むもの) は 2026-10-21 に消しました。
8 回直しても穴が残ったためです。コードは git の履歴 (コミット d983f4b まで) に残っています。

## ファイル

| ファイル | 役目 |
|---|---|
| [run.py](run.py) | 入口: 計画 (`plan`)・入力づくり (`prep`)・1 本ずつ聞く (`ask`)・1 本ずつの採点 (`score`)・試走 (`smoke`) |
| [full.py](full.py) | 本番: 計画のすべてを 2 つのモデルに聞く (`ask`。費用の上限つき)・まとめる (`report`)・モデルを呼ばずに採点を作り直す (`score`) |
| [__main__.py](__main__.py) | `python -m nationwide.llmread <コマンド>` (`ask`・`report`・`score` は full.py、ほかは run.py) |
| [plan.py](plan.py) | 読む資料の計画 (japan-gomi-data tier A・女川 12 か月・金沢 5 地区) → `out/llmread/plan.json` |
| [prep.py](prep.py) | 資料 1 本 → モデルに渡す入力。HTML は本文の範囲の文字 (表は簡単な HTML)、CSV・Excel は markdown の表、PDF はページの画像と文字層 |
| [htmlbody.py](htmlbody.py) | HTML の本文の範囲と使い回しの塊。ページの形 (タグ・id・class) だけで決め、文の意味では外さない |
| [decode.py](decode.py) | 文字コードの判定 (BOM → HTTP → meta → UTF-8 → cp932。読めなければ止める) |
| [fetch_docs.py](fetch_docs.py) | 読む資料の一覧と、足りない資料の取得 (発見の結果に載っているものだけ。取得は `nationwide/discover/fetcher.py`) |
| [prompt.py](prompt.py) | 問いかけと答えの JSON の形 (両方のモデルに同じもの。`llmread.p1`) |
| [runners.py](runners.py) | モデルの呼び出しと答えの記録 (トークン・費用・秒) |
| [llm.py](llm.py) | DeepSeek (OpenAI 互換) の呼び出し。`nationwide/discover/judge.py` と `nationwide/pilot/` も使う |
| [calc.py](calc.py) | 答えの形の確かめと、規則 → 日付の計算。答えてよいか (answerable) の印 |
| [holidays.py](holidays.py) | 2020〜2027 年度の休日の表 (国民の祝日・振替休日・国民の休日) |
| [score.py](score.py) | 正解との採点 (地区・品目は名前が合うものだけ当てる) |
| [jgd.py](jgd.py)・[jgd_tiers.json](jgd_tiers.json) | 正解 japan-gomi-data の取得・広げ方と、自治体ごとの使える度合い |
| [tests/](tests/) | ネットにつながず、モデルも呼ばないテスト |

## 動かし方

リポジトリの一番上で動かします。DeepSeek のキーは環境変数 `GOMISUTE_LLM_API_KEY` だけで渡します (リポジトリに書かない)。

```
python -m nationwide.llmread plan                        # out/llmread/plan.json (資料の一覧)
python -m nationwide.llmread prep jgd:112275             # 入力を作る (data/llmread/inputs/)
GOMISUTE_LLM_API_KEY=... python -m nationwide.llmread ask [--cap-opus 60] [--cap-deepseek 5]   # 聞く (答えがあれば聞き直さない)
python -m nationwide.llmread report                      # out/llmread/raw/・inputs.json・scores/・results.json
python -m nationwide.llmread score --check               # モデルを呼ばずに、生の答えから採点を作り直して今のものと比べる
python -m pytest -q nationwide/llmread/tests
```

- `score` は `out/llmread/raw/` (生の答え)・`out/llmread/inputs.json` (資料の記録)・`out/llmread/plan.json` と正解だけを読みます。
  モデルも `data/llmread/` も使いません。`--check` を付けないと `out/llmread/scores/` と `results.json` を書き直します
- japan-gomi-data の正解は先に取って広げます: `python nationwide/llmread/jgd.py fetch && python nationwide/llmread/jgd.py convert`
  (GitHub から tar.gz を 1 本。`data/readers/eval/` に置く)
- 1 本ずつ聞く・採点するときは `python nationwide/llmread/run.py ask jgd:112275` のように資料を選びます

## 正解

- japan-gomi-data ([tecolicom/japan-gomi-data](https://github.com/tecolicom/japan-gomi-data)、作者 tecolicom、CC BY 4.0)。
  コミット d786a985 に固定し、比べるためだけに使います
- 女川町: `out/onagawa_reading.json` の final
- 金沢市: `out/kanazawa_schedules.json`

## 置き場所

- `data/llmread/` (git に入れない): 入力 (画像・文字) と答えの記録
- `data/readers/` (git に入れない): 資料の一覧・使い回しの塊・japan-gomi-data。名前は前の読み取りのときのままです
- `out/llmread/` (git に入れる): 計画・生の答え・採点・まとめ。家のフォルダの道すじは伏せ、鍵は書きません
