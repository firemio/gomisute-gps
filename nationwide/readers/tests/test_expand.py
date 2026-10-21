"""expand.py: 規則から日付を作る条件 1〜6 (窓 W・closure_month・pending_printed・deferred・caveat・year_end_missing)、
印字と規則の比べ (rule_vs_printed)、方針の文 → 例外 (ネットにつながない)"""
import unittest

import _util  # noqa: F401
import content, expand, rules

FY = {"period_id": "fy2026", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
      "year_evidence": {"ok": True}}
OK = {"readers": [], "status": "agreed", "independence": "code_llm"}
HOL = {"exception_id": "h", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集します"}
CLOSE = {"exception_id": "y", "kind": "year_end", "treatment": "closure", "last": "2026-12-29", "first": "2027-01-04", "others": None,
         "text": "年末は12月29日まで、年始は1月4日から"}


def series(rule, excs, **kw):
    s = {"district_id": "d1", "category_id": "c1", "period": FY, "origin": "expanded", "rule": {"text": rule, "parsed": rules.parse(rule)},
         "exceptions": excs, "provenance": OK}
    s.update(kw)
    return expand.expand_series(s)


def reasons(s):
    return sorted({(u["reason"], u["from"][:7]) for u in s["unknown_ranges"]})


class TestConditions(unittest.TestCase):
    def test_weekly_ok(self):
        s = series("月・木", [HOL, CLOSE])
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertNotIn("2026-12-31", s["dates"])
        self.assertIn("2026-12-28", s["dates"])
        self.assertIn("2027-01-04", s["dates"])
        self.assertEqual(s["rule"]["rrule"], "FREQ=WEEKLY;BYDAY=MO,TH")
        self.assertIn("2027-03-22", [u["from"] for u in s["unknown_ranges"]])        # 振替休日 (月) は「祝日」に入らない
        self.assertEqual(s["gate"], {"unit": "expanded", "open": False})
        self.assertFalse(s["published"])

    def test_blocked_reasons(self):
        self.assertIn("rule_unparsed", series("自己搬入", [HOL, CLOSE])["blocked_by"])
        self.assertIn("rule_ambiguous", series("第1・3週の土曜日", [HOL, CLOSE])["blocked_by"])
        self.assertIn("no_anchor", series("隔週水曜日", [HOL, CLOSE])["blocked_by"])
        self.assertIn("holiday_policy_missing", series("月・木", [CLOSE])["blocked_by"])
        self.assertIn("year_end_missing", series("月・木", [HOL])["blocked_by"])
        self.assertIn("year_end_missing", series("第2・4水", [HOL])["blocked_by"])        # 月ごとの規則でも (窓の外だけを広げない)
        self.assertIn("pending_printed", series("月・木", [HOL, CLOSE], printed_pending=True)["blocked_by"])
        moved = dict(HOL, value="moved")
        self.assertIn("holiday_shift_target_missing", series("月・木", [moved, CLOSE])["blocked_by"])
        und = {"period_id": "u", "valid_from": "2026-10-19", "valid_to": None, "basis": {"text": "-", "kind": "undated"},
               "year_evidence": {"ok": False}}
        self.assertIn("period_unknown", series("月・木", [HOL, CLOSE], period=und)["blocked_by"])
        self.assertIn("version_unproven", series("月・木", [HOL, CLOSE], period=dict(FY, year_evidence={"ok": False}))["blocked_by"])
        s = series("月・木", [HOL, CLOSE], qualifiers=[{"qualifier_id": "qabc", "months": []}], markup_unhandled=["em"])
        self.assertEqual(sorted(s["blocked_by"]), ["markup_unhandled:em", "unconsumed_qualifier:qabc"])
        self.assertEqual(s["dates"], [])
        self.assertIn("single_reading", series("月・木", [HOL, CLOSE], provenance={"readers": [], "status": "single"})["blocked_by"])

    def test_monthly_window_w(self):
        s = series("第2・4水", [HOL, CLOSE])
        self.assertTrue(s["answerable"])
        self.assertIn(("year_end_window", "2026-12"), reasons(s))
        self.assertFalse(any("2026-12-15" <= d <= "2027-01-31" for d in s["dates"]))
        self.assertIn("2027-02-10", s["dates"])
        st = dict(CLOSE, others="unchanged")                    # 文が「ほかの回はずらさない」と言っていれば窓の中も作る
        s = series("第2・4水", [HOL, st])
        self.assertNotIn(("year_end_window", "2026-12"), reasons(s))
        self.assertIn("2027-01-13", s["dates"])
        self.assertNotIn("2026-12-30", s["dates"])
        mk = {"exception_id": "m", "kind": "year_end", "treatment": "makeup", "removed": "2027-01-01", "to": "2027-01-05", "others": None,
              "text": "1/1(金)は休みのため 1/5(火)に特別収集します"}
        self.assertIn(("year_end_window", "2026-12"), reasons(series("第1・3金", [HOL, mk])))     # 羽咋: 推し量らない

    def test_window_widened_by_closure(self):
        early = dict(CLOSE, last="2026-12-10", first="2027-01-04")
        w = expand.year_end_windows("2026-04-01", "2027-03-31", [(early["last"], early["first"])])
        self.assertEqual([(a.isoformat(), b.isoformat()) for a, b in w], [("2026-12-11", "2027-01-31")])

    def test_closure_month_for_monthly(self):
        nc = dict(HOL, value="not_collected")
        s = series("第3月曜日", [nc, CLOSE])                    # 海の日 7/20・敬老の日 9/21 は 3 回目の月曜日
        self.assertIn(("closure_month", "2026-07"), reasons(s))
        self.assertIn(("closure_month", "2026-09"), reasons(s))
        self.assertFalse(any(d.startswith("2026-07") for d in s["dates"]))
        s = series("月曜日", [nc, CLOSE])                        # 毎週の規則は、その日を抜くだけ
        self.assertNotIn("2026-07-20", s["dates"])
        self.assertIn("2026-07-27", s["dates"])
        self.assertNotIn(("closure_month", "2026-07"), reasons(s))
        cn = {"exception_id": "k", "kind": "cancelled", "date": "2026-05-04", "text": "5月4日(月)は点検のため収集は行いません"}
        self.assertIn(("closure_month", "2026-05"), reasons(series("第1月曜日", [HOL, CLOSE, cn])))

    def test_holiday_kinds(self):
        s = series("火・金", [HOL, CLOSE])
        self.assertEqual([(u["from"], u["reason"]) for u in s["unknown_ranges"]], [("2026-09-22", "holiday_kind_not_covered")])
        s = series("火・金", [dict(HOL, covers=["national", "citizens"]), CLOSE])
        self.assertEqual(s["unknown_ranges"], [])
        s = series("火・金", [dict(HOL, covers=["all_weekdays"]), CLOSE])
        self.assertEqual(s["unknown_ranges"], [])

    def test_deferred_and_caveat(self):
        dfr = {"exception_id": "d", "kind": "deferred", "period_name": "年末年始",
               "window": {"from": "2026-12-15", "to": "2027-01-31"}, "text": "年末年始は変更することがあります"}
        s = series("月・木", [HOL, dfr])                         # deferred だけでも (A) は満たし、窓は不明
        self.assertTrue(s["answerable"])
        self.assertIn(("deferred", "2026-12"), reasons(s))
        s = expand.expand_series({"district_id": "d1", "category_id": "c1", "period": FY, "origin": "printed",
                                  "printed_dates": ["2026-12-16", "2027-02-03"], "exceptions": [dfr], "provenance": OK})
        self.assertEqual(s["dates"], ["2027-02-03"])            # 印字より強い
        und = dict(dfr, period_name="お盆", window=None)
        self.assertIn("deferred_undated:お盆", series("月・木", [HOL, CLOSE, und])["blocked_by"])
        cav = {"exception_id": "v", "kind": "caveat", "text": "※ごみの収集日は変更する場合があります。"}
        s = series("月・木", [HOL, CLOSE, cav])
        self.assertTrue(s["answerable"])
        self.assertEqual(s["caveats"], ["v"])
        self.assertIn("year_end_missing", series("月・木", [HOL, cav])["blocked_by"])     # caveat は扱いに数えない

    def test_qualifier_months(self):
        s = series("月・木", [HOL, CLOSE], qualifiers=[{"qualifier_id": "q1", "months": [12, 1]}])
        self.assertTrue(s["answerable"])
        self.assertIn(("unconsumed_qualifier", "2026-12"), reasons(s))
        self.assertFalse(any(d[:7] in ("2026-12", "2027-01") for d in s["dates"]))

    def test_fifth_week(self):
        fw = {"exception_id": "f", "kind": "fifth_week", "value": "none", "text": "5番目の曜日に収集はありません"}
        s = series("第1・3・5水", [HOL, CLOSE, fw])
        self.assertFalse(any(rules.nth_of_month(__import__("datetime").date.fromisoformat(d)) == 5 for d in s["dates"]))
        # 毎週の規則でも第5週の扱いがあれば、月の何回目かを数えるので月ごとの規則 (§2.5 の 4)。窓 W は不明、休みの月は closure_month
        s = series("毎週月曜日", [HOL, CLOSE, fw])
        ref = series("毎週月曜日（5回目の月曜日を除く）", [HOL, CLOSE])
        self.assertEqual(s["dates"], ref["dates"])
        self.assertEqual(s["unknown_ranges"], ref["unknown_ranges"])
        self.assertIn(("year_end_window", "2026-12"), reasons(s))
        self.assertFalse(any("2026-12-15" <= d <= "2027-01-31" for d in s["dates"]))
        # 印字との比べも第5週の扱いを当てた規則で (5 回目の日をいつも違いに出さない)
        ir = {"kind": "weekly", "weekdays": ["MO"]}
        printed = [d.isoformat() for d in expand.base_dates(ir, FY, {"fifth_week": [fw]})]
        mism, q = expand.compare_printed(ir, printed, FY, {"fifth_week": [fw]}, "x")
        self.assertEqual((mism, q), ([], []))

    def test_reading_status_gate(self):          # answerable にしてよいのは agreed・agreed_weak・adjudicated だけ (§2.8)
        for prov, why in ((None, "single_reading"), ({"readers": []}, "single_reading"), ({"status": "both_unsure"}, "disputed:unspecified"),
                          ({"status": "single"}, "single_reading"), ({"status": "disputed"}, "disputed:unspecified")):
            s = {"district_id": "d1", "category_id": "c1", "period": FY, "origin": "expanded",
                 "rule": {"text": "月・木", "parsed": rules.parse("月・木")}, "exceptions": [HOL, CLOSE]}
            if prov is not None:
                s["provenance"] = prov
            r = expand.expand_series(s)
            self.assertFalse(r["answerable"], prov)
            self.assertIn(why, r["blocked_by"], prov)
        for st in ("agreed", "agreed_weak", "adjudicated"):
            self.assertTrue(series("月・木", [HOL, CLOSE], provenance={"status": st})["answerable"], st)

    def test_rest_not_consumed_blocks(self):     # マスの読み残し (「※第5週は休み」「（月2回）」) を黙って使ったことにしない
        for t in ("火曜日・金曜日 ※第5週は休み", "水曜日（月2回）"):
            s = series(t, [HOL, CLOSE])
            self.assertFalse(s["answerable"], t)
            self.assertTrue(any(b.startswith("unconsumed_qualifier:q") for b in s["blocked_by"]), s["blocked_by"])
        self.assertTrue(series("火曜日・金曜日 ※第5週は休み", [HOL, CLOSE], rest_consumed=True)["answerable"])

    def test_cell_notes(self):                   # マスの中の年末年始・祝日の文は、そのマスの地区・品目だけに当てる
        sc = {"category_ids": ["c1"], "district_ids": ["d1"]}
        ex, left = expand.cell_notes("(祝日の収集は休み)", FY, sc, {"source_id": "s1"})
        self.assertEqual([(e["kind"], e["value"], e["scope"]) for e in ex], [("holiday_policy", "not_collected", sc)])
        self.assertEqual(left, [])
        ex, left = expand.cell_notes("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", FY, sc, {"source_id": "s1"})
        self.assertEqual(([(e["last"], e["first"]) for e in ex], left), ([("2026-12-28", "2027-01-04")], []))
        self.assertEqual(expand.cell_notes("※第5週は休み", FY, sc, {"source_id": "s1"}), ([], ["※第5週は休み"]))
        self.assertEqual(expand.cell_notes("(月2回)", FY, sc, {"source_id": "s1"})[1], ["(月2回)"])
        ex, left = expand.cell_notes("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から※祝日は休み", FY, sc, {"source_id": "s1"})
        self.assertEqual(len(left), 1)                                     # 読めたあとに意味のある文字が残れば但し書き


class TestScope(unittest.TestCase):
    CATS = {f"c{i + 1}": {"coarse": __import__("categories").coarse(l), "official_label": l}
            for i, l in enumerate(["燃やすごみ", "燃やさないごみ", "資源", "プラスチック", "古紙", "びん・缶"])}

    def sents(self, t, heading=()):
        return content.sentences([content.Line(t, list(heading), "p", "p")])

    def test_deferred_not_narrowed_by_words(self):   # 足立・「広報紙」・「資源・ごみ」で deferred を狭めない (§2.6 の 4 は節で決める)
        for t in ("※　令和８年年末年始のごみ・資源収集に関する内容は、12月のあだち広報でお知らせします",
                  "年末年始の収集日は広報紙でお知らせします",
                  "年末年始は、資源・ごみの収集日程が変わることがあります",
                  "年末年始の収集日は変更する場合があります。"):
            ex, used, _ = expand.exceptions_from_sentences(self.sents(t, ("ごみの収集日",)), FY, self.CATS, "s1")
            d = [e for e in ex if e["kind"] == "deferred"]
            self.assertTrue(d, t)
            self.assertEqual({str(e["scope"]["category_ids"]) for e in d}, {"*"}, t)

    def test_deferred_section(self):             # 渋谷「（年末年始は変更あり）」は不燃ごみの節
        h = ("ごみ収集日", "地域ごとの収集日（曜日）", "（注）カレンダーの見方 燃やさないごみ「月1回」収集日")
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", self.CATS, h), ["c2"])
        self.assertEqual(expand.block_scope("年末年始は、資源・ごみの収集日程が変わることがあります", self.CATS, h), "*")   # 節の外の品目も言う
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", self.CATS, ("年末年始のごみ・資源の収集",)), "*")
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", self.CATS, ()), "*")

    def test_value_scope(self):
        self.assertEqual(expand.value_scope("燃やすごみは祝日も収集します", self.CATS), ["c1"])
        self.assertEqual(expand.value_scope("ごみは祝日も収集します", self.CATS), "*")
        self.assertEqual(expand.value_scope("資源物は祝日も収集します", self.CATS), ["c3"])          # 公式の名前「資源」が入っている
        no_label = {k: v for k, v in self.CATS.items() if k != "c3"}
        self.assertIs(expand.value_scope("資源物は祝日も収集します", no_label), expand.AMBIGUOUS)     # どの品目か決まらない
        self.assertIs(expand.value_scope("燃やすごみ以外のごみは祝日も収集します", self.CATS), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("祝日も収集します", self.CATS, ("燃やさないごみ", "注意")), ["c2"])   # 近い見出しから上へ
        ex, used, _ = expand.exceptions_from_sentences(self.sents("資源物は祝日も収集します"), FY, no_label, "s1")
        self.assertEqual((ex, used), ([], []))                              # 値に使わない (但し書きとして残る)

    def test_fifth_week_needs_category(self):    # 品目の決まらない「5番目の曜日」は使わない (全部の品目から日を消さない)
        ex, used, _ = expand.exceptions_from_sentences(self.sents("月の「5番目の曜日」に収集はありません。"), FY, self.CATS, "s1")
        self.assertEqual((ex, used), ([], []))
        ex, used, _ = expand.exceptions_from_sentences(
            self.sents("びん・缶の収集日は、その月の2番目と4番目にあたる各曜日です。月の「5番目の曜日」に収集はありません。"), FY, self.CATS, "s1")
        self.assertEqual([(e["kind"], e["scope"]["category_ids"]) for e in ex], [("fifth_week", ["c6"])])


class TestRuleVsPrinted(unittest.TestCase):
    def test_explained_and_queued(self):
        ir = {"kind": "monthly_nth", "weekday": "TH", "nth": [1, 3, 5]}
        printed = [d.isoformat() for d in rules.occurrences(ir, "2026-04-01", "2027-03-31") if d.isoformat() != "2026-12-31"]
        mism, q = expand.compare_printed(ir, printed, FY, {"year_end": [CLOSE]}, "x")
        self.assertEqual([m["month"] for m in mism], ["2026-12"])          # 12/31 は休みの期間の中で説明がつく
        self.assertEqual(q, [])
        printed2 = [x for x in printed if x != "2026-12-03"] + ["2026-12-10"]
        mism, q = expand.compare_printed(ir, sorted(printed2), FY, {"year_end": [CLOSE]}, "x")
        self.assertEqual([x["item_id"] for x in q], ["x:2026-12"])          # 説明のつかない違いは裁定へ
        s = expand.expand_series({"district_id": "d1", "category_id": "c1", "period": FY, "origin": "printed",
                                  "printed_dates": sorted(printed2), "rule": {"text": "第1・3・5木", "parsed": rules.parse("第1・3・5木")},
                                  "exceptions": [CLOSE], "provenance": OK})
        self.assertIn("disputed:d1:c1:2026-12", s["blocked_by"])


class TestSentences(unittest.TestCase):
    def sents(self, *lines, heading=()):
        out = []
        for t in lines:
            out += content.sentences([content.Line(t, list(heading), "p", "p")])
        return out

    def test_office_hours_not_policy(self):      # 塩竈・御蔵島の開庁時間
        ex, used, _ = expand.exceptions_from_sentences(self.sents("閉庁日：土曜日・日曜日・祝日、年末年始（12月29日から1月3日）"), FY, {}, "s1")
        self.assertEqual(ex, [])

    def test_city_closure_pair(self):            # 金沢の市全体「年末 ： 12月29日（火曜日）まで」「年始 ： 1月4日（月曜日）より」
        ex, used, loose = expand.exceptions_from_sentences(
            self.sents("年末 ： 12月29日（火曜日）まで", "年始 ： 1月4日（月曜日）より", heading=("2026年度年末年始のごみ収集について",)), FY, {}, "s1")
        self.assertEqual([(e["kind"], e["last"], e["first"]) for e in ex], [("year_end", "2026-12-29", "2027-01-04")])

    def test_cell_exceptions(self):
        ex, probs = expand.cell_exceptions("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", FY, {"category_ids": ["c1"]},
                                           {"source_id": "s1", "text": "x"})
        self.assertEqual([(e["last"], e["first"]) for e in ex], [("2026-12-28", "2027-01-04")])
        ex, probs = expand.cell_exceptions("年末は12月28日(火曜日)まで", FY, {}, {"source_id": "s1", "text": "x"})
        self.assertEqual((ex, probs), ([], ["weekday_mismatch"]))

    def test_parenthetical_consumed(self):       # 三鷹「祝日も収集します。（年末年始を除く）」
        ss = self.sents("祝日も収集します。（年末年始を除く）")
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, {}, "s1")
        used = expand.consume_parentheticals(ss, used)
        self.assertEqual(len(used), len(ss))


if __name__ == "__main__":
    unittest.main()
