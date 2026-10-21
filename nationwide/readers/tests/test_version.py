"""version.py: 年の印・見える文字・時の証拠・版の証拠・祝日の印の突き合わせ (ネットにつながない)。
合成の版の罠 (§6.2) は、ここで PDF を作って確かめる"""
import datetime as dt, unittest

import _util
import version


def make_pdf(spans, mod=None, created=None, rect=None):
    """[(文字, 色 (r,g,b) 0〜1, 大きさ, (x, y))] → PDF の bytes。rect があれば先に塗りの矩形を描く ((x0,y0,x1,y1), 色)"""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if rect:
        r, color = rect
        page.draw_rect(fitz.Rect(*r), color=color, fill=color)
    for text, color, size, (x, y) in spans:
        page.insert_text((x, y), text, fontname="japan", fontsize=size, color=color)
    md = {}
    if mod:
        md["modDate"] = mod
    if created:
        md["creationDate"] = created
    if md:
        doc.set_metadata(md)
    b = doc.tobytes()
    doc.close()
    return b


class TestTokens(unittest.TestCase):
    def test_patterns(self):
        cases = {"令和8年度ごみ収集日程表": 2026, "令和８年度": 2026, "R8年度": 2026, "2026年度地区別収集カレンダー": 2026,
                 "令和元年度": 2019, "平成31年度": 2019}
        for t, fy in cases.items():
            toks = version.year_tokens(t)
            self.assertEqual(toks[0]["fiscal_year"], fy, t)
        sp = version.year_tokens("令和8年度(Ｒ8年4月1日～Ｒ9年3月31日)")
        self.assertTrue(any(x["kind"] == "span" and x["from"] == dt.date(2026, 4, 1) and x["to"] == dt.date(2027, 3, 31) for x in sp))
        sp = version.year_tokens("令和８年（２０２６年）４月～令和９年（２０２７年）３月")
        self.assertTrue(any(x["fiscal_year"] == 2026 for x in sp))
        self.assertEqual(version.year_tokens("2026年4月～2027年3月")[0]["kind"], "span")
        self.assertEqual(version.year_tokens("毎週 月曜日・木曜日"), [])

    def test_filename(self):
        for u, fy in (("https://x/R8_01.pdf", 2026), ("https://x/pdf/calendar/r08_chomin_calendar.pdf", 2026),
                      ("https://x/reiwa8nendogominitteihyou.pdf", 2026), ("https://x/2026tikubetsu.xls", 2026),
                      ("https://x/R8gomisyuusyunittei.pdf", 2026)):
            self.assertIn(fy, [t["fiscal_year"] for t in version.filename_tokens(u)], u)
        self.assertEqual(version.filename_tokens("https://x/page003543.html"), [])

    def test_matches(self):
        tok = version.year_tokens("令和8年度")[0]
        self.assertTrue(version.token_matches(tok, "2026-04-01", "2027-03-31"))
        self.assertFalse(version.token_matches(tok, "2020-04-01", "2021-03-31"))
        cal = version.year_tokens("2026年")[0]                     # 川口の暦年の期間
        self.assertTrue(version.token_matches(cal, "2026-01-01", "2026-12-31"))


class TestVisible(unittest.TestCase):
    def test_white_text_not_counted(self):
        """川越の白い「令和２年度」: 白い文字は、下に塗りが無ければ見えない"""
        b = make_pdf([("令和８年度 ごみ収集カレンダー", (0, 0, 0), 20, (50, 80)), ("令和２年度", (1, 1, 1), 20, (50, 80))])
        title, body, hidden = version.pdf_tokens(b)
        self.assertEqual({t["fiscal_year"] for t in body}, {2026})
        self.assertEqual({t["fiscal_year"] for t in hidden}, {2020})

    def test_white_on_dark_band_counted(self):
        b = make_pdf([("令和８年度", (1, 1, 1), 20, (60, 90))], rect=((40, 60, 400, 110), (0.1, 0.3, 0.6)))
        _, body, hidden = version.pdf_tokens(b)
        self.assertEqual([t["fiscal_year"] for t in body], [2026])
        self.assertEqual(hidden, [])

    def test_tiny_text_not_counted(self):
        """日高の「使い方」の図に縮めて入った令和６年度の表 (3pt より小さい)"""
        b = make_pdf([("令和８年度 地区別ごみ収集日程表", (0, 0, 0), 18, (50, 80)), ("令和６年度 地区別ごみ収集日程表", (0, 0, 0), 1.5, (50, 200))])
        _, body, hidden = version.pdf_tokens(b)
        self.assertEqual({t["fiscal_year"] for t in body}, {2026})
        self.assertEqual({t["fiscal_year"] for t in hidden}, {2024})


class TestEvidence(unittest.TestCase):
    def test_synthetic_trap(self):
        """合成の版: メタデータの更新日が 2020 年、中に年の印が無く、Last-Modified が 2026 年、リンクの文字が「令和8年度」
        → 年の印は anchor_only、時の証拠は conflict → ok にしない (version の裁定へ)"""
        b = make_pdf([("ごみ収集日程表", (0, 0, 0), 20, (50, 80)), ("可燃ごみ 月・木", (0, 0, 0), 12, (50, 120))],
                     mod="D:20200305101010+09'00'")
        title, body, hidden = version.pdf_tokens(b)
        times = version.pdf_times(b) + version.http_times("Tue, 07 Apr 2026 00:26:27 GMT", "2026-10-19T03:14:18+09:00")
        ev = version.year_evidence("2026-04-01", "2027-03-31", title, body, version.filename_tokens("https://x/gomi.pdf"),
                                   version.year_tokens("令和8年度ごみ収集日程表 (PDFファイル: 83.3KB)"), times, hidden)
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["token"]["where"], "anchor_only")
        self.assertEqual(ev["conflict"]["kind"], "time")
        self.assertIn("anchor_only", ev["reasons"])

    def test_ok(self):
        b = make_pdf([("令和８年度 ごみ収集日程表", (0, 0, 0), 20, (50, 80))], mod="D:20260324000000+09'00'")
        title, body, hidden = version.pdf_tokens(b)
        ev = version.year_evidence("2026-04-01", "2027-03-31", title, body, [], [], version.pdf_times(b))
        self.assertTrue(ev["ok"], ev)
        self.assertEqual(ev["token"]["where"], "in_file")

    def test_time_rules(self):
        tok = version.year_tokens("令和8年度")
        lm_dyn = version.http_times("Sun, 18 Oct 2026 19:00:54 GMT", "2026-10-19T04:22:42+09:00")
        self.assertEqual(lm_dyn[0]["note"], "dynamic")                   # 動的なページの Last-Modified は数えない
        ev = version.year_evidence("2026-04-01", "2027-03-31", tok, [], [], [], lm_dyn)
        self.assertFalse(ev["ok"])
        self.assertIn("no_time_evidence", ev["reasons"])
        old = [{"kind": "page_updated", "value": "2024-06-06"}]
        ev = version.year_evidence("2026-04-01", "2027-03-31", tok, [], [], [], old)
        self.assertFalse(ev["ok"])
        self.assertIn("time_old", ev["reasons"])
        early = [{"kind": "page_updated", "value": "2025-01-15"}]        # 始まりの 15 か月前より後なら通す
        self.assertTrue(version.year_evidence("2026-04-01", "2027-03-31", tok, [], [], [], early)["ok"])

    def test_title_token_conflict(self):
        both = version.year_tokens("令和7年度 令和8年度 収集カレンダー")
        ev = version.year_evidence("2026-04-01", "2027-03-31", both, [], [], [], [{"kind": "last_modified", "value": "2026-03-01"}])
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["conflict"]["kind"], "token")

    def test_weak_calendar_year(self):
        """年度の期間では、暦年だけの印 (「2026年1月」) は年の印にならない (2025 年度の資料にもある)"""
        ev = version.year_evidence("2026-04-01", "2027-03-31", [], version.year_tokens("ごみ収集カレンダー 2026年1月"), [], [],
                                   [{"kind": "last_modified", "value": "2025-03-01"}])
        self.assertFalse(ev["ok"])
        self.assertIsNone(ev["token"])
        self.assertIn("weak_year_token", ev["reasons"])
        self.assertTrue(version.token_weak(version.year_tokens("2026年")[0], "2026-04-01", "2027-03-31"))
        self.assertFalse(version.token_matches(version.year_tokens("2026年")[0], "2026-04-01", "2027-03-31"))

    def test_updated_date_not_token(self):
        """年の無い年末年始のページ (江東の形) の本文の範囲に「更新日：2026年4月7日」があっても、年の印にしない (時の証拠にはなる)"""
        html = ("<html><head><title>年末年始のごみ収集</title></head><body><main><h1>年末年始のごみ収集</h1>"
                "<p>年末は12月30日（水曜日）まで、年始は1月4日（月曜日）から通常どおり収集します。</p>"
                "<p>更新日：2026年4月7日</p></main></body></html>")
        title, body = version.html_tokens(html)
        self.assertEqual(title + body, [])
        times = [version._time("page_updated", d) for d in version.page_updated(html)]
        self.assertEqual(times[0]["value"], "2026-04-07")
        ev = version.year_evidence("2026-04-01", "2027-03-31", title, body, version.filename_tokens("https://x/nenmatunenshi2019.html"),
                                   [], times)
        self.assertFalse(ev["ok"])
        self.assertIn("year_token_outside_period", ev["reasons"])
        self.assertEqual(version.year_tokens("最終更新日：令和8年4月1日 令和8年度"), version.year_tokens("令和8年度"))

    def test_token_conflicts_across_places(self):
        """題・ファイルの名前・本文の年の印のぶつかり (§3.3 の 1) は ok にしない"""
        t = [{"kind": "last_modified", "value": "2026-03-01"}]
        ev = lambda title="", body="", url="": version.year_evidence(
            "2026-04-01", "2027-03-31", version.year_tokens(title), version.year_tokens(body), version.filename_tokens(url), [], t)
        for e in (ev(body="令和2年度 ごみカレンダー", url="https://x/R8_cal.pdf"),            # 本文は令和2年度だけ、名前は R8
                  ev(title="令和8年度 ごみ収集カレンダー", url="https://x/R7_calendar.pdf"),
                  ev(title="令和8年度 ごみ収集カレンダー", url="https://x/gomi2019.pdf")):
            self.assertFalse(e["ok"], e)
            self.assertEqual(e["conflict"]["kind"], "token")
        ok = ev(title="令和8年度 ごみ収集カレンダー", body="令和8年度 … 令和7年度の実績は…", url="https://x/R8_calendar.pdf")
        self.assertTrue(ok["ok"], ok)                                 # 本文に合う印があれば、記事の別の年度はぶつかりにしない
        cal = version.year_evidence("2026-01-01", "2026-12-31", version.year_tokens("2026年 ごみカレンダー"), [], [], [], t)
        self.assertTrue(cal["ok"])                                    # 暦年の期間なら暦年の印で足りる

    def test_undated(self):
        ev = version.year_evidence("2026-10-19", None, [], [], [], [], [])
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["reasons"], ["undated"])

    def test_page_updated(self):
        self.assertEqual(version.page_updated("更新日：2026年3月1日"), [dt.date(2026, 3, 1)])
        self.assertEqual(version.page_updated("最終更新日 2026-04-01"), [dt.date(2026, 4, 1)])
        self.assertEqual(version.page_updated("更新日\n2026年3月1日"), [dt.date(2026, 3, 1)])
        self.assertEqual(version.page_updated("更新日：令和8年4月1日"), [dt.date(2026, 4, 1)])


class TestHolidayMarks(unittest.TestCase):
    def test_reused_2020_calendar(self):
        """2020 年度のカレンダーの使い回し: 7/23・7/24・8/10 の祝日の印が出る (2026 年度は平日)"""
        import holidays
        fy20 = holidays.fiscal_year(2020)
        shift = dt.date(2026, 4, 1) - dt.date(2020, 4, 1)
        marked = [d + shift for d in fy20]                               # 2020 年度の印を 2026 年度の暦に写したもの
        r = version.holiday_mark_check(marked, "2026-04-01", "2027-03-31")
        self.assertFalse(r["ok"])
        self.assertIn("2026-07-23", r["extra"])
        self.assertIn(2020, r["other_fy"])
        good = version.holiday_mark_check(list(holidays.fiscal_year(2026)), "2026-04-01", "2027-03-31")
        self.assertTrue(good["ok"])


class TestCached(unittest.TestCase):
    def test_hidaka_r6_miniature_not_counted(self):
        b = _util.cached("https://www.city.hidaka.lg.jp/material/files/group/13/reiwa8nendogominitteihyou.pdf")
        if b is None:
            self.skipTest("キャッシュに無い")
        title, body, hidden = version.pdf_tokens(b)
        self.assertNotIn(2024, {t["fiscal_year"] for t in title + body})   # 3pt より小さい令和６年度は数えない
        self.assertIn(2024, {t["fiscal_year"] for t in hidden})
        ev = version.year_evidence("2026-04-01", "2027-03-31", title, body, [], [], version.pdf_times(b))
        self.assertTrue(ev["ok"])


if __name__ == "__main__":
    unittest.main()
