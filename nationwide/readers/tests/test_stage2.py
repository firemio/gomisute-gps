"""eval/stage2.py の読みの組み立て: マスの但し書き、資料どうしの比べ、地区を分ける注、しるしの読み、出し方の決まらない品目、
ページの地区の 2 つ目の読み (ネットにつながない。ページは合成、LLM の答えも合成)"""
import types, unittest

import _util  # noqa: F401
import expand, fetch_docs, markup, stage2

TITLE = "<title>令和8年度 ごみ収集日</title>"


def page(body, title=TITLE):
    return f"<html><head>{title}</head><body><main>{body}</main></body></html>".encode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.pages = {}
        self._body, self._bp = stage2.body_of, fetch_docs.load_boilerplate
        self._rb, self._rtb = markup.reader_b, markup.reader_text_b
        stage2.body_of = lambda url: (self.pages.get(url), {"headers": {"content-type": "text/html; charset=utf-8"}})
        fetch_docs.load_boilerplate = lambda host: None

    def tearDown(self):
        stage2.body_of, fetch_docs.load_boilerplate = self._body, self._bp
        markup.reader_b, markup.reader_text_b = self._rb, self._rtb

    def run_pages(self, *htmls, llm=False):
        m = stage2.Muni("999999", "テスト市")
        a = types.SimpleNamespace(llm=llm, offline=True)
        for i, h in enumerate(htmls):
            url = f"https://example.invalid/p{i}.html"
            self.pages[url] = h
            stage2.read_source(m, {"url": url, "file_type": "html", "format_class": "html_table",
                                   "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"}, a, [])
        return m


class TestCells(Base):
    HTML = page("<h1>令和8年度 ごみの収集日</h1><p>祝日も収集します。</p><p>年末は12月29日まで、年始は1月4日から収集します。</p>"
                "<table><tr><th>地区</th><th>燃やすごみ</th><th>不燃ごみ</th></tr>"
                "<tr><td>本町</td><td>火曜日・金曜日 ※第5週は休み</td><td>水曜日（月2回）</td></tr>"
                "<tr><td>栄町</td><td>月曜日・木曜日（祝日の収集は休み）</td><td>第2水曜日</td></tr></table>")

    def test_cell_notes_are_not_consumed_silently(self):
        m = self.run_pages(self.HTML)
        by = {(s["ref"]["row"], s["rule"]["text"]): s for s in m.schedules}
        q5 = expand.rest_qualifier_id("※第5週は休み")
        self.assertIn(q5, [q["qualifier_id"] for q in by[(1, "火曜日・金曜日 ※第5週は休み")]["qualifiers"]])
        self.assertIn(expand.rest_qualifier_id("(月2回)"), [q["qualifier_id"] for q in by[(1, "水曜日（月2回）")]["qualifiers"]])
        unc = [q["text"] for q in m.sources[0]["extra"]["qualifiers_unconsumed"]]
        self.assertIn("※第5週は休み", unc)                       # 表の中の但し書きとして、その表の品目すべてを止める
        for s in m.schedules:
            r = expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))
            self.assertFalse(r["answerable"], (s["rule"]["text"], r["blocked_by"]))

    def test_cell_holiday_only_for_that_cell(self):   # §3.8「マスの中の（祝日の収集は休み）は、その品目の holiday_policy」
        m = self.run_pages(self.HTML)
        hps = [e for e in m.exceptions if e["kind"] == "holiday_policy"]
        page_hp = [e for e in hps if e["text"] == "祝日も収集します。"]
        cell_hp = [e for e in hps if "休み" in e["text"]]
        self.assertEqual(len(page_hp), 1)
        self.assertEqual(len(cell_hp), 1)
        sakae = next(s for s in m.schedules if s["ref"]["row"] == 2 and "月曜日" in s["rule"]["text"])
        self.assertEqual(cell_hp[0]["scope"], {"category_ids": [sakae["category_id"]], "district_ids": [sakae["district_id"]]})
        r = expand.expand_series(dict(sakae, extra_blocked=[], provenance={"status": "agreed"}))
        self.assertIn("holiday_policy_missing", r["blocked_by"])       # ページの「祝日も収集」とぶつかる → 答えない
        other = next(s for s in m.schedules if s["ref"]["row"] == 2 and s["rule"]["text"] == "第2水曜日")
        self.assertNotIn(cell_hp[0]["exception_id"], [e["exception_id"] for e in other["exceptions"]
                                                      if expand.in_scope(e, other["category_id"], other["district_id"])])


class TestSources(Base):
    def table(self, rule, label="燃やすごみ"):
        return page(f"<h1>令和8年度 ごみの収集日</h1><table><tr><th>地区</th><th>{label}</th></tr><tr><td>本町</td><td>{rule}</td></tr></table>")

    def test_disagreeing_source_disputes_first(self):  # 2 つ目の資料が違えば、先の系列を裁定待ちに (数えるだけにしない)
        m = self.run_pages(self.table("月曜日・木曜日"), self.table("火曜日・金曜日"))
        self.assertEqual(len(m.schedules), 1)
        s = m.schedules[0]
        self.assertEqual(s["provenance"]["status"], "disputed")
        self.assertTrue(s["disputes"] and s["disputes"][0].startswith("999999:"))
        self.assertEqual([q["kind"] for q in m.queue], ["rule"])
        self.assertEqual(dict(m.dup), {"disputed": 1})
        m = self.run_pages(self.table("月曜日・木曜日"), self.table("月・木"))
        self.assertEqual((len(m.schedules), dict(m.dup), m.schedules[0]["disputes"]), (1, {"agreed": 1}, []))
        m = self.run_pages(self.table("月曜日・木曜日"), self.table("月・木 ※第5週は休み"))     # 重なった資料の但し書きも先の系列に
        self.assertIn(expand.rest_qualifier_id("※第5週は休み"), [q["qualifier_id"] for q in m.schedules[0]["qualifiers"]])

    def test_same_coarse_categories_not_merged(self):   # 分類が同じ別の品目 (東秩父のプラスチック 2 つ) を 1 つにまとめて落とさない
        two = page("<h1>令和8年度</h1><table><tr><th>地区</th><th>プラスチック資源</th><th>プラ製容器包装</th></tr>"
                   "<tr><td>本町</td><td>第1水曜日</td><td>第3水曜日</td></tr></table>")
        m = self.run_pages(two, self.table("第2水曜日", "容器包装プラスチック"))
        self.assertEqual(len(m.schedules), 3)
        m = self.run_pages(two, self.table("第3水曜日", "プラ製容器包装"))   # 同じ品目 (同じ名前) は比べる
        self.assertEqual((len(m.schedules), dict(m.dup)), (2, {"agreed": 1}))

    def test_mode_undecided_not_reservation(self):
        m = self.run_pages(page("<h1>令和8年度</h1><table><tr><th>地区</th><th>燃やすごみ</th><th>粗大ごみ</th></tr>"
                                "<tr><td>本町</td><td>月曜日</td><td>第1金曜日</td></tr></table>"))
        doc = stage2.build_doc(m)
        self.assertEqual([c["official_label"] for c in doc["categories"]], ["燃やすごみ"])
        self.assertEqual([x["official_label"] for x in doc["build"]["categories_mode_undecided"]], ["粗大ごみ"])
        self.assertIn("mode", [q["kind"] for q in m.queue])


class TestInline(Base):
    def test_split_row(self):                     # 能美「第1・第3木曜日 注：松が岡 第2・第4木曜日」→ 地区を分ける
        m = self.run_pages(page("<h1>令和8年度</h1><table><tr><th>収集地区</th><th>可燃ごみ</th><th>容器包装プラ</th></tr>"
                                "<tr><td>和気町・松が岡五丁目133-3～21</td><td>火曜日・金曜日</td>"
                                "<td>第1・第3木曜日<br>注：松が岡<br>第2・第4木曜日</td></tr></table>"))
        doc = stage2.build_doc(m)
        ds = {d["district_id"]: [x["extra"]["name_verbatim"] for x in d["members"]] for d in doc["districts"]}
        self.assertEqual(sorted(ds.values()), [["和気町"], ["松が岡"]])
        ir = {(s["district_id"][-2:], s["rule"]["text"][:2]): s["rule"]["ir"] for s in doc["schedules"]}
        self.assertEqual(ir[("i1", "注:")], {"kind": "monthly_nth", "weekday": "TH", "nth": [2, 4]})
        self.assertEqual(ir[("-x", "第1")], {"kind": "monthly_nth", "weekday": "TH", "nth": [1, 3]})
        self.assertEqual(sum(1 for s in doc["schedules"] if s["rule"]["text"] == "火曜日・金曜日"), 2)   # ほかの品目は両方の地区に
        self.assertTrue(any(e["kind"] == "inline" for e in doc["exceptions"]))

    def test_unsplittable_is_unparsed(self):      # 名前がどの中身にも当たらなければ分けず、読めないもの (rule_unparsed)
        m = self.run_pages(page("<h1>令和8年度</h1><table><tr><th>収集地区</th><th>容器包装プラ</th></tr>"
                                "<tr><td>和気町</td><td>第1・第3木曜日 注：松が岡 第2・第4木曜日</td></tr></table>"))
        s = expand.expand_series(m.schedules[0])
        self.assertIn("rule_unparsed", s["blocked_by"])


class TestSecondReadings(Base):
    PAGE = page("<h1>令和8年度 大沢3丁目地区</h1><table><tr><th>種別</th><th>指定日</th></tr>"
                "<tr><th>燃えるごみ</th><td>月・木曜日</td></tr></table>")

    def fake_b(self, page_district):
        b_table = {"is_schedule_table": True, "orientation": "category_rows",
                   "categories": [{"header": "燃えるごみ", "coarse": ["burnable"], "mode": "curbside"}],
                   "rows": [{"row": 1, "members": None, "rules": [{"category": "燃えるごみ", "text": "月・木曜日",
                                                                  "ir": {"kind": "weekly", "weekdays": ["MO", "TH"]}, "status": "ok"}]}],
                   "markup": []}
        markup.reader_b = lambda *a, **k: ([b_table], [{"cached": True}])
        markup.reader_text_b = lambda *a, **k: ({"page_district": page_district, "sections": []}, {"cached": True})

    def test_page_district_needs_second_reading(self):   # 題の地区 (三鷹) は B が読まなければ agreed にしない
        self.fake_b(None)
        m = self.run_pages(self.PAGE, llm=True)
        self.assertEqual(m.schedules[0]["provenance"]["status"], "single")
        self.fake_b({"scope": "district", "members": [{"name_verbatim": "大沢", "unit": "machiaza",
                                                       "conditions": [{"kind": "range", "unit": "chome", "from": "3", "to": "3"}]}]})
        m = self.run_pages(self.PAGE, llm=True)
        self.assertEqual(m.schedules[0]["provenance"]["status"], "agreed")
        self.fake_b({"scope": "municipality", "members": None})
        m = self.run_pages(self.PAGE, llm=True)
        self.assertEqual(m.schedules[0]["provenance"]["status"], "disputed")

    def test_contact_rows(self):                   # 新宿の「*」の行は種類 3
        m = self.run_pages(page("<h1>令和8年度</h1><p>収集日欄に「*」マークがついている地域は清掃事務所に問い合わせてください。</p>"
                                "<table><tr><th>町名</th><th>燃やすごみ</th><th>資源</th></tr><tr><td>本町</td><td>*</td><td>*</td></tr>"
                                "<tr><td>新町</td><td>月・木</td><td>水</td></tr></table>"))
        doc = stage2.build_doc(m)
        contact = doc["coverage"]["by_district"]
        self.assertEqual(len(contact), 1)
        for s in doc["schedules"]:
            if s["district_id"] in contact:
                self.assertIn("rule_unparsed", s["blocked_by"])
                self.assertEqual(s["extra"]["answer_type"], "rule_contact")


if __name__ == "__main__":
    unittest.main()
