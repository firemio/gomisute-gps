#!/usr/bin/env python3
"""gomisute-gps の MCP サーバー (標準入出力の JSON-RPC 2.0。MCP SDK なしで動く最小実装)

  claude mcp add gomisute -- python /path/to/gomisute-gps/mcp_server.py
  あるいはリポジトリの .mcp.json をそのまま使う

LLM が「この場所・この住所で、この日に何のごみを出せるか」を確かな答えとして使うためのツールを出す。
答えは公式の資料から作ったデータだけに基づき、決められないときは決められないと返す (status / decision_blocked_by)。
既定では LLM が読む量を減らした要約 JSON を返し、detail="full" で resolve.py の JSON を全部返す。
"""
import json, math, os, re, sys, threading, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "gomisute-gps", "version": "0.8.0"}
RULES = ("答え方: status=ok で answerable=true のときだけ、days[].collections[].official_label を確かな答えとして言い、sources の題名を出典に添える。"
         "label_note がある品目は、見出し (official_label) と一緒に label_note の内容 (今の出し方) を必ず伝える。"
         "ok でも answerable=false (範囲外の日・確認が古い) なら、分からない日は「分からない」と言う。"
         "ambiguous: 地区を1つに決めない。candidates と how_to_confirm、notes を使って、市町村名・丁目・街区・番号・地番・行政区などを利用者に聞き返す。"
         "consensus があれば、どの候補でも同じ答えだけは言ってよい。"
         "unresolved: decision_blocked_by.reason のとおりに理由を伝え、contact を案内する。candidates は確かな答えではない。"
         "in_calendar_range=false や collections=null の日は「分からない」と答え、「収集なし」と言わない。collections=[] のときだけ「収集なし」。"
         "is_exception=true の品目は「通常の規則 (rule_text) と違う日」と添え、理由を推測しない。12月・1月は規則から曜日を計算せず、返った日付だけを使う。"
         "出す時刻は put_out_by。not_covered にあること (分別方法・集積所の場所など) は推測しない。"
         "freshness.status=source_changed なら元の公式ページが変わっている。日付を言わずに公式ページを案内する。"
         "out_of_coverage なら coverage の自治体だけに対応していると伝える。")
INSTRUCTIONS = "日本の家庭ごみの収集日と分け方を、自治体の公式資料から作った検証済みデータで答える。" + RULES

_state = {"k": None, "err": None}
_ready = threading.Event()

def _load():
    try:
        import resolve
        _state["resolve"] = resolve
        _state["k"] = resolve.Kanazawa()
        import kzaddress
        _state["k"]._addr_index = kzaddress.AddressIndex(_state["k"])      # 住所の索引も先に作る (最初の呼び出しを待たせない)
        try:
            import onagawa
            _state["onagawa_mod"] = onagawa
            _state["onagawa"] = onagawa.Onagawa()
        except (ImportError, FileNotFoundError):      # 女川町のデータがまだ無いときは金沢だけで動く
            _state["onagawa"] = None
    except Exception:
        _state["err"] = traceback.format_exc()
    finally:
        _ready.set()

threading.Thread(target=_load, daemon=True).start()

DATE = {"type": "string", "description": "日本時間の日付 YYYY-MM-DD。省略で今日"}
DAYS = {"type": "integer", "minimum": 1, "maximum": 14, "description": "何日分 (既定 1)"}
DETAIL = {"type": "string", "enum": ["summary", "full"], "description": "summary (既定): 答えに要る項目だけ / full: 判定の根拠をすべて"}
TOOLS = [
    {"name": "garbage_collection_by_address",
     "description": ("住所と日付から、その住所で家庭ごみを出せる品目を、自治体の公式の区域表とカレンダーに基づいて返す。"
                     "対応: 石川県金沢市 (2026年度。住居表示の住所と地番)、宮城県女川町 (2026年度。行政区の名前、または大字などの地名)。"
                     "住所は利用者の言葉のまま、市町村名から渡す (例: 「金沢市笠舞2丁目29番5号」「金沢市福久町ロ21番地」「女川町針浜」)。"
                     "建物名・部屋番号は落としてよい。" + RULES),
     "inputSchema": {"type": "object", "properties": {"address": {"type": "string", "maxLength": 200, "description": "住所の文字列 (市町村名から)"},
                                                      "date": DATE, "days": DAYS, "detail": DETAIL},
                     "required": ["address"]}},
    {"name": "garbage_collection_by_location",
     "description": ("緯度経度と日付から、その場所で家庭ごみを出せる品目を返す (金沢市のみ。女川町は住所・行政区で garbage_collection_by_address を使う)。"
                     "端末の位置の精度 accuracy_m が分かるときは渡す (Android・Chrome の coords.accuracy はそのまま。accuracy_confidence は省略でよい)。" + RULES),
     "inputSchema": {"type": "object", "properties": {"latitude": {"type": "number", "minimum": 20, "maximum": 46},
                                                      "longitude": {"type": "number", "minimum": 122, "maximum": 154},
                                                      "date": DATE, "days": DAYS, "accuracy_m": {"type": "number", "minimum": 0, "maximum": 1000},
                                                      "accuracy_confidence": {"type": "number", "minimum": 0.5, "maximum": 0.99},
                                                      "detail": DETAIL},
                     "required": ["latitude", "longitude"]}},
    {"name": "garbage_sorting",
     "description": ("品目 (例: ペットボトルのキャップ、色つきトレイ、発泡スチロール) をどの区分・かごに出すかを、その日付に有効な自治体の分別表で返す。"
                     "対応: 宮城県女川町 (2026年度。2026-10-01 に分け方が変わったので、日付で答えが変わる)。金沢市の分別は未対応。"
                     "status=ok のときだけ確かな答え。ambiguous なら材質・大きさなどを利用者に聞き返す。out_of_range・unresolved なら町に確かめるよう案内する。"),
     "inputSchema": {"type": "object", "properties": {"municipality": {"type": "string", "description": "自治体名 (例: 女川町) か住所"},
                                                      "item": {"type": "string", "maxLength": 200, "description": "品目"}, "date": DATE},
                     "required": ["municipality", "item"]}},
]

def _num(v):
    try: return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except (OverflowError, TypeError): return False                                        # 1 の後ろに 0 が400個、など

def _text(v, name, limit=200):
    """文字列の引数を確かめる → (値, 誤りの文 or None)"""
    if not isinstance(v, str) or not v.strip(): return None, f"{name} は文字列で指定してください。"
    if len(v) > limit: return None, f"{name} は {limit} 文字までにしてください。"
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in v): return None, f"{name} に読めない文字が入っています。"
    return v, None

def check_args(name, a):
    """引数の型と範囲を先に確かめる → (そろえた引数, 誤りの文 or None)。処理の中で Python の例外を出さないため"""
    a = dict(a)
    if a.get("days") is None: a["days"] = 1
    d = a["days"]
    if isinstance(d, float) and d.is_integer(): a["days"] = d = int(d)                  # JSON Schema の integer は 2.0 も認める
    if isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 14: return a, "days は 1〜14 の整数で指定してください。"
    if a.get("date") is not None and not isinstance(a["date"], str): return a, "date は日本時間の日付 (YYYY-MM-DD) の文字列で指定してください。"
    if name == "garbage_collection_by_address":
        a["address"], e = _text(a.get("address"), "address")
        return a, e
    if name == "garbage_collection_by_location":
        for k in ("latitude", "longitude"):
            if not _num(a.get(k)): return a, f"{k} は数 (度) で指定してください。"
        for k in ("accuracy_m", "accuracy_confidence"):
            if a.get(k) is not None and not _num(a[k]): return a, f"{k} は数で指定してください (分からなければ省略)。"
        return a, None
    if name == "garbage_sorting":
        a["municipality"], e = _text(a.get("municipality"), "municipality", 100)
        if e: return a, e + " (例: 女川町)"
        a["item"], e = _text(a.get("item"), "item")
        return a, e
    return a, None

def _day_compact(d):
    return {"date": d["date"], "weekday": d.get("weekday"), "in_calendar_range": d.get("in_calendar_range"),
            "collections": None if d.get("collections") is None else
            [{k: c[k] for k in ("official_label", "rule_text", "is_exception", "label_note") if k in c} for c in d["collections"]],
            **({"note": d["note"]} if d.get("note") else {})}

def compact(res):
    """LLM が答えるのに要る項目だけ。候補・確かめる行・規則と違う日の印・見出しの注は削らない。
    ok のときは日ごとの答えを days[] に1回だけ入れ、候補には根拠と確かめる行だけを残す"""
    loc = res.get("location") or {}
    st = res.get("status")
    out = {"status": st, "answerable": res.get("answerable"), "summary_ja": res.get("summary_ja")}
    if loc:
        out["location"] = {k: loc.get(k) for k in ("municipality", "collection_district", "district_confidence", "method", "place_name", "place_match",
                                                  "inference", "decision_blocked_by", "collection_district_before_change") if loc.get(k) is not None}
        if isinstance(loc.get("address"), dict): out["location"]["address"] = {k: loc["address"].get(k) for k in ("label", "municipality_stated")}
        if loc.get("nearest_address"): out["location"]["nearest_address"] = {k: loc["nearest_address"].get(k) for k in ("label", "distance_m")}
        if loc.get("nearest_parcel"): out["location"]["nearest_parcel"] = {k: loc["nearest_parcel"].get(k) for k in ("label", "distance_m")}
    if res.get("days"): out["days"] = [_day_compact(d) for d in res["days"]]
    if res.get("next_after_date"):
        out["next_after_date"] = [{k: x.get(k) for k in ("official_label", "date", "weekday", "null_reason", "label_note") if x.get(k)} for x in res["next_after_date"]]
    if res.get("candidates"):
        out["candidates"] = [{"collection_district": c.get("collection_district"), "official": c.get("official"),
                              "basis": [b if isinstance(b, str) else (b.get("label") or b.get("kind")) for b in c.get("basis", [])],
                              "how_to_confirm": c.get("how_to_confirm", []),
                              **({"days": [_day_compact(d) for d in c["days"]]} if c.get("days") and st != "ok" else {})}
                             for c in res["candidates"]]
    elif res.get("town_rows") and st != "ok":
        out["town_rows"] = res["town_rows"]
    if res.get("notes"): out["notes"] = res["notes"]
    for k in ("consensus", "official_notes", "put_out_by", "not_covered", "holiday_policy", "contact", "calendar", "data_version", "freshness",
              "matches", "other_periods", "decision_blocked_by", "coverage"):
        if res.get(k) not in (None, [], {}, ""): out[k] = res[k]
    if res.get("sources"): out["sources"] = [{k: s.get(k) for k in ("title", "url", "retrieved_at") if s.get(k)} for s in res["sources"]]
    return out

COVERAGE = [{"lg_code": "172014", "name": "石川県金沢市", "calendar": "2026-04-01〜2027-03-31",
             "tools": ["garbage_collection_by_address", "garbage_collection_by_location"]},
            {"lg_code": "045811", "name": "宮城県女川町", "calendar": "2026-04-01〜2027-03-31",
             "tools": ["garbage_collection_by_address (行政区・地名)", "garbage_sorting"]}]
OTHER_TOWN_KEYS = ("put_out_by", "contact", "not_covered", "holiday_policy", "calendar", "official_notes", "sources", "data_version", "town_rows")

def call(name, a):
    if not _ready.wait(120): raise RuntimeError("データの読み込みが終わっていません。少し待ってからもう一度呼んでください")
    if _state["err"]: raise RuntimeError("データの読み込みに失敗しました: " + _state["err"][-400:])
    r, k, ona, om = _state["resolve"], _state["k"], _state.get("onagawa"), _state.get("onagawa_mod")
    a, bad = check_args(name, a)
    if bad: return {"status": "invalid_input", "answerable": False, "summary_ja": bad}
    days = a["days"]
    if name == "garbage_collection_by_address":
        addr = a["address"]
        if ona and not om.is_onagawa(addr):                                     # 「女川針浜」「女川市場通り」「女川」(町を略した形) も女川町として引く
            m = re.match(r"^(日本国?)?(〒?\d{3}-?\d{4})?(宮城県)?(牡鹿郡)?(女川|おながわ)(?!町|市(?!場))(.*)$", om._n(addr))
            if m: addr = "女川町" + m[6]
        if ona and om.is_onagawa(addr): res = ona.resolve_address(addr, a.get("date"), days)
        else:
            res = r.resolve_address(k, addr, a.get("date"), days)
            b = (res.get("location") or {}).get("decision_blocked_by") or {}
            if ona and b.get("reason") == "address_not_found" and ona.match_place("女川町" + om._n(addr))[1]:
                hint = f"宮城県女川町の「{addr}」のことなら、garbage_collection_by_address に「女川町{addr}」で渡してください。"
                res["summary_ja"] = (res.get("summary_ja") or "") + hint
                res.setdefault("notes", []).append(hint)
    elif name == "garbage_collection_by_location":
        res = r.resolve(k, a["latitude"], a["longitude"], a.get("date"), days, online=True,
                        accuracy_m=a.get("accuracy_m"), accuracy_confidence=a.get("accuracy_confidence"))
    elif name == "garbage_sorting":
        m = a["municipality"]
        if ona and (om.is_onagawa(m) or om._n(m) in ("女川", "女川町", "おながわ", "おながわ町", "宮城県女川町", "宮城県牡鹿郡女川町")):
            return ona.sorting(a["item"], a.get("date"))
        return {"status": "out_of_coverage", "answerable": False, "coverage": COVERAGE,
                "summary_ja": f"「{m[:40]}」の分別表はまだデータにありません (対応: 宮城県女川町)。その自治体の公式サイトの分別表で確認してください。"}
    else:
        raise KeyError(name)
    if res.get("status") == "out_of_coverage":
        for x in OTHER_TOWN_KEYS: res.pop(x, None)                              # 金沢市の出す時刻・問い合わせ先を、ほかの町の答えに混ぜない
        res["coverage"] = COVERAGE if ona else COVERAGE[:1]
        if ona: res["summary_ja"] = (res.get("summary_ja") or "").replace("(対応: 石川県金沢市)", "(対応: 石川県金沢市、宮城県女川町)")
        mun = (res.get("location") or {}).get("municipality") or {}
        if ona and name == "garbage_collection_by_location" and isinstance(mun, dict) and mun.get("lg_code") == om.LG:
            res["summary_ja"] = ("この場所は宮城県女川町です。女川町は緯度経度では引けません。お住まいの行政区 (町民カレンダーの凡例の名前) か住所を利用者に聞いて、"
                                 "garbage_collection_by_address に「女川町<行政区>」で渡してください。")
            res["contact"] = ona.d["contact"]
    return res if a.get("detail") == "full" else compact(res)

def _err(mid, code, text):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

TOOL_NAMES = {t["name"] for t in TOOLS}

def result_for(msg):
    """1つの JSON-RPC メッセージ → 返す dict (通知・応答なら None)"""
    if not isinstance(msg, dict): return _err(None, -32600, "invalid request")
    is_note = "id" not in msg
    mid = msg.get("id")
    if not is_note and (isinstance(mid, bool) or not isinstance(mid, (str, int))):
        return _err(None, -32600, "invalid id")
    if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        if "method" not in msg and ("result" in msg or "error" in msg): return None      # 相手からの応答 (このサーバーは要求を出さないので捨てる)
        return None if is_note else _err(mid, -32600, "invalid request")
    method, params = msg["method"], msg.get("params", {})
    if params is None: params = {}
    if is_note: return None                                                               # 通知には返さない
    if not isinstance(params, dict): return _err(mid, -32602, "params must be an object")
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        v = params.get("protocolVersion")
        return ok({"protocolVersion": v if isinstance(v, str) and v in SUPPORTED else PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER, "instructions": INSTRUCTIONS})
    if method == "ping": return ok({})
    if method == "tools/list": return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or name not in TOOL_NAMES: return _err(mid, -32602, f"unknown tool: {str(name)[:60]}")
        if args is None: args = {}
        if not isinstance(args, dict): return _err(mid, -32602, "arguments must be an object")
        try:
            res = call(name, args)
            return ok({"content": [{"type": "text", "text": _dump(res)}], "isError": res.get("status") == "invalid_input"})
        except RuntimeError as e:
            return ok({"content": [{"type": "text", "text": f"エラー: {e}"}], "isError": True})
        except Exception as e:
            return ok({"content": [{"type": "text", "text": f"エラー: この入力を処理できませんでした ({type(e).__name__})"}], "isError": True})
    return _err(mid, -32601, f"method not found: {method[:60]}")

def _scrub(o):
    if isinstance(o, float) and not math.isfinite(o): return None
    if isinstance(o, dict): return {k: _scrub(v) for k, v in o.items()}
    if isinstance(o, list): return [_scrub(v) for v in o]
    return o

def _dump(o):
    """JSON の文字列。NaN は null に、UTF-8 にできない文字 (孤立サロゲート) は \\u でエスケープする"""
    try: s = json.dumps(o, ensure_ascii=False, allow_nan=False)
    except ValueError: s = json.dumps(_scrub(o), ensure_ascii=False)
    try: s.encode("utf-8")
    except UnicodeEncodeError: s = json.dumps(json.loads(s), ensure_ascii=True)
    return s

def _one(m):
    try: return result_for(m)
    except Exception as e:
        mid = m.get("id") if isinstance(m, dict) and isinstance(m.get("id"), (str, int)) and not isinstance(m.get("id"), bool) else None
        return _err(mid, -32603, f"internal error ({type(e).__name__})")

def handle_line(raw):
    """1行 (bytes) → 返す JSON の文字列 (返さないなら None)。どんな入力でもサーバーは落ちない"""
    try:
        line = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return _dump(_err(None, -32700, "parse error (not UTF-8)"))
    if not line: return None
    try:
        msg = json.loads(line)
    except (ValueError, RecursionError, MemoryError):                                    # JSONDecodeError、桁が多すぎる整数 (4300桁超) など
        return _dump(_err(None, -32700, "parse error"))
    try:
        if isinstance(msg, list):
            if not msg: return _dump(_err(None, -32600, "empty batch"))
            outs = [o for o in map(_one, msg) if o is not None]                     # 1つが失敗しても、ほかの返事は返す
            return _dump(outs) if outs else None
        out = _one(msg)
        return _dump(out) if out is not None else None
    except Exception as e:
        return _dump(_err(None, -32603, f"internal error ({type(e).__name__})"))

def main():
    out = open(sys.stdout.fileno(), "wb", buffering=0, closefd=False)
    for raw in open(sys.stdin.fileno(), "rb", closefd=False):
        try: reply = handle_line(raw)
        except Exception as e: reply = _dump(_err(None, -32603, f"internal error ({type(e).__name__})"))   # 何があっても次の行を読み続ける
        if reply is not None:
            out.write((reply + "\n").encode("utf-8", errors="backslashreplace")); out.flush()

if __name__ == "__main__":
    main()
