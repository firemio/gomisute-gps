"""htmlbody.py: HTML の本文の範囲と使い回しの塊 (ネットにつながない)

本文の範囲はページの形 (タグ・role・id・class・使い回しの塊) だけで決め、文の意味では外さない。
fixtures/ の HTML はテスト用に作ったもの (実際のページの形をまねた)。実際のページはキャッシュにあるときだけ確かめる。
"""
import unittest

import _util
import decode, htmlbody


def region_of(name):
    return htmlbody.content_region(_util.fixture(name))


class TestRegion(unittest.TestCase):
    def test_footer_and_header_removed(self):
        reg = region_of("cms_footer.html")
        self.assertEqual(reg.method, "cms:#main_body")
        text = reg.text()
        self.assertNotIn("閉庁日", text)                    # ページの足 (div#footer) の開庁時間 (塩竈の形)
        self.assertNotIn("このページに関するお問い合わせ", text)   # div#section_footer
        self.assertNotIn("文字サイズ", text)                # div#header
        self.assertIn("年末年始は12月29日から1月3日まで収集を休みます。", text)

    def test_inner_container_chosen(self):
        reg = region_of("layout_table.html")                 # ページ全体を包む #main より内側の #contents
        self.assertEqual(reg.method, "cms:#contents")
        self.assertNotIn("開庁時間", reg.text())             # 御蔵島の形
        self.assertIn("第1火曜日", reg.text())

    def test_wrapper_not_removed_by_name(self):
        html = ("<html><body><div id='pageTop'><header>頭</header><main id='main-contents'><h1>ごみ収集日</h1>"
                "<p>年末年始は収集を休んだり、変更することがあります。</p></main><footer>足</footer></div></body></html>")
        reg = htmlbody.content_region(html)
        self.assertIn("年末年始は収集を休んだり", reg.text())    # div#pageTop (名前は page top) がページ全体なら外さない
        self.assertNotIn("足", reg.text())

    def test_text_not_removed_for_its_meaning(self):
        """「お問い合わせ」の見出しのあとの文も残す (文の意味で外す決まりは無い。読むのは LLM)"""
        html = ("<html><body><div id='contents'><h1>収集日</h1><p>可燃ごみは月曜日です。</p>"
                "<h2>お問い合わせ</h2><p>環境課 電話 000-0000</p><p>年末年始は収集日が変わることがあります。</p></div></body></html>")
        text = htmlbody.content_region(html).text()
        for t in ("可燃ごみは月曜日です。", "お問い合わせ", "年末年始は収集日が変わることがあります。"):
            self.assertIn(t, text)

    def test_boilerplate_removed(self):
        pages = {}
        for i in range(4):
            html = (f"<html><body><div id='contents'><h1>ページ{i}</h1><p>中身その{i}のちがう文がここにあります{i * 7}。</p>"
                    "<p>閉庁日：土曜日・日曜日・祝日、年末年始</p></div></body></html>")
            pages[f"p{i}"] = htmlbody.block_hashes(html)
        bp = htmlbody.boilerplate_hashes(pages)
        self.assertEqual(len(bp), 1)
        html = ("<html><body><div id='contents'><h1>収集日</h1><p>可燃ごみは月曜日です。</p>"
                "<p>閉庁日：土曜日・日曜日・祝日、年末年始</p></div></body></html>")
        reg = htmlbody.content_region(html, boilerplate=bp)
        self.assertNotIn("閉庁日", reg.text())
        self.assertEqual(reg.boilerplate_removed, 1)

    def test_related_pages_not_boilerplate(self):
        same = "<p>この文は同じ形の地区のページにだけ出ます。</p>"
        pages = {f"p{i}": htmlbody.block_hashes(f"<html><body>{same}<p>共通の本文その一です。</p><p>地区{i}</p></body></html>")
                 for i in range(3)}
        self.assertEqual(htmlbody.boilerplate_hashes(pages), set())   # 関係のあるページ (中身がほとんど同じ) どうしは数えない

    def test_district_pages_related_by_region(self):
        """地区のページがホストの半分以上でも、地区に共通の文 (第N の決まり・祝日の扱い) を使い回しにしない。
        関係の無いページ 3 つ以上に出る感想のお願い・新着情報のリンクは使い回し (三鷹の形)"""
        frame = "<header><p>テスト市ホームページのヘッダです</p></header><footer><p>Copyright テスト市 All Rights Reserved</p></footer>"
        survey = "<p>より良いウェブサイトにするために、ページのご感想をお聞かせください。</p>"
        news = "<ul>" + "".join(f"<li><a href='/n{j}.html'>新着情報のお知らせその{j}です</a></li>" for j in range(4)) + "</ul>"
        common = ("<p>第1とは1～7日、第2とは8～14日になります。</p><p>古紙・古着は第1・3水曜日に収集します。</p>"
                  "<p>祝日も収集します。（年末年始を除く）</p><p>年末及び1月は収集日程、時間が異なることがあります。</p>")
        pages, regions, htmls = {}, {}, {}
        for i in range(6):
            body = "".join(f"<p>第{i}地区の町名その{j}丁目から{j + 1}丁目まで</p>" for j in range(3))
            htmls[f"d{i}"] = (f"<html><body>{frame}<div id='contents'><h1>令和8年度 第{i}地区</h1>{common}{body}"
                              f"{survey}{news}</div></body></html>")
        for i in range(3):
            body = "".join(f"<p>関係の無い話題{i}の本文その{j}です。</p>" for j in range(12))
            htmls[f"o{i}"] = f"<html><body>{frame}<div id='contents'><h1>話題{i}</h1>{body}{survey}{news}</div></body></html>"
        for k, h in htmls.items():
            pages[k], regions[k] = htmlbody.block_hashes(h), htmlbody.region_hashes(h)
        bp = htmlbody.boilerplate_hashes(pages, regions=regions)
        text = htmlbody.content_region(htmls["d0"], boilerplate=bp).text()
        for t in ("第1とは1～7日", "古紙・古着は第1・3水曜日", "祝日も収集します。（年末年始を除く）", "年末及び1月は収集日程"):
            self.assertIn(t, text)
        self.assertNotIn("ページのご感想をお聞かせください", text)
        self.assertNotIn("新着情報のお知らせ", text)


class TestCachedPages(unittest.TestCase):
    """キャッシュにある実際のページ (無ければ飛ばす)"""

    def page(self, url):
        b = _util.cached(url)
        if b is None:
            self.skipTest("キャッシュに無い")
        return decode.decode(b, "text/html").text

    def test_shiogama_footer(self):
        reg = htmlbody.content_region(self.page("https://www.city.shiogama.miyagi.jp/soshiki/18/37120.html"))
        self.assertNotIn("閉庁日", reg.text())
        self.assertIn("番地により別の地区があります", reg.text())

    def test_mikurajima_layout(self):
        reg = htmlbody.content_region(self.page("https://www.vill.mikurasima.tokyo.jp/section/jumin/gomi.html"))
        self.assertNotIn("開庁時間", reg.text())
        self.assertIn("第1火曜日", reg.text())

    def test_kawaguchi_notice_kept(self):
        reg = htmlbody.content_region(self.page("https://www.city.kawaguchi.lg.jp/kurashi_tetsuzuki/gomi_risaikuru/2/1/9626.html"))
        self.assertIn("通常の周期と異なることがございます", reg.text())

    def test_mitaka_district_policy_kept_with_boilerplate(self):
        """三鷹の地区のページ: 使い回しの塊 (data/readers/boilerplate/) を当てても、祝日・年末年始の文が残る"""
        import fetch_docs
        html = self.page("https://www.city.mitaka.lg.jp/c_service/095/095664.html")
        text = htmlbody.content_region(html, boilerplate=fetch_docs.load_boilerplate("www.city.mitaka.lg.jp")).text()
        self.assertIn("祝日も収集します。（年末年始を除く）", text)
        self.assertIn("年末及び1月は収集日程、時間が異なることがあります。", text)


if __name__ == "__main__":
    unittest.main()
