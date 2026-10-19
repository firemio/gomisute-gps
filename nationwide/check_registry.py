"""できた台帳 (out/registry/) を確かめる

  python nationwide/check_registry.py

件数、公式サイトの無い自治体、答えない所の判定 (gps2city の実際の答えを使った例)、
指定離島と N03 のかたまりの対応 (面積が合うか)、J-LIS の使い方、観光協会らしい URL、北方領土の ABR の印を確かめる。
問題があれば終了コード 1。
"""
import sys, re, json, csv, math, pathlib, collections, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "registry"
NT = {"016951", "016969", "016977", "016985", "016993", "017001"}
GOV_HOST = re.compile(r"\.(lg|go)\.jp$|(^|\.)(city|town|vill|village|pref)\.")    # build_registry.py と同じ
NAME_TOL = {True: math.log(1.25), False: math.log(1.6)}                          # 同じ (True = 2 km² 以上)

# gps2city (N03-2026) に 2026-10-18 に実際に問い合わせた答え: (場所, 緯度, 経度, 返った lg_code, 答えるべきか)
GPS2CITY_SAMPLES = [
    ("江島 (女川町)", 38.39919, 141.5939, "045811", True),
    ("青ヶ島", 32.45823, 139.76753, "134023", True),
    ("南鳥島", 24.28919, 153.98055, "134210", False),
    ("沖ノ鳥島", 20.42301, 136.07388, "134210", False),
    ("択捉島", 44.79886, 147.3545, "016985", False),
    ("竹島", 37.24213, 131.86471, "325287", False),
    ("魚釣島", 25.74321, 123.47656, "472077", False),
    ("鳥島 (所属未定地)", 30.48443, 140.30024, "130001", False),
    ("名古屋港 (所属未定地)", 35.00669, 136.81184, "230006", False),
    ("中城湾 (所属未定地)", 26.43409, 127.84315, "470007", False),
    ("志発島 (歯舞群島)", 43.50359, 146.13239, "012238", False),
    # 同じ自治体の住民のいる所は答える (位置はおおよそ、lg_code はその自治体のもの。gps2city には聞いていない)
    ("父島", 27.0945, 142.1918, "134210", True),
    ("根室市役所", 43.3301, 145.5827, "012238", True),
    ("石垣市役所", 24.3406, 124.1556, "472077", True),
    ("隠岐の島町役場", 36.2055, 133.3172, "325287", True),
]


def should_answer(areas, lg_code, lat, lon):
    """no_service_areas.json の使い方 (meta.how_to_use) のとおりに判定する"""
    for a in areas:
        if a["type"] in ("northern_territory", "unassigned_land"):
            if lg_code == a["gps2city_lg_code"]:
                return False, a["id"]
        elif lg_code == a["lg_code"]:
            x0, y0, x1, y1 = a["bbox"]
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                return False, a["id"]
    return True, None


def check_islands(rows):
    """指定離島の対応: 面積が合うこと (つながった島は和で)、同じかたまりに 2 つ以上当たるのは「まとめて」のときだけ"""
    bad = 0
    des = list(csv.DictReader(open(OUT / "designated_islands_n03.csv", encoding="utf-8")))
    hit = [d for d in des if d["n03_comp_id"]]
    print("指定離島と N03 のかたまり", len(des), "行、当たった", len(hit), dict(collections.Counter(d["n03_match"] or "なし" for d in des)))
    by_comp = collections.defaultdict(list)
    for d in hit:
        by_comp[(d["lg_code"], d["n03_comp_id"])].append(d)
    for (lg, c), ds in sorted(by_comp.items()):
        group = len(ds) > 1
        if group and not all("まとめて" in d["n03_match"] for d in ds):
            print(f"  NG {lg} かたまり {c} に 2 つ以上: {[d['island'] for d in ds]}")
            bad += 1
        if not all(d["area_km2"] for d in ds):
            continue
        a = sum(float(d["area_km2"]) for d in ds)
        e = min(abs(math.log(float(ds[0][k]) / a)) for k in ("n03_area_km2", "n03_muni_area_km2") if float(ds[0][k]) > 0)
        if e > NAME_TOL[a >= 2]:
            print(f"  NG {lg} {[d['island'] for d in ds]} 面積 {a:g} とかたまり {c} ({ds[0]['n03_area_km2']}) が合わない")
            bad += 1
    js = {(r["lg_code"], i["island"]): (i["n03_comp_id"], i["n03_match"]) for r in rows for i in r["inhabited_islands"]}
    cs = {(d["lg_code"], d["island"]): (int(d["n03_comp_id"]) if d["n03_comp_id"] else None, d["n03_match"] or None)
          for d in des if d["inhabited"] in ("yes", "staff_only")}
    ok = js == cs
    print("  JSON の inhabited_islands と designated_islands_n03.csv", "OK" if ok else "NG")
    return bad + (not ok)


def check_sites(rows):
    """J-LIS の URL は使ったときだけ (人が見る印つき)。自治体らしいホストがあれば、それ以外のホストは残さない"""
    bad = 0
    host = lambda u: (urllib.parse.urlsplit(u).hostname or "").removeprefix("www.")
    leak = [r["lg_code"] for r in rows if "jlis_url" in r["official_sites_check"]]
    used = [r for r in rows if r["official_sites_check"]["jlis_used"]]
    wrong = [r["lg_code"] for r in used if not (r["official_sites_check"]["needs_review"] and r["official_sites_source"] == "jlis")]
    wrong += [r["lg_code"] for r in rows if r["official_sites_source"] == "jlis" and r not in used]
    mixed = [r["lg_code"] for r in rows if any(GOV_HOST.search(host(u)) for u in r["official_sites"])
             and not all(GOV_HOST.search(host(u)) for u in r["official_sites"])]
    print("公式サイト: J-LIS の URL を使った", len(used), [r["name"] for r in used],
          " J-LIS の URL の書き出し", len(leak), " 印の食い違い", len(wrong), " 自治体らしいホストとそれ以外が混ざる", len(mixed))
    bad += bool(leak) + bool(wrong) + bool(mixed)
    return bad


def check_abr(rows):
    """北方領土の 6 村は ABR に町字の行が無いので、印はどれも false"""
    ng = [r["lg_code"] for r in rows if r["lg_code"] in NT
          and any(r["abr_parcel_coverage"][k] for k in ("mt_town", "mt_parcel", "mt_parcel_pos"))]
    empty = [r["lg_code"] for r in rows if r["abr_parcel_coverage"]["mt_town"] and not r["abr_parcel_coverage"]["mt_town_rows"]]
    print("ABR: 北方領土で印が true", len(ng), " 町字の行が 0 なのに mt_town が true", len(empty))
    return bool(ng) + bool(empty)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    d = json.load(open(OUT / "municipalities.json", encoding="utf-8"))
    rows = d["municipalities"]
    eff = [r for r in rows if r["kind"] != "北方領土"]
    kinds = collections.Counter(r["kind"] for r in rows)
    ok = (len(rows), len(eff), kinds["市"], kinds["町"], kinds["村"], kinds["特別区"], kinds["北方領土"]) == \
        (1747, 1741, 792, 743, 183, 23, 6)
    print("件数", len(rows), len(eff), dict(kinds), "OK" if ok else "NG")
    bad += not ok
    seirei = [r for r in rows if r["wards"]]
    ok = len(seirei) == 20 and sum(len(r["wards"]) for r in seirei) == 171
    print("政令市", len(seirei), "区", sum(len(r["wards"]) for r in seirei), "OK" if ok else "NG")
    bad += not ok
    print("島の種類", dict(collections.Counter(r["island_class"] for r in eff)))
    nosite = [r["lg_code"] + " " + r["name"] for r in eff if not r["official_sites"]]
    print("公式サイトの無い自治体 (北方領土を除く)", len(nosite), nosite)
    bad += bool(nosite)
    dates = sorted(r["lg_code"] for r in rows if r["coverage"]["answer_type"] == "dates")
    ok = dates == ["045811", "172014"]
    print("日付で答えられる", dates, "OK" if ok else "NG")
    bad += not ok
    csv_rows = list(csv.DictReader(open(OUT / "municipalities.csv", encoding="utf-8")))
    ok = [r["lg_code"] for r in csv_rows] == [r["lg_code"] for r in rows]
    print("CSV と JSON の行", len(csv_rows), "OK" if ok else "NG")
    bad += not ok

    areas = json.load(open(OUT / "no_service_areas.json", encoding="utf-8"))["areas"]
    print("答えない所", len(areas), dict(collections.Counter(a["type"] for a in areas)))
    for name, lat, lon, lg, want in GPS2CITY_SAMPLES:
        got, why = should_answer(areas, lg, lat, lon)
        print(f"  {'OK' if got == want else 'NG'} {name}: {'答える' if got else '答えない (' + why + ')'}")
        bad += got != want
    # 島のグループの bbox に、同じ自治体の町字のある別の島 (住民のいる島) が入っていないか
    comps = {r["comp_id"]: r for r in csv.DictReader(open(OUT / "islands.csv", encoding="utf-8"))}
    for a in areas:
        if "components" not in a:
            continue
        mine = {str(c["comp_id"]) for c in a["components"]}
        x0, y0, x1, y1 = a["bbox"]
        other = [c for c in comps.values() if a["n03_code"] in c["n03_codes"].split() and c["comp_id"] not in mine
                 and int(c["n_towns"]) > 0 and x0 <= float(c["rep_lon"]) <= x1 and y0 <= float(c["rep_lat"]) <= y1]
        print(f"  {'OK' if not other else 'NG'} {a['id']}: bbox に入るほかの町字のある島 {len(other)}")
        bad += bool(other)
    bad += check_islands(rows) + check_sites(rows) + check_abr(rows)
    print("問題", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
