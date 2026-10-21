"""content.py: 本文の範囲・使い回しの部分・方針の文のふるい・deferred と caveat・但し書きの印 (ネットにつながない)

fixtures/ の HTML はテスト用に作ったもの (実際のページの形をまねた)。実際のページはキャッシュにあるときだけ確かめる。
"""
import unittest

import _util
import content, decode


def region_of(name):
    return content.content_region(_util.fixture(name))


class TestRegion(unittest.TestCase):
    def test_footer_office_hours_removed(self):
        reg = region_of("cms_footer.html")
        self.assertEqual(reg.method, "cms:#main_body")
        text = reg.text()
        self.assertNotIn("閉庁日", text)                    # ページの足の開庁時間 (塩竈の罠)
        self.assertNotIn("このページに関するお問い合わせ", text)
        self.assertNotIn("文字サイズ", text)
        self.assertIn("年末年始は12月29日から1月3日まで収集を休みます。", text)   # 本文の方針の文は残る

    def test_inner_container_chosen(self):
        reg = region_of("layout_table.html")                 # ページ全体を包む #main より内側の #contents
        self.assertEqual(reg.method, "cms:#contents")
        self.assertNotIn("開庁時間", reg.text())             # 御蔵島の罠
        self.assertIn("第1火曜日", reg.text())

    def test_strong_only_line_is_heading(self):
        reg = region_of("strong_heading.html")
        ss = {s.text: s for s in reg.sentences()}
        s = ss["年末 ： 12月29日（火曜日）まで"]
        self.assertEqual(s.heading_path[-1], "2026年度年末年始のごみ収集について")
        self.assertEqual(content.classify_policy(s.text), "other")   # 文だけでは「ごみ」の言葉が無い
        self.assertTrue(content.policy_eligible(s))                  # 見出しが収集を言っているので方針に使える
        self.assertNotIn("文字サイズ変更", reg.text())

    def test_wrapper_not_removed_by_name(self):
        html = ("<html><body><div id='pageTop'><header>頭</header><main id='main-contents'><h1>ごみ収集日</h1>"
                "<p>年末年始は収集を休んだり、変更することがあります。</p></main><footer>足</footer></div></body></html>")
        reg = content.content_region(html)
        self.assertIn("年末年始は収集を休んだり", reg.text())    # div#pageTop (名前は page top) がページ全体なら外さない
        self.assertNotIn("足", reg.text())

    def test_table_kept_and_table_block_fallback(self):
        html = ("<html><body><div class='x'><h3>収集日</h3><table><tr><td>月曜日</td></tr></table></div>"
                "<div class='footer'>閉庁日：祝日</div></body></html>")
        reg = content.content_region(html, schedule_tables=["月曜日"])
        self.assertEqual(reg.method, "table_block")
        self.assertNotIn("閉庁日", reg.text())

    def test_boilerplate_removed(self):
        pages = {}
        for i in range(4):
            html = (f"<html><body><div id='contents'><h1>ページ{i}</h1><p>中身その{i}のちがう文がここにあります{i * 7}。</p>"
                    "<p>閉庁日：土曜日・日曜日・祝日、年末年始</p></div></body></html>")
            pages[f"p{i}"] = content.block_hashes(html)
        bp = content.boilerplate_hashes(pages)
        self.assertEqual(len(bp), 1)
        html = ("<html><body><div id='contents'><h1>収集日</h1><p>可燃ごみは月曜日です。</p>"
                "<p>閉庁日：土曜日・日曜日・祝日、年末年始</p></div></body></html>")
        reg = content.content_region(html, boilerplate=bp)
        self.assertNotIn("閉庁日", reg.text())
        self.assertEqual(reg.boilerplate_removed, 1)

    def test_related_pages_not_boilerplate(self):
        same = "<p>この文は同じ形の地区のページにだけ出ます。</p>"
        pages = {f"p{i}": content.block_hashes(f"<html><body>{same}<p>共通の本文その一です。</p><p>地区{i}</p></body></html>")
                 for i in range(3)}
        self.assertEqual(content.boilerplate_hashes(pages), set())   # 関係のあるページ (中身がほとんど同じ) どうしは数えない

    def district_host(self):
        """ホストのページの半分以上が、同じ形の地区のページ (三鷹の形)。地区に共通の中身と方針の文、サイトの枠を持つ"""
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
            pages[k], regions[k] = content.block_hashes(h), content.region_hashes(h)
        return htmls, pages, regions

    def test_district_pages_related_by_region(self):
        """地区のページがホストの半分以上でも、地区に共通の中身 (第N の決まり・規則の行) を使い回しにしない。
        関係の無いページ 3 つ以上に出る感想のお願い・新着情報のリンクは使い回し (三鷹の誤り: 地区の方針の文が外れていた)"""
        htmls, pages, regions = self.district_host()
        bp = content.boilerplate_hashes(pages, regions=regions)
        reg = content.content_region(htmls["d0"], boilerplate=bp)
        text = reg.text()
        for t in ("第1とは1～7日", "古紙・古着は第1・3水曜日", "祝日も収集します。（年末年始を除く）", "年末及び1月は収集日程"):
            self.assertIn(t, text)
        self.assertNotIn("ページのご感想をお聞かせください", text)
        self.assertNotIn("新着情報のお知らせ", text)
        frame_only = content.boilerplate_hashes(pages)                  # 枠を除いた比べ方だけなら、地区の中身まで外れる
        self.assertNotIn("第1とは1～7日", content.content_region(htmls["d0"], boilerplate=frame_only).text())
        kinds = [c["kind"] for _, c in content.change_sentences(reg.sentences())]
        self.assertIn("deferred", kinds)

    def test_policy_lines_never_boilerplate(self):
        """方針・但し書きの文は block_hashes に入らず、古い数え上げに入っていても本文の範囲から外さない"""
        html = "<html><body><div id='contents'><p>可燃ごみは月曜日です。</p><p>祝日も収集します。（年末年始を除く）</p></div></body></html>"
        line = next(l for l in content.lines(content.parse(html).body) if l.text.startswith("祝日"))
        self.assertNotIn(line.hash, content.block_hashes(html))
        reg = content.content_region(html, boilerplate={line.hash})
        self.assertIn("祝日も収集します", reg.text())
        self.assertEqual(reg.boilerplate_removed, 0)
        self.assertTrue(content.policy_like("年末及び1月は収集日程、時間が異なることがあります。"))
        self.assertTrue(content.policy_like("5番目の水曜日は収集しません"))
        self.assertFalse(content.policy_like("より良いウェブサイトにするために、ページのご感想をお聞かせください。"))
        self.assertFalse(content.policy_like("電話受付時間:8時30分~17時15分（土曜日、日曜日、年末年始を除く）"))


class TestInquiry(unittest.TestCase):
    """お問い合わせの欄などを外す決まり (§3.2 の 2) が、欄の外の中身を消さないこと"""

    def test_top_link_keeps_later_section(self):
        """「ページの先頭へ」の 1 行のあとの節 (年末年始の後で知らせる文・5 番目の但し書き) を消さない"""
        html = ("<html><body><div id='contents'><h2>可燃ごみ</h2><p>毎週月曜日</p><p><a href='#top'>ページの先頭へ</a></p>"
                "<h2>年末年始</h2><p>年末年始の収集日は、12月の広報でお知らせします。</p><p>5番目の水曜日は収集しません</p></div></body></html>")
        reg = content.content_region(html)
        self.assertNotIn("ページの先頭へ", reg.text())
        self.assertIn("毎週月曜日", reg.text())
        self.assertEqual([c["kind"] for _, c in content.change_sentences(reg.sentences())], ["deferred"])
        self.assertIn("5番目の水曜日は収集しません", [q["text"] for q in content.scan_qualifiers(reg.sentences())])

    def test_heading_section_only(self):
        """川口の形: 本文の塊 (600 字以下) の途中の「お問い合わせ先」の見出しは、その見出しの節だけを外す (塊ごと外さない)"""
        html = ("<html><body><div id='contents'><div><p>排出品目・収集日は「家庭ごみ収集日カレンダー」をご覧ください。"
                "（年末年始の収集日につきましては、通常の周期と異なることがございます。)</p>"
                "<h3>お問い合わせ先</h3><div><p>収集業務課</p><p>電話受付時間:8時30分~17時15分（土曜日、日曜日、年末年始を除く）</p></div>"
                "</div></div></body></html>")
        reg = content.content_region(html)
        self.assertIn("通常の周期と異なることがございます", reg.text())
        self.assertNotIn("お問い合わせ先", reg.text())
        self.assertNotIn("電話受付時間", reg.text())
        self.assertIn("deferred", [c["kind"] for _, c in content.change_sentences(reg.sentences())])

    def test_section_stops_at_policy_sentence(self):
        html = ("<html><body><div id='contents'><h2>収集日</h2><p>毎週月曜日</p><h3>お問い合わせ先</h3><p>環境課</p>"
                "<p>※年末年始の収集日は広報でお知らせします。</p></div></body></html>")
        text = content.content_region(html).text()
        self.assertNotIn("環境課", text)
        self.assertIn("※年末年始の収集日は広報でお知らせします。", text)

    def test_label_box_and_mid_label(self):
        """塊の頭の札は塊の中だけ外す。塊の途中の札 (見出しでない) は札だけ外す"""
        html = ("<html><body><div id='contents'><h2>収集日</h2><p>毎週月曜日</p><div class='x'><p>このページに関するお問い合わせ</p>"
                "<p>環境課 電話 000-0000</p></div><p>5番目の水曜日は収集しません</p>"
                "<div><p>第2木曜日は資源</p><p>関連情報</p><p>第4木曜日はびん</p></div></div></body></html>")
        text = content.content_region(html).text()
        self.assertNotIn("環境課", text)
        self.assertNotIn("関連情報", text)
        for t in ("毎週月曜日", "5番目の水曜日は収集しません", "第2木曜日は資源", "第4木曜日はびん"):
            self.assertIn(t, text)

    def test_container_starting_with_label_not_removed(self):
        """本文の範囲そのものが札の字で始まる短いページでも、範囲ごと外さない (空の範囲にしない)"""
        for st in ((), ["月曜日"]):
            html = ("<html><body><div id='contents'><p>関連情報</p><p>年末年始の収集日は、12月の広報でお知らせします。</p>"
                    "<table><tr><td>月曜日</td></tr></table></div></body></html>")
            reg = content.content_region(html, schedule_tables=st)
            self.assertEqual(reg.method, "cms:#contents")
            self.assertIn("年末年始の収集日は、12月の広報でお知らせします。", reg.text())
            self.assertIn("月曜日", reg.text())

    def test_content_sha256_ignores_updated_line(self):
        a = "<html><body><div id='contents'><p>可燃ごみは月曜日。</p><p>更新日：2026年4月1日</p></div></body></html>"
        b = a.replace("2026年4月1日", "2026年10月1日")
        c = a.replace("月曜日", "火曜日")
        self.assertEqual(content.content_sha256(a), content.content_sha256(b))
        self.assertNotEqual(content.content_sha256(a), content.content_sha256(c))


class TestSentences(unittest.TestCase):
    def test_split_and_parenthetical(self):
        html = "<html><body><div id='contents'><p>毎月29日・30・31日の不燃ごみの収集はありません。（年末年始は変更あり）※注意 祝日も収集します。</p></div></body></html>"
        ss = [s.text for s in content.content_region(html).sentences()]
        self.assertIn("毎月29日・30・31日の不燃ごみの収集はありません。", ss)
        self.assertIn("（年末年始は変更あり）", ss)
        self.assertIn("※注意 祝日も収集します。", ss)
        self.assertEqual(ss.count("（年末年始は変更あり）"), 1)

    def test_bracket_heading(self):
        html = "<html><body><div id='contents'><p>【可燃物（燃えるごみ）】</p><p>毎週 月曜日・木曜日</p></div></body></html>"
        ss = content.content_region(html).sentences()
        rule = next(s for s in ss if s.text.startswith("毎週"))
        self.assertEqual(rule.heading_path, ["【可燃物（燃えるごみ）】"])


class TestPolicyFilter(unittest.TestCase):
    def test_classify_policy(self):
        self.assertEqual(content.classify_policy("閉庁日：土曜日・日曜日・祝日、年末年始（12月29日から1月3日）"), "office")
        self.assertEqual(content.classify_policy("開庁時間 月曜日～金曜日 ※祝休日、12月29日～1月3日を除く"), "office")
        self.assertEqual(content.classify_policy("施設への持込みはできません"), "dropoff")
        self.assertEqual(content.classify_policy("クリーンセンターへのごみの搬入はできません"), "dropoff")
        self.assertEqual(content.classify_policy("※燃えるごみ・資源ごみの収集は、祝日、振替休日も実施します。"), "policy")
        self.assertEqual(content.classify_policy("年末 ： 12月29日（火曜日）まで"), "other")

    def test_section_ok(self):
        reg = region_of("strong_heading.html")
        s = next(x for x in reg.sentences() if x.text.startswith("年末"))
        self.assertTrue(content.section_ok(s))
        self.assertTrue(content.section_ok(s, [s.heading_path]))


class TestChange(unittest.TestCase):
    """§2.6 の見分け方 (期間の名前と印の両方 → deferred、印だけ → caveat)"""

    def k(self, text):
        c = content.classify_change(text)
        return c and c["kind"]

    def test_deferred(self):
        for t in ("年末年始は収集を休んだり、変更することがあります。",                    # 渋谷
                  "（年末年始は変更あり）",                                              # 渋谷 (不燃ごみの節)
                  "注釈：年末年始の収集日は変更する場合があります。",                     # 大田
                  "※　令和８年年末年始のごみ・資源収集に関する内容は、12月のあだち広報でお知らせします",   # 足立
                  "※年末年始の収集日は反映されていませんので、「年末・年始ごみ収集のお知らせ」をご覧ください",  # 川口
                  "（注）年末年始の収集日は、改めて板橋区ホームページ、区報、集積所看板への張り紙でお知らせいたします。"):
            self.assertEqual(self.k(t), "deferred", t)

    def test_kawakita_periods(self):
        c = content.classify_change("※収集日は、祝日・お盆休み・年末年始等により、一部変更になることがあります。")
        self.assertEqual(c["kind"], "deferred")
        self.assertEqual(c["periods"], ["年末年始", "お盆"])                     # 祝日は期間の名前に入れない
        self.assertTrue(c["mentions_holiday"])

    def test_adachi_dates(self):
        c = content.classify_change("年末(12/24～12/31)…年始(1/1～1/10)については、12月の広報やホームページ等をご覧ください")
        self.assertEqual(c["kind"], "deferred")
        self.assertEqual((min(c["dates"]), max(c["dates"])), ((1, 1), (12, 31)))
        self.assertIn((12, 24), c["dates"])
        self.assertIn((1, 10), c["dates"])

    def test_caveat(self):
        for t in ("※ごみの収集日は変更する場合があります。",                             # 村田
                  "※収集日が変更となる場合がありますので、町の広報紙をご確認ください。",   # 七ヶ宿
                  "祝日・振替休日などで、日程が変更になることがありますのでご確認ください。"):  # 能美
            self.assertEqual(self.k(t), "caveat", t)
        self.assertTrue(content.classify_change("祝日・振替休日などで、日程が変更になることがありますのでご確認ください。")["mentions_holiday"])

    def test_not_change(self):
        for t in ("年末：12月29日（火曜日）まで 年始：1月4日（月曜日）より",               # 金沢 (year_end の材料)
                  "（※）年末年始の影響で、収集日を変更しています。ご注意ください。",      # 金沢のカレンダーの注
                  "12月31日のごみ収集はありません",                                      # 国立
                  "年末年始のごみ収集の変更についてお知らせします。",                     # 「お知らせ」「変更」だけでは印にしない
                  "区報、町会回覧、ホームページなどでお知らせします。",                  # 前の文の続き
                  "大型連休中もごみ収集は通常通り実施します。",
                  "FirefoxなどのブラウザでPDFファイルを開くと、正しく表示されない場合があります。",
                  "大型の不要品も自宅にて引き取ってもらえることがあります。"):
            self.assertIsNone(content.classify_change(t), t)

    def test_festival_and_saijitsu(self):
        self.assertEqual(content.period_names("港まつりの日は収集日が変わることがあります"), ["港まつり"])
        self.assertEqual(content.period_names("※年末年始や祭典開催時は収集日が変更になることがあります"), ["年末年始", "祭典"])
        self.assertEqual(content.period_names("さくらまつり期間中は"), ["さくらまつり"])
        self.assertEqual(content.period_names("祝日、祭日、お盆休み、年末年始等で"), ["年末年始", "お盆"])
        self.assertEqual(content.period_names("ＧＷ期間中の収集日は変更になる場合があります"), ["ゴールデンウィーク"])
        self.assertEqual(self.k("ＧＷ期間中の収集日は変更になる場合があります。"), "deferred")

    def test_change_sentences_skip_office(self):
        reg = region_of("cms_footer.html")
        self.assertEqual(content.change_sentences(reg.sentences()), [])     # 足の開庁時間は本文の範囲で外れる

    def test_change_sentences_keep_office_and_dropoff_words(self):
        """「窓口」「持ち込み」を含んでも、期間の名前と印があれば deferred (§2.6 に除く決まりは無い。7 は止める方)"""
        for t in ("年末年始のごみ収集日程は、決まり次第ホームページや窓口でお知らせします。",
                  "年末年始のごみの収集と持ち込みについては、12月の広報でお知らせします。"):
            html = f"<html><body><div id='contents'><h1>ごみ収集</h1><p>{t}</p></div></body></html>"
            cs = content.change_sentences(content.content_region(html).sentences())
            self.assertEqual([(s.text, c["kind"]) for s, c in cs], [(t, "deferred")])
            self.assertIn(cs[0][1]["policy_class"], ("office", "dropoff"))

    def test_polite_markers(self):
        self.assertEqual(self.k("（年末年始の収集日につきましては、通常の周期と異なることがございます。)"), "deferred")   # 川口
        self.assertEqual(self.k("収集日は変更となる場合がございます。"), "caveat")


class TestQualifiers(unittest.TestCase):
    def test_negative_and_period_marks(self):
        for t in ("12月31日のごみ収集はありません",                        # 国立
                  "注意：12月31日（木）は収集しません",                     # 美里
                  "５月４日(月)、12月31日(木)は、施設の定期点検のため可燃ごみの収集は行いません",   # 日高
                  "12月29日から1月3日の収集はありません",                   # 鶴ヶ島
                  "月の「5番目の曜日」に収集はありません",                  # 新宿
                  "毎週 月曜日（1回目の月曜日を除く）",                     # 石狩
                  "第5週がある月の水曜日、土曜日の収集は休み"):              # 赤平
            self.assertTrue(content.qualifier_marks(t), t)
        self.assertEqual(content.qualifier_marks("毎週 月曜日・木曜日"), [])

    def test_named_months(self):
        # 毛呂山「…5・6・7・8・9・10月のみ第1水曜日でも出せる」: 足す収集の日の閉じた見本 (MONTH_TEMPLATES の additional) が読み、
        # その月だけ止める (足す日は読まないので 5〜10 月は不明。「のみ」は足す日の月を限るだけで、ほかの月は元の規則のまま)
        self.assertEqual(content.named_months("※ペットボトルは5・6・7・8・9・10月のみ第1水曜日でも出せる"), {5, 6, 7, 8, 9, 10})
        self.assertEqual(content.named_months("5月〜9月は紙・衣類とペットボトルの収集が隔週"), {5, 6, 7, 8, 9})
        # 止める方へ直した (古い期待): 「6・9・12・3月の第4木曜日」は述語が無く、その月だけの規則なのか、その月にも足すのか決まらない。
        # 前の期待 {6, 9, 12, 3} は、その 4 か月だけ止めてほかの月を答える (閉じた見本の外 → 空 = 系列ごと止める)
        self.assertEqual(content.named_months("6・9・12・3月の第4木曜日"), set())
        self.assertEqual(content.named_months("12月31日（木）は収集しません"), set())
        # 止める方へ直した (古い期待): 述語の無い月の範囲「11月〜2月」は、その月に何があるか言っていない。前の期待 {11, 12, 1, 2} は
        # ほかの月を答える → 空 (系列ごと止める)
        self.assertEqual(content.named_months("11月〜2月"), set())

    def test_named_months_period_words(self):
        """期間の名前の月も数える (三鷹「※年末及び1月は…」は 12 月と 1 月)。月の決まらない名前・季節は空 (系列ごと止める)"""
        self.assertEqual(content.named_months("※年末及び1月は収集日程、時間が異なります。"), {12, 1})
        self.assertEqual(content.named_months("年末年始は収集日が変わります"), {12, 1})
        self.assertEqual(content.named_months("お盆の期間は休みます"), {7, 8})
        self.assertEqual(content.named_months("大型連休と5月は収集日が変わります"), set())
        self.assertEqual(content.named_months("港まつりの日と6月は休み"), set())
        self.assertEqual(content.named_months("夏は隔週、6月は毎週"), set())
        self.assertEqual(content.named_months("夏（7月～9月）は隔週"), {7, 8, 9})

    def test_scan(self):
        reg = content.content_region("<html><body><div id='contents'><p>毎週 月曜日・木曜日</p>"
                                     "<p>※ペットボトルは5・6・7・8・9・10月のみ第1水曜日でも出せる</p>"
                                     "<p>※6・9・12・3月の第4木曜日</p></div></body></html>")
        qs = content.scan_qualifiers(reg.sentences())
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[0]["months"], [5, 6, 7, 8, 9, 10])       # 足す収集の日の見本 (additional): その月だけ止める
        # 止める方へ直した (古い期待は月の名指し [3, 6, 9, 12]): 述語の無い月の並びは閉じた見本の外 → 月なし (系列ごと止める)
        self.assertEqual(qs[1]["months"], [])


class TestCachedPages(unittest.TestCase):
    """キャッシュにある実際のページ (無ければ飛ばす)"""

    def page(self, url):
        b = _util.cached(url)
        if b is None:
            self.skipTest("キャッシュに無い")
        return decode.decode(b, "text/html").text

    def test_shiogama_footer(self):
        html = self.page("https://www.city.shiogama.miyagi.jp/soshiki/18/37120.html")
        reg = content.content_region(html)
        self.assertNotIn("閉庁日", reg.text())
        self.assertIn("番地により別の地区があります", reg.text())

    def test_mikurajima_layout(self):
        reg = content.content_region(self.page("https://www.vill.mikurasima.tokyo.jp/section/jumin/gomi.html"))
        self.assertNotIn("開庁時間", reg.text())
        self.assertIn("第1火曜日", reg.text())

    def test_shibuya_deferred(self):
        reg = content.content_region(self.page("https://www.city.shibuya.tokyo.jp/kurashi/gomi/kateigomi/gomid.html"))
        kinds = {s.text: c["kind"] for s, c in content.change_sentences(reg.sentences())}
        self.assertEqual(kinds.get("年末年始は収集を休んだり、変更することがあります。"), "deferred")
        self.assertEqual(kinds.get("（年末年始は変更あり）"), "deferred")

    def test_kawaguchi_inquiry_heading_keeps_notice(self):
        """川口のステーション検索のページ: 「お問い合わせ先」の見出しの前の年末年始の文を消さない"""
        reg = content.content_region(self.page("https://www.city.kawaguchi.lg.jp/kurashi_tetsuzuki/gomi_risaikuru/2/1/9626.html"))
        self.assertIn("通常の周期と異なることがございます", reg.text())
        self.assertNotIn("収集業務課業務係", reg.text())

    def test_mitaka_district_policy_kept_with_boilerplate(self):
        """三鷹の地区のページ: 段 0 の使い回しの部分を当てても、方針の文と deferred の文が残る"""
        import fetch_docs
        html = self.page("https://www.city.mitaka.lg.jp/c_service/095/095664.html")
        reg = content.content_region(html, boilerplate=fetch_docs.load_boilerplate("www.city.mitaka.lg.jp"))
        self.assertIn("祝日も収集します。（年末年始を除く）", reg.text())
        kinds = {s.text: c["kind"] for s, c in content.change_sentences(reg.sentences())}
        self.assertEqual(kinds.get("年末及び1月は収集日程、時間が異なることがあります。"), "deferred")

    def test_kanazawa_putout_holiday(self):
        reg = content.content_region(self.page("https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/3/7608.html"))
        s = next(x for x in reg.sentences() if x.text == "収集は年末年始を除く月曜日～金曜日（祝日含む）に行います。")
        self.assertTrue(content.policy_eligible(s))


if __name__ == "__main__":
    unittest.main()
