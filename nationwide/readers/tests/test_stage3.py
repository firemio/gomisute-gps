"""作る順の 7〜9: 型 D (pdf_layout)・LLM の仕事 (llm_tasks)・2 つの読みの比べ (compare)・待ち行列 (adjudicate)・
確かめ (validate)・入口 (build) (ネットにつながない。PDF は合成か、キャッシュにあるもの。LLM の答えは合成かキャッシュ)"""
import argparse, copy, datetime as dt, json, pathlib, tempfile, unittest

import _util
import adjudicate, build, compare, content, expand, llm_tasks, pdf_layout, rules, schema, validate

HIDAKA = "https://www.city.hidaka.lg.jp/material/files/group/13/reiwa8nendogominitteihyou.pdf"
MITAKA = "https://www.city.mitaka.lg.jp/c_service/095/attached/attach_95664_8.pdf"
FY26 = {"period_id": "p1", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
        "year_evidence": {"token": {"text": "令和8年度", "where": "in_file"}, "time": [{"kind": "last_modified", "value": "2026-02-24"}],
                          "conflict": None, "ok": True}}


def make_pdf(cells, title="令和8年度 ごみ収集日程表", dashed_rows=False):
    """罫線のある表の PDF を作る (セルの文字のリストのリスト)。dashed_rows なら 2 列目の行の区切りを破線にする"""
    import fitz
    doc = fitz.open()
    p = doc.new_page(width=640, height=320)
    nr, nc = len(cells), len(cells[0])
    x0, y0, w, h = 20, 40, 70, 30
    for r in range(nr + 1):
        p.draw_line((x0, y0 + r * h), (x0 + nc * w, y0 + r * h), color=(0, 0, 0), width=1)
    for c in range(nc + 1):
        p.draw_line((x0 + c * w, y0), (x0 + c * w, y0 + nr * h), color=(0, 0, 0), width=1)
    for r in range(nr):
        for c in range(nc):
            if cells[r][c]:
                p.insert_text((x0 + c * w + 4, y0 + r * h + 20), cells[r][c], fontname="japan", fontsize=10)
    p.insert_text((20, 25), title, fontname="japan", fontsize=14)
    return doc.tobytes()


CELLS = [["分別", "4月", "5月", "6月", "7月", "8月", "9月", "10月"],
         ["古紙", "8・22", "13", "10", "15", "19", "16", "14・28"],
         ["ビン・カン", "24", "", "26", "10", "14", "11", "23"]]


class TestPdfLayoutSynthetic(unittest.TestCase):
    def test_date_table_and_structure(self):
        doc = pdf_layout.open_pdf(make_pdf(CELLS))
        pg = pdf_layout.analyze_page(doc[0])
        self.assertEqual(pg["kind"], "date_table")
        t = pg["tables"][0]
        self.assertTrue(t["structure"]["ok"], t["structure"])           # find_tables と罫線の格子が同じ
        d = t["date_table"]
        self.assertEqual(d["orientation"], "months_in_row")
        by = {c["label"]: c for c in d["categories"]}
        self.assertEqual([x.get("days") for x in by["古紙"]["cells"]][:1], [[8, 22]])
        self.assertEqual(by["ビン・カン"]["cells"][1]["status"], "blank")   # 空のマスは「収集なし」と読まない (blank のまま)
        self.assertEqual(len(d["blank_cells"]), 1)

    def test_not_a_date_table_without_category_axis(self):
        cells = [["日", "4月", "5月", "6月", "7月", "8月", "9月"]] + [[str(i), "", "", "", "", "", ""] for i in range(1, 4)]
        doc = pdf_layout.open_pdf(make_pdf(cells))
        pg = pdf_layout.analyze_page(doc[0])
        self.assertTrue(all(t["date_table"] is None for t in pg["tables"]))   # 品目の名前の軸が無ければ日付の表にしない (川口の縦の月表)

    def test_broken_chars_and_days(self):
        self.assertTrue(pdf_layout.has_broken("コース番号⬱"))           # 日高のコース番号 10 の矢印の字
        self.assertTrue(pdf_layout.has_broken(""))
        self.assertFalse(pdf_layout.has_broken("コース番号1"))
        self.assertEqual(pdf_layout.parse_days("10日・24日"), [10, 24])
        self.assertIsNone(pdf_layout.parse_days("市では収集しません。"))
        self.assertIsNone(pdf_layout.parse_days("※15日・29日"))            # 印のついた日は日の並びにしない (裁定へ)
        self.assertEqual(pdf_layout.parse_days(""), [])

    def test_english_lines_dropped_from_labels(self):
        keep, drop = pdf_layout.japanese_lines("火曜日 /\n金曜日\nTuesday\n&\nFriday\n※臨時収集29日")
        self.assertEqual(keep, ["火曜日 /", "金曜日", "※臨時収集29日"])
        self.assertEqual(pdf_layout._label_text("指定収集袋\n燃やせるごみ\nCombustible\ngarbage"), "燃やせるごみ")

    def test_render_has_margins(self):
        doc = pdf_layout.open_pdf(make_pdf(CELLS))
        pg = pdf_layout.analyze_page(doc[0])
        png = pdf_layout.render_grid_png(doc[0], pg["tables"][0]["grid"])
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(pdf_layout.merged_desc(pg["tables"][0]["grid"]), [])            # 結合したマスが無い表


@unittest.skipUnless(_util.cached(HIDAKA), "日高の PDF がキャッシュに無い")
class TestPdfLayoutHidaka(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.an = build.analyze_pdf(HIDAKA)

    def test_page_kinds(self):
        self.assertEqual([p["kind"] for p in self.an["pages"]][2:], ["date_table", "date_table"])
        self.assertNotEqual(self.an["pages"][1]["kind"], "date_table")       # 粗大ごみの受入日の表 (品目の軸が無い) は日付の表にしない

    def test_course_1_and_broken_course_10(self):
        tabs = [t for t in self.an["pages"][2]["tables"] if t["date_table"]]
        self.assertEqual(len(tabs), 10)
        self.assertTrue(all(t["structure"]["ok"] for t in tabs))
        c1 = next(t["date_table"] for t in tabs if (t["date_table"]["header"]["course"] or {}).get("text") == "コース番号1")
        by = {c["label"]: c for c in c1["categories"]}
        self.assertEqual([x.get("days") for x in by["古紙･古布"]["cells"]][:3], [[8, 22], [13], [10]])
        self.assertEqual(c1["rule_cells"][0]["category_label"], "可燃ごみ")
        broken = [t for t in tabs if (t["date_table"]["header"]["course"] or {}).get("broken")]
        self.assertEqual(len(broken), 1)                                     # 3 ページ目の 10 番目 (コース番号 10)
        self.assertTrue(c1["blank_cells"])                                   # 有害ごみの空の月 → 人が決まりを足すまで止める

    def test_block_notes(self):
        pg = self.an["pages"][2]
        tabs = [t for t in pg["tables"] if t["date_table"]]
        notes = pdf_layout.notes_for(pg["info"], tabs)
        owned = [(ln["visible_text"].strip(), o) for ln, o in notes if "定期点検" in ln["visible_text"]]
        self.assertEqual(len(owned), 10)
        self.assertTrue(all(o is not None for _, o in owned))                # 表の下の注は、その表 (コース) の注

    def test_small_r6_table_not_counted(self):
        hidden = [c for c in self.an["pages"][0]["info"].chars if not c.visible and c.why == "too_small"]
        self.assertTrue(hidden)                                              # 使い方の図の令和6年度の表は 3pt より小さい


@unittest.skipUnless(_util.cached(MITAKA), "三鷹の PDF がキャッシュに無い")
class TestPdfLayoutMitaka(unittest.TestCase):
    def test_months_in_col_rule_columns_and_structure_dispute(self):
        an = build.analyze_pdf(MITAKA)
        t = next(t for t in an["pages"][0]["tables"] if t["date_table"])
        d = t["date_table"]
        self.assertEqual(d["orientation"], "months_in_col")
        rules_by = {r["category_label"]: rules.parse(r["japanese"].replace("\n", " ")) for r in d["rule_cells"]}
        self.assertEqual(rules_by["燃やせるごみ"].irs, [{"kind": "weekly", "weekdays": ["TU", "FR"]}])
        self.assertEqual(rules_by["燃やせるごみ"].rest, "※臨時収集29日")      # マスの読み残し → そのマスの地区・品目の但し書き
        nb = next(c for c in d["categories"] if c["label"] == "燃やせないごみ")
        self.assertEqual(nb["note"]["japanese"], "水曜日（月2回）")
        # 毎週の列は月ごとの破線で区切られている: find_tables は 1 つのマス、罫線の格子は 12 のマス → 表の形の裁定へ
        self.assertFalse(t["structure"]["ok"])
        dtext, bbox = build.district_title(an["pages"][0]["info"], t["bbox"])
        self.assertEqual(dtext, "大沢3丁目、深大寺、井口、野崎2・3丁目")      # 丸数字 (①) と「地区用」を外す


class TestCompare(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(compare.selftest())

    def test_policy_span_and_union(self):
        a = [{"exception_id": "e1", "kind": "holiday_policy", "value": "collected", "covers": ["national"], "text": "祝日も収集します。"},
             {"exception_id": "e2", "kind": "cancelled", "date": "2026-05-04", "text": "５月４日は収集しません"}]
        b = {"holiday_policy": {"value": "collected", "covers": ["national"], "span": "閉庁日：祝日、年末年始"},
             "closures": [{"dates": ["5/4"], "span": "５月４日は収集しません"}], "year_end": [], "deferred": [{"period": "年末年始", "span": "年末年始は変更することがあります"}],
             "caveats": [], "qualifiers": [{"text": "※5〜10月のみ", "span": "※5〜10月のみ"}], "ignored_office_hours": [], "valid": None}
        r = compare.compare_policy(a, [x["text"] for x in a], [], b, FY26)
        self.assertEqual(r["holiday_policy"], "disputed")                 # 値は同じでも span が違う所 (開庁時間) を指す
        self.assertEqual(r["off_days"], "agreed")
        self.assertEqual(len(r["deferred_b_only"]), 1)                   # B だけの deferred は足す (止める方)
        self.assertEqual(len(r["qualifiers_b_only"]), 1)                 # 但し書きは和
        self.assertEqual(compare.compare_policy(a, [], [], None, FY26)["holiday_policy"], "single")

    def test_others_inference_disputed(self):
        a = [{"exception_id": "e1", "kind": "year_end", "treatment": "makeup", "removed": "2027-01-01", "to": "2027-01-05", "others": None,
              "text": "1/1(金)は休みのため 1/5(火)に特別収集します"}]
        b = {"year_end": [{"treatment": "makeup", "dates": ["1/1", "1/5"], "others": "unchanged", "span": "1/1(金)は休みのため"}]}
        r = compare.compare_policy(a, [a[0]["text"]], [], b, FY26)
        self.assertEqual(r["others"], "disputed")                         # 羽咋: 推し量りの数え方は人が決める

    def test_members_community(self):
        a = build.district_members("高萩北　　旭ケ丘１・２　　久保（高麗川）", "行政区名", "112429")
        b = {"members": [{"name_verbatim": "高萩北", "unit": "community", "conditions": []},
                         {"name_verbatim": "旭ケ丘1・2", "unit": "community", "conditions": []},
                         {"name_verbatim": "久保", "unit": "community", "conditions": [{"kind": "note", "text": "高麗川"}]}]}
        aa = [dict(x, name_verbatim=x["extra"]["name_verbatim"]) for x in a]
        self.assertEqual(compare.compare_members(aa, b), "agreed")
        b2 = copy.deepcopy(b)
        b2["members"][1]["name_verbatim"] = "旭ケ丘1"
        self.assertEqual(compare.compare_members(aa, b2), "disputed")
        ex = build.expand_numbered(a)
        self.assertEqual([m["text"] for m in ex][:3], ["高萩北", "旭ケ丘1", "旭ケ丘2"])

    def test_cells(self):
        bc = compare.b_cells({"cells": {"R2C3": {"text": "8・22", "days": [8, 22]}, "r3c4": "13"}})
        self.assertEqual(bc[(1, 2)]["days"], [8, 22])
        self.assertEqual(compare.b_days(bc[(2, 3)]), [13])


class TestAdjudicate(unittest.TestCase):
    def test_routes(self):
        q = adjudicate.normalize([{"item_id": "x:structure", "kind": "structure"}, {"item_id": "x:cell", "kind": "cell", "series": [["d", "c", "p"]]},
                                  {"item_id": "x:policy", "kind": "policy", "policy_part": "others"},
                                  {"item_id": "x:cell", "kind": "cell", "series": [["d2", "c", "p"]]}])
        by = {i["item_id"]: i for i in q}
        self.assertEqual(by["x:structure"]["route"], ["code3", "human"])
        self.assertEqual(by["x:cell"]["route"], ["strong_model", "human"])
        self.assertEqual(len(by["x:cell"]["series"]), 2)                  # 同じ項目はまとめる
        self.assertEqual(by["x:policy"]["route"], ["human", "inquiry"])
        with self.assertRaises(ValueError):
            adjudicate.normalize([{"item_id": "y", "kind": "vote"}])

    def test_decisions(self):
        st = {"item_id": "x:structure", "kind": "structure"}
        self.assertFalse(adjudicate.decision_usable(st, {"by": "opus", "matches": "A", "source_sha256": "s"}, ["s"])[0])
        self.assertTrue(adjudicate.decision_usable(st, {"by": "human", "source_sha256": "s"}, ["s"])[0])
        self.assertFalse(adjudicate.decision_usable(st, {"by": "human", "source_sha256": "old"}, ["s"])[0])
        cell = {"item_id": "x:cell", "kind": "cell"}
        self.assertFalse(adjudicate.decision_usable(cell, {"by": "opus", "matches": None, "source_sha256": "s"}, ["s"])[0])
        self.assertTrue(adjudicate.decision_usable(cell, {"by": "opus", "matches": "B", "source_sha256": "s"}, ["s"])[0])
        doc = {"sources": [{"sha256": "s"}], "schedules": [{"blocked_by": ["disputed:x:structure", "markup_unhandled:blank"], "provenance": {}}]}
        n = adjudicate.apply_decisions(doc, [st], {})
        self.assertEqual(n, 0)                                            # 記録が無ければ何もしない
        n = adjudicate.apply_decisions(doc, [st], {"x:structure": {"item_id": "x:structure", "by": "human", "source_sha256": "s"}})
        self.assertEqual((n, doc["schedules"][0]["blocked_by"]), (1, ["markup_unhandled:blank"]))

    def test_write_queue(self):
        with tempfile.TemporaryDirectory() as d:
            p = adjudicate.write_queue("999999", adjudicate.normalize([{"item_id": "a", "kind": "markup"}]), queue_dir=d)
            body = json.loads(pathlib.Path(p).read_text("utf-8"))
            self.assertEqual(body["routes"], {"human": 1})


def printed_series(dates, note=None, did="112429-d1", cid="c1", **kw):
    s = {"district_id": did, "category_id": cid, "period_id": "p1", "dates_origin": "printed", "rule": None, "dates": dates,
         "unknown_ranges": [], "answerable": True, "blocked_by": [], "gate": {"unit": "D", "open": False}, "published": False,
         "caveats": [], "expanded_with": [], "rule_mismatches": [], "provenance": {"readers": [], "status": "agreed"},
         "ref": {"source_id": "s1"}, "extra": {"column_note": note} if note else {}}
    s.update(kw)
    return s


def mini_doc(scheds, excs=()):
    return {"schema": schema.SCHEMA, "lg_code": "112429", "name": "テスト市", "periods": [FY26],
            "partitions": [{"partition_id": "all", "category_ids": ["c1", "c2"], "basis": "unknown"}],
            "districts": [{"district_id": "112429-d1", "partition_id": "all", "label": "本町", "label_kind": "official",
                           "members": [{"text": "本町", "unit": "machiaza", "match": "unchecked", "conditions": [], "geometry": "unresolved"}],
                           "place_resolution": "unresolved"}],
            "categories": [{"category_id": "c1", "official_label": "燃やせないごみ", "coarse": ["non_burnable"], "mode": "curbside"},
                           {"category_id": "c2", "official_label": "ペットボトル", "coarse": ["pet_bottle"], "mode": "curbside"}],
            "schedules": scheds, "exceptions": list(excs), "sources": [{"source_id": "s1", "url": "u", "source_key": "u", "format": "pdf_text",
                                                                        "sha256": "s"}],
            "watch": {"sources": []}, "coverage": {"answer_type": "rule_contact"}, "build": {}}


FY_MONTHS = tuple((2026, m) for m in range(4, 13)) + tuple((2027, m) for m in range(1, 4))


def wednesdays_twice(months=FY_MONTHS):
    """年度の各月の第2・第4水曜日 (「水曜日（月2回）」の印字の形)"""
    out = []
    for y, m in months:
        ws = [dt.date(y, m, d) for d in range(1, 29) if dt.date(y, m, d).weekday() == 2]
        out += [ws[1].isoformat(), ws[3].isoformat()]
    return out


class TestValidate(unittest.TestCase):
    def test_weekday_and_count_pass(self):
        doc = mini_doc([printed_series(wednesdays_twice(), note="水曜日（月2回）")])
        r = validate.run(doc)
        self.assertEqual((r.checks[1]["ran"], r.checks[1]["failed"]), (1, 0))
        self.assertEqual((r.checks[3]["ran"], r.checks[3]["failed"]), (1, 0))

    def test_weekday_mismatch_blocks(self):
        ds = wednesdays_twice()
        ds[0] = (dt.date.fromisoformat(ds[0]) + dt.timedelta(days=1)).isoformat()      # 列の取り違えのような木曜日
        doc = mini_doc([printed_series(sorted(ds), note="水曜日（月2回）")])
        r = validate.run(doc)
        self.assertEqual(r.checks[1]["failed"], 1)
        items = validate.apply(doc, r, "112429")
        s = doc["schedules"][0]
        self.assertFalse(s["answerable"])
        self.assertEqual(s["dates"], [])
        self.assertTrue(any(b.startswith("disputed:112429:validate:1:") for b in s["blocked_by"]))
        self.assertEqual(items[0]["kind"], "cell")
        self.assertEqual(schema.validate(doc), [])

    def test_count_mismatch(self):
        ds = wednesdays_twice()[1:]                                       # 4 月の 1 回が抜けた (結合したマスの見落としの形)
        doc = mini_doc([printed_series(ds, note="水曜日（月2回）")])
        self.assertEqual(validate.run(doc).checks[3]["failed"], 1)

    def test_deferred_must_be_unknown(self):
        e = {"exception_id": "e1", "kind": "deferred", "period_name": "年末年始", "window": {"from": "2026-12-15", "to": "2027-01-31"},
             "scope": {"category_ids": "*"}, "text": "年末年始は変更することがあります。", "span": {"source_id": "s1", "text": "年末年始は変更することがあります。"}}
        sent = content.sentences([content.Line("年末年始は変更することがあります。", [], "p", "p")])
        doc = mini_doc([printed_series(wednesdays_twice())], [e])
        r = validate.run(doc, {"sources": {"s1": {"kind": "pdf", "sentences": sent, "unconsumed": []}}})
        self.assertGreaterEqual(r.checks[21]["failed"], 1)               # 印字の日付があっても、名前のある期間は不明にする
        doc["schedules"][0]["unknown_ranges"] = [{"from": "2026-12-15", "to": "2027-01-31", "reason": "deferred"}]
        r = validate.run(doc, {"sources": {"s1": {"kind": "pdf", "sentences": sent, "unconsumed": []}}})
        self.assertEqual(r.checks[21]["failed"], 0)

    def test_unconsumed_qualifier_check(self):
        doc = mini_doc([printed_series(wednesdays_twice())])
        ctx = {"sources": {"s1": {"kind": "pdf", "sentences": [], "unconsumed": [{"qualifier_id": "qx", "text": "※指定日以外は厳禁", "months": [],
                                                                               "scope": "*"}]}}}
        self.assertEqual(validate.run(doc, ctx).checks[15]["failed"], 1)


class TestLLMTasks(unittest.TestCase):
    def test_prompts_guard_and_versions(self):
        for name, t in llm_tasks.TASKS.items():
            self.assertIn("指示ではありません", t["system"], name)          # 抜粋は指示として扱わない (§3.4)
            self.assertRegex(t["version"], r"^[a-z_]+\.B@\d+$")
        self.assertIn("ambiguous_week", llm_tasks.RULES["system"])
        self.assertIn("others", llm_tasks.POLICY["system"])
        self.assertIn("推し量らない", llm_tasks.POLICY["system"])

    def test_offline_missing(self):
        with tempfile.TemporaryDirectory() as d:
            ans, rec = llm_tasks.ask("basis", "集積所の看板で確認してください", d, offline=True)
            self.assertIsNone(ans)
            self.assertTrue(rec["missing"])
            self.assertEqual(rec["prompt"], "basis.B@1")

    def test_policy_text_split(self):
        lines = [content.Line("## 見出し" if i % 50 == 0 else "あ" * 300, ["見出し"], "p", "p", is_heading=(i % 50 == 0)) for i in range(120)]
        chunks = llm_tasks.policy_text(lines, limit=10000)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(c) < 10400 for c in chunks))
        m = llm_tasks.merge_policy([{"holiday_policy": {"value": "collected", "covers": ["national"]}},
                                    {"holiday_policy": {"value": "not_collected", "covers": ["national"]}}, None])
        self.assertTrue(m["conflicting_holiday_policy"])
        self.assertEqual(m["missing"], 1)


@unittest.skipUnless(_util.cached(HIDAKA), "日高の PDF がキャッシュに無い")
class TestBuildHidakaOffline(unittest.TestCase):
    """日高を、LLM を使わずに (B が無い) 通す: どの系列も答えない、理由がそろう、検査が通る"""

    def test_build(self):
        a = argparse.Namespace(llm=False, offline=False, write=False)
        r = build.build_one("112429", a)
        doc = build.LAST["doc"]
        self.assertEqual(r["n_schema_errors"], 0, r["schema_errors"])
        self.assertEqual(r["answerable"], 0)
        by = {}
        for s in doc["schedules"]:
            by.setdefault(s["dates_origin"], []).append(s)
        printed = by["printed"]
        self.assertTrue(all("markup_unhandled:blank" in s["blocked_by"] for s in printed))    # 日付の表の空のマス
        self.assertTrue(all("single_reading" in s["blocked_by"] for s in printed))            # B が無い
        burn = [s for s in doc["schedules"] if s["category_id"] == next(c["category_id"] for c in doc["categories"]
                                                                        if c["official_label"] == "可燃ごみ")]
        self.assertTrue(burn and all("pending_printed" in s["blocked_by"] for s in burn))     # コースごとの月表 (型 F) がある
        self.assertTrue(all("holiday_policy_missing" in s["blocked_by"] for s in burn))      # 祝日の文は表のページの外
        self.assertEqual(doc["coverage"]["answer_type"], "rule_contact")
        kinds = {i["kind"] for i in build.LAST["queue"]}
        self.assertIn("markup", kinds)
        self.assertIn("mode", kinds)                                                          # 粗大・金属は出し方が決まらない
        csv_ids = {s["source_id"] for s in doc["sources"] if s["url"].endswith(".csv")}
        for s in doc["schedules"]:                                                            # 2020 年度の CSV は時の証拠で外れる
            if (s.get("ref") or {}).get("source_id") in csv_ids:
                self.assertTrue({"version_unproven", "period_unknown"} & set(s["blocked_by"]), s["blocked_by"])


class TestAreaExpanded(unittest.TestCase):
    def test_chome_expansion(self):
        import jgd
        gt = {"courses": [{"course": "1", "areas_by_period": {"p": [{"name": "銀座1丁目"}, {"name": "大蔵5・6丁目"}, {"name": "台"}]}}]}
        doc = {"districts": [{"district_id": "d1", "label": "x", "members": [
            {"text": "銀座 1～8丁目", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": "1", "to": "8"}],
             "extra": {"name_verbatim": "銀座"}},
            {"text": "大蔵5丁目", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": "5", "to": "5"}],
             "extra": {"name_verbatim": "大蔵"}}]}]}
        strict = jgd.area_map(gt, doc)
        ex = jgd.area_map_expanded(gt, doc)
        self.assertEqual(strict[("1", "銀座1丁目")], set())
        self.assertEqual(ex[("1", "銀座1丁目")], {"d1"})
        self.assertEqual(ex[("1", "大蔵5・6丁目")], set())               # 6 丁目は中身に無い → 当てない
        self.assertEqual(ex[("1", "台")], set())


class TestK4(unittest.TestCase):
    @unittest.skipUnless(_util.cached("https://www4.city.kanazawa.lg.jp/material/files/group/68/2026tikubetsu.xls"), "Excel がキャッシュに無い")
    def test_k4_dates_not_measurable(self):
        import regress
        k = regress.check_k4()
        self.assertEqual(k["district_sheets_with_dates"], 0)
        self.assertFalse(k["ok"])
        self.assertGreater(k["towns_compared"], 500)


if __name__ == "__main__":
    unittest.main()
