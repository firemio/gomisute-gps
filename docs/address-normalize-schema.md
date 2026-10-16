# address-normalize スキーマ v0.2

自治体の資料 (ごみ収集区域表など) に書かれた町名や自由文の住所を、**現行の町字 ID** に寄せる正規化層です。Cloudflare Worker (D1) として別にデプロイし、最初の利用者である gomisute-gps からは Service Binding の RPC で呼びます。

この文書は同じパスにある v0.1 draft を置き換えます。

- **v0.1 の範囲**: 全国の現行町字 (ABR) と、金沢の旧町名の系譜。型と取込パイプラインは全国 1,741 自治体を前提にしています。
- **DDL**: [address-normalize-schema.sql](address-normalize-schema.sql)。address-normalize のリポジトリでは `schema/0001_data.sql` (データ本体の D1) と `schema/0001_ops.sql` (書き込み用の D1、ファイル末尾の部分) に分けます。
- **検証用の試作**: [prototypes/address-normalize/](../prototypes/address-normalize/)。`build_check.py` で全国 ABR をこの DDL に流し込み (749,603 行・296MB)、`cases_check.py` で §10 の 10 ケースを解決します。出力は `cases_out.txt` (30 通り) で、§1.2 と §10 の数値はここから出ています。
- **決め方**: 方針の違う 3 案 (時間軸と来歴重視 / 1 週間で出す最小構成 / 利用側の契約重視) を独立に設計させ、審査役が採点して統合しました。骨格は時間軸の案で、表を 25 から 16 に減らし、API と gomisute-gps 側の使い方は利用側の案から取っています。
- **正規化規則**: `rules/rules.json` と `rules/golden.tsv`。Python の builder と TS の Worker が同じテストベクトルを通します。
- **調査レコード**: `research/{lg_code}/{town_id}.json` (git 管理)。

---

## 1. 前提

### 1.1 2026-10-16 までに確かめた事実

**F1. ABR 町字マスターに履歴が無い**
- 全 727,467 行・1,892 自治体で、ablt_date も alias_* も 0 行。
- efct_date は 98.6% が 1947-04-17、さらに 8,384 行が 1970-01-01 で、どちらもプレースホルダ。実日付は約 2 千行 (多くは 2021 年以降) だけ。
- 設計への影響: 履歴は自前で持つ。プレースホルダの日付は NULL にする。初回取込分の町字は「この日以前から存在した」とみなす。

**F2. ABR に無いことは廃止を意味しない**
- 地番の登記が無い残存町 (西堀川町・八幡町・笠舞町・糸田町・神田町) は ABR に無い。
- 郵便番号が現役の北安江町・西念町も ABR に無い。
- 設計への影響: ABR に無い町字を自前の実体 (X) として持つ。ABR・国勢調査・郵便番号に「無い」ことは弱い証拠として扱う。

**F3. 大字行の無い小字行 (幽霊大字) がある**
- 全国で 5,379 大字、383 自治体。
- 筆があれば本物 (窪町は 7 つの小字に 31 筆)。筆が無ければ死んだ行のことがある (入江町ヘは 0 筆)。
- 設計への影響: 合成親 P を作り、筆数で status_auto を決める。

**F4. 国勢調査の差分で後継が機械的に取れる**
- 2000〜2020 年の小地域を重ねると、後継と面積比が自動で出る (金沢で 10 件)。
- 2000 年より前は文献で調べる: 換地図、県公報、区画整理一覧 (公告日 + 1 日 = 効力発生日)、郵便番号、Wikipedia の navbox の ※、geolonia。
- 設計への影響: 国勢調査で見つけた変化を census_observed 事象として持ち、同じ変化を説明する調査事象ができたらそちらへ統合する。

**F5. 1 つの名前が段階的に消え、中間名を経て、復活もする**
- 段階的な消滅: 柳町 (1965/1992/1993)、北安江町 (1992〜2006)、寺地町 (1979 年に大半、1990 年頃に最後)。
- 小字の帯として残ることがある。
- 中間名がある: 神田町5丁目 (1965)、入江町イ〜ホ (1972)。
- 別の範囲で復活する: 観音町1〜3丁目 (2019)。
- 設計への影響: 名前ではなく実体で持つ。有効期間の境界を lo / hi / best で持つ。大半が移った事象と名前が消えた事象を分ける。復活した名前は別の実体にする。

**F6. 表記の畳み込みは必要だが衝突する**
- ノ/の、ヶ/ケ などを畳まないと照合できないが、畳むと同一自治体の別の大字が衝突する。
- 設計への影響: 照合キーを canon と fold の 2 段にし、fold で複数の候補に当たったら自動で選ばない。

**F7. 町字でない名前が混じる**
- 区域表に「湖南第2区画整理地」のような仮称が混じる。
- ABR 自身も「近岡町土地区画整理地」などの区画整理名の行を 264 行持つ。
- 設計への影響: level='label' として持つ。

**F8. 旧町名の行が指すのは、その行が書かれた時点でまだその名前だった土地**
- 多くは最後の事象の後継にあたる。
- 残った帯が別の地区にあることがある。
- 設計への影響: primary の印・段階・面積比・事象日を返し、どれを使うかは gomisute-gps が選ぶ。

**F9. 幾何の手がかりは外部にある**
- ABR の代表点 (金沢 906 点)、地番の代表点 (金沢 649,655 筆のうち 287,501 筆)、GSI の住居表示点、国勢調査のポリゴン。
- 設計への影響: 代表点・位置参照コード・KEY_CODE・筆数を返す。ポリゴンは持たない。

**F10. 市区町村層の材料**
- ABR mt_city (6 桁コード) と、address2houmukyoku の旧市町村表 (名前ベース、LLM 抽出、1985 年以降、非公開リポジトリ)。
- 設計への影響: muni と muni_name の 2 表にする。

### 1.2 2026-10-17 の追加計測

全国の ABR mt_town_all を再集計し、このスキーマに実際に投入して確かめた結果です。

1. **重複 ID**: (lg_code, machiaza_id) の重複 1,248 件は rsdt_addr_flg 0/1 の 2 行です (観音町3丁目など)。名称は同じなので 1 実体にまとめ、numbering='mixed' とします。type 4/5 の 101 行は v0.1 では取り込みません。
2. **type1 行の無い大字**: 大字コード (machiaza_id の先頭 4 桁) ごとに見ると、type1 行を持たない大字が 23,775 あります。内訳は丁目だけの束が 18,396、小字だけ (幽霊大字) が 5,379 です。
3. **既存の町字と同名の幽霊大字**: 幽霊大字のうち 159 は、大字名の canon キーが同じ自治体の既存の町字と一致します。
   - 例: 金沢の 山ノ上町五丁目 (大字コード 0632) の小字ソ〜レ と 山ノ上町5丁目 (0523005)、安中市 安中１丁目。
   - この 159 大字・941 小字は、既存の町字の子にします (flag phantom_adopted)。
4. **大字名が空の小字**: 131 自治体の 2,873 小字は、大字コードが 0000 で大字名が空です (北海道の号など)。親は作りません (flag no_oaza)。
5. **筆数が分かるまで uncertain**: 残る 5,089 の幽霊大字の配下 158,112 小字は、筆数を数えるまで uncertain になります (F3)。筆数の集計は v0.1 では金沢だけです。
6. **「大字X」と「X」の並存**: 同じ自治体に別の大字コードで並ぶ組が約 1,046 あります。住居表示を実施した部分と、未実施の地番区域です。
   - canon で「大字」を落とすと、同一自治体内の canon 衝突が 152 から 1,198 に増えます。
   - したがって「大字」は fold の段でだけ落とします。
7. **CJK 互換漢字**: NFKC は互換漢字も畳みます (塚 U+FA10 → 塚)。互換漢字だけが違う別の大字が、同一自治体に 14 組あります (浜松市中央区に 9 組)。canon で同じキーになるので、この組は常に ambiguous にします。
8. **ノ/の・ヶ/ケ の衝突数**
   - この 2 規則だけで畳むと、type1/2 の大字名で 19 組が衝突します。F6 の 17 組との差は数え方の違いで、字付きの名前を含めると 19 組です。
   - この設計の fold 規則全体では、fold の段でだけ衝突する組が 1,628 あります。大半は 6. の「大字X / X」の組です。
9. **筆の無い ABR の行**
   - 金沢では status_flg=0 かつ筆 0 の地番区域が 126 行あります (入江町ヘ・窪町ニ・窪町白金・赤土町ホ など)。
   - type1 でも、堀川角場町 (0612000) が status_flg=0・筆 0 です。2006 年に全域が堀川新町へ移っています。
10. **容量**: このスキーマに全国の ABR を入れると、town は 749,603 行 (うち合成親 23,485)、ファイルは索引込みで 296MB です。金沢は約 6,900 行、最大は福井市の 15,518 行です。
11. **検証**: §10 の 10 ケースの行はこの DDL にそのまま入り、外部キー違反は 0 件でした。§6 の規則を Python に写した検証器で、§10 に書いた結果が出ることを確認しました。

---

## 2. 設計原則

1. **名前をキーにしない**
   - 実体 (town)・表記 (town_name)・変化 (change_event と event_transfer) を別の表に分けます。
   - town の 1 行は「同じ正式名称が途切れずに有効だった区域」です。区域が縮んでも名前が続けば同じ行、改称・途切れ・復活は別の行にします。
   - 現行の町字・旧町名・中間名・仮称ラベル・残存帯を、すべて同じ表に置きます。
2. **時間は区間で持つ**
   - 有効期間は半開区間 [from, to) です。
   - 各境界は lo / hi / best / prec で表し、as-of の判定には lo と hi だけを使います。判定結果は yes / no / unknown の 3 値です。
   - 大半が移った事象 (bulk_event) と、名前が最後に消えた事象 (end_event) を分けて持ちます。
3. **ABR は現在形の証拠として扱う**
   - ABR に無くても廃止とは限らず、ABR にあっても生きているとは限りません。
   - ABR と規則だけで決めた status_auto と、調査で上書きした実効値 status を両方持ちます。
   - 月次の ABR 更新は調査結果を壊しません。
4. **すべての事実は claim を通す**
   - claim は確信度、検証状態 (反証の結果)、証拠 (資料・立場・強さ・観測日) を持ちます。
   - 同じ元リストから作られた資料は、indep_group で 1 票として数えます。
5. **表記の同一視は 2 段にする**
   - canon は同一視してよい規則、fold は衝突しうる規則で、fold は候補を出すためだけに使います。
   - canon で 2 件以上に当たった場合や、fold で別の実体に当たった場合は、§6.4 の規則に従って自動で選びません。
6. **どの後継にごみ規則を当てるかは gomisute-gps が決める**
   - address-normalize は、後継・段階・事象日・面積比・確信度と、「資料が書かれた時点でその名前だった土地」の印 (primary) を返します。
   - primary は推定であり、確定ではありません。
7. **D1 はビルド成果物にする**
   - 正本は git の調査レコードと、R2 に置いた生データです。
   - 毎回ローカルの SQLite に組み立て、不変条件とゴールデンケースを通った版だけを blue/green で公開します。
   - 書き込みのある表は別の D1 に置きます。
8. **v0.1 は幾何を持たない**: 代表点・位置参照コード・国勢調査の KEY_CODE・筆数という手がかりだけを返します。
9. **表は必要なものだけ作る**: データ本体 13 表と書き込み用 3 表です。v0.1 で誰も読まない表は作りません (§11)。

---

## 3. 層と ID

```
L1 市区町村   muni / muni_name
              ← ABR mt_city + address2houmukyoku の旧市町村表
L2 町字       town / town_name / change_event / event_transfer / town_census
              ← ABR mt_town (+pos, +筆数) / 国勢調査の差分 / 調査レコード
L3 地番・街区  v0.1 では持たない (town に筆数・位置参照コード・代表点だけ)
```

**GPS からの入口は別 Worker (gps2city)**
- 緯度経度 → 市区町村は [gps2city](https://github.com/firemio/gps2city) (国土数値情報 N03 を 0.1° タイル化) が担います。address-normalize は GPS を扱いません。
- 2026-10-17 から `POST /location` が `lg_code` (ABR と同じ 6 桁) と政令市の `ward` を返します。タイルの 5 桁コードに検査数字を付けた値で、ABR mt_city と 1,898/1,905 件一致しました (残り 7 件は所属未定地)。
- したがって muni.lg_code が GPS 側とも共通のキーになります。

**town_id の形**

| 形 | 意味 | 例 |
|---|---|---|
| `{lg}-{machiaza_id}` | ABR の行 (rsdt 違いの 2 行は 1 実体) | 172014-0157000 木ノ新保町 |
| `{lg}-P{大字コード4桁}` | ABR から合成した親。type1 行を持たない大字コードごとに 1 つ (丁目の束か幽霊大字) | 172014-P0164 窪町、172014-P0138 観音町 |
| `{lg}-X{5桁}` | 自前の実体 (調査・国勢調査・ABR 差分)。`registry/town_ids.jsonl` で払い出し、二度と変えない | 172014-X00001 木ノ新保七番丁 |

**ID の規則**
- town の CHECK 制約で、origin と ID の形を一致させます。
- 丁目・小字は parent_id で大字 (合成親を含む) につながります。自身と子孫をまとめて「名前の家族」と呼び、系譜の探索は家族単位で行います。たとえば寺地町の後継には、子の 寺地町ニ・寺地町ハ からの移転も含めます。
- 後で同一物と分かった実体は削除せず、redirect_to で寄せます (X の行が後で ABR に載った場合など)。下流が保存した ID はそのまま引けます。
- 主キーはすべて決定的な文字列にします (例: claim_id = 'r:172014-X00001:status')。再ビルドで番号が振り直されないので、版ごとの差分 (change_log) が小さくなります。

---

## 4. テーブル

**データ本体の D1**

| 表 | 1 行の意味 | 主な列 |
|---|---|---|
| dataset_version | 公開した版 | data_version, abr_snapshot, census_years, rules_version, research_ref, builder_ref |
| change_log | 前の版から変わった町字 | data_version, town_id, change。直近 24 版を保持 |
| ingest_run | 機械処理 1 回 | kind, code_ref (git), input_sha256, stats (件数・衝突監査・キュー候補) |
| source | 資料 | kind, url, archive_ref (R2), reliability, indep_group |
| claim | 事実 1 件 (一括の事実は 1 行) | confidence, verification, conf_before, reviewed_by, open_questions, record_ref |
| evidence | claim の証拠 | source_id, stance (supports / contradicts / context), strength (不在は weak), observed_on, locator, finding (要約のみ) |
| muni | 現行の市区町村 | lg_code (6 桁), lg5 (5 桁、全国で一意), parent_lg_code (区 → 市), lineage_level |
| muni_name | 市区町村名の各形と旧名 | kind (official / official_short / former), portion, event_kind, eff_date |
| change_event | 事象 1 件 (告示・換地処分・住居表示・改称・復活・観測) | kind, legal_ref, announce_date, eff_lo/hi/best/prec, eff_basis, project_ref, stage_label, merged_into |
| town | 町字の実体 | §3 の ID, level, parent_id, key_canon/fold, status / status_auto, from_* / to_*, start / bulk / end_event_id, revives_id, redirect_to, transitional, abr_*, parcel_count, numbering, 代表点, pos_code, flags |
| town_name | 正式名以外の表記 | kind (census / doc_label / map_label / legacy_spelling / project …), used_from / used_to |
| event_transfer | 事象で土地が from から to へ移ったこと | portion, share_of_from / share_of_to, share_basis, is_noise, koaza_detail |
| town_census | 町字と国勢調査小地域の対応 (年つき) | relation (equal / contains / part_of / hosts_remnant / point_in), share |

**書き込み用の D1 (addrnorm_ops)**

| 表 | 1 行の意味 | 主な列 |
|---|---|---|
| report | 利用者・下流からの指摘 | 番地以降と連絡先は保存せず、重複検知用の HMAC だけ持つ |
| unresolved_query | none / ambiguous / uncertain になった照合 | (lg_code, key_canon) ごとに集計 |
| research_queue | 調べること | reason (abr_removed, census_gone, phantom_no_parcels, usage_conflict …), priority |

### 列の補足

**status** は 4 値です。
- current: 現行。
- remnant: 法的には生きている極小の残存。郵便番号や筆が無いこともあります。
- abolished: 廃止。
- uncertain: ABR から消えた、筆が無い、または証拠が足りない。廃止とは言えない状態です。
- current と remnant は to_* と end_event を持てません (CHECK 制約)。

**status_auto** の決め方は §9.2 にあります。調査レコードが status を上書きしても status_auto は残るので、API は「ABR には行があるが調査では廃止」(入江町ヘ) のような食い違いを警告できます。

**from_* / to_* の NULL**
- from_lo と from_hi が両方 NULL なら、「ABR の初回取込より前から存在した」とみなします (F1 のとおり開始日は分からないため)。
- to_hi が NULL なら、「まだ残っている可能性がある」という意味です。

**abr_presence** は 4 値です。
- present: 最新の ABR にある。
- via_children: 合成親 (自分の行は無く、子の行が ABR にある)。
- gone: 以前の ABR にはあった。
- absent: 一度も ABR に無い。

**flags** に入る値: phantom, phantom_adopted, no_oaza, no_parcels, parcels_unchecked, abr_unconfirmed (status_flg=0), legacy_block, label_rule, census_hidden, name_reused, not_in_abr_but_living。

**town_name** には、ABR の現行町字の正式名は入れません (town.name と town.key_* で引きます)。行を持つのは、国勢調査・区域表・換地図での表記、旧表記、事業名だけです。

**event_transfer の share**
- 国勢調査の面積比などで、法的な割合ではありません。そのため share_basis を必ず付けます。
- 2% 未満の欠片は is_noise=1 とし、既定では返しません。
- 法的な移転でない重なりは移転にせず、town_census の hosts_remnant で表します (例: 西堀川町の 30.3% が北安江1丁目の小地域に入っている)。

**change_event.merged_into**
- 国勢調査で見つけた変化を、同じ変化を説明する調査事象に統合したときに設定します。
- 統合された事象は解決に使いませんが、その面積比は調査側の移転に写します。

---

## 5. 照合キー (rules/rules.json)

入力と保存側の両方に、同じ関数で strip → canon → fold の順に規則を当てます。Python (builder) と TS (Worker) は、同じ `rules/golden.tsv` を CI で通します。

既存の townnorm.norm_town は ヶ・之・異体字を表示名の段階で畳んでいるので、fold の段に移します。

| 段 | 規則 | 例 |
|---|---|---|
| strip (入力と表記だけ) | 末尾の「の一部」「一部」「地内」「ノ部」「の部」を外し、外した語を qualifiers に残す | 寺地町ニノ部 → 寺地町ニ + [ノ部]、藤江町地内 → 藤江町 + [地内] |
| canon | NFKC (全角の数字・英字、半角カナ、互換漢字)、空白の除去、数字の間のダッシュ類を '-' にそろえる | 山ノ上町５丁目 → 山ノ上町5丁目 |
| canon | 漢数字 + (丁目・番丁・番町・条・線・地割) を算用数字に。「第 + 漢数字」を「第N」に。末尾の「N丁」を「N丁目」に | 木ノ新保七番丁 → 木ノ新保7番丁、湖南第二 → 湖南第2、鞍月東1丁 → 鞍月東1丁目 |
| fold | ノ・之 → の、ヶ・ヵ → ケ | 木ノ新保7番丁 → 木の新保7番丁 |
| fold | 異体字: 斉→斎、舘→館、薮→藪、刈→苅、嶽→岳、冨→富、渕→淵、澤→沢、濱→浜、邊・邉→辺、嶋→島、龍→竜、槇→槙 | 芳斉 → 芳斎 |
| fold | 先頭の「大字」「字」を外す | 大字小柳 → 小柳、字松ヶ沢 → 松ケ沢 |

**しないこと**
- 「町」の有無は畳みません (西念 ≠ 西念町、寺地 ≠ 寺地町)。
- canon の段で「大字」を落としません (§1.2-6)。
- 「が/ヶ」(〜が丘 / 〜ヶ丘)、かな、編集距離による照合は v0.1 では扱いません。
- 「もりの里1」→「もりの里1丁目」のような補完は、キーではなく構文解析で行います (§6.9)。

---

## 6. 解決アルゴリズム

入力は次の 4 つです。
- lg_code (政令市なら全区を対象にする)
- text
- as_of = {date, mode}
- context ('municipal_table' または 'free_text')

### 6.1 as_of

- **as_of.date** は「その表記が書かれた日」です (区域表の公表日など)。'YYYY'、'YYYY-MM'、'YYYY-MM-DD' のどれでも渡せます。幅のある日付は両端で判定し、結果が食い違えば unknown にします。
- **mode** は 2 値です。
  - 'authored': 書かれた日として信用してよい。
  - 'unknown': 書かれた日が分からない。これが既定で、date には今日が入ります。
- 区域表は、廃止された旧町名を何十年も使い続けます (反証検証の横断所見)。そのため既定は unknown とし、unknown のときは時間で候補を落としません。

### 6.2 有効判定 (実体 t、日付 d)

**started (開始済みか)**
1. from_lo と from_hi が両方 NULL → yes
2. from_hi があり、d ≥ from_hi → yes
3. from_lo があり、d < from_lo → no
4. それ以外 → unknown

**ended (終了済みか)**
1. status が current か remnant → no
2. to_hi があり、d ≥ to_hi → yes
3. to_lo があり、d < to_lo → no
4. それ以外 → unknown

**valid (有効か)**
- started=yes かつ ended=no → yes
- started=no または ended=yes → no
- それ以外 → unknown

### 6.3 候補

1. 入力に strip → canon → fold を当てます。
2. 同じ自治体で、town.key_canon か town_name.key_canon が一致した実体の集合を C、key_fold が一致した実体の集合を F とします。
   - redirect_to が設定された実体は、寄せた先に置き換えます。
   - 空のキー (大字名が空) は照合に使いません。
3. 各候補に照合レベルを付けます。
   - 'exact': 表記が NFC で完全に一致
   - 'canonical': canon キーが一致
   - 'folded': fold キーだけが一致
   - exact は並べ替えにだけ使います。
4. mode='authored' のときは、C のうち valid=no の実体を candidates の後ろに回し、valid_at_as_of='no' を付けます。
   - これは、valid が yes または unknown の実体が他に残る場合に限ります。
   - 全部が no なら、そのまま残します。区域表に残った旧町名の行は、普通こちらに当たります。

### 6.4 match_status と auto_pick

| C の数 | 条件 | match_status | auto_pick |
|---|---|---|---|
| 2 以上 | | ambiguous (NAME_REUSED または CANON_COLLISION) | false |
| 1 (= e) | F に e 以外が無い | unique | true |
| 1 (= e) | F に e 以外があり、e は丁目の束 (合成親) でない | preferred (FOLD_COLLISION) | true |
| 1 (= e) | F に e 以外があり、e は丁目の束 | ambiguous (FOLD_COLLISION) | false |
| 0 | F が 1 件 | unique (MATCH_FOLDED) | true |
| 0 | F が 2 件以上 | ambiguous | false |
| 0 | F が 0 件 | none (unresolved_query に記録) | false |

- ambiguous のときも candidates は全部返します。
- preferred を取込時の要確認に回すかどうかは、gomisute-gps が決めます。

### 6.5 kind

best の実体の状態から決めます。

| kind | 意味 |
|---|---|
| current | 現行の大字・丁目 |
| current_group | 丁目の束 (targets は丁目全部) |
| koaza | 現行の小字 |
| remnant | 法的に生きている残存 |
| old_name | 廃止された町名 |
| transitional | 短命の中間名 |
| provisional | 区画整理の仮称など (level=label で abolished) |
| uncertain | 存否が定まらない |

### 6.6 targets (どの現行町字に寄せるか)

| best の状態 | targets |
|---|---|
| current (束でない) | self (primary) |
| current の丁目の束 | 配下の現行の丁目すべて (group_member, primary)。小字は children として件数だけ返す |
| remnant | self (primary、幾何なし)。家族から出た移転の後継は context として付ける (primary=false) |
| uncertain の小字で、親が生きている | self (primary=false) と parent (primary) |
| uncertain (それ以外) | self (primary=false、POSSIBLE_REMNANT) と後継 (§6.8 の規則で primary) |
| abolished / label | 後継 (§6.8 の規則で primary) |

**後継の探し方**
1. best の家族 (自身と子孫) から、家族の外へ出る移転を集めます。merged_into のある事象と is_noise の移転は使いません (include_noise を指定すれば返します)。
2. 行き先が current / remnant / uncertain なら、そこで止めます。
   - 行き先が abolished なら、その家族から出る移転をさらにたどります。対象は、前の移転より日付が前でないものだけです。
   - 深さは最大 6 段で、循環は防ぎます。
   - 生きている町字に着いた後の部分移転はたどりません。問題の土地がそこに含まれていたか分からないためです。
3. 家族に移転が 1 本も無い死んだ小字 (入江町ヘ) は、親の家族の後継を使います。warning INHERITED_FROM_PARENT を付け、inherited_from に親の ID を入れます。
4. 同じ行き先に複数の経路があれば 1 件にまとめます。via_events に全事象を並べ、段階は最も新しいものを採ります。
5. share は、経路の最初の移転が best 自身から出ていて、全段に share_of_from がある場合だけ、積で返します (share_compounded=true)。子経由なら null です。
6. confidence は経路上の claim (移転と事象) の最小値、verification は経路上で最も弱い状態です。

### 6.7 段階 (stage)

経路の最初の移転の事象について、best (親の系譜を使ったときは親) を基準に、次の順で判定します。

1. end_event と同じ → final
2. bulk_event と同じ → bulk
3. end_event が無い場合で、最も遅い事象 L と区間が重なる (eff_hi ≥ L.eff_lo) → latest
4. bulk より後 → intermediate
5. それ以外 → earlier

### 6.8 primary (F8: 資料が書かれた時点でその名前だった土地)

```
alive   = best.status ∈ {current, remnant}
t*      = alive                                         → mode=authored なら as_of、それ以外は「なし」
          mode=authored かつ valid(best, as_of) ≠ no    → as_of
          それ以外                                       → best.to_lo (無ければ、mode=authored のとき as_of)
primary = t* があり、かつ最初の移転の eff_hi ≥ t*  (eff_hi が NULL なら真)
```

- 死んだ名前で primary が 1 件も無ければ、final / latest の後継を primary にし、NO_DATED_PRIMARY を付けます。
- 応答には t* と、各後継の left_name (最初の移転の eff_lo〜eff_hi) を入れます。gomisute-gps はこれで判断の根拠を確かめられます。
- to_lo は「名前が消えた可能性のある最も早い日」、つまり名前が使われていたと確認できた最後の日の翌日です。この値を調査どおりに入れると、寺地町の primary が調査の gomi_successors (久安4丁目・有松3丁目) と一致します (§10.3)。

### 6.9 自由文 (normalize)

1. **市区町村**: canon を当て、muni_name を前方一致で引きます。対象は、都道府県あり/なし、郡あり/なしの各形と旧名です。
   - 旧名に当たったら MUNI_FORMER_NAME と via_former を付けます。
   - 同じ名前が複数あれば MUNI_AMBIGUOUS とし、候補を返します。
   - 政令市を区なしで書いた場合は全区で照合し、同じ名前が複数の区にあれば WARD_AMBIGUOUS にします。
2. **町字**: 残りの文字列の先頭 1〜30 文字について canon キーと fold キーを作り、自治体パック (§7.4) の索引で最長一致を取ります。fold の一致が canon の一致より長いときだけ fold を採ります (「大字」を省いた入力のため)。
3. **丁目の推定**: 一致したのが丁目の束で、残りが「N-」か「N丁目」で始まり、その束に N丁目があれば、N丁目を採ります (CHOME_INFERRED)。
4. **残り**: 町字より後ろは rest として返します。番地・号の解析は v0.1 ではしません。旧町名に番地が続く場合は OLD_NUMBER_UNMAPPABLE を付けます。

### 6.10 confidence と warnings

**confidence**: auto_pick が true なら、min(best.confidence, primary な後継の confidence の最大値)。false なら 0 です。

**warnings** は次の固定語彙から付けます。
- 照合: AS_OF_UNKNOWN, NAME_REUSED, CANON_COLLISION, FOLD_COLLISION, MATCH_FOLDED, QUALIFIER_PART, CHOME_INFERRED
- 名前の種類: HISTORICAL_NAME, TRANSITIONAL_NAME, PROVISIONAL_LABEL
- 存否と幾何: POSSIBLE_REMNANT, REMNANT_NO_GEOMETRY, NOT_IN_ABR_BUT_LIVING, ABR_ROW_SUSPECT_DEAD, ABR_NO_PARCELS, PARCELS_UNCHECKED, PARENT_ALIVE
- 系譜と確信度: INHERITED_FROM_PARENT, NO_DATED_PRIMARY, END_UNDETERMINED, EVIDENCE_INSUFFICIENT, LOW_CONFIDENCE, SHARE_FROM_CENSUS, LINEAGE_NOT_RESEARCHED
- 市区町村と番地: MUNI_FORMER_NAME, MUNI_AMBIGUOUS, WARD_AMBIGUOUS, OLD_NUMBER_UNMAPPABLE

muni.lineage_level が abr_only の自治体で、結果が none か current 以外になったら LINEAGE_NOT_RESEARCHED を付けます。「見つからない」と「調べていない」を区別するためです。

---

## 7. API

### 7.1 Service Binding RPC (主な経路)

gomisute-gps の wrangler.jsonc:

```jsonc
"services": [{ "binding": "ADDR", "service": "address-normalize", "entrypoint": "AddressNormalizeV1" }]
```

- address-normalize の compatibility_date は 2024-04-03 以降にします (名前付き entrypoint の RPC に必要)。
- 互換性を壊す変更は AddressNormalizeV2 を足して行い、V1 は残します。
- 型は `worker/src/contract.ts` に置き、gomisute-gps と共有します。型だけなので、公開リポジトリに置いてもかまいません。

```ts
export class AddressNormalizeV1 extends WorkerEntrypoint<Env> {
  resolveLabels(req: ResolveLabelsRequest): Promise<ResolveLabelsResponse>;   // 区域表 1 枚 (≤ 5,000 行)
  resolveLabel(q: { lg_code: string; text: string; as_of?: AsOf; context?: Context }): Promise<LabelResolution>;
  normalize(text: string, opts?: { as_of?: AsOf; lg_code_hint?: string }): Promise<NormalizeResult>;
  getTown(town_id: string, include?: ("names"|"children"|"lineage"|"claims"|"census")[]): Promise<TownDetail | null>;
  muni(q: { lg_code?: string; lg5?: string; name?: string }): Promise<MuniRef[]>;
  changesSince(data_version: string): Promise<{ data_version: string; full_resync: boolean;
                                                changed: { town_id: string; change: string }[] }>;
  report(r: ReportInput): Promise<{ report_id: string }>;
  meta(): Promise<{ api_version: "1"; data_version: string; abr_snapshot: string; census_years: number[]; rules_version: string }>;
}
```

### 7.2 型 (contract.ts の要点)

```ts
type DateStr = string;                                      // 'YYYY-MM-DD'
type Prec = "day"|"month"|"year"|"decade"|"range"|"unknown";
interface Interval { lo: DateStr|null; hi: DateStr|null; best: DateStr|null; prec: Prec|null; note?: string }
interface AsOf { date: string; mode: "authored"|"unknown" }  // 既定 { 今日, 'unknown' }
type Context = "municipal_table"|"free_text";
type TownStatus = "current"|"remnant"|"abolished"|"uncertain";

interface TownRef {
  town_id: string; lg_code: string; name: string; level: "oaza"|"chome"|"koaza"|"label";
  status: TownStatus; status_auto: TownStatus;
  origin: "abr"|"abr_parent"|"research"|"census"|"abr_diff"|"user";
  abr_id: string|null; is_group: boolean; transitional: boolean;
  valid_from: Interval|null; valid_to: Interval|null;
  bulk_converted: Interval|null;                            // bulk_event の発効
  confidence: number; verification: string; flags: string[];
  numbering: "jukyo"|"chiban"|"mixed"|null; parcel_count: number|null;
  geo: { rep: { lon: number; lat: number; src: string; conf: number|null } | null;
         pos_code: string|null; post_code: string|null;
         census: { year: number; key_code: string; relation: string; share: number|null }[] };
}
interface EventRef {
  event_id: string; kind: string; label: string; eff: Interval & { basis: string };
  legal_ref: string|null; announce_date: string|null; confidence: number; verification: string;
}
interface Target {
  town: TownRef;
  role: "self"|"group_member"|"parent"|"successor"|"context";
  primary: boolean;
  stage?: "final"|"bulk"|"latest"|"intermediate"|"earlier";
  left_name?: { lo: DateStr|null; hi: DateStr|null };       // 土地がその名前を離れた時期
  via_events: EventRef[];
  via_child: string|null;                                    // 子経由 (寺地町 → 寺地町ニ)
  inherited_from: string|null;                               // 親の系譜を使った (入江町ヘ → P0607)
  portion: "whole"|"remainder"|"part"|"unknown"|null;
  share_of_from: number|null; share_basis: string|null; share_compounded: boolean;
  confidence: number; verification: string;
  also_listed?: boolean;                                     // resolveLabels: 同じ表に target 自身の行がある
}
interface Candidate {
  town: TownRef; match: "exact"|"canonical"|"folded"; matched_surface: string; surface_kind: string;
  valid_at_as_of: "yes"|"no"|"unknown";
}
interface LabelResolution {
  ref: string; input: string;
  parsed: { key_canon: string; key_fold: string; qualifiers: string[]; rest: string|null };
  match_status: "unique"|"preferred"|"ambiguous"|"none"; auto_pick: boolean;
  kind: "current"|"current_group"|"koaza"|"remnant"|"old_name"|"transitional"|"provisional"|"uncertain"|null;
  best: Candidate|null; candidates: Candidate[];
  t_star: DateStr|null; targets: Target[];
  children?: { count: number; items: TownRef[] };
  confidence: number; warnings: string[];
  lineage_level: "abr_only"|"abr_diff"|"census_diff"|"researched";
}
interface ResolveLabelsRequest {
  lg_code: string; as_of?: AsOf; context?: Context;
  labels: { ref: string; text: string; ward?: string }[];
  options?: { coverage?: boolean; include_noise?: boolean; include_geo?: "none"|"summary" };
}
interface Coverage {
  universe: number;            // 生きている町字の数 (束は丁目単位で数え、小字は除き、残存の大字は含む)
  uncovered: TownRef[];        // どのラベルの self / group_member / primary にもならない町字
  only_via_old_names: { town: TownRef; via: { ref: string; label: string; share: number|null }[] }[];
}
interface ResolveLabelsResponse {
  api_version: "1"; data_version: string; results: LabelResolution[]; coverage?: Coverage;
}
interface NormalizeResult extends LabelResolution {
  muni: { lg_code: string; name: string; match: "official"|"official_short"|"former";
          via_former?: { name: string; event_kind: string; eff_date: string|null } } | null;
  muni_candidates: { lg_code: string; name: string }[];
}
```

- 入力の形式が不正なとき (lg_code の桁違いなど) は TypeError を投げます。
- 「見つからない」「曖昧」は例外にせず、match_status で返します。

### 7.3 HTTP (RPC と同じ型、読み取り専用)

| メソッド | パス | 対応する RPC |
|---|---|---|
| GET | /v1/labels/resolve?lg=172014&text=木ノ新保7番丁&as_of=2025-04-01&as_of_mode=authored | resolveLabel |
| POST | /v1/labels/resolve (body は ResolveLabelsRequest) | resolveLabels |
| GET | /v1/normalize?q=石川県金沢市窪町ロ12 | normalize |
| GET | /v1/towns/{town_id}?include=names,lineage,claims | getTown |
| GET | /v1/munis?lg=172014 / ?lg5=17201 / ?name=鶴来町 | muni |
| GET | /v1/changes?since={data_version} | changesSince |
| GET | /v1/meta | meta |
| POST | /v1/reports | v0.1 では 403 (RPC でだけ受け付ける) |

- **キャッシュ**: GET には `ETag: <data_version>` と `Cache-Control: public, max-age=86400` を付けます。Cache API のキーには data_version を含めます。
- **レート制限**: 公開アクセスには、Workers Rate Limiting で IP ごとの上限をかけます。
- **エラー**: 形は `{ "error": { "code": "BAD_REQUEST"|"MUNI_NOT_FOUND"|"TOO_MANY_ITEMS"|"DATASET_UNAVAILABLE", "message": "..." } }`、ステータスは 400 / 404 / 413 / 503 です。
- **出典表示**: すべての応答に data_version と出典 (ABR、e-Stat、geolonia CC BY 4.0) を付けます。

### 7.4 実装 (D1 の制約への対応)

- **自治体パック**
  - lg_code ごとに、town (必要な列)、town_name、その自治体の change_event と event_transfer、town_census を、4〜5 文の batch で読みます。
  - 読んだ結果は isolate 内の LRU に、(data_version, lg_code) をキーとして置きます。規模は金沢で約 6,900 行、最大の福井市で 15,518 行です。
  - 照合と系譜の探索はメモリ上で行い、再帰 CTE は使いません。
- **バインド変数の上限**: IN 句の値は `json_each(?)` で渡し、100 個の上限を避けます。
- **市区町村表**: muni と muni_name (約 1 万行) は起動時に 1 回だけ読みます。
- **未解決の記録**: 照合が解決しなかったときは、ctx.waitUntil で ops D1 の unresolved_query に加算します。
- **ホットパス**: gomisute-gps の GPS → /resolve の経路では address-normalize を呼びません。経路は gps2city (lg_code・ward) → 収集地区タイル → 境界距離による confidence です。gomisute-gps は区域表の取込時に address-normalize で解決し、town_id を保存しておきます。

### 7.5 応答例

`resolveLabel({lg_code:"172014", text:"木ノ新保7番丁"})`、as_of は省略。TownRef は省略形で示します。

```json
{"ref":"q","input":"木ノ新保7番丁",
 "parsed":{"key_canon":"木ノ新保7番丁","key_fold":"木の新保7番丁","qualifiers":[],"rest":null},
 "match_status":"unique","auto_pick":true,"kind":"old_name",
 "best":{"town":{"town_id":"172014-X00001","name":"木ノ新保七番丁","level":"oaza","status":"abolished","status_auto":"uncertain",
    "origin":"research","abr_id":null,"valid_from":{"lo":null,"hi":"2000-10-01","best":null,"prec":"unknown"},
    "valid_to":{"lo":"2006-08-12","hi":"2006-08-12","best":"2006-08-12","prec":"day"},
    "confidence":0.85,"verification":"confirmed",
    "geo":{"rep":null,"pos_code":null,"post_code":null,
           "census":[{"year":2005,"key_code":"172010640","relation":"equal","share":null}]}},
   "match":"canonical","matched_surface":"木ノ新保7番丁","surface_kind":"doc_label","valid_at_as_of":"no"},
 "t_star":"2006-08-12",
 "targets":[
  {"town":{"town_id":"172014-0157000","name":"木ノ新保町","status":"current"},"role":"successor","primary":true,
   "stage":"final","left_name":{"lo":"2006-08-12","hi":"2006-08-12"},
   "via_events":[{"event_id":"ev:172014:2006-08-12:kz181","kind":"kukaku_seiri","label":"金沢駅北土地区画整理 換地処分",
      "eff":{"lo":"2006-08-12","hi":"2006-08-12","best":"2006-08-12","prec":"day","basis":"koukoku_plus_1"},
      "legal_ref":"石川県告示第432号","announce_date":"2006-08-11","confidence":0.9,"verification":"confirmed"}],
   "via_child":null,"inherited_from":null,"portion":"part","share_of_from":0.636,"share_basis":"census_area:2005->2010",
   "share_compounded":false,"confidence":0.85,"verification":"confirmed"},
  {"town":{"town_id":"172014-0451000","name":"堀川新町","status":"current"},"role":"successor","primary":true,
   "stage":"final","portion":"part","share_of_from":0.357,"share_basis":"census_area:2005->2010",
   "confidence":0.85,"verification":"confirmed"}],
 "confidence":0.85,"warnings":["AS_OF_UNKNOWN","HISTORICAL_NAME","SHARE_FROM_CENSUS"],"lineage_level":"researched"}
```

---

## 8. gomisute-gps での使い方

1. **区域表の解析**: 区域表を解析し、行ごとに {ref, text, 街区・番地の範囲, district} を作ります (kz_rules.py の出力)。
2. **一括解決**: 表 1 枚につき resolveLabels を 1 回呼びます。金沢の 1,776 行なら 1 回で済みます。
   - context='municipal_table'
   - as_of: 公表日が分かれば {date, 'authored'}、分からなければ省略
   - coverage=true
3. **結果の扱い (kind ごと)**
   - **current / koaza**: zone member = best.town_id。番地の範囲は numbering で振り分けます。
     - jukyo: pos_code で GSI の住居表示点に当てる
     - chiban: 地番から筆ポリゴンに当てる (gomi 側の第 3 層)
     - mixed: 両方を試す
   - **current_group** (泉野町 → 1〜6丁目): group_member を全部入れます。
   - **old_name / provisional / transitional**: primary な targets を member の候補にします。
     - also_listed=true の町は自分の行を優先し、旧町名の規則は「旧町に由来する部分」にだけ当てます。
     - その部分は、best.town.geo.census の最後の小地域ポリゴンと現行のポリゴンを重ねて切り出します。2000 年より前の名前は切り出せないので unresolved_partial にします。
     - also_listed=false の町 (木ノ新保町) は、旧町名の規則が町全体に効く可能性があります。
   - **remnant / uncertain の self**: 幾何が無いので、例外 unresolved_partial にします (HANDOFF の例外型④)。低信頼の代表点と筆数を添えます。
   - **ambiguous / none**: 自動では入れません。gomi のレビューに積み、report(kind='unresolved_label') を送ります。
4. **gomi_successors の移し先**: 調査レコードにあった gomi_successors は address-normalize には入れず、gomisute-gps の選択規則のテストの期待値に移します。
   - 例: 入江町 → 米丸 の行では、primary 3 件 (入江2・3丁目、新神田1丁目) のうち地区が合う入江3丁目を選ぶ。
   - 例: 窪町 → 富樫 の行では、context の窪1・2・6丁目が地区の整合で残る。
5. **coverage の扱い**
   - **uncovered**: 区域表の「別記以外」という既定の規則で埋めるか、人が見ます。
   - **only_via_old_names**: 地区の食い違いに注意します (例: 木ノ新保町は 玉井町 → 芳斎、木ノ新保7番丁 → 此花、南安江町 → 此花 から入る)。境界付近の confidence を下げます。
6. **更新への追従**: 結果は data_version と一緒に保存します。毎月 changesSince を呼び、変わった town_id を含む行だけを再解決します。full_resync=true なら全部を再解決します。

---

## 9. 取込と更新

### 9.0 構成

```
address-normalize/
  worker/    TS (src/index.ts, resolve.ts, keys.ts, contract.ts, test/)
  builder/   Python (muni.py, abr.py, census.py, research.py, invariants.py, publish.py)
             ← gomisute-gps の abr_check.py / census_lineage.py / townnorm.py を移す
  rules/     rules.json, golden.tsv
  research/  {lg_code}/{town_id}.json   (調査レコード v2)
  registry/  town_ids.jsonl             (X ID の払い出し台帳)
  schema/    0001_data.sql, 0001_ops.sql
```

- 生データは R2 の `raw/{source}/{date}/` に sha256 つきで置きます。v0.1 ではローカルの data/ でもかまいません。
- 前回のビルドの ABR 状態 (行ハッシュ、first_seen / last_seen) は、`state/abr_state.sqlite` として R2 に置きます。

### 9.1 市区町村

1. ABR mt_city_all (1,918 行) から muni を作ります。lg5 は先頭 5 桁で、全国で一意です。政令市の区には parent_lg_code を入れます。
2. muni_name に official (都道府県 + 郡 + 市 + 区、郡なし、区だけ) と official_short を入れます。
3. address2houmukyoku の municipality-aliases.json を、環境変数 A2H_PATH のローカルパスから読みます。
   - old_full は muni_name (kind former) にします。
   - new_full は muni_name で現行の lg_code に解決し、その後にまた合併していれば連鎖をたどります。解決できない行は ingest_run.stats に出します。
   - claim は basis=external_dataset、0.8 unverified。ABR mt_city で行き先が一意に決まれば 0.9 machine にします。
4. この JSON は公開リポジトリにも R2 にも置きません。D1 に入るのは結果 (旧名 → lg_code) だけです。2024 年の浜松市の区再編のように a2h に無いものは、調査レコードで足します。

### 9.2 ABR (初回と月次)

**取得するもの**
- mt_city_all、mt_town_all (fullset)、mt_town_pos_pref01〜47。
- mt_parcel_city は筆数の集計にだけ使います。v0.1 は金沢で、続いて幽霊大字を持つ 361 自治体です。
- sha256 が前回と同じなら何もしません。

**組み立て** (全国で 30 秒程度。§1.2 の数値はこの手順で出しました)
1. type 4/5 を除き、(lg_code, machiaza_id) ごとに 1 実体にします。rsdt_addr_flg が 0 と 1 の両方あれば numbering=mixed です。
2. efct_date の 1947-04-17 と 1970-01-01 は NULL にします。実日付は abr_efct に入れ、from_lo / hi / best にも入れます (basis abr_efct)。
3. 大字コード (先頭 4 桁) ごとに親を決めます。
   - type1 の行があれば、それを親にする。
   - 大字コード 0000 (大字名が空) は親を作らない (flag no_oaza)。
   - 丁目がある場合は、合成親 P (丁目の束、status current) を作る。
   - 小字しか無い場合は、大字名の canon キーが同じ自治体の既存の町字と一致すれば、その町字を親にする (flag phantom_adopted)。一致しなければ合成親 P (幽霊大字、flag phantom) を作る。
   - 丁目のついた小字は、その丁目の行を親にする。
4. level を決めます。
   - type1 → oaza、type2 → chome、type3 → koaza
   - 大字名が `(土地)?区画整理(事業)?(地|地内|区域)?$` に合えば label (flag label_rule)
5. status_auto を次の表で決めます。

| 行 | status_auto | claim (確信度) |
|---|---|---|
| type1/2 | current (status_flg=0 なら abr_unconfirmed、筆 0 の地番区域なら no_parcels を付ける) | ABR 一括 (0.9) |
| 幽霊大字の小字、筆あり | current | phantom_parcels (0.8) |
| 幽霊大字の小字、筆 0 | uncertain + no_parcels | no_parcels (0.3) |
| 幽霊大字の小字、筆が未集計 | uncertain + parcels_unchecked | phantom_unchecked (0.5) |
| 通常の小字、筆 0 かつ status_flg=0 | uncertain + no_parcels | no_parcels (0.3) |
| 通常の小字 (それ以外) | current | ABR 一括 (0.9) |
| 合成親 (丁目の束) | current | group_parent (0.9) |
| 合成親 (幽霊大字) | 筆のある子があれば remnant、無ければ uncertain | group_parent |

6. 代表点と位置参照コード (mt_town_pos)、郵便番号、筆数を付けます。
7. 前回の状態と、(lg_code, machiaza_id, rsdt_addr_flg) の単位で比べます。
   - **追加**
     - 新しい実体を作り、abr_first_seen = 今回とします。
     - from_lo = 前回の翌日、from_hi = 今回 (basis abr_snapshot_window)。実日付があればそちらを優先します。
     - start_event に abr_observed の事象を付け、research_queue に abr_added を入れます。
     - 同じ月・同じ自治体で消えた行があれば組にして detail に書きます。系譜には入れません。
   - **削除**
     - 行は消さず、abr_presence=gone とし、abr_last_seen は止めます。
     - status_auto=uncertain にします。廃止にはしません (F2)。
     - to_lo = 最後に見えた日の翌日。
     - research_queue に abr_removed を入れます。
   - **同じ ID で名前が変わった**
     - canon キーが同じなら、旧表記を legacy_spelling として town_name に残します。
     - 違えば、旧名を X の実体 (origin abr_diff) にし、rename の事象 (machine 0.7) を作り、research_queue に abr_renamed を入れます。
   - **住居表示フラグや郵便番号だけの変化**: change_log に記録し、research_queue に低い優先度で入れます (住居表示実施の兆候のため)。
   - **lg_code が変わった (合併)**: v0.1 では research_queue に入れるだけです。
8. 初回 (2026-10) は比べる相手が無いので、全行が「この日以前から存在」になります。

### 9.3 国勢調査 (5 年ごと)

gomisute-gps の census_lineage.py を一般化して使います。v0.1 は金沢だけです (2000〜2020 年分は手元にあります)。

1. **名前の対応**: HCODE 8101 だけを使い、8154 (水面) は除きます。S_NAME を canon / fold で town に当て、town_census (年, KEY_CODE, relation) を作ります。
   - KEY_CODE は年ごとに意味が違うので、年つきで持ちます。
   - 例: 観音町は 2015 年が 172015700、2020 年は 17201570000「観音町三丁目」。
2. **消えた名前**: 年 A にあって年 A+5 に無い名前について、A と A+5 のポリゴンを重ねて share_of_from / share_of_to を出します。
   - change_event (census_observed、eff A-10-02〜(A+5)-10-01、basis census_window) と event_transfer を作ります。
   - 2% 未満の欠片は is_noise にします。
3. **当たる実体が無い名前**: registry で X を払い出し、town (origin census、status uncertain) を作って、research_queue に census_gone を入れます。
   - 国勢調査だけでは abolished にしません。小さな町は隣の小地域に合算されることがあるためです (南安江町・玉井町)。
4. **調査との統合**: 調査レコードが同じ変化を説明したら、国勢調査の事象を merged_into で統合し、面積比を調査側の移転に写します。

### 9.4 調査レコード (Claude の調査 + 反証検証)

正本は research/{lg}/{town_id}.json です。old_town_lineage.json の「abolished_on 1 個 + 後継リスト」を、事象単位に分けた v2 形式にします。

```json
{"town_id":"172014-X00003","lg_code":"172014",
 "town":{"name":"寺地町","level":"oaza","status":"abolished",
   "valid_to":{"lo":"1979-07-01","hi":"2000-10-01","best":"1990-11-07","prec":"range","note":"1990年頃 (地図の隣接から推定)"},
   "bulk_event":"ev:172014:1979-06-30:kz115","end_event":"ev:172014:1990-11-07:kz147",
   "claim":{"confidence":0.45,"verification":"corrected","conf_before":0.5,"evidence":["e2","e3"],
            "open_questions":"1990 は地図の隣接から推定。ハの帯の行方は未確認"}},
 "children":[{"town_id":"172014-X00004","name":"寺地町ニ","level":"koaza",
              "names":[{"surface":"寺地町ニノ部","kind":"map_label"}]}],
 "events":[{"event_id":"ev:172014:1990-11-07:kz147","kind":"kukaku_seiri","label":"久安第二 換地処分",
   "announce_date":"1990-11-06","eff":{"lo":"1990-11-07","hi":"1990-11-07","prec":"day","basis":"koukoku_plus_1"},
   "claim":{"confidence":0.85,"verification":"confirmed","evidence":["e1"]},
   "transfers":[{"from":"172014-X00004","to":"172014-0412004","portion":"whole","koaza_detail":"寺地町ニノ部",
                 "claim":{"confidence":0.45,"verification":"corrected","evidence":["e3"]}}]}],
 "evidence":{"e1":{"source":"kz:kukaku_list","stance":"supports","finding":"(要約)"},
             "e3":{"source":"kz:kanchizu:147","stance":"supports","observed_on":"1990-11-07",
                   "locator":"久安四丁目の東端","finding":"(要約)"}},
 "reviews":[{"reviewer":"claude:adversarial:2026-10-16","verdict":"corrected","note":"1979-06 は大半の転換日で廃止日ではない"}]}
```

**手順**
1. research_queue の優先度順に、Claude が調べます。見る資料は、換地図 PDF (画像化してラベルを読む)、区画整理一覧の公告日 (+1 日)、旧町名一覧、県公報の告示番号、日本郵便、navbox の ※、geolonia、市史年表です。
2. 別の Claude が反証検証をし、confirmed / corrected / insufficient を付けます。corrected の場合は訂正後の値を採り、訂正前の確信度を conf_before に残します。
3. 人が差分を確認してから git にマージします。

**取込時の検査**
- JSON スキーマに合っていること。
- 移転の行き先は、town_id か、一意に決まる canon キーで指定されていること。曖昧なら取込を失敗させます。
- status を上書きするレコードは、証拠を 1 件以上持つこと。
- 同じ告示・同じ事業の工区は、同じ event_id を共有すること。たとえば 1965-09-01 の芳斎校下の住居表示は、島田町・玉井町・柳町で 1 件です。
- evidence.finding は自分の言葉での要約にし、原文は転載しないこと。Wikipedia (CC BY-SA) も URL と要約だけを持ちます。

**確信度の目安**

| 由来 | confidence | verification |
|---|---|---|
| 告示本文を人が確認 | 0.95 | primary_confirmed |
| 告示番号つきの二次資料があり、換地図か国勢調査とも整合 | 0.9 | confirmed |
| 公式一覧か、告示番号つきの二次資料のどちらか一方 | 0.8 | researched (反証後に confirmed) |
| 公式資料からの推論 (事業の町名索引 + 地図) | 0.6 | researched |
| 地図の隣接関係だけ | 0.45 | researched |
| 未検証の推定 | 0.3 | researched |
| 国勢調査差分 (Σshare ≥ 0.95 かつ名前が実体に一致) | 0.7 | machine |
| 国勢調査差分 (その他) | 0.5 | machine |
| address2houmukyoku (LLM 抽出) | 0.8 (ABR で一意に決まれば 0.9) | unverified / machine |
| 利用者報告 (採用前) | 0.3 | unverified |

**初回の投入**
- old_town_lineage.json の 19 件 (verdict の corrected_record を優先) と、census_lineage.json の 10 件を v2 形式に変換します。
- 国勢調査の 4 件 (北安江町・西念町・木の新保7番丁・西堀川町) は、調査事象に統合します。
- 中間名 (神田町5丁目、入江町イ〜ホ) と残存帯 (寺地町ニ・ハ) を実体として起こします。
- gomi_successors は取り込みません (§8)。

### 9.5 利用者報告・未解決クエリ

- gomisute-gps の画面から report() の RPC を呼び、ops D1 の report に入れます。Turnstile は gomi 側で行います。番地以降と連絡先は保存せず、重複検知用の HMAC だけを持ちます。
- none / ambiguous / uncertain になった (lg_code, key_canon) は unresolved_query に加算します。
- 週に 1 回、Claude と人がトリアージします。採用した報告は調査レコード (basis user_report、0.3 unverified) として §9.4 の手順を通ります。報告がデータを直接変えることはありません。

### 9.6 ビルド・検査・公開

1. builder が全入力から、ローカルの SQLite (schema/0001_data.sql) を組み立てます。
2. **不変条件**: 1 つでも破れたら公開しません。
   - 外部キー違反が 0 件。ID の形が正しい (CHECK)。
   - current / remnant は to_* と end_event を持たない (CHECK)。abolished は to_lo か end_event を持つ。
   - end_event / bulk_event は、その町字の家族から出る移転を持つ。
   - 同じ事象・同じ from の share_of_from の合計が 1.05 以下。
   - 移転のグラフに循環が無い。移転の発効が from の from_lo より前でない。
   - abolished の町字は、生きている町字にたどり着くか、research_queue に successor_unknown がある。
   - 一括でない claim は、evidence を 1 件以上持つ。confidence ≥ 0.8 の research claim は、reliability が official 以上の資料を持つか、indep_group の異なる 2 つの資料を持つ。
   - 現行の町字が、前の版より 0.5% 以上減っていない。
   - §10 の全ケースのゴールデンテストと、rules の golden.tsv が、Python と TS の両方で通る。
3. **警告**: 公開は止めず、research_queue に入れます。
   - usage_conflict: evidence.observed_on (使用例の日付) が from_lo より前、または to_hi より後 (窪5丁目の 1974 年の使用と 1979 年の窪第一)。
   - 衝突の監査: 前の版より増えた canon / fold の衝突。
4. change_log は、前の版の町字ごとの内容ハッシュと比べて作ります。
5. **公開 (blue/green)**
   1. `sqlite3 build.sqlite .dump` から BEGIN / COMMIT と PRAGMA を取り除き、先頭に `PRAGMA defer_foreign_keys = true;` を付けます。表の作成順 (change_event が town より先) に投入されるので、参照先が先に入ります。
   2. 使っていない側の D1 (addrnorm_a / addrnorm_b) を空にし、`wrangler d1 execute <db> --remote --file=dump.sql` で流し込みます (全国で約 300MB)。
   3. その D1 にバインドした preview の Worker で、金沢の区域表 1,776 行を一括で解決し、前の版と結果が変わった行を一覧にします。
   4. wrangler.jsonc のバインディングを切り替えて `wrangler deploy` します。ロールバックは前の Worker の版に戻すだけです (前の D1 は残っています)。D1 の Time Travel も使えます。
   5. dataset_version を書きます。gomisute-gps は changesSince で追従します。
6. 全国の初回投入は、本体と索引で数百万行の書込みになり、無料枠 (1 日 10 万行) を超えます。そのため Workers Paid が前提です。金沢だけなら無料枠に収まります。

### 9.7 鮮度の運用

| 対象 | 頻度 | 動き |
|---|---|---|
| ABR | 月次 (GitHub Actions の cron で sha256 を比較。v0.1 は手動) | 変化があればビルドし、abr_added / abr_removed を research_queue へ |
| 筆数 (mt_parcel) | 四半期 | 幽霊大字を持つ 361 自治体から集計し、uncertain (parcels_unchecked) を減らす |
| 国勢調査 | 公表時 (2025 年調査の小地域境界) | 差分を追加 |
| 調査レコード | マージのたび | ビルドして公開 |
| 報告・未解決クエリ | 週次でトリアージ | 調査キューへ |
| 公開 | 入力が変わったとき、最低でも月 1 回 | data_version を更新 |

調査の優先度は、次の順です。
1. gomisute-gps の区域表で none / ambiguous になった名前
2. ABR の増減
3. census_gone
4. low_confidence

---

## 10. ケース別の表現

**共通事項**
- lg_code は 172014 (金沢市) で、ID の頭の「172014-」は省きます。
- 「公告+1」は、区画整理一覧の換地処分公告日の翌日です。
- 結果は、特に書かない限り as_of を省略した場合 (mode unknown) のものです。
- ここに書いた行は、§1.2-11 の検証で DDL に入ることを確かめ、結果も検証器の出力どおりです。

### 10.1 木ノ新保7番丁 (2 つの町に分割。国勢調査 + 調査)

**town**
- X00001
  - level oaza、origin research、name 木ノ新保七番丁
  - key_canon 木ノ新保7番丁、key_fold 木の新保7番丁
  - status abolished (status_auto uncertain)、abr_presence absent
  - from: lo NULL、hi 2000-10-01 (2000 年国勢調査に載っている)、prec unknown
  - to: lo = hi = best 2006-08-12、prec day
  - bulk_event = end_event = E181
  - status_claim: 0.85 confirmed (reviewed_by claude:adversarial:2026-10-16)

**town_name**
- 木の新保七番丁 (census、used 2000〜2005)
- 木ノ新保7番丁 (doc_label、ごみ区域表)

**change_event**
- E181 `ev:172014:2006-08-12:kz181`
  - kukaku_seiri「金沢駅北土地区画整理 換地処分」
  - project_ref kz-kukaku-181 (市施行、11.92ha)
  - legal_ref 石川県告示第432号、announce 2006-08-11
  - eff 2006-08-12 (day、koukoku_plus_1)、0.9 confirmed
- `ev:172014:census:2005-2010:木の新保七番丁`
  - census_observed、eff 2005-10-02〜2010-10-01 (census_window)
  - merged_into E181

**event_transfer (E181)**
- X00001 → 0157000 木ノ新保町: part、share_of_from 0.636 / share_of_to 0.694、census_area:2005->2010、0.85 confirmed
- X00001 → 0451000 堀川新町: part、0.357 / 0.215、0.85 confirmed
- X00001 → 0247000 昭和町: part、0.007、is_noise=1、0.5 machine

**town_census**
- (2000, 172010640, 木の新保七番丁, equal)
- (2005, 172010640, 木の新保七番丁, equal)

**evidence (status claim)**
- 区画整理一覧 (公告日): supports
- 換地図181: strong。事業区域の外に七番丁の帯が無い
- 国勢調査: strong。2005 年に 56,144㎡
- 旧町名一覧: context。一〜四・六番丁だけが載っており、七番丁は 1965 年の住居表示では消えていない
- ABR・日本郵便・navbox・geolonia に無い: weak

**解決結果**
- 「木ノ新保7番丁」(区域表)
  - canonical で X00001 の doc_label に一致。unique / old_name / t* 2006-08-12
  - targets: 木ノ新保町 (successor、final、primary、0.636)、堀川新町 (successor、final、primary、0.357)
  - warnings: AS_OF_UNKNOWN、HISTORICAL_NAME、SHARE_FROM_CENSUS
- 「木の新保七番丁」(国勢調査の表記): canonical で同じ結果。
- ABR に残る死んだ旧行 (木ノ新保 + 小字 四番丁 0613102、親は合成親 P0613) は、キーが 木ノ新保4番丁 なので混ざりません。
- coverage: 木ノ新保町は区域表に自分の行が無いので only_via_old_names に入ります (← 木ノ新保7番丁・玉井町・南安江町)。堀川新町は also_listed=true です。

### 10.2 柳町 (段階的に消えた。証拠が足りない)

**town**
- X00006
  - oaza、status uncertain
  - 調査の判定は insufficient。最も確からしい読みは「2000 年までに廃止、最後の記録は 1993-01-09」だが、残存を否定できない
  - to: lo 1993-01-09、hi NULL、prec unknown
  - validity_note「1993-01-09 の部分移転より後の不明な日。残存も否定できない」
  - bulk_event、end_event とも NULL
  - status_claim: 0.5 insufficient。open_questions「石川県告示第4号の本文、2006 年換地図の隣接ラベル」

**change_event**
- Ea `ev:172014:1965-09-01:jukyo-hosai`
  - jukyo_hyoji「芳斎校下 住居表示」、eff 1965-09-01 (official_list = 旧町名一覧の S40.9.1)
  - 0.8 confirmed。島田町・玉井町と同じ事象
- Eb `ev:172014:1992-06-10:kz172`
  - chiku_henko「昭和町地区 町区域変更」、石川県告示第322号、legal_ref_secondary
  - 0.7 researched。label に「換地処分公告 (1998-08-31) より前」と書く
- Ec `ev:172014:1993-01-09:kz171`
  - kukaku_seiri「金沢駅南 換地処分」、石川県告示第4号、announce 1993-01-08、koukoku_plus_1
  - 0.8 confirmed

**event_transfer (すべて part)**
- Ea: X00006 → 0455001 本町1丁目 (0.8)、X00006 → 0247000 昭和町 (0.8)
- Eb: X00006 → 0247000 昭和町 (0.6 researched)
- Ec: X00006 → 0247000 昭和町 (0.6 researched)

**evidence**
- 旧町名一覧: supports
- Wikipedia 昭和町 (告示番号つき): supports
- Wikipedia 柳町 の stub: contradicts。tertiary で、出典なしに現行地名としている
- 換地図171: context。隣接ラベルに柳町が無い
- ABR・郵便番号・2000 年以降の国勢調査・navbox 2021 に無い: weak
- 校区表とごみ区域表: context、weak。indep_group kz:legacy_town_list が同じなので 1 票として数える
- research_queue: low_confidence

**解決結果**
- unique / uncertain / t* 1993-01-09
- targets:
  - 柳町 self (primary=false、幾何なし)
  - 昭和町 (successor、latest、primary。via は Ea / Eb / Ec)
  - 本町1丁目 (earlier、primary=false)
- warnings: POSSIBLE_REMNANT、END_UNDETERMINED、EVIDENCE_INSUFFICIENT
- 昭和町は区域表に自分の行があるので also_listed=true。gomi は自分の行を優先し、柳町の行は 1993 年分の帯に当たる unresolved_partial として扱います。

### 10.3 寺地町 (大半が移った日と、名前が最後に消えた日が違う)

**town**
- X00003 寺地町
  - oaza、abolished
  - to: lo 1979-07-01 (1979 年の換地図に帯が残っている)、hi 2000-10-01 (2000 年国勢調査に無い)、best 1990-11-07、prec range
  - note「1990年頃 (地図の隣接から推定)」
  - bulk_event E79、end_event E90
  - status_claim: 0.45 corrected (conf_before 0.5。1979-06 は大半の転換日であって廃止日ではないと訂正)
- X00004 寺地町ニ
  - koaza、parent X00003、abolished
  - to: lo 1979-07-01、hi 2000-10-01、best 1990-11-07
  - end_event E90、0.45 corrected
  - town_name 寺地町ニノ部 (map_label、換地図115)
- X00005 寺地町ハ
  - koaza、parent X00003、abolished
  - to: lo 1979-07-01、hi 2000-10-01、best 1982-06-01
  - end_event E82、0.3 researched (未確認)
  - town_name 寺地町ハノ部

**change_event (いずれも day、koukoku_plus_1)**
- E79 `ev:172014:1979-06-30:kz115` 寺地第一 (公告 1979-06-29)、0.85 confirmed
- E82 `ev:172014:1982-06-01:kz129` 有松第一 (公告 1982-05-31)、0.8
- E90 `ev:172014:1990-11-07:kz147` 久安第二 (公告 1990-11-06)、0.85 confirmed

**event_transfer**
- E79: X00003 → 0320001 寺地1丁目、0320002 寺地2丁目、0432001 伏見台1丁目、0432002 伏見台2丁目 (part、0.6 confirmed)
- E90: X00004 → 0412004 久安4丁目 (whole、koaza_detail 寺地町ニノ部、0.45 corrected)。根拠は、換地図147 で久安4丁目の東端が帯の位置まで延びていること
- E82: X00005 → 0018003 有松3丁目 (whole、0.3 researched)

**解決結果**
- 「寺地町」(as_of unknown): unique / old_name / t* 1979-07-01
  - primary: 久安4丁目 (final、via_child 寺地町ニ、0.45)、有松3丁目 (intermediate、via_child 寺地町ハ、0.3)
  - primary でない: 寺地1・2丁目、伏見台1・2丁目 (bulk、1979)
  - これは調査レコードの gomi_successors (久安4丁目・有松3丁目) と一致します。
- as_of {1985-04-01, authored}: t* 1985-04-01 で、primary は久安4丁目だけになります。ハの帯は 1982 年に有松3丁目になっていたためです。{1981-04-01, authored} なら両方が primary です。
- 区域表の実際の行「寺地町 / ニ → 三馬」(= 寺地町ニ): X00004 に一致し、久安4丁目 (final、primary)。三馬は久安4丁目の地区なので整合します。
- 「寺地町ニノ部」: strip で同じ実体に当たり、qualifiers は [ノ部]。
- 「寺地」: 丁目の束 P0320 (寺地1・2丁目) で、別の実体です。「町」は畳みません。

### 10.4 窪町 (筆のある幽霊大字)

**ABR の小字** (9 行。すべて type3、status_flg 0、rsdt 0、efct はプレースホルダ)

| ID | 小字 | 筆 | status_auto |
|---|---|---|---|
| 0164102 | イ | 8 | current |
| 0164104 | チ | 2 | current |
| 0164105 | ト | 2 | current |
| 0164106 | ニ | 0 | uncertain (no_parcels) |
| 0164108 | ハ | 1 | current |
| 0164109 | ハバケ | 3 | current |
| 0164110 | ロ | 11 | current |
| 0164123 | 白金 | 0 | uncertain (no_parcels) |
| 0164126 | 六才 | 4 | current |

**town**
- P0164 窪町
  - origin abr_parent、flags [phantom]、abr_presence via_children、numbering chiban、parcel_count 31
  - status_auto remnant。調査で status remnant を裏づける (status_claim 0.6 corrected。廃止日の精度を 1979-03 から 1970 年代に訂正)
  - from: lo = hi = best 1935-12-16 (富樫村の編入で大字窪が窪町になった。secondary)
  - 代表点: geolonia 136.652698, 36.514226 (rep_conf 0.3)。窪3丁目のポリゴンの中にあり、ごみ規則の地区 (富樫) と合わないので research_queue に入れる

**change_event**
- Ek1 `ev:172014:x:kubo-chome`
  - unknown「窪1・2・3・6丁目の設定 (仕組み不明)」
  - eff lo 1970-01-01、hi 1979-03-03、prec decade、basis usage_bounds
  - 0.5 researched
- Ek2 `ev:172014:1979-03-03:kz114`
  - 窪第一 換地処分 (公告 1979-03-02)、day
  - 0.8 confirmed

**event_transfer (すべて part)**
- Ek1: P0164 → 0163001 窪1丁目、0163002 窪2丁目、0163003 窪3丁目、0163006 窪6丁目 (0.5)
- Ek2: P0164 → 0163004 窪4丁目、0163005 窪5丁目、0163007 窪7丁目、0432003 伏見台3丁目、0412001 久安1丁目 (0.6)

**検査で拾う矛盾**
- 市史年表が 1974-09-02 に「窪5丁目」を使っている (evidence.observed_on) のに、窪5丁目を作った Ek2 は 1979 年です。
- usage_conflict として research_queue に入れます。昭和町と同じく、町区域の変更が換地処分より先だった可能性があります。

**解決結果**
- 「窪町」(区域表 → 富樫)
  - canonical で P0164。unique / remnant
  - targets: 窪町 self (primary、幾何なし。代表点 0.3、筆 31、children 9 行)、窪1〜7丁目・伏見台3丁目・久安1丁目 (context、primary=false)
  - warnings: REMNANT_NO_GEOMETRY
  - 調査の gomi_successors (窪1・2・6丁目 = 富樫) は、gomi 側が地区の整合で選びます (§8)。
- 「窪町ロ」: 0164110 (koaza、current、筆 11、位置参照なし)。normalize で「金沢市窪町ロ12」と引けば rest は "12"。
- 「窪町ニ」: 0164106 (uncertain、筆 0)。親が生きているので、targets は self (primary=false) と parent の窪町 (primary)。warnings: ABR_NO_PARCELS、PARENT_ALIVE。

### 10.5 入江町ヘ (中身の無い旧行)

**town**
- 0607101 入江町ヘ
  - origin abr、koaza、parent P0607、status_flg 0、筆 0
  - status_auto uncertain (flags phantom、no_parcels、abr_unconfirmed)
  - 調査で status abolished (0.5 corrected)。to: lo 1972-12-05、hi 2000-10-01、prec range
  - evidence:
    - contradicts: ABR に行がある
    - supports: 筆が 0。06xxxxx の旧ブロックにあり、確実に消えた 木ノ新保四番丁 (1965 年) と 堀川角場町 (2006 年に全域吸収) が並んでいる
  - open_questions「ヘは 1972 年の字区画の一つかもしれない」
- P0607 入江町
  - abr_parent、flags phantom、status_auto uncertain
  - 調査で abolished (0.5 corrected、conf_before 0.45)。「1972-01-08 に廃止」は誤りで、入江第一は字区画を組み直しただけで町名は残った
  - from: lo = hi 1935-12-16 (米丸村の編入)
  - to: lo 1972-12-05 (年表 1972-12-04「金沢競馬場 (入江町)」)、hi 2000-10-01 (2000 年国勢調査は入江1〜3丁目だけ)、prec range
  - end_event NULL
- X00007 入江町イ、X00008 入江町ロ、X00009 入江町ハ、X00010 入江町ホ
  - 1972 年の字区画のうち、換地図081 で読めた分
  - koaza、parent P0607、transitional=1、abolished
  - from 1972-01-08 (start_event Ei2)、to lo 1972-12-05 / hi 2000-10-01、end_event Ei3
  - 0.5 researched
  - town_name 入江町ロの部・入江町ホの部 (map_label)

**change_event**
- Ei1 `ev:172014:1968-04-01:kz052` 神田第二 (公告 1968-03-31)、0.8 confirmed
- Ei2 `ev:172014:1972-01-08:kz081` 入江第一 (公告 1972-01-07)、0.8 confirmed。label「町名は字区画イ〜ホで存続」
- Ei3 `ev:172014:x:irie-chome` unknown「入江2・3丁目の設定」、eff lo 1972-12-05 / hi 2000-10-01 (range)、0.5
- Ei4 `ev:172014:x:irie-racecourse` unknown「競馬場跡の新神田1丁目編入」、eff lo 1972-12-05 / hi 1977-12-31 (range)、0.4

**event_transfer (すべて part。字区画ごとの行き先は分からないので親から張る)**
- Ei1: P0607 → 0046001 入江1丁目 (0.6)
- Ei2: P0607 → 0460001 間明町1丁目、0460002 間明町2丁目 (0.5。端の帯だけ)
- Ei3: P0607 → 0046002 入江2丁目、0046003 入江3丁目 (0.5)
- Ei4: P0607 → 0250001 新神田1丁目 (0.4)

**解決結果**
- 「入江町ヘ」: canonical で 0607101 (ABR の行)。unique / old_name
  - 自分の家族に移転が無いので、親 P0607 の系譜を使う (inherited_from P0607)
  - t* 1972-12-05
  - primary: 入江2丁目・入江3丁目 (latest)、新神田1丁目 (latest。Ei3 と区間が重なる)
  - primary でない: 入江1丁目 (1968)、間明町1・2丁目 (1972-01)
  - warnings: ABR_ROW_SUSPECT_DEAD、INHERITED_FROM_PARENT、END_UNDETERMINED、HISTORICAL_NAME
- 「入江町」(区域表 → 米丸): P0607 に一致し、同じ targets。gomi は地区の整合 (入江3丁目 = 米丸) で絞ります。
- ABR が毎月 入江町ヘ を載せ続けても、status_auto が uncertain のまま更新されるだけで、調査で決めた status (abolished) は変わりません。

### 10.6 西堀川町 (ABR に無いが生きている残存)

**town**
- X00002
  - oaza、research、status remnant (status_auto uncertain)、abr_presence absent
  - flags [not_in_abr_but_living, census_hidden]、from / to なし
  - 代表点: geolonia 136.652588, 36.582815 (rep_conf 0.4)
  - status_claim: 0.6 confirmed (反証検証で、2006 年の換地図181 が事業区域の外に「西堀川町」の帯を描いていることを確かめた)

**event_transfer**
- E181 (木ノ新保7番丁と同じ事象) で、X00002 → 0451000 堀川新町
- part、share_of_from 0.697 (census_area:2005->2010)、0.8 confirmed
- 国勢調査の差分事象は E181 に統合 (merged_into)

**town_census**
- (2000, 172015080, 西堀川町, equal)
- (2005, 172015080, 西堀川町, equal)
- (2010, 17201516001, 北安江１丁目, hosts_remnant, share 0.303)
- 30.3% が北安江1丁目の小地域に入っているのは法的な移転ではないので、移転にはしません。

**evidence**
- 換地図181: strong (map_label)
- navbox の ※: weak (2021-11-01)
- 校区表: weak
- ABR に無い: weak (F2)
- 郵便番号が無い: context (※の町は郵便番号を持たない)

**解決結果**
- unique / remnant
- targets: 西堀川町 self (primary、幾何なし。代表点 0.4 と hosts_remnant の小地域)、堀川新町 (context、2006、0.697、primary=false)
- as_of {2000-04-01, authored} なら t* は 2000-04-01 で、堀川新町も primary になります。
- warnings: NOT_IN_ABR_BUT_LIVING、REMNANT_NO_GEOMETRY
- 将来 ABR が西堀川町を載せたら、新しい行と canon キーが一致します。そのとき X00002 の redirect_to を提案し、調査で確かめてから設定します。

### 10.7 湖南第2区画整理地 (仮称のラベル)

**town**
- X00011
  - level label、research、status abolished
  - from: lo 1993-01-01 (事業期間 H5〜)、prec unknown
  - to: lo = hi = best 2002-02-06、prec day
  - end_event K2、0.85 confirmed
  - town_name 湖南第二土地区画整理事業 (project。canon では「第二」が「第2」になる)

**change_event** (project_ref kz-kukaku-176 湖南第二 (個人施行、40.35ha)、koukoku_plus_1)
- K1 `ev:172014:1998-12-05:kz176-1` 第一工区 (公告 1998-12-04)、0.85 confirmed
- K2 `ev:172014:2002-02-06:kz176-2` 第二工区 (公告 2002-02-05。換地図の「H14.2.6現在」と一致)、0.85 confirmed

**event_transfer**
- K1: X00011 → 0476001 みずき1丁目、0476002 みずき2丁目 (part、0.6 researched)。open_questions「ラベルが第二工区だけを指している可能性」
- K2: X00011 → 0476003 みずき3丁目、0476004 みずき4丁目 (remainder、0.85 confirmed)
- K2 には法的な町域の移転も入れる (census_area:2000->2005、0.7 machine):
  - 0145000 木越町 → みずき3丁目 (share_of_to 0.88)
  - 0082000 大場町 → みずき3丁目 (share_of_to 0.12)
  - 木越町 → みずき4丁目 (share_of_to 1.0)
- 仮称が覆っていた範囲と法的な町域の移転は、from の level (label かどうか) で区別できます。

**解決結果**
- unique / provisional / t* 2002-02-06
- primary: みずき3・4丁目 (final、0.85)
- primary でない: みずき1・2丁目 (earlier、0.6)
- warnings: PROVISIONAL_LABEL、HISTORICAL_NAME
- 仮称の型 (`区画整理(事業)?地`) の入力は完全キー以外では照合しないので、湖南町 (0186000、森本) は候補になりません。

**ABR 自身の仮称行**
- 近岡町土地区画整理地 (0638000): level label (label_rule)、status current、abr_efct 2023-12-01 (実日付なので from にも入れる)
- 解決結果は unique / current + PROVISIONAL_LABEL
- 換地処分の後に ABR から消えた月に、abr_removed として調査に回ります。

### 10.8 観音町 (復活した名前)

**town**
- X00012 観音町 (旧)
  - oaza、research (国勢調査 2000〜2015 年の「観音町」2,530㎡)、status abolished
  - from: なし (以前から存在)
  - to: lo 2015-10-02 (2015 年国勢調査に載っている)、hi 2019-12-31、prec year
  - note「2019 (旧町名復活。月日と告示は未確認)」
  - end_event ER、flags [name_reused]、0.6 researched
- P0138 観音町 (復活後)
  - abr_parent (丁目の束)、current
  - from: lo 2019-01-01、hi 2019-12-31、prec year
  - start_event ER、revives_id X00012、flags [name_reused]
- 0138001 観音町1丁目 (jukyo)、0138002 観音町2丁目 (jukyo)、0138003 観音町3丁目 (mixed。ABR に rsdt 0/1 の 2 行がある)
  - current、from: lo 2019-01-01、hi 2019-12-31、start_event ER
  - ABR の efct (1947-04-17) は使わない

**change_event**
- ER `ev:172014:2019:kannon-revival`
  - revival、eff lo 2019-01-01 / hi 2019-12-31 (year)、basis secondary
  - 0.6 researched。open_questions「市の旧町名復活の資料で、月日と由来町を確認」
- `ev:172014:census:2015-2020:観音町`: census_observed、merged_into ER

**event_transfer (ER)**
- X00012 → 0138003 観音町3丁目: part、0.987、census_area:2015->2020、0.7
- X00012 → 0198000 子来町: 0.013、is_noise=1
- 東山側から 1〜3丁目に入った土地 (1966 年の住居表示で東山へ移った部分が戻ったもの) は未調査なので、移転を作りません (§11 の 10)。

**town_census**
- X00012: (2000〜2015 年, 172015700, 観音町, equal)
- 0138003: (2020, 17201570003, 観音町三丁目, contains) と (2020, 17201570000, 観音町三丁目, contains)。後者は旧観音町の面です。

**解決結果 (丁目の無い「観音町」)**

| as_of | 各実体の判定 | match_status / kind | targets | warnings |
|---|---|---|---|---|
| 省略 (unknown) | X00012 と P0138 が canon で一致 | ambiguous | 自動で選ばない (両方を candidates に返す) | NAME_REUSED |
| {2018-04-01, authored} | X00012 は unknown、P0138 は no (候補の後ろへ) | unique / old_name、t* 2018-04-01 | 観音町3丁目 (final、primary、0.987) | NAME_REUSED |
| {2025-04-01, authored} | X00012 は no、P0138 は yes | unique / current_group | 観音町1〜3丁目 (group_member) | NAME_REUSED |
| {2019-06-01, authored} | どちらも unknown | ambiguous | | NAME_REUSED |

「観音町1丁目」などは丁目まで書かれているので一意に決まります。区域表には 1〜3丁目の行だけがあります (地区は材木)。

### 10.9 神田町5丁目 (中間名)

**town**
- X00013 神田町
  - oaza、research、status remnant (navbox の ※。換地図052 にハ/ニ/ホの部が残る)
  - bulk_event Ei1 (神田第二 1968-04-01。入江町の Ei1 と同じ事象)
  - flags [not_in_abr_but_living]、0.5 confirmed
  - open_questions「1935 年に大字御供田を改称した可能性」
- X00014 神田町5丁目
  - chome、parent X00013、chome_num 5、transitional=1、abolished
  - from: lo 1965-09-25 (市議会が新町名を議決。年表)、hi 1967-01-12 (この日付の換地図052 に描かれている)、best 1965-11-20 (神田第一の公告 1965-11-19 + 1)、prec range
  - to: lo 1967-01-13、hi 2000-10-01、prec range
  - start_event G1、end_event G3
  - 0.5 researched (反証担当が見つけた名前で、経路は 1 本だけ)
  - town_name 神田町五丁目 (map_label)
- 年表の「神田3丁目」(1971) は 新神田3丁目 のゆるい呼び方なので、名前として登録しません。evidence の context に残すだけです。

**change_event**
- G1 `ev:172014:1965-11-20:kanda1`
  - 神田第一 (組合、12.63ha、公告 1965-11-19)、koukoku_plus_1、0.7
  - X00014 の start_event。神田町 → 神田町5丁目 は同じ家族の中なので、移転は張りません。
- Ei1 `ev:172014:1968-04-01:kz052` (神田第二)
  - X00013 → 0250001〜0250005 新神田1〜5丁目 (part、0.6)
- G3 `ev:172014:x:kanda5-rename`
  - chomei_chiban_henko「神田町5丁目 → 神田1・2丁目 (推定)」
  - eff lo 1967-01-13、hi 2000-10-01、basis usage_bounds、0.4
  - X00014 → 0135001 神田1丁目、0135002 神田2丁目 (portion unknown、0.4)

**解決結果**
- 「神田町5丁目」「神田町五丁目」
  - canonical で X00014。unique / transitional / t* 1967-01-13
  - primary: 神田1丁目・神田2丁目 (final、0.4)
  - warnings: TRANSITIONAL_NAME、LOW_CONFIDENCE
  - 自由文でも完全な名前の最長一致が先に当たるので、「神田町」(残存) + rest「5丁目」と誤って分けません。中間名を実体として持つ理由はここにあります。
- 「神田町」(区域表 → 新神田)
  - X00013。unique / remnant
  - targets: 神田町 self (primary、幾何なし)、新神田1〜5丁目 (context、bulk)、神田1・2丁目 (context、via_child 神田町5丁目)

### 10.10 ノ/の の衝突 (山の上町 と 山ノ上町)

**ABR の行と実体**
- 0522000 山の上町
  - type1、jukyo、筆 1,396、〒920-0816
  - key_canon 山の上町、key_fold 山の上町
- 0523005 山ノ上町５丁目
  - type2、chiban、筆 0 (flag no_parcels)。国勢調査に自分の面が無い (census_hidden)
  - key_canon 山ノ上町5丁目、key_fold 山の上町5丁目
- P0523 山ノ上町
  - 合成親 (丁目の束。5丁目だけ)
  - key_canon 山ノ上町、key_fold 山の上町
- 0632101〜0632110 山ノ上町五丁目ソ〜レ
  - 幽霊大字 (大字コード 0632) だが、大字名の canon キー「山ノ上町5丁目」が 0523005 と一致するので、0523005 の子にする (phantom_adopted)
  - 筆 3〜144、status_flg 1。地番住所の幾何はこちらが持つ
- 監査: fold キー「山の上町」に 0522000 と P0523 の 2 実体が当たる (ingest_run.stats の衝突一覧に載る)。

**解決結果**

| 入力 | C (canon) / F (fold) | match_status | 補足 |
|---|---|---|---|
| 山の上町 | C={0522000} / F={0522000, P0523} | preferred (auto_pick) | best は 0522000。FOLD_COLLISION を付け、P0523 を candidates に入れる |
| 山ノ上町 | C={P0523} / F={P0523, 0522000} | ambiguous | canon で当たったのが丁目の束なので、自動で選ばない |
| 山ノ上町5丁目 | C={0523005} / F={0523005} | unique | children に 山ノ上町五丁目ソ〜レ (筆あり) |
| 山の上町5丁目 | C={} / F={0523005} | unique (MATCH_FOLDED) | |

- 区域表には 山の上町 と 山ノ上町5丁目 の両方の行があり (どちらも地区は森山)、どちらも自動で解決します。
- 全国では、ノ/の・ヶ/ケ の衝突が type1/2 の大字名で 19 組あります (気仙沼市 内の脇/内ノ脇、京都市伏見区 久我森の宮町/久我森ノ宮町、町田市 高ケ坂/高ヶ坂、鉾田市 塔ケ崎/塔ヶ崎 など)。
- canon の段の衝突 152 組 (互換漢字の 塚/塚 の 14 組を含む) は、常に ambiguous になります。

---

## 11. v0.1 で作らないもの

**データと調査の範囲**
1. **国勢調査差分の全国実行と 2025 年調査**: コードは v0.1 で書きます。これが入ると、ABR 初回取込分の町字にも from_hi (初めて国勢調査に載った年) が付きます。そうすれば、荒屋1丁目のような 2020 年以降の新設町を、古い資料の as-of で誤って yes と判定しなくなります。
2. **筆数の全国集計**: それまで、幽霊大字の配下の 158,112 小字は uncertain のままです。
3. **金沢以外の旧町名の系譜調査**: gomisute-gps の取込で none / ambiguous になった名前から、research_queue の順に調べます。
4. **換地図 PDF の機械読取 (VLM) と、県公報の告示本文の収集**: primary_confirmed の事実を増やす作業です。
5. **存在の証言の機械取込**: 日本郵便 KEN_ALL の月次差分を存在の証言にすること (v0.2 の最優先。北安江町・西念町のように、郵便番号があって ABR に無い町を機械的に拾えます) と、geolonia の全国の点。

**幾何と時点**
6. **L3**: 番地・号の解析、地番代表点 (parcels() API、R2 のパック)、筆ポリゴンとの結合、ポリゴンの保持。
7. **過去の時点の町字に寄せる機能** (at パラメータ)。

**市区町村と系譜**
8. **市区町村合併に伴う町名の改称**: 鶴来本町のような改称を町字単位の系譜で持つこと、市区町村の境界変更、1985 年より前の市区町村の履歴、総務省の廃置分合データとの突合。muni_name は名前ベースのままにします。
9. **ABR 差分からの後継の自動推定** (代表点の近さなど): v0.1 では research_queue の detail に候補を書くだけです。
10. **生きている町字どうしの部分的な区域移動の網羅**: 東山1丁目の一部 → 観音町、藤江北4丁目 → 鞍月 など。見つけた分だけ入れます。

**照合と運用**
11. **照合の拡張**: かな・ローマ字、編集距離、「が/ヶ」、京都の通り名、北海道の条丁目・線号、岩手の地割の構文解析、type 4/5 (町字なし・道路名)。
12. **運用の改善**: sqldiff による差分公開 (月次の書込みを減らす)、Queues / Webhook による変更通知、D1 の read replication、公開の報告フォーム、系譜の閲覧・編集 UI、OpenAPI。

---

## 12. リスク

1. **調査系譜の誤りで、収集日を誤案内する**
   - 対策: 後継ごとの confidence / verification を返し、primary は推定であると明示します。反証検証、PR でのレビュー、利用者報告で直します。
2. **「見つからない」を「存在しない」と誤読する**
   - 対策: lineage_level と LINEAGE_NOT_RESEARCHED を返します。デモは金沢に限ります。
3. **as_of の取り違え**
   - 対策: 既定を unknown にします。名前の再利用 (観音町) は ambiguous にします。
4. **ABR の仕様や中身が変わる** (ablt_date が埋まり始める、合併で ID が振り直される)
   - 対策: 差分を (lg, id, rsdt) 単位で取ります。プレースホルダの日付を検知します。ID の再利用は禁止します。
5. **正規化の二重実装 (Python と TS) がずれる**
   - 対策: rules.json と golden.tsv を 1 つにし、両方で同じテストを通します。
6. **国勢調査の面積比の誤差**
   - 対策: share_basis を必ず返し、欠片は is_noise、法的でない重なりは hosts_remnant にします。調査事象に統合します。
7. **幽霊大字の uncertain が多い** (全国で 158k 小字)
   - 対策: 照合は止めません (auto_pick は維持し、PARCELS_UNCHECKED を付ける)。筆数の集計を優先します。
8. **D1 の容量・書込み・投入時間**
   - 296MB は 1 DB の上限 (無料 500MB、有料 10GB) に収まりますが、初回の書込み行数のため Workers Paid が必要です。blue/green で 2 DB を使います。
   - 対策: 投入は公開前に済ませます。
9. **ライセンスと出典表示**
   - ABR: デジタル庁の利用規約に従い出典を表示する。
   - e-Stat: 政府標準利用規約。
   - geolonia: CC BY 4.0。
   - Wikipedia: CC BY-SA なので転載しない。
   - 換地図: 市の著作物なので、読み取った事実だけを持つ。
   - address2houmukyoku: 非公開リポジトリ。
10. **プライバシー**
    - 対策: 番地以降は保存しません。ログは町字までに切ります。
11. **スコープが膨らむ**
    - 対策: v0.1 の完了条件を「全国の現行町字 + 金沢の系譜 + gomi の金沢の取込が通ること」に固定します。

---

## 13. 工程 (ビルドウィーク 2026-11-02〜10、1 人 + Claude Code)

| 日 | 作業 | 完了の条件 |
|---|---|---|
| 11/2 | リポジトリ、wrangler (Workers Paid。D1 は addrnorm_a / addrnorm_b / addrnorm_ops)、DDL、rules.json と golden.tsv、キー関数の Python 版と TS 版 | 両方の実装が golden を通る |
| 11/3 | builder: muni / muni_name (a2h)、ABR 全国 (親の合成・採用・label・status_auto・代表点)、金沢の筆数 | 件数が §1.2 と一致し、約 300MB |
| 11/4 | 調査レコード v2 への変換 (19 + 10 件、中間名・残存帯)、国勢調査 (金沢)、不変条件 | §10 の行が揃い、不変条件が通る |
| 11/5 | Worker の解決コア (キー、候補、as-of、match_status、系譜、段階、primary) と自治体パック | §10 のゴールデンが TS で通る |
| 11/6 | RPC / HTTP、resolveLabels と coverage、normalize、meta / changesSince。公開スクリプト (dump → import → preview → 切替) | 本番の D1 に初回公開 |
| 11/7 | gomisute-gps と Service Binding で接続。金沢の 1,776 行を一括で解決し、未解決の 59 町を見直す | 未解決の件数を前後で比較 |
| 11/8 | ABR 月次差分 (取得済みのスナップショットと新しく取得した分)、report / unresolved_query、幽霊大字のある自治体の筆数集計 (時間があれば) | 差分から change_log と research_queue が出る |
| 11/9 | E2E テスト、日本語ドキュメント、デモ (木ノ新保町が旧町名の行だけで解決される例) | |
| 11/10 | 予備日、提出 | |

**遅れたときに後回しにする順**
1. normalize の CHOME_INFERRED
2. coverage の only_via_old_names 以外の項目
3. ABR 月次差分の自動化 (手動実行にする)
4. 筆数の追加集計

**削らないもの**: primary と t*、auto_pick の規則、lineage_level、data_version。

**規模の見込み**: Worker (TS) 約 1,500 行、builder (Python) 約 1,200 行。

---

## 14. 未決事項

所有者の判断が要るものです (2026-10-17 時点)。

1. Workers Paid ($5/月) を使うか。全国 ABR の初回投入 (本体と索引で数百万行の書込み) と blue/green の 2 DB に必要。使わない場合は、v0.1 で公開する範囲を石川県か金沢に絞る。
2. address2houmukyoku (非公開、LLM 抽出) から作った旧市町村 → lg_code の対応を、公開 HTTP API で返してよいか。だめなら、muni_name の former は Service Binding (gomisute-gps) にだけ出す。
3. address-normalize のリポジトリと research/ の調査レコードを公開するか。gomisute-gps は公開している。調査レコードには、Wikipedia と市の換地図から読み取った事実の要約が入る。公開するなら、調査レコード自体のライセンス表記も決める。
4. HTTP API をビルドウィーク中に匿名で公開するか、Service Binding だけにするか。レート制限、負荷、出典表示の責任が関わる。
5. ハッカソンの規約に照らして、事前に作った成果物 (10/16〜17 の調査データ old_town_lineage.json、ABR の取得物、この設計文書) を申告する必要があるか。どこまで流用してよいかも確認する。
6. gomisute-gps 側で、preferred (ノ/の 衝突) と低確信度 (例: confidence < 0.5) の結果を自動で採用するか、人の確認に回すか。その閾値も決める。
7. ABR の status_flg=0 かつ筆 0 の小字 (金沢で 126 行、全国は未集計) と、筆数を数えていない幽霊大字の小字 (全国 158,112 行) を自動で uncertain にする方針でよいか。照合は止めないが、現存の扱いが弱くなる。代案として、全国の筆数集計 (幽霊大字を持つ 361 自治体の mt_parcel、数 GB) をビルドウィークに前倒しすることもできる。
