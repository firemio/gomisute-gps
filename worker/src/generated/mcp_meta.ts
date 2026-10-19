// mcp_server.py のツールの定義・RULES・対応する自治体 (mcp_server.py から抜き出したもの)
// nationwide/packs/build_packs.py が作る。手で直さない
export default {
 "PROTOCOL": "2025-06-18",
 "SUPPORTED": [
  "2025-06-18",
  "2025-03-26",
  "2024-11-05"
 ],
 "SERVER": {
  "name": "gomisute-gps",
  "version": "0.8.0"
 },
 "RULES": "答え方: status=ok で answerable=true のときだけ、days[].collections[].official_label を確かな答えとして言い、sources の題名を出典に添える。label_note がある品目は、見出し (official_label) と一緒に label_note の内容 (今の出し方) を必ず伝える。ok でも answerable=false (範囲外の日・確認が古い) なら、分からない日は「分からない」と言う。ambiguous: 地区を1つに決めない。candidates と how_to_confirm、notes を使って、市町村名・丁目・街区・番号・地番・行政区などを利用者に聞き返す。consensus があれば、どの候補でも同じ答えだけは言ってよい。unresolved: decision_blocked_by.reason のとおりに理由を伝え、contact を案内する。candidates は確かな答えではない。in_calendar_range=false や collections=null の日は「分からない」と答え、「収集なし」と言わない。collections=[] のときだけ「収集なし」。is_exception=true の品目は「通常の規則 (rule_text) と違う日」と添え、理由を推測しない。12月・1月は規則から曜日を計算せず、返った日付だけを使う。出す時刻は put_out_by。not_covered にあること (分別方法・集積所の場所など) は推測しない。freshness.status=source_changed なら元の公式ページが変わっている。日付を言わずに公式ページを案内する。out_of_coverage なら coverage の自治体だけに対応していると伝える。",
 "INSTRUCTIONS": "日本の家庭ごみの収集日と分け方を、自治体の公式資料から作った検証済みデータで答える。答え方: status=ok で answerable=true のときだけ、days[].collections[].official_label を確かな答えとして言い、sources の題名を出典に添える。label_note がある品目は、見出し (official_label) と一緒に label_note の内容 (今の出し方) を必ず伝える。ok でも answerable=false (範囲外の日・確認が古い) なら、分からない日は「分からない」と言う。ambiguous: 地区を1つに決めない。candidates と how_to_confirm、notes を使って、市町村名・丁目・街区・番号・地番・行政区などを利用者に聞き返す。consensus があれば、どの候補でも同じ答えだけは言ってよい。unresolved: decision_blocked_by.reason のとおりに理由を伝え、contact を案内する。candidates は確かな答えではない。in_calendar_range=false や collections=null の日は「分からない」と答え、「収集なし」と言わない。collections=[] のときだけ「収集なし」。is_exception=true の品目は「通常の規則 (rule_text) と違う日」と添え、理由を推測しない。12月・1月は規則から曜日を計算せず、返った日付だけを使う。出す時刻は put_out_by。not_covered にあること (分別方法・集積所の場所など) は推測しない。freshness.status=source_changed なら元の公式ページが変わっている。日付を言わずに公式ページを案内する。out_of_coverage なら coverage の自治体だけに対応していると伝える。",
 "TOOLS": [
  {
   "name": "garbage_collection_by_address",
   "description": "住所と日付から、その住所で家庭ごみを出せる品目を、自治体の公式の区域表とカレンダーに基づいて返す。対応: 石川県金沢市 (2026年度。住居表示の住所と地番)、宮城県女川町 (2026年度。行政区の名前、または大字などの地名)。住所は利用者の言葉のまま、市町村名から渡す (例: 「金沢市笠舞2丁目29番5号」「金沢市福久町ロ21番地」「女川町針浜」)。建物名・部屋番号は落としてよい。答え方: status=ok で answerable=true のときだけ、days[].collections[].official_label を確かな答えとして言い、sources の題名を出典に添える。label_note がある品目は、見出し (official_label) と一緒に label_note の内容 (今の出し方) を必ず伝える。ok でも answerable=false (範囲外の日・確認が古い) なら、分からない日は「分からない」と言う。ambiguous: 地区を1つに決めない。candidates と how_to_confirm、notes を使って、市町村名・丁目・街区・番号・地番・行政区などを利用者に聞き返す。consensus があれば、どの候補でも同じ答えだけは言ってよい。unresolved: decision_blocked_by.reason のとおりに理由を伝え、contact を案内する。candidates は確かな答えではない。in_calendar_range=false や collections=null の日は「分からない」と答え、「収集なし」と言わない。collections=[] のときだけ「収集なし」。is_exception=true の品目は「通常の規則 (rule_text) と違う日」と添え、理由を推測しない。12月・1月は規則から曜日を計算せず、返った日付だけを使う。出す時刻は put_out_by。not_covered にあること (分別方法・集積所の場所など) は推測しない。freshness.status=source_changed なら元の公式ページが変わっている。日付を言わずに公式ページを案内する。out_of_coverage なら coverage の自治体だけに対応していると伝える。",
   "inputSchema": {
    "type": "object",
    "properties": {
     "address": {
      "type": "string",
      "maxLength": 200,
      "description": "住所の文字列 (市町村名から)"
     },
     "date": {
      "type": "string",
      "description": "日本時間の日付 YYYY-MM-DD。省略で今日"
     },
     "days": {
      "type": "integer",
      "minimum": 1,
      "maximum": 14,
      "description": "何日分 (既定 1)"
     },
     "detail": {
      "type": "string",
      "enum": [
       "summary",
       "full"
      ],
      "description": "summary (既定): 答えに要る項目だけ / full: 判定の根拠をすべて"
     }
    },
    "required": [
     "address"
    ]
   }
  },
  {
   "name": "garbage_collection_by_location",
   "description": "緯度経度と日付から、その場所で家庭ごみを出せる品目を返す (金沢市のみ。女川町は住所・行政区で garbage_collection_by_address を使う)。端末の位置の精度 accuracy_m が分かるときは渡す (Android・Chrome の coords.accuracy はそのまま。accuracy_confidence は省略でよい)。答え方: status=ok で answerable=true のときだけ、days[].collections[].official_label を確かな答えとして言い、sources の題名を出典に添える。label_note がある品目は、見出し (official_label) と一緒に label_note の内容 (今の出し方) を必ず伝える。ok でも answerable=false (範囲外の日・確認が古い) なら、分からない日は「分からない」と言う。ambiguous: 地区を1つに決めない。candidates と how_to_confirm、notes を使って、市町村名・丁目・街区・番号・地番・行政区などを利用者に聞き返す。consensus があれば、どの候補でも同じ答えだけは言ってよい。unresolved: decision_blocked_by.reason のとおりに理由を伝え、contact を案内する。candidates は確かな答えではない。in_calendar_range=false や collections=null の日は「分からない」と答え、「収集なし」と言わない。collections=[] のときだけ「収集なし」。is_exception=true の品目は「通常の規則 (rule_text) と違う日」と添え、理由を推測しない。12月・1月は規則から曜日を計算せず、返った日付だけを使う。出す時刻は put_out_by。not_covered にあること (分別方法・集積所の場所など) は推測しない。freshness.status=source_changed なら元の公式ページが変わっている。日付を言わずに公式ページを案内する。out_of_coverage なら coverage の自治体だけに対応していると伝える。",
   "inputSchema": {
    "type": "object",
    "properties": {
     "latitude": {
      "type": "number",
      "minimum": 20,
      "maximum": 46
     },
     "longitude": {
      "type": "number",
      "minimum": 122,
      "maximum": 154
     },
     "date": {
      "type": "string",
      "description": "日本時間の日付 YYYY-MM-DD。省略で今日"
     },
     "days": {
      "type": "integer",
      "minimum": 1,
      "maximum": 14,
      "description": "何日分 (既定 1)"
     },
     "accuracy_m": {
      "type": "number",
      "minimum": 0,
      "maximum": 1000
     },
     "accuracy_confidence": {
      "type": "number",
      "minimum": 0.5,
      "maximum": 0.99
     },
     "detail": {
      "type": "string",
      "enum": [
       "summary",
       "full"
      ],
      "description": "summary (既定): 答えに要る項目だけ / full: 判定の根拠をすべて"
     }
    },
    "required": [
     "latitude",
     "longitude"
    ]
   }
  },
  {
   "name": "garbage_sorting",
   "description": "品目 (例: ペットボトルのキャップ、色つきトレイ、発泡スチロール) をどの区分・かごに出すかを、その日付に有効な自治体の分別表で返す。対応: 宮城県女川町 (2026年度。2026-10-01 に分け方が変わったので、日付で答えが変わる)。金沢市の分別は未対応。status=ok のときだけ確かな答え。ambiguous なら材質・大きさなどを利用者に聞き返す。out_of_range・unresolved なら町に確かめるよう案内する。",
   "inputSchema": {
    "type": "object",
    "properties": {
     "municipality": {
      "type": "string",
      "description": "自治体名 (例: 女川町) か住所"
     },
     "item": {
      "type": "string",
      "maxLength": 200,
      "description": "品目"
     },
     "date": {
      "type": "string",
      "description": "日本時間の日付 YYYY-MM-DD。省略で今日"
     }
    },
    "required": [
     "municipality",
     "item"
    ]
   }
  }
 ],
 "COVERAGE": [
  {
   "lg_code": "172014",
   "name": "石川県金沢市",
   "calendar": "2026-04-01〜2027-03-31",
   "tools": [
    "garbage_collection_by_address",
    "garbage_collection_by_location"
   ]
  },
  {
   "lg_code": "045811",
   "name": "宮城県女川町",
   "calendar": "2026-04-01〜2027-03-31",
   "tools": [
    "garbage_collection_by_address (行政区・地名)",
    "garbage_sorting"
   ]
  }
 ],
 "OTHER_TOWN_KEYS": [
  "put_out_by",
  "contact",
  "not_covered",
  "holiday_policy",
  "calendar",
  "official_notes",
  "sources",
  "data_version",
  "town_rows"
 ]
};
