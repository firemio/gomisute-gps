"""LLM の答えの形を確かめ、書かれた規則から日付を計算する (意味は読まない。計算だけ)

  規則 → 日付 (毎週・第N・最後・毎月の日・N 週ごと・months)。第N は「月の何回目のその曜日か」((日 - 1) // 7 + 1)
  祝日 → holidays.py (国民の祝日・振替休日・国民の休日)。移す先は shift (翌日・翌平日・翌週の同じ曜日・前の平日)
  例外は LLM が書いた scope の地区・品目の、dates 以外の規則の日にだけ当てる。順番:
    第N回目の休み → 祝日 → 休みの期間 → 休みの月 → 休みの日 → 移す日 → 足す日・印字の日付 (dates) → 期間で切る

答えてよいか (answerable) は計算とは別に記録する: 期間が無い・規則が無い・その地区か品目を指す unsure がある・(dates 以外の規則で)
祝日の扱いが無い・期間が 12/31 を含むのに年末年始の扱いが無い、のどれかがあれば false。
"""
import datetime as dt

import jsonschema

import holidays
from prompt import SCHEMA

WD_IDX = {w: i for i, w in enumerate(("MO", "TU", "WE", "TH", "FR", "SA", "SU"))}
NEEDS = {"weekly": ("weekdays",), "nth_weekday": ("weekdays", "nth"), "last_weekday": ("weekdays",), "monthly_day": ("days",),
         "every_n_weeks": ("weekdays", "n", "anchor"), "dates": ("dates",)}
EXC_NEEDS = {"holiday": ("policy",), "closure": ("from", "to"), "nth_off": ("nth",), "months_off": ("months",),
             "cancelled": ("dates",), "moved": ("from", "to"), "added": ("dates",), "year_end_normal": ()}


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def validate(ans):
    """答え → 問題のリスト (空なら形は正しい)"""
    if not isinstance(ans, dict):
        return ["答えが JSON のオブジェクトでない"]
    errs = [f"{'/'.join(map(str, e.path))}: {e.message[:200]}" for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(ans)]
    if errs:
        return errs
    dids = [x["id"] for x in ans["districts"]]
    cids = [x["id"] for x in ans["categories"]]
    for name, ids in (("districts", dids), ("categories", cids)):
        if len(ids) != len(set(ids)):
            errs.append(f"{name} の id が重なる")
    for i, s in enumerate(ans["schedules"]):
        if s["district"] not in dids or s["category"] not in cids:
            errs.append(f"schedules/{i}: 無い id ({s['district']}, {s['category']})")
        for j, r in enumerate(s["rules"]):
            miss = [k for k in NEEDS[r["type"]] if k not in r]
            if miss:
                errs.append(f"schedules/{i}/rules/{j}: {r['type']} に {miss} が無い")
            for k in ("anchor", "from", "to", *(["dates"] if r["type"] == "dates" else [])):
                for v in (r.get(k) if isinstance(r.get(k), list) else [r.get(k)]):
                    if v is not None:
                        try:
                            _d(v)
                        except ValueError:
                            errs.append(f"schedules/{i}/rules/{j}: 日付でない {v}")
    for i, e in enumerate(ans["exceptions"]):
        miss = [k for k in EXC_NEEDS[e["kind"]] if k not in e]
        if e["kind"] == "holiday" and e.get("policy") == "moved" and "moved_to" not in e:
            miss.append("moved_to")
        if miss:
            errs.append(f"exceptions/{i}: {e['kind']} に {miss} が無い")
        for k, ids in (("districts", dids), ("categories", cids)):
            bad = [x for x in e["scope"][k] if x != "*" and x not in ids]
            if bad:
                errs.append(f"exceptions/{i}: scope の {k} に無い id {bad}")
    return errs


# ---- 規則 → 日付 ----
def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def nth_of_month(d):
    """その日が、月の何回目のその曜日か"""
    return (d.day - 1) // 7 + 1


def is_last_of_month(d):
    """その日が、月の最後のその曜日か"""
    return (d + dt.timedelta(days=7)).month != d.month


def _matcher(r):
    """規則 1 つ (dates 以外) → 日付を受けて、その規則の日か返す関数"""
    t = r["type"]
    if t == "monthly_day":
        ds = set(r["days"])
        return lambda d: d.day in ds
    wd = {WD_IDX[w] for w in r["weekdays"]}
    if t == "weekly":
        return lambda d: d.weekday() in wd
    if t == "nth_weekday":
        ns = set(r["nth"])
        return lambda d: d.weekday() in wd and nth_of_month(d) in ns
    if t == "last_weekday":
        return lambda d: d.weekday() in wd and is_last_of_month(d)
    if t == "every_n_weeks":
        # 起点は印字の収集日。曜日が違う曜日の起点は、その週の同じ週の中の曜日にずらす (起点の週を数えの元にする)
        a, step = _d(r["anchor"]), 7 * int(r["n"])
        starts = [a + dt.timedelta(days=WD_IDX[w] - a.weekday()) for w in r["weekdays"]]
        return lambda d: any((d - s).days % step == 0 for s in starts)
    raise ValueError(f"知らない規則の種類 {t}")


def rule_dates(r, lo, hi):
    """規則 1 つ → 日付の集まり ([lo, hi] と、規則の from・to で切る。months があればその月だけ)"""
    a, b = max(lo, _d(r["from"])) if r.get("from") else lo, min(hi, _d(r["to"])) if r.get("to") else hi
    if a > b:
        return set()
    months = set(r.get("months") or ())
    if r["type"] == "dates":
        return {x for x in map(_d, r["dates"]) if a <= x <= b and (not months or x.month in months)}
    hit = _matcher(r)
    return {d for d in _days(a, b) if (not months or d.month in months) and hit(d)}


def shift(d, to):
    """祝日の収集を移す先 (moved_to) → 日付"""
    if to == "next_day":
        return d + dt.timedelta(days=1)
    if to == "next_weekday":
        x = d + dt.timedelta(days=1)
        while x.weekday() >= 5 or holidays.holiday(x):
            x += dt.timedelta(days=1)
        return x
    if to == "next_same_weekday":
        return d + dt.timedelta(days=7)
    if to == "prev_weekday":
        x = d - dt.timedelta(days=1)
        while x.weekday() >= 5 or holidays.holiday(x):
            x -= dt.timedelta(days=1)
        return x
    return None


def _in_scope(e, did, cid):
    s = e["scope"]
    return ("*" in s["districts"] or did in s["districts"]) and ("*" in s["categories"] or cid in s["categories"])


def _holiday_in_table(d):
    try:
        return holidays.holiday(d)
    except ValueError:
        return None


def series_dates(s, excs, lo, hi):
    """系列 (地区 × 品目) の規則と当たる例外 → (日付の集まり, 使った例外の番号, 問題)"""
    pattern, listed = set(), set()
    has_pattern = False
    for r in s["rules"]:
        if r["type"] == "dates":
            listed |= rule_dates(r, lo, hi)
        else:
            has_pattern = True
            pattern |= rule_dates(r, lo, hi)
    used, notes = [], []
    if has_pattern:
        for i, e in excs:
            if e["kind"] == "nth_off":
                pattern = {d for d in pattern if nth_of_month(d) not in e["nth"]}
                used.append(i)
        hol = [(i, e) for i, e in excs if e["kind"] == "holiday"]
        if len({(e["policy"], e.get("moved_to")) for _, e in hol}) > 1:
            notes.append("holiday_conflict")                     # 2 通りの扱いが当たる: どちらも当てない
        elif hol:
            i, e = hol[0]
            used.append(i)
            if e["policy"] != "collected":
                for d in sorted(pattern):
                    if _holiday_in_table(d):
                        pattern.discard(d)
                        if e["policy"] == "moved":
                            t = shift(d, e["moved_to"])
                            if t:
                                pattern.add(t)
        for i, e in excs:
            k = e["kind"]
            if k == "closure":
                pattern = {d for d in pattern if not _d(e["from"]) <= d <= _d(e["to"])}
            elif k == "months_off":
                pattern = {d for d in pattern if d.month not in e["months"]}
            elif k == "cancelled":
                pattern -= set(map(_d, e["dates"]))
            elif k == "moved":
                if _d(e["from"]) in pattern:
                    pattern.discard(_d(e["from"]))
                    pattern.add(_d(e["to"]))
            else:
                continue
            used.append(i)
    for i, e in excs:
        if e["kind"] == "added":
            listed |= set(map(_d, e["dates"]))
            used.append(i)
    dates = {d for d in pattern | listed if lo <= d <= hi}
    return dates, sorted(set(used)), notes


def expand_answer(ans, window=None):
    """答え → {"period": {...}, "series": [{district, category, dates, answerable, why, has_pattern}]}。
    答えに期間が無ければ window (正解の側の期間。(from, to)) で計算し、period_from_key の印を付ける"""
    per = ans.get("period") or {}
    why_all = []
    if per.get("from") and per.get("to"):
        lo, hi = _d(per["from"]), _d(per["to"])
        src = "answer"
    elif window:
        lo, hi = _d(window[0]), _d(window[1])
        src = "answer_key"
        why_all.append("period_missing")
    else:
        return {"period": None, "series": [], "error": "期間が無い"}
    # 地区・品目を指さない unsure (自治体の名前・期間・祝日が書いていない など) は止めない。数だけ記録する
    # (期間・祝日・年末年始は下の形の確かめで止める)。地区か品目を指す unsure は、その系列を止める
    unsure_all = [u for u in ans.get("unsure") or [] if not u.get("district") and not u.get("category")]
    out = []
    excs_all = list(enumerate(ans.get("exceptions") or []))
    has_dec31 = any(lo <= dt.date(y, 12, 31) <= hi for y in range(lo.year, hi.year + 1))
    for s in ans.get("schedules") or []:
        did, cid = s["district"], s["category"]
        excs = [(i, e) for i, e in excs_all if _in_scope(e, did, cid)]
        try:
            dates, used, notes = series_dates(s, excs, lo, hi)
        except (ValueError, KeyError) as e:
            dates, used, notes = set(), [], [f"calc_error:{e}"]
        why = list(why_all) + notes
        pattern = any(r["type"] != "dates" for r in s["rules"])
        if not s["rules"]:
            why.append("no_rules")
        if any(u.get("district") in (did, None) and u.get("category") in (cid, None) and (u.get("district") or u.get("category"))
               for u in ans.get("unsure") or []):
            why.append("unsure")
        if pattern and not any(e["kind"] == "holiday" for _, e in excs):
            why.append("holiday_not_stated")
        if pattern and has_dec31 and not any(e["kind"] == "year_end_normal" or (e["kind"] == "closure" and e.get("year_end"))
                                             for _, e in excs):
            why.append("year_end_not_stated")
        out.append({"district": did, "category": cid, "dates": sorted(x.isoformat() for x in dates), "has_pattern": pattern,
                    "exceptions_used": used, "answerable": not why, "why": why})
    return {"period": {"from": lo.isoformat(), "to": hi.isoformat(), "source": src}, "series": out, "unsure_global": len(unsure_all)}
