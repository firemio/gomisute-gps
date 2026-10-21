"""html_tables.py (2 通りの格子・形の確かめ・表の読み)、markup.py (しるしの棚卸し・空のマス・生の断片・A と B の比べ)、
tabular.py (CSV) (ネットにつながない。実際のページはキャッシュにあるときだけ)"""
import unittest

import _util
import content, decode, html_tables, markup, rules, tabular


def table_of(html):
    soup = content.parse(html)
    return soup.find("table")


BROKEN = """<table><tr><th>種類</th><th>収集の日</th><th>袋</th></tr>
<tr><td rowspan="3">もやせるごみ</td><td>月・木</td><td>指定袋</td></tr>
<tr><td rowspan="2">もやせないごみ</td><td>第2木曜日</td><td>指定袋</td></tr>
<tr><td>資源</td><td>第1水曜日</td><td>なし</td></tr></table>"""


class TestGrids(unittest.TestCase):
    def test_spans_and_two_grids(self):
        t = table_of("<table><tr><th>音</th><th>町名</th><th>燃やすごみ</th></tr>"
                     "<tr><td rowspan='2'>あ</td><td>青葉台</td><td>水・土</td></tr><tr><td>朝志ケ丘</td><td>火・金</td></tr></table>")
        grid, over = html_tables.spec_grid(t)
        self.assertEqual([[c.text for c in r] for r in grid], [["音", "町名", "燃やすごみ"], ["あ", "青葉台", "水・土"],
                                                                ["あ", "朝志ケ丘", "火・金"]])
        self.assertEqual(over, 0)
        self.assertEqual([len(r) for r in html_tables.own_cells(t)], [3, 3, 2])
        self.assertTrue(html_tables.structure(t)["ok"])

    def test_broken_rowspan_detected(self):       # 七ヶ宿の形 (rowspan の数が行と合わない)
        st = html_tables.structure(table_of(BROKEN))
        self.assertFalse(st["ok"])
        self.assertTrue(any("幅" in i or "超える" in i or "はみ出す" in i for i in st["issues"]))

    def test_filesize_stripped(self):             # 能美「(PDF 791KB)」・板橋「（PDF 255.9KB）」
        t = table_of("<table><tr><td><a href='x.pdf'>和気町・松が岡五丁目133-3~21,177~(PDF 832KB)</a></td></tr></table>")
        c = html_tables.own_cells(t)[0][0]
        self.assertEqual(c.text, "和気町・松が岡五丁目133-3~21,177~")
        self.assertEqual(c.stripped, ["(PDF 832KB)"])

    def test_header_rule_fragment_not_weekly(self):   # 三郷「びん、かん（第1・3週）不有資（第2・4週）」の列のマスは曜日だけ
        p = html_tables.rule_with_header("びん、かん\n（第1・3週）\n不有資\n（第2・4週）", "水")
        self.assertEqual(p.status, "multiple")
        p = html_tables.rule_with_header("びん・かん（第1・3週）", "水")
        self.assertEqual(p.status, "ambiguous")
        p = html_tables.rule_with_header("不燃ごみ（月2回）", "2・4回目の月曜日")      # 回数の書き添えは切れ端にしない
        self.assertEqual(p.irs, [{"kind": "monthly_nth", "weekday": "MO", "nth": [2, 4]}])

    def test_orientations(self):
        rd = html_tables.read_table(table_of("<table><tr><th>種別</th><th>指定日</th><th>出し方</th></tr>"
                                             "<tr><th>燃えるごみ</th><td>月・木曜日</td><td>袋</td></tr>"
                                             "<tr><th>金属類</th><td>第1火曜日</td><td>袋</td></tr>"
                                             "<tr><th>粗大ごみ</th><td>自己搬入</td><td>-</td></tr></table>"))
        self.assertEqual(rd["orientation"], "category_rows")
        self.assertEqual(rd["categories"]["粗大ごみ"]["mode"], "dropoff")
        self.assertEqual(rd["rows"][0]["rules"]["金属類"].irs, [{"kind": "monthly_nth", "weekday": "TU", "nth": [1]}])
        rd = html_tables.read_table(table_of("<table><tr><th>曜日</th><th>ごみ・資源物の種類</th></tr>"
                                             "<tr><td>水曜日</td><td>第2・4<br>燃やせないごみ</td></tr>"
                                             "<tr><td>火曜日</td><td>燃やせるごみ</td></tr><tr><td>金曜日</td><td>燃やせるごみ</td></tr></table>"))
        self.assertEqual(rd["orientation"], "weekday_rows")                         # 三鷹
        r = rd["rows"][0]["rules"]
        self.assertEqual(r["燃やせないごみ"].irs, [{"kind": "monthly_nth", "weekday": "WE", "nth": [2, 4]}])
        self.assertEqual(r["燃やせるごみ"].irs, [{"kind": "weekly", "weekdays": ["TU", "FR"]}])


class TestMarkup(unittest.TestCase):
    def inv(self, html, page=""):
        t = table_of(html)
        rd = html_tables.read_table(t)
        return markup.inventory(t, rd, page), rd

    def test_uniform_emphasis_is_styling(self):   # 世田谷 (どのマスも <strong>)
        inv, _ = self.inv("<table><tr><th>町名</th><th>可燃ごみ</th></tr><tr><td><strong>赤堤</strong></td><td><strong>水・土曜日</strong></td></tr>"
                          "<tr><td><strong>池尻</strong></td><td><p><strong>月・</strong></p><p><strong>木曜日</strong></p></td></tr></table>")
        self.assertEqual(markup.unhandled(inv), [])
        self.assertTrue(all(m["rule"] == "uniform_emphasis@1" for m in inv))

    def test_partial_emphasis_unhandled(self):
        inv, _ = self.inv("<table><tr><th>町名</th><th>可燃ごみ</th></tr><tr><td>赤堤</td><td><strong>水</strong>・土曜日</td></tr>"
                          "<tr><td>池尻</td><td>月・木曜日</td></tr></table>")
        self.assertEqual(markup.unhandled(inv), ["strong"])

    def test_italic_needs_page_explanation(self):  # 塩竈の斜体
        html = "<table><tr><th>地区</th><th>資源物</th></tr><tr><td><em>新富町</em></td><td>月曜日</td></tr><tr><td>本町</td><td>月曜日</td></tr></table>"
        inv, _ = self.inv(html)
        self.assertEqual(markup.unhandled(inv), ["em"])
        inv, _ = self.inv(html, "斜体は番地により別の地区があります")
        self.assertEqual([m["rule"] for m in inv if m["kind"] == "em"], ["page_italic_partial@1"])

    def test_blank_cells(self):
        html = "<table><tr><th>町名</th><th>可燃ごみ</th><th>不燃ごみ</th></tr><tr><td>赤堤</td><td>水・土曜日</td><td></td></tr>" \
               "<tr><td>池尻</td><td>月・木曜日</td><td>第1月曜日</td></tr></table>"
        inv, _ = self.inv(html)
        self.assertEqual(markup.unhandled(inv), ["blank"])                    # 黙って「収集なし」と読まない
        inv, _ = self.inv(html, "空欄の日は収集がありません")
        self.assertEqual(markup.unhandled(inv), [])
        inv, _ = self.inv("<table><tr><th>町名</th><th>丁目</th><th>可燃ごみ</th></tr><tr><td>砧公園</td><td></td><td>火・金曜日</td></tr>"
                          "<tr><td>上馬</td><td>1</td><td>月・木曜日</td></tr></table>")
        self.assertEqual([m["rule"] for m in inv if m["kind"] == "blank"], ["blank_chome@1"])   # 世田谷「砧公園」
        # blank_chome@1 は読みを足す決まり (空のマス → 町名の丸ごと)。人が確かめるまで止める理由に残す (§3.6 の 3)
        self.assertEqual(markup.unhandled(inv), ["blank"])
        self.assertEqual([m["rule"] for m in markup.pending_rules(inv)], ["blank_chome@1"])

    def test_readings_applied(self):              # 決まりの読み (partial・rule_contact) を地区と系列に当てる
        html = "<table><tr><th>地区</th><th>資源物</th></tr><tr><td><em>新富町</em>・本町</td><td>月曜日</td></tr>" \
               "<tr><td>東町</td><td>*</td></tr></table>"
        inv, rd = self.inv(html, "斜体は番地により別の地区があります。*のついた地域は清掃事務所に問い合わせてください")
        self.assertEqual(markup.apply_partial(inv, rd), 1)
        ms = {m["extra"]["name_verbatim"]: [c["kind"] for c in m["conditions"]] for m in rd["rows"][0]["members"]}
        self.assertEqual(ms, {"新富町": ["partial"], "本町": []})
        self.assertEqual(markup.contact_rows(inv), {2})

    def test_compare_markup(self):                # しるしの意味も A の決まりの表と B を比べる (§3.6 の 4)
        inv = [{"kind": "blank", "row": 3, "col": 1, "text": "", "rule": "blank_chome@1", "reading": "whole"},
               {"kind": "strong", "row": 1, "col": 1, "text": "x", "rule": "uniform_emphasis@1", "reading": "styling"}]
        self.assertEqual(markup.compare_markup(inv, [{"kind": "空のマス", "meaning": "丁目なし（町名全体として扱った）"}]), {"whole": "agreed"})
        self.assertEqual(markup.compare_markup(inv, [{"kind": "strong", "meaning": "styling"}]), {"whole": "missing"})
        self.assertEqual(markup.compare_markup(inv, [{"kind": "空のマス", "meaning": "その回は収集なし"}]),
                         {"whole": "disputed", "b_only:none": "disputed"})
        self.assertEqual(markup.compare_markup(inv[1:], [{"kind": "*", "meaning": "清掃事務所に問い合わせ"}]),
                         {"b_only:rule_contact": "disputed"})
        self.assertEqual(markup.affected_rows(inv, markup.VALUE_READINGS), {3: {"whole"}})

    def test_district_cell_pdf_is_calendar(self):  # 能美: 地区のマスが地区ごとの PDF へのリンク → その行は pending_printed
        rd = html_tables.read_table(table_of("<table><tr><th>収集地区</th><th>可燃ごみ</th></tr><tr><td><a href='/fs/A.pdf'>赤井町"
                                             "(PDF 791KB)</a></td><td>月曜日・木曜日</td></tr><tr><td><a href='#x'>本町</a></td><td>火曜日</td></tr></table>"))
        self.assertEqual([r["calendar_links"] for r in rd["rows"]], [["/fs/A.pdf"], []])


    def test_none_mark_and_unused_column(self):
        inv, _ = self.inv("<table><tr><th>町名</th><th>不燃ごみ</th><th>出し方の決まり</th></tr><tr><td>本町</td><td>ー</td>"
                          "<td><strong>※70Lは使えます</strong></td></tr><tr><td>新町</td><td>第1水曜日</td><td>袋</td></tr></table>")
        rules_ = {m["rule"] for m in inv}
        self.assertIn("none_mark@1", rules_)
        self.assertIn("unused_column@1", rules_)
        self.assertEqual(markup.unhandled(inv), [])

    def test_raw_fragment_keeps_tags(self):
        t = table_of("<table onclick='x()'><tr><th>地区</th></tr><tr><td><em>新富町</em><script>evil()</script></td></tr>"
                     "<tr><td>本町</td></tr></table>")
        f = markup.raw_fragment(t, rows=[2], n_head=1)
        self.assertIn('data-row="0"', f)
        self.assertNotIn("新富町", f)                               # 選んだ行だけ (見出しの行は毎回付ける)
        self.assertNotIn("script", f)
        self.assertNotIn("onclick", f)
        f = markup.raw_fragment(t)
        self.assertIn("<em>新富町</em>", f)                         # しるしは残す

    def test_compare_table(self):
        rd = html_tables.read_table(table_of("<table><tr><th>町名</th><th>可燃ごみ</th></tr><tr><td>青柳1から3丁目</td><td>月・木</td></tr></table>"))
        b = {"is_schedule_table": True, "orientation": "district_rows",
             "categories": [{"header": "可燃ごみ", "coarse": ["burnable"], "mode": "curbside"}],
             "rows": [{"row": 1, "members": [{"name_verbatim": "青柳", "unit": "machiaza",
                                              "conditions": [{"kind": "range", "unit": "chome", "from": "1", "to": "3"}]}],
                       "rules": [{"category": "可燃ごみ", "text": "月・木", "ir": {"kind": "weekly", "weekdays": ["TH", "MO"]}, "status": "ok"}]}]}
        c = markup.compare_table(rd, b)
        self.assertEqual((c["categories"]["可燃ごみ"], c["members"][1], c["rules"][(1, "可燃ごみ")]), ("agreed", "agreed", "agreed"))
        b["rows"][0]["rules"][0]["ir"] = {"kind": "monthly_nth", "weekday": "MO", "nth": [1, 3]}
        self.assertEqual(markup.compare_table(rd, b)["rules"][(1, "可燃ごみ")], "disputed")
        self.assertEqual(markup.compare_table(rd, {})["table"], "missing")


class TestText(unittest.TestCase):         # 型 C (HTML の文)
    HTML = ("<main><h1>地区のごみ収集日</h1><h2>藤蔵・中島</h2><p>令和８年４月より下記のとおり収集日を変更します。<br>"
            "【可燃物（燃えるごみ）】 毎週 月曜日・木曜日<br>【不燃物（燃えないごみ）】 第３金曜日</p>"
            "<h2>壱ツ屋・与九郎島</h2><p>【可燃物（燃えるごみ）】 毎週 火曜日・金曜日<br>【粗大ごみ】 持込 第2日曜日</p>"
            "<h2>注意事項</h2><p>※資源の収集日は、第３火曜日です。</p></main>")

    def read(self, html):
        reg = content.content_region(html)
        return html_tables.read_text(reg.lines())

    def test_sections_by_district(self):          # 川北: 地区の見出しごとに行。地区をまたいで毎週の規則をまとめない
        rd = self.read(self.HTML)
        self.assertEqual(rd["orientation"], "text_sections")
        rows = {r["district_text"]: r for r in rd["rows"]}
        self.assertEqual(sorted(rows), ["壱ツ屋・与九郎島", "藤蔵・中島"])
        self.assertEqual(rows["藤蔵・中島"]["rules"]["可燃物(燃えるごみ)"].irs, [{"kind": "weekly", "weekdays": ["MO", "TH"]}])
        self.assertEqual(rows["壱ツ屋・与九郎島"]["rules"]["可燃物(燃えるごみ)"].irs, [{"kind": "weekly", "weekdays": ["TU", "FR"]}])
        self.assertTrue("粗大ごみ" not in rd["categories"] or rd["categories"]["粗大ごみ"]["mode"] == "dropoff")   # 持込は日付にしない
        self.assertTrue(all(r["district_from"] == "heading" for r in rd["rows"]))

    def test_same_district_two_sections_differ(self):
        rd = self.read("<main><h2>燃やすごみ</h2><p>毎週月曜日</p><h2>ごみの出し方</h2><p>燃やすごみ：毎週火曜日</p></main>")
        self.assertEqual(rd["rows"][0]["rules"]["燃やすごみ"].status, "multiple")

    def test_listed_and_noise(self):
        rd = self.read("<main><h2>不燃ごみ</h2><p>4月21日、5月19日、6月9日</p><h2>粗大ごみ</h2><p>電話で申し込んでください</p></main>")
        self.assertEqual(rd["rows"][0]["listed"], {"不燃ごみ": [(4, 21), (5, 19), (6, 9)]})
        self.assertNotIn("粗大ごみ", rd["categories"])                                     # 申し込みの説明は規則にしない
        self.assertEqual(self.read("<main><h2>ごみパックン</h2><p>6月5日、9月5日、12月5日</p></main>")["orientation"], "other")

    def test_compare_text_and_page_district(self):
        rd = self.read(self.HTML)
        for r in rd["rows"]:
            r["members"] = __import__("members").parse_cell(r["district_text"])
        b = {"page_district": {"scope": "municipality", "members": None},
             "sections": [{"district": "藤蔵・中島", "category": "可燃物（燃えるごみ）", "coarse": ["burnable"], "mode": "curbside",
                           "district_members": [{"name_verbatim": "藤蔵", "unit": "machiaza", "conditions": []},
                                                {"name_verbatim": "中島", "unit": "machiaza", "conditions": []}],
                           "ir": {"kind": "weekly", "weekdays": ["MO", "TH"]}, "status": "ok"}]}
        c = markup.compare_text(rd, b)
        row = next(r for r in rd["rows"] if r["district_text"] == "藤蔵・中島")
        self.assertEqual(c["rules"][(row["row"], "可燃物(燃えるごみ)")], "agreed")
        self.assertEqual(c["members"][row["row"]], "agreed")
        other = next(r for r in rd["rows"] if r["district_text"] != "藤蔵・中島")
        self.assertEqual(c["rules"][(other["row"], "可燃物(燃えるごみ)")], "missing")
        self.assertEqual(markup.compare_page_district("", [], b), "agreed")
        self.assertEqual(markup.compare_page_district("大沢3丁目", [], b), "disputed")
        self.assertEqual(markup.compare_page_district("", [], None), "missing")


class TestTabular(unittest.TestCase):
    def test_csv_title_row_and_bom(self):
        body = "﻿資源・ごみ収集日（朝8時までに）,,,\n50音,町名,丁目,可燃ごみ（週2回）,不燃ごみ（月2回）\nあ,赤堤,1・3～5,水曜日・土曜日,2回目・4回目の月曜日\n"
        rows, rec = tabular.read_csv(body.encode("utf-8"), "text/csv; charset=Shift_JIS")
        self.assertEqual(rec["how"], "bom")
        self.assertTrue(rec["conflicts"])                           # 見出しと BOM のぶつかりを記録
        rd = tabular.read_rows(rows)
        self.assertEqual(rd["header_index"], 1)
        r = rd["rows"][0]
        self.assertEqual(r["row"], 2)
        self.assertEqual(r["rules"]["不燃ごみ（月2回）"].irs, [{"kind": "monthly_nth", "weekday": "MO", "nth": [2, 4]}])

    def test_excel_merged_hidden_old_year(self):   # 結合したマスを広げ、隠しシートと期間の外の年のシートを外す
        import io, openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "令和8年度"
        ws.append(["収集日一覧", None, None])
        ws.append(["町名", "丁目", "可燃ごみ"])
        ws.append(["柏原", "1", "月・木"])
        ws.append([None, "2", "火・金"])
        ws.merge_cells("A3:A4")
        old = wb.create_sheet("令和7年度")
        old.append(["町名", "丁目", "可燃ごみ"])
        hid = wb.create_sheet("作業用")
        hid.sheet_state = "hidden"
        buf = io.BytesIO()
        wb.save(buf)
        sheets, skipped = tabular.read_excel(buf.getvalue(), {"valid_from": "2026-04-01", "valid_to": "2027-03-31"})
        self.assertEqual([x["sheet"] for x in sheets], ["令和8年度"])
        self.assertEqual(sorted(x["why"] for x in skipped), ["hidden", "year_token_outside_period"])
        rd = tabular.read_rows(sheets[0]["rows"])
        self.assertEqual([r["district_text"] for r in rd["rows"]], ["柏原 1", "柏原 2"])     # 結合した町名を下の行にも
        keep, sk = tabular.drop_old_sheets([{"sheet": "令和8年度"}, {"sheet": "令和7年度"}, {"sheet": "柏原"}],
                                           {"valid_from": "2026-04-01", "valid_to": "2027-03-31"})
        self.assertEqual(([x["sheet"] for x in keep], [x["sheet"] for x in sk]), (["令和8年度", "柏原"], ["令和7年度"]))
        self.assertEqual(len(tabular.drop_old_sheets([{"sheet": "令和7年度"}], None)[0]), 1)     # 期間が無ければ外さない

    def test_xls_merged_cells_read(self):          # .xls の結合したマスは formatting_info=True のときだけ xlrd が読む
        import sys, types
        seen = {}

        class Sheet:
            name, nrows, ncols, visibility = "令和8年度", 3, 2, 0
            merged_cells = [(1, 3, 0, 1)]
            vals = [["町名", "可燃ごみ"], ["柏原", "月・木"], ["", "火・金"]]

            def cell_value(self, r, c):
                return self.vals[r][c]

        def open_workbook(file_contents=None, formatting_info=False):
            seen["formatting_info"] = formatting_info
            return types.SimpleNamespace(sheets=lambda: [Sheet()])
        fake = types.ModuleType("xlrd")
        fake.open_workbook = open_workbook
        old = sys.modules.get("xlrd")
        sys.modules["xlrd"] = fake
        try:
            sheets, _ = tabular.read_excel(b"\xd0\xcf\x11\xe0" + b"\0" * 8)
        finally:
            if old is not None:
                sys.modules["xlrd"] = old
            else:
                del sys.modules["xlrd"]
        self.assertTrue(seen["formatting_info"])
        self.assertEqual([r[0] for r in sheets[0]["rows"]], ["町名", "柏原", "柏原"])

    def test_cached_csvs(self):
        for url, n in (("https://www.city.setagaya.lg.jp/documents/416/27-08-26.csv", 118),
                       ("https://www.city.suginami.tokyo.jp/documents/12125/garbage.csv", 52)):
            b = _util.cached(url)
            if b is None:
                self.skipTest("キャッシュに無い")
            rows, rec = tabular.read_csv(b)
            rd = tabular.read_rows(rows)
            self.assertEqual(rd["orientation"], "district_rows")
            self.assertEqual(len(rd["rows"]), n)
            st = {p.status for r in rd["rows"] for p in r["rules"].values()}
            self.assertEqual(st, {"ok"}, url)


class TestCachedPages(unittest.TestCase):
    def read(self, url):
        b = _util.cached(url)
        if b is None:
            self.skipTest("キャッシュに無い")
        reg = content.content_region(decode.decode(b, "text/html").text)
        return reg, [html_tables.read_table(t) for _, t in html_tables.tables(reg.root)]

    def test_asaka(self):
        reg, rds = self.read("https://www.city.asaka.lg.jp/soshiki/15/dust-syuusyuu.html")
        rd = rds[0]
        self.assertEqual(rd["orientation"], "district_rows")
        self.assertEqual(len(rd["rows"]), 38)
        self.assertEqual(rd["roles"], ["index", "district", "category", "category", "category"])

    def test_setagaya_html_equals_csv(self):        # 同じ資料を 2 つの形式で出している所は資料どうしも比べる (§3.11)
        reg, rds = self.read("https://www.city.setagaya.lg.jp/02241/416.html")
        b = _util.cached("https://www.city.setagaya.lg.jp/documents/416/27-08-26.csv")
        if b is None:
            self.skipTest("キャッシュに無い")
        csv_rd = tabular.read_rows(tabular.read_csv(b)[0])
        key = lambda r: content.squash(r["district_text"])
        a = {(key(r), content.squash(k)): rules.canon(p.ir) for rd in rds for r in rd.get("rows", []) for k, p in r["rules"].items()}
        c = {(key(r), content.squash(k)): rules.canon(p.ir) for r in csv_rd["rows"] for k, p in r["rules"].items()}
        self.assertEqual(len(a), 472)
        self.assertEqual(a, c)


if __name__ == "__main__":
    unittest.main()
