"""decode.py (文字コードの判定を 1 つに) と holidays.py (休日の種類つきの表) のテスト (ネットにつながない)"""
import codecs, datetime as dt, unittest

import _util
import decode, holidays


class TestDecode(unittest.TestCase):
    def test_bom_beats_header(self):
        body = codecs.BOM_UTF8 + "町名,可燃ごみ\n阿佐谷北,水曜日\n".encode("utf-8")
        d = decode.decode(body, "text/csv; charset=Shift_JIS", html=False)
        self.assertEqual((d.encoding, d.how), ("utf-8-sig", "bom"))
        self.assertTrue(d.text.startswith("町名"))
        self.assertTrue(any("BOM" in c for c in d.conflicts))      # 見出しとぶつかったことを記録する

    def test_utf16_bom(self):
        d = decode.decode(codecs.BOM_UTF16_LE + "ごみ".encode("utf-16-le"), "", html=False)
        self.assertEqual((d.text, d.how), ("ごみ", "bom"))

    def test_http_then_meta(self):
        body = "<html><head><meta charset='utf-8'></head><body>可燃ごみ</body></html>".encode("cp932")
        d = decode.decode(body, "text/html; charset=Shift_JIS")
        self.assertEqual((d.encoding, d.how), ("cp932", "http"))
        self.assertTrue(any("ぶつかった" in c for c in d.conflicts))
        body = "<html><head><meta http-equiv='Content-Type' content='text/html; charset=Shift_JIS'></head><body>資源</body></html>".encode("cp932")
        d = decode.decode(body, "text/html")
        self.assertEqual((d.encoding, d.how), ("cp932", "meta"))

    def test_fallbacks(self):
        self.assertEqual(decode.decode("燃やすごみ".encode("utf-8"), "").how, "utf8")
        d = decode.decode("燃やすごみ,火曜日".encode("cp932"), "", html=False)
        self.assertEqual((d.encoding, d.how), ("cp932", "cp932"))

    def test_declared_wrong_is_recorded(self):
        d = decode.decode("燃やすごみ".encode("utf-8"), "text/html; charset=EUC-JP")
        self.assertEqual(d.how, "utf8")
        self.assertTrue(d.conflicts)

    def test_stops_when_unreadable(self):
        with self.assertRaises(decode.DecodeError):
            decode.decode(b"\x80\x81\xff\xfe\x00\x81", "", html=False)
        euc = ("可燃ごみは月曜日と木曜日に収集します。資源ごみは第二水曜日です。" * 3).encode("euc_jp")
        with self.assertRaises(decode.DecodeError):          # 宣言の無い EUC-JP を cp932 で読むと半角カナだらけ → 止める
            decode.decode(euc, "", html=False)
        self.assertEqual(decode.decode(euc, "text/plain; charset=EUC-JP", html=False).encoding, "euc_jp")


class TestHolidays(unittest.TestCase):
    def test_fy2026(self):
        fy = holidays.fiscal_year(2026)
        self.assertEqual(len(fy), 19)
        self.assertEqual(fy[dt.date(2026, 5, 6)]["kind"], "substitute")
        self.assertEqual(fy[dt.date(2026, 9, 22)]["kind"], "citizens")
        self.assertEqual(fy[dt.date(2027, 3, 22)]["kind"], "substitute")
        self.assertEqual(fy[dt.date(2027, 3, 21)]["name"], "春分の日")
        self.assertEqual(fy[dt.date(2026, 7, 20)]["name"], "海の日")

    def test_fy2020_olympic(self):
        """2020 年度と 2026 年度は曜日の並びが同じだが、祝日は違う"""
        self.assertEqual(dt.date(2020, 4, 1).weekday(), dt.date(2026, 4, 1).weekday())
        fy20 = holidays.fiscal_year(2020)
        for d in (dt.date(2020, 7, 23), dt.date(2020, 7, 24), dt.date(2020, 8, 10)):
            self.assertIn(d, fy20)
        self.assertNotIn(dt.date(2020, 7, 20), fy20)
        self.assertNotIn(dt.date(2020, 10, 12), fy20)
        self.assertIn(dt.date(2021, 7, 22), holidays.fiscal_year(2021))
        self.assertIn(dt.date(2021, 8, 9), holidays.fiscal_year(2021))     # 山の日 8/8 (日) の振替休日

    def test_equinox_table_matches_formula(self):
        for y, v in holidays.EQUINOX.items():
            self.assertEqual(holidays.equinox_calc(y), v)

    def test_provisional(self):
        h = holidays.holiday(dt.date(2028, 3, 20))
        self.assertEqual(h["name"], "春分の日")
        self.assertTrue(h["provisional"])                  # 2027 年 2 月の官報までは計算の値
        self.assertFalse(holidays.holiday(dt.date(2027, 3, 21))["provisional"])
        with self.assertRaises(ValueError):
            holidays.holiday(dt.date(2019, 5, 1))

    def test_check_against_cao(self):
        lines = ["国民の祝日・休日月日,国民の祝日・休日名称"] + [f"{d.year}/{d.month}/{d.day},{v['name']}"
                                                         for d, v in holidays.table().items() if v["kind"] == "national"]
        lines += [f"{d.year}/{d.month}/{d.day},休日" for d, v in holidays.table().items() if v["kind"] != "national"]
        csv_bytes = "\n".join(lines).encode("cp932")
        self.assertEqual(holidays.check_against_cao(csv_bytes), {"missing": [], "extra": [], "name_diff": []})
        bad = csv_bytes.replace("2026/7/20,海の日".encode("cp932"), "2026/7/21,海の日".encode("cp932"))
        r = holidays.check_against_cao(bad)
        self.assertEqual(r["missing"], ["2026-07-21"])
        self.assertEqual(r["extra"], ["2026-07-20"])


if __name__ == "__main__":
    unittest.main()
