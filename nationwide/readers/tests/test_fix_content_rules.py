"""M1 読み取りの危ない方向の不具合 (content.named_months・rules.holiday_policy・規則のマスの注) を直したことの確かめ
(ネットにつながない。LLM を呼ばない)"""
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


def qual(text):
    return {"qualifier_id": "q" + content.sha256_text(text)[:8], "months": sorted(content.named_months(text))}


class TestNamedMonthsWeekday(unittest.TestCase):
    """「N月曜日」の「月」を月と読まない (読むと、ほかの月のその月曜日を答える)"""

    def test_monday_is_not_a_month(self):
        for t in ("※第5月曜日は収集しません", "毎月第2・第4月曜日（祝日は除く）", "第1、第3月曜日は除く", "※第1～3月曜日は休み",
                  "第１、第３月曜日は除く", "第2・4月は休み", "1・3月曜日は休み", "第1と第3月曜日"):
            self.assertEqual(content.named_months(t), set(), t)

    def test_real_months_still_named(self):
        self.assertEqual(content.named_months("8月～10月は休み"), {8, 9, 10})
        self.assertEqual(content.named_months("1月は第2月曜日"), {1})
        self.assertEqual(content.named_months("6月より8月は隔週"), {6, 7, 8})
        # 3 回目 (閉じた見本): 述語の無い月 (「4月・5月は」「4月から9月まで」) と「の第4木曜日」(その月だけの規則かもしれない) は空
        self.assertEqual(content.named_months("4月・5月は"), set())
        self.assertEqual(content.named_months("6・9・12・3月の第4木曜日"), set())
        self.assertEqual(content.named_months("4月から9月まで"), set())
        self.assertEqual(content.named_months("12月29日から1月3日まで"), set())        # 日付は月の名指しに数えない (前と同じ)

    def test_mark_kept(self):                    # 但し書きの印は減らさない (印を減らすと止める方に倒れなくなる)
        self.assertIn(("range", "N月～N月"), content.qualifier_marks("※第1～3月曜日は休み"))

    def test_open_and_wide_months_block_all(self):
        # 終わりの無い月・月をまたぐ但し書き (祝日・第5週) と一緒の月は、その月だけ止めると、ほかの月を答える
        for t in ("ペットボトルは、6月まで隔週収集で、7月から毎週収集になります。", "10月から収集日が変わります", "3月末までは第1水曜日",
                  "(国民の祝日にあたる場合はその直後の平日・8月・1月・2月は除く)", "(8月は除く・第5週は休み)", "8月は祝日も収集します"):
            self.assertEqual(content.named_months(t), set(), t)

    def test_series_blocked(self):
        s = series("第1・3月曜日", [HOL, CLOSE], qualifiers=[qual("※第5月曜日は収集しません")])
        self.assertFalse(s["answerable"])
        self.assertEqual(s["dates"], [])
        s = series("第1・3月曜日", [HOL, CLOSE], qualifiers=[qual("※1月は第2月曜日")])     # 月を名指しした但し書きは、その月だけ不明
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertFalse([d for d in s["dates"] if d.startswith("2027-01")])
        self.assertIn("2026-04-06", s["dates"])


class TestHolidayPolicy(unittest.TestCase):
    """祝日の扱いを、確かでない値で返さない (読み切れなければ None → 使われなかった但し書きとして止まる)"""

    def hp(self, t):
        r = rules.holiday_policy(t)
        return r and (r["value"], r["covers"], (r.get("shift") or {}).get("to"))

    def test_day_after_holiday_is_not_a_move(self):
        self.assertIsNone(rules.holiday_policy("祝日の翌日は収集しません"))
        self.assertIsNone(rules.holiday_policy("祝日の翌日も収集します"))
        self.assertEqual(self.hp("祝日の場合は翌日に収集します"), ("moved", ["national"], "next_day"))
        self.assertEqual(self.hp("祝日は収集を休み、翌日に収集します"), ("moved", ["national"], "next_day"))     # 休みと読んで終わりにしない
        self.assertEqual(self.hp("祝日の場合は翌週の同じ曜日に収集します"), ("moved", ["national"], "next_same_weekday"))

    def test_substitute_said_apart_is_not_covered(self):
        # 3 回目 (閉じた見本): 「が、」の逆接の節は見本の外 → None (使われなかった但し書きとして止める)
        self.assertIsNone(self.hp("祝日も収集しますが、振替休日は収集しません"))
        self.assertIsNone(self.hp("祝日は収集しませんが、振替休日は収集します"))
        self.assertEqual(self.hp("※燃えるごみ・資源ごみ・燃えないごみの収集は、祝日、振替休日も実施します。"),
                         ("collected", ["national", "substitute"], None))
        self.assertEqual(self.hp("祝日・振替休日は収集しません"), ("not_collected", ["national", "substitute"], None))
        self.assertEqual(self.hp("祝日も通常どおり収集します（振替休日含む）"), ("collected", ["national", "substitute"], None))

    def test_substitute_day_not_claimed(self):   # 振替休日 (2027-03-22 月) を収集すると答えない
        self.assertIsNone(rules.holiday_policy("祝日も収集しますが、振替休日は収集しません"))     # 3 回目: 逆接は読まない
        e = dict(HOL, value=self.hp("祝日も収集します")[0], covers=self.hp("祝日も収集します")[1])  # 覆うのは祝日だけ
        s = series("月・木", [e, CLOSE])
        self.assertNotIn("2027-03-22", s["dates"])
        self.assertIn("2027-03-22", [u["from"] for u in s["unknown_ranges"]])

    def test_double_negative(self):
        for t in ("祝日も収集はお休みしません", "祝日でも収集を休むことはありません", "祝日も収集を休止しません"):
            self.assertEqual(self.hp(t), ("collected", ["national"], None), t)

    def test_mixed_is_not_read(self):            # 収集すると言いつつ別の休みを言う文は読まない (休みの部分が落ちる)
        for t in ("祝日も収集しますが、ゴールデンウィークは休みます", "祝日も収集しますが、年末年始と大型連休は休み",
                  "祝日は休みですが、可燃ごみは収集します", "祝日も収集します(ゴールデンウィークを除く)", "祝日も収集しますが、日曜日の祝日は休み"):
            self.assertIsNone(rules.holiday_policy(t), t)

    def test_existing_phrasings_kept(self):
        for t in ("祝日も収集しません", "祝日も収集はありません", "祝日でも収集は行いません", "祝日も収集はお休みです", "（祝日の収集は休み）"):
            self.assertEqual(self.hp(t)[0], "not_collected", t)
        for t in ("祝日も収集します", "祝日も通常どおり収集します", "祝日も収集します（年末年始は休み）",
                  "日曜日、年末年始（12月31日～1月3日まで）を除き、祝日も収集します。"):
            self.assertEqual(self.hp(t)[0], "collected", t)
        hp = rules.holiday_policy("※祝日も収集します（年末年始を除く）")
        self.assertEqual((hp["value"], hp["except_year_end"]), ("collected", True))
        self.assertEqual(rules.holiday_policy("収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。")["covers"], ["all_weekdays"])


class TestCellNote(unittest.TestCase):
    """規則のマスの注の「第N週」を規則と読まない。頭の規則を読み、注は rest に残す (止める)"""

    def test_monthly_head_with_note(self):
        p = rules.parse("第2・4水曜日（第5週は休み）")
        self.assertEqual((p.status, p.irs, p.rest), ("ok", [{"kind": "monthly_nth", "weekday": "WE", "nth": [2, 4]}], "(第5週は休み)"))
        p = rules.parse("第1・3水曜日（祝日の場合は収集なし）")
        self.assertEqual((p.irs, p.rest), ([{"kind": "monthly_nth", "weekday": "WE", "nth": [1, 3]}], "(祝日の場合は収集なし)"))
        self.assertEqual(rules.parse("第1・3・(5)火曜日(祝日は休み)").irs[0]["nth"], [1, 3, 5])

    def test_note_blocks(self):
        for t in ("第2・4水曜日（第5週は休み）", "第1・3水曜日（祝日の場合は収集なし）"):
            s = series(t, [HOL, CLOSE])
            self.assertFalse(s["answerable"], t)
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in s["blocked_by"]), s["blocked_by"])
            # 地区と品目を分けて読む呼ぶ側 (rest_consumed) でも、使えなかった注は月を名指ししない但し書きとして系列を止める
            ex, left = expand.cell_notes(rules.parse(t).rest, FY, {"category_ids": ["c1"], "district_ids": ["d1"]}, {"source_id": "s1"})
            self.assertEqual(ex, [])
            s = series(t, [HOL, CLOSE], rest_consumed=True, qualifiers=[qual(x) for x in left])
            self.assertFalse(s["answerable"], t)

    def test_week_in_note_is_not_a_rule(self):
        for t in ("第5週は休み", "(第5週は休み)", "第5週には収集がない"):
            p = rules.parse(t)
            self.assertNotEqual(p.status, "ambiguous", t)
            self.assertEqual(p.irs, [], t)
        self.assertEqual(rules.parse("びん、かん（第1・3週）").irs, [{"kind": "ambiguous_week", "weeks": [1, 3]}])   # 曜日の無い「第N週」は前と同じ
        self.assertEqual(rules.parse("第2、4週の月2回").status, "ambiguous")

    def test_weekly_head_unchanged(self):
        self.assertEqual(rules.parse("火曜日・金曜日 ※第5週は休み").rest, "※第5週は休み")
        self.assertEqual(rules.parse("水曜日（週1回）").rest, "")
        self.assertEqual(rules.parse("水曜日（月2回）").rest, "(月2回)")


class TestHolidayPolicyMixedClause(unittest.TestCase):
    """2 回目: 「祝日も収集しますが、元日は休みます」を祝日は休み (逆) と読まない。読み切れなければ None"""

    def hp(self, t):
        r = rules.holiday_policy(t)
        return r and (r["value"], r["covers"])

    def test_negation_in_later_clause_is_not_read(self):        # A
        for t in ("祝日も収集しますが、元日は休みます", "祝日も収集しますが、お盆は休みます", "祝日も収集しますが、土曜は休み",
                  "祝日も収集しますが、GWは休み", "祝日も収集しますが、正月は休み", "祝日も収集しますが、台風時は休止",
                  "祝日も収集しますがお盆は休みます", "祝日も収集しますが、びんは休み", "祝日も収集しますが、元日のみ休み",
                  "祝日も収集しますけど、元日は休み", "祝日も収集し、元日は休み", "祝日も収集し元日は休み",
                  "祝日も休みですが、可燃ごみは収集します", "祝日も収集します（正月は休み）"):
            self.assertIsNone(rules.holiday_policy(t), t)

    def test_same_clause_negation_kept(self):
        for t in ("祝日も収集しません", "祝日も収集はありません", "祝日でも収集は行いません", "祝日も収集はお休みです",
                  "祝日も収集を休止します", "祝日でも収集を行わない"):
            self.assertEqual(self.hp(t), ("not_collected", ["national"]), t)
        # 3 回目 (閉じた見本): 後ろに「が」の続く文・逆接の節は見本の外 → None
        for t in ("祝日も収集しませんが", "祝日も収集しますが、振替休日は休み", "祝日も収集しますが、年末年始は休みます"):
            self.assertIsNone(self.hp(t), t)

    def test_cell_note(self):                    # 規則のマスの注でも同じ (使われなかった但し書きとして止める)
        p = rules.parse("月曜日・木曜日（祝日も収集しますが元日は休み）")
        ex, left = expand.cell_notes(p.rest, FY, {"category_ids": ["c1"], "district_ids": ["d1"]}, {"source_id": "s1"})
        self.assertEqual(ex, [])
        self.assertEqual(left, [p.rest])
        s = series("月曜日・木曜日", [HOL, CLOSE], rest_consumed=True, qualifiers=[qual(x) for x in left])
        self.assertFalse(s["answerable"])

    def test_substitute_listed_with_other_period(self):          # C
        for t in ("祝日も収集しますが、振替休日とお盆は休みます", "祝日も収集しますが、振替休日及びゴールデンウィークは休みます",
                  "祝日は収集しませんが、振替休日とゴールデンウィークは収集します", "祝日は収集しません（振替休日とお盆期間を含む）",
                  "祝日も収集しますが振替休日はお盆と同じく休み", "祝日も収集しますが、国民の休日とお盆期間は収集しません",
                  "日曜日、年末年始（お盆を含む）を除き、祝日も収集します", "日曜日、年末年始（12月29日～1月3日、8月13日～15日）を除き、祝日も収集します",
                  "祝日の場合は翌日に収集します（お盆を含む）"):
            self.assertIsNone(rules.holiday_policy(t), t)
        self.assertIsNone(self.hp("祝日は収集しませんが、振替休日は収集します"))                 # 3 回目: 逆接は読まない
        self.assertEqual(self.hp("日曜日、年末年始（12月31日～1月3日まで）を除き、祝日も収集します。"), ("collected", ["national"]))

    def test_all_weekdays_other_side(self):      # C: すべての平日の言い方にも逆の言い方を見る
        for t in ("月曜日～金曜日（祝日を含む）に収集しますが、お盆は休みます", "月曜日から金曜日（祝日含む）収集。ただしお盆は休み",
                  "月曜日～金曜日（祝日を含む）に収集しますが、振替休日は休み"):
            self.assertIsNone(rules.holiday_policy(t), t)
        self.assertEqual(self.hp("月曜日～金曜日（祝日を含む）に収集します。"), ("collected", ["all_weekdays"]))

    def test_hedged_double_negative(self):       # E: 「収集しないことはありません」は収集しないと読まない
        for t in ("祝日でも収集しないことはありません", "祝日でも収集しないわけではありません"):
            self.assertNotEqual((rules.holiday_policy(t) or {}).get("value"), "not_collected", t)
            self.assertIsNone(rules.holiday_policy(t), t)

    def test_page_sentence_blocks(self):         # 本文の文として読んでも、休みの部分を落として答えない
        for t in ("祝日も収集しますが、元日は休みます。", "祝日も収集しますが、振替休日とお盆は休みます。",
                  "月曜日～金曜日（祝日を含む）に収集しますが、お盆は休みます。", "祝日でも収集しないことはありません。"):
            heads = ["ごみの収集日"]
            lines = [content.Line(heads[0], [], "h2", "h2", is_heading=True), content.Line(t, heads, "p", "p")]
            sents = content.sentences(lines)
            excs, used, _ = expand.exceptions_from_sentences(sents, FY, {"c1": {"official_label": "燃やすごみ", "coarse": ["burnable"]}}, "s1")
            self.assertFalse([e for e in excs if e["kind"] == "holiday_policy"], t)
            self.assertFalse(used, t)


class TestNamedMonthsNarrowing(unittest.TestCase):
    """2 回目: 但し書きの月を狭く読まない (その月だけ止めると、ほかの月を答える)"""

    def test_fifth_monday_with_month(self):     # B: 「第5月」を消す前に第5を見る
        for t in ("※第5月曜日は収集しません（8月を除く）", "※第5月曜日は休み、ただし3月は収集します", "※8月以外の第5月曜日は休み",
                  "※第5月曜日は休み（8月は第4月曜日も休み）", "※第2・第4月曜日収集（第5月曜日は8月のみ収集）", "※第1・3・5月曜日は休み（8月）"):
            self.assertEqual(content.named_months(t), set(), t)

    def test_open_or_excepted_months(self):      # D
        for t in ("※9月末日まで隔週収集", "※3月末日までは第1水曜日に収集します", "※6月いっぱい隔週", "※9月いっぱいは隔週",
                  "※9月中までは隔週", "※9月末で終了します", "※4月～翌年3月の間は、プラスチックは隔週収集です",
                  "※令和8年4月～令和9年3月は第1・3水曜日に変更", "※4月〜翌年3月は第1・3水曜日に変更", "※8月以外は第2水曜日です",
                  "※12月・1月以外は収集しません", "※1・2月以外は隔週", "※2月〜3月以外は毎週", "※第３月（8月は除く）"):
            self.assertEqual(content.named_months(t), set(), t)

    def test_closed_months_kept(self):
        self.assertEqual(content.named_months("※6月中は隔週"), {6})
        self.assertEqual(content.named_months("※5月・6月は休み"), {5, 6})
        self.assertEqual(content.named_months("※7月～8月（夏季）は毎週"), {7, 8})
        self.assertEqual(content.named_months("※毎月第１月曜（但し8月は第2月曜）"), set())      # 3 回目: 見本の外 (系列ごと止める)
        self.assertEqual(content.named_months("1月は第2月曜日"), {1})

    def test_series_blocked(self):
        for q in ("※第5月曜日は収集しません（8月を除く）", "※9月末日まで隔週収集", "※令和8年4月～令和9年3月は第1・3水曜日に変更",
                  "※8月以外は第2水曜日です"):
            s = series("毎週月曜日", [HOL, CLOSE], qualifiers=[qual(q)])
            self.assertFalse(s["answerable"], q)
            self.assertEqual(s["dates"], [], q)



class TestHolidayPolicyWhitelist(unittest.TestCase):
    """3 回目: 祝日の扱いは閉じた見本 (rules._HP_TEMPLATES) にまるごと当たる文だけを読む。言い換え・逆接・含み・ほかの主語は None"""

    def hp(self, t):
        r = rules.holiday_policy(t)
        return r and (r["value"], r["covers"], (r.get("shift") or {}).get("to"), r["except_year_end"])

    def test_templates(self):
        for t, want in (("祝日も収集します。", ("collected", ["national"], None, False)),
                        ("祝日・振替休日も通常どおり収集します", ("collected", ["national", "substitute"], None, False)),
                        ("祝日・振替休日・国民の休日も収集します", ("collected", ["national", "substitute", "citizens"], None, False)),
                        ("祝日は収集しません。", ("not_collected", ["national"], None, False)),
                        ("祝日はお休みです", ("not_collected", ["national"], None, False)),
                        ("祝日は収集はお休みです", ("not_collected", ["national"], None, False)),
                        ("祝日の場合は翌日に収集します。", ("moved", ["national"], "next_day", False)),
                        ("祝日も収集はお休みしません。", ("collected", ["national"], None, False)),
                        ("年末年始（12/29～1/3）を除き、祝日も収集します", ("collected", ["national"], None, True)),
                        ("日曜日・年末年始（12月29日～1月3日）を除き、祝日も収集します", ("collected", ["national"], None, True)),
                        ("月曜日～金曜日（祝日を含む）に収集します。", ("collected", ["all_weekdays"], None, False))):
            self.assertEqual(self.hp(t), want, t)

    def test_paraphrases_block(self):
        for t in ("祝日も収集 年末年始休み", "祝日も収集 土曜休み", "祝日も収集/年末年始休み", "祝日も収集→年末年始休み",
                  "祝日も収集 ※年末年始は休み", "祝日も収集 第5週休み", "祝日も収集 びん休み", "祝日も収集で土曜休み",
                  "祝日も収集(土曜休み)", "祝日も収集 ただし年末年始休み", "祝日でも収集 振替休日なし",
                  # 逆接・ほかの品目・ほかの日 (r3)
                  "祝日は収集しませんが、月曜日の祝日は収集", "祝日は休み（月曜祝日は収集）", "祝日は休み（燃やすごみは収集）",
                  "祝日は収集しません（可燃ごみを除く）", "祝日は収集しません（年末年始を除く）", "祝日は収集しません（海の日は収集）",
                  "祝日の収集は休み（5月3日～5日を除く）", "燃やすごみ以外は祝日も収集しません", "祝日は可燃ごみ以外休み",
                  # 含み (r13)
                  "祝日は休みの場合があります。", "祝日も収集する場合があります。", "祝日も収集します（地区により異なります）。",
                  "祝日も原則収集します。", "祝日も収集予定です。", "祝日も収集するかどうかは未定です。", "祝日も収集しますか？",
                  "祝日は収集しません（一部地域を除く）。", "祝日も収集します。※一部除く",
                  # 祝日のほかの主語・ほかの日 (r18・r11)
                  "祝日に関わらず、第5週は収集しません", "祝日でも、雨天時は収集しません", "祝日でも年末年始は収集しません",
                  "祝日も土曜日は収集しません", "祝日を含め、年末年始は収集しません", "祝日でも、1月1日は収集しません",
                  "東地区では祝日も収集しません。", "ざつがみは祝日も収集しません。", "Aコースは祝日も収集します。", "東地区の場合、祝日は収集しません。",
                  "燃やすごみ以外は祝日も収集しません。", "燃やすごみ（剪定枝は除く）は祝日も収集します。", "祝日も実施します。",
                  # 振り替え先の決まらない言い方
                  "祝日の場合は翌週に収集します", "祝日の場合は前日に収集します", "祝日の翌日は収集しません",
                  # 2 つ目の文・逆接
                  "祝日は収集しません。振替休日は収集します", "祝日も収集しますが、元日は休みます"):
            self.assertIsNone(rules.holiday_policy(t), t)

    def test_year_end_exclusion_is_dec_jan_only(self):        # 年末年始を除く言い方は 12 月・1 月の日付だけ (r10)
        for t in ("日曜日、年末年始（12/29～1/3、8/13～8/15）を除き、祝日も収集します", "日曜日、年末年始（12月29日～1月3日、他）を除き、祝日も収集します",
                  "日曜日、年末年始（12月29日～1月3日等）を除き、祝日も収集します", "日曜日、年末年始等を除き、祝日も収集します",
                  "日曜日、年末年始などを除き、祝日も収集します", "日曜日、正月を除き、祝日も収集します", "年末年始と5月3日を除き、祝日も収集します",
                  "年末年始（12/31～1/3、5/3）を除き、祝日も収集します", "年末年始（12/31～1/3）は休み・祝日も収集します",
                  "土日（8/13～8/15含む）を除き、祝日も収集します", "日曜日、年末年始（夏季を含む）を除き、祝日も収集します",
                  "年末年始を除き、祝日は収集しません", "祝日も収集します（年末年始・お盆は休み）"):
            self.assertIsNone(rules.holiday_policy(t), t)

    def test_page_sentence_blocks(self):         # 本文の文として読んでも、見本の外の文は但し書きとして系列を止める (r11)
        for t in ("日曜日、年末年始（12/29～1/3、8/13～8/15）を除き、祝日も収集します。", "東地区では祝日も収集しません。",
                  "祝日も収集 土日休み",
                  "燃やすごみ・陶器ごみは祝日も収集しません。"):    # 品目の主語は見本で読むが、資料に無い品目なら value_scope が使わない
            heads = ["ごみの収集日"]
            lines = [content.Line(heads[0], [], "h2", "h2", is_heading=True), content.Line(t, heads, "p", "p")]
            sents = content.sentences(lines)
            excs, used, _ = expand.exceptions_from_sentences(sents, FY, {"c1": {"official_label": "燃やすごみ", "coarse": ["burnable"]}}, "s1")
            self.assertFalse([e for e in excs if e["kind"] == "holiday_policy"], t)
            quals = [q for q in content.scan_qualifiers(sents) if q["text"] not in {u.text for u in used}]
            s = series("毎週月曜日", [CLOSE], qualifiers=[{"qualifier_id": q["qualifier_id"], "months": q["months"]} for q in quals])
            self.assertFalse(s["answerable"], t)
            self.assertEqual(s["dates"], [], t)


class TestNamedMonthsWhitelist(unittest.TestCase):
    """3 回目: 月は、閉じた月の並び・範囲と決まった述語にまるごと当たる文だけで名指しする。ほかはすべて空 (系列ごと止める)"""

    def test_closed_templates(self):
        for t, want in (("※8月は休み", {8}), ("※10月～3月は休み", {10, 11, 12, 1, 2, 3}), ("※10月から3月まで休み", {10, 11, 12, 1, 2, 3}),
                        ("※5月・6月は収集しません", {5, 6}), ("※7・8月は毎週", {7, 8}), ("※1・2月は隔週収集です。", {1, 2}),
                        ("1月は第2月曜日", {1}), ("※夏季（7・8月）は休み", {7, 8}), ("※年末年始（12/29～1/3）は休み", {12, 1}),
                        ("年末年始は収集日が変わります", {12, 1}), ("※年末及び1月は収集日程、時間が異なります。", {12, 1})):
            self.assertEqual(content.named_months(t), want, t)

    def test_other_wording_blocks(self):
        for t in ("※4・6・8月のみ収集", "※4・6・8月だけ収集します", "※隔月（4・6・8月）", "※5月～10月のみ収集", "※収集は4月・10月です",
                  "※7・8月は毎週収集、その他の月は第1・3水曜日", "※7・8月は毎週、それ以外の月は隔週", "※7・8月は毎週、他の月は隔週",
                  "※7・8月は毎週。通常は隔週", "※8月は休み、その他の月は通常どおり", "※8月は除外", "※8月は対象外", "※8月の他は隔週",
                  "※8月～翌3月", "※8月〜3月", "※10月～翌年3月は休み", "※令和8年10月～令和9年3月", "※8月下旬～9月上旬は休み",
                  "※8月20日～9月10日は休み", "※8月末まで毎週", "※8月中旬から隔週", "※8月の後半は隔週", "※8月初めから隔週", "※8月頭から隔週",
                  "※8月を最後に終了", "※8月をもって終了", "※8月限りで終了", "※8月で終了", "※8月を境に隔週", "※8月開始", "※8月スタート",
                  "※8月分から隔週", "※8月の第1週以降は隔週", "※8・9月以降は隔週", "※8月とそれ以降", "※8月まで", "※8月頃から隔週",
                  "※8月の翌月から隔週", "※8月になったら隔週", "※8月を過ぎると隔週", "※8月末より隔週", "※8月は第2水曜日に変更",
                  "8月は収集日が変わります", "※9月末日まで隔週収集", "※第5月曜日は休み、ただし8月は収集します", "※最終月曜日は休み、8月は収集",
                  "※月末の月曜日は休み、8月は収集", "※第4月曜日は休み、8月は収集", "11月〜2月", "※ペットボトルは5・6月は休み",
                  "※5月の連休は休み", "※奇数月は休み", "※年度末(3月)は休み", "※8月は第5水曜日も収集"):
            self.assertEqual(content.named_months(t), set(), t)

if __name__ == "__main__":
    unittest.main()
