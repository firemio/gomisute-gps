"""日本の休日の表 (休日の種類つき)。2020 年度〜2027 年度 (2020-04-01〜2028-03-31)

  python nationwide/readers/holidays.py 2026                 # 2026 年度の休日を並べる
  python nationwide/readers/holidays.py --check <内閣府の syukujitsu.csv>   # 内閣府の表と突き合わせる

休日の種類 (DESIGN.md §2.6 の covers):
  national    国民の祝日 (祝日法 2 条。元日・成人の日 …)
  substitute  振替休日 (祝日法 3 条 2 項。祝日が日曜なら、その後のいちばん近い祝日でない日)
  citizens    国民の休日 (祝日法 3 条 3 項。前の日と次の日が祝日である、祝日でない日)
  all_weekdays は種類ではなく「すべての平日」(金沢「月曜日～金曜日（祝日含む）」)。is_covered で使う

作り方: 祝日法と、東京五輪の特例 (2020 年・2021 年の海の日・スポーツの日・山の日の移動) から計算する。
春分の日・秋分の日は前の年の 2 月の官報で決まる。表の EQUINOX に書いたものを使い、まだ発表されていない年
(2028 年の春分の日。2027 年 2 月に発表) は計算の値に provisional の印を付ける。
内閣府の「国民の祝日」の CSV は、まだ取っていない (手元に置いたら --check で突き合わせる)。
"""
import csv, datetime as dt, io, sys

KINDS = ("national", "substitute", "citizens")
FIRST_FY, LAST_FY = 2020, 2027

# 官報で発表された春分の日・秋分の日 (月, 日)。発表の無い年は計算の値を使い provisional とする
EQUINOX = {
    2020: ((3, 20), (9, 22)), 2021: ((3, 20), (9, 23)), 2022: ((3, 21), (9, 23)), 2023: ((3, 21), (9, 23)),
    2024: ((3, 20), (9, 22)), 2025: ((3, 20), (9, 23)), 2026: ((3, 20), (9, 23)), 2027: ((3, 21), (9, 23)),
}
ANNOUNCED_THROUGH = 2027          # 2026 年 2 月の官報で 2027 年まで発表済み (今日 2026-10 の時点)

# 東京五輪の特例 (国民の祝日に関する法律の特例。2020 年・2021 年だけ)
OLYMPIC = {
    2020: {"海の日": (7, 23), "スポーツの日": (7, 24), "山の日": (8, 10)},
    2021: {"海の日": (7, 22), "スポーツの日": (7, 23), "山の日": (8, 8)},
}


def equinox_calc(year):
    """春分の日・秋分の日の計算の値 (1980〜2099 年に使われる式)"""
    y = year - 1980
    spring = int(20.8431 + 0.242194 * y - int(y / 4))
    autumn = int(23.2488 + 0.242194 * y - int(y / 4))
    return (3, spring), (9, autumn)


def _nth_monday(year, month, n):
    d = dt.date(year, month, 1)
    first = d + dt.timedelta(days=(0 - d.weekday()) % 7)
    return first + dt.timedelta(days=7 * (n - 1))


def national(year):
    """その暦年の国民の祝日 → {日付: (名前, provisional)}"""
    out = {}
    def put(m, d, name, prov=False):
        out[dt.date(year, m, d)] = (name, prov)
    put(1, 1, "元日")
    out[_nth_monday(year, 1, 2)] = ("成人の日", False)
    put(2, 11, "建国記念の日")
    if year >= 2020:
        put(2, 23, "天皇誕生日")
    if year in EQUINOX:
        (sm, sd), (am, ad) = EQUINOX[year]
        prov = False
    else:
        (sm, sd), (am, ad) = equinox_calc(year)
        prov = year > ANNOUNCED_THROUGH
    put(sm, sd, "春分の日", prov)
    put(4, 29, "昭和の日")
    put(5, 3, "憲法記念日")
    put(5, 4, "みどりの日")
    put(5, 5, "こどもの日")
    sp = OLYMPIC.get(year, {})
    if "海の日" in sp:
        put(*sp["海の日"], "海の日")
    else:
        out[_nth_monday(year, 7, 3)] = ("海の日", False)
    put(*sp.get("山の日", (8, 11)), "山の日")
    out[_nth_monday(year, 9, 3)] = ("敬老の日", False)
    put(am, ad, "秋分の日", prov)
    if "スポーツの日" in sp:
        put(*sp["スポーツの日"], "スポーツの日")
    else:
        out[_nth_monday(year, 10, 2)] = ("スポーツの日", False)
    put(11, 3, "文化の日")
    put(11, 23, "勤労感謝の日")
    return out


def calendar_year(year):
    """その暦年の休日 → {日付: {"name", "kind", "provisional"}}"""
    nat = national(year)
    nxt = national(year + 1)                     # 12/31 の次の日 (国民の休日の判定) のため
    out = {d: {"name": n, "kind": "national", "provisional": p} for d, (n, p) in nat.items()}
    for d, (n, p) in sorted(nat.items()):        # 振替休日: 日曜の祝日のあと、いちばん近い祝日でない日
        if d.weekday() == 6:
            x = d + dt.timedelta(days=1)
            while x in nat:
                x += dt.timedelta(days=1)
            if x.year == year:
                out[x] = {"name": "振替休日", "kind": "substitute", "provisional": p}
    prev = national(year - 1)
    allnat = {**prev, **nat, **nxt}
    d = dt.date(year, 1, 1)
    while d.year == year:                         # 国民の休日: 前と次の日が祝日で、自分は祝日でない日
        if d not in nat and (d - dt.timedelta(days=1)) in allnat and (d + dt.timedelta(days=1)) in allnat \
                and d.weekday() != 6 and d not in out:
            p = allnat[d - dt.timedelta(days=1)][1] or allnat[d + dt.timedelta(days=1)][1]
            out[d] = {"name": "国民の休日", "kind": "citizens", "provisional": p}
        d += dt.timedelta(days=1)
    return out


def table(first_fy=FIRST_FY, last_fy=LAST_FY):
    """年度の範囲の休日 → {日付: {"name", "kind", "provisional"}} (年度 = 4 月 1 日〜翌 3 月 31 日)"""
    lo, hi = dt.date(first_fy, 4, 1), dt.date(last_fy + 1, 3, 31)
    out = {}
    for y in range(first_fy, last_fy + 2):
        for d, v in calendar_year(y).items():
            if lo <= d <= hi:
                out[d] = v
    return dict(sorted(out.items()))


_TABLE = None


def _all():
    global _TABLE
    if _TABLE is None:
        _TABLE = table()
    return _TABLE


def _date(d):
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])


def in_range(d):
    d = _date(d)
    return dt.date(FIRST_FY, 4, 1) <= d <= dt.date(LAST_FY + 1, 3, 31)


def holiday(d):
    """その日の休日の記録 ({"name", "kind", "provisional"})。休日でなければ None。表の範囲の外は例外"""
    d = _date(d)
    if not in_range(d):
        raise ValueError(f"{d} は休日の表の範囲 ({FIRST_FY} 年度〜{LAST_FY} 年度) の外")
    return _all().get(d)


def fiscal_year(fy):
    """その年度の休日 → {日付: 記録}"""
    lo, hi = dt.date(fy, 4, 1), dt.date(fy + 1, 3, 31)
    return {d: v for d, v in _all().items() if lo <= d <= hi}


def is_covered(d, covers):
    """休日 d が、方針の covers (national / substitute / citizens / all_weekdays) に入っているか。休日でなければ True
    (「祝日」とだけ書いた方針は ["national"] で、振替休日・国民の休日は入らない。DESIGN.md §2.6)"""
    h = holiday(d)
    if h is None:
        return True
    covers = set(covers or ())
    if "all_weekdays" in covers and _date(d).weekday() < 5:
        return True
    return h["kind"] in covers


def uncovered(dates, covers):
    """日付の並びのうち、covers に入らない休日 → [(日付, 記録)]"""
    out = []
    for d in dates:
        d = _date(d)
        if not is_covered(d, covers):
            out.append((d, holiday(d)))
    return out


def check_against_cao(csv_bytes, first_fy=FIRST_FY, last_fy=LAST_FY):
    """内閣府「国民の祝日」の CSV (syukujitsu.csv、cp932。「国民の祝日・休日月日,国民の祝日・休日名称」) と突き合わせる
    → {"missing": [表に無い日], "extra": [CSV に無い日], "name_diff": [...]}。範囲は年度で切る"""
    text = csv_bytes.decode("cp932") if not csv_bytes.startswith(b"\xef\xbb\xbf") else csv_bytes.decode("utf-8-sig")
    lo, hi = dt.date(first_fy, 4, 1), dt.date(last_fy + 1, 3, 31)
    theirs = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            y, m, d = (int(x) for x in row[0].replace("-", "/").split("/"))
        except ValueError:
            continue
        day = dt.date(y, m, d)
        if lo <= day <= hi:
            theirs[day] = row[1].strip()
    mine = table(first_fy, last_fy)
    last_cao = max(theirs) if theirs else lo
    return {"missing": sorted(str(d) for d in theirs if d not in mine),
            "extra": sorted(str(d) for d in mine if d not in theirs and d <= last_cao),
            "name_diff": sorted((str(d), mine[d]["name"], theirs[d]) for d in theirs
                                if d in mine and mine[d]["kind"] == "national" and mine[d]["name"] != theirs[d])}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--check"]:
        res = check_against_cao(open(argv[1], "rb").read())
        print(res)
        return 0 if not any(res.values()) else 1
    fy = int(argv[0]) if argv else 2026
    for d, v in fiscal_year(fy).items():
        print(d, "月火水木金土日"[d.weekday()], v["kind"], v["name"], "(計算の値・未発表)" if v["provisional"] else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
