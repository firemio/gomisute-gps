"""calc.py: LLM の答えの JSON の形の確かめと、書かれた規則からの日付の計算 (ネットにつながない。モデルは呼ばない)"""
import copy, datetime as dt, unittest

import _util
import calc


def answer(rules, exceptions=(), period=("2026-04-01", "2027-03-31"), unsure=()):
    """地区 1 つ・品目 1 つの答え (prompt.SCHEMA の形)"""
    return {"municipality": "テスト市", "period": {"printed": None, "from": period[0], "to": period[1]},
            "districts": [{"id": "d1", "label": "本町", "members": ["本町1丁目"]}],
            "categories": [{"id": "c1", "label": "燃やすごみ", "coarse": ["burnable"]}],
            "schedules": [{"district": "d1", "category": "c1", "rules": list(rules)}],
            "exceptions": [{"scope": {"districts": ["*"], "categories": ["*"]}, **e} for e in exceptions],
            "unsure": list(unsure)}


HOLIDAY_OFF = {"kind": "holiday", "policy": "not_collected"}
YEAR_END = {"kind": "closure", "from": "2026-12-29", "to": "2027-01-03", "year_end": True}


def dates(ans, window=None):
    ex = calc.expand_answer(ans, window)
    return ex["series"][0]["dates"], ex["series"][0]


class TestValidate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(calc.validate(answer([{"type": "weekly", "weekdays": ["MO", "TH"]}], [HOLIDAY_OFF, YEAR_END])), [])

    def test_not_an_object(self):
        self.assertTrue(calc.validate(["x"]))
        self.assertTrue(calc.validate(None))

    def test_schema_errors(self):
        a = answer([{"type": "weekly", "weekdays": ["MON"]}])               # 曜日の書き方が違う
        self.assertTrue(calc.validate(a))
        a = answer([{"type": "weekly", "weekdays": ["MO"]}])
        a["extra"] = 1                                                       # 決まっていない項目
        self.assertTrue(calc.validate(a))
        a = answer([{"type": "dates", "dates": ["2026-04-31"]}])            # 形は日付だが無い日
        self.assertTrue(any("日付でない" in e for e in calc.validate(a)))

    def test_missing_rule_fields(self):
        errs = calc.validate(answer([{"type": "nth_weekday", "weekdays": ["TU"]}]))
        self.assertTrue(any("['nth']" in e for e in errs), errs)
        errs = calc.validate(answer([{"type": "weekly", "weekdays": ["MO"]}], [{"kind": "holiday", "policy": "moved"}]))
        self.assertTrue(any("['moved_to']" in e for e in errs), errs)

    def test_unknown_ids(self):
        a = answer([{"type": "weekly", "weekdays": ["MO"]}])
        a["schedules"][0]["district"] = "d9"
        self.assertTrue(any("無い id" in e for e in calc.validate(a)))
        a = answer([{"type": "weekly", "weekdays": ["MO"]}], [{"kind": "added", "dates": ["2026-04-04"]}])
        a["exceptions"][0]["scope"]["categories"] = ["c9"]
        self.assertTrue(any("scope" in e for e in calc.validate(a)))
        a = answer([{"type": "weekly", "weekdays": ["MO"]}])
        a["districts"].append(copy.deepcopy(a["districts"][0]))
        self.assertTrue(any("重なる" in e for e in calc.validate(a)))


class TestExpand(unittest.TestCase):
    APRIL = ("2026-04-01", "2026-04-30")

    def test_nth_weekday(self):
        got, _ = dates(answer([{"type": "nth_weekday", "weekdays": ["TU"], "nth": [2, 4]}], [HOLIDAY_OFF], self.APRIL))
        self.assertEqual(got, ["2026-04-14", "2026-04-28"])                 # 4 月の火曜は 7・14・21・28 日

    def test_last_weekday_and_monthly_day(self):
        got, _ = dates(answer([{"type": "last_weekday", "weekdays": ["FR"]}], [HOLIDAY_OFF], self.APRIL))
        self.assertEqual(got, ["2026-04-24"])
        got, _ = dates(answer([{"type": "monthly_day", "days": [1, 15]}], [HOLIDAY_OFF], self.APRIL))
        self.assertEqual(got, ["2026-04-01", "2026-04-15"])

    def test_every_n_weeks_anchor(self):
        """起点 4/6 (月) の 2 週ごと。木曜は起点の週の木曜 (4/9) から数える"""
        r = {"type": "every_n_weeks", "weekdays": ["MO", "TH"], "n": 2, "anchor": "2026-04-06"}
        got, _ = dates(answer([r], [HOLIDAY_OFF], self.APRIL))
        self.assertEqual(got, ["2026-04-06", "2026-04-09", "2026-04-20", "2026-04-23"])

    def test_months_and_rule_range(self):
        r = {"type": "weekly", "weekdays": ["MO"], "months": [5], "from": "2026-05-10"}
        got, _ = dates(answer([r], [HOLIDAY_OFF], ("2026-04-01", "2026-06-30")))
        self.assertEqual(got, ["2026-05-11", "2026-05-18", "2026-05-25"])    # 5/4 (みどりの日) は from より前

    def test_holiday_policies(self):
        """4/29 (水) は昭和の日。収集しない → 落とす、翌日に移す → 4/30、収集する → そのまま"""
        wed = [{"type": "weekly", "weekdays": ["WE"]}]
        got, s = dates(answer(wed, [HOLIDAY_OFF], self.APRIL))
        self.assertNotIn("2026-04-29", got)
        self.assertTrue(s["answerable"])
        got, _ = dates(answer(wed, [{"kind": "holiday", "policy": "moved", "moved_to": "next_day"}], self.APRIL))
        self.assertIn("2026-04-30", got)
        self.assertNotIn("2026-04-29", got)
        got, _ = dates(answer(wed, [{"kind": "holiday", "policy": "collected"}], self.APRIL))
        self.assertIn("2026-04-29", got)

    def test_moved_next_weekday_skips_holidays(self):
        """5/4 (月・みどりの日) を翌平日へ: 5/5 (こどもの日)・5/6 (振替休日) を飛ばして 5/7"""
        got, _ = dates(answer([{"type": "weekly", "weekdays": ["MO"]}],
                              [{"kind": "holiday", "policy": "moved", "moved_to": "next_weekday"}], ("2026-05-01", "2026-05-10")))
        self.assertEqual(got, ["2026-05-07"])

    def test_closure_nth_off_and_added(self):
        tue = [{"type": "weekly", "weekdays": ["TU"]}]
        got, s = dates(answer(tue, [HOLIDAY_OFF, YEAR_END], ("2026-12-01", "2027-01-31")))
        self.assertNotIn("2026-12-29", got)
        self.assertIn("2026-12-22", got)
        self.assertTrue(s["answerable"])
        got, _ = dates(answer([{"type": "weekly", "weekdays": ["TH"]}], [HOLIDAY_OFF, {"kind": "nth_off", "nth": [5]}], self.APRIL))
        self.assertEqual(got, ["2026-04-02", "2026-04-09", "2026-04-16", "2026-04-23"])   # 4/30 は第5木曜
        got, _ = dates(answer(tue, [HOLIDAY_OFF, {"kind": "added", "dates": ["2026-04-04"]},
                                    {"kind": "cancelled", "dates": ["2026-04-07"]}], self.APRIL))
        self.assertEqual(got, ["2026-04-04", "2026-04-14", "2026-04-21", "2026-04-28"])

    def test_printed_dates_keep_holidays(self):
        """印字の日付 (dates) には祝日の扱いを当てない。祝日の扱いが無くても答えてよい"""
        got, s = dates(answer([{"type": "dates", "dates": ["2026-04-29", "2026-05-13"]}], (), self.APRIL))
        self.assertEqual(got, ["2026-04-29"])                               # 期間の外の 5/13 は落とす
        self.assertTrue(s["answerable"])

    def test_not_answerable(self):
        mon = [{"type": "weekly", "weekdays": ["MO"]}]
        _, s = dates(answer(mon))                                           # 祝日・年末年始の扱いが無い
        self.assertFalse(s["answerable"])
        self.assertEqual(set(s["why"]), {"holiday_not_stated", "year_end_not_stated"})
        _, s = dates(answer(mon, [HOLIDAY_OFF, YEAR_END], unsure=[{"district": "d1", "category": None, "note": "?"}]))
        self.assertEqual(s["why"], ["unsure"])
        a = answer(mon, [HOLIDAY_OFF], period=(None, None))
        ex = calc.expand_answer(a, ("2026-04-01", "2026-04-30"))           # 期間が無い: 正解の側の期間で計算するが答えない
        self.assertEqual(ex["period"]["source"], "answer_key")
        self.assertIn("period_missing", ex["series"][0]["why"])
        self.assertEqual(calc.expand_answer(a)["error"], "期間が無い")

    def test_holiday_conflict_not_applied(self):
        two = [{"kind": "holiday", "policy": "not_collected"}, {"kind": "holiday", "policy": "collected"}]
        got, s = dates(answer([{"type": "weekly", "weekdays": ["WE"]}], two, self.APRIL))
        self.assertIn("2026-04-29", got)
        self.assertIn("holiday_conflict", s["why"])


if __name__ == "__main__":
    unittest.main()
