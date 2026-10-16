# ABR 町字マスターで金沢の「ポリゴンが引けない32町」を引けるか — 検証結果

生成: `lineage_report.py` (入力 `abr_check.py` / `census_lineage.py` / Claude 調査＋反証ワークフロー)

## 結論

- **ABR では旧町字・廃止町字は引けない。** 金沢の町字 6,713 行、全国 727,467 行とも `ablt_date` 0件・別名0件。 `efct_date` は 6,681 行が 1947-04-17 のプレースホルダ
- ABR で引けるのは「現行の町字」まで。旧町名は **国勢調査の差分 (2000年以降)** と **文献調査 (それ以前)** で自前に持つ必要がある
- しかも **ABR に無い ≠ 廃止**。地番の登記が無い残存地 (西堀川町・八幡町・笠舞町・糸田町・神田町) や、郵便番号が現役の北安江町・西念町も ABR に無い
- 住居表示で消えた町名の未実施地番が「旧町名＋イロハ小字」として ABR に残ることがある (大字行のない小字 = 幽霊大字)。ただし地番マスターに筆があるものだけが本物 (窪町 31筆 ○ / 入江町ヘ 0筆 ×)
- 1つの町名が数十年かけて段階的に消える・中間町名を経る・別の範囲で復活する (観音町 2019) ので、町名は安定キーにならない

## 32町の分類

| 分類 | 件数 | 意味 |
|---|---|---|
| ABR_NOT_FOUND | 15 | ABR にも国勢調査2020にも無い (廃止済み、または地番の無い残存地) |
| PARCEL_AREA(地番区域) | 6 | ABR に現存。国勢調査2020は親の町名のまま (2020年以降の丁目新設とみられるが ABR の日付は仮置き)。地番の位置参照あり |
| SPLIT_INTO_CHOME | 3 | 区域表が丁目を付けずに書いている (ABR・国勢調査は丁目単位) |
| OLD_TOWN_REMNANT(旧町名+小字) | 3 | 大字行の無い小字行だけが ABR に残る。筆があるのは窪町・円光寺町のみ (入江町ヘは筆0で実体なし) |
| AZA_AREA(小字あり) | 2 | ABR に大字として現存し小字を持つ。国勢調査では丁目側に吸収 |
| NEW_AFTER_CENSUS | 1 | 国勢調査2020後に新設 (ABR に効力発生日あり) |
| LAND_READJUSTMENT(区画整理の仮称) | 1 | 町字ではない (区画整理事業地の呼び名) |
| CURRENT_TOWN | 1 | ABR に現存。国勢調査の小地域では別の町と合算 |

## 消えた町名の系譜 (Claude 調査 → 反証検証)

| 旧町名 | 状態 | 廃止 | 事由 | 後継 (面積比) | 検証 | 確信度 |
|---|---|---|---|---|---|---|
| 入江町 | split | ? (unknown) | unknown | 入江1丁目, 入江2丁目, 入江3丁目, 新神田1丁目 | corrected | 0.50 |
| 八幡町 | remnant | 1966-09-01 (day) | 住居表示 | 東山1丁目, 東山2丁目 | confirmed | 0.60 |
| 円光寺町 | remnant | 1979-06 (month) | 土地区画整理 | 円光寺1丁目, 円光寺2丁目, 円光寺3丁目, 円光寺本町, 伏見台3丁目 | confirmed | 0.55 |
| 北安江町 | remnant | 2001-06-02 (day) | 土地区画整理 | 北安江4丁目(66%), 北安江2丁目(28%), 諸江町(6%), 北安江1丁目, 北安江3丁目, 昭和町, 木ノ新保町, 堀川新町 | corrected | 0.70 |
| 南安江町 | split | 2006-08-12 (day) | 土地区画整理 | 堀川新町, 木ノ新保町 | confirmed | 0.70 |
| 寺地町 | split | 1990 (year) | 土地区画整理 | 寺地1丁目, 寺地2丁目, 伏見台1丁目, 伏見台2丁目, 久安4丁目, 有松3丁目 | corrected | 0.45 |
| 島田町 | split | 1993-01-09 (day) | 土地区画整理 | 昭和町, 本町2丁目 | confirmed | 0.80 |
| 木ノ新保7番丁 | split | 2006-08-12 (day) | 土地区画整理 | 木ノ新保町(64%), 堀川新町(36%) | confirmed | 0.85 |
| 柳町 | split | 1993-01-09 (day) | 土地区画整理 | 昭和町, 本町1丁目 | insufficient | 0.40 |
| 湖南第2区画整理地 | provisional | 2002-02-06 (day) | 土地区画整理 | みずき1丁目, みずき2丁目, みずき3丁目, みずき4丁目 | confirmed | 0.85 |
| 玉井町 | split | 2006-08-12 (day) | 土地区画整理 | 本町2丁目, 昭和町, 木ノ新保町 | corrected | 0.85 |
| 神田町 | remnant | 1968-04-01 (day) | 土地区画整理 | 新神田1丁目, 新神田2丁目, 新神田3丁目, 新神田4丁目, 新神田5丁目, 神田1丁目, 神田2丁目 | confirmed | 0.50 |
| 窪町 | remnant | 1970 (decade) | 土地区画整理 | 窪1丁目, 窪2丁目, 窪3丁目, 窪4丁目, 窪5丁目, 窪6丁目, 窪7丁目, 伏見台3丁目, 久安1丁目 | corrected | 0.60 |
| 笠舞町 | remnant | 1973-06-01 (day) | 住居表示 | 笠舞1丁目, 笠舞2丁目, 笠舞3丁目, 笠舞本町1丁目, 笠舞本町2丁目, 小立野3丁目, 城南1丁目, 城南2丁目 | confirmed | 0.50 |
| 糸田町 | remnant | 1972-08-23 (day) | 土地区画整理 | 糸田1丁目, 糸田2丁目, 糸田新町, 東力1丁目, 東力2丁目 | confirmed | 0.55 |
| 藤江町 | split | 1970 (decade) | 町名地番変更 | 藤江北1丁目, 藤江北2丁目, 藤江北3丁目, 藤江北4丁目, 藤江南1丁目, 藤江南2丁目, 藤江南3丁目 | confirmed | 0.50 |
| 西堀川町 | remnant | 2006-08-12 (day) | 土地区画整理 | 堀川新町(70%) | confirmed | 0.60 |
| 西念町 | remnant | 2001-06-02 (day) | 土地区画整理 | 西念3丁目(26%), 西念2丁目(22%), 西念4丁目(22%), 駅西新町3丁目(9%), 駅西本町4丁目(8%), 駅西新町1丁目(7%), 北安江4丁目(4%), 鞍月東1丁目(1%), 西念1丁目 | confirmed | 0.65 |
| 額新保町 | split | 1984-05-16 (day) | 土地区画整理 | 額新保1丁目, 額新保2丁目, 額新保3丁目, 額新町1丁目, 額新町2丁目 | confirmed | 0.55 |

## 国勢調査差分で機械的に取れた系譜 (金沢 2000→2020)

- 六丁町 (2000年まで) → 二ツ屋町 47%, 駅西本町4丁目 35%, 駅西本町3丁目 18%
- 北安江町 (2000年まで) → 北安江4丁目 66%, 北安江2丁目 28%, 諸江町 6%
- 西念 (2000年まで) → 西念1丁目 100%
- 西念町 (2000年まで) → 西念3丁目 26%, 西念2丁目 22%, 西念4丁目 22%, 駅西新町3丁目 9%, 駅西本町4丁目 8%
- 北陽台 (2005年まで) → 北陽台3丁目 100%
- 木の新保7番丁 (2005年まで) → 木ノ新保町 64%, 堀川新町 36%
- 西堀川町 (2005年まで) → 堀川新町 70%, 北安江1丁目 30%
- 上安原町南 (2010年まで) → 上安原南 100%
- 旭町 (2010年まで) → 田上さくら3丁目 99%
- 観音町 (2015年まで) → 観音町3丁目 99%, 子来町 1%

## 反証担当の横断所見

1) ABR as evidence. efct_date is the 1947-04-17 default everywhere, so it carries no history. The legacy machiaza_id block 06xxxxx (status_flg 0) mixes names that are certainly dead (木ノ新保四番丁, gone 1965; 堀川角場町, wholly absorbed 2006) with 入江町ヘ. Treat a type-3 koaza row as a real remnant only when mt_parcel has parcels under it: 窪町 (31 parcels in 7 koaza) and 円光寺町チ7-2 qualify. The reverse also holds: ABR lacks every ※ remnant that has no registered parcels (西堀川町, 八幡町, 笠舞町, 糸田町, 神田町), and also 北安江町 and 西念町, which still have postal codes. So 'not in ABR' does not mean 'abolished'.
2) Sources for whether a name still exists. Japan Post (live): 北安江町 920-0023 and 西念町 920-0026 are listed, and ※ remnants are left out by design. The Wikipedia navbox (2021-11-01) marks ※ = 'tiny remnant with no postal code'. geolonia japanese-addresses (MLIT-derived) has points for most remnants, and point-in-polygon on the 2020 census areas locates them roughly. Watch for artefacts: geolonia's 額新保町 point lies 4 km away inside 坪野町. The city school-district table and the garbage table share the same legacy town list, which still includes fully abolished names (島田町, 玉井町, 柳町, 藤江町, 額新保町, 入江町, 木ノ新保7番丁). Neither proves existence, and they are not independent of each other.
3) Best primary sources are the city's 換地図 PDFs (image-only; render with PyMuPDF and read the labels). They name the remnant strips left outside each project, for example: 北安江町ト and 広岡町ロ, plus 西堀川町 (2006); 北安江町ト/ヘ (1993); 寺地町ニ/ハノ部 and 円光寺町ニ/ハノ部 (1979); 神田町ハ/ニ/ホの部 and 入江町ハの部 (1968); 糸田町ナ/アの部 (1972). This gives remnant koaza and approximate locations for district assignment.
4) Dates. The city 区画整理 table gives 換地処分公告 dates; the town change takes effect the next day, which checks out four times (2006-08-12, 1993-01-09, 2002-10-26, 1996-10-02). Pitfalls: (a) multi-工区 projects show only the final 公告 (金沢駅西 had 1986, 1992 and 1996 stages). (b) A town-area change can come before the 換地処分: 昭和町地区's change was 1992-06-10 (石川県告示第322号), but its 公告 was 1998-08-31. (c) A 換地処分 can keep the old town name with new 字 zones (入江第一 1972 → 入江町イ〜ホ), so its date is not an abolition date. (d) Wikipedia town articles cite exact 石川県公報 告示 numbers (reliable), but some stubs (柳町, 堀川町) are unsourced copy-paste. (e) The 金沢市立図書館 市史年表 gives dated usage bounds, but its naming is sometimes loose ('神田3丁目' in 1971 for 新神田3丁目).
5) Relation types for the normalization table: whole absorption (玉井町→木ノ新保町; 島田町→昭和町); a split between two new towns (七番丁→木ノ新保町 + 堀川新町); staged attrition over decades (柳町 1965/1992/1993; 北安江町 1992-2006); a surviving koaza strip; intermediate or transitional names (神田町5丁目 in 1965; 入江町イ〜ホ in 1972; 笠舞一丁目/小立野三丁目 between 1964-01 and 1964-04); placeholder labels (湖南第2区画整理地, and ABR's own 近岡町土地区画整理地); and revived names (旧町名復活, e.g. 観音町1-3丁目 in 2019, so a dead name can return with a different extent). Store abolished_on separately for the date most of the town converted and the date the name finally disappeared. Records currently mix the two (寺地町, 柳町).
6) gomi_successors. An old-name garbage rule applies to the land that still carried the name when the table was compiled, i.e. the successors of the latest event, not the 1960s pieces that have long had their own rows. 木ノ新保町 has no garbage row at all, so the station area resolves only through old-name rows (玉井町→芳斎, 木ノ新保7番丁/南安江町→此花). Surviving strips can sit in a different district from the old-name rule (北安江町ト by 広岡町/昭和町, while the rule says 諸江).
7) Spelling and normalization. Handle ノ/の (木ノ新保 vs census 木の新保). Number styles differ: garbage 7番丁 vs legal 七番丁; census 2000-2015 uses full-width １丁目, 2020 uses 一丁目, ABR uses chome_number. Watch ケ/ヶ and 斉/斎 (芳斉 vs 芳斎). A census area can hide tiny towns inside a neighbour (南安江町 and 玉井町 had no area of their own in 2000/2005), and remnants vanish from the census while still legally alive. Use census overlays only for the main conversions.

## 全行 (ABR 照合)

| set | town | gomi reason | ABR match | class | census | ABR id/type/status/efct | koaza | rep_pt | 街区 | 地番(位置付) |
|---|---|---|---|---|---|---|---|---|---|---|
| missing_poly | 荒屋1丁目 | census_outdated(parent=荒屋) | exact | PARCEL_AREA(地番区域) | census_has_parent:荒屋 | 0015001 荒屋１丁目 chome s1 1947-04-17 | 0 | True | 0 | 169(169) |
| missing_poly | 泉野町 | chome_split_in_census | children_chome (+5) | SPLIT_INTO_CHOME | census_children:泉野町1丁目,泉野町2丁目,泉野町3丁目,泉野町4丁目,泉野町5丁目,泉野町6丁目 | 0032001 泉野町１丁目 chome s1 1947-04-17 | 0 | True | 17 | 393(0) |
| missing_poly | 泉野出町 | chome_split_in_census | children_chome (+3) | SPLIT_INTO_CHOME | census_children:泉野出町1丁目,泉野出町2丁目,泉野出町3丁目,泉野出町4丁目 | 0031001 泉野出町１丁目 chome s1 1947-04-17 | 0 | True | 22 | 431(431) |
| missing_poly | 糸田町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | いなほ3丁目 | not_in_census | exact | NEW_AFTER_CENSUS | absent | 0041003 いなほ３丁目 chome s1 2021-09-30 | 0 | False | 0 | 17(17) |
| missing_poly | 入江町 | not_in_census | phantom_oaza | OLD_TOWN_REMNANT(旧町名+小字) | absent |  入江町 phantom_oaza(小字のみ) s  |  |  |  | () |
| missing_poly | 円光寺町 | not_in_census | phantom_oaza | OLD_TOWN_REMNANT(旧町名+小字) | absent |  円光寺町 phantom_oaza(小字のみ) s  |  |  |  | () |
| missing_poly | 笠舞町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 神田町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 北安江町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 木ノ新保7番丁 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 窪町 | not_in_census | phantom_oaza | OLD_TOWN_REMNANT(旧町名+小字) | absent |  窪町 phantom_oaza(小字のみ) s  |  |  |  | () |
| missing_poly | 湖南第2区画整理地 | not_in_census | none | LAND_READJUSTMENT(区画整理の仮称) | absent |    s  |  |  |  | () |
| missing_poly | 古府西1丁目 | census_outdated(parent=古府西) | exact | PARCEL_AREA(地番区域) | census_has_parent:古府西 | 0193001 古府西１丁目 chome s1 1947-04-17 | 0 | True | 0 | 20(0) |
| missing_poly | 西念町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 島田町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 新竪町3丁目 | census_outdated(parent=新竪町) | exact | PARCEL_AREA(地番区域) | census_has_parent:新竪町 | 0253003 新竪町３丁目 chome s1 1947-04-17 | 0 | True | 0 | 283(0) |
| missing_poly | 千木1丁目 | census_outdated(parent=千木) | exact | PARCEL_AREA(地番区域) | census_has_parent:千木 | 0268001 千木１丁目 chome s1 1947-04-17 | 0 | True | 0 | 275(275) |
| missing_poly | 太陽が丘 | chome_split_in_census | children_chome (+2) | SPLIT_INTO_CHOME | census_children:太陽が丘1丁目,太陽が丘2丁目,太陽が丘3丁目,太陽が丘区画整理地内 | 0277001 太陽が丘１丁目 chome s1 1947-04-17 | 0 | True | 0 | 37(31) |
| missing_poly | 玉井町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 土清水町 | not_in_census | exact | AZA_AREA(小字あり) | absent | 0315000 土清水町 oaza_cho s1 1947-04-17 | 11 | True | 0 | 91(0) |
| missing_poly | 寺地町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 直江北1丁目 | census_outdated(parent=直江北) | exact | PARCEL_AREA(地番区域) | census_has_parent:直江北 | 0343001 直江北１丁目 chome s1 1947-04-17 | 0 | True | 0 | 315(315) |
| missing_poly | 西堀川町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 額新保町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 八幡町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 福久東1丁目 | census_outdated(parent=福久東) | exact | PARCEL_AREA(地番区域) | census_has_parent:福久東 | 0423001 福久東１丁目 chome s1 1947-04-17 | 0 | True | 0 | 139(139) |
| missing_poly | 藤江町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 南安江町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 柳町 | not_in_census | none | ABR_NOT_FOUND | absent |    s  |  |  |  | () |
| missing_poly | 山ノ上町5丁目 | not_in_census | exact | CURRENT_TOWN | absent | 0523005 山ノ上町５丁目 chome s1 1947-04-17 | 0 | True | 0 | 0(0) |
| missing_poly | 涌波町 | not_in_census | exact | AZA_AREA(小字あり) | absent | 0553000 涌波町 oaza_cho s1 1947-04-17 | 8 | True | 0 | 48(28) |
| unresolved | 赤土町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0004000 赤土町 oaza_cho s1 1947-04-17 | 11 | True | 0 | 1740(1699) |
| unresolved | 泉本町6丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0033006 泉本町６丁目 chome s1 1947-04-17 | 0 | True | 0 | 211(211) |
| unresolved | 上野本町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0048000 上野本町 oaza_cho s1 1947-04-17 | 2 | True | 0 | 27(0) |
| unresolved | 畝田中3丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0055003 畝田中３丁目 chome s1 1947-04-17 | 0 | True | 0 | 136(136) |
| unresolved | 畝田中4丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0055004 畝田中４丁目 chome s1 1947-04-17 | 0 | True | 0 | 479(479) |
| unresolved | 大桑町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0076000 大桑町 oaza_cho s1 1947-04-17 | 63 | True | 0 | 3618(1629) |
| unresolved | 神谷内町 | no_jukyo_points | exact | AZA_AREA(小字あり) | in_census | 0130000 神谷内町 oaza_cho s1 1947-04-17 | 29 | True | 0 | 3705(1262) |
| unresolved | 窪6丁目 | aza/unparsed | exact | PARCEL_AREA(地番区域) | in_census | 0163006 窪６丁目 chome s1 1947-04-17 | 0 | True | 0 | 443(431) |
| unresolved | 古府町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0194000 古府町 oaza_cho s1 1947-04-17 | 4 | True | 0 | 1171(539) |
| unresolved | 三十苅町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0212000 三十苅町 oaza_cho s1 1947-04-17 | 11 | True | 0 | 954(671) |
| unresolved | しじま台2丁目 | aza/unparsed | exact | PARCEL_AREA(地番区域) | in_census | 0216002 しじま台２丁目 chome s1 1947-04-17 | 0 | True | 0 | 400(400) |
| unresolved | 下新町 | no_jukyo_points | exact | CURRENT_TOWN | in_census | 0232000 下新町 oaza_cho s1 1947-04-17 | 0 | True | 6 | 91(0) |
| unresolved | 太陽が丘2丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0277002 太陽が丘２丁目 chome s1 1947-04-17 | 0 | True | 0 | 350(350) |
| unresolved | 高柳町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0291000 高柳町 oaza_cho s1 1947-04-17 | 36 | True | 0 | 1733(1636) |
| unresolved | 竪町 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0299000 竪町 oaza_cho s1 1947-04-17 | 0 | True | 0 | 129(0) |
| unresolved | 玉鉾町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0304000 玉鉾町 oaza_cho s1 1947-04-17 | 7 | True | 0 | 187(185) |
| unresolved | 中屋町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0359000 中屋町 oaza_cho s1 1947-04-17 | 10 | True | 0 | 630(411) |
| unresolved | 野田町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0387000 野田町 oaza_cho s1 1947-04-17 | 31 | True | 2 | 756(632) |
| unresolved | 福久町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0424000 福久町 oaza_cho s1 1947-04-17 | 35 | True | 0 | 1949(1744) |
| unresolved | 福増町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0425000 福増町 oaza_cho s1 1947-04-17 | 8 | True | 0 | 2222(1977) |
| unresolved | 普正寺町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0433000 普正寺町 oaza_cho s1 1947-04-17 | 23 | True | 0 | 1382(1089) |
| unresolved | 二ツ寺町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0435000 二ツ寺町 oaza_cho s1 1947-04-17 | 4 | True | 0 | 919(571) |
| unresolved | 大豆田本町 | aza/unparsed | exact | AZA_AREA(小字あり) | in_census | 0469000 大豆田本町 oaza_cho s1 1947-04-17 | 6 | True | 0 | 604(408) |
| unresolved | 湊2丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0487002 湊２丁目 chome s1 1947-04-17 | 0 | True | 0 | 619(619) |
| unresolved | 湊4丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0487004 湊４丁目 chome s1 1947-04-17 | 0 | True | 0 | 137(96) |
| unresolved | 南御所町 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0488000 南御所町 oaza_cho s1 1947-04-17 | 0 | True | 0 | 418(418) |
| unresolved | もりの里2丁目 | no_jukyo_points | exact | PARCEL_AREA(地番区域) | in_census | 0510002 もりの里２丁目 chome s1 1947-04-17 | 0 | True | 0 | 314(314) |

