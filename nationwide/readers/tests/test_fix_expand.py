"""expand.py の値を当てる品目 (value_scope・cell_notes) を広げない: 資料の品目に無い品目を言う文 (「ペットボトルは祝日も
収集しません。」で表が燃やすごみ・燃やさないごみだけ) は、ほかの品目にも「*」にも当てず、使われなかった但し書きとして残して
系列を止める (ネットにつながない。LLM も呼ばない)"""
import types, unittest

import _util  # noqa: F401
import categories, content, expand, fetch_docs, markup, rules, stage2

FY = {"period_id": "fy2026", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
      "year_evidence": {"ok": True}}
OK = {"readers": [], "status": "agreed", "independence": "code_llm"}
CLOSE = {"exception_id": "y", "kind": "year_end", "treatment": "closure", "last": "2026-12-29", "first": "2027-01-04", "others": None,
         "text": "年末は12月29日まで、年始は1月4日から"}
PET = "ペットボトルは祝日も収集しません。"


def cats_of(*labels):
    return {f"c{i + 1}": {"coarse": categories.coarse(l), "official_label": l} for i, l in enumerate(labels)}


CATS2 = cats_of("燃やすごみ", "燃やさないごみ")
CATS6 = cats_of("燃やすごみ", "燃やさないごみ", "資源", "プラスチック", "古紙", "びん・缶")


def sents(*lines, heading=()):
    out = []
    for t in lines:
        out += content.sentences([content.Line(t, list(heading), "p", "p")])
    return out


def series(cid, excs, quals=()):
    return expand.expand_series({"district_id": "d1", "category_id": cid, "period": FY, "origin": "expanded",
                                 "rule": {"text": "月・木", "parsed": rules.parse("月・木")}, "exceptions": excs,
                                 "qualifiers": list(quals), "provenance": OK})


def unconsumed(ss, used):
    ut = {content.squash(s.text) for s in used}
    return [q for q in content.scan_qualifiers(ss) if content.squash(q["text"]) not in ut]


class TestUnknownItem(unittest.TestCase):
    def test_value_scope(self):
        self.assertIs(expand.value_scope(PET, CATS2), expand.AMBIGUOUS)                       # 表に無い品目 → 「*」にしない
        self.assertIs(expand.value_scope("粗大ごみは祝日も収集します", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("スプレー缶は祝日も収集しません", CATS6), expand.AMBIGUOUS)  # 「缶」の品目はあるがスプレー缶は無い
        mixed = "燃やすごみとペットボトルは祝日も収集しません"
        # 3 回目 (閉じた見本): 主題に資料の品目でない品目 (ペットボトル) が並べば、表にある品目にも狭めない
        self.assertIs(expand.value_scope(mixed, CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope(mixed, cats_of("燃やすごみ", "資源")), expand.AMBIGUOUS)   # 「資源」の中かもしれない
        # 公式の名前の無い品目 (分類 metal だけ) の「金属・陶器・ガラスごみ」は、陶器・ガラスが資料の品目に当たらない → 見本の外
        self.assertIs(expand.value_scope("金属・陶器・ガラスごみは祝日も収集します", {"m": {"coarse": ["metal"]}}), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("燃やさないごみは祝日も収集しません。", CATS2), ["c2"])      # 表にある品目はその品目だけ
        self.assertEqual(expand.value_scope("びんは祝日も収集します", CATS6), ["c6"])
        self.assertEqual(expand.value_scope("祝日も収集します。", CATS2), "*")                          # 名指しが無ければ今までどおり
        self.assertEqual(expand.value_scope("祝日も収集します。カレンダーを配布しています", CATS2), "*")   # 「配布」の「布」は品目でない

    def test_heading_and_line_not_widened(self):   # 見出し・同じ行の名指しが表に無い品目なら、上の見出しへ進まない
        self.assertIs(expand.value_scope("祝日も収集しません。", CATS2, ("ごみの収集日", "ペットボトル")), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("祝日も収集しません。", CATS2, ("燃やすごみ", "ペットボトル")), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("祝日も収集しません。", CATS2, (), "ペットボトルは水曜日。祝日も収集しません。"),
                      expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("祝日も収集しません。", CATS2, ("燃やすごみ", "注意")), ["c1"])

    def test_not_applied_and_qualifier_kept(self):
        ss = sents(PET, heading=("ごみの収集日",))
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertEqual((ex, used), ([], []))                              # どの品目にも holiday_policy を作らない
        q = unconsumed(ss, used)
        self.assertEqual([x["text"] for x in q], [PET])                     # 使われなかった但し書きとして残る
        self.assertEqual(expand.block_scope(PET, CATS2, ("ごみの収集日",)), "*")
        quals = [{"qualifier_id": x["qualifier_id"], "months": x["months"]} for x in q]
        hol = {"exception_id": "h", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集します"}
        for cid in CATS2:
            s = series(cid, [hol, CLOSE], quals)
            self.assertFalse(s["answerable"])
            self.assertIn(f"unconsumed_qualifier:{q[0]['qualifier_id']}", s["blocked_by"])
            self.assertEqual(s["dates"], [])

    def test_mixed_with_generic(self):             # 「祝日も収集します」は全部の品目に、ペットボトルの文は使わずに残す
        ss = sents("祝日も収集します。", PET)
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertEqual([(e["kind"], e["value"], e["scope"]["category_ids"]) for e in ex], [("holiday_policy", "collected", "*")])
        self.assertEqual([x["text"] for x in unconsumed(ss, used)], [PET])

    def test_mixed_known_and_unknown(self):        # 表にある品目と表に無い品目が並ぶ主題: どれにも当てず、但し書きとして残す (3 回目で狭めるのもやめた)
        t = "燃やすごみとペットボトルは祝日も収集しません。"
        ss = sents(t)
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertEqual((ex, used), ([], []))
        self.assertEqual([x["text"] for x in unconsumed(ss, used)], [t])
        ss = sents(t)
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, cats_of("燃やすごみ", "資源"), "s1")
        self.assertEqual((ex, used), ([], []))
        self.assertEqual([x["text"] for x in unconsumed(ss, used)], [t])
        self.assertEqual(expand.block_scope(t, cats_of("燃やすごみ", "資源"), ("燃やすごみ",)), "*")

    def test_known_item_only_itself(self):
        ss = sents("燃やさないごみは祝日も収集しません。")
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertEqual([(e["kind"], e["value"], e["scope"]["category_ids"]) for e in ex], [("holiday_policy", "not_collected", ["c2"])])
        self.assertTrue(expand.in_scope(ex[0], "c2"))
        self.assertFalse(expand.in_scope(ex[0], "c1"))

    def test_generic_unchanged(self):
        ex, used, _ = expand.exceptions_from_sentences(sents("祝日も収集します。"), FY, CATS2, "s1")
        self.assertEqual([(e["kind"], e["scope"]["category_ids"]) for e in ex], [("holiday_policy", "*")])
        self.assertEqual(len(used), 1)

    def test_year_end_unknown_item(self):          # 年末年始の休みも、表に無い品目の文はすべての品目に当てない
        t = "粗大ごみは12月29日から1月3日まで収集しません。"
        ss = sents(t)
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertEqual((ex, used), ([], []))
        self.assertEqual([x["text"] for x in unconsumed(ss, used)], [t])

    def test_excluded_items(self):                 # 「…以外」「…を除き」は名指しした品目の値ではない (逆の品目に当てない)
        self.assertIs(expand.value_scope("燃やすごみ以外は祝日も収集しません。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("びん・缶を除き、祝日も収集します。", CATS6), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("燃やすごみは祝日も収集します（年末年始を除く）", CATS2), ["c1"])
        ex, used, _ = expand.exceptions_from_sentences(sents("燃やすごみ以外は祝日も収集しません。"), FY, CATS2, "s1")
        self.assertEqual((ex, used), ([], []))

    def test_block_scope_widens(self):             # 止める向き: 表に無い品目・「以外」を言う文は、節の品目に狭めない
        h = ("ごみの収集日", "燃やさないごみ")
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", CATS2, h), ["c2"])            # 今までどおり
        self.assertEqual(expand.block_scope(PET, CATS2, h), "*")
        self.assertEqual(expand.block_scope("燃やさないごみ以外は年末年始の収集日が変わります", CATS2, h), "*")
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", CATS2, ("燃やさないごみ", "ペットボトル")), "*")


class TestCellNotes(unittest.TestCase):
    SC = {"category_ids": ["c1"], "district_ids": ["d1"]}

    def notes(self, t, cats=None):
        return expand.cell_notes(t, FY, self.SC, {"source_id": "s1"}, cats=cats)

    def test_other_item_left(self):                # 燃やすごみのマスの「（ペットボトルは祝日の収集は休み）」を燃やすごみに当てない
        t = "(ペットボトルは祝日の収集は休み)"
        self.assertEqual(self.notes(t), ([], [t]))
        self.assertEqual(self.notes(t, CATS2), ([], [t]))
        self.assertEqual(self.notes("(燃やさないごみは祝日も収集)", CATS2), ([], ["(燃やさないごみは祝日も収集)"]))
        self.assertEqual(self.notes("(燃やすごみ以外は祝日の収集は休み)", CATS2)[0], [])

    def test_own_item_or_none_applied(self):
        ex, left = self.notes("(燃やすごみは祝日の収集は休み)", CATS2)
        self.assertEqual(([(e["kind"], e["value"], e["scope"]) for e in ex], left), ([("holiday_policy", "not_collected", self.SC)], []))
        self.assertEqual(self.notes("(燃やすごみは祝日の収集は休み)")[1], ["(燃やすごみは祝日の収集は休み)"])   # 品目が分からなければ当てない
        for cats in (None, CATS2):
            ex, left = self.notes("(祝日の収集は休み)", cats)
            self.assertEqual(([(e["kind"], e["value"]) for e in ex], left), ([("holiday_policy", "not_collected")], []))


class TestStage2Page(unittest.TestCase):         # 合成のページを stage2 で読む: 表に無い品目の祝日の文で全部の品目の日付を作らない
    HTML = ("<html><head><title>令和8年度 ごみ収集日</title></head><body><main><h1>令和8年度 ごみの収集日</h1>"
            f"<p>{PET}</p><p>年末は12月29日まで、年始は1月4日から収集します。</p>"
            "<table><tr><th>地区</th><th>燃やすごみ</th><th>燃やさないごみ</th></tr>"
            "<tr><td>本町</td><td>月曜日・木曜日</td><td>水曜日</td></tr></table></main></body></html>").encode("utf-8")

    def setUp(self):
        self._body, self._bp = stage2.body_of, fetch_docs.load_boilerplate
        self._rb, self._rtb = markup.reader_b, markup.reader_text_b
        stage2.body_of = lambda url: (self.HTML, {"headers": {"content-type": "text/html; charset=utf-8"}})
        fetch_docs.load_boilerplate = lambda host: None

    def tearDown(self):
        stage2.body_of, fetch_docs.load_boilerplate = self._body, self._bp
        markup.reader_b, markup.reader_text_b = self._rb, self._rtb

    def test_page(self):
        m = stage2.Muni("999999", "テスト市")
        stage2.read_source(m, {"url": "https://example.invalid/p0.html", "file_type": "html", "format_class": "html_table",
                               "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                           types.SimpleNamespace(llm=False, offline=True), [])
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        unc = [q["text"] for q in m.sources[0]["extra"]["qualifiers_unconsumed"]]
        self.assertIn(PET, unc)
        self.assertEqual(len(m.schedules), 2)
        for s in m.schedules:
            r = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
            self.assertFalse(r["answerable"], r["blocked_by"])
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in r["blocked_by"]), r["blocked_by"])


# ---- 2 回目: 辞書 (COARSE_RULES) に無い品目・「<言葉>ごみ」・主題「Xは」も「*」にしない ----
UNKNOWN = ["危険ごみは祝日も収集しません。", "埋立ごみは祝日の収集はありません。", "剪定枝は祝日も収集します。",
           "発泡スチロールは祝日も収集しません。", "白色トレイは祝日も収集しません。", "せとものは祝日も収集しません。",
           "陶器類は祝日も収集しません。", "布団は祝日も収集しません。", "カーペットは祝日も収集しません。",
           "その他のごみは祝日も収集しません。", "家庭ごみは祝日も収集します。", "大型ごみは祝日も収集しません。",
           "ストーブの祝日収集はありません。", "マットレスは祝日も収集しません。", "この地区は祝日も収集しません。",
           "□剪定枝は祝日も収集します。", "危 険 ご み は 祝 日 も 収 集 し ま せ ん"]
GENERIC = ["祝日も収集します。", "ごみは祝日も収集します。", "すべてのごみは祝日も収集します。", "全てのごみは祝日も収集します。",
           "各ごみの収集は祝日も行います。", "ごみの収集は祝日も行います。", "国民の祝日も収集します。", "祝日・振替休日も収集します。",
           "収集日が祝日と重なった場合も収集します。", "本市では祝日も収集します。", "◇祝日も収集します。",
           "収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。", "雨天、祝日も収集します。", "祝日も通常通りごみの収集を行います。",
           "詳しくはカレンダーをご覧ください。祝日も収集します。", "祝日も収集します。カレンダーを配布しています。"]


class TestUnknownItemRound2(unittest.TestCase):
    def test_unknown_items_ambiguous(self):          # 表が燃やすごみ・燃やさないごみ (と資源など) だけなら、どれにも「*」にも当てない
        for cats in (CATS2, CATS6):
            for t in UNKNOWN:
                self.assertIs(expand.value_scope(t, cats, ("ごみの収集日",)), expand.AMBIGUOUS, t)
                self.assertEqual(expand.block_scope(t, cats, ("ごみの収集日", "燃やすごみ")), "*", t)   # 止める向きは広げる

    def test_generic_unchanged(self):                # 品目を狭めない文は今までどおり「*」
        for cats in (CATS2, CATS6):
            for t in GENERIC:
                self.assertEqual(expand.value_scope(t, cats, ("ごみの収集日",)), "*", t)
        self.assertEqual(expand.value_scope("祝日も収集します。", CATS2, ("令和8年度 ごみの収集日",)), "*")   # 空白は語の切れ目 (「年度ごみ」でない)

    def test_source_label_named(self):               # 資料の名前なら、その品目だけ
        cats = cats_of("燃やすごみ", "燃やさないごみ", "危険ごみ", "剪定枝")
        self.assertEqual(expand.value_scope("危険ごみは祝日も収集しません。", cats), ["c3"])
        self.assertEqual(expand.value_scope("剪定枝は祝日も収集します。", cats), ["c4"])
        self.assertIs(expand.value_scope("埋立ごみは祝日も収集しません。", cats), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("金属・陶器・ガラスごみは祝日も収集します", cats_of("燃やすごみ", "金属・陶器・ガラスごみ")), ["c2"])
        # 品目のかっこ書きの例 (「紙おむつ」) は、その品目の名前の一部
        self.assertEqual(expand.value_scope("祝日も収集します。", CATS2, ("燃やすごみ（生ごみ・紙おむつなど）",)), ["c1"])
        self.assertEqual(expand.value_scope("燃やすごみ（生ごみ・紙おむつなど）は祝日も収集します", CATS2), ["c1"])
        self.assertIs(expand.value_scope("燃やすごみ（剪定枝を除く）は祝日も収集します", CATS2), expand.AMBIGUOUS)

    def test_mixed_with_known_not_narrowed(self):    # 辞書に無い品目は、資料のどの品目の中か分からない (陶器は燃やさないごみの中かも) → 狭めない
        self.assertIs(expand.value_scope("燃やすごみと陶器類は祝日も収集しません。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("燃やすごみと剪定枝は祝日も収集しません。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("燃やすごみと危険ごみは祝日も収集しません。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("紙おむつは祝日も収集しません。", cats_of("燃やすごみ", "古紙")), expand.AMBIGUOUS)   # 古紙でない
        self.assertIs(expand.value_scope("ガラス食器は祝日も収集しません。", cats_of("燃やすごみ", "びん")), expand.AMBIGUOUS)

    def test_heading_not_widened(self):              # 見出しの品目が辞書に無くても、上の見出しへ進まない
        for h in (("ごみの収集日", "剪定枝"), ("燃やすごみ", "危険ごみ"), ("燃やすごみ", "陶器類の出し方"), ("家庭ごみの収集日",)):
            self.assertIs(expand.value_scope("祝日も収集しません。", CATS2, h), expand.AMBIGUOUS, h)
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", CATS2, ("燃やさないごみ", "剪定枝")), "*")
        self.assertEqual(expand.block_scope("（年末年始は変更あり）", CATS2, ("ごみの収集日", "燃やさないごみ")), ["c2"])   # 今までどおり

    def test_sentences_left_and_block(self):         # 例外を作らず、使われなかった但し書きとして残り、どの品目の系列も止める
        for t in UNKNOWN[:7]:
            ss = sents("祝日も収集します。", t, heading=("ごみの収集日",))
            ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
            self.assertEqual([(e["kind"], e["value"], e["scope"]["category_ids"]) for e in ex], [("holiday_policy", "collected", "*")], t)
            q = unconsumed(ss, used)
            self.assertEqual([x["text"] for x in q], [t])
            quals = [{"qualifier_id": x["qualifier_id"], "months": x["months"]} for x in q]
            for cid in CATS2:
                s = series(cid, ex + [CLOSE], quals)
                self.assertFalse(s["answerable"], (t, cid))
                self.assertIn(f"unconsumed_qualifier:{q[0]['qualifier_id']}", s["blocked_by"])

    def test_p11_3b(self):                           # 「危険ごみは祝日も収集します」だけ → 燃やすごみに「*」の collected を作らない
        ss = sents("年末は12月29日まで、年始は1月4日から収集します。", "危険ごみは祝日も収集します。", heading=("ごみの収集日",))
        ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
        self.assertFalse([e for e in ex if e["kind"] == "holiday_policy"])
        s = series("c1", ex)
        self.assertFalse(s["answerable"])
        self.assertNotIn("2026-07-20", s["dates"])


class TestCellNotesRound2(unittest.TestCase):
    SC = {"category_ids": ["c1"], "district_ids": ["d1"]}

    def notes(self, t, cats=None, sc=None):
        return expand.cell_notes(t, FY, sc or self.SC, {"source_id": "s1"}, cats=cats)

    def test_other_item_left(self):                  # 燃やすごみのマスの「（危険ごみは…）」をそのマスの方針にしない
        for t in ("(危険ごみは祝日の収集は休み)", "(埋立ごみは祝日も収集)", "(剪定枝は祝日も収集)", "(陶器類は祝日休み)", "(家庭ごみは祝日も収集)"):
            for cats in (None, CATS2):
                self.assertEqual(self.notes(t, cats), ([], [t]), (t, cats))

    def test_own_or_none_applied(self):
        cats = cats_of("燃やすごみ", "危険ごみ")
        ex, left = self.notes("(危険ごみは祝日の収集は休み)", cats, {"category_ids": ["c2"], "district_ids": ["d1"]})
        self.assertEqual(([(e["kind"], e["value"]) for e in ex], left), ([("holiday_policy", "not_collected")], []))
        for cats in (None, CATS2):
            ex, left = self.notes("(祝日も収集)", cats)
            self.assertEqual(([(e["kind"], e["value"]) for e in ex], left), ([("holiday_policy", "collected")], []))


class TestNotItemCloth(unittest.TestCase):           # 「配布」「分布」「調布」の「布」は古布でない。「布団」は古布でない (名指しとみなす)
    CATS = cats_of("燃やすごみ", "古布", "ペットボトル")

    def test_named_in(self):
        for t in ("祝日も収集します。カレンダーを配布しています", "分布図", "調布市のごみの収集日", "公布された条例"):
            self.assertEqual(categories.named_in(t, self.CATS)[0], set(), t)
        self.assertEqual(categories.named_in("古布は祝日も収集します", self.CATS), ({"c2"}, set()))
        self.assertEqual(categories.named_in("毛布は祝日も収集します", self.CATS), ({"c2"}, set()))
        self.assertEqual(categories.named_in("布団は祝日も収集しません", self.CATS)[0], set())
        self.assertEqual(categories.named_in("カーペットは祝日も収集しません", self.CATS)[0], set())   # ペットボトルでない

    def test_value_scope(self):
        self.assertEqual(expand.value_scope("祝日も収集します。カレンダーを配布しています", self.CATS), "*")
        self.assertEqual(expand.value_scope("祝日も収集します。", self.CATS, ("調布市のごみの収集日",)), "*")
        self.assertIs(expand.value_scope("布団は祝日も収集しません。", self.CATS), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("カーペットは祝日も収集しません。", self.CATS), expand.AMBIGUOUS)

    def test_coarse(self):
        c = categories.coarse
        self.assertEqual((c("布団"), c("古布・古着"), c("毛布"), c("紙・布"), c("カーペット")),
                         (["other"], ["cloth"], ["cloth"], ["paper", "cloth"], ["other"]))


class TestStage2PageRound2(TestStage2Page):        # 合成のページ: 「危険ごみは祝日も収集しません。」で全部の品目の日付を作らない
    T = "危険ごみは祝日も収集しません。"
    HTML = TestStage2Page.HTML.replace(PET.encode("utf-8"), T.encode("utf-8"))

    def test_page(self):
        self.assertIn(self.T.encode("utf-8"), self.HTML)
        m = stage2.Muni("999999", "テスト市")
        stage2.read_source(m, {"url": "https://example.invalid/p0.html", "file_type": "html", "format_class": "html_table",
                               "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                           types.SimpleNamespace(llm=False, offline=True), [])
        self.assertFalse([e for e in m.exceptions if e["kind"] == "holiday_policy"])
        self.assertIn(self.T, [q["text"] for q in m.sources[0]["extra"]["qualifiers_unconsumed"]])
        self.assertEqual(len(m.schedules), 2)
        for s in m.schedules:
            r = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
            self.assertFalse(r["answerable"], r["blocked_by"])
            self.assertTrue(any(b.startswith("unconsumed_qualifier:") for b in r["blocked_by"]), r["blocked_by"])


# ---- 3 回目: 閉じた見本。主題がそのまま見本に当たり、残りに対比・名前・外し・限りが無いときだけ品目が決まる ----
OTHER_SUBJECTS = ["東地区では祝日も収集しません。", "東地区では、祝日も収集しません。", "集合住宅では祝日も収集します。",
                  "山間部では祝日も収集しません。", "旧○○町地域では祝日も収集しません。", "島しょ部では祝日の収集はありません。",
                  "Aコースでは祝日も収集します。", "Aコースは祝日も収集します。", "Aコースの祝日は休みです。", "東地区の祝日の収集はありません。",
                  "東地区の場合、祝日は収集しません。", "東地区については祝日も収集します。", "東地区に限り祝日も収集します。",
                  "東地区のみ祝日も収集します。", "東地区を除き祝日も収集します。", "東地区以外は祝日も収集しません。",
                  "月曜日コースは祝日も収集します。", "月曜コースでは祝日も収集します。", "山田町は祝日も収集します。", "東地区：祝日も収集します",
                  "ざつがみは祝日も収集しません。", "ふとんは祝日も収集しません。", "かさは祝日も収集しません。", "なべ・やかんは祝日も収集しません。",
                  "せんてい枝は祝日も収集しません。", "えだ・はっぱは祝日も収集しません。", "ぺっとぼとるは祝日も収集しません。",
                  "ぷらは祝日も収集しません。", "あきびんは祝日も収集しません。", "小型金属は祝日も収集しません。", "生ごみは祝日も収集しません。",
                  "雑がみは祝日も収集しません。", "資源物は祝日も収集しません。", "資源ごみは祝日も収集しません。", "プラごみは祝日も収集しません。",
                  "事業系ごみは祝日も収集しません。", "燃やすごみ・びん・かんは祝日も収集します。", "燃やすごみ（剪定枝を含む）は祝日も収集します。",
                  "火曜日の祝日も燃やすごみは収集します。", "日曜日、年末年始（12/29～1/3、8/13～8/15）を除き、祝日も収集します。"]
CONTRASTS = ["祝日は休み（燃やすごみは収集）。", "祝日は休み（燃やすごみは通常収集）。", "祝日は休み、燃やすごみは祝日も収集。",
             "祝日は収集しません（燃やすごみを除く）。", "祝日は休み、燃やすごみを除く。", "祝日は収集しません（燃やすごみ以外）。",
             "祝日も燃やすごみ以外は収集しません。", "祝日は収集なし 燃やすごみのみ収集。", "祝日は休み、燃やすごみのみ収集。",
             "祝日は休み（燃やすごみの収集は行います）。", "祝日は収集しませんが、月曜日の祝日は収集。", "祝日は休み（月曜祝日は収集）。",
             "祝日は収集しません（月曜日を除く）。", "祝日の収集は休み（5月3日～5日を除く）。", "祝日は休み（5/3～5/5は収集）。",
             "祝日は収集しません（海の日は収集）。", "祝日は休み、ただしハッピーマンデーは収集。", "祝日も収集しますが、元日は休みます。",
             "祝日も収集しますが、お盆は休みます。", "年末年始（12/31～1/3、5/3）を除き、祝日も収集します。", "祝日も収集します（山田町）。",
             "祝日も山田町で収集します。", "祝日もざつがみを収集します。", "祝日も危険ごみを収集します。", "祝日でも収集しないことはありません。",
             "祝日も収集する場合があります。", "祝日も収集します（地区により異なります）。", "祝日は収集しません（一部地域を除く）。",
             "祝日も原則収集します。", "祝日は休みです。燃やすごみは祝日も収集します。"]


class TestClosedTemplatesRound3(unittest.TestCase):
    H = ("ごみの収集日",)

    def test_other_subjects_ambiguous(self):       # 地区・住まい・コース・ひらがなの品目・「Xの場合、」「Xについては」・外し・限り
        for t in OTHER_SUBJECTS:
            self.assertIs(expand.value_scope(t, CATS2, self.H), expand.AMBIGUOUS, t)
        for t in OTHER_SUBJECTS[:20]:                # 地区・コースの主題は、どの品目の資料でも
            self.assertIs(expand.value_scope(t, CATS6, self.H), expand.AMBIGUOUS, t)
        # 辞書の分類が資料の 1 つの品目だけに当たれば、その品目 (「あきびん」→「びん・缶」、「雑がみ」→「古紙」)
        self.assertEqual(expand.value_scope("あきびんは祝日も収集しません。", CATS6, self.H), ["c6"])
        self.assertEqual(expand.value_scope("雑がみは祝日も収集しません。", CATS6, self.H), ["c5"])

    def test_contrasts_ambiguous(self):            # 残りの中の対比 (品目・曜日・日付・祝日の名前)・外し・限り・名前・決めていない言い方
        for t in CONTRASTS:
            self.assertIs(expand.value_scope(t, CATS2, self.H), expand.AMBIGUOUS, t)

    def test_left_and_block(self):                 # 例外を作らず、但し書きとして残り、どの品目の系列も止める
        for t in ("東地区では祝日も収集しません。", "Aコースは祝日も収集します。", "ざつがみは祝日も収集しません。", "祝日は休み（燃やすごみは収集）。"):
            ss = sents(t, heading=self.H)
            ex, used, _ = expand.exceptions_from_sentences(ss, FY, CATS2, "s1")
            self.assertEqual((ex, used), ([], []), t)
            q = [x for x in unconsumed(ss, used) if x["text"] == t]
            self.assertTrue(q, t)
            quals = [{"qualifier_id": x["qualifier_id"], "months": x["months"]} for x in q]
            hol = {"exception_id": "h", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集します"}
            for cid in CATS2:
                s = series(cid, [hol, CLOSE], quals)
                self.assertFalse(s["answerable"], (t, cid))

    def test_templates_kept(self):                 # 見本に当たる文は今までどおり
        for t in ("すべてのごみは祝日も収集します。", "各ごみの収集は祝日も行います。", "本市では祝日も収集します。", "祝日もごみを収集します。",
                  "祝日でも、ごみ収集を行います。", "また、祝日も収集します。", "12月29日（火）から1月3日（日）までは、ごみの収集をお休みします。",
                  "年末は12月29日（火）まで、年始は1月4日（月）から収集します。", "祝日も収集します（年末年始を除く）。",
                  "年末年始（12月29日～1月3日）を除き、祝日も収集します。", "◆収集日の朝8時までに出してください（雨天、祝日も収集します）。",
                  "月の「5番目の曜日」に収集はありません。", "※大型連休中もごみ収集は通常通り実施します。"):
            self.assertEqual(expand.value_scope(t, CATS2, self.H), "*", t)
        for t, want in (("燃やさないごみは祝日も収集しません。", ["c2"]), ("可燃物は祝日も収集します。", ["c1"]),
                        ("【燃やすごみ】祝日も収集します", ["c1"]), ("燃やすごみ：祝日も収集します", ["c1"]),
                        ("燃やすごみは、年末は12月30日まで、年始は1月4日から収集します。", ["c1"]),
                        ("燃やすごみ(おむつ)は祝日も収集します。", ["c1"]), ("燃やさないごみ（陶器・ガラス・金属）は祝日も収集します。", ["c2"])):
            self.assertEqual(expand.value_scope(t, CATS2, self.H), want, t)

    def test_object_of_collection(self):           # 「X を/の 収集」の X が資料の品目で、方針の言葉より前に述語が無いときだけ
        k = cats_of("可燃ごみ", "燃やせないごみ")
        self.assertEqual(expand.value_scope("祝日も燃やすごみを収集します。", CATS2), ["c1"])
        self.assertEqual(expand.value_scope("※注意 5月4日(月)、12月31日(木)は、施設の定期点検のため可燃ごみの収集は行いません。", k), ["c1"])
        self.assertEqual(expand.value_scope("収集日が祝日にあたる場合、「燃やせないごみ」の収集はお休みです。", k), ["c2"])
        self.assertIs(expand.value_scope("祝日は休み（燃やすごみの収集は行います）。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("燃やすごみは祝日も燃やさないごみを収集します。", CATS2), expand.AMBIGUOUS)

    def test_line_context(self):                   # かっこ書きの文は、それを含む文の主題を見本で見る
        p = "（祝日も収集）"
        self.assertIs(expand.value_scope(p, CATS2, (), "東地区は月・木（祝日も収集）"), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope(p, CATS2, (), "燃やすごみは月・木（祝日も収集）"), ["c1"])
        self.assertEqual(expand.value_scope("（祝日は休み）", CATS2, (), "燃やすごみは月曜日（祝日は休み）。燃やさないごみは水曜日（祝日も収集）。"),
                         ["c1"])                                                        # 同じ行のほかの文の品目に広げない
        self.assertIs(expand.value_scope("祝日も収集します。", CATS2, (), "東地区は月曜日です。祝日も収集します。"), expand.AMBIGUOUS)

    def test_heading_district_and_generic_conflict(self):
        self.assertIs(expand.value_scope("祝日も収集します。", CATS2, ("ごみの収集日", "東地区")), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("祝日も収集します。", CATS2, ("令和8年度 東地区の収集日",)), "*")   # いちばん上 (ページの題) は呼ぶ側が地区を当てる
        self.assertIs(expand.value_scope("すべてのごみは祝日も収集します。", CATS2, ("燃やすごみ",)), expand.AMBIGUOUS)

    def test_private_use_chars(self):              # 本文の私用領域の字 (PDF の字形) を品目の印と取り違えない
        self.assertIs(expand.value_scope("\ue000は祝日も収集します。", CATS2), expand.AMBIGUOUS)
        self.assertIs(expand.value_scope("\ue001祝日は休み", CATS2), expand.AMBIGUOUS)
        self.assertEqual(expand.value_scope("\uf06c 祝日も収集します。", CATS2), "*")          # 空白の続く字は行頭の印


class TestCellNotesRound3(unittest.TestCase):    # マスの中の文も同じ見本: 主題なしか、そのマスの品目そのものだけ当てる (年末年始の文も)
    SC = {"category_ids": ["c1"], "district_ids": ["d1"]}

    def notes(self, t, cats=CATS2):
        return expand.cell_notes(t, FY, self.SC, {"source_id": "s1"}, cats=cats)

    def test_left(self):
        for t in ("(燃やさないごみは12月29日まで)", "東地区は年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", "(東地区は祝日も収集)",
                  "(ごみは祝日も収集)", "(祝日は休み・燃やすごみは収集)", "(祝日は休み月曜祝日は収集)", "(祝日も収集びん休み)", "月・木祝日も収集"):
            self.assertEqual(self.notes(t), ([], [t]), t)

    def test_applied(self):
        ex, left = self.notes("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から")
        self.assertEqual(([(e["kind"], e["last"], e["first"], e["scope"]) for e in ex], left),
                         ([("year_end", "2026-12-28", "2027-01-04", self.SC)], []))
        ex, left = self.notes("年末は12月28日(月曜日)まで年始は1月4日(月曜日)から", None)
        self.assertEqual(([(e["kind"], e["last"]) for e in ex], left), ([("year_end", "2026-12-28")], []))


if __name__ == "__main__":
    unittest.main()
