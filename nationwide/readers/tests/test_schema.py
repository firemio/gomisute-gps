"""schema.py: スキーマ v0.2 の形と中身の検査、日ごとの答え (ネットにつながない)"""
import copy, json, unittest

import _util
import schema


def minimal():
    """検査に通る小さな v0.2 (架空の町。lg_code は検査数字の合うもの)"""
    ev_ok = {"token": {"text": "令和8年度", "where": "in_file", "place": "title", "visible": True},
             "time": [{"kind": "last_modified", "value": "2026-04-07", "source_id": "s1"}], "conflict": None, "ok": True}
    return {
        "schema": schema.SCHEMA, "lg_code": "172073", "name": "テスト市",
        "periods": [{"period_id": "fy2026", "valid_from": "2026-04-01", "valid_to": "2027-03-31",
                     "basis": {"text": "令和8年度", "ref": {"source_id": "s1", "page": 1}}, "year_evidence": ev_ok}],
        "partitions": [{"partition_id": "all", "category_ids": ["c1", "c2"], "basis": "residence"}],
        "districts": [{"district_id": "172073-a", "partition_id": "all", "label": "A", "label_kind": "derived",
                       "members": [{"text": "本町", "unit": "machiaza", "machiaza_id": None, "match": "exact", "conditions": [],
                                    "geometry": "polygon"},
                                   {"text": "旭町（団地を除く）", "unit": "machiaza", "machiaza_id": None, "match": "unchecked",
                                    "conditions": [{"kind": "facility", "name": "団地", "negate": True},
                                                   {"kind": "except", "of": [{"kind": "range", "unit": "chome", "from": "1", "to": "3"}]}],
                                    "geometry": "points"}],
                       "place_resolution": "points"}],
        "categories": [{"category_id": "c1", "official_label": "可燃ごみ", "coarse": ["burnable"], "mode": "curbside"},
                       {"category_id": "c2", "official_label": "粗大ごみ", "coarse": ["oversized"], "mode": "dropoff"}],
        "schedules": [
            {"district_id": "172073-a", "category_id": "c1", "period_id": "fy2026", "dates_origin": "expanded",
             "rule": {"text": "火・金", "ir": {"kind": "weekly", "weekdays": ["TU", "FR"]}},
             "dates": ["2026-04-03", "2026-04-07"], "unknown_ranges": [{"from": "2026-09-22", "to": "2026-09-22",
                                                                       "reason": "holiday_kind_not_covered"}],
             "answerable": True, "blocked_by": [], "gate": {"unit": "expanded", "open": False}, "published": False,
             "caveats": ["e2"], "expanded_with": ["e1"], "rule_mismatches": [],
             "provenance": {"readers": [{"reader": "code.rules@1", "kind": "code"}, {"reader": "llm.rules.B@1", "kind": "llm"}],
                            "independence": "code_llm", "status": "agreed"}},
            {"district_id": "172073-a", "category_id": "c2", "period_id": "fy2026", "dates_origin": None, "rule": None,
             "dates": [], "unknown_ranges": [], "answerable": False, "blocked_by": ["rule_unparsed"],
             "gate": {"unit": "C", "open": False}, "published": False,
             "provenance": {"readers": [{"reader": "code.rules@1", "kind": "code"}], "status": "single"}}],
        "exceptions": [{"exception_id": "e1", "kind": "holiday_policy", "scope": {"category_ids": "*"}, "value": "collected",
                        "covers": ["national", "substitute"], "text": "祝日、振替休日も実施します。",
                        "span": {"source_id": "s1", "page": 1, "text": "祝日、振替休日も実施します。"}},
                       {"exception_id": "e2", "kind": "caveat", "text": "※ごみの収集日は変更する場合があります。"}],
        "sources": [{"source_id": "s1", "url": "https://example.invalid/a.pdf", "source_key": "https://example.invalid/a.pdf",
                     "format": "pdf_text", "sha256": "0" * 64}],
        "watch": {"sources": [{"source_id": "s1", "content_sha256": None}]},
        "coverage": {"answer_type": "rule_contact"}, "build": {},
    }


class TestValidate(unittest.TestCase):
    def test_minimal_ok(self):
        self.assertEqual(schema.validate(minimal()), [])

    def bad(self, edit, *needles):
        d = minimal()
        edit(d)
        err = schema.validate(d)
        self.assertTrue(err, "誤りが出るはず")
        for n in needles:
            self.assertTrue(any(n in e for e in err), (n, err))

    def test_structural(self):
        self.bad(lambda d: d["schedules"][0]["unknown_ranges"][0].update(reason="maybe"), "決まった値")
        self.bad(lambda d: d["schedules"][0].update(foo=1), "知らない項目 foo")
        self.bad(lambda d: d["districts"][0]["members"][0].update(unit="city"), "決まった値")
        self.bad(lambda d: d["districts"][0]["members"][1]["conditions"].append({"kind": "maybe"}), "決まった値")
        self.bad(lambda d: d["periods"][0].pop("year_evidence"), "year_evidence が無い")
        self.bad(lambda d: d["schedules"][0].update(dates=["2026/04/03"]), "形")

    def test_answerable_rules(self):
        self.bad(lambda d: d["schedules"][0].update(blocked_by=["pending_printed"]), "blocked_by が空でないのに answerable")
        self.bad(lambda d: d["schedules"][0]["provenance"].update(status="single"), "読みの状態 single")
        self.bad(lambda d: d["schedules"][1].update(answerable=True, blocked_by=[], dates_origin="listed",
                                                    provenance={"readers": [], "status": "agreed"}), "mode dropoff")
        self.bad(lambda d: d["schedules"][1].update(blocked_by=[]), "理由) が空")
        self.bad(lambda d: d["schedules"][0].update(published=True), "published")
        self.bad(lambda d: d["schedules"][1].update(blocked_by=["because"]), "決まった形でない")
        self.bad(lambda d: d["schedules"][0]["gate"].update(unit="B"), "関門の単位は expanded")

    def test_blocked_reasons(self):
        for r in ("deferred_undated:お盆", "unconsumed_qualifier:q1", "markup_unhandled:blank",
                  "disputed:172073:fy2026:cell:x", "provisional_month"):
            self.assertTrue(schema.blocked_ok(r), r)
        for r in ("disputed", "deferred_undated:", "pending"):
            self.assertFalse(schema.blocked_ok(r), r)

    def test_version_unproven_required(self):
        def edit(d):
            d["periods"][0]["year_evidence"].update(ok=False)
            d["schedules"][0].update(answerable=False, blocked_by=["single_reading"])
        self.bad(edit, "version_unproven")

        def anchor_only(d):
            d["periods"][0]["year_evidence"]["token"]["where"] = "anchor_only"
        self.bad(anchor_only, "年の印")

    def test_dates_and_refs(self):
        self.bad(lambda d: d["schedules"][0].update(dates=["2026-04-07", "2026-04-03"]), "並んでいない")
        self.bad(lambda d: d["schedules"][0].update(dates=["2026-03-31"]), "期間の外")
        self.bad(lambda d: d["schedules"][0].update(expanded_with=["e9"]), "例外 e9 が無い")
        self.bad(lambda d: d["schedules"][0].update(caveats=["e1"]), "caveat・contingency だけ")
        self.bad(lambda d: d["exceptions"][0]["span"].update(source_id="s9"), "出典 s9")
        self.bad(lambda d: d.update(lg_code="172074"), "検査数字")
        self.bad(lambda d: d["districts"][0].update(district_id="a"), "頭に lg_code")
        self.bad(lambda d: d["districts"][0].update(place_resolution="polygon"), "いちばん弱いもの")
        self.bad(lambda d: d["schedules"].append(copy.deepcopy(d["schedules"][0])), "2 つある")

    def test_more_semantic_checks(self):
        """形の検査に通っても、中身として通してはいけないもの"""
        def printed_expanded(d):
            d["schedules"][0].update(dates_origin="printed", gate={"unit": "expanded", "open": False})
        self.bad(printed_expanded, "expanded にしない")
        self.bad(lambda d: d["periods"][0]["year_evidence"].update(time=[{"kind": "last_modified", "value": "2020-03-01"}]), "窓")
        self.bad(lambda d: d["periods"][0]["year_evidence"].update(
            time=[{"kind": "last_modified", "value": "2026-04-07", "in_window": False}]), "in_window")
        self.bad(lambda d: d["periods"][0]["year_evidence"].update(
            time=[{"kind": "last_modified", "value": "2026-04-07"}, {"kind": "pdf_modified", "value": "2020-03-05"}]), "窓より古い")
        self.bad(lambda d: d["periods"][0]["year_evidence"]["token"].update(fiscal_year=2020), "年度 2020")
        self.bad(lambda d: d["schedules"][0]["unknown_ranges"].append(
            {"from": "2027-03-30", "to": "2027-04-02", "reason": "deferred"}), "はみ出す")

        def dup(d):
            d["schedules"][1].update(blocked_by=["rule_unparsed", "rule_unparsed"])
        self.bad(dup, "同じ理由")
        for r in ("deferred_undated: ", "deferred_undated: お盆", "unconsumed_qualifier:q1 "):
            self.assertFalse(schema.blocked_ok(r), r)
        dynamic_only = minimal()
        dynamic_only["periods"][0]["year_evidence"]["time"] = [{"kind": "last_modified", "value": "2026-04-07", "note": "dynamic"}]
        self.assertTrue(any("窓" in e for e in schema.validate(dynamic_only)))      # 動的なページの Last-Modified は数えない

    def test_deferred_stronger_than_dates(self):
        """deferred に当たる答えるとした系列は、窓の日をすべて unknown_ranges にする。窓の無い deferred なら答えない"""
        def add(d, window):
            d["exceptions"].append({"exception_id": "e5", "kind": "deferred", "period_name": "年末年始", "window": window,
                                    "overrides_printed": True, "text": "年末年始は収集を休んだり、変更することがあります。"})
        self.bad(lambda d: add(d, {"from": "2026-12-15", "to": "2027-01-31", "default": True}), "unknown_ranges に入っていない")
        self.bad(lambda d: add(d, None), "窓の無い deferred")

        def covered(d):
            add(d, {"from": "2026-12-15", "to": "2027-01-31", "default": True})
            d["schedules"][0]["unknown_ranges"].append({"from": "2026-12-15", "to": "2027-01-31", "reason": "deferred",
                                                        "exception_id": "e5"})
        d = minimal()
        covered(d)
        self.assertEqual(schema.validate(d), [])

    def test_jsonschema_agrees(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema が無い")
        jsonschema.Draft202012Validator.check_schema(schema.JSON_SCHEMA)
        v = jsonschema.Draft202012Validator(schema.JSON_SCHEMA)
        self.assertEqual(list(v.iter_errors(minimal())), [])
        d = minimal()
        d["schedules"][0]["unknown_ranges"][0]["reason"] = "maybe"
        self.assertTrue(list(v.iter_errors(d)))

    def test_json_schema_file_current(self):
        p = schema.JSON_SCHEMA_FILE
        if not p.exists():
            self.skipTest("schedule-0.2.schema.json をまだ書いていない")
        self.assertEqual(json.loads(p.read_text("utf-8")), json.loads(json.dumps(schema.JSON_SCHEMA)))


class TestDayAnswer(unittest.TestCase):
    def test_unknown_stronger_than_dates(self):
        d = minimal()
        d["schedules"][0]["dates"].append("2026-09-22")
        a = schema.day_answer(d, "172073-a", "2026-09-22")
        self.assertNotIn("c1", a["categories"])
        self.assertIn(("c1", "holiday_kind_not_covered"), a["unknown"])

    def test_out_of_period_is_unknown(self):
        a = schema.day_answer(minimal(), "172073-a", "2027-04-02")
        self.assertEqual(a["status"], "unknown")
        self.assertEqual(a["categories"], [])

    def test_partial_status(self):
        """分からない品目がある日は partial (categories が空でも「収集なし」と言わない)"""
        d = minimal()
        d["schedules"][0]["unknown_ranges"].append({"from": "2026-04-10", "to": "2026-04-10", "reason": "deferred"})
        a = schema.day_answer(d, "172073-a", "2026-04-10")
        self.assertEqual(a["status"], "partial")
        self.assertEqual(a["categories"], [])
        self.assertEqual(schema.day_answer(d, "172073-a", "2026-04-08")["status"], "dates")     # どの品目も分かる (収集なし)

    def test_collected_and_published_only(self):
        d = minimal()
        self.assertEqual(schema.day_answer(d, "172073-a", "2026-04-03")["categories"], ["c1"])
        a = schema.day_answer(d, "172073-a", "2026-04-03", only_published=True)
        self.assertEqual(a["categories"], [])
        self.assertIn(("c1", "not_published"), a["unknown"])


if __name__ == "__main__":
    unittest.main()
