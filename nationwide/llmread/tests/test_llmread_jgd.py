"""jgd.py (japan-gomi-data を正解にする: 広げ方・名前の当て・使える度合いの表) のテスト (ネットにつながない)。
先方のデータ (CC BY 4.0、tecolicom) は data/readers/eval/ にあるときだけ使う。ほかは小さな合成の course-*.yaml で確かめる"""
import datetime as dt, json, unittest

import _util
import jgd

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



class TestNames(unittest.TestCase):
    def test_split_town_and_area_map(self):
        g = gt()
        self.assertEqual(jgd.split_names(g), {"割町"})                     # 2 コースに同じ名前 (判別子の無い割れ町)
        doc = {"districts": [{"district_id": "d1", "label": "Aコース", "members": [{"text": "本町"}]},
                             {"district_id": "d2", "label": "x", "members": [{"text": "新町"}, {"text": "割町"}]}]}
        m = jgd.area_map(g, doc)
        self.assertEqual(m[("A", "本町")], {"d1"})
        self.assertEqual(m[("B", "新町")], {"d2"})
        self.assertEqual(m[("A", "割町")], {"d2"})                         # 名前では当たる。使わないのは score.py (split_names)

    def test_nk(self):
        self.assertEqual(jgd.nk("霞ヶ丘 三丁目"), jgd.nk("霞ケ丘3丁目"))
        self.assertEqual(jgd.nk("1～3丁目"), jgd.nk("1〜3丁目"))


class TestFetchedData(unittest.TestCase):
    """data/readers/eval/ に先方のデータがあるときだけ"""

    def test_check_port(self):
        if jgd._cached_body(jgd.TARBALL_URL) is None:
            self.skipTest("japan-gomi-data の tar.gz を取っていない (jgd.py fetch)")
        ok, n, diffs = jgd.check_port()
        if n < len(jgd.PORT_COURSES):
            self.skipTest("公開の JSON を取っていない (jgd.py fetch --pages)")
        self.assertEqual((ok, n), (8, 8), diffs)


class TestTiers(unittest.TestCase):
    def test_tiers(self):
        t = jgd.tiers()
        a = [r for r in t.values() if r["tier"] == "A"]
        self.assertEqual(len(t), 38)
        self.assertEqual(len(a), 17)
        doc = json.loads(jgd.TIERS_FILE.read_text("utf-8"))
        self.assertIn("CC BY 4.0", doc["attribution"])
        self.assertIn("tecolicom", doc["attribution"])


if __name__ == "__main__":
    unittest.main()
