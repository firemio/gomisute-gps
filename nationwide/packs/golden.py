#!/usr/bin/env python3
"""Worker (TypeScript) が Python と同じ答えを返すかを確かめる正解 (golden) を作る

  python nationwide/packs/golden.py [--n-jukyo 215] [--n-parcel 165] [--seed 20261018] [--loc-scale 1] [--only 組,組] [--prefetch-gps]
  → worker/test/golden/<組>.json.gz と worker/test/golden/meta.json

mcp_server.handle_line() (標準入出力の MCP サーバーと同じ処理) に JSON-RPC の行を渡し、返った行をそのまま記録する。
ツールの呼び出しは、要約 (既定) と detail="full" の2回。要約は返った行をそのまま、full は行の sha256 と、
比べる項目 (status・answerable・collection_district・候補の地区・日ごとの品目・consensus・decision_blocked_by.reason・summary_ja) を書く
(10件に1件は full の行もそのまま)。
時刻 (FIXED_NOW)・公式ページの変更監視の結果 (STATUS の場面)・gps2city の返事は固定して記録し、Worker のテストも同じものを使う。

組:
  kanazawa_address  金沢市の住所。bench/eval_address.py と同じ書き方 (住居表示 12通り・地番 5通り) を実在の住所から抜き出して作る。
                    計測の 38問 (docs/mcp-bench-*.json) と、検証役・文書に出てくる住所、数字を ❷・➀・፩・① などに置き換えた形も入れる
  onagawa_address   女川町の行政区・地名 × 日付
  sorting           女川町の分別。品目 × 2026-09-30/10-01 ほか
  location          緯度経度 (約5,200件): 金沢市の住所・地番の近く、区域 (地区) と国勢調査の町の境目、隣の市町との境界、海岸、
                    住居表示住所の無い区域表の行の近く、区域表に無い町・注記のある町など × accuracy_m (なし・10・30・60・200m・小数) と、
                    市の外 (周りの市町・海・女川町・日本のどこか)。地点は種だけで決まる (location_points)。--prefetch-gps で gps2city の返事を先に取れる
  invalid           入力の誤り
  protocol          MCP (JSON-RPC) の層: バッチ・通知・誤った id・パースできない行・4300桁を超える整数・深い入れ子 (知らないツールの name も) など
  freshness         変更監視の結果の場面ごと (変わった・消えた・取れなかった・古い・無い)

組ごとに乱数と JSON-RPC の id を分けるので、--only で一部の組だけ作り直しても、全部作ったときと同じファイルになる。
--only のとき、ほかの組のファイルと meta.json の記録はそのまま残す (時刻・場面・gps2city の返事が meta.json と違えば止める)。
"""
import argparse, base64, copy, datetime as dt, gzip, hashlib, json, os, random, re, sys, time, types, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ["GOMISUTE_AUTO_MONITOR"] = "0"            # 監視のスレッドを動かさない (公式ページを取りに行かない)
try: sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError): pass

import mcp_server, resolve, onagawa                    # mcp_server は読み込みのスレッドを始める (約5秒)

OUT = os.path.join(ROOT, "worker", "test", "golden")
GPS_CACHE = os.path.join(ROOT, "data", "golden_gps2city.json")     # gps2city の返事 (取り直さないため。data/ は git に入れない)
JST = resolve.JST
FIXED_NOW = dt.datetime(2026, 10, 18, 21, 0, 0, tzinfo=JST)

# ---------------------------------------------------------------- 固定する時刻
class FixedDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW.replace(tzinfo=None)

_fake_dt = types.ModuleType("datetime")
_fake_dt.__dict__.update(dt.__dict__)
_fake_dt.datetime = FixedDatetime
resolve.dt = _fake_dt
onagawa.dt = _fake_dt

# ---------------------------------------------------------------- 変更監視の結果の場面
REG = json.load(open("out/source_registry.json", encoding="utf-8"))
KZ_CAL1 = "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/19353.html"
KZ_RULES = resolve.RULES_SOURCE["url"]
KZ_PUT = resolve.PUT_OUT_RULES["url"]
ONA_CAL = "https://www.town.onagawa.miyagi.jp/pdf/calendar/r08_chomin_calendar.pdf"
ONA_PAGE = "https://www.town.onagawa.miyagi.jp/05_05.html"

def _status(age_hours=3.0, change=(), gone=(), error=(), drop=(), simulated=False):
    src = []
    for u, r in REG.items():
        if u in drop: continue
        st = "changed" if u in change else "error: HTTP Error 404: Not Found" if u in gone else "error: <urlopen error timed out>" if u in error else "unchanged"
        src.append({"url": u, "municipality": r["municipality"], "kind": r["kind"], "title": r["title"], "status": st,
                    "recorded_sha256": r["content_sha256"], "current_sha256": None if st.startswith("error") else ("simulated" if simulated and st == "changed" else r["content_sha256"]),
                    "recorded_at": r["recorded_at"]})
    return {"checked_at": (FIXED_NOW - dt.timedelta(hours=age_hours)).isoformat(timespec="seconds"), "simulated": simulated, "sources": src}

STATUS = {
    "fresh": _status(),
    "stale": _status(age_hours=24 * 8.5),
    "unchecked": None,
    "kz_calendar_changed": _status(change=(KZ_CAL1,)),
    "kz_rules_changed": _status(change=(KZ_RULES,), simulated=True),
    "kz_calendar_gone": _status(gone=(KZ_CAL1,)),
    "kz_put_out_error": _status(error=(KZ_PUT,)),
    "kz_put_out_untracked": _status(drop=(KZ_PUT,)),
    "ona_calendar_changed": _status(change=(ONA_CAL,)),
    "ona_page_error": _status(error=(ONA_PAGE,)),
    "none_tracked": {"checked_at": (FIXED_NOW - dt.timedelta(hours=1)).isoformat(timespec="seconds"), "simulated": False, "sources": []},
}
_cur = {"status": "fresh"}
def _load_status(path=None):
    return copy.deepcopy(STATUS[_cur["status"]])
resolve.load_source_status = _load_status
onagawa.load_source_status = _load_status

# ---------------------------------------------------------------- gps2city (返事を記録して使う)
def _gps_load():
    """前に記録した gps2city の返事 (data/ に無ければ、リポジトリの golden の meta.json から)"""
    if os.path.exists(GPS_CACHE): return json.load(open(GPS_CACHE, encoding="utf-8"))
    m = os.path.join(OUT, "meta.json")
    return json.load(open(m, encoding="utf-8")).get("gps2city", {}) if os.path.exists(m) else {}
_gps = _gps_load()
_gps_new = [0]
def _gps_key(lat, lng): return f"{float(lat)!r},{float(lng)!r}"
def _gps_save():
    os.makedirs(os.path.dirname(GPS_CACHE), exist_ok=True)
    json.dump(_gps, open(GPS_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
def gps2city_record(lat, lng):
    """本物の gps2city に聞く (1回だけ。結果は data/golden_gps2city.json に覚える) → {"status": HTTP の状態, "body": 返事}
    robots.txt を守り、1秒に1回まで、User-Agent で名乗る (polite.py)"""
    key = _gps_key(lat, lng)
    if key not in _gps:
        import polite
        body = json.dumps({"latitude": lat, "longitude": lng}).encode()
        for attempt in range(4):
            try:
                with polite.urlopen(resolve.GPS2CITY, data=body, headers={"Content-Type": "application/json"}, method="POST", timeout=10) as r:
                    _gps[key] = {"status": r.status, "body": json.load(r)}
                break
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 3: _gps[key] = {"status": e.code, "body": None}; break
            except urllib.error.URLError:
                if attempt == 3: raise
            time.sleep(5.0 * (attempt + 1))             # 届かない・5xx はしばらく待って取り直す
        _gps_new[0] += 1
        if _gps_new[0] % 25 == 0: _gps_save()           # 毎回書くと遅いので 25件ごと (最後にも書く)
    return _gps[key]

def _fake_gps2city(lat, lng):
    g = gps2city_record(lat, lng)
    if g["status"] != 200: raise urllib.error.HTTPError(resolve.GPS2CITY, g["status"], "Not Found" if g["status"] == 404 else "Error", {}, None)
    return copy.deepcopy(g["body"])
resolve.gps2city = _fake_gps2city

# ---------------------------------------------------------------- 1件を記録する
def line(o):
    """JSON の1行。孤立サロゲートを含むときは ASCII だけの形にする (UTF-8 にできないため)"""
    s = json.dumps(o, ensure_ascii=False)
    try: s.encode("utf-8")
    except UnicodeEncodeError: s = json.dumps(o, ensure_ascii=True)
    return s

def rpc(raw):
    return mcp_server.handle_line(raw if isinstance(raw, bytes) else raw.encode("utf-8"))

_ids = [0]
def tool_req(name, args, mid=None):
    _ids[0] += 1
    return line({"jsonrpc": "2.0", "id": mid if mid is not None else _ids[0], "method": "tools/call", "params": {"name": name, "arguments": args}})

def excerpt(res_text):
    """full の答えから比べる項目を抜き出す"""
    try:
        r = json.loads(res_text)
        t = r["result"]["content"][0]["text"]
        o = json.loads(t)
    except Exception:
        return None
    if not isinstance(o, dict): return None
    loc = o.get("location") or {}
    b = loc.get("decision_blocked_by") or o.get("decision_blocked_by") or {}
    day = lambda d: {"date": d.get("date"), "collections": None if d.get("collections") is None else [[c.get("official_label"), c.get("is_exception")] for c in d["collections"]]}
    return {"status": o.get("status"), "answerable": o.get("answerable"), "collection_district": loc.get("collection_district"),
            "candidates": [c.get("collection_district") for c in o.get("candidates") or []],
            "days": [day(d) for d in o.get("days") or []],
            "candidate_days": [[day(d) for d in c.get("days") or []] for c in o.get("candidates") or []],
            "consensus": o.get("consensus"), "reason": b.get("reason"), "summary_ja": o.get("summary_ja"),
            "matches": [[m.get("item"), m.get("destination")] for m in o.get("matches") or []]}

CASES = {}
def case(group, cid, name, args, status="fresh", keep_full=False, **extra):
    _cur["status"] = status
    req = tool_req(name, args)
    res = rpc(req)
    fa = dict(args); fa["detail"] = "full"
    req_full = tool_req(name, fa)
    res_full = rpc(req_full)
    c = {"id": cid, "status": status, "req": req, "res": res, "req_full": req_full,
         "full_sha256": hashlib.sha256(res_full.encode("utf-8")).hexdigest(), "full": excerpt(res_full), **extra}
    if keep_full: c["full_res"] = res_full
    CASES.setdefault(group, []).append(c)
    _cur["status"] = "fresh"
    return c

def raw_case(group, cid, raw, status="fresh"):
    _cur["status"] = status
    rb = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    try: rb.decode("utf-8"); c = {"id": cid, "status": status, "req": rb.decode("utf-8")}
    except UnicodeDecodeError: c = {"id": cid, "status": status, "req_b64": base64.b64encode(rb).decode()}
    c["res"] = rpc(rb)
    CASES.setdefault(group, []).append(c)
    _cur["status"] = "fresh"

ADDR, LOCN, SORT = "garbage_collection_by_address", "garbage_collection_by_location", "garbage_sorting"

# ---------------------------------------------------------------- 金沢市の住所
KAN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
def kan(n):
    if n <= 10: return KAN[n]
    if n < 20: return "十" + KAN[n - 10]
    if n < 100: return KAN[n // 10] + "十" + (KAN[n % 10] if n % 10 else "")
    return str(n)

DATES = ["2026-10-20", "2026-10-21", "2026-10-23", "2026-11-03", "2026-11-12", "2026-11-23", "2026-12-01", "2026-12-08", "2026-12-28", "2026-12-29",
         "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-04", "2027-01-05", "2027-01-08", "2027-01-15", "2027-01-22", "2027-01-29",
         "2027-03-30", "2027-03-31", "2027-04-01", "2026-03-31", "2026-04-01", "2026-09-29", "2026-06-30", None]

def date_args(rnd):
    a = {}
    d = rnd.choice(DATES + ["random"] * 6)
    if d == "random": d = str(dt.date(2026, 4, 1) + dt.timedelta(days=rnd.randrange(0, 365)))
    if d: a["date"] = d
    n = rnd.choice([None, 1, 1, 1, 2, 3, 7, 14])
    if n: a["days"] = n
    return a

def kanazawa_cases(rnd, n_jukyo, n_parcel):
    import pandas as pd
    k = mcp_server._state["k"]
    J = k.jukyo.dropna(subset=["blk_i", "num_i"])
    J = J.loc[rnd.sample(list(J.index), n_jukyo)]
    i = 0
    def add(form, text):
        nonlocal i
        i += 1
        case("kanazawa_address", f"kz{i:05d}-{form}", ADDR, {"address": text, **date_args(rnd)}, keep_full=(i % 10 == 0), form=form)
    for t, raw, b, n in zip(J.ntown, J.town, J.blk_i.astype(int), J.num_i.astype(int)):
        m = re.match(r"^(.*?)(\d+)丁目$", t)
        add("jukyo_canonical", f"金沢市{raw}{b}番{n}号")
        add("jukyo_hyphen", f"金沢市{m[1]}{m[2]}-{b}-{n}" if m else f"金沢市{t}{b}-{n}")
        add("jukyo_kanji", f"金沢市{raw}{kan(b)}番{kan(n)}号")
        add("jukyo_banchi_form", f"金沢市{raw}{b}番地{n}")
        add("jukyo_with_room", f"金沢市{raw}{b}-{n} {rnd.choice(['2階', '201号室', '1F', 'サンハイツ101', '2号棟'])}")
        add("jukyo_no_city", f"{raw}{b}番{n}号")
        add("jukyo_space_separated", f"金沢市{raw} {b} {n}")
        add("jukyo_postal_code", f"〒920-0000 金沢市{raw}{b}番{n}号")
        add("jukyo_other_dash", f"金沢市{raw}{b}{rnd.choice(['―', '－', '−', 'ー', '–'])}{n}")
        add("town_only", f"金沢市{raw}")
        if m: add("jukyo_chome_dropped", f"金沢市{m[1]}{b}番{n}号")
        if m: add("jukyo_chome_dropped_hyphen", f"金沢市{m[1]}{b}-{n}")
    pn = pd.read_csv("data/abr/mt_parcel_city172014.csv", dtype=str, usecols=["oaza_cho", "chome", "koaza", "prc_num1", "prc_num2", "rsdt_addr_flg"]).fillna("")
    pn = pn[(pn.rsdt_addr_flg == "0") & (pn.prc_num1 != "")]
    pn = pn.loc[rnd.sample(list(pn.index), n_parcel)]
    for oz, ch, kz, n1, n2 in zip(pn.oaza_cho, pn.chome, pn.koaza, pn.prc_num1, pn.prc_num2):
        town, num = oz + ch, int(n1)
        add("chiban_canonical", f"金沢市{town}{kz}{num}番地" + (f"{n2}" if n2 else ""))
        add("chiban_hyphen", f"金沢市{town}{kz}{num}-{n2 or 1}")
        if ch: add("chiban_chome_dropped_hyphen", f"金沢市{oz}{num}-{n2 or 1}")
        if kz:
            add("chiban_aza", f"金沢市{town}字{kz}{num}番地")
            add("chiban_koaza_dropped", f"金沢市{town}{num}番地")

# 計測の質問 (docs/mcp-bench-*.json) と、検証役の指摘・文書に出てくる住所
REVIEW_ADDR = [
    "金沢市笠舞2丁目29番5号", "金沢市笠舞2-29-5", "金沢市笠舞2-29", "金沢市笠舞29番5号", "笠舞2丁目29番5号", "石川県金沢市笠舞2丁目29番5号",
    "金沢市福久町ロ21番地", "金沢市福久町ロ21-3", "金沢市窪町ロ12", "金沢市窪6-223-1", "金沢市泉野出町3-16", "金沢市泉野出町1番1号", "金沢市泉野出町",
    "泉野出町3-16", "押野2丁目1番1号", "押野2丁目", "金沢市押野2丁目1番1号", "金沢市主計町1番1号", "金沢市主計町", "金沢市寺町4丁目 1 8",
    "金沢市寺町4丁目1 8", "金沢市笠舞二丁目二十九番五号", "金沢市笠舞2丁目二十九番地五", "金沢市笠舞2丁目29番地の5", "金沢市笠舞2丁目29の5",
    "金沢市笠舞2丁目29ー5", "金沢市笠舞2丁目29 5 サンハイツ201", "〒920-0965 金沢市笠舞2丁目29番5号", "日本 〒920-0965 石川県金沢市笠舞2-29-5",
    "金沢市古府1番地1", "金沢市湊1-2", "金沢市福増町南638", "金沢市福増町南700番地", "金沢市福久町ロ86", "金沢市大桑町猫シタイ5番地", "金沢市神谷内町1番地",
    "金沢市赤土町リ1", "金沢市しじま台2丁目75番地", "金沢市高柳町12の1", "金沢市兼六町1番1号", "金沢市玉川町2番", "金沢市中村町8丁目1番",
    "金沢市太陽が丘3丁目1番1号", "金沢市諸江町中丁1番地", "金沢市", "金沢市本町1丁目1番1号", "本町1丁目1番1号", "金沢市広坂1丁目1番1号",
    "金沢市入江3丁目1番1号", "金沢市東山1丁目1番1号", "金沢市八幡町1番地", "金沢市入江町", "金沢市平栗町イ1番地", "金沢市伝燈寺町イ1", "金沢市傅燈寺町イ1",
    "金沢市木ノ新保7番丁", "金沢市木の新保七番丁", "金沢市もりの里1丁目1番1号", "金沢市鞍月東1丁目1番地", "金沢市鞍月東1丁1",
    "石川県野々市市押野2丁目1番1号", "野々市市押野2丁目", "東京都千代田区千代田1-1", "静岡県静岡市清水区港町1丁目", "北海道札幌市中央区北1条西2丁目",
    "大阪府大阪市北区梅田1-1", "京都府京都市中京区寺町通", "石川県白山市倉光1丁目", "河北郡内灘町字鶴ケ丘1", "白山市倉光", "能美郡川北町壱ツ屋",
    "金沢市七八十丁目", "金沢市二十十丁目1番1号", "金沢市一二丁目", "金沢市笠舞٢-٢٩-٥", "金沢市笠舞2-29-5\n2階", "金沢市笠舞2-29-5\r\n201",
    "　金沢市笠舞2-29-5　", "金沢市笠舞2‐29‐5", "金沢市笠舞2―29―5", "金沢市笠舞２丁目２９番５号", "金沢市笠舞2丁目29番5号201号室",
    "金沢市笠舞2丁目29番5号 2階", "金沢市笠舞2丁目29番", "金沢市笠舞2丁目", "金沢市笠舞", "金沢市笠", "金沢市針浜", "針浜", "浦宿浜", "女川", "女川町",
    "女川針浜", "女川市場通り", "女川町清水区", "女川町清水行政区", "女川町江島", "おながわ町針浜", "宮城県牡鹿郡女川町針浜", "宮城県女川町浦宿浜",
    "女川町鷲神浜字内山", "女川町市場通り", "女川町浦宿浜字浦宿", "女川町針浜字唐松43", "女川町石浜1-2", "女川町旭が丘二丁目9-1", "女川町東京",
    "金沢市大字福久町ロ21", "金沢市字福久町ロ21", "金沢市福久町字ロ21番地", "金沢市福久町大字ロ21番地", "金沢市尾張町1丁目1番16号",
    "金沢市駅西本町6丁目6番27号", "金沢市弥生2丁目15番1号", "金沢市平和町2丁目4番13号", "金沢市御影町9番30号", "金沢市泉野町2丁目12番22号",
    "金沢市泉野町5丁目", "金沢市長坂台7番38号", "金沢市田井町11番18号", "金沢市並木町2番21号", "金沢市彦三町1丁目1-1-1001",
    "金沢市彦三町1-1-1-1001", "金沢市彦三町1丁目1番1号1001", "金沢市9999番", "金沢市笠舞2丁目9999番9999号", "金沢市笠舞2-9999-1", "1-2-3", "金沢市1-2-3",
    "ﾃｽﾄ", "test", "金沢市abc", "金沢市笠舞2丁目29番5号金沢市笠舞2丁目29番5号", "🍙金沢市笠舞2-29-5", "金沢市笠舞2-29-5🍙",
    "金沢市九九九九九九九九九九九九九九九九九九九番一号", "金沢市笠舞2丁目〇番〇号", "金沢市笠舞2丁目百番", "金沢市笠舞2丁目29番5号の2",
    "金沢市笠舞2丁目99999999999999999999番1号", "金沢市笠舞2-99999999999999999999-1", "金沢市笠舞2丁目9007199254740993番", "金沢市福久町ロ99999999999999999999",
    "金沢市笠舞2丁目٢٩番٥号", "金沢市笠舞2丁目２９番５号", "金沢市笠舞2丁目29番5号‐201", "金沢市笠舞 2丁目 29番 5号", "金沢市 笠舞2丁目29番5号",
    "石川県 金沢市 笠舞 2-29-5", "金沢市泉野出町1丁目3番16号", "金沢市泉野出町3丁目16番", "金沢市泉野出町3丁目", "金沢市湊2丁目1番地2",
    "金沢市古府2丁目1番地1", "金沢市古府町291番地", "金沢市古府町南291番地", "金沢市古府町字南291番地4", "金沢市主計町2番3号", "金沢市彦三町1丁目",
]

def review_cases(rnd):
    for f in ("mcp-bench-kanazawa.json", "mcp-bench-onagawa.json"):
        for c in json.load(open(os.path.join("docs", f), encoding="utf-8"))["cases"]:
            q = c["question"]
            m = re.match(r"^(.+?) に住んでいます。(\d{4})年(\d+)月(\d+)日", q)
            if not m: continue
            d = f"{m[2]}-{int(m[3]):02d}-{int(m[4]):02d}"
            for addr in dict.fromkeys([m[1], re.sub(r"^(石川県|宮城県牡鹿郡)", "", m[1])]):
                case("kanazawa_address", f"bench-{c['id']}-{addr}", ADDR, {"address": addr, "date": d}, keep_full=True, form="bench")
    for i, a in enumerate(REVIEW_ADDR):
        case("kanazawa_address", f"review{i:03d}", ADDR, {"address": a, **date_args(rnd)}, keep_full=True, form="review")

# str.isdigit() は真だが 10進数字ではない文字 (❷・➀・⓵・፩ など。NFKC でも ASCII にならない) と、NFKC で数字になる文字 (①・²・⑴)。
# kzaddress.town_of は「笠舞2」の次が数字なら丁目にしないので、isdigit() の範囲が Worker と違うと答えが変わる
ODD_DIGITS = {"1": "❶➀➊⓵፩𐩀𐹠𑁒᧚①⑴⒈¹₁", "2": "❷➁➋⓶፪𐩁𐹡𑁓②⑵⒉²₂", "3": "❸➂➌⓷፫𐩂𐹢𑁔③⑶⒊³₃", "4": "❹➃➍⓸፬𐩃𐹣𑁕④⑷⒋⁴₄",
              "5": "❺➄➎⓹፭𐹤𑁖⑤⑸⒌⁵₅", "6": "❻➅➏⓺፮𐹥𑁗⑥⑹⒍⁶₆", "7": "❼➆➐⓻፯𐹦𑁘⑦⑺⒎⁷₇", "8": "❽➇➑⓼፰𐹧𑁙⑧⑻⒏⁸₈",
              "9": "❾➈➒⓽፱𐹨𑁚⑨⑼⒐⁹₉", "0": "⓿⓪⁰₀"}

def digit_cases(rnd):
    """金沢市の住所の数字を ODD_DIGITS の文字に置き換えた形 (全部・頭の数字の後ろだけ・1文字だけ)"""
    base = [json.loads(c["req"])["params"]["arguments"]["address"] for c in CASES["kanazawa_address"]
            if c.get("form") in ("jukyo_canonical", "jukyo_hyphen", "jukyo_banchi_form", "chiban_canonical", "chiban_hyphen")]
    odd = lambda ch: rnd.choice(ODD_DIGITS[ch]) if ch in ODD_DIGITS else ch
    i = 0
    for a in rnd.sample(base, 40) + ["金沢市笠舞2丁目29番5号", "金沢市笠舞2-29-5", "金沢市福久町ロ21番地"]:
        m = re.search(r"\d+", a)
        pos = [j for j, ch in enumerate(a) if ch in ODD_DIGITS]
        p = rnd.choice(pos) if pos else -1
        forms = ["".join(map(odd, a)),                                                            # 数字を全部
                 a[:m.end()] + "".join(map(odd, re.sub(r"^\D+", "", a[m.end():]))) if m else a,   # 頭の数字のすぐ後ろに続ける (大額3❷❻❷番地 の形)
                 "".join(odd(ch) if j == p else ch for j, ch in enumerate(a))]                   # 1文字だけ
        for f in dict.fromkeys(forms):
            i += 1
            case("kanazawa_address", f"digit{i:03d}", ADDR, {"address": f, "date": "2026-10-20"}, keep_full=True, form="odd_digits")

# ---------------------------------------------------------------- 女川町
def onagawa_cases(rnd):
    ona = mcp_server._state["onagawa"]
    names = sorted({v["name"] for v in ona.gk.values()})
    ds = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-12-28", "2026-12-29", "2026-12-31", "2027-01-01", "2027-01-04", "2027-01-05",
          "2027-03-31", "2027-04-01", "2026-03-31", "2026-04-01", "2026-11-23", None]
    forms = ["女川町{}", "女川町{}区", "女川{}", "宮城県牡鹿郡女川町{}", "女川町{}行政区", "女川町{}地区", "〒986-2200 宮城県女川町{}", "おながわ町{}"]
    out = []
    for g in names:
        for f in rnd.sample(forms, 4): out.append(f.format(g))
    for p in ona.places:
        for f in rnd.sample(["女川町{}", "女川町{}字X1", "女川町{}1-2", "宮城県女川町{}", "女川町{}町", "{}"], 2): out.append(f.format(p))
    i = 0
    for a in out:
        i += 1
        d = rnd.choice(ds)
        args = {"address": a, **({"date": d} if d else {}), **({"days": rnd.choice([1, 2, 3, 7, 14])} if rnd.random() < 0.5 else {})}
        case("onagawa_address", f"ona{i:04d}", ADDR, args, keep_full=(i % 5 == 0))
    while i < 200:
        i += 1
        a = rnd.choice(names + list(ona.places))
        d = str(dt.date(2026, 4, 1) + dt.timedelta(days=rnd.randrange(-5, 370)))
        case("onagawa_address", f"ona{i:04d}", ADDR, {"address": "女川町" + a, "date": d, "days": rnd.choice([1, 3, 14])}, keep_full=(i % 5 == 0))

SORT_EXTRA = ["ペットボトルのキャップ", "ペットボトルの蓋", "ペットボトルのフタ", "ペットボトルのふた", "キャップ", "ふた", "フタ", "ラベル", "ペットボトルのラベル",
              "色つきトレイ", "色付きトレイ", "模様つきトレイ", "色トレー", "トレー", "トレイ", "白いトレイ", "白色トレー", "肉のトレイ", "発泡スチロール", "発砲スチロール",
              "プラスチックのバケツ", "ポリバケツ", "バケツ", "金属のハンガー", "プラスチックのハンガー", "ハンガー", "牛乳パック", "牛乳", "紙パック", "銀色の紙パック",
              "新聞", "新聞紙", "雑誌", "雑誌類", "段ボール", "ダンボール", "たばこ", "加熱式たばこ", "電子たばこ", "モバイルバッテリー", "乾電池", "電池",
              "リチウムイオン電池", "植木鉢", "プラスチックの植木鉢", "陶器の植木鉢", "小さい植木鉢", "布団", "ふとん", "てんぷら油", "天ぷら油", "廃油", "食用油",
              "蛍光灯", "蛍光管", "冷蔵庫", "テレビ", "エアコン", "パソコン", "自転車", "びん", "ビン", "瓶", "びんのふた", "金属のふた", "プラスチックのふた",
              "缶", "空き缶", "スプレー缶", "なべ", "フライパン", "生ごみ", "ぬいぐるみ", "かばん", "靴", "傘", "ガラス", "割れたコップ", "カミソリ", "瀬戸物",
              "ドライヤー", "土", "衣類", "タオル", "カーテン", "剪定枝", "枝", "洗剤のボトル", "シャンプーのボトル", "プラスチックのボトル", "卵パック", "レジ袋",
              "お菓子の袋", "カップ麺の容器", "弁当のパック", "スプーン", "ストロー", "定規", "おもちゃ", "洗濯ばさみ", "洗面器", "ポンプ", "ふた・ポンプ",
              "ティッシュの箱", "封筒", "カレンダー", "包み紙", "文庫本", "教科書", "チラシ", "雑紙", "ゴムホース", "ステンレスの鍋", "アルミホイル",
              "ガラスのコップ", "スチール缶", "アルミ缶", "金属", "プラスチック", "紙", "ペット", "PETボトル", "ペットボトル", "ペットボトル本体", "空きびん",
              "ジュースのびん", "お酒のびん", "化粧品のびん", "スマートフォン", "携帯電話", "ゲーム機", "ノートパソコン", "オートバイ", "バイク", "タイヤ",
              "消火器", "ガスボンベ", "灯油", "ペンキ", "薬", "注射針", "おむつ", "紙おむつ", "猫砂", "ペットのふん", "木の枝", "竹", "草", "落ち葉", "石",
              "コンクリート", "レンガ", "ブロック", "引越しのごみ", "事業ごみ", "プランター", "ザル", "ボウル", "まな板", "ペットボトルのキャップとラベル",
              "  ペットボトルのキャップ  ", "ﾍﾟｯﾄﾎﾞﾄﾙ", "ＰＥＴボトル", "ぺっとぼとる", "a", "ab", "x" * 200, "不明な品目"]

def sorting_cases(rnd):
    ona = mcp_server._state["onagawa"]
    items = []
    for r in ona.d["sorting_rules"]:
        items.append(r["item"])
        items += [n for n, _ in onagawa.item_names(r["item"])]
        items += re.findall(r"[（(](.*?)[）)]", r["item"])
        items += [x for g in re.findall(r"[（(](.*?)[）)]", r["item"]) for x in re.split(r"[・、：:]", g)]
    items = [x for x in dict.fromkeys(items + SORT_EXTRA) if x and x.strip()]
    rnd.shuffle(items)
    items = items[:300]
    i = 0
    for it in items:
        for d in ("2026-09-30", "2026-10-01"):
            i += 1
            mun = rnd.choice(["女川町", "女川町", "女川町", "宮城県牡鹿郡女川町", "宮城県女川町", "女川", "おながわ", "女川町針浜"])
            case("sorting", f"sort{i:04d}", SORT, {"municipality": mun, "item": it, "date": d}, keep_full=False)
    for it in items[:60]:
        i += 1
        d = rnd.choice(["2026-04-01", "2027-03-31", "2027-04-01", "2026-03-31", None, "2026-12-31"])
        case("sorting", f"sort{i:04d}", SORT, {"municipality": "女川町", "item": it, **({"date": d} if d else {})})
    for mun in ("金沢市", "東京都千代田区", "石川県金沢市", "おながわ町", "宮城県牡鹿郡女川町", "女川町役場", "女川町針浜", "x" * 101, "  "):
        i += 1
        case("sorting", f"sort{i:04d}", SORT, {"municipality": mun, "item": "ペットボトルのキャップ", "date": "2026-10-20"})

# ---------------------------------------------------------------- 緯度経度
LOC_POINTS = [
    ("tokyo", 35.681236, 139.767125, {}), ("onagawa", 38.4453, 141.4467, {}), ("onagawa_hama", 38.4235, 141.4485, {}),
    ("nonoichi", 36.5193, 136.6097, {}), ("hakusan", 36.5147, 136.5658, {}), ("uchinada", 36.6531, 136.6450, {}),
    ("sea_japan", 36.75, 136.40, {}), ("sea_pacific", 30.0, 140.0, {}), ("okinawa", 26.2124, 127.6809, {}),
    ("sapporo", 43.0621, 141.3544, {}), ("tsushima", 34.2, 129.29, {}), ("tokyo_acc", 35.681236, 139.767125, {"accuracy_m": 200}),
    ("tokyo_acc_conf", 35.681236, 139.767125, {"accuracy_m": 30, "accuracy_confidence": 0.95}),
    ("kanazawa_low_acc", 36.561325, 136.656205, {"accuracy_m": 500}), ("kanazawa_low_acc95", 36.561325, 136.656205, {"accuracy_m": 300, "accuracy_confidence": 0.95}),
    ("kanazawa_low_acc_int", 36.56, 136.66, {"accuracy_m": 1000, "accuracy_confidence": 0.5}),
]
KANAZAWA_POINTS = [("kanazawa_station", 36.578044, 136.648018), ("kasamai", 36.5547, 136.6714), ("c25", 36.591999, 136.641164),
                   # めったに出ない判定の例 (Python で金沢市の中の点を3万点調べて見つけたもの)
                   ("kazuemachi_note", 36.572618, 136.662589), ("address_row_only", 36.589851, 136.648417), ("town_row_prefix", 36.554392, 136.656563),
                   ("conditional_similar_row", 36.535137, 136.709504), ("only_nearby_evidence", 36.609319, 136.611599),
                   ("far_address_not_in_table", 36.576139, 136.648996), ("census_town_not_in_table", 36.578605, 136.649379),
                   ("no_official_row", 36.603705, 136.586314)]

# 金沢市の中と周りの地点の種類と数 (scale=1 のとき)。地点は種 (seed) だけで決まる (--prefetch-gps で gps2city の返事を先に取るときも同じ地点)
LOC_KINDS = [("jukyo", 650), ("parcel", 550), ("city", 400), ("zone_border", 500), ("town_border", 150), ("muni_border", 400), ("coast", 80),
             ("unindexed", 100), ("special", 150), ("near_city", 150), ("noted", 15), ("onagawa", 40), ("japan", 40)]

def location_points(seed, scale=1.0):
    """[(種類, 緯度, 経度)]。金沢市の住所・地番の近く、区域 (地区) の境目、国勢調査の町の境目、隣の市町との境界、海岸、
    住居表示住所の無い区域表の行の近く、区域表に無い町・旧町名の町・主計町・金沢港の水面などと、市の外 (周りの市町・海・女川町・日本のどこか)"""
    import math, pyproj, shapely
    k = mcp_server._state["k"]
    rnd = random.Random(seed)
    to_ll = pyproj.Transformer.from_crs(6675, 4326, always_xy=True)
    to_xy = pyproj.Transformer.from_crs(4326, 6675, always_xy=True)
    out = []
    def put(kind, x, y):
        lng, lat = to_ll.transform(x, y)
        out.append((kind, round(lat, 6), round(lng, 6)))
    def jitter(x, y, r):                          # 半径 r の円の中で一様に
        a, d = rnd.uniform(0, 2 * math.pi), r * math.sqrt(rnd.random())
        return x + d * math.cos(a), y + d * math.sin(a)
    def on_lines(geoms):                          # 線の上で一様に
        lens = [g.length for g in geoms]
        r = rnd.uniform(0, sum(lens))
        for g, n in zip(geoms, lens):
            if r <= n: return g.interpolate(r)
            r -= n
        return geoms[-1].interpolate(geoms[-1].length)
    def in_polys(polys):                          # 多角形の中で一様に (面積で重みづけ、外れたら引き直す)
        areas = [g.area for g in polys]
        g = rnd.choices(polys, weights=areas)[0]
        x0, y0, x1, y1 = g.bounds
        for _ in range(10000):
            q = shapely.Point(rnd.uniform(x0, x1), rnd.uniform(y0, y1))
            if g.contains(q): return q
        return g.representative_point()
    n = lambda c: max(1, round(c * scale))
    counts = dict(LOC_KINDS)
    jx, jy = shapely.get_x(k.jgeom), shapely.get_y(k.jgeom)
    for _ in range(n(counts["jukyo"])):
        i = rnd.randrange(len(jx)); put("jukyo", *jitter(jx[i], jy[i], 60))
    px, py = shapely.get_x(k.pgeom), shapely.get_y(k.pgeom)
    for _ in range(n(counts["parcel"])):
        i = rnd.randrange(len(px)); put("parcel", *jitter(px[i], py[i], 80))
    for _ in range(n(counts["city"])):
        q = in_polys([k.city]); put("city", q.x, q.y)
    rings = [ring for g in k.parts.geometry for poly in getattr(g, "geoms", [g]) for ring in [poly.exterior, *poly.interiors]]
    for _ in range(n(counts["zone_border"])):
        q = on_lines(rings); put("zone_border", *jitter(q.x, q.y, 25))
    trings = [ring for g in k.towns.geometry for poly in getattr(g, "geoms", [g]) for ring in [poly.exterior, *poly.interiors]]
    for _ in range(n(counts["town_border"])):
        q = on_lines(trings); put("town_border", *jitter(q.x, q.y, 25))
    border = list(k.land_border.geoms)
    for _ in range(n(counts["muni_border"])):
        q = on_lines(border); put("muni_border", *jitter(q.x, q.y, rnd.choice([30, 120, 450])))
    city_rings = [ring for poly in getattr(k.city, "geoms", [k.city]) for ring in [poly.exterior, *poly.interiors]]
    c = 0
    while c < n(counts["coast"]):                 # 市の境のうち、隣の市町との境界から離れた所 = 海岸
        q = on_lines(city_rings)
        if k.land_border.distance(q) < 500: continue
        put("coast", *jitter(q.x, q.y, 350)); c += 1
    for _ in range(n(counts["unindexed"])):
        g = k.ui_geom[rnd.randrange(len(k.ui_geom))]; put("unindexed", *jitter(g.x, g.y, 150))
    from townnorm import norm_town
    special = [g for t, h, g in zip(k.towns.ntown, k.towns.HCODE, k.towns.geometry) if h != 8101 or not k.by_town.get(t) or k.via_old.get(t)]
    special += [g for t, m, g, i in zip(k.parts.town, k.parts.method, k.parts.geometry, k.parts.index)
                if m == "points_single" or i in k.enclave_parts or k.unindexed.get(norm_town(t)) or any(r["note"] for r in k.by_town.get(norm_town(t), []))]
    for i in range(n(counts["special"])):
        q = in_polys([special[i % len(special)]]); put("special", q.x, q.y)
    x0, y0, x1, y1 = k.city.bounds
    c = 0
    while c < n(counts["near_city"]):             # 市の周り 6km (周りの市町・海)
        q = shapely.Point(rnd.uniform(x0 - 6000, x1 + 6000), rnd.uniform(y0 - 6000, y1 + 6000))
        if k.city.contains(q): continue
        put("near_city", q.x, q.y); c += 1
    # 区域表に注記のある町 (主計町「実際の地区とは異なります」) の中 (別の乱数にして、ほかの種類の地点を変えない)
    r2 = random.Random(seed + 2)
    noted = [g for t, g in zip(k.parts.town, k.parts.geometry) if any(r["note"] for r in k.by_town.get(norm_town(t), []))]
    for _ in range(n(counts["noted"])):
        g = r2.choice(noted)
        x0_, y0_, x1_, y1_ = g.bounds
        while True:
            q = shapely.Point(r2.uniform(x0_, x1_), r2.uniform(y0_, y1_))
            if g.contains(q): break
        put("noted", q.x, q.y)
    for _ in range(n(counts["onagawa"])):         # 女川町の辺り (陸・海・隣の石巻市)
        out.append(("onagawa", round(rnd.uniform(38.38, 38.48), 6), round(rnd.uniform(141.38, 141.60), 6)))
    for _ in range(n(counts["japan"])):           # 日本のどこか (多くは海)
        out.append(("japan", round(rnd.uniform(24.0, 45.5), 6), round(rnd.uniform(123.0, 146.0), 6)))
    return out

def acc_args(rnd):
    """accuracy_m (なし・10・30・60・200m、ときどき小数) と accuracy_confidence"""
    v = rnd.choices([None, 10, 30, 60, 200, "float"], weights=[28, 18, 18, 14, 14, 8])[0]
    if v is None: return {}
    if v == "float": v = round(rnd.uniform(2, 120), 1)
    a = {"accuracy_m": v}
    c = rnd.choice([None, 0.95, 0.99]) if v == 200 else rnd.choice([None] * 6 + [0.5, 0.9, 0.95])
    if c is not None: a["accuracy_confidence"] = c
    return a

def location_cases(seed, scale=1.0):
    i = 0
    for name, lat, lng, extra in LOC_POINTS:
        for d in (None, "2026-10-20"):
            i += 1
            args = {"latitude": lat, "longitude": lng, **extra, **({"date": d} if d else {})}
            case("location", f"loc{i:05d}-{name}", LOCN, args, keep_full=True, gps2city={"latitude": lat, "longitude": lng, **gps2city_record(lat, lng)})
    for name, lat, lng in KANAZAWA_POINTS:
        for extra in ({}, {"accuracy_m": 30}, {"accuracy_m": 200, "accuracy_confidence": 0.95}):
            i += 1
            case("location", f"loc{i:05d}-{name}", LOCN, {"latitude": lat, "longitude": lng, "date": "2026-10-20", **extra}, keep_full=True)
    rnd = random.Random(seed + 1)
    for kind, lat, lng in location_points(seed, scale):
        for v in range(2 if rnd.random() < 0.62 else 1):
            i += 1
            case("location", f"loc{i:05d}-{kind}", LOCN, {"latitude": lat, "longitude": lng, **acc_args(rnd), **date_args(rnd)}, keep_full=(i % 10 == 0), kind=kind)

def prefetch_gps(seed, scale):
    """location の地点の gps2city の返事を先に取る (robots.txt を守り 1秒に1回。取ったものは data/golden_gps2city.json に覚える)"""
    pts = [(lat, lng) for _, lat, lng, _ in LOC_POINTS] + [(lat, lng) for _, lat, lng in KANAZAWA_POINTS] + [(lat, lng) for _, lat, lng in location_points(seed, scale)]
    todo = list(dict.fromkeys(p for p in pts if _gps_key(*p) not in _gps))
    print(f"地点 {len(pts)}・まだ取っていない {len(todo)} (約 {len(todo) // 60 + 1} 分)", flush=True)
    try:
        for j, (lat, lng) in enumerate(todo):
            gps2city_record(lat, lng)
            if j % 100 == 99: print(f"  {j + 1}/{len(todo)}", flush=True)
    finally: _gps_save()                    # 途中で止まっても、取った分は覚える

# ---------------------------------------------------------------- 入力の誤り
def invalid_cases():
    A = [
        {}, {"address": None}, {"address": ""}, {"address": "   "}, {"address": "　"}, {"address": 123}, {"address": ["金沢市"]}, {"address": {"a": 1}},
        {"address": True}, {"address": "x" * 200}, {"address": "x" * 201}, {"address": "金沢市" + "1" * 197}, {"address": "金沢市\ud800"},
        {"address": "金沢市笠舞2-29-5", "days": 0}, {"address": "金沢市笠舞2-29-5", "days": 15}, {"address": "金沢市笠舞2-29-5", "days": 2.0},
        {"address": "金沢市笠舞2-29-5", "days": 2.5}, {"address": "金沢市笠舞2-29-5", "days": True}, {"address": "金沢市笠舞2-29-5", "days": "3"},
        {"address": "金沢市笠舞2-29-5", "days": None}, {"address": "金沢市笠舞2-29-5", "days": -1}, {"address": "金沢市笠舞2-29-5", "days": 14},
        {"address": "金沢市笠舞2-29-5", "date": "2026/10/20"}, {"address": "金沢市笠舞2-29-5", "date": "2026-02-30"}, {"address": "金沢市笠舞2-29-5", "date": "1999-12-31"},
        {"address": "金沢市笠舞2-29-5", "date": "2100-01-01"}, {"address": "金沢市笠舞2-29-5", "date": 20261020}, {"address": "金沢市笠舞2-29-5", "date": " 2026-10-20 "},
        {"address": "金沢市笠舞2-29-5", "date": "2026-10-20T10:00:00"}, {"address": "金沢市笠舞2-29-5", "date": ""}, {"address": "金沢市笠舞2-29-5", "date": "２０２６-１０-２０"},
        {"address": "金沢市笠舞2-29-5", "date": "2026-1-2"}, {"address": "金沢市笠舞2-29-5", "date": None}, {"address": "金沢市笠舞2-29-5", "date": "x'y\"z"},
        {"address": "金沢市笠舞2-29-5", "date": "it's"}, {"address": "金沢市笠舞2-29-5", "date": "a\tb\nc\\d\u0001 é　"},
        {"address": "金沢市笠舞2-29-5", "detail": "verbose"}, {"address": "金沢市笠舞2-29-5", "detail": None}, {"address": "金沢市笠舞2-29-5", "extra": 1},
        {"address": "女川町針浜", "date": "2026/10/20"}, {"address": "女川町針浜", "date": "2000-01-01"}, {"address": "女川町針浜", "days": 0},
        {"address": "女川針浜", "date": "it's \"q\""},
    ]
    i = 0
    for a in A:
        i += 1
        case("invalid", f"inv{i:03d}", ADDR, a, keep_full=True)
    L = [
        {}, {"latitude": 36.5}, {"latitude": "36.5", "longitude": 136.6}, {"latitude": True, "longitude": 136.6}, {"latitude": None, "longitude": 136.6},
        {"latitude": 136.6, "longitude": 36.5}, {"latitude": 95, "longitude": 136.6}, {"latitude": 36.5, "longitude": 190}, {"latitude": -36.5, "longitude": -136.6},
        {"latitude": 36.5, "longitude": 136.6, "accuracy_m": -1}, {"latitude": 36.5, "longitude": 136.6, "accuracy_m": 1001}, {"latitude": 36.5, "longitude": 136.6, "accuracy_m": 1001.0},
        {"latitude": 36.5, "longitude": 136.6, "accuracy_m": "30"}, {"latitude": 36.5, "longitude": 136.6, "accuracy_confidence": 0.3},
        {"latitude": 36.5, "longitude": 136.6, "accuracy_confidence": 1}, {"latitude": 36.5, "longitude": 136.6, "accuracy_confidence": 1.0},
        {"latitude": 36.5, "longitude": 136.6, "accuracy_confidence": "0.9"}, {"latitude": 36.5, "longitude": 136.6, "date": "2026-13-01"},
        {"latitude": 36.5, "longitude": 136.6, "days": 0}, {"latitude": 36.5, "longitude": 136.6, "date": 5}, {"latitude": 1e308, "longitude": 136.6},
        {"latitude": 36, "longitude": 136, "accuracy_m": 2000}, {"latitude": 36.5, "longitude": 136.6, "accuracy_m": 1e300},
    ]
    for a in L:
        i += 1
        case("invalid", f"inv{i:03d}", LOCN, a, keep_full=True)
    S = [{}, {"municipality": "女川町"}, {"item": "キャップ"}, {"municipality": "", "item": "キャップ"}, {"municipality": "女川町", "item": ""},
         {"municipality": "女川町", "item": "x" * 201}, {"municipality": "女川町", "item": 5}, {"municipality": "女川町", "item": "キャップ", "date": "2026/10/01"},
         {"municipality": "女川町", "item": "キャップ", "date": 1}, {"municipality": "女川町", "item": "キャップ", "days": 99}, {"municipality": "女川町\udfff", "item": "キャップ"},
         {"municipality": "女川町", "item": "キャップ\ud83d"}]
    for a in S:
        i += 1
        case("invalid", f"inv{i:03d}", SORT, a, keep_full=True)
    # 生の JSON (NaN・Infinity・大きい数・float の id など)
    raws = [
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":NaN,"longitude":136.6}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":Infinity,"longitude":136.6}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":-Infinity}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":1e400,"longitude":136.6}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":1' + "0" * 400 + ',"longitude":136.6}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":1' + "0" * 30 + ',"longitude":136.6}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":NaN}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":2000}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":2000.0}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":1e3,"accuracy_confidence":0.2}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_confidence":1e1}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_confidence":123456789012345678901234567890}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":1e-7,"accuracy_confidence":0.99}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_location","arguments":{"latitude":36.5,"longitude":136.6,"accuracy_m":1e16}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":1e400}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":3.0}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":3e0}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":NaN}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":-0.0}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","days":12345678901234567890123}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"\\ud800金沢市"}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"\\ud83c\\udf59金沢市笠舞2-29-5"}}}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_sorting","arguments":{"municipality":"女川町","item":"\\udc00"}}}',
    ]
    for r in raws:
        i += 1
        raw_case("invalid", f"inv{i:03d}", r)

# ---------------------------------------------------------------- MCP の層
def protocol_cases():
    R = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}',
        '{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}',
        '{"jsonrpc":"2.0","id":3,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}',
        '{"jsonrpc":"2.0","id":4,"method":"initialize","params":{"protocolVersion":"2099-01-01"}}',
        '{"jsonrpc":"2.0","id":5,"method":"initialize","params":{"protocolVersion":5}}',
        '{"jsonrpc":"2.0","id":6,"method":"initialize"}', '{"jsonrpc":"2.0","id":7,"method":"initialize","params":null}',
        '{"jsonrpc":"2.0","id":8,"method":"initialize","params":[]}', '{"jsonrpc":"2.0","id":"s","method":"ping"}', '{"jsonrpc":"2.0","id":0,"method":"ping"}',
        '{"jsonrpc":"2.0","id":-5,"method":"ping"}', '{"jsonrpc":"2.0","id":1.0,"method":"ping"}', '{"jsonrpc":"2.0","id":1e2,"method":"ping"}',
        '{"jsonrpc":"2.0","id":true,"method":"ping"}', '{"jsonrpc":"2.0","id":null,"method":"ping"}', '{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}',
        '{"jsonrpc":"2.0","id":[1],"method":"ping"}', '{"jsonrpc":"2.0","id":123456789012345678901234567890,"method":"ping"}',
        '{"jsonrpc":"2.0","id":-9007199254740993,"method":"ping"}', '{"jsonrpc":"2.0","id":"日本語\\u3000id","method":"ping"}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}', '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}',
        '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}', '{"jsonrpc":"2.0","method":"anything","params":5}',
        '{"jsonrpc":"2.0","id":9,"result":{}}', '{"jsonrpc":"2.0","id":9,"error":{"code":1,"message":"x"}}', '{"id":9,"result":{}}',
        '{"jsonrpc":"1.0","id":10,"method":"ping"}', '{"id":11,"method":"ping"}', '{"jsonrpc":"2.0","id":12}', '{"jsonrpc":"2.0","id":13,"method":5}',
        '{"jsonrpc":"2.0","method":5}', '{"jsonrpc":2.0,"id":14,"method":"ping"}', '{"jsonrpc":"2.0","id":15,"method":"tools/list"}',
        '{"jsonrpc":"2.0","id":16,"method":"tools/list","params":{"cursor":"x"}}', '{"jsonrpc":"2.0","id":17,"method":"resources/list"}',
        '{"jsonrpc":"2.0","id":18,"method":"' + "m" * 100 + '"}', '{"jsonrpc":"2.0","id":19,"method":"\\ud800"}', '{"jsonrpc":"2.0","id":20,"method":"日本語のメソッド"}',
        '{"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"nope"}}', '{"jsonrpc":"2.0","id":22,"method":"tools/call","params":{"name":5}}',
        '{"jsonrpc":"2.0","id":23,"method":"tools/call","params":{"name":null}}', '{"jsonrpc":"2.0","id":24,"method":"tools/call","params":{}}',
        '{"jsonrpc":"2.0","id":25,"method":"tools/call","params":{"name":{"b":1,"a":[1,2.5,null,true,"x\'y"]}}}',
        '{"jsonrpc":"2.0","id":26,"method":"tools/call","params":{"name":"' + "n" * 100 + '"}}',
        '{"jsonrpc":"2.0","id":27,"method":"tools/call","params":{"name":"garbage_sorting","arguments":[]}}',
        '{"jsonrpc":"2.0","id":28,"method":"tools/call","params":{"name":"garbage_sorting","arguments":"x"}}',
        '{"jsonrpc":"2.0","id":29,"method":"tools/call","params":{"name":"garbage_sorting","arguments":null}}',
        '{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"garbage_collection_by_address"}}',
        '{"jsonrpc":"2.0","id":31,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","date":"2026-10-20"}}}',
        '{"jsonrpc":"2.0","id":32,"method":"tools/call","params":[1,2]}', '{"jsonrpc":"2.0","id":33,"method":"tools/call","params":"x"}',
        '[]', '[1]', '[1,2,3]', '["x"]', '[{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","method":"notifications/initialized"},{"jsonrpc":"2.0","id":2,"method":"tools/list"}]',
        '[{"jsonrpc":"2.0","method":"notifications/initialized"}]', '[{"jsonrpc":"2.0","id":1,"result":{}}]', '[[]]', '[{"jsonrpc":"2.0","id":1,"method":"ping"},5,null]',
        '[{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_collection_by_address","arguments":{"address":"金沢市笠舞2-29-5","date":"2026-10-20"}}},'
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"garbage_sorting","arguments":{"municipality":"女川町","item":"キャップ","date":"2026-10-01"}}}]',
        '5', '"x"', 'null', 'true', '{}', '{"a":1}', '', '   ', '\t\n', '　', '　{"jsonrpc":"2.0","id":1,"method":"ping"}　', '{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        '{', '}', '[', '{"jsonrpc":"2.0","id":1,"method":"ping",}', "{'a':1}", '{"jsonrpc":"2.0","id":1,"method":"ping"} x', 'NaN', '-Infinity', '1' * 4300, '1' * 4301,
        '-' + '1' * 4301, '1' * 4301 + '.5', '1' * 5000 + 'e1', '[' * 2997 + ']' * 2997, '[' * 2998 + ']' * 2998, '[' * 2999 + ']' * 2999, '[' * 100000,
        '{"a":' * 2997 + '1' + '}' * 2997, '{"a":' * 2998 + '1' + '}' * 2998, '{"a":' * 2999 + '1' + '}' * 2999,
        '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":' + '[' * 2996 + ']' * 2996 + '}}', '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":' + '[' * 2997 + ']' * 2997 + '}}',
        '{"jsonrpc":"2.0","id":1,"method":"ping","x":"\u0001"}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":"\\u0001\\uffff\\ud800\\udc00"}',
        '{"jsonrpc":"2.0","id":1,"method":"ping","x":"\\x"}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":01}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":.5}',
        '{"jsonrpc":"2.0","id":1,"method":"ping","x":1.}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":+1}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":1e}',
        '{"jsonrpc":"2.0","id":1,"method":"ping","x":-}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":tru}', '{"jsonrpc":"2.0","id":1,"method":"ping","x":nan}',
        '{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}', '{"jsonrpc":"2.0","method":"ping","id":3,"method":"tools/list"}', '{"__proto__":{"id":1},"jsonrpc":"2.0","method":"ping"}',
        '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"__proto__":null}}', '{"jsonrpc" : "2.0" , "id" : 1 , "method" : "ping" }',
        '﻿{"jsonrpc":"2.0","id":1,"method":"ping"}', '{"jsonrpc":"2.0","id":1,"method":"ping"}\x00', '{"jsonrpc":"2.0","id":"\\u0000","method":"ping"}',
        '{"jsonrpc":"2.0","id":"a\\"b\\\\c\\/d\\b\\f\\n\\r\\t","method":"ping"}', '{"jsonrpc":"2.0","id":"  \u007f\u0080","method":"ping"}',
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"garbage_sorting","arguments":{"municipality":"女川町","item":"キャップ","date":"2026-10-01","detail":"full"}}}',
        # 知らないツールの name が深い入れ子 (repr を 60文字で切る。入れ子の上限 2998 段のすぐ下まで)
        '{"jsonrpc":"2.0","id":34,"method":"tools/call","params":{"name":' + '{"a":' * 2996 + '1' + '}' * 2996 + '}}',
        '{"jsonrpc":"2.0","id":35,"method":"tools/call","params":{"name":' + '[' * 2996 + ']' * 2996 + '}}',
        '{"jsonrpc":"2.0","id":36,"method":"tools/call","params":{"name":' + '[{"b":' * 1498 + '"x"' + '}]' * 1498 + '}}',
        '{"jsonrpc":"2.0","id":37,"method":"tools/call","params":{"name":' + "{\"k'\":[1.5," * 1498 + 'null' + ']}' * 1498 + '}}',
        '[{"jsonrpc":"2.0","id":38,"method":"tools/call","params":{"name":' + '{"a":' * 2995 + '1' + '}' * 2995 + '}},{"jsonrpc":"2.0","id":39,"method":"ping"}]',
        '{"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":' + '{"a":' * 2997 + '1' + '}' * 2997 + '}}',
    ]
    for i, r in enumerate(R):
        raw_case("protocol", f"proto{i:03d}", r)
    for i, b in enumerate([b"\xff\xfe", b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"\xc3"}', b"\xed\xa0\x80", b'{"jsonrpc":"2.0","id":1,"method":"\xe3\x81"}']):
        raw_case("protocol", f"proto_bytes{i}", b)

# ---------------------------------------------------------------- 変更監視の場面
def freshness_cases(rnd):
    addrs = ["金沢市笠舞2丁目29番5号", "金沢市泉野出町3-16", "金沢市主計町1番1号", "金沢市福久町ロ21番地", "金沢市入江町", "金沢市笠舞29番5号",
             "押野2丁目1番1号", "金沢市二ツ屋町9番3号", "金沢市広坂1丁目2番17号"]
    onas = ["女川町針浜", "女川町浦宿浜", "女川町江島", "女川町東京", "女川町鷲神浜字内山"]
    items = ["ペットボトルのキャップ", "トレイ", "冷蔵庫", "不明な品目"]
    i = 0
    for st in STATUS:
        if st == "fresh": continue
        for a in addrs:
            i += 1
            case("freshness", f"fr{i:03d}-{st}", ADDR, {"address": a, "date": rnd.choice(["2026-10-20", "2026-12-31", "2027-04-01"]), "days": rnd.choice([1, 3])}, status=st, keep_full=True)
        for a in onas:
            i += 1
            case("freshness", f"fr{i:03d}-{st}", ADDR, {"address": a, "date": rnd.choice(["2026-10-20", "2026-12-31", "2027-04-01"])}, status=st, keep_full=True)
        for it in items:
            i += 1
            case("freshness", f"fr{i:03d}-{st}", SORT, {"municipality": "女川町", "item": it, "date": rnd.choice(["2026-09-30", "2026-10-01", "2027-04-01"])}, status=st)
        for name, lat, lng, extra in LOC_POINTS[:2] + LOC_POINTS[13:14] + [("kasamai", 36.5547, 136.6714, {}), ("c25", 36.591999, 136.641164, {"accuracy_m": 30}),
                                                                           ("kazuemachi_note", 36.572618, 136.662589, {})]:
            i += 1
            case("freshness", f"fr{i:03d}-{st}", LOCN, {"latitude": lat, "longitude": lng, **extra}, status=st,
                 gps2city={"latitude": lat, "longitude": lng, **gps2city_record(lat, lng)})

# ---------------------------------------------------------------- 本体
GROUPS = ["kanazawa_address", "onagawa_address", "sorting", "location", "invalid", "protocol", "freshness"]

def write_out(out, only, made):
    """組のファイルと meta.json を書く。--only のときは、ほかの組のファイルと meta の記録 (件数・種など) をそのまま残す。
    ほかの組は前の meta の時刻・場面・gps2city の返事で作ったものなので、それが今と違えば混ぜずに止める"""
    os.makedirs(out, exist_ok=True)
    mp = os.path.join(out, "meta.json")
    old = json.load(open(mp, encoding="utf-8")) if only and os.path.exists(mp) else {}
    keep = [f[:-len(".json.gz")] for f in sorted(os.listdir(out)) if f.endswith(".json.gz") and only and f[:-len(".json.gz")] not in only]
    shared = json.loads(json.dumps({"fixed_now": FIXED_NOW.isoformat(), "json_max_depth": 2998, "status": STATUS}))
    gps = dict(_gps)
    if keep:
        for k, v in shared.items():
            if k in old and old[k] != v: sys.exit(f"meta.json の {k} が今と違うので、--only では作れない (全部の組を作り直してください)")
        for k, v in old.get("gps2city", {}).items():
            if gps.setdefault(k, v) != v: sys.exit(f"gps2city の返事 ({k}) が meta.json と違うので、--only では作れない (全部の組を作り直してください)")
    groups = {}
    for g in keep:                              # 作り直さない組: 記録はそのまま (無ければファイルから数える)
        if g in old.get("groups", {}): groups[g] = old["groups"][g]; continue
        gz = open(os.path.join(out, f"{g}.json.gz"), "rb").read()
        raw = gzip.decompress(gz)
        groups[g] = {"cases": len(json.loads(raw)["cases"]), "bytes": len(raw), "gzip_bytes": len(gz)}
    for f in os.listdir(out):
        if f.endswith(".json.gz") and f[:-len(".json.gz")] not in keep: os.remove(os.path.join(out, f))
    for g, cs in CASES.items():
        raw = json.dumps({"group": g, "cases": cs}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        gz = gzip.compress(raw, compresslevel=9, mtime=0)
        open(os.path.join(out, f"{g}.json.gz"), "wb").write(gz)
        groups[g] = {"cases": len(cs), "bytes": len(raw), "gzip_bytes": len(gz), **made[g]}
    groups = dict(sorted(groups.items(), key=lambda x: (GROUPS.index(x[0]) if x[0] in GROUPS else len(GROUPS), x[0])))
    meta = {"generated_by": "nationwide/packs/golden.py", **shared, "gps2city": gps, "groups": groups}
    json.dump(meta, open(mp, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    for g, s in groups.items(): print(f"  {g}: {s['cases']} 件, {s['bytes']:,} B (gzip {s['gzip_bytes']:,} B)" + ("" if g in CASES else " (そのまま)"))
    print("合計", sum(s["cases"] for s in groups.values()), "件")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-jukyo", type=int, default=215)
    ap.add_argument("--n-parcel", type=int, default=165)
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--out", default=OUT, help="書く場所 (既定 worker/test/golden。大きな件数で別に確かめるときは別の場所に)")
    ap.add_argument("--only", default="", help="作る組 (カンマ区切り。既定は全部。ほかの組のファイルと meta.json の記録はそのまま残す)")
    ap.add_argument("--loc-scale", type=float, default=1.0, help="location の地点の数の倍率 (1 で約3,200地点・約5,200件)")
    ap.add_argument("--prefetch-gps", action="store_true", help="location の地点の gps2city の返事を先に取るだけ (robots.txt を守り 1秒に1回)")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    if only - set(GROUPS): sys.exit(f"知らない組: {', '.join(sorted(only - set(GROUPS)))} (組は {', '.join(GROUPS)})")
    want = lambda g: not only or g in only
    if not mcp_server._ready.wait(180) or mcp_server._state.get("err"): sys.exit("mcp_server のデータを読み込めない: " + str(mcp_server._state.get("err")))
    if a.prefetch_gps:
        prefetch_gps(a.seed, a.loc_scale); return
    # 組ごとに乱数と JSON-RPC の id を分ける (--only で1つの組だけ作っても、全部作ったときと同じものになる)
    rnd = lambda g: random.Random(f"{a.seed}/{g}")
    py = sys.version.split()[0]
    made = {g: {"seed": a.seed, "python": py} for g in GROUPS}
    made["kanazawa_address"].update(n_jukyo=a.n_jukyo, n_parcel=a.n_parcel)
    made["location"].update(loc_scale=a.loc_scale)
    def start(g):
        _ids[0] = 0
        return want(g)
    if start("kanazawa_address"):
        r = random.Random(a.seed)
        kanazawa_cases(r, a.n_jukyo, a.n_parcel); print("kanazawa_address", len(CASES["kanazawa_address"]))
        review_cases(r); print("+ review/bench", len(CASES["kanazawa_address"]))
        digit_cases(rnd("digits")); print("+ odd digits", len(CASES["kanazawa_address"]))
    if start("onagawa_address"): onagawa_cases(rnd("onagawa_address")); print("onagawa_address", len(CASES["onagawa_address"]))
    if start("sorting"): sorting_cases(rnd("sorting")); print("sorting", len(CASES["sorting"]))
    if start("location"): location_cases(a.seed, a.loc_scale); print("location", len(CASES["location"]))
    if start("invalid"): invalid_cases(); print("invalid", len(CASES["invalid"]))
    if start("protocol"): protocol_cases(); print("protocol", len(CASES["protocol"]))
    if start("freshness"): freshness_cases(rnd("freshness")); print("freshness", len(CASES["freshness"]))
    _gps_save()
    write_out(a.out, only, made)

if __name__ == "__main__":
    main()
