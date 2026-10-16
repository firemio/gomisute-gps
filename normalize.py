"""世田谷CSV / 長野zip → schema v0.1 (zone + schedule) JSON
python normalize.py  → out/setagaya.json, out/nagano.json, 検証結果を表示
"""
import csv, json, re, sys, datetime as dt, pathlib, hashlib, collections
from dateutil.rrule import rrulestr

sys.stdout.reconfigure(encoding="utf-8")
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
WD = {"月": "MO", "火": "TU", "水": "WE", "木": "TH", "金": "FR", "土": "SA", "日": "SU"}
WD_IDX = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
Z2H = str.maketrans("０１２３４５６７８９", "0123456789")

def sha(p): return "sha256:" + hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:16]

# ---------------- 世田谷 ----------------
def expand_chome(s):
    """'1・3～5' → [1,3,4,5]"""
    s = s.translate(Z2H); out = []
    for part in s.split("・"):
        if "～" in part:
            a, b = part.split("～"); out += range(int(a), int(b) + 1)
        elif part.strip():
            out.append(int(part))
    return out

def to_rrule(text):
    """'水曜日・土曜日' / '2回目・4回目の月曜日' → RRULE"""
    m = re.fullmatch(r"((?:\d回目・?)+)の(.)曜日", text)
    if m:
        ns = re.findall(r"(\d)回目", m.group(1))
        return "FREQ=MONTHLY;BYDAY=" + ",".join(f"{n}{WD[m.group(2)]}" for n in ns)
    days = re.findall(r"(.)曜日", text)
    if days and len(days) == text.count("曜日"):
        return "FREQ=WEEKLY;BYDAY=" + ",".join(WD[d] for d in days)
    raise ValueError(text)

def setagaya():
    src = "data/setagaya.csv"
    rows = list(csv.reader(open(src, encoding="cp932")))[2:]
    cats = [("resource", "資源"), ("burnable", "可燃ごみ"), ("nonburnable", "不燃ごみ"), ("pet", "ペットボトル")]
    zones, scheds, errs = [], [], []
    source = {"url": "https://www.city.setagaya.lg.jp/documents/416/27-08-26.csv",
              "fetched_at": str(dt.date.today()), "hash": sha(src)}
    for i, r in enumerate(rows):
        if len(r) < 8 or not r[1]: continue
        zid = f"13112-{i:04d}"
        zones.append({"zone_id": zid, "muni_code": "13112", "muni_name": "世田谷区",
                      "members": [{"town": r[1], "chome": expand_chome(r[2])}],
                      "exceptions": [], "geom": None, "confidence": 0.95,
                      "meta": {"office": r[7]}, "source": source})
        for (cat, label), cell in zip(cats, r[3:7]):
            try:
                scheds.append({"zone_id": zid, "category": cat, "category_label": label,
                               "rule_kind": "rrule", "rrule": to_rrule(cell), "exdates": [], "rdates": [],
                               "time_window": "~08:00", "source": source})
            except ValueError:
                errs.append((r[1], r[2], cell))
    json.dump({"zones": zones, "schedules": scheds}, open(OUT / "setagaya.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    chome_total = sum(len(z["members"][0]["chome"]) for z in zones)
    print(f"[世田谷] zones={len(zones)} 町丁目={chome_total} schedules={len(scheds)} parse_errors={len(errs)}")
    for e in errs[:10]: print("   ERR", e)
    return zones, scheds

# ---------------- 長野 ----------------
CAT_MAP = {"可燃ごみ": "burnable", "不燃ごみ": "nonburnable", "資源プラスチック": "plastic", "紙": "paper",
           "缶、スプレー缶、カセットボンベ缶": "can", "ペットボトル": "pet", "ビン": "bottle",
           "乾電池": "battery", "剪定枝葉等": "yard"}

def fit_rule(dates, start, end):
    """実日付集合 → 最小誤差の RRULE + EXDATE/RDATE を推定"""
    ds = set(dates)
    cands = []
    wds = collections.Counter(WD_IDX[d.weekday()] for d in ds)
    # weekly: 出現が多い曜日
    main = [w for w, c in wds.items() if c >= 8]
    if main:
        for interval in (1, 2):
            cands.append(f"FREQ=WEEKLY;INTERVAL={interval};BYDAY={','.join(sorted(main, key=WD_IDX.index))}")
    # monthly nth weekday
    nth = collections.Counter(f"{(d.day - 1) // 7 + 1}{WD_IDX[d.weekday()]}" for d in ds)
    keys = [k for k, c in nth.items() if c >= 4]
    if keys:
        cands.append("FREQ=MONTHLY;BYDAY=" + ",".join(sorted(keys)))
    best = None
    for rr in cands:
        base = {x.date() for x in rrulestr(rr, dtstart=dt.datetime.combine(start, dt.time()))
                .between(dt.datetime.combine(start, dt.time()), dt.datetime.combine(end, dt.time()), inc=True)}
        # 隔週は位相も試す
        variants = [base]
        if "INTERVAL=2" in rr:
            s2 = start + dt.timedelta(days=7)
            variants.append({x.date() for x in rrulestr(rr, dtstart=dt.datetime.combine(s2, dt.time()))
                             .between(dt.datetime.combine(start, dt.time()), dt.datetime.combine(end, dt.time()), inc=True)})
        for k, v in enumerate(variants):
            ex, rd = sorted(v - ds), sorted(ds - v)
            score = len(ex) + len(rd)
            if best is None or score < best[0]:
                best = (score, rr + (";PHASE=+7d" if k else ""), ex, rd)
    if best is None or best[0] > len(ds) * 0.3:
        seg = fit_segments(sorted(ds))
        if seg: return seg
        return {"rule_kind": "explicit", "dates": [str(d) for d in sorted(ds)]}
    return {"rule_kind": "rrule", "rrule": best[1], "exdates": [str(d) for d in best[2]],
            "rdates": [str(d) for d in best[3]]}

def fit_segments(ds):
    """N週ローテーション型: 支配的間隔gで等間隔、位相ずれ(gの倍数でない間隔)で区間分割"""
    gaps = collections.Counter((b - a).days for a, b in zip(ds, ds[1:]))
    g = gaps.most_common(1)[0][0]
    if g % 7: return None
    segs, cur = [], [ds[0]]
    for a, b in zip(ds, ds[1:]):
        if (b - a).days % g: segs.append(cur); cur = [b]
        else: cur.append(b)
    segs.append(cur)
    if len(segs) > 4: return None
    rules = []
    for s in segs:
        full = [s[0] + dt.timedelta(days=g * i) for i in range((s[-1] - s[0]).days // g + 1)]
        ex = sorted(set(full) - set(s))
        rules.append({"rrule": f"FREQ=WEEKLY;INTERVAL={g // 7};BYDAY={WD_IDX[s[0].weekday()]}",
                      "dtstart": str(s[0]), "until": str(s[-1]), "exdates": [str(x) for x in ex]})
    return {"rule_kind": "segments", "segments": rules}

def nagano():
    base = pathlib.Path("data/nagano_2026")
    chiku = list(csv.reader(open(base / "gomi_calendar_chiku.csv", encoding="utf-8")))[1:]
    source = {"url": "https://www.city.nagano.nagano.jp/documents/16973/gomi_calendar_2026.zip",
              "fetched_at": str(dt.date.today()), "hash": sha("data/nagano_2026.zip")}
    zones, scheds, partial = [], [], []
    stats = collections.Counter()
    for r in chiku:
        if len(r) < 3 or not r[1].strip(): continue
        no = int(r[1]); areas = [a for a in r[2:] if a]
        exc = []
        for a in areas:
            if re.search(r"一部|を除く", a):
                exc.append({"type": "unresolved_partial", "note": a}); partial.append((no, a))
        zid = f"20201-cal{no:02d}"
        zones.append({"zone_id": zid, "muni_code": "20201", "muni_name": "長野市",
                      "members": [{"chiku": a} for a in areas], "exceptions": exc, "geom": None,
                      "confidence": 0.6 if exc else 0.85, "source": source})
        cal = list(csv.reader(open(base / f"gomi_calendar_2026_{no}.csv", encoding="utf-8")))
        dates = [dt.date(*map(int, h.split("/"))) for h in cal[0][3:]]
        for row in cal[1:]:
            if len(row) < 4 or not row[1]: continue
            hit = [d for d, v in zip(dates, row[3:]) if v.strip() == "○"]
            if not hit: continue
            rule = fit_rule(hit, dates[0], dates[-1])
            stats[rule["rule_kind"]] += 1
            if rule["rule_kind"] == "segments":
                gen = set()
                for sg in rule["segments"]:
                    st = dt.datetime.fromisoformat(sg["dtstart"])
                    gen |= {x.date() for x in rrulestr(sg["rrule"] + ";UNTIL=" + sg["until"].replace("-", "") + "T235959", dtstart=st)}
                    gen -= set(map(dt.date.fromisoformat, sg["exdates"]))
                stats["seg_ok" if gen == set(hit) else "seg_ng"] += 1
                stats["seg_n_" + str(len(rule["segments"]))] += 1
            if rule["rule_kind"] == "rrule":
                stats["ex_total"] += len(rule["exdates"]); stats["rd_total"] += len(rule["rdates"])
                # 往復検証: rrule+ex+rd が元の日付集合と一致するか
                rr = rule["rrule"].replace(";PHASE=+7d", "")
                st = dates[0] + dt.timedelta(days=7 if "PHASE" in rule["rrule"] else 0)
                gen = {x.date() for x in rrulestr(rr, dtstart=dt.datetime.combine(st, dt.time()))
                       .between(dt.datetime.combine(dates[0], dt.time()), dt.datetime.combine(dates[-1], dt.time()), inc=True)}
                gen = (gen - set(map(dt.date.fromisoformat, rule["exdates"]))) | set(map(dt.date.fromisoformat, rule["rdates"]))
                stats["roundtrip_ok" if gen == set(hit) else "roundtrip_ng"] += 1
            scheds.append({"zone_id": zid, "category": CAT_MAP.get(row[1], row[1]), "category_label": row[1],
                           **rule, "valid_from": str(dates[0]), "valid_to": str(dates[-1]), "source": source})
    json.dump({"zones": zones, "schedules": scheds}, open(OUT / "nagano.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[長野] zones={len(zones)} 地区={sum(len(z['members']) for z in zones)} schedules={len(scheds)} {dict(stats)}")
    print(f"   一部/除く を含む地区: {len(partial)}")
    for p in partial: print("   ", p)
    return zones, scheds

if __name__ == "__main__":
    setagaya(); nagano()
