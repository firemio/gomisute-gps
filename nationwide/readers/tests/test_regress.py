"""eval/regress.py: 金沢・女川の今のデータを v0.2 に書き直す (K1・K3 の分け方・O1)。
入力は out/ (git に入っている) と data/ の下のキャッシュ (git に入らない)。無ければ飛ばす"""
import collections, datetime as dt, json, unittest

import _util
import regress, schema

ROOT = _util.ROOT
NEED_KZ = [ROOT / "out" / "kanazawa_schedules.json", ROOT / "data" / "kanazawa_rules.csv", ROOT / "data" / "kanazawa_rules.html",
           ROOT / "data" / "kanazawa_cal"]
NEED_ONA = [ROOT / "out" / "onagawa_data.json", ROOT / "out" / "onagawa_reading.json"]


def have(paths):
    return all(p.exists() for p in paths)


@unittest.skipUnless(have(NEED_KZ), "金沢の入力 (data/kanazawa_*) が無い")
class TestKanazawa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v01 = json.loads((ROOT / "out" / "kanazawa_schedules.json").read_text("utf-8"))
        cls.doc, cls.queue = regress.kanazawa_v02()

    def test_schema(self):
        self.assertEqual(schema.validate(self.doc), [])
        self.assertEqual(len(self.doc["schedules"]), 355)
        self.assertEqual(len(self.doc["districts"]), 71)

    def test_k1(self):
        r = regress.check_k1(self.v01, self.doc)
        self.assertEqual((r["series"], r["series_equal"]), (355, 355))
        self.assertEqual(r["roundtrip_ok"], r["roundtrip_checked"])
        self.assertEqual((r["district_days"], r["district_days_equal"]), (71 * 371, 71 * 371))
        self.assertTrue(r["ok"], r["diffs"][:3])

    def test_k3_split(self):
        r = regress.check_k3(self.doc, self.queue)
        self.assertEqual(r["explained"], 13)
        self.assertEqual(r["explained_by_cat_month"], {"plastic:2026-12": 13})
        self.assertEqual(r["queued"], 62)
        self.assertEqual(r["queued_by_cat_month"], {"nonburnable:2027-01": 10, "plastic:2026-12": 18,
                                                    "plastic:2027-01": 17, "recyclable:2027-01": 17})
        self.assertTrue(all(x["decision"] is None for x in self.queue))     # 人が決めるまで開いたまま

    def test_blocked_and_not_published(self):
        c = collections.Counter(b.split(":")[0] for s in self.doc["schedules"] for b in s["blocked_by"])
        self.assertEqual(c["single_reading"], 355)                          # 読みが kz_calendar.py の 1 つだけ
        self.assertEqual(c["disputed"], 62)
        self.assertFalse(any(s["answerable"] or s["published"] for s in self.doc["schedules"]))
        burn = [s for s in self.doc["schedules"] if s["category_id"] == "burnable"]
        self.assertTrue(all(s["dates_origin"] == "expanded" and s["rule"]["ir"]["kind"] == "weekly" for s in burn))
        self.assertTrue(all(s["dates_origin"] == "printed" for s in self.doc["schedules"] if s["category_id"] != "burnable"))

    def test_city_year_end_and_holiday(self):
        ex = {e["exception_id"]: e for e in self.doc["exceptions"]}
        self.assertEqual((ex["e_city_year_end"]["last"], ex["e_city_year_end"]["first"]), ("2026-12-29", "2027-01-04"))
        self.assertIsNone(ex["e_city_year_end"]["others"])                  # ほかの回の数え方は書いていない
        self.assertEqual(ex["e_holiday"]["covers"], ["all_weekdays"])

    def test_city_year_end_text_verbatim(self):
        """市全体の年末年始の文と span は原文どおり (かっこ書きを重ねない)。span の要素の行をつなぐと text になる"""
        import content, decode
        e = next(x for x in self.doc["exceptions"] if x["exception_id"] == "e_city_year_end")
        self.assertEqual(e["text"], "年末 ： 12月29日（火曜日）まで\n年始 ： 1月4日（月曜日）より")
        self.assertEqual(e["span"]["text"], e["text"])
        self.assertNotIn("start", e["span"])
        html = decode.decode((ROOT / "data" / "kanazawa_rules.html").read_bytes(), "text/html").text
        reg = content.content_region(html)
        soup = reg.root
        while soup.parent is not None:
            soup = soup.parent
        el = soup.select_one(e["span"]["selector"])
        self.assertIsNotNone(el)
        self.assertEqual("\n".join(l.text for l in content.lines(el)), e["text"])

    def test_no_local_paths(self):
        text = json.dumps(self.doc, ensure_ascii=False)
        for bad in ("C:\\", "C:/", "/Users/", "/home/", "AppData"):
            self.assertNotIn(bad, text)

    def test_legacy_occurrences_same_as_resolve(self):
        try:
            import resolve
        except Exception as e:                                              # geopandas などが無い
            self.skipTest(f"resolve.py を読めない ({type(e).__name__})")
        for s in self.v01["schedules"][:40]:
            self.assertEqual(regress.legacy_occurrences(s), resolve.Kanazawa._occurrences(s))


@unittest.skipUnless(have(NEED_ONA), "女川の入力が無い")
class TestOnagawa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.final = json.loads((ROOT / "out" / "onagawa_reading.json").read_text("utf-8"))["final"]
        cls.doc = regress.onagawa_v02()

    def test_schema(self):
        self.assertEqual(schema.validate(self.doc), [])

    def test_o1(self):
        r = regress.check_o1(self.final, self.doc)
        self.assertEqual((r["cells"], r["cells_equal"]), (324, 324))
        self.assertEqual((r["final_dates"], r["v02_dates"]), (742, 742))

    def test_calendar_is_text_pdf(self):
        """町民カレンダーは文字の PDF (型 F。文字層がある)。gate.unit F と合う"""
        src = next(x for x in self.doc["sources"] if x["source_id"] == "s_cal")
        self.assertEqual(src["format"], "pdf_text")
        self.assertTrue(all(s["gate"]["unit"] == "F" for s in self.doc["schedules"]))

    def test_two_periods(self):
        self.assertEqual([p["period_id"] for p in self.doc["periods"]], ["fy2026a", "fy2026b"])
        self.assertEqual(self.doc["periods"][1]["valid_from"], "2026-10-01")

    def test_enoshima_rule_contact(self):
        s = [x for x in self.doc["schedules"] if x["district_id"].endswith("enoshima")]
        self.assertTrue(s and all(not x["answerable"] and "year_end_missing" in x["blocked_by"] for x in s))

    def test_agreed_weak_but_not_answerable(self):
        printed = [s for s in self.doc["schedules"] if s["dates_origin"] == "printed"]
        self.assertEqual(len(printed), 52)
        self.assertTrue(all(s["provenance"]["status"] == "agreed_weak" for s in printed))    # 同じモデル (Opus) 2 人の一致
        self.assertTrue(all(s["blocked_by"] == ["reader_not_built"] for s in printed))      # 但し書きの洗い出しがまだ (型 F は M2)
        self.assertFalse(any(s["answerable"] or s["published"] for s in self.doc["schedules"]))
        self.assertTrue(all(m["unit"] in ("community", "island") for d in self.doc["districts"] for m in d["members"]))


if __name__ == "__main__":
    unittest.main()
