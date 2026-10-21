"""rules.py: 規則の文法・漢数字と「ニ」・「無し」・日付・RRULE・方針の文の文法 (ネットにつながない)"""
import datetime as dt
import unittest

import _util  # noqa: F401
import rules

D = dt.date.fromisoformat


class TestGrammar(unittest.TestCase):
    def ir(self, text, nth_def=None):
        p = rules.parse(text, nth_def)
        return p.status, p.irs

    def test_weekly(self):
        for t in ("月・木", "毎週 月曜日・木曜日", "毎週月曜日、木曜日", "月曜日 / 木曜日"):
            self.assertEqual(self.ir(t), ("ok", [{"kind": "weekly", "weekdays": ["MO", "TH"]}]), t)
        self.assertEqual(self.ir("月曜日～土曜日")[1][0]["weekdays"], ["MO", "TU", "WE", "TH", "FR", "SA"])

    def test_except_nth(self):          # 石狩「毎週 月曜日（1回目の月曜日を除く）」
        st, irs = self.ir("毎週 月曜日（1回目の月曜日を除く）")
        self.assertEqual(irs, [{"kind": "weekly", "weekdays": ["MO"], "except_nth": [1]}])
        self.assertTrue(rules.is_monthly(irs[0]))                 # 月の回を数えるので月ごとの規則
        self.assertFalse(rules.is_monthly({"kind": "weekly", "weekdays": ["MO"]}))

    def test_nth_forms(self):
        want = [{"kind": "monthly_nth", "weekday": "WE", "nth": [1, 3]}]
        for t in ("第1・第3水曜日", "第１・３水", "1･3水", "1回目と3回目の水曜", "一回目と三回目の水曜日", "第1,3水曜日", "毎月1回目・3回目の水",
                  "1番目と3番目の水曜日"):
            self.assertEqual(self.ir(t), ("ok", want), t)
        self.assertEqual(self.ir("第１・３・(5)火")[1][0]["nth"], [1, 3, 5])
        self.assertEqual(self.ir("第２･５木曜日")[1][0]["nth"], [2, 5])          # 七ヶ宿
        self.assertEqual(self.ir("第1・2・3・5\n金曜日")[1][0]["nth"], [1, 2, 3, 5])   # 東秩父 (マスの中の折り返し)

    def test_kanji_and_katakana_ni(self):
        want = ("ok", [{"kind": "monthly_nth", "weekday": "WE", "nth": [2, 4]}])
        self.assertEqual(self.ir("第二・四水"), want)
        self.assertEqual(self.ir("第ニ・四水"), want)              # カタカナの「ニ」は「第」と区切りの間だけ
        self.assertEqual(rules.prep("ニュータウン月曜"), "ニュータウン月曜")   # 地名の「ニ」は寄せない

    def test_ambiguous_week(self):      # 三郷・蕨・美深
        for t in ("第1週・第3週の土曜日", "1・3週目の土", "第2、4週 月曜日"):
            st, irs = self.ir(t)
            self.assertEqual(st, "ambiguous", t)
            self.assertEqual(irs[0]["kind"], "ambiguous_week")
        self.assertEqual(self.ir("びん、かん（第1・3週）")[0], "ambiguous")   # 曜日の無い「第N週」も
        st, irs = self.ir("第1・3週の土曜日", "nth")
        self.assertEqual(irs, [{"kind": "monthly_nth", "weekday": "SA", "nth": [1, 3]}])
        with self.assertRaises(ValueError):
            rules.occurrences({"kind": "ambiguous_week", "weekday": "SA", "weeks": [1]}, "2026-04-01", "2026-04-30")

    def test_other_kinds(self):
        self.assertEqual(self.ir("最終金曜日")[1], [{"kind": "monthly_last", "weekday": "FR"}])
        self.assertEqual(self.ir("月末の金曜")[1], [{"kind": "monthly_last", "weekday": "FR"}])
        self.assertEqual(self.ir("隔週水曜日")[1], [{"kind": "biweekly", "weekday": "WE", "anchor": None}])
        self.assertEqual(self.ir("3週間に1回の月曜日")[1][0]["every_weeks"], 3)
        self.assertEqual(self.ir("毎月10日・25日")[1], [{"kind": "monthly_day", "days": [10, 25]}])
        self.assertEqual(self.ir("毎月十日")[1], [{"kind": "monthly_day", "days": [10]}])
        self.assertEqual(self.ir("6・9・12・3月の第4木曜日")[1][0]["months"], [6, 9, 12, 3])
        self.assertEqual(self.ir("5月〜9月は隔週水曜日")[1][0]["kind"], "seasonal")
        self.assertEqual(self.ir("随時")[0], "on_demand")
        self.assertEqual(self.ir("申込制")[0], "on_demand")

    def test_none_marks(self):          # 大田の「ー」「-」
        for t in ("ー", "-", "―", "‐", "−", "✕", "×", " ー "):
            self.assertEqual(self.ir(t), ("none", [{"kind": "none"}]), t)
        self.assertNotEqual(self.ir("ー月")[0], "none")

    def test_adds_rule_subject(self):   # 東秩父「※ペットボトルは、5・6・7・8・9・10月のみ第1水曜日でも出せる」
        p = rules.parse("第3火曜日\n※ペットボトルは、5・6・7・8・9・10月のみ第1水曜日でも出せる")
        self.assertEqual(p.status, "partial_subject")
        self.assertEqual(p.subjects, [None, "ペットボトル"])
        self.assertEqual(p.irs[1]["months"], [5, 6, 7, 8, 9, 10])

    def test_rest_after_weekly(self):   # 金沢の燃やすごみのマス
        p = rules.parse("火曜日・ 金曜日 年末は 12月29日 （火曜日） まで 年始は 1月5日 （火曜日） から")
        self.assertEqual(p.irs, [{"kind": "weekly", "weekdays": ["TU", "FR"]}])
        self.assertIn("年末は12月29日", p.rest)

    def test_unparsed(self):
        self.assertEqual(self.ir("自己搬入")[0], "unparsed")
        self.assertEqual(self.ir("第2・4水曜日、第1土曜日")[0], "multiple")

    def test_nth_definition(self):
        how, _ = rules.nth_definition(["第1とは1～7日、第2とは8～14日になります。"])           # 三鷹
        self.assertEqual(how, "nth")
        how, _ = rules.nth_definition(["第1(または第2・第3・第4)○曜日とは、「当該月の第1番目(または第2番目)の○曜日」ということです。"])
        self.assertEqual(how, "nth")                                                           # 渋谷
        self.assertEqual(rules.nth_definition(["収集日の朝8時までに"]), (None, None))


class TestDates(unittest.TestCase):
    def test_occurrences(self):
        ds = rules.occurrences({"kind": "monthly_nth", "weekday": "TU", "nth": [1, 3, 5]}, "2026-06-01", "2026-06-30")
        self.assertEqual([d.day for d in ds], [2, 16, 30])
        ds = rules.occurrences({"kind": "weekly", "weekdays": ["MO"], "except_nth": [1]}, "2026-06-01", "2026-06-30")
        self.assertEqual([d.day for d in ds], [8, 15, 22, 29])
        ds = rules.occurrences({"kind": "monthly_last", "weekday": "FR"}, "2026-07-01", "2026-07-31")
        self.assertEqual([d.day for d in ds], [31])
        ds = rules.occurrences({"kind": "calendar_week", "weekday": "SA", "weeks": [1], "week_starts": "MO"}, "2026-08-01", "2026-08-31")
        self.assertEqual([d.day for d in ds], [1])
        ds = rules.occurrences({"kind": "biweekly", "weekday": "WE", "anchor": "2026-04-08"}, "2026-04-01", "2026-05-10")
        self.assertEqual([d.isoformat() for d in ds], ["2026-04-08", "2026-04-22", "2026-05-06"])
        with self.assertRaises(ValueError):
            rules.occurrences({"kind": "biweekly", "weekday": "WE", "anchor": None}, "2026-04-01", "2026-05-01")

    def test_week_readings_differ(self):
        ir = {"kind": "ambiguous_week", "weekday": "SA", "weeks": [1]}
        got = {name: [d.day for d in rules.occurrences(x, "2026-08-01", "2026-08-31")] for name, x in rules.week_readings(ir)}
        self.assertEqual(got, {"nth": [1], "calendar_SU": [1], "calendar_MO": [1]})
        ir = {"kind": "ambiguous_week", "weekday": "MO", "weeks": [1]}
        got = {name: [d.day for d in rules.occurrences(x, "2026-08-01", "2026-08-31")] for name, x in rules.week_readings(ir)}
        self.assertEqual(got["nth"], [3])
        self.assertEqual(got["calendar_SU"], [])                   # 8/1 は土曜。日曜始まりの 1 行目の月曜は 7 月

    def test_compress_roundtrip(self):
        ir = {"kind": "monthly_nth", "weekday": "WE", "nth": [2, 4]}
        ds = [d for d in rules.occurrences(ir, "2026-04-01", "2027-03-31") if d != D("2026-12-23")] + [D("2026-12-25")]
        c = rules.compress(ir, sorted(ds), "2026-04-01", "2027-03-31")
        self.assertEqual(c["rrule"], "FREQ=MONTHLY;BYDAY=2WE,4WE")
        self.assertEqual(c["exdates"], ["2026-12-23"])
        self.assertEqual(c["rdates"], ["2026-12-25"])
        back = set(rules.rrule_dates(c["rrule"], "2026-04-01", "2027-03-31")) - {D(x) for x in c["exdates"]} | {D(x) for x in c["rdates"]}
        self.assertEqual(back, set(ds))
        self.assertIsNone(rules.compress({"kind": "weekly", "weekdays": ["MO"], "except_nth": [1]}, [], "2026-04-01", "2026-04-30"))


class TestPolicyGrammar(unittest.TestCase):
    PER = {"valid_from": "2026-04-01", "valid_to": "2027-03-31"}

    def test_holiday_policy(self):
        self.assertEqual(rules.holiday_policy("※燃えるごみ・資源ごみ・燃えないごみの収集は、祝日、振替休日も実施します。")["covers"],
                         ["national", "substitute"])                                        # 羽咋
        self.assertEqual(rules.holiday_policy("収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。")["covers"], ["all_weekdays"])
        hp = rules.holiday_policy("※祝日も収集します（年末年始を除く）")
        self.assertEqual((hp["value"], hp["covers"], hp["except_year_end"]), ("collected", ["national"], True))
        self.assertEqual(rules.holiday_policy("（祝日の収集は休み）")["value"], "not_collected")
        self.assertEqual(rules.holiday_policy("祝日の場合は翌日に収集します")["shift"], {"to": "next_day"})
        self.assertIsNone(rules.holiday_policy("収集日の朝8時までに出してください"))

    def test_year_end(self):
        y = lambda t: rules.year_end_parts(t, self.PER)
        self.assertEqual(y("12/29(火)最終で 1/5(火)から")[0], {"treatment": "closure", "last": "2026-12-29", "first": "2027-01-05",
                                                            "problems": []})
        mk = y("1/1(金)は休みのため 1/5(火)に特別収集します")[0]
        self.assertEqual((mk["treatment"], mk["removed"], mk["to"], mk["others"]), ("makeup", "2027-01-01", "2027-01-05", None))
        self.assertEqual(y("12月31日のごみ収集はありません")[0]["removed_dates"], ["2026-12-31"])          # 国立
        self.assertEqual(y("注意：1月1日（金）は収集しません")[0]["removed_dates"], ["2027-01-01"])        # 美里
        c = y("12月29日から1月3日の収集はありません")[0]                                                   # 鶴ヶ島
        self.assertEqual((c["last"], c["first"]), ("2026-12-28", "2027-01-04"))
        self.assertEqual(y("年末は12月29日(月)まで")[0]["problems"], ["weekday_mismatch"])                  # 曜日が合わなければ使わない
        two = y("5月3日から5月5日及び12月31日から1月2日の期間、収集はお休みです。")                           # 赤平
        self.assertEqual([(x["off_from"], x["off_to"]) for x in two], [("2026-05-03", "2026-05-05"), ("2026-12-31", "2027-01-02")])
        self.assertEqual(rules.year_end_parts("年末は12月29日まで", None)[0]["problems"], ["no_year"])     # 期間の無い資料

    def test_fifth_week(self):
        self.assertTrue(rules.fifth_week("月の「5番目の曜日」に収集はありません"))                        # 新宿
        self.assertFalse(rules.fifth_week("第5週がある月の水曜日、土曜日の収集は休み"))                     # 赤平 (形が決まらない)

    def test_holiday_negation(self):             # 「祝日も収集」のあとの打ち消しを「収集する」と読まない
        for t in ("祝日も収集しません", "祝日も収集はありません", "祝日でも収集は行いません", "祝日も収集はお休みです"):
            self.assertEqual(rules.holiday_policy(t)["value"], "not_collected", t)
        for t in ("祝日も収集します", "祝日も通常どおり収集します", "祝日も収集します（年末年始は休み）"):
            self.assertEqual(rules.holiday_policy(t)["value"], "collected", t)

    def test_year_end_collected_day_not_skipped(self):
        # 「12/29 は通常どおり収集し、12/30 から 1/3 までは収集しません」の 12/29 を休みと読まない (休みの期間だけ)
        for t, last in (("12月29日（火）は通常どおり収集し、12月30日（水）から1月3日（日）までは収集しません", "2026-12-29"),
                        ("12月30日（水）は通常どおり収集し、12月31日（木）から1月3日（日）までは休みです", "2026-12-30")):
            parts = rules.year_end_parts(t, self.PER)
            self.assertEqual([(p["treatment"], p.get("last"), p.get("first")) for p in parts], [("closure", last, "2027-01-04")], t)
        self.assertEqual(rules.year_end_parts("12月31日(木)、1月1日(金)はお休みです", self.PER)[0]["removed_dates"],
                         ["2026-12-31", "2027-01-01"])
        parts, spans, t = rules.year_end_parts("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", self.PER, with_spans=True)
        self.assertEqual(rules.leftover(t, spans), "")
        parts, spans, t = rules.year_end_parts("年末は12月28日(月曜日)まで ※祝日は休み", self.PER, with_spans=True)
        self.assertTrue(rules.leftover(t, spans))                                                  # 読み残し → 但し書き


class TestCellText(unittest.TestCase):
    def test_day_of_month_is_not_sunday(self):  # 「毎月1日」の「日」を日曜日と読まない
        for n in range(1, 6):
            p = rules.parse(f"毎月{n}日")
            self.assertEqual((p.status, p.irs), ("ok", [{"kind": "monthly_day", "days": [n]}]))
        self.assertEqual(rules.parse("第1日曜日").irs, [{"kind": "monthly_nth", "weekday": "SU", "nth": [1]}])
        self.assertEqual(rules.parse("1・3日").status, "unparsed")
        self.assertEqual(rules.parse("毎月10日・25日").irs, [{"kind": "monthly_day", "days": [10, 25]}])

    def test_rest_is_kept(self):                 # 規則のあとの注は rest に残す (呼ぶ側が使えなければ但し書き)
        self.assertEqual(rules.parse("火曜日・金曜日 ※第5週は休み").rest, "※第5週は休み")
        self.assertEqual(rules.parse("水曜日（月2回）").rest, "(月2回)")                         # 毎週と合わない回数
        p = rules.parse("水曜日（週1回）")                                                        # 曜日の数と合う回数は同じことを言うだけ
        self.assertEqual((p.status, p.rest), ("ok", ""))
        self.assertEqual(rules.parse("月・木（週2回）").rest, "")
        self.assertEqual(rules.parse("月・木（週3回）").rest, "(週3回)")

    def test_inline(self):                       # 能美「第1・第3木曜日 注：松が岡 第2・第4木曜日」
        for t in ("第1・第3木曜日\n注：松が岡\n第2・第4木曜日", "第1・第3木曜日 注：松が岡 第2・第4木曜日"):
            p = rules.parse(t)
            self.assertEqual(p.status, "inline", t)
            self.assertEqual(p.irs, [{"kind": "monthly_nth", "weekday": "TH", "nth": [1, 3]}])
            self.assertEqual(p.inline[0]["names"], ["松が岡"])
            self.assertEqual(p.inline[0]["ir"], {"kind": "monthly_nth", "weekday": "TH", "nth": [2, 4]})
        self.assertNotEqual(rules.parse("第2・4水曜日 注：12月は休み").status, "inline")


if __name__ == "__main__":
    unittest.main()
