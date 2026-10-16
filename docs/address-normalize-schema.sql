-- address-normalize D1 schema v0.2 (final, 2026-10-17)。docs/address-normalize-schema.md の v0.1 を置き換える
-- ============================================================================================
-- 規約
--  * D1 (データ本体) は builder が毎回ローカル SQLite に組み立てる「ビルド成果物」。Worker は読むだけ。
--    書き込みが要るもの (利用者報告・未解決クエリ・調査キュー) は別 D1 (addrnorm_ops, 末尾の ops_schema) に置く。
--  * 検証: SQLite 3.45.3 で全国 ABR (town 749,603 行) + 金沢 10 ケースを投入し FK 違反 0、ファイル 296MB (2026-10-17)。
--  * 日付は 'YYYY-MM-DD' の TEXT (辞書順 = 時間順)。有効期間は半開区間 [from, to)。
--    to = 新しい状態が効力を持つ初日 (区画整理なら 換地処分公告日 + 1日)。
--    *_lo / *_hi = その境界の「最も早い可能性 / 最も遅い可能性」(両端含む)。NULL = その側は不明。
--    *_best = 推定値 (表示・並べ替え用)。as-of 判定には lo / hi だけを使う (3値: yes / no / unknown)。
--  * town_id (一度払い出したら変えない・再利用しない):
--      '{lg}-{machiaza_id 7桁}'  ABR の行            例 172014-0157000 木ノ新保町
--      '{lg}-P{大字コード4桁}'    ABR から合成した親  例 172014-P0164 窪町 (小字行だけの幽霊大字) / 172014-P0138 観音町 (丁目の束)
--      '{lg}-X{5桁}'             自前 (調査・国勢調査・ABR差分) 例 172014-X00001 木ノ新保七番丁。registry/town_ids.jsonl で払い出す
--  * 主キーはすべて決定的な文字列 (再ビルドで番号が振り直されない)。
--  * D1 は外部キーを強制する。投入 SQL の先頭で PRAGMA defer_foreign_keys = true を使う。

-- ------------------------------------------------------------------ 0. 版と来歴
CREATE TABLE dataset_version (
  data_version   TEXT PRIMARY KEY,               -- '2026.11.06-1'
  published_at   TEXT NOT NULL,
  schema_version TEXT NOT NULL,                  -- '0.2'
  abr_snapshot   TEXT NOT NULL,                  -- ABR 取得日 '2026-10-01'
  census_years   TEXT NOT NULL,                  -- '2000,2005,2010,2015,2020'
  rules_version  TEXT NOT NULL,                  -- rules/rules.json の版
  research_ref   TEXT NOT NULL,                  -- research/ の git commit
  builder_ref    TEXT NOT NULL,                  -- builder の git commit
  notes          TEXT
);

CREATE TABLE change_log (                        -- 前の版と比べて変わった町字 (changesSince 用。直近24版を保持)
  data_version TEXT NOT NULL REFERENCES dataset_version(data_version),
  town_id      TEXT NOT NULL,
  change       TEXT NOT NULL CHECK (change IN ('added','status','validity','names','lineage','geo','redirected')),
  PRIMARY KEY (data_version, town_id, change)
) WITHOUT ROWID;

CREATE TABLE ingest_run (                        -- 機械処理 1 回 = 1 行。自動導出の claim はここに紐づく
  run_id       TEXT PRIMARY KEY,                 -- 'abr:2026-10-01' 'census:172014:2000-2020' 'research:3f2a9c1' 'a2h:2026-05-05'
  kind         TEXT NOT NULL CHECK (kind IN ('abr','census','research','muni_alias','postal','rule')),
  code_ref     TEXT NOT NULL,                    -- builder の git commit
  input_sha256 TEXT,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  stats        TEXT CHECK (stats IS NULL OR json_valid(stats))   -- 件数・衝突監査・キュー候補
);

CREATE TABLE source (
  source_id    TEXT PRIMARY KEY,                 -- 'abr:mt_town_all:2026-10-01' 'kz:kanchizu:181' 'wp:木ノ新保町' 'jp:ken_all:2026-10'
  kind         TEXT NOT NULL CHECK (kind IN ('abr','census','legal_notice','official_map','official_table','municipal_service_table',
                                             'postal','wikipedia','chronology','derived_dataset','llm_research','user_report','other')),
  title        TEXT,
  url          TEXT,
  archive_ref  TEXT,                             -- R2 キー or リポジトリ内パス (取得物の控え)
  published_on TEXT,
  retrieved_at TEXT NOT NULL,
  sha256       TEXT,
  reliability  TEXT NOT NULL CHECK (reliability IN ('primary','official','dataset','secondary','tertiary','user')),
  indep_group  TEXT,                             -- 同じ元リスト由来は同じ値 (校区表 + ごみ区域表 = 'kz:legacy_town_list')。独立した2票と数えない
  license      TEXT
);

-- 事実 1 件 = claim 1 行 (ABR スナップショットのような一括事実は 1 行で全行を覆う)。確信度と検証状態はここだけに持つ。
-- 反証で訂正された値は訂正後を採り、訂正前の確信度を conf_before に残す (訂正前の全文は git の履歴)。
CREATE TABLE claim (
  claim_id       TEXT PRIMARY KEY,               -- 'abr:2026-10-01' / 'r:172014-X00001:status' / 'r:ev:172014:2006-08-12:kz181'
  aspect         TEXT NOT NULL CHECK (aspect IN ('batch','status','validity','event','transfer','name','census','muni_alias')),
  basis          TEXT NOT NULL CHECK (basis IN ('abr_snapshot','abr_diff','census_diff','research','rule','external_dataset','user_report')),
  confidence     REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  verification   TEXT NOT NULL CHECK (verification IN ('machine','unverified','researched','confirmed','corrected','insufficient',
                                                       'primary_confirmed','disputed')),
  conf_before    REAL,
  review_note    TEXT,                           -- 反証検証の要約 (自分の言葉)
  reviewed_by    TEXT,                           -- 'claude:adversarial:2026-10-16' / 'human:firemio'
  open_questions TEXT,
  record_ref     TEXT,                           -- 'research/172014/X00001.json@3f2a9c1'
  run_id         TEXT REFERENCES ingest_run(run_id)
);

CREATE TABLE evidence (                          -- 「存在の証言」もここに入れる (ABR・国勢調査・郵便に無いことは strength='weak')
  claim_id    TEXT NOT NULL REFERENCES claim(claim_id),
  seq         INTEGER NOT NULL,
  source_id   TEXT NOT NULL REFERENCES source(source_id),
  stance      TEXT NOT NULL CHECK (stance IN ('supports','contradicts','context')),
  strength    TEXT NOT NULL DEFAULT 'normal' CHECK (strength IN ('strong','normal','weak')),
  observed_on TEXT,                              -- 資料が反映している日付 (地図の作成日・年表の記事日)。使用期間の矛盾検査に使う
  locator     TEXT,                              -- '換地図181 東側の帯ラベル' / 'row: 寺地町 ニ'
  finding     TEXT NOT NULL,                     -- 要約のみ。原文は転載しない
  PRIMARY KEY (claim_id, seq)
) WITHOUT ROWID;
CREATE INDEX evidence_source ON evidence(source_id);

-- ------------------------------------------------------------------ 1. 市区町村
CREATE TABLE muni (
  lg_code        TEXT PRIMARY KEY CHECK (length(lg_code) = 6),   -- 検査数字つき 6 桁 (ABR mt_city 1,918 行)
  lg5            TEXT NOT NULL UNIQUE,                            -- N03 / gps2city / 国勢調査の 5 桁 (全国で一意を確認済み)
  parent_lg_code TEXT REFERENCES muni(lg_code),                   -- 政令市の区 → 市
  pref TEXT NOT NULL, county TEXT, city TEXT, ward TEXT,
  name           TEXT NOT NULL,                                   -- '石川県金沢市'
  status         TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','abolished')),
  lineage_level  TEXT NOT NULL DEFAULT 'abr_only'                 -- 「見つからない」と「調べていない」を区別する
                 CHECK (lineage_level IN ('abr_only','abr_diff','census_diff','researched')),
  abr_first_seen TEXT,
  abr_last_seen  TEXT
);

CREATE TABLE muni_name (                         -- 現行名の各形 + 旧市町村名 (旧 muni_alias。address2houmukyoku 由来)
  key_canon  TEXT NOT NULL,
  lg_code    TEXT NOT NULL REFERENCES muni(lg_code),
  kind       TEXT NOT NULL CHECK (kind IN ('official','official_short','former')),
  surface    TEXT NOT NULL,                      -- '石川県石川郡鶴来町' / '金沢市'
  portion    TEXT NOT NULL DEFAULT 'whole' CHECK (portion IN ('whole','part')),   -- 旧市町村が割れた先なら part (複数行)
  event_kind TEXT,                               -- 合併 / 編入 / 市制施行 / 町制施行 / 名称変更 / 区設置 / 区再編
  eff_date   TEXT,
  claim_id   TEXT NOT NULL REFERENCES claim(claim_id),
  PRIMARY KEY (key_canon, lg_code, kind)
) WITHOUT ROWID;

-- ------------------------------------------------------------------ 2. 変化の事象 (town より先に作る: ダンプ投入順で参照先が先に入る)
CREATE TABLE change_event (
  event_id      TEXT PRIMARY KEY,                -- 'ev:172014:2006-08-12:kz181' / 'ev:172014:x:kubo-chome'
  lg_code       TEXT NOT NULL REFERENCES muni(lg_code),
  kind          TEXT NOT NULL CHECK (kind IN ('jukyo_hyoji','kukaku_seiri','chomei_chiban_henko','chiku_henko','chiseki_chosa',
                                              'rename','revival','creation','muni_merger','abr_observed','census_observed','unknown')),
  label         TEXT NOT NULL,                   -- '金沢駅北土地区画整理 換地処分'
  project_ref   TEXT,                            -- 'kz-kukaku-181 金沢駅北 (市施行 11.92ha)'
  stage_label   TEXT,                            -- '第二工区'
  legal_ref     TEXT,                            -- '石川県告示第432号'
  announce_date TEXT,                            -- 換地処分公告日 (効力発生日とは別に保存)
  eff_lo TEXT, eff_hi TEXT, eff_best TEXT,
  eff_prec      TEXT NOT NULL CHECK (eff_prec IN ('day','month','year','decade','range','unknown')),
  eff_basis     TEXT NOT NULL CHECK (eff_basis IN ('legal_text','legal_ref_secondary','official_list','koukoku_plus_1','secondary',
                                                 'map_inference','usage_bounds','census_window','abr_efct','abr_snapshot_window')),
  eff_text      TEXT NOT NULL,
  merged_into   TEXT REFERENCES change_event(event_id),   -- 国勢調査差分などを調査イベントに統合したら設定 (解決では使わない)
  claim_id      TEXT NOT NULL REFERENCES claim(claim_id),
  CHECK (eff_lo IS NULL OR eff_hi IS NULL OR eff_lo <= eff_hi)
);
CREATE INDEX event_lg ON change_event(lg_code);

-- ------------------------------------------------------------------ 3. 町字 (実体)
-- town 1 行 = 「同じ正式名称が途切れずに有効だった区域」。区域が縮んでも名称が続く限り同じ行 (柳町 1965/1992/1993)。
-- 改称・名称の途切れ・復活は別の行 (旧 観音町 X と 復活後の 観音町 P0138)。現行・旧町名・中間名・仮称ラベル・残存帯を同じ表に置く。
CREATE TABLE town (
  town_id         TEXT PRIMARY KEY,
  lg_code         TEXT NOT NULL REFERENCES muni(lg_code),
  origin          TEXT NOT NULL CHECK (origin IN ('abr','abr_parent','research','census','abr_diff','user')),
  level           TEXT NOT NULL CHECK (level IN ('oaza','chome','koaza','label')),   -- label = 区画整理の仮称など町字でない名称
  parent_id       TEXT REFERENCES town(town_id),  -- 丁目・小字 → 大字 (合成親を含む)。自身 + 子孫 = 「名前の家族」
  name            TEXT NOT NULL,                  -- 表示名 (ABR の字形のまま、丁目は算用数字)
  oaza            TEXT NOT NULL,
  chome_num       INTEGER,
  koaza           TEXT,
  key_canon       TEXT NOT NULL,                  -- 同一視してよい規則だけ (rules.json の canon)
  key_fold        TEXT NOT NULL,                  -- 衝突しうる規則まで (fold)。候補を出すためだけに使う
  status          TEXT NOT NULL CHECK (status IN ('current','remnant','abolished','uncertain')),   -- 実効値 (調査 > 自動)
  status_auto     TEXT NOT NULL CHECK (status_auto IN ('current','remnant','abolished','uncertain')), -- ABR と規則だけで決めた値
  status_claim_id TEXT NOT NULL REFERENCES claim(claim_id),   -- status と to_* の根拠
  confidence      REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),   -- status_claim から写した値 (読み取り高速化)
  verification    TEXT NOT NULL CHECK (verification IN ('machine','unverified','researched','confirmed','corrected','insufficient',
                                                        'primary_confirmed','disputed')),
  transitional    INTEGER NOT NULL DEFAULT 0 CHECK (transitional IN (0,1)),   -- 短命の中間名称 (神田町5丁目, 入江町イ〜ホ)
  from_lo TEXT, from_hi TEXT, from_best TEXT,
  from_prec       TEXT CHECK (from_prec IN ('day','month','year','decade','range','unknown')),
  to_lo   TEXT, to_hi   TEXT, to_best   TEXT,     -- to = 名称が最後に消えた日 (final)。区域の大半が移った日は bulk_event_id で別に持つ
  to_prec         TEXT CHECK (to_prec IN ('day','month','year','decade','range','unknown')),
  validity_note   TEXT,                           -- '1990年頃 (地図の隣接から推定)'
  start_event_id  TEXT REFERENCES change_event(event_id),
  bulk_event_id   TEXT REFERENCES change_event(event_id),     -- 区域の大半が移った事象 (寺地町 1979)
  end_event_id    TEXT REFERENCES change_event(event_id),     -- 名称が最後に消えた事象 (寺地町 1990)
  revives_id      TEXT REFERENCES town(town_id),              -- 旧町名復活: 同名の先代 (観音町)
  redirect_to     TEXT REFERENCES town(town_id),              -- 同一物と判明 (X 行が後で ABR に載った等)。API は寄せて返す
  abr_presence    TEXT NOT NULL CHECK (abr_presence IN ('present','via_children','gone','absent')),
  numbering       TEXT CHECK (numbering IN ('jukyo','chiban','mixed')),   -- ABR rsdt_addr_flg。0/1 の2行がある ID は mixed
  abr_status_flg  INTEGER,
  abr_efct        TEXT,                           -- 実日付のみ。1947-04-17 / 1970-01-01 (プレースホルダ) は NULL
  abr_first_seen  TEXT,                           -- 自前の ABR スナップショット日 (初回取込分は「この日以前から」)
  abr_last_seen   TEXT,
  parcel_count     INTEGER,                       -- mt_parcel の筆数 (子の小字込み)。NULL = 未集計
  parcel_pos_count INTEGER,
  post_code       TEXT,
  pos_code        TEXT,                           -- 位置参照 12 桁 (ABR pos_oaza_cho_chome_code) → GSI 住居表示点と突合
  rep_lon REAL, rep_lat REAL,
  rep_src         TEXT CHECK (rep_src IN ('abr_town_pos','abr_parcel_pos','geolonia','research')),
  rep_conf        REAL,
  flags           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags)),
                  -- phantom / phantom_adopted / no_oaza / no_parcels / parcels_unchecked / abr_unconfirmed / legacy_block /
                  -- label_rule / census_hidden / name_reused / not_in_abr_but_living
  updated_in      TEXT NOT NULL,                  -- 内容が最後に変わった data_version
  CHECK (from_lo IS NULL OR from_hi IS NULL OR from_lo <= from_hi),
  CHECK (to_lo   IS NULL OR to_hi   IS NULL OR to_lo   <= to_hi),
  CHECK (status NOT IN ('current','remnant') OR (to_lo IS NULL AND to_hi IS NULL AND end_event_id IS NULL)),
  CHECK (CASE origin
           WHEN 'abr'        THEN town_id GLOB lg_code || '-[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
           WHEN 'abr_parent' THEN town_id GLOB lg_code || '-P[0-9][0-9][0-9][0-9]'
           ELSE                   town_id GLOB lg_code || '-X[0-9][0-9][0-9][0-9][0-9]' END)
);
CREATE INDEX town_canon  ON town(lg_code, key_canon);
CREATE INDEX town_fold   ON town(lg_code, key_fold);
CREATE INDEX town_parent ON town(parent_id);
-- 外部キーの子側の列には索引を張る (無いと遅延 FK 検査で親行の投入ごとに全表走査になる。全国投入で実測)
CREATE INDEX town_revives  ON town(revives_id)     WHERE revives_id     IS NOT NULL;
CREATE INDEX town_redirect ON town(redirect_to)    WHERE redirect_to    IS NOT NULL;
CREATE INDEX town_start_ev ON town(start_event_id) WHERE start_event_id IS NOT NULL;
CREATE INDEX town_bulk_ev  ON town(bulk_event_id)  WHERE bulk_event_id  IS NOT NULL;
CREATE INDEX town_end_ev   ON town(end_event_id)   WHERE end_event_id   IS NOT NULL;

-- 正式名 (town.name) 以外の表記。ABR の現行町字は正式名だけなので、ほとんどの町字はここに行を持たない
CREATE TABLE town_name (
  town_id   TEXT NOT NULL REFERENCES town(town_id),
  surface   TEXT NOT NULL,                       -- 書かれたまま: '木の新保七番丁' '木ノ新保7番丁' '寺地町ニノ部'
  lg_code   TEXT NOT NULL,
  kind      TEXT NOT NULL CHECK (kind IN ('official_variant','census','postal','doc_label','map_label','legacy_spelling','project')),
  key_canon TEXT NOT NULL,
  key_fold  TEXT NOT NULL,
  used_from TEXT,                                -- その表記がその実体を指していた期間 (任意)
  used_to   TEXT,
  claim_id  TEXT NOT NULL REFERENCES claim(claim_id),
  PRIMARY KEY (town_id, surface)
) WITHOUT ROWID;
CREATE INDEX town_name_canon ON town_name(lg_code, key_canon);
CREATE INDEX town_name_fold  ON town_name(lg_code, key_fold);

-- ------------------------------------------------------------------ 4. 土地の移転 (有向グラフ。事象は 2. の change_event)
CREATE TABLE event_transfer (
  event_id      TEXT NOT NULL REFERENCES change_event(event_id),
  from_id       TEXT NOT NULL REFERENCES town(town_id),
  to_id         TEXT NOT NULL REFERENCES town(town_id),
  portion       TEXT NOT NULL CHECK (portion IN ('whole','remainder','part','unknown')),  -- remainder = その時点の残り全部
  koaza_detail  TEXT,                            -- '寺地町ニノ部'
  share_of_from REAL CHECK (share_of_from IS NULL OR share_of_from BETWEEN 0 AND 1),   -- from (その時点) の面積に占める割合
  share_of_to   REAL CHECK (share_of_to   IS NULL OR share_of_to   BETWEEN 0 AND 1),   -- to の面積のうち from 由来の割合
  share_basis   TEXT,                            -- 'census_area:2005->2010' (法的な割合ではない)
  is_noise      INTEGER NOT NULL DEFAULT 0 CHECK (is_noise IN (0,1)),   -- 境界ずれの欠片 (2%未満)。既定では返さない
  claim_id      TEXT NOT NULL REFERENCES claim(claim_id),
  PRIMARY KEY (event_id, from_id, to_id),
  CHECK (from_id <> to_id)
) WITHOUT ROWID;
CREATE INDEX transfer_from ON event_transfer(from_id);
CREATE INDEX transfer_to   ON event_transfer(to_id);

-- ------------------------------------------------------------------ 5. 幾何フック (ポリゴンは持たない)
CREATE TABLE town_census (                       -- 国勢調査小地域 (e-Stat KEY_CODE は年ごとに意味が違うので年つき)
  town_id  TEXT NOT NULL REFERENCES town(town_id),
  year     INTEGER NOT NULL CHECK (year IN (2000,2005,2010,2015,2020,2025)),
  key_code TEXT NOT NULL,
  s_name   TEXT,                                 -- その年の表記
  relation TEXT NOT NULL CHECK (relation IN ('equal','contains','part_of','hosts_remnant','point_in')),
  share    REAL,                                 -- town 側の面積のうちこの小地域に入る割合
  claim_id TEXT NOT NULL REFERENCES claim(claim_id),
  PRIMARY KEY (town_id, year, key_code)
) WITHOUT ROWID;
CREATE INDEX town_census_key ON town_census(year, key_code);

-- ============================================================================================
-- 別 D1: addrnorm_ops (読み書き。ビルドで作り直さない) -- schema/0001_ops.sql。上とは別のデータベースで実行する
-- ============================================================================================
CREATE TABLE report (
  report_id     TEXT PRIMARY KEY,               -- ULID
  received_at   TEXT NOT NULL,
  via           TEXT NOT NULL CHECK (via IN ('gomisute-gps','maintainer')),   -- v0.1 は RPC のみ
  lg_code TEXT, town_id TEXT,
  input_text    TEXT,                           -- 町字・小字まで。番地以降は保存しない
  as_of         TEXT,
  kind          TEXT NOT NULL CHECK (kind IN ('unresolved_label','wrong_target','missing_name','still_exists','abolished','wrong_date','other')),
  body          TEXT CHECK (body IS NULL OR length(body) <= 2000),
  evidence_url  TEXT,
  reporter_hash TEXT,                           -- 連絡先は保存しない。重複検知用 HMAC のみ
  data_version  TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new','triaged','accepted','rejected','duplicate')),
  resolution    TEXT                            -- 採用した調査レコードのパス / 却下理由
);
CREATE TABLE unresolved_query (
  lg_code TEXT NOT NULL, key_canon TEXT NOT NULL, sample_text TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('not_found','ambiguous','uncertain')),
  hits INTEGER NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL, last_data_version TEXT,
  PRIMARY KEY (lg_code, key_canon)
) WITHOUT ROWID;
CREATE TABLE research_queue (
  queue_id   TEXT PRIMARY KEY,                  -- '{reason}:{lg}:{town_id|key}'
  lg_code    TEXT NOT NULL,
  town_id    TEXT,
  name       TEXT,
  reason     TEXT NOT NULL CHECK (reason IN ('abr_added','abr_removed','abr_renamed','abr_attr_changed','census_gone',
                                             'phantom_no_parcels','abr_unconfirmed','fold_collision','unresolved_query',
                                             'user_report','low_confidence','successor_unknown','usage_conflict')),
  priority   INTEGER NOT NULL DEFAULT 50,
  detail     TEXT,
  created_at TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','in_progress','done','wontfix'))
);