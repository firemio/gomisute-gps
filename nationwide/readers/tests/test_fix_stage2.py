"""eval/stage2.py の直し: 地区・品目の名前のマスの書き添えが但し書きの関門 (§3.8) を通ること、資料どうしで重なって落とした系列の
止める材料 (deferred・但し書き・マスの例外) が残った系列に掛かること (ネットにつながない。ページ・CSV は合成、LLM は使わない)"""
import types, unittest

import _util  # noqa: F401
import expand, fetch_docs, schema, stage2

TITLE = "<title>令和8年度 ごみ収集日</title>"
H = "<h1>令和8年度 ごみの収集日</h1>"
Q_HOL = expand.rest_qualifier_id("※祝日は収集しません")


def page(body, title=TITLE):
    return f"<html><head>{title}</head><body><main>{body}</main></body></html>".encode("utf-8")


def table(head, *rows):
    tr = lambda cells, tag: "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
    return "<table>" + tr(head, "th") + "".join(tr(r, "td") for r in rows) + "</table>"


class Base(unittest.TestCase):
    def setUp(self):
        self.pages = {}
        self._body, self._bp = stage2.body_of, fetch_docs.load_boilerplate
        stage2.body_of = lambda url: (self.pages.get(url)[0], {"headers": {"content-type": self.pages.get(url)[1]}}) \
            if url in self.pages else (None, None)
        fetch_docs.load_boilerplate = lambda host: None

    def tearDown(self):
        stage2.body_of, fetch_docs.load_boilerplate = self._body, self._bp

    def run_docs(self, *docs):
        """docs: HTML の bytes か ("csv", 文字)。順に読む"""
        m = stage2.Muni("999999", "テスト市")
        a = types.SimpleNamespace(llm=False, offline=True)
        for i, d in enumerate(docs):
            csv = isinstance(d, tuple)
            url = f"https://example.invalid/p{i}." + ("csv" if csv else "html")
            self.pages[url] = (d[1].encode("utf-8"), "text/csv; charset=utf-8") if csv else (d, "text/html; charset=utf-8")
            stage2.read_source(m, {"url": url, "file_type": "csv" if csv else "html", "format_class": "html_table",
                                   "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"}, a, [])
        return m

    def series(self, m):
        """{(地区の見出し, 品目の名前): 系列の材料}"""
        lab = {d["district_id"]: d["label"] for d in m.districts}
        return {(lab[s["district_id"]], m.categories[s["category_id"]]["official_label"]): s for s in m.schedules}

    def blocked(self, s):
        return expand.expand_series(dict(s, extra_blocked=[], provenance={"status": "agreed"}))["blocked_by"]

    def qids(self, s):
        return [q["qualifier_id"] for q in s["qualifiers"]]

    def schema_errors(self, m):
        return [e for e in schema.validate(stage2.build_doc(m)) if "検査数字" not in e]   # 合成の団体コード


class TestNameCells(Base):
    def test_district_note_blocks_that_district_only(self):     # 「東町（※祝日は収集しません）」をマスごと使ったことにしない
        m = self.run_docs(page(H + "<p>祝日も収集します。</p>" + table(
            ["地区", "燃やすごみ", "不燃ごみ"], ["東町（※祝日は収集しません）", "月曜日・木曜日", "第2水曜日"],
            ["西町", "火曜日・金曜日", "第4水曜日"])))
        by = self.series(m)
        self.assertEqual(len(by), 4)
        for (d, c), s in by.items():
            if d.startswith("東町"):
                self.assertIn(Q_HOL, self.qids(s), c)
                self.assertIn(f"unconsumed_qualifier:{Q_HOL}", self.blocked(s), c)
            else:
                self.assertEqual(self.qids(s), [], c)
                self.assertFalse([b for b in self.blocked(s) if b.startswith("unconsumed_qualifier")], c)
        # 地区のマスの文をページの方針 (全地区・全品目の祝日の扱い) にしない。資料すべてを止める但し書きにもしない
        self.assertEqual([e["text"] for e in m.exceptions], ["祝日も収集します。"])
        ex = m.sources[0]["extra"]
        self.assertEqual(ex["qualifiers_unconsumed"], [])
        self.assertEqual([q["text"] for q in ex["qualifiers_name_cells"]], ["※祝日は収集しません"])

    def test_district_note_forms(self):                        # かっこが無くても、2 行目でも
        for cell in ("東町 ※祝日は収集しません", "東町<br>※祝日は収集しません", "東町※祝日は収集しません"):
            with self.subTest(cell=cell):
                m = self.run_docs(page(H + table(["地区", "燃やすごみ"], [cell, "月曜日・木曜日"], ["西町", "火曜日・金曜日"])))
                by = {d[:2]: s for (d, _), s in self.series(m).items()}
                self.assertIn(Q_HOL, self.qids(by["東町"]))
                self.assertEqual(self.qids(by["西町"]), [])

    def test_parsed_member_text_not_blocked(self):              # 地区の文法が読んだ条件・名前の中の字は但し書きにしない
        m = self.run_docs(page(H + table(["地区", "燃やすごみ"], ["旭町1から3丁目（旭町団地を除く）", "月曜日・木曜日"],
                                         ["夏見", "火曜日・金曜日"], ["祝園", "水曜日・土曜日"], ["あけぼの町（上記以外）", "月曜日・木曜日"],
                                         ["大字浜崎の一部（県道和光志木線北側）", "火曜日・金曜日"],
                                         ["本町（祝日を除く）", "火曜日・金曜日"])))
        for (d, _), s in self.series(m).items():
            if d.startswith("本町"):                            # 「祝日を除く」は地区の条件ではなく、日付の但し書き
                self.assertEqual([q["text"] for q in m.sources[0]["extra"]["qualifiers_name_cells"]], ["祝日を除く"])
                self.assertTrue(self.qids(s), d)
            else:
                self.assertEqual(self.qids(s), [], d)
        self.assertEqual(m.sources[0]["extra"]["qualifiers_unconsumed"], [])

    def test_header_label_note_blocks_that_category(self):    # 見出し「燃やせるごみ ※…」の注は、その品目の系列だけ
        m = self.run_docs(page(H + "<p>祝日も収集します。</p>" + table(
            ["地区", "燃やせるごみ<br>※祝日は収集しません", "不燃ごみ ※年末は別日程"], ["東町", "月曜日・木曜日", "第2水曜日"],
            ["西町", "火曜日・金曜日", "第4水曜日"])))
        by = self.series(m)
        q12 = expand.rest_qualifier_id("※年末は別日程")
        for (d, c), s in by.items():
            if c.startswith("燃やせる"):
                self.assertEqual(self.qids(s), [Q_HOL], d)
                self.assertIn(f"unconsumed_qualifier:{Q_HOL}", self.blocked(s))
            else:                                               # 月を名指しした但し書き: 12 月だけ不明 (§3.8 の 4)
                self.assertEqual(s["qualifiers"], [{"qualifier_id": q12, "months": [12]}], d)
        self.assertEqual([e["text"] for e in m.exceptions], ["祝日も収集します。"])
        self.assertEqual(m.sources[0]["extra"]["qualifiers_unconsumed"], [])

    def test_weekday_and_category_rows_label_cells(self):
        m = self.run_docs(page(H + table(["曜日", "ごみの種類"], ["月曜日", "燃やすごみ<br>※祝日は収集しません"], ["水曜日", "びん・缶"],
                                         ["金曜日", "第2・4 不燃ごみ"])))
        for (_, c), s in self.series(m).items():               # 注の行と同じマスの品目は止め、ほかのマスの品目は止めない
            self.assertEqual(self.qids(s), [Q_HOL] if c in ("燃やすごみ", "※祝日は収集しません") else [], c)
        m = self.run_docs(page(H + table(["種別", "収集日"], ["燃やすごみ（※祝日は収集しません）", "月・木曜日"], ["不燃ごみ", "第2水曜日"])))
        for (_, c), s in self.series(m).items():
            self.assertEqual(self.qids(s), [Q_HOL] if c.startswith("燃やす") else [], c)

    def test_csv_name_cells(self):                             # CSV には文が無い (洗い出しに出ない) ので、マスの文字から
        m = self.run_docs(("csv", "地区,燃やすごみ ※祝日は収集しません,不燃ごみ\n東町（※年末年始は休み）,月曜日・木曜日,第2水曜日\n"
                                  "西町,火曜日・金曜日,第4水曜日\n"))
        qye = {"qualifier_id": expand.rest_qualifier_id("※年末年始は休み"), "months": [1, 12]}
        for (d, c), s in self.series(m).items():
            want = ([qye] if d.startswith("東町") else []) + \
                ([{"qualifier_id": Q_HOL, "months": []}] if c.startswith("燃やす") else [])
            self.assertEqual(sorted(s["qualifiers"], key=str), sorted(want, key=str), (d, c))

    def test_text_sections_heading_and_label(self):          # 型 C: 地区の見出しと行の頭の品目の書き添え
        q = expand.rest_qualifier_id("※祝日はお休み")
        m = self.run_docs(page(H + "<h2>東町（※祝日はお休み）</h2><p>燃やすごみ：毎週月曜日・木曜日</p>"
                                   "<h2>西町</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"))
        for (d, _), s in self.series(m).items():
            self.assertEqual(self.qids(s), [q] if d.startswith("東町") else [], d)
        self.assertEqual(m.exceptions, [])                    # 見出しの注をページの方針にしない
        m = self.run_docs(page(H + "<p>【燃やすごみ ※祝日はお休み】 毎週月曜日・木曜日</p><p>【不燃ごみ】 第2水曜日</p>"))
        for (_, c), s in self.series(m).items():
            self.assertEqual(self.qids(s), [q] if c.startswith("燃やす") else [], c)

    def test_text_heading_over_table_stays_in_scan(self):   # 地区の見出しの下に表もあれば、注は表の系列にも掛かりうる → 洗い出しに残す
        m = self.run_docs(page(H + "<h2>東町（※祝日はお休み）</h2><p>燃やすごみ：毎週月曜日・木曜日</p>" +
                               table(["種別", "収集日"], ["不燃ごみ", "第2水曜日"]) + "<h2>西町</h2><p>燃やすごみ：毎週火曜日・金曜日</p>"))
        self.assertEqual([x["text"] for x in m.sources[0]["extra"]["qualifiers_unconsumed"]], ["東町（※祝日はお休み）"])
        by = self.series(m)
        self.assertIn(expand.rest_qualifier_id("東町（※祝日はお休み）"), self.qids(by[("テスト市", "不燃ごみ")]))
        self.assertEqual(m.exceptions, [])

    def test_unmatched_cell_text_stops_source(self):         # マスの文字に無い印 (図の alt) は、どこに掛かるか決まらない → 資料すべて
        m = self.run_docs(page(H + table(["地区", "燃やすごみ"], ['東町<img alt="※祝日は収集しません" src="x.png">', "月曜日・木曜日"],
                                         ["西町", "火曜日・金曜日"])))
        self.assertEqual([q["text"] for q in m.sources[0]["extra"]["qualifiers_unconsumed"]], ["※祝日は収集しません"])
        for k, s in self.series(m).items():
            self.assertIn(Q_HOL, self.qids(s), k)


class TestDuplicates(Base):
    A = page(H + "<p>祝日も収集します。</p>" + table(["地区", "燃やすごみ", "不燃ごみ"], ["本町", "月曜日・木曜日", "第2水曜日"]))

    def test_later_source_deferred_and_qualifiers_carried(self):
        b = page(H + "<p>年末年始の収集日は決まり次第お知らせします。</p><p>※一部の集積所を除く。</p>" +
                 table(["地区", "燃やすごみ", "備考"], ["本町", "月曜日・木曜日", "※臨時休止あり"]))
        m = self.run_docs(self.A, b)
        by = self.series(m)
        self.assertEqual(len(by), 2)
        self.assertEqual(dict(m.dup), {"agreed": 1})
        s, other = by[("本町", "燃やすごみ")], by[("本町", "不燃ごみ")]
        mine = [e for e in s["exceptions"] if expand.in_scope(e, s["category_id"], s["district_id"])]
        dfr = [e for e in mine if e["kind"] == "deferred"]
        self.assertEqual(len(dfr), 1)
        self.assertEqual(dfr[0]["extra"]["carried_from"]["source_id"], "s2")
        self.assertEqual(dfr[0]["scope"], {"category_ids": [s["category_id"]], "district_ids": [s["district_id"]]})
        for t in ("※一部の集積所を除く。", "※臨時休止あり"):     # ページの但し書き・表の中の但し書き
            self.assertIn(expand.rest_qualifier_id(t), self.qids(s), t)
        # 重なっていない品目 (先の資料だけ) には広げない
        self.assertFalse([e for e in other["exceptions"] if e["kind"] == "deferred" and
                          expand.in_scope(e, other["category_id"], other["district_id"])])
        self.assertEqual(self.qids(other), [])
        self.assertEqual(self.schema_errors(m), [])

    def test_cell_exception_conflict_disputes(self):        # マスの「（祝日の収集は休み）」が先の資料の「祝日も収集します」と違う
        b = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日（祝日の収集は休み）"]))
        m = self.run_docs(self.A, b)
        s = self.series(m)[("本町", "燃やすごみ")]
        hp = [e for e in s["exceptions"] if e["kind"] == "holiday_policy" and expand.in_scope(e, s["category_id"], s["district_id"])]
        self.assertEqual(sorted(e["value"] for e in hp), ["collected", "not_collected"])
        bl = self.blocked(dict(s, disputes=s["disputes"]))
        self.assertIn("holiday_policy_missing", bl)
        self.assertTrue(any(b.startswith("disputed:") and ":policy:" in b for b in expand.expand_series(s)["blocked_by"]))
        self.assertEqual([q["kind"] for q in m.queue], ["policy"])
        self.assertEqual(s["provenance"]["status"], "disputed")
        self.assertEqual(self.schema_errors(m), [])           # 落とした地区に当てた例外を残さない

    def test_cell_exception_carried_when_first_has_none(self):  # 先の資料に祝日の扱いが無ければ、後のマスの扱いを和で足す (違いは無い)
        a = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日"]))
        b = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日（祝日の収集は休み）"]))
        m = self.run_docs(a, b)
        s = self.series(m)[("本町", "燃やすごみ")]
        hp = [e for e in s["exceptions"] if e["kind"] == "holiday_policy" and expand.in_scope(e, s["category_id"], s["district_id"])]
        self.assertEqual([(e["value"], e["extra"]["carried_from"]["source_id"]) for e in hp], [("not_collected", "s2")])
        self.assertEqual(m.queue, [])
        self.assertEqual(self.schema_errors(m), [])

    def test_same_source_duplicate(self):                   # 同じ資料の 2 つの表
        m = self.run_docs(page(H + "<p>祝日も収集します。</p>" + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日"]) +
                               table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日（祝日の収集は休み）"])))
        s = self.series(m)[("本町", "燃やすごみ")]
        self.assertIn("holiday_policy_missing", self.blocked(s))
        self.assertTrue(s["disputes"])
        dists = {d["district_id"] for d in m.districts}
        for e in m.exceptions:
            self.assertTrue(set((e.get("scope") or {}).get("district_ids") or []) <= dists, e)
        self.assertEqual(self.schema_errors(m), [])

    def test_label_note_of_duplicate_carried(self):         # 後の資料の品目の見出しの注 (分類が同じで突き合わせた品目)
        b = page(H + table(["地区", "燃やせるごみ ※祝日は収集しません"], ["本町", "月曜日・木曜日"]))
        m = self.run_docs(self.A, b)
        by = self.series(m)
        self.assertEqual(len(by), 2)
        self.assertIn(Q_HOL, self.qids(by[("本町", "燃やすごみ")]))
        self.assertEqual(self.qids(by[("本町", "不燃ごみ")]), [])


# ---- 2 回目の直し ----
YE = "<p>年末は12月29日まで、年始は1月4日から収集します。</p>"


def mine(s, kind=None):
    return [e for e in s["exceptions"] if expand.in_scope(e, s["category_id"], s["district_id"]) and (kind is None or e["kind"] == kind)]


class TestCellNoteCategories(Base):
    """A: マスの読み残しの品目の名指しは、系列と同じ資料の品目で見る (cell_notes に cats を渡す)"""
    HEAD = ["地区", "燃やすごみ", "不燃ごみ"]

    def test_own_category_note_used(self):                 # 燃やすごみのマスの「（燃やすごみは祝日の収集は休み）」はそのマスの方針
        m = self.run_docs(page(H + table(self.HEAD, ["本町", "月曜日・木曜日（燃やすごみは祝日の収集は休み）", "第2水曜日"])))
        by = self.series(m)
        s, other = by[("本町", "燃やすごみ")], by[("本町", "不燃ごみ")]
        self.assertEqual([e["value"] for e in mine(s, "holiday_policy")], ["not_collected"])
        self.assertEqual(s["_cell_qualifiers"] if "_cell_qualifiers" in s else s["qualifiers"], [])
        self.assertFalse([b for b in self.blocked(s) if b.startswith("unconsumed_qualifier")])
        self.assertEqual(mine(other, "holiday_policy"), [])   # ほかの品目には広げない
        self.assertEqual(m.sources[0]["extra"]["qualifiers_unconsumed"], [])

    def test_other_or_unknown_category_note_stays(self):    # ほかの品目・資料に無い品目・「資源」を名指し → 使わず、止める
        for note in ("（不燃ごみは祝日の収集は休み）", "（ペットボトルは祝日の収集は休み）", "（資源は祝日も収集）"):
            with self.subTest(note=note):
                m = self.run_docs(page(H + table(self.HEAD, ["本町", "月曜日・木曜日" + note, "第2水曜日"])))
                by = self.series(m)
                s, other = by[("本町", "燃やすごみ")], by[("本町", "不燃ごみ")]
                self.assertEqual(mine(s, "holiday_policy") + mine(other, "holiday_policy"), [])
                self.assertTrue(s["qualifiers"])
                self.assertTrue([b for b in self.blocked(s) if b.startswith("unconsumed_qualifier")])


class TestSeasonMarksInNames(Base):
    """B: 名前のマスの祝・夏・冬は、あとに地名の字が続くときだけ名前の字。助詞・かっこ・終わりが続けば書き添え (但し書き)"""

    def test_season_and_holiday_notes_block_that_district(self):
        for cell in ("東町（夏は毎週）", "東町（祝も収集）", "東町（冬は休み）", "東町（夏）", "東町（夏・冬は隔週）", "東町 ※祝、夏の収集なし"):
            with self.subTest(cell=cell):
                m = self.run_docs(page(H + "<p>祝日も収集します。</p>" + table(["地区", "燃やすごみ"], [cell, "毎週月曜日"],
                                                                              ["西町", "毎週火曜日"])))
                by = {d[:2]: s for (d, _), s in self.series(m).items()}
                self.assertTrue(self.qids(by["東町"]))
                self.assertTrue([b for b in self.blocked(by["東町"]) if b.startswith("unconsumed_qualifier")])
                self.assertEqual(self.qids(by["西町"]), [])
                self.assertEqual([e["text"] for e in m.exceptions], ["祝日も収集します。"])

    def test_place_names_with_marks_not_blocked(self):     # 夏見・冬木・祝園・日夏町は名前
        m = self.run_docs(page(H + table(["地区", "燃やすごみ"], ["夏見", "毎週月曜日"], ["冬木", "毎週火曜日"], ["祝園", "毎週水曜日"],
                                         ["日夏町", "毎週木曜日"])))
        for (d, _), s in self.series(m).items():
            self.assertEqual(self.qids(s), [], d)
        self.assertEqual(m.sources[0]["extra"]["qualifiers_unconsumed"], [])
        self.assertEqual(m.sources[0]["extra"]["qualifiers_name_cells"], [])


class TestDuplicatePolicies(Base):
    """C: 重なって落とした資料のページの方針 (祝日・年末年始) を、残った系列の方針と比べる"""
    A = page(H + "<p>祝日も収集します。</p>" + YE + table(["地区", "燃やすごみ", "不燃ごみ"], ["本町", "月曜日・木曜日", "第2水曜日"]))

    def b(self, text, title=TITLE, h=H, row=("本町", "月曜日・木曜日")):
        return page(h + text + table(["地区", "燃やすごみ"], list(row)), title=title)

    def test_page_holiday_policy_differs_disputes(self):
        m = self.run_docs(self.A, self.b("<p>祝日は収集しません。</p>" + YE))
        by = self.series(m)
        s, other = by[("本町", "燃やすごみ")], by[("本町", "不燃ごみ")]
        self.assertEqual(sorted(e["value"] for e in mine(s, "holiday_policy")), ["collected", "not_collected"])
        self.assertIn("holiday_policy_missing", self.blocked(s))
        self.assertEqual([q["kind"] for q in m.queue], ["policy"])
        self.assertTrue(m.queue[0]["item_id"].endswith(":holiday_policy"))
        self.assertIn(m.queue[0]["item_id"], s["disputes"])
        self.assertEqual(s["provenance"]["status"], "disputed")
        self.assertEqual(other["disputes"], [])               # 重なっていない品目には広げない
        self.assertEqual([e["value"] for e in mine(other, "holiday_policy")], ["collected"])
        self.assertEqual(self.schema_errors(m), [])

    def test_page_year_end_differs_disputes(self):
        m = self.run_docs(self.A, self.b("<p>祝日も収集します。</p><p>年末は12月30日まで、年始は1月5日から収集します。</p>"))
        s = self.series(m)[("本町", "燃やすごみ")]
        self.assertEqual(sorted(e["last"] for e in mine(s, "year_end")), ["2026-12-29", "2026-12-30"])
        self.assertEqual([q["item_id"].rsplit(":", 1)[1] for q in m.queue], ["year_end"])
        self.assertTrue(any(b.startswith("disputed:") for b in expand.expand_series(s)["blocked_by"]))

    def test_same_page_policy_kept(self):                     # 同じなら写さず、裁定待ちにもしない
        m = self.run_docs(self.A, self.b("<p>祝日も収集します。</p>" + YE))
        s = self.series(m)[("本町", "燃やすごみ")]
        self.assertEqual(m.queue, [])
        self.assertEqual(s["disputes"], [])
        self.assertFalse([e for e in mine(s) if (e.get("extra") or {}).get("carried_from")])

    def test_page_policy_carried_when_survivor_has_none(self):  # 先の資料に方針が無ければ、後の資料のページの方針を和で足す
        a = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日"]))
        m = self.run_docs(a, self.b("<p>祝日も収集します。</p>" + YE))
        s = self.series(m)[("本町", "燃やすごみ")]
        got = sorted((e["kind"], e["extra"]["carried_from"]["source_id"]) for e in mine(s))
        self.assertEqual(got, [("holiday_policy", "s2"), ("year_end", "s2")])
        for e in mine(s):
            self.assertEqual(e["scope"], {"category_ids": [s["category_id"]], "district_ids": [s["district_id"]]})
        self.assertEqual(m.queue, [])
        self.assertEqual(self.schema_errors(m), [])

    def test_other_period_policy_not_carried(self):          # 期間の違う資料の方針は写さない (同じでなければ裁定待ち)
        a = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日"]))
        old = self.b("<p>祝日も収集します。</p>", title="<title>令和7年度 ごみ収集日</title>", h="<h1>令和7年度 ごみの収集日</h1>")
        m = self.run_docs(a, old)
        self.assertEqual(len(m.periods), 2)
        s = self.series(m)[("本町", "燃やすごみ")]
        self.assertEqual(mine(s, "holiday_policy"), [])
        self.assertIn("holiday_policy_missing", self.blocked(s))
        self.assertEqual([q["kind"] for q in m.queue], ["policy"])
        self.assertIn(m.queue[0]["item_id"], s["disputes"])

    def test_page_qualifier_and_deferred_reach_every_survivor(self):  # 後の資料の行が 2 つの系列に重なる → どちらにも
        a = page(H + table(["地区", "燃やすごみ", "不燃ごみ"], ["本町", "月曜日・木曜日", "第2水曜日"]))
        b = page(H + "<p>年末年始の収集日は決まり次第お知らせします。</p><p>※一部の集積所を除く。</p>" +
                 table(["地区", "燃やすごみ", "不燃ごみ"], ["本町", "月曜日・木曜日", "第2水曜日"]))
        m = self.run_docs(a, b)
        by = self.series(m)
        self.assertEqual(len(by), 2)
        for k, s in by.items():
            self.assertIn(expand.rest_qualifier_id("※一部の集積所を除く。"), self.qids(s), k)
            self.assertEqual(len(mine(s, "deferred")), 1, k)

    def test_markup_and_calendar_link_carried(self):         # 落とした行の表のしるし・行のカレンダーのリンクも残った系列に
        b = page(H + table(["地区", "燃やすごみ"], ['<a href="honmachi.pdf">本町</a>', '<span style="color:red">月曜日・木曜日</span>']))
        a = page(H + table(["地区", "燃やすごみ"], ["本町", "月曜日・木曜日"]))
        m = self.run_docs(a, b)
        s = self.series(m)[("本町", "燃やすごみ")]
        self.assertIn("style_color", s["markup_unhandled"])
        self.assertTrue(s["printed_pending"])
        bl = self.blocked(s)
        self.assertIn("markup_unhandled:style_color", bl)
        self.assertIn("pending_printed", bl)


# ---- 3 回目の直し: 名前のマスを閉じた型 (whitelist) で読む ----
class TestNameCellWhitelist(Base):
    """名前 (members の名前を丸ごと・品目の言葉) と形の決まった条件を除いて、句読点・かっこ・空白しか残らないときだけマスごと使う"""

    def test_district_leftover_blocks(self):                  # 印の無い書き添えも、1 字でも残れば止める
        for cell in ("東町（月2回）", "東町（水曜日に収集）", "東町（第2週）", "東町（毎週）", "東町（夏山シーズン中は毎週）", "東町（注連）",
                     "東町（夏見）は毎週", "東町 月2回", "東町<br>毎週", "東町隔週", "東町（冬木を除く）", "東町（夏期を除く）",
                     "東町（一部夏は休み）", "東町1丁目（第2週）", "東町2・4", "東町、夏", "東町※"):
            with self.subTest(cell=cell):
                m = self.run_docs(page(H + table(["地区", "燃やすごみ"], [cell, "毎週月曜日"], ["西町", "毎週火曜日"])))
                by = {d[:2]: s for (d, _), s in self.series(m).items()}
                self.assertTrue([b for b in self.blocked(by["東町"]) if b.startswith("unconsumed_qualifier")])
                self.assertEqual(self.qids(by["西町"]), [])

    def test_district_structured_conditions_used(self):      # 範囲・除く・一部・班・上記以外・線の片側・町内会の名前は地区の条件
        for cell in ("東町（冬木地区を除く）", "東町（一部）", "東町（3班）", "東町（1・3丁目を除く）", "東町（上記以外）", "東町（夏川沿い）",
                     "東町5番《吉津町内会》", "東町（7・8・10～12番一部）", "東町1~3丁目", "東町の一部（県道和光志木線北側）"):
            with self.subTest(cell=cell):
                frags, rest = stage2.district_rest(cell, stage2.members.parse_cell(cell))
                self.assertEqual(rest, [])

    def test_label_leftover_blocks(self):
        for text, kind in (("可燃ごみ（週2回）", "category"), ("燃やすごみは別日程", "catlabel"), ("不燃ごみ 第2・4", "catlist"),
                           ("燃やすごみ（隔週）", "catlabel"), ("燃えるごみ\n（メタン発酵処理）", "catlabel"), ("燃やすごみ\n水曜日", "catlabel"),
                           ("有料", "category")):
            with self.subTest(text=text, kind=kind):
                self.assertTrue(stage2.label_rest(text, kind)[1])
        for text, kind in (("びん類\n（無色びん・茶びん）", "catlabel"), ("可燃物（燃えるごみ）", "text"), ("びん・缶（第1・3週）", "category"),
                           ("第2・4 不燃ごみ", "catlist"), ("資源物 びん・缶・\nペットボトル\n紙・布", "category"), ("もやせない\nごみ", "category")):
            with self.subTest(text=text, kind=kind):
                self.assertEqual(stage2.label_rest(text, kind)[1], [])

    def test_header_count_note_blocks_category(self):         # 世田谷「可燃ごみ（週2回）」: 回数の書き添えが規則と合わなければ、その品目を止める
        # 4 回目 (関門 consume の freq): 規則の曜日の数と合う「（週2回）」(月曜日・木曜日) は同じことを言っているだけ → その系列では使う。
        # 合わない行 (月曜日だけ・第2月曜日) は、その系列だけ止める
        m = self.run_docs(page(H + table(["地区", "可燃ごみ（週2回）", "不燃ごみ"], ["東町", "月曜日・木曜日", "第2水曜日"],
                                         ["西町", "月曜日", "第4水曜日"], ["南町", "第2月曜日", "第1水曜日"])))
        for (d, c), s in self.series(m).items():
            if c.startswith("可燃") and not d.startswith("東町"):
                self.assertEqual(self.qids(s), [expand.rest_qualifier_id("週2回")], d)
            else:
                self.assertEqual(self.qids(s), [], (d, c))


if __name__ == "__main__":
    unittest.main()
