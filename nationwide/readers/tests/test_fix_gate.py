"""使ったことにする関門 (consume.py。default-deny) の直し: 文・かっこ書き・マスの注と読み残し・名前のマス・回数の書き添えを
「使った」と数えるのは、読み取りが値を返し、文の全体が名前のある閉じた見本に当たったときだけ。見本の外は使われなかった但し書きと
して止める (4 回目の直し。3 回目の直しを検証した再現 a1〜a15 が、どれも「止める」か「正しい」になること。ネットにつながない。
LLM を呼ばない)"""
import types, unittest
from unittest import mock

import _util  # noqa: F401
import consume, content, expand, fetch_docs, rules, schema, stage2

FY = {"period_id": "fy2026", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
      "year_evidence": {"ok": True}}
CATS = {"c1": {"official_label": "燃やすごみ", "coarse": ["burnable"]}, "c2": {"official_label": "燃やさないごみ", "coarse": ["non_burnable"]}}
YE = "年末は12月29日まで、年始は1月4日から収集します。"
HEADS = ("ごみの収集日",)


def run(texts, rule="毎週月曜日", cid="c1", heads=HEADS, cats=CATS):
    """本文の文 (1 つずつ別の行) → (系列, 例外, 使われなかった但し書き, 使った文)。stage2 と同じ順 (例外 → かっこ書き → 洗い出し)"""
    heads = list(heads)
    lines = [content.Line(heads[0], [], "h2", "h2", is_heading=True)] + [content.Line(t, heads, "p", "p") for t in texts]
    sents = content.sentences(lines)
    excs, used, _ = expand.exceptions_from_sentences(sents, FY, cats, "s1")
    used = expand.consume_parentheticals(sents, used)
    quals = [q for q in content.scan_qualifiers(sents) if not consume.used(q["sentence"])]      # 文の同一性 (stage2 と同じ)
    mine = [q for q in quals if expand.block_scope(q["text"], cats, q["heading_path"]) == "*" or
            cid in expand.block_scope(q["text"], cats, q["heading_path"])]
    spec = {"district_id": "d1", "category_id": cid, "period": FY, "origin": "expanded",
            "rule": {"text": rule, "parsed": rules.parse(rule)}, "exceptions": excs, "rest_consumed": True,
            "qualifiers": [{"qualifier_id": q["qualifier_id"], "months": q["months"]} for q in mine],
            "markup_unhandled": [], "disputes": [], "provenance": {"status": "agreed"}}
    return expand.expand_series(spec), excs, quals, used


class Case(unittest.TestCase):
    def blocks(self, texts, **kw):
        s, excs, quals, used = run(texts, **kw)
        self.assertFalse(s["answerable"], (texts, s["blocked_by"]))
        self.assertEqual(s["dates"], [], texts)
        return s, excs, quals, used

    def answers(self, texts, has=(), lacks=(), **kw):
        s, excs, quals, used = run(texts, **kw)
        self.assertTrue(s["answerable"], (texts, s["blocked_by"]))
        for d in has:
            self.assertIn(d, s["dates"], (texts, d))
        for d in lacks:
            self.assertNotIn(d, s["dates"], (texts, d))
        return s, excs, quals, used


# ---- 1. 関門は 1 つ: take を「いつも None」にすると、どの道も何も使わない (止める方に倒れる) ----
class TestSingleGate(Case):
    def test_deny_all_sentences(self):
        texts = ["祝日も収集します。（年末年始を除く）", YE, "※大型連休中もごみ収集は通常通り実施します。",
                 "年末年始の収集日は変更する場合があります。"]
        with mock.patch.object(consume, "take", return_value=None):
            s, excs, quals, used = self.blocks(texts)
        self.assertEqual(used, [])
        self.assertEqual({e["kind"] for e in excs}, {"deferred"})       # 止める向きの例外 (deferred・caveat) だけは作る
        self.assertEqual({q["text"] for q in quals}, {"祝日も収集します。", "（年末年始を除く）", YE, "※大型連休中もごみ収集は通常通り実施します。",
                                                    "年末年始の収集日は変更する場合があります。"})

    def test_deny_all_fifth_week(self):
        t = "燃やすごみの収集日は毎月第1・3水曜日です。月の「5番目の曜日」に収集はありません。"
        with mock.patch.object(consume, "take", return_value=None):
            s, excs, quals, used = self.blocks([t, "祝日も収集します。", YE], rule="第1・3・5水曜日", cats={"c1": CATS["c1"]})
        self.assertEqual((excs, used), ([], []))

    def test_deny_all_cells_and_rules(self):
        sc = {"category_ids": ["c1"], "district_ids": ["d1"]}
        with mock.patch.object(consume, "take", return_value=None):
            for piece in ("(祝日の収集は休み)", "(祝日も収集)", "年末は12月28日(月曜日)まで年始は1月4日(月曜日)から"):
                self.assertEqual(expand.cell_notes(piece, FY, sc, {"source_id": "s1"}), ([], [piece]), piece)
            self.assertEqual(rules.parse("月・木（週2回）").rest, "(週2回)")                     # 回数の書き添えも関門を通す
            self.assertEqual(content.named_months("※8月は休み"), set())                       # 月の名指し (その月のほかを使う) も
            self.assertTrue(stage2.label_rest("びん類（無色びん・茶びん）", "category")[1])       # 品目の名前のマス
            self.assertTrue(stage2.district_rest("東町（一部）", stage2.members.parse_cell("東町（一部）"))[1])   # 地区の名前のマス

    def test_deny_all_page(self):                   # stage2 にページを通しても、どの系列も止まる (名前のマス・方針の文・年末年始)
        with mock.patch.object(consume, "take", return_value=None):
            m = PageRun().run("祝日も収集します。")
        self.assertEqual(len(m.schedules), 2)
        for s in m.schedules:
            x = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
            self.assertFalse(x["answerable"])
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in x["blocked_by"]), x["blocked_by"])
        self.assertFalse([e for e in m.exceptions if e["kind"] in ("holiday_policy", "year_end")])

    def test_templates_recorded(self):              # 使った文と例外には見本の名前 (provenance)
        cats = {"c1": CATS["c1"]}
        s, excs, quals, used = self.answers(["祝日も収集します。", YE, "※大型連休中もごみ収集は通常通り実施します。",
                                             "年末年始の収集日は変更する場合があります。"], cats=cats)
        tags = {e["kind"]: e.get("template") for e in excs}
        self.assertEqual(tags, {"holiday_policy": "holiday_policy:collected", "year_end": "year_end:closure_pair",
                                "long_holiday": "long_holiday:long_collected", "deferred": "change:period+may_change"})
        self.assertEqual(sorted(getattr(u, "consumed") for u in used),
                         sorted(["holiday_policy:collected", "year_end:closure_pair", "long_holiday:long_collected", "change:period+may_change"]))
        sc = {"category_ids": ["c1"], "district_ids": ["d1"]}
        ex, left = expand.cell_notes("(祝日の収集は休み)/年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", FY, sc, {"source_id": "s1"})
        self.assertEqual(([e["template"] for e in ex], left), (["cell_holiday:not_collected_of", "cell_year_end:closure_pair"], []))

    def test_registry_closed(self):
        self.assertIsNone(consume.take("holiday_policy", "not_a_template"))
        self.assertIsNone(consume.take("no_such_kind", "collected"))
        self.assertIsNone(consume.take("holiday_policy", None))
        self.assertIsNone(consume.take("holiday_policy", "collected_noverb", "祝日も収集"))      # 動詞の無い言い方は本文の文では使わない
        self.assertIsNone(consume.take("cell_holiday", "collected_noverb", "祝日も収集"))         # マスでも、かっこで包んだ注だけ
        self.assertIsNone(consume.take("cell_holiday", "collected_noverb", ""))
        self.assertEqual(consume.take("cell_holiday", "collected_noverb", "(祝日も収集)"), "cell_holiday:collected_noverb")
        self.assertEqual(schema.EXCEPTION["properties"]["template"], {"type": "string"})


# ---- 1. 品目の決まらない文 (AMBIGUOUS) は、印が無くても但し書き (a10) ----
class TestAmbiguousAlwaysQualifier(Case):
    def test_no_marks_still_block(self):
        for t in ("東地区の収集は12月28日までです。", "東地区は12月28日が最終収集日です。", "山田町は12月28日までです。",
                  "東地区は1月6日から収集します。", "東地区は12月29日（火）は収集しません。", "東地区は8月14日は収集しません。"):
            s, excs, quals, used = self.blocks(["祝日も収集します。", YE, t], rule="毎週火曜日")
            self.assertIn(t, [q["text"] for q in quals], t)
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in s["blocked_by"]), t)

    def test_period_name_without_marks(self):       # GW・祭典の名前は印の言葉に無い: 収集の言葉と一緒なら但し書き
        for t in ("東地区はゴールデンウィーク中も収集します。", "港まつりの日は収集します。"):
            s, excs, quals, used = self.blocks(["祝日は収集しません。", YE, t])
            self.assertIn(t, [q["text"] for q in quals], t)

    def test_scope_ambiguous_marked(self):          # 読み取りは値を返したが、品目が決まらない (地名の見出しの節) → 印をつけて但し書きに
        lines = [content.Line("ごみの収集日", [], "h2", "h2", is_heading=True),
                 content.Line("祝日も収集します。", ["ごみの収集日", "東町"], "p", "p")]
        ss = content.sentences(lines)
        excs, used, _ = expand.exceptions_from_sentences(ss, FY, CATS, "s1")
        self.assertEqual((excs, used), ([], []))
        self.assertEqual(getattr(ss[1], "unconsumed_reading", None), "scope")
        self.assertIn(("hint", "読めたが使えない:scope"), content.qualifier_hints(ss[1]))


# ---- 2. 探す書き方だった読み取りを閉じた見本に ----
class TestLongHoliday(Case):                         # a5
    NEG = ("ゴールデンウィーク中も収集しません。", "ゴールデンウィーク中も収集はお休みです。", "大型連休中も通常どおりの収集とはなりません。",
           "GW期間中も収集は行いません。", "ゴールデンウィーク期間中でも収集はありません。", "大型連休中も収集しないことがあります。")

    def test_negations_not_read(self):
        for t in self.NEG:
            self.assertIsNone(rules.long_holiday(t), t)
            s, excs, quals, used = self.blocks(["祝日も収集します。", t, YE])
            self.assertNotIn("long_holiday", [e["kind"] for e in excs], t)

    def test_collected_kept(self):
        self.assertEqual(rules.long_holiday("※大型連休中もごみ収集は通常通り実施します。"), {"value": "collected", "template": "long_collected"})
        self.assertEqual(rules.long_holiday("大型連休中も通常どおり収集します")["template"], "long_collected_usual")


class TestFifthWeek(Case):                           # a14
    def test_paraphrases_block(self):
        for t in ("燃やすごみは5番目の曜日も休みなく収集します。", "燃やすごみは5番目の曜日の収集はなしとしていましたが、今後は収集します。",
                  "燃やすごみは月の5番目の曜日も休みなしで収集します。", "※第5週は休み", "第5月曜日は収集しません"):
            self.assertIsNone(rules.fifth_week(t), t)
            s, excs, quals, used = self.blocks(["祝日も収集します。", YE, t], rule="毎週水曜日", cats={"c1": CATS["c1"]})
            self.assertNotIn("fifth_week", [e["kind"] for e in excs], t)

    def test_shinjuku_kept(self):
        self.assertEqual(rules.fifth_week("月の「5番目の曜日」に収集はありません。"), {"value": "none", "template": "fifth_none"})


class TestChangeSentences(Case):                     # a12: 含みの文に、確かな文を飲み込ませない
    def test_definite_part_blocks(self):
        for t in ("8月14日は休み、ほかの日も変更になる場合があります。", "8月14日は休みです。ほかの日も変更になる場合があります。",
                  "第5金曜日は収集しません（変更になる場合があります）。", "祝日は収集しませんが、変更になることがあります。"):
            s, excs, quals, used = self.blocks(["祝日も収集します。", YE, t], rule="毎週金曜日")
            self.assertIn("caveat", [e["kind"] for e in excs], t)                         # 注意の例外は作るが
            self.assertFalse([u.text for u in used if not u.parenthetical and ("場合があります" in u.text or "ことがあります" in u.text)],
                             t)                                                     # 含みの文は使わない (止める)
            self.assertNotIn("2026-08-14", s["dates"])

    def test_month_sentence_correct(self):
        s, excs, quals, used = self.answers(["祝日も収集します。", YE, "8月は収集しません。詳しくは広報でお知らせします。"], rule="毎週金曜日",
                                            has=["2026-07-31"], lacks=["2026-08-07", "2026-08-14"])
        self.assertIn(("2026-08-01", "2026-08-31", "unconsumed_qualifier"), [(u["from"], u["to"], u["reason"]) for u in s["unknown_ranges"]])
        self.blocks(["祝日も収集します。", YE, "8月は収集しませんので、広報をご確認ください。"], rule="毎週金曜日")

    def test_change_templates(self):
        for t, want in (("（注）年末年始の収集日は、改めて板橋区ホームページ、区報、集積所看板への張り紙でお知らせいたします。", "period+notice"),
                        ("※ごみの収集日は変更する場合があります。", "none+may_change"), ("年末年始は収集を休んだり、変更することがあります。", "period+may_rest"),
                        ("（年末年始は変更あり）", "period+changed_mark"), ("※収集日は、祝日・お盆休み・年末年始等により、一部変更になることがあります。",
                                                                    "topic_by_period+may_change"),
                        ("※　令和８年年末年始のごみ・資源収集に関する内容は、12月のあだち広報でお知らせします", "period+notice")):
            self.assertEqual(content.change_template(t), want, t)
        for t in ("8月14日は休み、ほかの日も変更になる場合があります。", "ほかの日も変更になる場合があります。", "大型連休中も収集しないことがあります。",
                  "年末年始は休み、詳しくはホームページでお知らせします", "日程が決まり次第、しぶや区ニュース等でお知らせします。",
                  "東地区の収集日は広報でお知らせします", "年末及び1月は収集日程、時間が異なることがあります。"):
            self.assertIsNone(content.change_template(t), t)


class TestYearEndTemplates(Case):                    # a6・a2: 範囲ごとに自分の述語。文の全体を読んだときだけ
    def test_each_range_own_predicate(self):
        for t in ("12月29日～12月31日通常収集、1月1日～1月3日休み", "12月29日（火）～12月31日（木）通常収集、1月1日（金）～1月3日（日）休み"):
            p = rules.year_end_parts(t, FY)
            self.assertEqual([(x["last"], x["first"]) for x in p], [("2026-12-31", "2027-01-04")], t)   # 休みは 1/1〜1/3 だけ
            self.answers(["祝日も収集します。", t], rule="毎週火曜日", has=["2026-12-29", "2027-01-05"])
            self.answers(["祝日も収集します。", t], rule="毎週水曜日", has=["2026-12-30"])
        for t in ("12月30日～12月31日収集、1月1日～1月3日休み", "12月29日～12月31日は通常どおり収集し、1月1日～1月3日は休みです"):
            s, excs, quals, used = run(["祝日も収集します。", t], rule="毎週火曜日")
            self.assertTrue(not s["answerable"] or ("2026-12-29" in s["dates"] and "2027-01-05" in s["dates"]), t)

    def test_whole_text_only(self):
        for t in ("12月29日から1月3日の収集はありません、8月14日も休み", "12月29日から1月3日は休み、ほかの日も休み",
                  "年末は12月29日まで ※祝日は休み", "東地区の収集は12月28日までです"):
            self.assertEqual(rules.year_end_parts(t, FY), [], t)
            self.assertIsNone(rules.year_end_template(t), t)
        parts, spans, t = rules.year_end_parts("年末は12月28日(月曜日)まで ※祝日は休み", FY, with_spans=True)
        self.assertEqual(parts, [])
        self.assertTrue(rules.leftover(t, spans))

    def test_page_html_blocks(self):                 # a2: 表のあるページの文として
        for note in ("祝日も収集します。ゴールデンウィーク中も収集しません。", "祝日も収集します。8月14日は休み、ほかの日も変更になる場合があります。",
                     "祝日も収集します。12月30日～12月31日収集、1月1日～1月3日休み"):
            m = PageRun().run(note)
            for s in m.schedules:
                x = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
                self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in x["blocked_by"]), (note, x["blocked_by"]))


class TestParentheticals(Case):                      # a1・3: 文の外のかっこ書きは、それ自体が見本のときだけ。かっこ書きの品目で範囲を決めない
    def test_not_consumed_block(self):
        for texts in (["祝日も収集します。（お盆は休み）", YE], ["祝日も収集します。（第5月曜日は休み）", YE],
                      ["祝日も収集します。（燃やすごみは休み）", YE], ["祝日も収集します。(東地区は祝日休み)", YE],
                      ["祝日も収集します。（月曜日の祝日は休み）", YE], ["祝日も収集します。（ただし5月は隔週）", YE],
                      ["祝日も収集します（年末年始を除く）。（8月は休み）", YE], [YE + "（8月は休み）"],
                      ["祝日は収集しません。（燃やすごみは収集します）", YE], ["祝日は収集しません。（年末年始を除く）", YE]):
            self.blocks(texts)
        self.blocks(["祝日も収集します。（振替休日は収集しません）", YE], rule="毎週水曜日")

    def test_paren_item_not_scope(self):             # 「祝日は収集しません。（燃やすごみは収集します）」を燃やすごみの休みと読まない (逆の意味)
        s, excs, quals, used = run(["祝日は収集しません。（燃やすごみは収集します）", YE])
        self.assertFalse([e for e in excs if e["kind"] == "holiday_policy"])
        self.assertIs(expand.value_scope("祝日は収集しません。", CATS, HEADS, "祝日は収集しません。（燃やすごみは収集します）"), expand.AMBIGUOUS)

    def test_own_sentence_correct(self):             # かっこ書きが文として読める (「（8月17日は休み）」は休みの日の見本) なら正しく
        self.answers(["祝日も収集します。（8月17日は休み）", YE], has=["2026-07-20"], lacks=["2026-08-17"])

    def test_mitaka_kept(self):                      # 三鷹「祝日も収集します。（年末年始を除く）」は今までどおり
        s, excs, quals, used = self.answers(["祝日も収集します。（年末年始を除く）", YE], has=["2026-07-20"])
        self.assertIn("paren:ye_excluded_after_collected", [getattr(u, "consumed", None) for u in used])
        self.assertEqual(quals, [])


# ---- 4. 年末年始の休みが 2 つあって違えば止める。「ただし」「なお」の主語 (a15・a13) ----
class TestYearEndConflictAndBut(Case):
    def test_conflict_blocks(self):
        s, excs, quals, used = self.blocks(["祝日も収集します。", YE, "年末は12月25日まで、年始は1月8日から収集します。"])
        self.assertIn("year_end_conflict", s["blocked_by"])
        self.assertIn("year_end_conflict", schema.BLOCKED_PLAIN)
        self.answers(["祝日も収集します。", YE, "燃やすごみは、年末は12月29日まで、年始は1月4日から収集します。"],
                     has=["2026-12-28"], lacks=["2026-12-30"])                    # 同じ期間なら止めない

    def test_but_without_subject(self):
        t = "ただし、年末は12月23日まで、年始は1月13日から収集します。"
        self.assertIs(expand.value_scope(t, CATS, HEADS, t), expand.AMBIGUOUS)
        for cid, rule in (("c1", "毎週月曜日"), ("c2", "毎週水曜日")):
            self.blocks(["祝日も収集します。", YE, "燃やさないごみは毎週水曜日です。", t], cid=cid, rule=rule)
        self.blocks(["祝日も収集します。", YE, "なお、祝日は収集しません。"])
        line = "燃やさないごみは毎週水曜日です。" + t                                  # 同じ行に主語があれば、その品目
        self.assertEqual(expand.value_scope(t, CATS, HEADS, line), ["c2"])


# ---- 5. 見出し (いちばん上のほか) は閉じた並びの一般の見出しか品目の見出しだけ (a3・a4) ----
class TestHeadings(Case):
    def test_place_headings_ambiguous(self):
        for h in ("厚田区", "東町", "旧厚田村", "北部", "山田町", "石狩地区", "Aブロック", "4月～9月"):
            for t in ("祝日の収集は休みです。", "（祝日の収集は休み）", "祝日も収集します。", "燃やすごみは祝日も収集します。"):
                self.assertIs(expand.value_scope(t, CATS, ("ごみの収集日", h)), expand.AMBIGUOUS, (h, t))
        self.assertIs(expand.value_scope("祝日も収集します。", CATS, ("ごみの収集日", "厚田区", "燃やすごみ")), expand.AMBIGUOUS)

    def test_generic_headings_kept(self):
        for h, want in (("年末年始の収集", "*"), ("お知らせ", "*"), ("ご注意ください", "*"), ("燃やすごみ", ["c1"]),
                        ("2026年度年末年始のごみ収集について", "*"), ("収集日", "*"),
                        ("収集日程はページ下部の添付ファイルをご覧ください。", "*"), ("収集日確認手順", "*")):   # 案内の見出し (三鷹・板橋)
            self.assertEqual(expand.value_scope("祝日も収集します。", CATS, ("ごみの収集日", h)), want, h)

    def test_page_district_heading_blocks(self):     # a4: 地区の見出しの節の方針の文を、ほかの地区の表に当てない
        body = ("<h2>厚田区</h2><p>祝日の収集は休みです。</p>" + table(["地区", "燃やすごみ"], ["厚田", "毎週月曜日"]) +
                "<h2>浜益区</h2>" + table(["地区", "燃やすごみ"], ["浜益", "毎週月曜日"]))
        m = PageRun().run_body("<p>" + YE + "</p>" + body)
        self.assertEqual(len(m.schedules), 2)
        for s in m.schedules:
            x = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
            self.assertFalse(x["answerable"])
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in x["blocked_by"]), x["blocked_by"])


# ---- 6. 動詞の無い「祝日も収集」と行の折り返し (a8) ----
class TestVerbless(Case):
    def test_line_break_blocks(self):
        for t in ("祝日も収集\nできません。", "祝日も収集\nしないでください。", "祝日も収集\nできない場合があります。", "祝日も収集\nいたしかねます。"):
            s, excs, quals, used = self.blocks([t, YE])
            self.assertFalse([e for e in excs if e["kind"] == "holiday_policy"], t)
            self.assertIn(t.split("\n")[1], [q["text"] for q in quals], t)          # 続きの文も但し書き (印が無くても)

    def test_continuation_after_full_sentence(self):
        s, excs, quals, used = self.blocks(["祝日も収集します\nできない地区もあります。", YE])
        self.assertEqual([u.text for u in used if "祝日" in u.text], [])                 # 続きのある前の文も使わない

    def test_whole_paren_cell_only(self):
        self.assertIsNone(rules.holiday_policy("祝日も収集"))
        self.assertEqual(rules.holiday_policy("祝日も収集", noverb=True)["template"], "collected_noverb")
        sc = {"category_ids": ["c1"], "district_ids": ["d1"]}
        ex, left = expand.cell_notes("(祝日も収集)", FY, sc, {"source_id": "s1"})
        self.assertEqual(([(e["value"], e["template"]) for e in ex], left), ([("collected", "cell_holiday:collected_noverb")], []))
        for piece in ("祝日も収集", "月・木祝日も収集"):
            self.assertEqual(expand.cell_notes(piece, FY, sc, {"source_id": "s1"}), ([], [piece]), piece)


# ---- 8. 止めすぎを戻す (確かに同じことを言っているときだけ) ----
class TestCoverageRecovery(unittest.TestCase):
    def test_frequency_note(self):
        f = lambda t, rule: rules.freq_template(t, rules.parse(rule).ir)
        self.assertEqual(f("週2回", "月曜日・木曜日"), "weekly_count")
        self.assertEqual(f("(週2回)", "水曜日・土曜日"), "weekly_count")
        self.assertIsNone(f("週2回", "月曜日"))
        self.assertIsNone(f("週2回", "第2・4月曜日"))
        self.assertEqual(f("月2回", "2回目・4回目の月曜日"), "monthly_count")
        self.assertIsNone(f("月3回", "第1・3・5水曜日"))                           # 第5 を含む規則は月によって回数が違う
        self.assertIsNone(f("月2回", "毎週月曜日"))
        self.assertEqual(rules.parse("第2・4水曜日（月2回）").rest, "")
        self.assertEqual(rules.parse("水曜日（月2回）").rest, "(月2回)")

    def test_additional_months(self):                 # 毛呂山「5・6・7・8・9・10月のみ…でも出せる」: その月だけ止める
        months, tag = content.named_months_template("※ペットボトルは5・6・7・8・9・10月のみ第1水曜日でも出せる")
        self.assertEqual((months, tag), ({5, 6, 7, 8, 9, 10}, "named_months:additional"))
        for t in ("※4・6・8月のみ収集", "※5月～10月のみ収集", "※ペットボトルは5・6月のみ出せる", "※5・6月のみ第5水曜日でも出せる"):
            self.assertEqual(content.named_months(t), set(), t)

    def test_header_frequency_series(self):          # 世田谷「可燃ごみ（週2回）」: 曜日の数が合う系列だけ使う
        m = PageRun().run_body(table(["地区", "可燃ごみ（週2回）", "不燃ごみ（月2回）"], ["東町", "水曜日・土曜日", "2回目・4回目の月曜日"],
                                     ["西町", "水曜日", "毎週月曜日"]))
        lab = {d["district_id"]: d["label"] for d in m.districts}
        got = {(lab[s["district_id"]], m.categories[s["category_id"]]["official_label"]): [q["qualifier_id"] for q in s["qualifiers"]]
               for s in m.schedules}
        self.assertEqual(got[("東町", "可燃ごみ（週2回）")], [])
        self.assertEqual(got[("東町", "不燃ごみ（月2回）")], [])
        self.assertEqual(got[("西町", "可燃ごみ（週2回）")], [expand.rest_qualifier_id("週2回")])
        self.assertEqual(got[("西町", "不燃ごみ（月2回）")], [expand.rest_qualifier_id("月2回")])


# ---- stage2 にページを通す道具 ----
def table(head, *rows):
    tr = lambda cells, tag: "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
    return "<table>" + tr(head, "th") + "".join(tr(r, "td") for r in rows) + "</table>"


class PageRun:
    @staticmethod
    def html(body):
        return (f"<html><head><title>令和8年度 ごみ収集日</title></head><body><main><h1>令和8年度 ごみの収集日</h1>{body}"
                "</main></body></html>").encode("utf-8")

    def run_body(self, body):
        html = self.html(body)
        old = stage2.body_of, fetch_docs.load_boilerplate
        stage2.body_of = lambda url: (html, {"headers": {"content-type": "text/html; charset=utf-8"}})
        fetch_docs.load_boilerplate = lambda host: None
        try:
            m = stage2.Muni("999999", "テスト市")
            stage2.read_source(m, {"url": "https://example.invalid/p0.html", "file_type": "html", "format_class": "html_table",
                                   "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                               types.SimpleNamespace(llm=False, offline=True), [])
        finally:
            stage2.body_of, fetch_docs.load_boilerplate = old
        return m

    def run(self, note):
        return self.run_body(f"<p>{note}</p><p>{YE}</p>" + table(["地区", "燃やすごみ"], ["東町", "毎週月曜日"], ["西町", "毎週火曜日"]))


# ==== 5 回目の直し: 4 回目の関門を回り道した所 (再現 t1〜t18)。どれも「止める」か「正しい」になること ====
def ex(s):
    return expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))


def series(m):
    lab = {d["district_id"]: d["label"] for d in m.districts}
    return {(lab[s["district_id"]], m.categories[s["category_id"]]["official_label"]): ex(s) for s in m.schedules}


def unconsumed(m):
    return [q["text"] for q in m.sources[-1]["extra"]["qualifiers_unconsumed"]]


T2 = table(["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日・木曜日", "第2水曜日"], ["西町", "火曜日・金曜日", "第4水曜日"])
HOL = "<p>祝日も収集します。</p><p>" + YE + "</p>"


# ---- 1. 表の中の文は、その文の要素が読み切ったマスの中にあるときだけ外す (文字の部分の一致で外さない。t18) ----
class TestCellIdentity(unittest.TestCase):
    def test_short_note_not_dropped_by_substring(self):
        t1 = table(["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日・木曜日（祝日は休み）", "第2水曜日"], ["西町", "月曜日・木曜日", "第4水曜日"])
        t2 = "<table><tr><th>日付</th><th>お知らせ</th></tr><tr><td>8月17日（月）</td><td>休み</td></tr></table>"
        m = PageRun().run_body(HOL + t1 + t2)
        self.assertIn("休み", unconsumed(m))                  # 東町のマスの文字の一部だが、別の表のマスの文
        for k, s in series(m).items():
            self.assertFalse(s["answerable"], k)
            self.assertNotIn("2026-08-17", s["dates"])

    def test_read_cell_sentences_dropped(self):           # 読み切ったマスの中の文 (「（祝日は休み）」のあるマス) は但し書きにしない
        m = PageRun().run_body("<p>祝日は収集しません。</p><p>" + YE + "</p>" +
                               table(["地区", "燃やすごみ"], ["東町", "月曜日・木曜日（祝日は休み）"], ["西町", "火曜日・金曜日"]))
        self.assertEqual(unconsumed(m), [])
        s = series(m)[("東町", "燃やすごみ")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertNotIn("2026-07-20", s["dates"])

    def test_head_label_cells(self):                     # 見出しの行の列の札 (「地区」) は読んだ。札に注が付けば但し書き
        m = PageRun().run_body(HOL + T2)
        self.assertEqual(unconsumed(m), [])
        self.assertTrue(all(s["answerable"] for s in series(m).values()))
        m = PageRun().run_body(HOL + table(["地区（東町は翌日）", "燃やすごみ"], ["東町", "月曜日・木曜日"]))
        self.assertIn("地区（東町は翌日）", unconsumed(m))


# ---- 2. 日付の一覧: 読んだ日付だけを一覧に、並びの後ろは cell_notes と関門へ (t4・t14) ----
class TestListedRest(unittest.TestCase):
    LINE = "燃やさないごみ：4月13日、5月11日、6月8日、7月13日、8月10日"
    KEY = ("テスト市", "燃やさないごみ")

    def run_listed(self, tail, extra=""):
        return PageRun().run_body(f"<p>{YE}</p><p>{self.LINE}{tail}</p>{extra}")

    def test_reader_reads_run_only(self):
        ln = content.Line(self.LINE + " ※7月13日は収集しません", ["令和8年度 ごみの収集日"], "p", "p")
        row = stage2.html_tables.read_text([ln])["rows"][0]
        self.assertEqual(row["listed"]["燃やさないごみ"], [(4, 13), (5, 11), (6, 8), (7, 13), (8, 10)])   # 注の 7月13日 を足さない
        self.assertEqual(row["listed_rest"]["燃やさないごみ"], "※7月13日は収集しません")
        sp = row["listed_spans"]["燃やさないごみ"][0]
        self.assertIs(sp["line"], ln)
        self.assertEqual((sp["start"], sp["end"]), (0, len(ln.text)))
        row = stage2.html_tables.read_text([content.Line("燃やさないごみ：4月13日・27日、5月11日", ["令和8年度"], "p", "p")])["rows"]
        self.assertEqual(row, [])                            # 月の無い日は並びを切る (3 つに足りなければ一覧にしない)

    def test_control_answers(self):
        s = series(self.run_listed(""))[self.KEY]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertEqual(s["dates"], ["2026-04-13", "2026-05-11", "2026-06-08", "2026-07-13", "2026-08-10"])

    def test_note_same_line_blocks(self):
        for tail in (" ※7月13日は収集しません", "（8月は休みです）", " 東町は翌日"):
            s = series(self.run_listed(tail))[self.KEY]
            self.assertFalse(s["answerable"], tail)
            self.assertEqual(s["dates"], [])
        m = self.run_listed(" ※7月13日は収集しません")
        self.assertIn(("cancelled", "2026-07-13", "cell_year_end:days_off"),
                      [(e["kind"], e.get("date"), e.get("template")) for e in m.exceptions])
        self.assertTrue(any(":exception_vs_printed:" in b for b in series(m)[self.KEY]["blocked_by"]))
        m = self.run_listed(" 東町は翌日")
        self.assertIn(self.LINE + " 東町は翌日", unconsumed(m))

    def test_deny_all_keeps_note(self):                  # 関門を「いつも None」にしても、行の注は消えない (t14)
        for tail in (" ※7月13日は収集しません", "（8月は休みです）"):
            with mock.patch.object(consume, "take", return_value=None):
                m = self.run_listed(tail)
            s = series(m)[self.KEY]
            self.assertFalse(s["answerable"], tail)
            self.assertTrue(any(t.startswith(self.LINE) for t in unconsumed(m)), (tail, unconsumed(m)))


# ---- 3. 但し書きから外すのは、その文そのものが使われたときだけ (文字が同じほかの文では外さない。t12) ----
class TestQualifierIdentity(unittest.TestCase):
    BODY = (HOL + table(["地区", "燃やすごみ", "不燃ごみ"], ["東町", "火曜日・金曜日", "月曜日"], ["西町", "火曜日・金曜日", "月曜日"]) +
            "<h2>燃やすごみ</h2><p>祝日は収集しません。</p><h2>西町</h2><p>祝日は収集しません。</p>")

    def test_used_flag(self):
        s = content.sentences([content.Line("祝日も収集します。", [], "p", "p")])[0]
        self.assertFalse(consume.used(s))
        consume.mark(s, "holiday_policy:collected")
        self.assertTrue(consume.used(s))
        s.unconsumed_reading = "scope"
        self.assertFalse(consume.used(s))                    # 使えなかった印のある文は、いつも但し書き

    def test_stage2_same_text_other_section(self):
        m = PageRun().run_body(self.BODY)
        self.assertIn("祝日は収集しません。", unconsumed(m))     # 西町の節の文 (品目の節の同じ文字の文は使った)
        for k, s in series(m).items():
            self.assertFalse(s["answerable"], k)

    def test_build_html_policy_b(self):
        import argparse, build, compare
        from test_fix_build_version import FakeLLM, POL_OK
        m = PageRun().run_body(self.BODY)
        lines = content.content_region(PageRun.html(self.BODY).decode("utf-8")).lines()
        seen, orig = {}, compare.compare_policy

        def cap(a_excs, used, quals, b, per, office_texts=(), sentences=None):
            seen.update(used=list(used), quals=[q["sentence"] for q in quals if q.get("sentence") is not None])
            return orig(a_excs, used, quals, b, per)
        with mock.patch.object(compare, "compare_policy", side_effect=cap):
            build.html_policy_b(m, {"url": "https://example.invalid/p0.html", "title": "収集日"}, "s1", FakeLLM(lambda t: POL_OK),
                                argparse.Namespace(llm=True, offline=False, write=False), {"sources": {}}, lines=lines)
        west = [s for s in seen["quals"] if s.text == "祝日は収集しません。"]
        self.assertEqual([s.heading_path[-1] for s in west], ["西町"])
        self.assertFalse(consume.used(west[0]))
        self.assertEqual(seen["used"].count("祝日は収集しません。"), 1)

    def test_build_pdf_identity(self):
        import argparse, build, fitz, pdf_layout
        from test_fix_build_version import CELLS, FakeLLM, LG, POL_OK, make_pdf
        doc = fitz.open(stream=make_pdf(CELLS), filetype="pdf")
        for y in (230, 280):
            doc[0].insert_text((20, y), "祝日は収集しません", fontname="japan", fontsize=10)
        body = doc.tobytes()
        pdf = pdf_layout.open_pdf(body)
        an = {"pages": [pdf_layout.analyze_page(p) for p in pdf], "doc": pdf, "body": body}
        an["prep"] = build.pdf_prep(an)
        m, ctx, orig, n = stage2.Muni(LG, "テスト町"), {"sources": {}}, expand.exceptions_from_sentences, []

        def second_left(ss, *a, **k):                    # 同じ文字の 2 つ目は使えなかった (地区の節の文など) ことにする
            excs, used, loose = orig(ss, *a, **k)
            for s in [s for s in used if s.text == "祝日は収集しません"]:
                n.append(s)
                if len(n) > 1:
                    del s.consumed
                    consume.left(s, "scope")
            return excs, [s for s in used if consume.used(s)], loose
        x = {"url": "https://example.test/R8_calendar.pdf", "anchors": ["令和8年度 ごみ収集日程表"], "sha256": "x" * 64,
             "last_modified": "Tue, 24 Feb 2026 00:00:00 GMT", "fetched_at": "2026-10-19T03:00:00+09:00"}
        with mock.patch.object(expand, "exceptions_from_sentences", side_effect=second_left):
            build.read_pdf_source(m, x, an, argparse.Namespace(llm=True, offline=False, write=False), FakeLLM(lambda t: POL_OK), [],
                                  ctx, [])
        self.assertEqual(len(n), 2)
        self.assertEqual([q["text"] for q in ctx["sources"]["s1"]["unconsumed"]].count("祝日は収集しません"), 1)


# ---- 4. 規則の後ろを消さない・季節の日を落とさない・足す規則をそれだけで広げない (t5・t6) ----
class TestRuleTails(Case):
    def test_season_day_bounds(self):
        p = rules.parse("4月15日～9月15日は毎週月曜日")
        self.assertEqual(p.ir, {"kind": "seasonal", "from": "04-15", "to": "09-15", "rule": {"kind": "weekly", "weekdays": ["MO"]}})
        ds = [d.isoformat() for d in rules.occurrences(p.ir, "2026-04-01", "2026-10-31")]
        self.assertEqual((ds[0], ds[-1]), ("2026-04-20", "2026-09-14"))
        self.assertEqual(rules.parse("5月～9月は毎週月曜日").ir["from"], "05-01")          # 日の無い言い方は今までどおり
        self.assertEqual(rules.parse("4月31日～9月15日は毎週月曜日").irs, [])             # 月に無い日は読まない
        self.answers(["祝日も収集します。", YE], rule="4月15日～9月15日は毎週月曜日", has=["2026-04-20", "2026-09-14"],
                     lacks=["2026-04-13", "2026-09-21"])

    def test_months_tail_kept(self):
        for t, rest in (("5・6月は第1水曜日でも出せますが、8月は休み", "が8月は休み"), ("5・6月は第1水曜日でも出せます（雨天中止）", "(雨天中止)"),
                        ("5・6月は第1水曜日にも収集しますが東町を除く", "が東町を除く")):
            p = rules.parse(t)
            self.assertEqual(p.rest, rest, t)
            self.assertTrue(rules.is_additive(p.ir), t)
        self.assertFalse(rules.is_additive(rules.parse("5・6月は第1水曜日").ir))

    def test_additive_not_expanded(self):
        s, *_ = self.blocks(["祝日も収集します。", YE], rule="5・6月は第1水曜日でも出せます")
        self.assertIn("rule_unparsed", s["blocked_by"])

    def test_page_cells_block(self):                     # t6: マスの後ろは使われなかった但し書き
        m = PageRun().run_body(HOL + table(["地区", "燃やすごみ", "ペットボトル"], ["東町", "月曜日・木曜日", "5・6月は第1水曜日でも出せますが、8月は休み"],
                                           ["西町", "火曜日・金曜日", "5・6月は第1水曜日でも出せます（雨天中止）"]))
        got = series(m)
        for k in (("東町", "ペットボトル"), ("西町", "ペットボトル")):
            self.assertFalse(got[k]["answerable"])
            self.assertIn("rule_unparsed", got[k]["blocked_by"])
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in got[k]["blocked_by"]))
        m = PageRun().run_body(HOL + table(["地区", "ペットボトル"], ["東町", "4月15日～9月15日は毎週月曜日"]))
        s = series(m)[("東町", "ペットボトル")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertNotIn("2026-04-13", s["dates"])
        self.assertNotIn("2026-09-21", s["dates"])


# ---- 5. 休みの理由は閉じた並び (地名・施設の名前のある理由は読まない。t8・t9) ----
class TestReasonClosed(Case):
    PLACE = ("12月29日～1月3日は東町の道路工事のため収集しません。", "12月29日～1月3日は山田町の工事に伴い収集しません。",
             "12月29日～1月3日は東町につき収集しません。", "12月29日～1月3日は北部の工事により収集しません。",
             "12月31日は東町の祭りのため収集しません。", "12月31日は東町の祭りにより燃やすごみの収集は行いません。",
             "8月17日（月）は東町内の工事に伴い、燃やすごみの収集は行いません。")

    def test_place_reason_not_read(self):
        for t in self.PLACE:
            self.assertIsNone(rules.year_end_template(t), t)
            self.assertEqual(rules.year_end_parts(t, FY), [], t)
            s, excs, quals, used = self.blocks(["祝日も収集します。", YE, t], rule="毎週火曜日")
            self.assertIn(t, [q["text"] for q in quals], t)
            self.assertNotIn("2026-12-29", s["dates"])

    def test_generic_reason_kept(self):
        for t in ("※注意 5月4日(月)、12月31日(木)は、施設の定期点検のため可燃ごみの収集は行いません。",    # 日高
                  "12月31日は清掃工場の定期点検のため収集しません。", "12月31日は年末年始につき収集しません。", "8月14日は祭典のため収集しません。"):
            self.assertEqual(rules.year_end_template(t), "days_off", t)
        self.assertEqual(expand.value_scope("12月31日は施設の点検のため燃やすごみの収集は行いません。", CATS, HEADS), ["c1"])
        self.assertIs(expand.value_scope("12月31日は東町の点検のため燃やすごみの収集は行いません。", CATS, HEADS), expand.AMBIGUOUS)

    def test_page_blocks(self):                          # t9
        for note in ("8月17日は東町の道路工事のため収集しません。", "8月17日（月）は東町内の工事に伴い、燃やすごみの収集は行いません。"):
            m = PageRun().run_body(HOL + f"<p>{note}</p>" + T2)
            self.assertIn(note, unconsumed(m))
            for k, s in series(m).items():
                self.assertFalse(s["answerable"], (note, k))


# ---- 6. 日程の節の、印の無い文も但し書き (地区・場所・曜日・前後の日・日付・続きの頭)。続きは同じ塊の次の行も見る (t1・t16) ----
class TestUnmarkedSentences(unittest.TestCase):
    NOTES = ("<p>祝日も収集します。</p><p>ただし、東町は翌日に収集します。</p>", "<p>祝日も収集します。ただし、東町は翌日に収集します。</p>",
             "<p>祝日も収集します</p><p>が、東町は翌日になります。</p>", "<ul><li>祝日も収集します。</li><li>東町は、その翌日に収集します。</li></ul>",
             "<p>祝日も収集します。</p><p>東町の燃やすごみは火曜日と金曜日です。</p>")

    def test_page_blocks(self):
        for note in self.NOTES:
            m = PageRun().run_body(note + f"<p>{YE}</p>" + T2)
            for k, s in series(m).items():
                self.assertFalse(s["answerable"], (note, k))
                self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in s["blocked_by"]), (note, k))

    def test_hints(self):
        heads = ["ごみの収集日"]
        lines = [content.Line(t, heads, "p", "p") for t in ("東町は翌日に収集します。", "燃やすごみは火曜日です。", "その日は出せます。",
                                                           "8月17日に出してください。", "が、東町は翌日になります。", "北部は別の日です。",
                                                           "収集は終了しました。", "収集を再開します。", "収集は翌月からです。")]   # 収集の動き (6 回目)
        for s in content.sentences(lines):
            self.assertTrue(content.qualifier_hints(s), s.text)
        quiet = [content.Line("東町", heads, "h3", "h3", is_heading=True),                  # 見出し
                 content.Line("窓口は月曜日から金曜日の8時30分からです。", heads, "p", "p"),      # 開庁時間の文
                 content.Line("詳しくは東町の会館でお聞きください。", ["お問い合わせ"], "p", "p"),  # 日程の節の外 (ごみの言葉も無い)
                 content.Line("ごみは収集日の朝8時30分までに出してください。", heads, "p", "p"),  # 出し方の指図 (収集の動きでない)
                 content.Line("令和8年度 地区別ごみ収集日程表", heads, "p", "p"),                 # 地区ごとの一覧の題
                 content.Line("取扱店の一覧は1月1日現在の情報です。", heads, "p", "p")]          # 日程でない日付
        for s in content.sentences(quiet):
            self.assertEqual(content.qualifier_hints(s), [], s.text)
        s = content.sentences([content.Line("世田谷区のごみの出し方です。", heads, "p", "p")])[0]
        self.assertEqual(content.scan_qualifiers([s], own_names=("世田谷区",)), [])               # 自治体そのものの名前は地区でない
        self.assertTrue(content.scan_qualifiers([s]))

    def test_continued_across_blocks(self):
        def first_holiday(html, cats=CATS):
            ss = content.sentences(content.content_region(html).lines())
            excs, used, _ = expand.exceptions_from_sentences(ss, FY, cats, "s1")
            return [e for e in excs if e["kind"] == "holiday_policy"], next(s for s in ss if s.text.startswith("祝日も収集します"))
        wrap = "<html><body><main><h1>ごみの収集日</h1>{}</main></body></html>"
        for body in ("<p>祝日も収集します</p><p>が、東町は翌日になります。</p>", "<ul><li>祝日も収集します</li><li>けれど東町は翌日です。</li></ul>",
                     "<p>祝日も収集します</p><p>できない地区もあります。</p>"):
            hp, s = first_holiday(wrap.format(body))
            self.assertEqual((hp, getattr(s, "unconsumed_reading", None)), ([], "continued"), body)
        for body in ("<p>祝日も収集します。</p><p>なお、燃やすごみは月曜日です。</p>", "<div><p>祝日も収集します</p></div><div><p>が、</p></div>"):
            hp, s = first_holiday(wrap.format(body))
            self.assertEqual(len(hp), 1, body)                # 「。」で終わる文・別の塊の行は続きにしない (次の文は但し書きとして別に止める)
        import build
        a, b = build.PdfLine("祝日も収集します", [], "p", 0, (0, 0, 1, 1)), build.PdfLine("が、東町は翌日です。", [], "p", 0, (0, 2, 1, 3))
        a.owner = b.owner = None
        ss = content.sentences([a, b])
        self.assertTrue(expand._continued(ss[0], ss))      # PDF の見た目の行
        b.owner = 0
        self.assertFalse(expand._continued(ss[0], ss))     # 別の表の注

    def test_compare_by_span(self):                     # t16: B の但し書きは、A の文で読み切れていなければ B だけのもの
        import compare
        a_excs = [{"kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集します",
                   "scope": {"category_ids": "*"}}]
        used = ["祝日も収集します", "年末は12月29日まで収集します。"]
        for q in ("祝日も収集しますが、山間部は翌日になります。", "祝日も収集します（東町は祝日の翌日）", "年末は12月29日まで収集します。ただし東町は12月30日まで"):
            b = {"holiday_policy": {"value": "collected"}, "qualifiers": [{"span": q}], "deferred": [], "caveats": []}
            self.assertEqual(compare.compare_policy(a_excs, used, [], b, FY)["qualifiers_b_only"], [{"span": q}], q)
        for q in ("祝日も収集します", "年末は12月29日まで収集します。祝日も収集します。", "東町は翌日"):
            b = {"holiday_policy": {"value": "collected"}, "qualifiers": [{"span": q}], "deferred": [], "caveats": []}
            got = compare.compare_policy(a_excs, used, [{"text": "ただし、東町は翌日です。"}], b, FY)["qualifiers_b_only"]
            self.assertEqual(got, [], q)                     # A の文 (使った文・A の但し書き) で読み切れている


# ---- 7. 大型連休と祝日の扱いがぶつかれば止める。印字・一覧の日付と休みの例外がぶつかれば止める (t15・t4) ----
class TestLongHolidayAndPrinted(Case):
    def test_long_holiday_conflict(self):
        for hp in ("祝日は収集しません。", "祝日の場合は翌日に収集します。"):
            s, *_ = self.blocks([hp, YE, "大型連休中も収集します。"])
            self.assertIn("holiday_policy_conflict", s["blocked_by"])
        self.assertIn("holiday_policy_conflict", schema.BLOCKED_PLAIN)
        self.answers(["祝日も収集します。", YE, "大型連休中も収集します。"], has=["2026-05-04"])      # 同じことを言うなら答える

    def test_printed_exception_conflict(self):
        spec = {"district_id": "d1", "category_id": "c1", "period": FY, "origin": "listed",
                "printed_dates": ["2026-07-06", "2026-07-13", "2026-12-31"],
                "exceptions": [{"exception_id": "e1", "kind": "cancelled", "date": "2026-07-13", "scope": {"category_ids": "*"}},
                               {"exception_id": "e2", "kind": "year_end", "treatment": "closure", "last": "2026-12-29",
                                "first": "2027-01-04", "scope": {"category_ids": "*"}}],
                "qualifiers": [], "markup_unhandled": [], "disputes": [], "provenance": {"status": "agreed"}}
        s = expand.expand_series(spec)
        self.assertFalse(s["answerable"])
        self.assertEqual({x["exception_id"]: x["dates"] for x in s["extra"]["exception_vs_printed"]},
                         {"e1": ["2026-07-13"], "e2": ["2026-12-31"]})
        self.assertIn("disputed:d1:c1:exception_vs_printed:e1", s["blocked_by"])
        self.assertNotIn("extra", spec)                      # 材料は書き換えない
        s = expand.expand_series(dict(spec, printed_dates=["2026-07-06", "2026-07-20", "2027-01-04"]))
        self.assertTrue(s["answerable"], s["blocked_by"])

    def test_page_note_separate(self):                   # t4 l3: 別の段落の休みの文も、一覧の日付と比べる
        m = PageRun().run_body(f"<p>{YE}</p><p>{TestListedRest.LINE}</p><p>※7月13日は収集しません</p>")
        s = series(m)[TestListedRest.KEY]
        self.assertFalse(s["answerable"])
        self.assertTrue(any(":exception_vs_printed:" in b for b in s["blocked_by"]), s["blocked_by"])


# ---- 8. 別の文の「年末は…まで」と「年始は…から」は、前後が逆でなく間が 40 日までのときだけ組にする (t10b) ----
class TestYearEndPairGuard(Case):
    def test_far_pair_not_made(self):
        for texts in (["祝日も収集します。", "年末は12月29日まで収集します。", "年始は3月1日から収集します。"],
                      ["祝日も収集します。", "年末は1月5日まで収集します。", "年始は12月29日から収集します。"]):
            s, excs, quals, used = self.blocks(texts)
            self.assertNotIn("year_end", [e["kind"] for e in excs], texts)
            self.assertIn("year_end_missing", s["blocked_by"])
            for t in texts[1:]:
                self.assertIn(t, [q["text"] for q in quals], t)

    def test_near_pair_kept(self):
        self.answers(["祝日も収集します。", "年末は12月29日まで収集します。", "年始は1月4日から収集します。"], has=["2026-12-28", "2027-01-04"])
        self.assertTrue(expand.closure_ok("2026-12-29", "2027-01-04"))
        self.assertFalse(expand.closure_ok("2026-12-29", "2027-03-01"))
        self.assertFalse(expand.closure_ok("2027-01-05", "2026-12-29"))


# ==== 6 回目の直し: 5 回目の確かめの穴 (再現 F1a〜F8・v1〜v16)。どれも「止める」か「正しい」になること ====
def note_row(html):
    return T2.replace("</table>", html + "</table>")


def all_blocked(test, m, why=""):
    got = series(m)
    test.assertTrue(got, why)
    for k, s in got.items():
        test.assertFalse(s["answerable"], (why, k, s["blocked_by"]))
        test.assertEqual(s["dates"], [], (why, k))
    return got


def run_year(body, year):
    """題の年度を変えて stage2 に通す (令和9年度 = 2027 年度。うるう年の 2028 年 2 月を含む)"""
    html = PageRun.html(body).decode("utf-8").replace("令和8年度", year).encode("utf-8")
    old = stage2.body_of, fetch_docs.load_boilerplate
    stage2.body_of = lambda url: (html, {"headers": {"content-type": "text/html; charset=utf-8"}})
    fetch_docs.load_boilerplate = lambda host: None
    try:
        m = stage2.Muni("999999", "テスト市")
        stage2.read_source(m, {"url": "https://example.invalid/p0.html", "file_type": "html", "format_class": "html_table",
                               "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                           types.SimpleNamespace(llm=False, offline=True), [])
    finally:
        stage2.body_of, fetch_docs.load_boilerplate = old
    return m


# ---- 1. 表の中の全幅の注の行は地区の行にしない: 表の系列すべての注として関門に通し、読めなければ止める (F1a・F1b・F1c・v1・v5) ----
class TestTableNoteRows(unittest.TestCase):
    def test_note_rows_block_whole_table(self):          # F1a・F1b・a8・a9・a10・a12
        for row in ("<tr><td colspan=3>※第5週は収集しません</td></tr>", "<tr><td colspan=3>※東町は祝日の翌日に収集します</td></tr>",
                    "<tr><td colspan=2>※東町は祝日の翌日に収集します</td><td></td></tr>",
                    "<tr><td>※</td><td colspan=2>東町は祝日の翌日に収集します</td></tr>",
                    "<tr><td colspan=3>【注意】祝日は収集しません</td></tr>", "<tr><td colspan=3>東町は翌日です</td></tr>"):
            m = PageRun().run_body(HOL + note_row(row))
            got = all_blocked(self, m, row)
            self.assertEqual({k[0] for k in got}, {"東町", "西町"}, row)          # 注の行を地区にしない
            self.assertTrue(unconsumed(m), row)                                   # 但し書きに届く (印が無くても)
            for s in got.values():
                self.assertTrue(any(b.startswith("unconsumed_qualifier:") or b == "holiday_policy_missing" for b in s["blocked_by"]), row)

    def test_month_note_blocks_that_month(self):         # F1c: 月を名指しした注は、その月だけ止める (表の系列すべて)
        m = PageRun().run_body(HOL + note_row("<tr><td colspan=3>8月は収集しません</td></tr>"))
        got = series(m)
        self.assertEqual({k[0] for k in got}, {"東町", "西町"})
        for k, s in got.items():
            self.assertFalse(any(d.startswith("2026-08") for d in s["dates"]), k)
            self.assertIn(("2026-08-01", "2026-08-31"), [(u["from"], u["to"]) for u in s["unknown_ranges"]], k)

    def test_note_read_through_gate_with_table_scope(self):     # a5・a6: 読める注は、その表の系列だけに当てる (ページの方針にしない)
        row = "<tr><td colspan=3>祝日は収集しません</td></tr>"
        got = series(PageRun().run_body(f"<p>{YE}</p>" + note_row(row)))
        self.assertEqual({k[0] for k in got}, {"東町", "西町"})
        s = got[("東町", "燃やすごみ")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertNotIn("2026-07-20", s["dates"])                              # 海の日 (月曜日) は休み
        all_blocked(self, PageRun().run_body(HOL + note_row(row)), "page collected vs table not_collected")

    def test_reader_notes(self):
        texts = [["地区", "燃やすごみ"], ["東町", "月曜日"], ["※注意", "祝日は休み"], ["", "火曜日"], ["西町", "水曜日"], ["※", "木曜日"]]
        rd = stage2.html_tables.read_grid(texts, 1)
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["東町", "西町"])
        self.assertEqual([n["text"] for n in rd["notes"]], ["※注意\n祝日は休み", "火曜日", "※\n木曜日"])
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ", "不燃ごみ"], ["※第5週は休み"] * 3, ["東町", "月曜日", "水曜日"]], 1)
        self.assertEqual([n["text"] for n in rd["notes"]], ["※第5週は休み"])      # CSV: 札と値が同じ文字の 1 つ
        m = PageRun().run_body(HOL + table(["地区", "燃やすごみ"], ["東町", "毎週月曜日"], ["", "毎週火曜日"]))
        all_blocked(self, m, "district-less row")


# ---- 2. 品目の名前の読み残しは、もとの品目に寄せて資料の中のその品目すべてを止める (F2a・F2b・F2c・v9・v9b・v12) ----
class TestLabelLeftovers(unittest.TestCase):
    def test_base(self):
        for label, base in (("燃やせるごみ（祝日は休み）", "燃やせるごみ"), ("燃やすごみ（東町は火曜日・金曜日）", "燃やすごみ"),
                            ("東町の燃やすごみ", "燃やすごみ"), ("燃やすごみ(東町を除く)", "燃やすごみ"), ("※燃やすごみ", "燃やすごみ")):
            b, pieces = stage2.label_base(label, "text")
            self.assertEqual(b, base, label)
            self.assertTrue(pieces, label)
        self.assertEqual(stage2.label_base("燃やすごみ", "text"), ("燃やすごみ", []))

    def test_merged_and_blocked(self):
        P = HOL
        for body in (table(["曜日", "ごみの種類"], ["火曜日", "燃やせるごみ"], ["金曜日", "燃やせるごみ（祝日は休み）"]),       # F2a
                     table(["曜日", "ごみの種類"], ["火曜日", "燃やせるごみ"], ["金曜日", "燃やせるごみ（東町のみ）"]),           # g2
                     table(["品目", "収集曜日"], ["燃やすごみ", "毎週月曜日・木曜日"], ["燃やすごみ（東町は火曜日・金曜日）", "毎週月曜日・木曜日"]),
                     table(["品目", "収集曜日"], ["燃やすごみ", "毎週火曜日"], ["燃やすごみ（8月のみ）", "毎週金曜日"]),           # g3
                     "<p>燃やすごみ：毎週月曜日・木曜日</p><p>東町の燃やすごみ：毎週火曜日・金曜日</p>",                       # F2c
                     "<p>燃やすごみ：毎週月曜日・木曜日</p><p>燃やすごみ（東町）：毎週火曜日・金曜日</p>",
                     "<p>燃やすごみ：毎週月曜日・木曜日</p><p>北部の燃やすごみ：毎週火曜日・金曜日</p>",
                     table(["品目", "収集曜日"], ["燃やすごみ（東町は火曜日・金曜日）", "毎週月曜日・木曜日"])):                # e7
            got = series(PageRun().run_body(P + body))
            burn = [k for k in got if k[1].startswith(("燃やす", "燃やせる"))]
            self.assertEqual(len(burn), 1, (body, list(got)))                     # 別の品目にしない
            self.assertIn(burn[0][1], ("燃やすごみ", "燃やせるごみ"), body)
            s = got[burn[0]]
            self.assertFalse(s["answerable"], body)
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in s["blocked_by"]), body)

    def test_whole_source(self):                         # 名前に地区が入れば、資料の中のほかの読み (表) の同じ品目も止める
        m = PageRun().run_body(HOL + "<p>東町の燃やすごみ：毎週火曜日・金曜日</p>" + T2)
        got = series(m)
        for k in (("東町", "燃やすごみ"), ("西町", "燃やすごみ")):
            self.assertFalse(got[k]["answerable"], k)
        self.assertTrue(got[("東町", "不燃ごみ")]["answerable"], got[("東町", "不燃ごみ")]["blocked_by"])

    def test_freq_note_kept(self):                       # 回数の書き添えだけ (ほかに同じ品目が無い) は今までどおり
        got = series(PageRun().run_body(HOL + table(["地区", "可燃ごみ（週2回）"], ["東町", "月曜日・木曜日"])))
        self.assertTrue(got[("東町", "可燃ごみ（週2回）")]["answerable"])


# ---- 3. 曜日の表の曜日のマスが曜日だけでない行を読み飛ばさない (v15・v16) ----
class TestWeekdayCells(unittest.TestCase):
    def test_reader(self):
        rd = stage2.html_tables.read_grid([["曜日", "ごみの種類"], ["第2月曜日", "不燃ごみ"], ["木曜日", "燃やすごみ"]], 1)
        p = rd["rows"][0]["rules"]["不燃ごみ"]
        self.assertEqual((p.status, p.ir), ("ok", {"kind": "monthly_nth", "weekday": "MO", "nth": [2]}))
        for wd in ("月・第2", "第2・4\n不燃ごみ"):
            rd = stage2.html_tables.read_grid([["曜日", "ごみの種類"], [wd if "\n" not in wd else "第1月曜日", "第3\n不燃ごみ" if "\n" in wd else "不燃ごみ"]], 1)
            self.assertNotEqual(rd["rows"][0]["rules"]["不燃ごみ"].status, "ok", wd)

    def test_not_skipped(self):
        for h in ("<h2>ごみの収集日</h2>", "<h2>東町の曜日表</h2>"):
            for wd in ("月曜日（第2週）", "月曜日 第2週", "月曜日※", "月・第2", "月曜日<br>(第2週)", ""):
                m = PageRun().run_body(h + HOL + table(["曜日", "ごみの種類"], [wd, "燃やすごみ"], ["木曜日", "燃やすごみ"]))
                s = series(m)[("テスト市", "燃やすごみ")]
                self.assertFalse(s["answerable"], (h, wd, s["blocked_by"]))
        got = series(PageRun().run_body(HOL + table(["曜日", "ごみの種類"], ["第2月曜日", "不燃ごみ"], ["木曜日", "燃やすごみ"])))
        s = got[("テスト市", "不燃ごみ")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertIn("2026-07-13", s["dates"])                                  # 7 月の第 2 月曜日
        self.assertNotIn("2026-07-06", s["dates"])


# ---- 4. 一覧の後ろの祝日の注は使わない。ページの「祝日は収集しません」と一覧の祝日の日付がぶつかれば止める (F3a・F3b・v13) ----
class TestListedHoliday(unittest.TestCase):
    LIST = "<p>燃やさないごみ：7月20日、8月17日、9月21日{}</p>"
    KEY = ("テスト市", "燃やさないごみ")

    def test_rest_holiday_not_consumed(self):            # F3a
        m = PageRun().run_body(HOL + "<p>燃やすごみ：毎週月曜日・木曜日</p>" + self.LIST.format("（祝日は休み）"))
        s = series(m)[self.KEY]
        self.assertFalse(s["answerable"])
        self.assertNotIn("cell_holiday", str([e.get("template") for e in m.exceptions]))
        _, left = expand.cell_notes("（祝日は休み）", FY, {"category_ids": ["c1"]}, {}, holiday=False)
        self.assertEqual(left, ["（祝日は休み）"])

    def test_page_policy_vs_listed(self):                # F3b・h2
        for pol in ("祝日は収集しません。", "祝日の場合は翌日に収集します。"):
            m = PageRun().run_body(f"<p>{pol}</p><p>{YE}</p><p>燃やすごみ：毎週月曜日・木曜日</p>" + self.LIST.format(""))
            s = series(m)[self.KEY]
            self.assertFalse(s["answerable"], pol)
            self.assertTrue(any(":exception_vs_printed:" in b for b in s["blocked_by"]), (pol, s["blocked_by"]))
        s = series(PageRun().run_body(HOL + "<p>燃やすごみ：毎週月曜日・木曜日</p>" + self.LIST.format("")))[self.KEY]
        self.assertTrue(s["answerable"], s["blocked_by"])                       # 祝日も収集する資料なら答える


# ---- 5. 季節の枠の 2 月の終わりは 29 日まで (F4・v2) ----
class TestSeasonLeap(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(rules.parse("12月～2月は毎週火曜日").ir["to"], "02-29")
        self.assertEqual(rules.parse("12月1日～2月29日は毎週火曜日").ir["to"], "02-29")
        self.assertEqual(rules.parse("12月～2月30日は毎週火曜日").irs, [])
        ds = [d.isoformat() for d in rules.occurrences(rules.parse("12月～2月は毎週火曜日").ir, "2028-02-01", "2028-03-31")]
        self.assertEqual(ds[-1], "2028-02-29")
        ds = [d.isoformat() for d in rules.occurrences(rules.parse("12月～2月は毎週火曜日").ir, "2027-02-01", "2027-03-31")]
        self.assertEqual(ds[-1], "2027-02-23")

    def test_series(self):
        m = run_year(HOL + table(["地区", "燃やすごみ", "ペットボトル"], ["東町", "月曜日・木曜日", "12月～2月は毎週火曜日"]), "令和9年度")
        s = series(m)[("東町", "ペットボトル")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertIn("2028-02-29", s["dates"])


# ---- 6・7. 一覧: 別の節の違う一覧を足し合わせない。かっこの曜日が年と合わなければ止める。規則の文があれば比べる (F5・F6・v3・v11・v14) ----
class TestListedChecks(unittest.TestCase):
    KEY = ("テスト市", "燃やさないごみ")
    P = HOL + "<p>燃やすごみ：毎週月曜日・木曜日</p>"

    def test_sections_not_unioned(self):                 # F5
        for a, b in (("旧日程", "新日程"), ("収集日一覧", "収集日一覧（追加分）"), ("上半期", "下半期")):   # 期間の見出しは地区にしない
            m = PageRun().run_body(self.P + f"<h2>{a}</h2><p>燃やさないごみ：4月22日、5月20日、6月10日</p>"
                                            f"<h2>{b}</h2><p>燃やさないごみ：4月21日、5月19日、6月9日</p>")
            s = series(m)[self.KEY]
            self.assertFalse(s["answerable"], (a, b))
            self.assertTrue(any(b_.endswith(":listed_sections") for b_ in s["blocked_by"]), s["blocked_by"])
        m = PageRun().run_body(self.P + "<h2>収集日一覧</h2><p>燃やさないごみ：4月21日、5月19日、6月9日</p>"
                                        "<h2>日程（再掲）</h2><p>燃やさないごみ：4月21日、5月19日、6月9日</p>")
        self.assertTrue(series(m)[self.KEY]["answerable"])                       # 同じ一覧なら答える

    def test_weekday_in_parens(self):                    # F6
        m = PageRun().run_body(self.P + "<p>燃やさないごみ：4月21日（水）、5月19日（水）、6月9日（水）</p>")
        s = series(m)[self.KEY]
        self.assertFalse(s["answerable"])
        self.assertTrue(any(b.endswith(":listed_weekday") for b in s["blocked_by"]), s["blocked_by"])
        s = series(PageRun().run_body(self.P + "<p>燃やさないごみ：4月21日（火）、5月19日（火）、6月9日（火）</p>"))[self.KEY]
        self.assertTrue(s["answerable"], s["blocked_by"])
        s = series(PageRun().run_body(self.P + "<p>燃やさないごみ：7月20日（祝）、8月17日（月）、9月22日（祝）</p>"))[self.KEY]
        self.assertTrue(s["answerable"], s["blocked_by"])                       # 2026 年の 9月22日は国民の休日
        s = series(PageRun().run_body(self.P + "<p>燃やさないごみ：7月20日（祝）、8月17日（月）、9月24日（祝）</p>"))[self.KEY]
        self.assertTrue(any(b.endswith(":listed_weekday") for b in s["blocked_by"]), s["blocked_by"])    # 9月24日は祝日でない

    def test_rule_and_listed_one_series(self):           # v14: 同じ地区・品目の規則と一覧は 1 つの系列 (一覧) で比べる
        m = PageRun().run_body(self.P + "<p>燃やさないごみ：第2水曜日</p><p>燃やさないごみ：4月21日、5月19日、6月9日</p>")
        specs = [s for s in m.schedules if m.categories[s["category_id"]]["official_label"] == "燃やさないごみ"]
        self.assertEqual(len(specs), 1)
        s = series(m)[self.KEY]
        self.assertFalse(s["answerable"])
        self.assertTrue(any(b.startswith("disputed:") for b in s["blocked_by"]))


# ---- 8. 表の備考などの列の文は、その行の系列だけの注 (関門で読めれば例外、読めなければその行を止める。ページの方針にしない) (F7・v6) ----
class TestOtherColumns(unittest.TestCase):
    def tbl(self, east, west):
        return table(["地区", "燃やすごみ", "不燃ごみ", "備考"], ["東町", "月曜日・木曜日", "第2水曜日", east], ["西町", "火曜日・金曜日", "第4水曜日", west])

    def test_row_scoped(self):                           # F7
        got = series(PageRun().run_body(f"<p>{YE}</p>" + self.tbl("祝日は収集しません", "")))
        s = got[("東町", "燃やすごみ")]
        self.assertTrue(s["answerable"], s["blocked_by"])
        self.assertNotIn("2026-07-20", s["dates"])
        for k in (("西町", "燃やすごみ"), ("西町", "不燃ごみ")):
            self.assertIn("holiday_policy_missing", got[k]["blocked_by"], k)     # 東町の備考を西町に当てない

    def test_unread_blocks_row(self):
        got = series(PageRun().run_body(HOL + self.tbl("東町は翌日に収集します", "")))
        for k in (("東町", "燃やすごみ"), ("東町", "不燃ごみ")):
            self.assertFalse(got[k]["answerable"], k)
        got = series(PageRun().run_body(HOL + self.tbl("祝日も収集します", "-")))
        self.assertTrue(all(s["answerable"] for s in got.values()), {k: s["blocked_by"] for k, s in got.items()})   # 「-」は注が無い


# ---- 9. 止める言い方 (できません・出せません・停止・廃止・延期…) と、収集・出す動きのある使われなかった文は但し書き (F8・v4b) ----
class TestSuspensionSentences(unittest.TestCase):
    CASES = ("当分の間、不燃ごみは出せません。", "不燃ごみの収集は廃止しました。", "収集を停止します。", "収集を延期します。",
             "しばらく収集を見合わせます。", "収集を取りやめます。", "収集を一時中断します。", "収集はできません。", "収集は終了しました。",
             "収集を再開します。", "収集は見送ります。", "不燃ごみは集積所に出せなくなりました。", "収集回数が減ります。",
             "不燃ごみは受け付けていません。", "収集は翌月からです。", "8月は燃やすごみを出さないでください。")

    def test_marks(self):
        for t in ("当分の間、不燃ごみは出せません。", "収集を停止します。", "収集を延期します。", "不燃ごみは受け付けていません。", "収集回数が減ります。"):
            self.assertTrue(content.qualifier_marks(t), t)

    def test_page_blocks(self):
        for c in self.CASES:
            m = PageRun().run_body(HOL + f"<p>{c}</p>" + T2)
            self.assertIn(c, unconsumed(m), c)
            all_blocked(self, m, c)


# ---- 10. B との比べ: A の但し書きは stage2 が実際に残したものと系列ごとのマスの但し書き。deferred・caveat は文の場所で比べる ----
class TestPolicyBCompare(unittest.TestCase):
    def test_deferred_by_location(self):
        import compare
        lines = [content.Line(t, ["ごみの収集日"], f"p{i}", "p") for i, t in
                 enumerate(("年末年始の収集日は決まり次第お知らせします。", "粗大ごみの収集日は決まり次第お知らせします。"))]
        sents = content.sentences(lines)
        a = [{"exception_id": "e1", "kind": "deferred", "text": sents[0].text, "span": sents[0].span("s1")}]
        b = {"deferred": [{"period": "年末年始", "span": "粗大ごみの収集日は決まり次第お知らせします。"}], "caveats": [], "qualifiers": []}
        self.assertTrue(compare.span_overlap(a[0]["text"], b["deferred"][0]["span"]))      # 8 字の共通はある
        self.assertEqual(len(compare.compare_policy(a, [], [], b, FY, sentences=sents)["deferred_b_only"]), 1)
        b["deferred"][0]["span"] = "決まり次第お知らせします"                               # 2 つの文に入る: どの文か決まらない
        self.assertEqual(len(compare.compare_policy(a, [], [], b, FY, sentences=sents)["deferred_b_only"]), 1)
        b["deferred"][0]["span"] = "年末年始の収集日は決まり次第"
        self.assertEqual(compare.compare_policy(a, [], [], b, FY, sentences=sents)["deferred_b_only"], [])
        self.assertEqual(len(compare.compare_policy(a, [], [], {"deferred": [{"span": "粗大ごみの収集日は決まり次第お知らせします。"}]},
                                                    FY)["deferred_b_only"]), 1)      # 文が無ければ丸ごと含むときだけ

    def test_html_policy_b_quals(self):
        import argparse, build, compare
        from test_fix_build_version import FakeLLM, POL_OK
        body = HOL + table(["地区", "燃やすごみ"], ["東町 ※祝日は収集しません", "月曜日・木曜日"], ["西町", "火曜日・金曜日"])
        m = PageRun().run_body(body)
        lines = content.content_region(PageRun.html(body).decode("utf-8")).lines()
        seen, orig = {}, compare.compare_policy

        def cap(a_excs, used, quals, b, per, office_texts=(), sentences=None):
            seen.update(quals=[q["text"] for q in quals], sentences=sentences)
            return orig(a_excs, used, quals, b, per, sentences=sentences)
        pol = dict(POL_OK, qualifiers=[{"span": "※祝日は収集しません"}, {"span": "火曜日・金曜日"}])
        with mock.patch.object(compare, "compare_policy", side_effect=cap):
            build.html_policy_b(m, {"url": "https://example.invalid/p0.html", "title": "収集日"}, "s1", FakeLLM(lambda t: pol),
                                argparse.Namespace(llm=True, offline=False, write=False), {"sources": {}}, lines=lines)
        self.assertIs(seen["sentences"], m.sentences["s1"])
        self.assertIn("※祝日は収集しません", seen["quals"])                       # 名前のマスの但し書き (その行だけを止めるもの)
        self.assertNotIn("火曜日・金曜日", seen["quals"])                          # 読み切った規則のマスの文は A の但し書きにしない
        items = [q["item_id"] for q in m.queue if q["kind"] == "qualifier"]
        self.assertEqual(len(items), 1, items)                                    # B だけのものは「火曜日・金曜日」だけ



class TestGenericHeadingRegression(unittest.TestCase):
    """6 回目の後退: 総称の言葉を部分一致で見ていたため「西町（9月改定）」がページの地区になっていた。総称だけでできた見出しだけをページの地区にする"""

    def test_place_with_generic_words_stays_district(self):
        import html_tables
        for t in ["西町（9月改定）", "西町（4月1日現在）", "西町地区（上期）", "西町（再掲）", "西町 以降", "西町地区のごみ収集日",
                  "西町（令和8年度）", "東地区", "日野町", "別府地区"]:
            self.assertFalse(html_tables._generic_heading(t), t)
            self.assertEqual(html_tables._district_heading([t]), t)

    def test_generic_only_headings(self):
        import html_tables
        for t in ["令和8年度 ごみ収集カレンダー", "各地区のごみ収集日は下記のとおりです", "上半期", "下半期", "注意事項", "4月～9月",
                  "収集日程の変更について", "ごみの出し方", "お問い合わせ", "地区ごとの収集日", "令和8年4月以降", "地区別収集日"]:
            self.assertTrue(html_tables._generic_heading(t), t)
            self.assertIsNone(html_tables._district_heading([t]))


# ==== 7 回目の直し: 6 回目の確かめの穴 (再現 G1〜G8・n1〜n21)。どれも「止める」か「正しい」になること ====
P7 = "<p>祝日も収集します。</p><p>" + YE + "</p>"
T7 = table(["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日・木曜日", "第2水曜日"], ["西町", "火曜日・金曜日", "第4水曜日"])


def run_docs(*docs):
    """資料 (HTML の本文 (str)・("csv", 文字)・("xlsx", bytes)) を順に stage2 に通す (ネットにつながない)"""
    pages = {}
    m = stage2.Muni("999999", "テスト市")
    old = stage2.body_of, fetch_docs.load_boilerplate
    stage2.body_of = lambda url: pages.get(url, (None, None))
    fetch_docs.load_boilerplate = lambda host: None
    try:
        for i, d in enumerate(docs):
            ft = d[0] if isinstance(d, tuple) else "html"
            url = f"https://example.invalid/p{i}.{ft}"
            pages[url] = ((d[1].encode("utf-8"), {"headers": {"content-type": "text/csv; charset=utf-8"}}) if ft == "csv" else
                          (d[1], {"headers": {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}})
                          if ft == "xlsx" else (PageRun.html(d), {"headers": {"content-type": "text/html; charset=utf-8"}}))
            stage2.read_source(m, {"url": url, "file_type": ft, "format_class": "html_table",
                                   "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                               types.SimpleNamespace(llm=False, offline=True), [])
    finally:
        stage2.body_of, fetch_docs.load_boilerplate = old
    return m


def tr_(tag, *cells):
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def raw(*trs):
    return "<table>" + "".join(trs) + "</table>"


# ---- 1. CSV・Excel の注の行 (見出しより上の行・1 つのマスだけの行・役目の無い行) は表の系列すべての注 (G1a・G1b・n9) ----
CSV_HEAD = "令和8年度 ごみ収集日,,,\n地区,燃やすごみ,不燃ごみ,備考\n"
CSV_ROWS = "東町,月曜日・木曜日,第2水曜日,\n西町,火曜日・金曜日,第4水曜日,\n"


class TestCsvNoteRows(unittest.TestCase):
    def test_trailing_and_pre_header_notes_block(self):          # G1a・G1b・n9 の CSV1〜CSV3・CSV7・CSV6
        for csv in (CSV_HEAD + CSV_ROWS + "※第5週は収集しません,,,\n",
                    "令和8年度 ごみ収集日,,,\n※第5週は収集しません,,,\n地区,燃やすごみ,不燃ごみ,備考\n" + CSV_ROWS,
                    CSV_HEAD + CSV_ROWS + ",,,※第5週は収集しません\n",
                    CSV_HEAD + CSV_ROWS + "東町は8月から火曜日・金曜日,,,\n",
                    CSV_HEAD + CSV_ROWS + ",第5週は収集しません,,\n"):
            m = run_docs(P7 + T7.replace("東町", "北町").replace("西町", "南町"), ("csv", csv))
            got = series(m)
            for k in [("東町", "燃やすごみ"), ("東町", "不燃ごみ"), ("西町", "燃やすごみ"), ("西町", "不燃ごみ")]:
                self.assertFalse(got[k]["answerable"], (csv, k, got[k]["blocked_by"]))
                self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in got[k]["blocked_by"]), (csv, k))

    def test_html_policies_page_and_csv(self):                   # G1a そのまま: 方針の HTML のあとの CSV の注の行
        csv = "令和8年度 ごみ収集日,,\n地区,燃やすごみ,不燃ごみ\n東町,月曜日・木曜日,第2水曜日\n西町,火曜日・金曜日,第4水曜日\n※第5週は収集しません,,\n"
        m = run_docs(P7 + T7, ("csv", csv))
        self.assertTrue(series(m))
        self.assertTrue(all(not s["answerable"] for s in series(m).values()))

    def test_month_note_through_gate(self):                      # n9 CSV8: 読める月の注は、その月だけ止める (表の系列すべて)
        rows = CSV_ROWS.replace(",\n", ',"祝日も収集します\n' + YE + '"\n')
        m = run_docs(("csv", "令和8年度 ごみ収集日,,,\n8月は収集しません,,,\n地区,燃やすごみ,不燃ごみ,備考\n" + rows))
        got = series(m)
        self.assertTrue(got)
        for k, s in got.items():
            self.assertFalse(any(d.startswith("2026-08") for d in s["dates"]), k)

    def test_reader_title_and_notes(self):
        import tabular
        rows = [["令和8年度 ごみ収集日", "", ""], ["※祝日は休み", "", ""], ["地区", "燃やすごみ", "不燃ごみ"],
                ["東町", "月曜日", "第2水曜日"], ["収集は朝8時まで", "", ""], ["", "", "第5週は休み"], ["西町", "火曜日", "第4水曜日"]]
        rd = tabular.read_rows(rows, own_names=("テスト市",))
        self.assertEqual(rd["title_rows"], ["令和8年度 ごみ収集日", "※祝日は休み"])      # 期間の手がかりは今までどおりすべての行
        self.assertEqual([(n["row"], n["text"]) for n in rd["notes"]], [(1, "※祝日は休み"), (4, "収集は朝8時まで"), (5, "第5週は休み")])
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["東町", "西町"])
        self.assertEqual(tabular.note_rows(rd), [1, 4, 5])                    # B にも見せる行
        html = tabular.as_table_html(rows, sorted(set(tabular.sample_rows(rd, 2)) | set(tabular.note_rows(rd))), 2)
        self.assertIn("※祝日は休み", html)
        self.assertIn("収集は朝8時まで", html)
        self.assertFalse(tabular.title_row("令和8年4月以降 ごみ収集日"))     # 期間の言葉の題は注 (止める)
        self.assertTrue(tabular.title_row("テスト市 令和8年度 ごみ収集日", ("テスト市",)))

    def test_excel_merged_note_top_left_only(self):              # Excel の結合したマスの注 (左上だけに文字)
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日", "水曜日"], ["※祝日は休み", "", ""]], 1)
        self.assertEqual([n["text"] for n in rd["notes"]], ["※祝日は休み"])
        rd = stage2.html_tables.read_grid([["品目", "収集曜日"], ["燃やすごみ", "月曜日"], ["※第5週は休み", ""]], 1)
        self.assertEqual([n["text"] for n in rd["notes"]], ["※第5週は休み"])
        self.assertNotIn("※第5週は休み", rd["categories"])

    def test_html_row_without_categories_is_note(self):         # n1 A1〜A4: 品目のマスが空の行も注 (HTML)
        from bs4 import BeautifulSoup
        t = BeautifulSoup(T7.replace("</table>", "<tr><td>第5週は収集しません</td><td></td><td></td></tr></table>"), "html.parser")
        rd = stage2.html_tables.read_table(t.find("table"))
        self.assertEqual([n["text"] for n in rd["notes"]], ["第5週は収集しません"])
        m = PageRun().run_body(P7 + T7.replace("</table>", "<tr><td>東町の燃やすごみは7月から水曜日</td><td></td><td></td></tr></table>"))
        all_blocked(self, m, "A2")


# ---- 2. 品目の行・曜日の行の表の地区・丁目の列は、地区の値ごとに読む。規則の列が 2 つ以上ならまとめない (G2a〜G2d・n16〜n18・n20・n21) ----
class TestDistrictColumnsInCategoryTables(unittest.TestCase):
    def test_per_district_rows(self):                            # G2a・D5・D7・D10・D13
        m = PageRun().run_body(P7 + table(["地区", "品目", "収集曜日"], ["A", "燃やすごみ", "毎週月曜日・木曜日"], ["B", "燃やすごみ", "毎週火曜日・金曜日"],
                                          ["A", "不燃ごみ", "第2水曜日"], ["B", "不燃ごみ", "第4水曜日"]))
        got = series(m)
        self.assertEqual(set(got), {("A", "燃やすごみ"), ("B", "燃やすごみ"), ("A", "不燃ごみ"), ("B", "不燃ごみ")})
        a = got[("A", "燃やすごみ")]
        self.assertTrue(a["answerable"], a["blocked_by"])
        self.assertIn("2026-07-20", a["dates"])
        self.assertNotIn("2026-07-21", a["dates"])                           # B の火曜日を A に足さない
        self.assertNotIn("2026-07-08", got[("B", "不燃ごみ")]["dates"])      # A の第2水曜日を B に足さない

    def test_no_municipality_wide_series(self):                  # G2b・G2c・D6・D8・D9・D11・D12・D14〜D17
        cases = [P7 + table(["地区", "曜日", "ごみの種類"], ["1", "月曜日", "燃やすごみ"], ["2", "火曜日", "燃やすごみ"]),
                 P7 + raw('<tr><th rowspan="2">品目</th><th colspan="2">収集曜日</th></tr>', tr_("th", "A", "B"),
                          tr_("td", "燃やすごみ", "毎週月曜日・木曜日", "毎週火曜日・金曜日")),
                 P7 + table(["品目", "収集曜日 A", "収集曜日 B"], ["燃やすごみ", "毎週月曜日・木曜日", "毎週火曜日・金曜日"]),
                 P7 + table(["品目", "収集曜日", "収集曜日"], ["燃やすごみ", "毎週月曜日・木曜日", "毎週火曜日・金曜日"]),
                 P7 + table(["品目", "収集曜日", "対象地区"], ["燃やすごみ", "毎週月曜日", "東町"], ["燃やすごみ", "毎週火曜日", "西町"]),
                 P7 + table(["収集区域", "品目", "収集曜日"], ["①", "燃やすごみ", "毎週月曜日"], ["②", "燃やすごみ", "毎週木曜日"])]
        for body in cases:
            got = series(PageRun().run_body(body))
            self.assertTrue(got, body[-120:])
            for k, s in got.items():
                if k[0] == "テスト市":
                    self.assertFalse(s["answerable"], (body[-120:], k, s["blocked_by"]))
                elif s["answerable"]:                                    # 地区ごとの系列は、その地区の 1 つの曜日だけ
                    self.assertFalse({"2026-07-20", "2026-07-21"} <= set(s["dates"]), (body[-120:], k))
                    self.assertFalse({"2026-07-20", "2026-07-23"} <= set(s["dates"]), (body[-120:], k))

    def test_csv_long_format(self):                              # G2d・n20・n21
        note = '"祝日も収集します\n' + YE + '"'
        m = run_docs(("csv", f"令和8年度 ごみ収集日,,,\n地区名,品目,収集曜日,備考\n東町,燃やすごみ,毎週月曜日・木曜日,{note}\n"
                             f"西町,燃やすごみ,毎週火曜日・金曜日,{note}\n"))
        got = series(m)
        self.assertEqual(set(got), {("東町", "燃やすごみ"), ("西町", "燃やすごみ")})
        self.assertIn("2026-07-20", got[("東町", "燃やすごみ")]["dates"])
        self.assertNotIn("2026-07-21", got[("東町", "燃やすごみ")]["dates"])
        self.assertNotIn("2026-07-20", got[("西町", "燃やすごみ")]["dates"])

    def test_reader_blank_district_rows(self):                   # 地区の列が空の行の品目は、どの地区か決まらない (読めないもの)
        rd = stage2.html_tables.read_grid([["地区", "品目", "収集曜日"], ["東町", "燃やすごみ", "月曜日"], ["", "燃やすごみ", "木曜日"],
                                           ["西町", "燃やすごみ", "火曜日"]], 1)
        self.assertEqual(rd["orientation"], "category_rows")
        rows = {r["district_text"]: r for r in rd["rows"]}
        self.assertEqual(set(rows), {"東町", "", "西町"})
        self.assertTrue(all(r["rules"]["燃やすごみ"].status == "unparsed" for r in rows.values()))
        self.assertEqual(rows["東町"]["district_from"], "column")
        self.assertEqual(rows["東町"]["members"][0]["extra"]["name_verbatim"], "東町")


# ---- 3. 月・回数・出し方を限る印の無い文 (奇数月・隔月・年N回・Nか月・ごと・予約・申込・事前・月の並び・表の題) は但し書き (G4・n11・n12) ----
class TestRestrictionWords(unittest.TestCase):
    def test_sentences_block(self):                              # G4・n12
        for c in ["不燃ごみは奇数月です。", "不燃ごみは隔月の収集です。", "不燃ごみは偶数月に出してください。", "不燃ごみは年6回です。",
                  "不燃ごみは4月、6月、8月、10月、12月、2月です。", "不燃ごみは2か月に1回です。", "不燃ごみは3か月ごとです。",
                  "不燃ごみは予約制です。", "不燃ごみは申込制になりました。", "不燃ごみは事前にお申し込みください。", "燃やすごみの日は月・木です。",
                  "不燃ごみは2週間に1回です。", "不燃ごみは奇数週です。", "不燃ごみは8月に出してください。"]:
            m = PageRun().run_body(P7 + T7 + f"<p>{c}</p>")
            self.assertIn(c, unconsumed(m), c)
            all_blocked(self, m, c)

    def test_caption_and_after_table(self):                      # n11 H5・H7
        m = PageRun().run_body(P7 + T7.replace("<table>", "<table><caption>不燃ごみは奇数月です</caption>"))
        all_blocked(self, m, "caption")
        all_blocked(self, PageRun().run_body(P7 + T7 + "<p>不燃ごみは奇数月です。</p>"), "after table")

    def test_marks(self):
        for t in ["奇数月", "隔月", "年6回", "6回/年", "2か月に1回", "3ヶ月ごと", "2週ごと", "予約制", "申込", "申し込み", "事前に", "2週間に1回"]:
            self.assertTrue(content.qualifier_marks(t), t)
        for t in ["地区ごとの収集日", "燃やすごみ"]:
            self.assertEqual(content.qualifier_marks(t), [], t)
        self.assertFalse(content.policy_like("粗大ごみは事前に申し込みが必要です。"))     # 出し方の印は本文の範囲の安全弁に数えない


# ---- 4. 表の参照の印 (※・※1・注1・*) の表の外の注は、印の無い系列のページの方針にしない (G6a・G6b・n3) ----
class TestFootnoteMarks(unittest.TestCase):
    def test_header_mark(self):                                  # G6a・F8: 印の無い不燃ごみに「祝日も収集」を当てない
        m = PageRun().run_body(f"<p>{YE}</p>" + T7.replace("<th>燃やすごみ</th>", "<th>燃やすごみ※</th>") + "<p>※祝日も収集します。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        got = series(m)
        self.assertNotIn("2026-09-23", got[("西町", "不燃ごみ")]["dates"])      # 秋分の日 (第4水曜日)
        self.assertFalse(got[("西町", "燃やすごみ※")]["answerable"])

    def test_district_mark(self):                                # G6b・F9: 印の無い西町に「祝日は収集しません」を当てない
        m = PageRun().run_body(f"<p>{YE}</p>" + T7.replace("<td>東町</td>", "<td>東町※</td>") + "<p>※祝日は収集しません。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        got = series(m)
        qid = "unconsumed_qualifier:" + expand.rest_qualifier_id("※祝日は収集しません。")
        for k in [("東町※", "燃やすごみ"), ("東町※", "不燃ごみ")]:
            self.assertFalse(got[k]["answerable"], k)
            self.assertIn(qid, got[k]["blocked_by"], k)                       # 注は印のある東町だけを止める
        # 7 回目の確かめのあと: 印の注は表全体の注かもしれないので、印の無い西町も止める (ページの方針としては当てない)
        self.assertIn(qid, got[("西町", "燃やすごみ")]["blocked_by"])
        self.assertFalse(got[("西町", "燃やすごみ")]["answerable"])

    def test_unmarked_table_keeps_page_policy(self):             # F12: 表に印が無ければ、※の文はこれまでどおりページの方針
        m = PageRun().run_body(f"<p>{YE}</p>" + T7 + "<p>※祝日は収集しません。</p>")
        self.assertTrue([e for e in m.exceptions if e["kind"] == "holiday_policy"])

    def test_numbered_and_unmatched(self):                       # ※1・※2 は印ごと。番号の合わない ※3 は資料すべてを止める
        t = T7.replace("<td>東町</td>", "<td>東町※1</td>").replace("<td>西町</td>", "<td>西町※2</td>")
        m = PageRun().run_body(f"<p>{YE}</p>" + t + "<p>※1 祝日も収集します。</p><p>※2 祝日は収集しません。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        m = PageRun().run_body(P7 + T7.replace("<td>東町</td>", "<td>東町※1</td>") + "<p>※3 西町は祝日の翌日です。</p>")
        all_blocked(self, m, "unmatched footnote number")

    def test_same_line_continuation(self):                       # 注の行のあとの文も注 (ページの方針にしない)
        m = PageRun().run_body(f"<p>{YE}</p>" + T7.replace("<td>東町</td>", "<td>東町※</td>") + "<p>※東町の注です。祝日は収集しません。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])

    def test_mark_reader(self):
        self.assertEqual(stage2.ref_marks_in("燃やすごみ※"), {("※", "")})
        self.assertEqual(stage2.ref_marks_in("東町※1"), {("※", "1")})
        self.assertEqual(stage2.ref_marks_in("月曜日・木曜日（注1）"), {("注", "1")})
        self.assertEqual(stage2.ref_marks_in("月曜日・木曜日＊"), {("*", "")})
        self.assertEqual(stage2.ref_marks_in("※祝日は休み"), set())                  # 印のあとに文が続くのはマスの注
        self.assertEqual(stage2.ref_marks_in("東町 ※10月は休み"), set())
        for t, k in (("※祝日も収集します", ("※", "")), ("※1 祝日", ("※", "1")), ("注1：祝日", ("注", "1")), ("（注1）祝日", ("注", "1")),
                     ("＊祝日", ("*", "")), ("※10月は休み", ("※", "")), ("注意事項", None), ("祝日も収集します", None)):
            self.assertEqual(stage2.foot_key(t), k, t)


# ---- 5. 表の本文の途中の見出しの行 (すべて th・列の役目の言葉だけ) のあとの行は読まない (G7・n2 B4・B5・n1 B1〜B3) ----
class TestMidBodyHeader(unittest.TestCase):
    def test_rows_after_blocked(self):
        th = lambda *c: tr_("th", *c)
        td = lambda *c: tr_("td", *c)
        for t in (raw(th("地区", "燃やすごみ", "不燃ごみ"), td("東町", "月曜日・木曜日", "第2水曜日"), th("地区", "プラスチック", "びん・かん"),
                      td("北町", "水曜日", "第1金曜日")),
                  raw(th("地区", "燃やすごみ", "不燃ごみ"), td("東町", "月曜日・木曜日", "第2水曜日"), th("地区", "不燃ごみ", "燃やすごみ"),
                      td("北町", "第1金曜日", "水曜日")),
                  raw(th("地区", "燃やすごみ", "不燃ごみ"), td("東町", "月曜日・木曜日", "第2水曜日"), th("地区", "燃やすごみ", "不燃ごみ"),
                      td("北町", "水曜日・土曜日", "第1金曜日")),
                  raw(th("地区", "燃やすごみ", "不燃ごみ"), td("東町", "月曜日・木曜日", "第2水曜日"),
                      td("<b>地区</b>", "<b>プラスチック</b>", "<b>びん・かん</b>"), td("北町", "水曜日", "第1金曜日"))):
            got = series(PageRun().run_body(P7 + t))
            self.assertNotIn("地区", {k[0] for k in got})                          # 見出しの行を地区にしない
            self.assertTrue([k for k in got if k[0] == "北町"])
            for k, s in got.items():
                if k[0] == "北町":
                    self.assertFalse(s["answerable"], (k, s["blocked_by"]))

    def test_reader(self):
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ"], ["東町", "月曜日"], ["地区", "プラスチック"], ["北町", "水曜日"]], 1)
        self.assertEqual(rd["mid_heads"], [2])
        self.assertEqual(rd["rows"][0]["rules"]["燃やすごみ"].status, "ok")
        self.assertEqual(rd["rows"][1]["rules"]["燃やすごみ"].status, "unparsed")
        self.assertFalse(stage2.html_tables._mid_head(["月曜日", "燃やすごみ"], None))     # 曜日の表の行は見出しでない
        self.assertFalse(stage2.html_tables._mid_head(["東町", "月曜日"], None))


# ---- 6. 型 C の見出しは、地区の文法で地名の部分が読めるときだけ地区 (G5・n13・n19) ----
class TestDistrictHeadingGrammar(unittest.TestCase):
    def test_non_place_headings(self):                           # n13: ページの地区の同じ品目も止まる
        for h in ["祝日のある週", "予備日", "振替日", "雨天時", "臨時", "夏季", "旧", "新", "改定後", "変更前", "当面", "試行", "通常", "全域"]:
            m = PageRun().run_body(P7 + f"<p>燃やすごみ：毎週月曜日・木曜日</p><h2>{h}</h2><p>燃やすごみ：毎週火曜日・金曜日</p>")
            self.assertNotIn(h, {k[0] for k in series(m)}, h)
            all_blocked(self, m, h)
            only = series(PageRun().run_body(P7 + f"<h2>{h}</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"))     # その節にしか規則が無くても
            self.assertTrue(only, h)
            self.assertTrue(all(not s["answerable"] for s in only.values()), h)

    def test_place_headings_kept(self):                          # 6 回目の確かめのあとの直しを保つ (地名の部分は地区、残りは但し書き)
        for h in ["西町（9月改定）", "西町（4月1日現在）", "西町地区（上期）", "西町地区のごみ収集日", "東町（祝日の週）"]:
            got = series(PageRun().run_body(P7 + f"<h2>北町</h2><p>燃やすごみ：毎週月曜日・木曜日</p><h2>{h}</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"))
            self.assertIn(h, {k[0] for k in got}, h)
            self.assertFalse(got[(h, "燃やすごみ")]["answerable"], h)
            self.assertTrue(got[("北町", "燃やすごみ")]["answerable"], h)
            self.assertNotIn("テスト市", {k[0] for k in got}, h)

    def test_generic_under_place(self):                          # n19: 地区の見出しの下の総称の見出し (「収集日」) は、その地区
        got = series(PageRun().run_body(P7 + "<h2>東町</h2><h3>収集日</h3><p>燃やすごみ：毎週火曜日・金曜日</p>"))
        self.assertEqual({k[0] for k in got}, {"東町"})
        got = series(PageRun().run_body(P7 + "<h2>テスト市のごみ収集日</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"))
        self.assertEqual({k[0] for k in got}, {"テスト市"})                    # 自治体の名前は地区にしない

    def test_grammar(self):
        import members
        for t in ["東町", "藤蔵・中島", "壱ツ屋・与九郎島", "西町（9月改定）", "西町地区のごみ収集日", "日野町", "A地区", "第5区", "上記以外", "あけぼの町",
                  "本町1丁目", "西町・北町（令和8年4月）"]:
            self.assertTrue(members.place_part(t), t)
        for t in ["祝日のある週", "変更前", "試行", "予備日", "雨天時", "臨時", "旧", "新", "通常", "全域", "お住まいの地区", "臨時収集地区"]:
            self.assertFalse(members.place_part(t), t)
        self.assertFalse(members.readable_district("不燃ごみは奇数月です"))
        self.assertFalse(members.readable_district("西町の不燃ごみは奇数月"))
        self.assertTrue(members.readable_district("山間部"))
        self.assertTrue(members.readable_district("大字浜崎の一部（県道和光志木線北側）"))


# ---- 7. 品目がすべて無し・読めない行で、地区のマスが地区の文法で読めない行は表の注 (G8・n11 H2〜H4) ----
class TestNoneRowNotes(unittest.TestCase):
    def test_blocks(self):
        hd = tr_("th", "地区", "燃やすごみ", "不燃ごみ")
        rows = tr_("td", "東町", "月曜日・木曜日", "第2水曜日") + tr_("td", "西町", "火曜日・金曜日", "第2水曜日")
        for note in (tr_("td", "不燃ごみは奇数月です", "－", "－"), tr_("td", "※不燃ごみは奇数月です", "－", "－"),
                     tr_("td", "西町の不燃ごみは奇数月", "－", "－"), tr_("td", "備考", "祝日は収集しません", "")):
            m = PageRun().run_body(P7 + raw(hd, rows, note))
            got = all_blocked(self, m, note)
            self.assertEqual({k[0] for k in got}, {"東町", "西町"}, note)

    def test_readable_district_kept(self):                       # 地名として読めるマス (山間部) は、収集の無い地区のまま
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日", "水曜日"], ["山間部", "－", "－"]], 1)
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["東町", "山間部"])
        self.assertEqual(rd["notes"], [])
        m = run_docs(("csv", CSV_HEAD + CSV_ROWS + "不燃ごみは奇数月です,－,－,\n"))
        all_blocked(self, m, "csv none row")


# ---- 7 回目の直しの追い打ち (同じ穴の言い換え): どれも「止める」か「正しい」 ----
def xlsx(*sheets):
    """[(シートの名前, 行のリスト)] → xlsx の bytes"""
    import io, openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class TestRound7Variants(unittest.TestCase):
    def test_excel_other_sheet_is_note(self):                    # 日程の表にならないシート (「注意事項」) の文字も表の系列の注
        rows = [["令和8年度 ごみ収集日"], ["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日・木曜日", "第2水曜日"], ["西町", "火曜日・金曜日", "第4水曜日"]]
        m = run_docs(("xlsx", xlsx(("収集日", rows), ("注意事項", [["第5週は収集しません"]]))))
        all_blocked(self, m, "other sheet")
        m = run_docs(("xlsx", xlsx(("収集日", rows + [["※祝日は休み", None, None]]))))
        all_blocked(self, m, "merged note top-left")

    def test_more_restriction_words(self):                       # 漢数字・おき・春秋・「・」の月の並び
        for c in ["不燃ごみは二か月に一回です。", "不燃ごみは2週おきです。", "不燃ごみは年度内に2回です。", "不燃ごみは春と秋です。",
                  "不燃ごみは4・6・8・10・12・2月です。", "不燃ごみは毎年5月です。"]:
            m = PageRun().run_body(P7 + T7 + f"<p>{c}</p>")
            all_blocked(self, m, c)
        for t in ["春日部市のごみ", "秋分の日", "1回に出せる量は3袋まで"]:
            self.assertEqual([g for g, _ in content.qualifier_marks(t) if g in ("count", "range")], [], t)

    def test_other_footnote_symbols(self):                       # ★・＜注＞ の印も同じ
        m = PageRun().run_body(f"<p>{YE}</p>" + T7.replace("<td>東町</td>", "<td>東町★</td>") + "<p>★祝日は収集しません。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        m = PageRun().run_body(f"<p>{YE}</p>" + T7.replace("<td>月曜日・木曜日</td>", "<td>月曜日・木曜日＜注1＞</td>") +
                               "<p>＜注1＞祝日は収集しません。</p>")
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])

    def test_single_bare_heading_not_district(self):             # 地区の字の無い 1 つの名前の見出し (「特例」「中島」) は地区にしない
        for h in ["特例", "中島", "例外", "指定地区", "対象地区"]:
            m = PageRun().run_body(P7 + f"<p>燃やすごみ：毎週月曜日・木曜日</p><h2>{h}</h2><p>燃やすごみ：毎週火曜日・金曜日</p>")
            self.assertNotIn(h, {k[0] for k in series(m)}, h)
            all_blocked(self, m, h)

    def test_place_names_with_date_like_chars(self):             # 祝園・夏見・緑が丘・(寺井町)横町 は地名 (表の行のまま)
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ"], ["祝園", "月曜日"], ["夏見", "火曜日"], ["緑が丘", "水曜日"],
                                           ["(寺井町)横町", "木曜日"], ["ひばりが丘", "金曜日"]], 1)
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["祝園", "夏見", "緑が丘", "(寺井町)横町", "ひばりが丘"])
        self.assertEqual(rd["notes"], [])

    def test_rule_row_without_place_is_note(self):              # 地区のマスに地名が無い行は、規則が読めても注 (別の地区にしない)
        rd = stage2.html_tables.read_grid([["地区", "燃やすごみ", "不燃ごみ"], ["東町", "月曜日", "第2水曜日"],
                                           ["第5週は収集しません", "月曜日", ""]], 1)
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["東町"])
        self.assertEqual(len(rd["notes"]), 1)
        rd = stage2.html_tables.read_grid([["地区", "品目", "収集曜日"], ["東町", "燃やすごみ", "月曜日"], ["不燃ごみは奇数月です", "不燃ごみ", "第2水曜日"]], 1)
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["東町"])
        self.assertEqual(len(rd["notes"]), 1)

    def test_conditional_district_heading_blocks_page(self):     # 「東町（祝日の週）」の節があれば、ページの地区の同じ品目も止める
        body = "<p>燃やすごみ：毎週月曜日・木曜日</p><h2>{}</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"
        for h in ["東町（祝日の週）", "西町（9月改定）", "東町は翌日"]:
            all_blocked(self, PageRun().run_body(P7 + body.format(h)), h)
        got = series(PageRun().run_body(P7 + body.format("西町（再掲）")))        # 総称だけの残りは条件でない
        self.assertTrue(got[("テスト市", "燃やすごみ")]["answerable"])


if __name__ == "__main__":
    unittest.main()


class TestRound7Regressions(unittest.TestCase):
    """7 回目の確かめで見つかった後退と穴: 題の行が広すぎる・参照の印の注がほかの系列の止めを外す"""

    def test_title_row_closed(self):
        import tabular
        for t in ["令和8年度 ごみ収集日", "2026年度 家庭ごみ収集カレンダー", "令和８年度（2026年度）ごみ収集日一覧"]:
            self.assertTrue(tabular.title_row(t), t)
        self.assertTrue(tabular.title_row("テスト市 令和8年度 ごみ収集日程表", ("テスト市",)))
        for t in ["資源は月1回", "資源は年2回", "資源（月1回）", "資源ごみ 第1・第3", "収集日は第1日曜日", "ごみは月2回",
                  "令和8年4月以降 ごみ収集日", "※祝日は休み", "8月は収集しません"]:
            self.assertFalse(tabular.title_row(t), t)
