#!/usr/bin/env python3
"""宮城県女川町: 行政区・地名 + 日付 → その日に出せるごみ、品目 + 日付 → 分け方 (2026年度)

  python onagawa.py 女川町針浜 2026-12-31
  python onagawa.py --sorting ペットボトルのキャップ 2026-10-20

データ (out/onagawa_data.json) は build_onagawa.py が、Opus のエージェント2人が別々に読んだ公式資料
(令和8年度 おながわ町民カレンダー、町のごみのページと 2026-10-01 からの分別表) から作る。答えは resolve.py (金沢市) と同じ形の JSON。
確定 (ok) するのは、行政区の名前がそのまま書かれているときだけ。大字などの地名から行政区を当てた場合は候補として返し、行政区を聞き返す。
"""
import copy, datetime as dt, json, os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve import JST, WDJ, fmt, _date, load_source_status, freshness, apply_freshness, _sentences, consensus

LG, MUNI = "045811", "宮城県女川町"
PREFIX = r"^(日本国?)?(〒?\d{3}-?\d{4})?(宮城県)?(牡鹿郡)?(女川町|おながわ町)"
FRESH_NOTE = {"stale": "公式資料の変更の確認が古いので、念のため町の公式資料でも確かめてください。",
              "partly_unchecked": "元にした公式資料の一部を確認できなかったので、念のため町の公式資料でも確かめてください。",
              "unchecked": "元にした公式資料が変わっていないかを確かめられていないので、念のため町の公式資料でも確かめてください。"}
NO_DEST = ("印字なし", "記載なし", "対象外", "入れない")       # 出し先が書かれていないルール

def _n(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).replace("ヶ", "ケ").replace("ヵ", "ケ")

def is_onagawa(text):
    """住所の文字列が女川町のものか (宮城県・牡鹿郡は省略可。「女川」を含むだけの別の住所は当てない)"""
    return isinstance(text, str) and bool(re.match(PREFIX, _n(text)))

MATERIAL = re.compile(r"プラスチック|金属|陶器|陶磁器|ガラス|アルミ|スチール|ステンレス|ゴム")   # 1文字の材質 (木・紙・布) は「植木鉢」「新聞紙」に誤って当たるので使わない

SYNONYM = (("トレー", "トレイ"), ("てんぷら", "天ぷら"), ("蛍光灯", "蛍光管"), ("発砲", "発泡"), ("ふとん", "布団"), ("ペットボトルの蓋", "ペットボトルのふた"))
GENERIC_SUFFIX = ("紙", "類", "など", "全般")                    # 「新聞」→「新聞紙」、「雑誌」→「雑誌類」は同じ品目とみなす
CONTAINER = re.compile(r"パック|ボトル|容器|びん")                # 例 (牛乳・ジュースなど) が「中身」を書いている品目

def _qnorm(s):
    """品目の表記ゆれをそろえる: 蓋/フタ→ふた、付き/ついた→つき、白い→白色、トレー→トレイ、「の」「や」「・」「製」「等」「など」は外す"""
    s = _n(s)
    for x, y in SYNONYM: s = s.replace(x, y)
    s = s.replace("蓋", "ふた").replace("フタ", "ふた").replace("付き", "つき").replace("ついた", "つき").replace("白い", "白色")
    return re.sub(r"[のや・製等]|など", "", s)

def item_names(item):
    """分別表の品目名 → 照合に使う名前 [(名前, 種類)]。種類: full (括弧も含めた品目名そのもの) / main (品目名・その「・」区切りの要素) /
    example (括弧の中の例)。「色・模様つきトレイ」からは「色つきトレイ」「模様つきトレイ」も作る"""
    core = re.sub(r"[（(].*?[）)]", "", item)
    inner = re.findall(r"[（(](.*?)[）)]", item)
    names = [(item, "full"), (core, "main")] + [(x, "main") for x in core.split("・") if len(core.split("・")) > 1] \
        + [(x, "example") for g in inner for x in re.split(r"[・、：:]", g)]
    m = re.fullmatch(r"(\w{1,3})・([\u4e00-\u9fff]+)(.+)", core)
    if m: names += [(m[1] + m[3], "main"), (m[2] + m[3], "main")]
    return [(_qnorm(n), kind) for n, kind in names if _qnorm(n)]

def item_match(query, item):
    """品目の近さ → (点, 当たり方)。0.9 以上だけを確定に使う。
    1.0 品目名そのもの (括弧つきの名前も) / 0.9 括弧の中の例と一致、品目名が問いに含まれ残りが「用」「空き」や品目の材質・容器の語だけ、
    問いの後ろに「紙」「類」が付くと品目名 (「新聞」→「新聞紙」) / 0.6 問いが品目名の一部 (「たばこ」⊂「加熱式たばこ」、「トレイ」⊂「白色トレイ」)、
    材質・容器の語が抜けている (「植木鉢」と「プラスチック製の植木鉢」、「牛乳」と「紙パック（牛乳…）」)、似ているだけ / 0。
    キャップ⇔ふた は言い換えとして両方試す (「金属のハンガー」は残り「金属」が品目の材質と違うので当てない)"""
    import difflib
    core = _qnorm(re.sub(r"[（(].*?[）)]", "", item))
    core_mat, core_box = MATERIAL.search(core), CONTAINER.search(core)
    item_mat = core_mat or MATERIAL.search("".join(re.findall(r"[（(](.*?)[）)]", item)))     # 「びんのふた（金属製）」は金属
    best = (0.0, None)
    up = lambda sc, how: max(best, (sc, how), key=lambda x: x[0])
    for q in dict.fromkeys([query, query.replace("キャップ", "ふた"), query.replace("ふた", "キャップ")]):
        q_mat = MATERIAL.search(q)
        if q_mat and item_mat and q_mat[0] != item_mat[0]: continue                       # 材質が違う品目は候補にもしない
        q_rest = q.replace(q_mat[0], "", 1) if q_mat else None
        q_bare = q.replace(q_mat[0], "", 1) if q_mat and core_mat and q_mat[0] == core_mat[0] else None   # 「プラスチックのバケツ」→「バケツ」(品目と同じ材質)
        for n, kind in item_names(item):
            if q == n:
                if kind in ("full", "main"): best = up(1.0, "name")
                elif core_mat and not q_mat: best = up(0.6, "example_without_material")
                elif core_box and not CONTAINER.search(q): best = up(0.6, "example_is_contents")
                else: best = up(0.9, "example")
                continue
            if q_bare and kind == "example" and len(q_bare) >= 2 and (q_bare == n or (n.endswith(q_bare) and n[:-len(q_bare)] in ("ポリ",))):
                # 材質を言ったうえで例に当たる (「プラスチックのバケツ」→「ポリバケツ」)。「プラスチックのボトル」を「シャンプー等のボトル」には当てない
                best = up(0.9, "example"); continue
            if (len(n) >= 2 or kind in ("main", "full")) and n in q:
                rest = re.sub(r"(用|空き|空)", "", q.replace(n, "", 1))
                if not rest or (core_mat and rest == core_mat[0]) or (kind == "example" and core_box and core.endswith(rest) and not MATERIAL.search(n)):
                    # 「牛乳パック」= 例「牛乳」+ 品目の容器の語。例が材質 (「プラスチック製」) のときは当てない (「プラスチックのふた」は「びんのふた」に限らない)
                    best = up(0.9, "name_in_query" if kind != "example" else "example")
                elif not MATERIAL.fullmatch(n) and not (MATERIAL.search(rest) and core_mat and MATERIAL.search(rest)[0] != core_mat[0]):
                    best = up(0.6, "name_in_query_partly")                               # 名前の一部だけ当たる: 候補に並べる
                continue                                                                 # 残りが品目と違う材質 (「金属のハンガー」の「金属」) なら当てない
            if q_rest and len(q_rest) >= 2 and n.endswith(q_rest):
                best = up(0.6, "same_kind")                                              # 「プラスチックのふた」→「びんのふた」「ペットボトルのキャップ（ふた）」を候補に
                continue
            if len(q) >= 2 and q in n:
                ok_suffix = n.startswith(q) and n[len(q):] in GENERIC_SUFFIX and kind != "example"
                best = up(0.9 if ok_suffix and not (core_mat and not q_mat) else 0.6, "name" if ok_suffix else "query_in_name")
                continue
            r = difflib.SequenceMatcher(None, q, n).ratio()
            suffix = next((k for k in range(min(len(q), len(n)), 0, -1) if q[-k:] == n[-k:]), 0)
            if len(n) >= 2 and ((suffix >= 2 and r >= 0.7) or r >= 0.85): best = up(0.6, "similar")
    return best

def item_score(query, item):
    return item_match(query, item)[0]

class Onagawa:
    def __init__(self, path="out/onagawa_data.json"):
        raw = open(path, "rb").read()
        self.d = json.loads(raw)
        import hashlib
        self.data_version = f"onagawa-fy{self.d['fiscal_year']}-{hashlib.sha256(raw).hexdigest()[:12]}"
        self.lo, self.hi = dt.date.fromisoformat(self.d["valid_from"]), dt.date.fromisoformat(self.d["valid_to"])
        self.gk = {}                                   # 行政区 → {burnable: group, resource: row}
        for g in self.d["burnable"]["groups"]:
            wd = sorted({dt.date.fromisoformat(x).weekday() for x in g["dates"]})
            g["weekdays_short"] = "・".join(WDJ[w] for w in wd)
            g["color_short"] = re.sub(r"の日$", "", g["color"])
            for k in g["gyoseiku"]: self.gk.setdefault(_n(k), {"name": k})["burnable"] = g
        for r in self.d["resource"]["rows"]:
            for k in r["gyoseiku"]: self.gk.setdefault(_n(k), {"name": k})["resource"] = r
        # 地名 (大字など) → 行政区の候補。「鷲神一丁目・鷲神二丁目」は分け、「（…）」の注は外す
        self.places = {}
        for p in self.d.get("place_to_gyoseiku", []):
            for key in re.split(r"[・、]", re.sub(r"[（(].*?[）)]", "", p["place"])):
                key = _n(key)
                if not key: continue
                cur = self.places.get(key)
                gy = list(dict.fromkeys((cur["gyoseiku"] if cur else []) + p["gyoseiku"]))
                self.places[key] = {"gyoseiku": gy, "certainty": "unknown" if not gy else "likely", "basis": p["basis"] if not cur else cur["basis"] + " / " + p["basis"]}
        self.unknown_new = {k for k, v in self.places.items() if not v["gyoseiku"]}         # 震災後の新しい町名 (行政区が公式資料に無い)
        self.no_dates = {_n(g["gyoseiku"][0]): g for g in self.d["burnable"]["groups"] if not g["dates"]}   # 江島: 日付の印字が無い
        # 列の見出しは4月印刷のままで、年度の途中から中身が変わった列 (「…白色トレイ…」の列: 10/1 から色つきトレイなども赤かごへ)。
        # 読み取りで書いた列の説明「YYYY-MM-DDから：…」をそのまま、その日以降の答えに添える
        self.label_notes = {}                          # 見出し → [(この日から, この日まで, 注)]
        for c in self.d["resource"]["columns"]:
            t = c.get("contents", "")
            m = re.search(r"(\d{4}-\d{2}-\d{2})から：(.+?)。(?=全|$)", t)
            if not m: continue
            notes = [(m[1], None, f"見出しは4月に印刷されたカレンダーのまま。{m[1]} からのこの日の出し方: {m[2]}。品目ごとの出し先は garbage_sorting で引ける")]
            m0 = re.search(r"(\d{4}-\d{2}-\d{2})まで：(.+?)。(?=\d{4}-\d{2}-\d{2}から)", t)
            if m0: notes.append((None, m0[1], f"{m0[1]} までのこの日の出し方: {m0[2]}。{m[1]} から分け方が変わる。品目ごとの出し先は garbage_sorting で引ける"))
            self.label_notes[c["label"]] = notes

    # ---------------- 場所 ----------------
    def match_place(self, text):
        """住所・地名 → (行政区のリスト, 当て方, 確かさ sure/likely/unknown, 根拠)
        sure: 行政区の名前がそのまま (後ろに「区」「行政区」「地区」だけ)。likely: 大字などの地名から行政区の候補。
        地名の後ろが「町」など別の町名の続きなら当てない。字が震災後の新しい町名 (内山・伊勢など) なら unknown"""
        s = re.sub(PREFIX, "", _n(text))
        m = re.fullmatch(r"(.+?)(行政区|地区|区)?", s)
        for k, v in self.gk.items():
            if s == k or (m and m[1] == k and m[2]):
                return [v["name"]], f"行政区「{v['name']}」", "sure", "カレンダーの凡例"
        hits = []
        for k in self.places:
            if not s.startswith(k): continue
            rest = s[len(k):]
            if rest and not re.match(r"(字|大字|\d|丁目|-)", rest) and not (k in self.unknown_new): continue
            hits.append(k)
        for k in self.gk:                                  # 行政区の名前の後ろに字・番地が続く (針浜字唐松43)
            if s.startswith(k) and re.match(r"(字|大字|\d|-)", s[len(k):] or "x"): hits.append(k)
        if not hits: return [], None, None, None
        k = max(hits, key=len)
        rest = re.sub(r"^(大字|字)", "", s[len(k):])
        if any(rest.startswith(u) for u in self.unknown_new):                  # 鷲神浜字内山: 字が新しい町名
            u = next(u for u in self.unknown_new if rest.startswith(u))
            return [], f"地名「{k}字{u}」", "unknown", self.places[u]["basis"]
        if k in self.gk and k not in self.places:
            return [self.gk[k]["name"]], f"行政区「{self.gk[k]['name']}」(後ろに字・番地)", "likely", "行政区の名前の後ろに字・番地がある"
        v = self.places[k]
        return v["gyoseiku"], f"地名「{k}」→ 行政区の候補 {'・'.join(v['gyoseiku']) or '(不明)'}", v["certainty"], v["basis"]

    def _label_note(self, label, day):
        return next((n for lo, hi, n in self.label_notes.get(label, []) if (lo is None or lo <= day) and (hi is None or day <= hi)), None)

    def _day(self, gyoseiku, d):
        g = self.gk[_n(gyoseiku)]
        day = {"date": str(d), "weekday": WDJ[d.weekday()], "in_calendar_range": self.lo <= d <= self.hi}
        if not day["in_calendar_range"]:
            day["collections"] = None
            day["note"] = f"女川町の{self.lo.year}年度カレンダー ({self.lo}〜{self.hi}) の範囲外で分かりません (「収集なし」という意味ではありません)"
            return day
        cols = []
        b = g.get("burnable")
        if b and str(d) in b["dates"]:
            cols.append({"category": "burnable", "official_label": self.d["burnable"]["label"], "rule_text": f"{b['color']} ({b['weekdays_short']})"})
        r = g.get("resource")
        if r:
            for c in self.d["resource"]["columns"]:
                if str(d) in r["by_column"].get(c["label"], []):
                    x = {"category": "resource", "official_label": c["label"]}
                    note = self._label_note(c["label"], str(d))
                    if note: x["label_note"] = note
                    cols.append(x)
        day["collections"] = cols
        return day

    def _next(self, gyoseiku, d):
        g, out = self.gk[_n(gyoseiku)], []
        labels = ([self.d["burnable"]["label"]] if g.get("burnable") else []) + ([c["label"] for c in self.d["resource"]["columns"]] if g.get("resource") else [])
        if d + dt.timedelta(days=1) < self.lo:        # 前年度のカレンダーは持っていない
            return [{"official_label": l, "date": None, "null_reason": f"{self.lo} より前の期間のカレンダーは未取得なので、次の収集日は分からない"} for l in labels]
        b = g.get("burnable")
        if b:
            nx = min((x for x in b["dates"] if x > str(d)), default=None)
            out.append({"official_label": self.d["burnable"]["label"], "date": nx, **({} if nx else {"null_reason": f"{self.lo.year}年度のカレンダー ({self.hi} まで) にこれより後の日が無い"})})
        r = g.get("resource")
        if r:
            for c in self.d["resource"]["columns"]:
                nx = min((x for x in r["by_column"].get(c["label"], []) if x > str(d)), default=None)
                out.append({"official_label": c["label"], "date": nx, **({} if nx else {"null_reason": f"{self.lo.year}年度のカレンダー ({self.hi} まで) にこれより後の日が無い"})})
        for x in out:
            if x["date"]: x["weekday"] = WDJ[dt.date.fromisoformat(x["date"]).weekday()]
        for x in out:
            note = x.get("date") and self._label_note(x["official_label"], x["date"])
            if note: x["label_note"] = note
        return sorted(out, key=lambda x: x["date"] or "9999")

    def year_end(self, gyoseiku):
        """この行政区の、品目ごとの年内最後と年明け最初の収集日 (同じ日付の品目はまとめる)"""
        g, fy = self.gk[_n(gyoseiku)], self.lo.year
        def edge(dates):
            return (max((x for x in dates if x <= f"{fy}-12-31"), default=None), min((x for x in dates if x >= f"{fy + 1}-01-01"), default=None))
        out = []
        if g.get("burnable") and g["burnable"]["dates"]:
            l, f = edge(g["burnable"]["dates"])
            out.append(f"{self.d['burnable']['label']}は {fmt(dt.date.fromisoformat(l), True)} まで、{fmt(dt.date.fromisoformat(f), True)} から")
        if g.get("resource"):
            by = {}
            for c in self.d["resource"]["columns"]:
                l, f = edge(g["resource"]["by_column"].get(c["label"], []))
                if l and f: by.setdefault((l, f), []).append(c["label"])
            for (l, f), labels in sorted(by.items()):
                out.append(f"{'・'.join(labels)}は {fmt(dt.date.fromisoformat(l), True)} まで、{fmt(dt.date.fromisoformat(f), True)} から")
        return ("祝日もカレンダーの色・表のとおり収集する。品目ごとの年内最後と年明け最初の収集日 (この行政区): " + "、".join(out)) if out else self.d.get("holiday_policy")

    def _contact_text(self):
        c = self.d.get("contact") or {}
        return f"{c.get('name', '女川町役場')}{' (' + c['phone'] + ')' if c.get('phone') else ''}"

    def _base(self, q):
        return {"data_version": self.data_version, "generated_at": dt.datetime.now(JST).isoformat(timespec="seconds"), "query": q,
                "put_out_by": self.d.get("put_out_by"), "not_covered": self.d.get("not_covered", []), "contact": self.d.get("contact"),
                "holiday_policy": self.d.get("holiday_policy")}

    def resolve_address(self, address, date=None, days=1):
        return copy.deepcopy(self._resolve_address(address, date, days))

    def _resolve_address(self, address, date, days):
        now = dt.datetime.now(JST)
        q = {"address": address, "timezone": "Asia/Tokyo", "date": str(date or now.date()), "date_basis": "user" if date else "server_today_jst", "days": days}
        base = self._base(q)
        bad = lambda msg: {**base, "status": "invalid_input", "answerable": False, "summary_ja": msg}
        if not isinstance(address, str) or not address.strip() or len(address) > 200: return bad("address は住所の文字列 (200文字まで) で指定してください。")
        try: d0 = _date(date) if date is not None else now.date()
        except (TypeError, ValueError): return bad("date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。")
        if not (isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 14): return bad("days は 1〜14 の整数で指定してください。")
        gys, how, cert, basis = self.match_place(address)
        loc = {"municipality": {"lg_code": LG, "name": MUNI, "checked_by": "address_text"}, "place_name": address, "method": None,
               "collection_district": None, "district_confidence": 0.0, "decision_blocked_by": None, "place_match": how}
        res = {**base, "location": loc, "sources": self.d["sources"], "calendar": {"valid_from": str(self.lo), "valid_to": str(self.hi)},
               "town_rows": sorted({v["name"] for v in self.gk.values()})}
        if gys and len(gys) == 1 and _n(gys[0]) in self.no_dates:
            g = self.no_dates[_n(gys[0])]
            notes = [p["text"] for p in self.d.get("put_out_texts", []) if "江島" in p["text"]]
            quote = max([g["weekdays_text"]] + notes, key=len)
            res.update(status="unresolved", answerable=False, candidates=[])
            loc.update(method="schedule_not_published", decision_blocked_by={"reason": "schedule_not_published", "town": gys[0], "official_text": quote})
            res["summary_ja"] = (f"{gys[0]}地区は、カレンダーに収集日の日付が載っていません。カレンダーには「{quote}」とあります。"
                                 f"日付ごとの答えは出せないので、区長さんか{self._contact_text()}に確かめてください。")
            return self._finish(res)
        if not gys:
            reason = "place_not_mapped" if how else "place_not_found"
            res.update(status="unresolved", answerable=False, candidates=[])
            loc.update(method=reason, decision_blocked_by={"reason": reason, "town": None, "label": address, **({"basis": basis} if basis else {})})
            res["summary_ja"] = ((f"{how} は、どの行政区に入るか公式資料に書かれていません。" if how else f"住所 ({address}) から女川町の行政区を決められませんでした。")
                                 + f"お住まいの行政区 ({'・'.join(res['town_rows'][:8])} など) を教えてください。{self._contact_text()}にも確認できます。")
            return self._finish(res)
        cands = [{"collection_district": g, "official": cert == "sure", "basis": [{"kind": "gyoseiku" if cert == "sure" else "place_to_gyoseiku",
                                                                                    "label": how, "detail": basis or ""}],
                  "how_to_confirm": [{"行政区": g, "燃やせるごみ": self.gk[_n(g)].get("burnable", {}).get("color"),
                                      "資源ごみの欄": self.gk[_n(g)].get("resource", {}).get("label")}],
                  "days": [self._day(g, d0 + dt.timedelta(days=i)) for i in range(days)]} for g in gys]
        if cert == "sure" and len(gys) == 1:
            g = gys[0]
            res.update(status="ok", answerable=all(x["in_calendar_range"] for x in cands[0]["days"]))
            loc.update(collection_district=g, method="gyoseiku", district_confidence=0.98)
            res["days"], res["next_after_date"] = cands[0]["days"], self._next(g, d0)
            res["holiday_policy"] = self.year_end(g)
            res["groups"] = {"burnable": self.gk[_n(g)].get("burnable", {}).get("color"), "resource_row": self.gk[_n(g)].get("resource", {}).get("label")}
        else:
            res.update(status="ambiguous", answerable=False, candidates=cands, consensus=consensus(cands))
            loc.update(method="place_to_gyoseiku_candidates", place_basis=basis)
        return self._finish(res, summarize=True)

    def _finish(self, res, summarize=False):
        """公式資料の変更監視の結果を当てて、要約を作る (変わっていたら、先に作った要約も日付を言わない文に置き換える)"""
        apply_freshness(res, freshness(load_source_status(), [s["url"] for s in self.d["sources"]]))
        b = (res.get("location") or {}).get("decision_blocked_by") or {}
        if summarize or b.get("reason") == "source_changed": res["summary_ja"] = self._summary(res)
        fr = res["freshness"]["status"]
        if b.get("reason") != "source_changed" and fr in FRESH_NOTE: res["summary_ja"] += FRESH_NOTE[fr]
        return res

    def _summary(self, res):
        loc = res["location"]
        if res["status"] == "ok":
            days = "。".join(f"{fmt(dt.date.fromisoformat(d['date']), True)}は" + ("・".join(c["official_label"] for c in d["collections"]) or "収集なし")
                            if d["in_calendar_range"] else f"{fmt(dt.date.fromisoformat(d['date']), True)}は女川町のカレンダーの範囲外で分かりません (「収集なし」という意味ではありません)"
                            for d in res["days"]) + "。"
            put = (f"出すのは{res['put_out_by']['deadline']} (女川町「{res['put_out_by']['source_title']}」)。"
                   if res.get("put_out_by") and any(d.get("collections") for d in res["days"]) else "")
            lnote = next((c["label_note"] for d in res["days"] for c in (d.get("collections") or []) if c.get("label_note")), None)
            lnote = f"注: 「{next(c['official_label'] for d in res['days'] for c in (d.get('collections') or []) if c.get('label_note'))}」の{lnote}。" if lnote else ""
            return _sentences(f"女川町の行政区 {loc['collection_district']} は、燃やせるごみが「{res['groups']['burnable']}」、資源ごみが「{res['groups']['resource_row']}」の欄です。",
                              days, put, lnote, f"出典: 女川町「{self.d['calendar_title'].split('（')[0]}」。")
        b = loc.get("decision_blocked_by") or {}
        if res["status"] == "unresolved" and b.get("reason") == "source_changed":
            return (f"このデータの元にした公式資料が {b['checked_at'][:10]} の確認で変わっていました。作り直して確かめるまでは確かな答えとして返しません。"
                    f"女川町の公式ページか{self._contact_text()}で確かめてください。")
        same = len({(c["how_to_confirm"][0]["燃やせるごみ"], c["how_to_confirm"][0]["資源ごみの欄"]) for c in res["candidates"]}) == 1
        head = (f"{loc.get('place_match')} は町の資料から推定したもので、確かな対応表はありません。" if same else
                f"{loc.get('place_match')} は行政区によって収集日が違うため決められません。")
        per = "".join(f"行政区 {c['collection_district']} なら、" + "。".join(
            f"{fmt(dt.date.fromisoformat(d['date']), True)}は" + (("・".join(x["official_label"] for x in d["collections"]) or "収集なし") if d["in_calendar_range"] else "分かりません")
            for d in c["days"]) + "。" for c in res["candidates"][:3])
        return _sentences(head, per, f"お住まいの行政区 (町民カレンダーの凡例の名前) を確かめてください。{self._contact_text()}にも確認できます。")

    # ---------------- 分け方 ----------------
    def sorting(self, item, date=None):
        return copy.deepcopy(self._sorting(item, date))

    def _sorting(self, item, date):
        now = dt.datetime.now(JST)
        q = {"municipality": MUNI, "item": item, "date": str(date or now.date()), "date_basis": "user" if date else "server_today_jst"}
        base = {"data_version": self.data_version, "generated_at": now.isoformat(timespec="seconds"), "query": q, "contact": self.d.get("contact")}
        if not isinstance(item, str) or not item.strip() or len(item) > 200:
            return {**base, "status": "invalid_input", "answerable": False, "summary_ja": "item は品目の文字列 (200文字まで) で指定してください。"}
        try: d0 = _date(date) if date is not None else now.date()
        except (TypeError, ValueError):
            return {**base, "status": "invalid_input", "answerable": False, "summary_ja": "date は日本時間の日付 (YYYY-MM-DD、2000〜2099年) で指定してください。"}
        fr = freshness(load_source_status(), [x["url"] for x in self.d["sources"]])
        base["freshness"] = fr
        if not (self.lo <= d0 <= self.hi):
            return {**base, "status": "out_of_range", "answerable": False, "matches": [],
                    "summary_ja": f"女川町の分別のデータは {self.lo}〜{self.hi} の分です。{fmt(d0, True)}の分け方は分かりません。{self._contact_text()}に確かめてください。"}
        if fr["status"] == "source_changed":
            return {**base, "status": "unresolved", "answerable": False, "matches": [],
                    "decision_blocked_by": {"reason": "source_changed", "changed": fr["changed"], "checked_at": fr["checked_at"]},
                    "summary_ja": f"このデータの元にした公式資料が変わっていました。作り直して確かめるまでは確かな答えとして返しません。{self._contact_text()}に確かめてください。"}
        res = self._sorting_answer(item, d0, base)
        if fr["status"] in FRESH_NOTE:
            res["answerable"] = False
            res["summary_ja"] += FRESH_NOTE[fr["status"]]
        return res

    def _sorting_answer(self, item, d0, base):
        s = _qnorm(item)
        rules = self.d.get("sorting_rules", [])
        inside = lambda r, d: (not r.get("valid_from") or r["valid_from"] <= str(d)) and (not r.get("valid_to") or str(d) <= r["valid_to"])
        scored = [(*item_match(s, r["item"]), r) for r in rules]
        now_rules = [(sc, how, r) for sc, how, r in scored if inside(r, d0)]
        top = max((sc for sc, _, _ in now_rules), default=0)
        strong = [(how, r) for sc, how, r in now_rules if sc >= 0.9 and sc >= min(top, 0.95)]      # 完全一致があればそれだけを使う
        pick = lambda rs: [{k: r.get(k) for k in ("item", "destination", "how", "valid_from", "valid_to", "source")} for r in rs]
        other = [r for sc, _, r in scored if sc >= 0.9 and not inside(r, d0)]
        if not strong:
            weak = [r for sc, _, r in sorted(now_rules, key=lambda x: -x[0]) if sc > 0][:6]
            if weak:
                return {**base, "status": "ambiguous", "answerable": False, "matches": pick(weak),
                        "summary_ja": f"「{item}」とそのまま同じ品目は分別表に無く、近い品目があります: " + "、".join(f"「{r['item']}」→「{r['destination']}」" for r in weak)
                                      + "。どれに当たるか (材質・大きさ・中身など) を確かめてください。"}
            return {**base, "status": "unresolved", "answerable": False, "matches": [], "other_periods": pick(other),
                    "decision_blocked_by": {"reason": "item_not_found"},
                    "summary_ja": (f"「{item}」は {fmt(d0, True)}時点の女川町の分別表のデータに見つかりません"
                                   + (f" ({'、'.join(sorted({(r['valid_from'] or '') + '〜' + (r['valid_to'] or '') for r in other}))} の分別表にはあります)" if other else "")
                                   + f"。女川町の分別表 ({self.d['sorting_source_url']}) か{self._contact_text()}で確かめてください。")}
        rs = [r for _, r in strong]
        if len({r["destination"] for r in rs}) > 1:          # 当てはまる品目で出し方が違う (ふた・植木鉢など): 決めずに並べる
            return {**base, "status": "ambiguous", "answerable": False, "matches": pick(rs[:6]),
                    "summary_ja": f"「{item}」に当てはまる品目が分別表に複数あり、出し方が違います: "
                                  + "、".join(f"「{r['item']}」→「{r['destination']}」" for r in rs[:6]) + "。どれに当たるか (材質・大きさなど) を確かめてください。"}
        how0, b = strong[0]
        # 同じ語を名前に含む別の品目 (例外): 「紙パック」→「内部が銀色の紙パック」は燃やせるごみ
        related = [r for sc, h, r in now_rules if h == "query_in_name" and r not in rs and r["destination"] != b["destination"]][:4]
        # 広い区分の例として当たったとき (「布団」⊂ 粗大ごみ): ほかの品目の出し方の注記に同じ語があれば添える
        mentions = []
        if how0 == "example" and len(item.strip()) >= 2:
            for r in rules:
                if r is b or not inside(r, d0): continue
                for sent in re.split(r"(?<=。)", r.get("how") or ""):
                    if item.strip() in sent and len(mentions) < 2: mentions.append({"item": r["item"], "destination": r["destination"], "text": sent.strip()})
        res = {**base, "matches": pick(rs), "other_periods": pick(other), **({"related": pick(related)} if related else {}),
               **({"related_notes": mentions} if mentions else {})}
        if any(x in b["destination"] for x in NO_DEST):          # 公式の資料に出し先が書かれていない
            res.update(status="unresolved", answerable=False, decision_blocked_by={"reason": "not_in_official_table"},
                       summary_ja=f"「{b['item']}」の {fmt(d0, True)}時点の出し先は、女川町の公式資料に書かれていません ({b['destination']})。{self._contact_text()}に確かめてください。")
            return res
        start = self.d["valid_from"]
        if b.get("valid_from") and b["valid_from"] > start:
            period = f" ({b['valid_from']} から)" if other else f" ({b['valid_from']} からの一覧表に載っている品目)"
        else: period = ""
        period += f" ({b['valid_to']} まで)" if b.get("valid_to") else ""
        ch = "".join(f"なお {c['valid_from'] or ''}〜{c['valid_to'] or ''} は「{c['item']}」→「{c['destination']}」でした。" if c.get("valid_to") and c["valid_to"] < str(d0)
                     else f"なお {c['valid_from']} からは「{c['item']}」→「{c['destination']}」になります。" for c in other)
        rel = "".join(f"ただし「{r['item']}」は「{r['destination']}」。" for r in related)
        men = "".join(f"「{m['item']}」の出し方には「{m['text']}」ともあります。" for m in mentions)
        head = (f"女川町では{fmt(d0, True)}時点で、「{b['item']}」は町のごみ収集に出せません ({b['destination']}){period}。"
                if b["destination"].startswith("収集しない") else
                f"女川町では{fmt(d0, True)}時点で、「{b['item']}」は「{b['destination']}」に出します{period}。")
        res.update(status="ok", answerable=True,
                   summary_ja=_sentences(head, (b["how"].rstrip("。") + "。") if b.get("how") else "", rel, men, ch, f"出典: {b.get('source', '').rstrip('。')}。"))
        return res

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    o = Onagawa()
    if a and a[0] == "--sorting":
        print(json.dumps(o.sorting(a[1], a[2] if len(a) > 2 else None), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(o.resolve_address(a[0], a[1] if len(a) > 1 else None, int(a[2]) if len(a) > 2 else 1), ensure_ascii=False, indent=1))
