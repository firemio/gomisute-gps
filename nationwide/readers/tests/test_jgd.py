"""eval/jgd.py (japan-gomi-data を正解にした系列の採点) と eval/oracle/ の札のテスト (ネットにつながない)。
先方のデータ (CC BY 4.0、tecolicom) は data/readers/eval/ にあるときだけ使う。ほかは小さな合成の course-*.yaml で確かめる"""
import copy, datetime as dt, glob, json, pathlib, unittest

import _util
import jgd, content, decode

META = "code: 99999\nname_ja: テスト町\n"
COURSE_A = """metadata:
  city: test
  course: A
  course_name_ja: Aコース
  areas:
    - name: 本町
    - name: 割町
  period: 2026-04--2027-03
  source: {source_url: "https://example.invalid/a"}
rules:
  - {category: burnable, pattern: weekly, days: [MO, TH]}
  - {category: non_burnable, pattern: monthly_nth, days: [WE], occurrences: [2, 4]}
  - {category: glass_bottle, pattern: monthly_specific, dates: [2026-04-15, 2026-05-20, 2027-01-13]}
overrides:
  - {date: 2026-12-31, cancelled: true, category: burnable, note: 年末}
  - {date: 2027-01-14, category: non_burnable, note: 1月のずらし}
unknown_periods:
  - {from: 2027-01-01, to: 2027-01-03, reason: 後で知らせる}
"""
COURSE_B = """metadata:
  city: test
  course: B
  course_name_ja: Bコース
  areas:
    - name: 新町
    - name: 割町
  period: 2026-04--2026-09
  source: {source_url: "https://example.invalid/b"}
rules:
  - {category: burnable, pattern: weekly, days: [TU, FR]}
"""


def files():
    return {"municipalities/test/town/meta.yaml": META.encode("utf-8"),
            "municipalities/test/town/2026-04--2027-03/course-A.yaml": COURSE_A.encode("utf-8"),
            "municipalities/test/town/2026-04--2026-09/course-B.yaml": COURSE_B.encode("utf-8")}


def gt():
    return jgd.convert(files())[jgd.check_digit("99999")]


class TestExpand(unittest.TestCase):
    def test_categories_on(self):
        rules = [{"category": "burnable", "pattern": "weekly", "days": ["MO"]},
                 {"category": "non_burnable", "pattern": "monthly_nth", "days": ["MO"], "occurrences": [1]}]
        d = dt.date(2026, 4, 6)                                     # 第1月曜日
        self.assertEqual(jgd.categories_on(d, rules, []), (["burnable", "non_burnable"], None))
        self.assertEqual(jgd.categories_on(d, rules, [{"date": "2026-04-06", "cancelled": True, "category": "burnable"}]),
                         ([], "cancelled"))                          # cancelled はその日の全品目を消す
        self.assertEqual(jgd.categories_on(d, rules, [{"date": "2026-04-06", "category": "glass_bottle"}]),
                         (["burnable", "glass_bottle"], "override"))   # 月ごとの品目は落ちる

    def test_convert(self):
        g = gt()
        self.assertEqual(g["lg_code"], "999997")                           # 5 桁の団体コード + 検査数字 (JIS X 0402)
        a = next(c for c in g["courses"] if c["course"] == "A")
        b = next(c for c in g["courses"] if c["course"] == "B")
        self.assertNotIn("2026-12-31", a["by_category"]["burnable"])
        self.assertIn("2027-01-14", a["by_category"]["non_burnable"])      # 足した日
        self.assertFalse(any("2027-01-01" <= x <= "2027-01-03" for v in a["by_category"].values() for x in v))
        self.assertEqual(a["by_category"]["glass_bottle"], ["2026-04-15", "2026-05-20", "2027-01-13"])
        self.assertTrue(all(x <= "2026-09-30" for x in b["by_category"]["burnable"]))   # 期間の外は出さない
        wed = [x for x in a["by_category"]["non_burnable"] if x != "2027-01-14"]
        self.assertTrue(all((dt.date.fromisoformat(x).day - 1) // 7 + 1 in (2, 4) for x in wed))

    def test_upper95(self):
        self.assertEqual(jgd.upper95(0, 300), 0.01)
        self.assertAlmostEqual(jgd.upper95(1, 100), 0.0466, places=3)    # Clopper-Pearson の片側
        self.assertIsNone(jgd.upper95(0, 0))


class TestScore(unittest.TestCase):
    def test_selftest(self):
        r = jgd.selftest(gt())
        self.assertEqual(r["identity"]["wrong"], 0)
        self.assertGreater(r["identity"]["n"], 0)
        self.assertEqual(r["drop_one_day"]["wrong"], 1)
        self.assertGreater(r["swap_districts"]["series"]["wrong"], 0)
        self.assertEqual(r["nothing_answered"]["n"], 0)

    def test_split_town_not_used(self):
        g = gt()
        self.assertEqual(jgd.split_names(g), {"割町"})                     # 2 コースに同じ名前 (判別子の無い割れ町)
        doc = jgd.gt_to_v02(g)
        for d in doc["districts"]:
            d["members"] = [m for m in d["members"] if m["text"] == "割町"]
            d["label"] = "x"
        r = jgd.score_series(g, doc)
        self.assertEqual(r["n"], 0)
        self.assertTrue(all(u["why"] == "district_not_in_gt" for u in r["uncomparable"]))

    def test_uncomparable_category(self):
        g = gt()
        doc = jgd.gt_to_v02(g)
        doc["categories"].append({"category_id": "res", "official_label": "資源", "coarse": ["recyclable"], "mode": "curbside"})
        doc["partitions"][0]["category_ids"].append("res")
        s = copy.deepcopy(doc["schedules"][0])
        s["category_id"] = "res"
        doc["schedules"].append(s)
        r = jgd.score_series(g, doc)
        self.assertEqual(r["wrong"], 0)
        self.assertIn("category_not_in_gt", [u["why"] for u in r["uncomparable"]])

    def test_merged_label_wrong_when_dates_differ(self):
        """こちらの 1 品目が先方の 2 つの鍵 (日付が違う) をまとめていれば誤り"""
        g = gt()
        doc = jgd.gt_to_v02(g)
        c = next(x for x in doc["categories"] if x["category_id"] == "burnable")
        c["coarse"] = ["burnable", "glass_bottle"]
        self.assertGreater(jgd.score_series(g, doc)["wrong"], 0)

    def test_unknown_ranges_not_scored(self):
        g = gt()
        doc = jgd.gt_to_v02(g)
        s = next(x for x in doc["schedules"] if x["category_id"] == "burnable" and x["dates"])
        s["dates"] = [x for x in s["dates"] if not x.startswith("2026-12")]
        s["unknown_ranges"].append({"from": "2026-12-01", "to": "2026-12-31", "reason": "year_end_window"})
        self.assertEqual(jgd.score_series(g, doc)["wrong"], 0)            # 不明にした日は誤りにも正しいにも数えない

    def test_areas(self):
        g = gt()
        r = jgd.score_areas(g, jgd.gt_to_v02(g))
        self.assertEqual(r["area_recall"], 1.0)
        self.assertEqual(r["areas_wrong"], 0)
        self.assertEqual(r["ambiguous"], 2)                                 # 割町は聞き返す


    def test_areas_two_periods(self):
        """期間を 2 つに分けた地区 (女川・西東京の形) の系列は、その期間の中の日だけで比べる (J2 を誤りにしない)"""
        g = gt()
        doc = jgd.gt_to_v02(g)
        p = doc["periods"][0]
        a, b = dict(p, period_id="fy2026a", valid_to="2026-09-30"), dict(p, period_id="fy2026b", valid_from="2026-10-01")
        doc["periods"] = [a, b]
        out = []
        for s in doc["schedules"]:
            for q in (a, b):
                x = copy.deepcopy(s)
                x["period_id"] = q["period_id"]
                x["dates"] = [t for t in s["dates"] if q["valid_from"] <= t <= q["valid_to"]]
                x["unknown_ranges"] = [dict(u, **{"from": max(u["from"], q["valid_from"]), "to": min(u["to"], q["valid_to"])})
                                       for u in s["unknown_ranges"] if u["to"] >= q["valid_from"] and u["from"] <= q["valid_to"]]
                out.append(x)
        doc["schedules"] = out
        self.assertEqual(jgd.schema.validate(doc), [])
        r = jgd.score(g, doc)
        self.assertEqual(r["series"]["wrong"], 0)
        self.assertEqual(r["areas"]["areas_wrong"], 0)

    def test_gt_stale(self):
        """食い違いを gt_stale に分けてよいのは、両方の側の証拠があるときだけ (J7)"""
        g = gt()
        doc = jgd.gt_to_v02(g)
        s = next(x for x in doc["schedules"] if x["dates"])
        s["dates"] = s["dates"][1:]
        key = jgd.schema.series_key(s)
        good = {"lg_code": g["lg_code"], "series": key,
                "evidence": {"gt_extracted_at": "2026-04-01", "our_source_sha256": "0" * 64, "differs": "先方は改定前の版"}}
        bad = {"lg_code": g["lg_code"], "series": key, "evidence": {"differs": "古いと思う"}}
        r = jgd.score_series(g, doc, stale=[good])
        self.assertEqual((r["wrong"], len(r["gt_stale"])), (0, 1))
        r = jgd.score_series(g, doc, stale=[bad])
        self.assertEqual((r["wrong"], len(r["gt_stale_rejected"])), (1, 1))
        self.assertEqual(jgd.check_stale([good, bad])["with_evidence"], 1)
        self.assertFalse(jgd.check_stale([good, bad])["ok"])
        self.assertEqual(jgd.load_stale(), [])                               # いまは 0 件

    def test_oracle_j4(self):
        g = gt()
        doc = jgd.gt_to_v02(g)
        oracle = {"lg_code": g["lg_code"], "human_checked": True, "period_of_interest": {"valid_from": "2026-04-01", "valid_to": "2027-03-31"},
                  "categories": [{"coarse": ["burnable"], "expected": "answerable", "district_overrides": []},
                                 {"coarse": ["non_burnable"], "expected": "not_answerable", "district_overrides": []},
                                 {"coarse": ["glass_bottle"], "expected": "answerable",
                                  "district_overrides": [{"districts": "Bコース", "expected": "not_answerable", "why": "x"}]}]}
        r = jgd.score_oracle(oracle, doc)
        n_dist = len(doc["districts"])
        self.assertEqual((r["answerable_expected"], r["answered_of_expected"]), (n_dist, n_dist))
        self.assertEqual((r["not_answerable_expected"], r["answered_of_not"]), (n_dist, n_dist))   # 答えてはいけない系列を答えた
        self.assertFalse(r["ok"])
        self.assertEqual(r["unknown"], n_dist)                               # district_ids の無い上書き → 決まらない
        oracle["categories"][2]["district_overrides"][0]["district_ids"] = [doc["districts"][1]["district_id"]]
        r = jgd.score_oracle(oracle, doc)
        self.assertEqual(r["unknown"], 0)
        draft = dict(oracle, human_checked=False)
        self.assertFalse(jgd.score_oracle(draft, doc)["counted"])            # 下書きの札は数えない

    def test_score_unmeasurable_exit(self):
        """答えるとした系列が無ければ「測れない」(2)。合格 (0) にしない"""
        import contextlib, io, tempfile
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(jgd.main(["score", "--schedules", d]), 2)
            self.assertEqual(jgd.main(["oracle", "--schedules", d]), 2)


class TestFetchedData(unittest.TestCase):
    """data/readers/eval/ に先方のデータがあるときだけ"""

    def test_check_port(self):
        if jgd._cached_body(jgd.TARBALL_URL) is None:
            self.skipTest("japan-gomi-data の tar.gz を取っていない (jgd.py fetch)")
        ok, n, diffs = jgd.check_port()
        if n < len(jgd.PORT_COURSES):
            self.skipTest("公開の JSON を取っていない (jgd.py fetch --pages)")
        self.assertEqual((ok, n), (8, 8), diffs)

    def test_tier_a_selftest(self):
        if jgd._cached_body(jgd.TARBALL_URL) is None:
            self.skipTest("japan-gomi-data の tar.gz を取っていない")
        gts = jgd.convert(jgd.load_tar())
        for lg, t in jgd.tiers().items():
            if t["tier"] != "A":
                continue
            r = jgd.selftest(gts[lg])
            self.assertEqual(r["identity"]["wrong"], 0, lg)
            self.assertEqual(r["drop_one_day"]["wrong"], 1, lg)


class TestTiersAndOracle(unittest.TestCase):
    def test_tiers(self):
        t = jgd.tiers()
        a = [r for r in t.values() if r["tier"] == "A"]
        self.assertEqual(len(t), 38)
        self.assertEqual(len(a), 17)
        self.assertEqual(sum(1 for r in a if r.get("m1_type")), 8)
        doc = json.loads(jgd.TIERS_FILE.read_text("utf-8"))
        self.assertIn("CC BY 4.0", doc["attribution"])
        self.assertIn("tecolicom", doc["attribution"])

    def test_oracle_files(self):
        tiers = jgd.tiers()
        files = sorted(glob.glob(str(jgd.HERE / "oracle" / "*.json")))
        self.assertEqual(len(files), 8)
        for p in files:
            o = json.loads(pathlib.Path(p).read_text("utf-8"))
            self.assertEqual(o["format"], "gomisute-oracle/0.1")
            self.assertEqual(tiers[o["lg_code"]]["tier"], "A")
            self.assertFalse(o["human_checked"])                            # 下書き。人が確かめるまで J4 に数えない
            ids = {d["doc_id"] for d in o["documents"]}
            for c in o["categories"]:
                self.assertIn(c["expected"], ("answerable", "not_answerable", "unknown"))
                self.assertIn(c["printed_dates"], ("yes", "no", "unknown"))
                self.assertTrue(set(c["coarse"]) <= set(jgd.VOCAB) | {"recyclable", "other"})
                if c["expected"] == "answerable":
                    self.assertEqual(c["expected_blocked_by"], [])
                for e in c["evidence"]:
                    self.assertIn(e["doc_id"], ids)

    def test_oracle_quotes_in_cache(self):
        n = 0
        for p in sorted(glob.glob(str(jgd.HERE / "oracle" / "*.json"))):
            o = json.loads(pathlib.Path(p).read_text("utf-8"))
            texts = {}
            for d in o["documents"]:
                b = _util.cached(d["url"])
                if b is None:
                    continue
                if b[:5] == b"%PDF-":
                    import fitz
                    with fitz.open(stream=b, filetype="pdf") as doc:
                        t = "".join(pg.get_text() for pg in doc)
                else:
                    t = content.parse(decode.decode(b).text).get_text(" ")
                texts[d["doc_id"]] = content.squash(t)
            for c in o["categories"]:
                for e in c["evidence"]:
                    if e["doc_id"] in texts:
                        for part in e["quote"].split(" / "):
                            n += 1
                            self.assertIn(content.squash(part), texts[e["doc_id"]], (o["lg_code"], part))
        if n == 0:
            self.skipTest("札の資料がキャッシュに無い")


if __name__ == "__main__":
    unittest.main()
