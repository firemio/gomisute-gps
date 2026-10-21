"""M1 の読み取りの危ない方向の不具合の直し: build.py (型 D の印字の系列も方針の B を見る・A の但し書きも待ち行列へ)・
version.py (暦年の印で短い期間を ok にしない)・compare.py (1 字のローマ字のコース・地区の名前を消さない)。
ネットにつながない。LLM は呼ばない (B の答えは合成。build_one を通すものはキャッシュだけ)"""
import argparse, copy, json, re, types, unittest

import _util
import build, categories, compare, content, expand, fetch_docs, members, pdf_layout, rules, stage2, version

LG = "999999"
CELLS = [["地区名", "本町", "", "", "", "", "", ""],
         ["分別", "4月", "5月", "6月", "7月", "8月", "9月", "10月"],
         ["古紙", "8・22", "13", "10", "15", "19", "16", "14・28"],
         ["ビン・カン", "24", "12", "26", "10", "14", "11", "23"]]
POL_OK = {"holiday_policy": None, "year_end": [], "deferred": [], "caveats": [], "closures": [], "qualifiers": [],
          "ignored_office_hours": [], "valid": {"from": "2026-04-01", "to": "2027-03-31"}}
FY26 = {"period_id": "p1", "valid_from": "2026-04-01", "valid_to": "2027-03-31", "basis": {"text": "令和8年度", "kind": "stated"},
        "year_evidence": {"token": {"text": "令和8年度", "where": "in_file"}, "time": [{"kind": "last_modified", "value": "2026-02-24"}],
                          "conflict": None, "ok": True}}
T = [{"kind": "last_modified", "value": "2026-03-01"}]


def make_pdf(cells, title="令和8年度 ごみ収集日程表"):
    """罫線のある表の PDF (test_stage3 と同じ作り方)"""
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


class FakeLLM:
    """B の読みの合成 (品目・マス・地区は A と同じに読む。方針は policy(入力の文字) の答え。None は読めなかった)"""

    def __init__(self, policy):
        self.policy = policy

    def prefetch(self, jobs, workers=8):
        pass

    def ask(self, name, text, images=()):
        if name == "category":
            labels = re.findall(r"^- (.+)$", text, re.M)
            return {"categories": [{"label": l, "coarse": categories.category(l)["coarse"], "mode": categories.category(l)["mode"]}
                                   for l in labels]}, {}
        if name == "cells":
            return {"cells": {f"R{r + 1}C{c + 1}": v for r, row in enumerate(CELLS) for c, v in enumerate(row) if v}}, {}
        if name == "members":
            mem = members.parse_cell("本町", LG)
            return {"rows": [{"id": 1, "members": [{"name_verbatim": "本町", "unit": x["unit"], "conditions": x["conditions"]}
                                                   for x in mem]}]}, {}
        if name == "policy":
            return self.policy(text), {}
        raise AssertionError(name)


def read_d(policy, llm=True, two_chunks=False):
    """合成の型 D の資料を read_pdf_source で読む → (Muni, 系列のリスト (expand_series のあと))"""
    body = make_pdf(CELLS)
    doc = pdf_layout.open_pdf(body)
    an = {"pages": [pdf_layout.analyze_page(p) for p in doc], "doc": doc, "body": body}
    an["prep"] = build.pdf_prep(an)
    if two_chunks:                                   # 方針の入力が 2 つに分かれたページ (2 つ目は「(続き)」)
        k = ("policy", 0)
        an["prep"]["prep"][k] = an["prep"]["prep"][k] + [an["prep"]["prep"][k][0] + "\n(続き)"]
    m = stage2.Muni(LG, "テスト町")
    a = argparse.Namespace(llm=llm, offline=False, write=False)
    x = {"url": "https://example.test/R8_calendar.pdf", "anchors": ["令和8年度 ごみ収集日程表"], "sha256": "x" * 64,
         "last_modified": "Tue, 24 Feb 2026 00:00:00 GMT", "fetched_at": "2026-10-19T03:00:00+09:00"}
    build.read_pdf_source(m, x, an, a, FakeLLM(policy), [], {"sources": {}}, [])
    return m, [expand.expand_series(s) for s in m.schedules]


class TestPrintedSeriesPolicyB(unittest.TestCase):
    """型 D の印字の系列: B の方針の読み (期間・deferred・但し書き) が無い・片方だけ・食い違うなら答えない"""

    def test_control_agreed_answers(self):
        m, out = read_d(lambda t: POL_OK)
        self.assertTrue(out)
        for s in out:                                               # 合成の資料がそもそも答えられること (下の確かめの前提)
            self.assertEqual(s["dates_origin"], "printed")
            self.assertTrue(s["answerable"], s["blocked_by"])
            self.assertEqual(s["extra"]["states"]["policy"], "agreed")
            self.assertEqual(s["extra"]["policies_reader"], "code_llm")

    def test_policy_unread_blocks(self):
        m, out = read_d(lambda t: None)                             # B の方針の答えが 1 つも無い
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn("single_reading", s["blocked_by"])
            self.assertEqual(s["extra"]["policies_reader"], "code_only")

    def test_policy_partly_unread_blocks(self):
        m, out = read_d(lambda t: None if "(続き)" in t else POL_OK, two_chunks=True)   # ページの一部だけ B が読めていない
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn("single_reading", s["blocked_by"])
            self.assertEqual(s["extra"]["policies_reader"], "code_llm_partial")

    def test_valid_single_blocks(self):
        m, out = read_d(lambda t: dict(POL_OK, valid=None))         # 期間を A だけが読んだ
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn("single_reading", s["blocked_by"])

    def test_valid_disputed_blocks(self):
        m, out = read_d(lambda t: dict(POL_OK, valid={"from": "2025-04-01", "to": "2026-03-31"}))
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn(f"disputed:{LG}:p1:version:s1", s["blocked_by"])
        self.assertIn(f"{LG}:p1:version:s1", {i["item_id"] for i in m.queue})

    def test_no_llm_blocks(self):
        m, out = read_d(lambda t: POL_OK, llm=False)
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn("single_reading", s["blocked_by"])

    def test_b_only_qualifier_blocks_and_queued(self):
        q = {"text": "※収集日は変更になることがあります", "span": "※収集日は変更になることがあります"}
        m, out = read_d(lambda t: dict(POL_OK, qualifiers=[q]))
        qid = "q" + content.sha256_text(content.squash(q["span"]))[:10]
        for s in out:
            self.assertFalse(s["answerable"])
            self.assertIn(f"unconsumed_qualifier:{qid}", s["blocked_by"])
        self.assertIn(qid, {i["item_id"].rsplit(":", 1)[-1] for i in m.queue if i["kind"] == "qualifier"})


class TestHtmlPolicyPartial(unittest.TestCase):
    """HTML の方針の B: 本文の一部を B が読めていなければ single_reading を外さない"""

    def run_b(self, policy):
        paras = "".join(f"<p>{i}番目の段落です。燃やすごみは月曜日と木曜日に出してください。" + "あ" * 40 + "</p>" for i in range(300))
        html = f"<html><head><title>収集日</title></head><body><main><h1>令和8年度 収集日程</h1>{paras}<p>末尾の段落</p></main></body></html>"
        lines = content.content_region(html).lines()
        m = stage2.Muni(LG, "テスト町")
        m.sources.append({"source_id": "s1", "url": "https://example.test/a.html", "sha256": "y" * 64, "extra": {}})
        m.schedules.append({"district_id": "d1", "category_id": "c1", "period": dict(FY26), "origin": "listed",
                            "printed_dates": ["2026-04-06"], "exceptions": [], "qualifiers": [], "markup_unhandled": [], "disputes": [],
                            "provenance": {"readers": [], "status": "agreed"}, "ref": {"source_id": "s1"}, "extra": {},
                            "extra_blocked": ["single_reading"]})
        a = argparse.Namespace(llm=True, offline=False, write=False)
        self.assertGreater(len(build.llm_tasks.policy_text(lines)), 1)
        build.html_policy_b(m, {"url": "https://example.test/a.html", "title": "収集日"}, "s1", FakeLLM(policy), a,
                            {"sources": {}}, lines=lines)
        return m.schedules[0]

    def test_full_reading_clears(self):
        self.assertNotIn("single_reading", self.run_b(lambda t: POL_OK)["extra_blocked"])

    def test_partial_reading_keeps_block(self):
        s = self.run_b(lambda t: None if "末尾の段落" in t else POL_OK)
        self.assertIn("single_reading", s["extra_blocked"])


class TestQualifierQueue(unittest.TestCase):
    """系列を止める使われなかった但し書きのどれにも、待ち行列の qualifier がある"""

    def muni(self):
        m = stage2.Muni(LG, "テスト町")
        m.sources.append({"source_id": "s1", "sha256": "z" * 64, "extra": {"qualifiers_unconsumed": [
            {"qualifier_id": "qaaaaaaaaaa", "text": "※5〜10月のみ", "months": [5, 6, 7, 8, 9, 10]},
            {"qualifier_id": "qbbbbbbbbbb", "text": "※臨時に変更することがあります", "months": []}]}})
        spec = {"district_id": "d1", "category_id": "c1", "period": dict(FY26), "origin": "listed", "printed_dates": ["2026-04-06"],
                "exceptions": [], "qualifiers": [{"qualifier_id": "qbbbbbbbbbb", "months": []}], "markup_unhandled": [], "disputes": [],
                "provenance": {"readers": [], "status": "agreed"}, "ref": {"source_id": "s1"}, "extra": {}, "extra_blocked": []}
        m.schedules.append(spec)
        return m

    def test_source_qualifiers_enqueued_once(self):
        m = self.muni()
        build.enqueue_source_qualifiers(m, "s1")
        build.enqueue_source_qualifiers(m, "s1")                    # 同じ qualifier_id は 1 つ
        items = {i["item_id"]: i for i in m.queue if i["kind"] == "qualifier"}
        self.assertEqual(set(items), {f"{LG}:p1:qualifier:s1:qaaaaaaaaaa", f"{LG}:p1:qualifier:s1:qbbbbbbbbbb"})
        b = items[f"{LG}:p1:qualifier:s1:qbbbbbbbbbb"]
        self.assertEqual(b["series"], [["d1", "c1", "p1"]])
        self.assertEqual(b["text"], "※臨時に変更することがあります")
        self.assertEqual(b["source_sha256"], "z" * 64)

    def test_every_blocking_id_has_item(self):
        m = self.muni()
        parsed = rules.parse("毎週月曜日 ※第5週は休み")
        m.schedules.append({"district_id": "d2", "category_id": "c1", "period": dict(FY26), "origin": "expanded",
                            "rule": {"text": parsed.text, "parsed": parsed}, "exceptions": [], "rest_consumed": False,
                            "qualifiers": [], "markup_unhandled": [], "disputes": [], "provenance": {"readers": [], "status": "agreed"},
                            "ref": {"source_id": "s1"}, "extra": {}, "extra_blocked": []})
        doc = {"schedules": [expand.expand_series(s) for s in m.schedules]}
        build.ensure_qualifier_items(m, doc, {"sources": {}})
        have = {i["item_id"].rsplit(":", 1)[-1]: i for i in m.queue if i["kind"] == "qualifier"}
        need = {b.split(":", 1)[1] for s in doc["schedules"] for b in s["blocked_by"] if b.startswith("unconsumed_qualifier:")}
        self.assertTrue(need)
        self.assertLessEqual(need, set(have))
        if parsed.rest:                                             # 規則のマスの読み残しは、文を規則の rest から引く
            self.assertEqual(have[expand.rest_qualifier_id(parsed.rest)]["text"], parsed.rest[:200])

    @unittest.skipUnless((build.DATA / "132047" / "docs.json").exists(), "調布の段 0 の一覧が無い")
    def test_build_offline_chofu(self):
        """調布をキャッシュだけで通す: HTML・CSV の A の但し書きで止まる系列にも待ち行列の項目がある (直す前は 3 つ無かった)"""
        build.build_one("132047", argparse.Namespace(llm=True, offline=True, write=False))
        doc, q = build.LAST["doc"], build.LAST["queue"]
        have = {i["item_id"].rsplit(":", 1)[-1] for i in q if i["kind"] == "qualifier"}
        need = {b.split(":", 1)[1] for s in doc["schedules"] for b in s["blocked_by"] if b.startswith("unconsumed_qualifier:")}
        need |= {r["note"] for s in doc["schedules"] for r in s.get("unknown_ranges") or [] if r.get("reason") == "unconsumed_qualifier"}
        self.assertEqual(need - have, set())


class TestYearEvidenceSubYear(unittest.TestCase):
    """暦年の印 (「2026年」) だけでは、暦年の中の短い期間・年度を ok にしない。本文の別の年度の印はぶつかり"""

    def test_fy2025_calendar_not_ok_for_half_year(self):
        body = version.year_tokens("令和7年度 ごみ収集カレンダー 2026年1月 2026年2月")
        ev = version.year_evidence("2026-04-01", "2026-09-30", [], body, [], [], [{"kind": "pdf_modified", "value": "2025-03-01"}])
        self.assertFalse(ev["ok"], ev)
        self.assertIsNone(ev["token"])
        self.assertIn("weak_year_token", ev["reasons"])
        only_year = version.year_evidence("2026-04-01", "2026-09-30", [], version.year_tokens("ごみ収集カレンダー 2026年1月"), [], [],
                                          [{"kind": "pdf_modified", "value": "2025-03-01"}])
        self.assertFalse(only_year["ok"], only_year)

    def test_token_matches_only_whole_calendar_year(self):
        y = version.year_tokens("2026年")[0]
        self.assertFalse(version.token_matches(y, "2026-04-01", "2026-09-30"))
        self.assertTrue(version.token_weak(y, "2026-04-01", "2026-09-30"))
        self.assertFalse(version.token_matches(y, "2026-04-01", "2027-03-31"))
        self.assertTrue(version.token_matches(y, "2026-01-01", "2026-12-31"))
        self.assertFalse(version.token_weak(y, "2026-01-01", "2026-12-31"))
        self.assertFalse(version.token_weak(y, "2027-04-01", "2028-03-31"))

    def test_calendar_year_with_other_fiscal_year_conflicts(self):
        ev = version.year_evidence("2026-01-01", "2026-12-31", version.year_tokens("2026年 ごみカレンダー"),
                                   version.year_tokens("2026年 ごみカレンダー 令和7年度の分け方"), [], [], T)
        self.assertFalse(ev["ok"], ev)
        self.assertEqual(ev["conflict"]["kind"], "token")
        ok = version.year_evidence("2026-01-01", "2026-12-31", version.year_tokens("2026年 ごみカレンダー"), [], [], [], T)
        self.assertTrue(ok["ok"], ok)                               # 川口の暦年のカレンダーは今までどおり

    def test_sub_year_span_still_ok(self):
        ev = version.year_evidence("2026-04-01", "2026-09-30", version.year_tokens("2026年4月～2026年9月 ごみ収集日程"), [], [], [], T)
        self.assertTrue(ev["ok"], ev)
        fy = version.year_evidence("2026-04-01", "2027-03-31", version.year_tokens("令和8年度 ごみ収集日程"),
                                   version.year_tokens("令和8年度 2026年4月"), [], [], T)
        self.assertTrue(fy["ok"], fy)                               # 年度の印があれば本文の暦年の印は邪魔しない


class TestLabelSame(unittest.TestCase):
    def test_single_letters_kept(self):
        self.assertFalse(compare.label_same("Aコース", "Bコース"))
        self.assertFalse(compare.label_same("A地区", "B地区"))
        self.assertFalse(compare.label_same("コース 1 Course 1", "コース 2 Course 2"))
        self.assertFalse(compare.label_same("コース\nA\nCourse A", "コース\nB\nCourse B"))
        self.assertTrue(compare.label_same("Ａコース", "Aコース"))

    def test_english_translation_dropped(self):
        self.assertTrue(compare.label_same("火曜日/金曜日\nEvery week\nTuesday & Friday", "火曜日/金曜日 Every week Tuesday & Friday"))
        self.assertTrue(compare.label_same("燃やせるごみ\nCombustible\ngarbage", "燃やせるごみ Combustible garbage"))
        self.assertTrue(compare.label_same("可燃ごみ\nBurnable", "可燃ごみ(Burnable)"))
        self.assertTrue(compare.label_same("第2・4水曜日\n2nd & 4th Wed", "第2・4水曜日 2nd & 4th Wed"))
        self.assertTrue(compare.label_same("Aコース\nCourse A", "Aコース Course A"))
        self.assertTrue(compare.selftest())

    def test_capital_codes_kept(self):                             # 2 字以上の大文字の名前・英語の語のあとの名前を消さない (p10)
        for a, b in [("ABコース", "CDコース"), ("AAコース", "ABコース"), ("NE地区", "SW地区"), ("Zone A 東地区", "Zone B 東地区"),
                     ("東地区 East A", "東地区 East B"), ("コース Course A", "コース Course B"), ("コース\nCourse A", "コース\nCourse B"),
                     ("A-1地区", "A-2地区"), ("AB1コース", "AB2コース"), ("No.1地区", "No.2地区"), ("地区 No.1", "地区 No.2"),
                     ("月曜 A", "月曜 B"), ("Aコース(月・木)", "Bコース(月・木)"), ("北A", "北B"), ("aコース", "bコース"),
                     ("コース\nCourse 1", "コース\nCourse 2"), ("Aコース", "Bコース")]:
            with self.subTest(a=a, b=b):
                self.assertFalse(compare.label_same(a, b))
        self.assertTrue(compare.label_same("ABコース", "ＡＢコース"))
        self.assertTrue(compare.label_same("Zone A 東地区", "Zone A\n東地区"))
        self.assertTrue(compare.label_same("可燃ごみ Every Monday", "可燃ごみ\nEvery Monday"))
        self.assertTrue(compare.label_same("第1・3水曜日\n1st & 3rd Wednesday", "第1・3水曜日"))      # 英語だけの行は消える
        self.assertTrue(compare.label_same("年末年始\nNew Year's holidays", "年末年始"))


# ---- 型 D の規則のマスの文 (cell_notes に資料の品目を渡す) ----
CELLS_RULE = [["地区名", "本町", "", "", "", "", "", ""],
              ["分別", "4月", "5月", "6月", "7月", "8月", "9月", "10月"],
              ["燃やすごみ", "", "", "", "", "", "", ""],
              ["燃やさないごみ", "8・22", "13", "10", "15", "19", "16", "14・28"],
              ["ビン・カン", "24", "12", "26", "10", "14", "11", "23"]]


def make_pdf_merged(cells, merged, title="令和8年度 ごみ収集日程表"):
    """罫線のある表の PDF。merged {行: 文字} はその行の 2 列目から右端までを 1 つのマス (月をまたぐ規則のマス) にする"""
    import fitz
    doc = fitz.open()
    p = doc.new_page(width=640, height=360)
    nr, nc = len(cells), len(cells[0])
    x0, y0, w, h = 20, 40, 70, 30
    for r in range(nr + 1):
        p.draw_line((x0, y0 + r * h), (x0 + nc * w, y0 + r * h), color=(0, 0, 0), width=1)
    for r in range(nr):
        for c in range(nc + 1):
            if r in merged and 1 < c < nc:
                continue
            p.draw_line((x0 + c * w, y0 + r * h), (x0 + c * w, y0 + (r + 1) * h), color=(0, 0, 0), width=1)
    for r in range(nr):
        for c in range(nc):
            t = merged[r] if (r in merged and c == 1) else ("" if r in merged and c > 1 else cells[r][c])
            if t:
                p.insert_text((x0 + c * w + 4, y0 + r * h + 20), t, fontname="japan", fontsize=10)
    p.insert_text((20, 25), title, fontname="japan", fontsize=14)
    return doc.tobytes()


class FakeLLMRules(FakeLLM):
    def ask(self, name, text, images=()):
        if name == "rules":
            return {"rules": []}, {}
        return super().ask(name, text, images)


class TestRuleCellNotesCats(unittest.TestCase):
    """型 D の規則のマスの読み残し: 資料の品目を cell_notes に渡す。そのマスの品目の祝日の文は当て、ほかの品目・資料に無い品目の文は
    読み残し (その系列を止める但し書き) にする"""

    def read(self, note):
        body = make_pdf_merged(CELLS_RULE, {2: "毎週月曜日" + note})
        doc = pdf_layout.open_pdf(body)
        an = {"pages": [pdf_layout.analyze_page(p) for p in doc], "doc": doc, "body": body}
        an["prep"] = build.pdf_prep(an)
        m = stage2.Muni(LG, "テスト町")
        x = {"url": "https://example.test/R8_calendar.pdf", "anchors": ["令和8年度 ごみ収集日程表"], "sha256": "x" * 64,
             "last_modified": "Tue, 24 Feb 2026 00:00:00 GMT", "fetched_at": "2026-10-19T03:00:00+09:00"}
        build.read_pdf_source(m, x, an, argparse.Namespace(llm=True, offline=False, write=False), FakeLLMRules(lambda t: POL_OK),
                              [], {"sources": {}}, [])
        burn = next(c for c, v in m.categories.items() if v["official_label"] == "燃やすごみ")
        spec = next(s for s in m.schedules if s["category_id"] == burn)
        self.assertEqual(spec["origin"], "expanded")                     # 合成の資料に規則のマスがあること (下の確かめの前提)
        hol = [e for e in m.exceptions if e["kind"] == "holiday_policy"]
        return m, burn, spec, hol

    def test_own_item_applied(self):
        m, burn, spec, hol = self.read("（燃やすごみは祝日も収集）")
        self.assertEqual([(e["value"], e["scope"]["category_ids"]) for e in hol], [("collected", [burn])])
        self.assertEqual(spec["qualifiers"], [])
        m, burn, spec, hol = self.read("（祝日も収集）")                 # 品目を言わない文は今までどおりそのマスの品目
        self.assertEqual([(e["value"], e["scope"]["category_ids"]) for e in hol], [("collected", [burn])])

    def test_other_item_left(self):
        for note in ("（燃やさないごみは祝日も収集）", "（ペットボトルは祝日も収集）", "（燃やすごみ以外は祝日も収集）"):
            with self.subTest(note=note):
                m, burn, spec, hol = self.read(note)
                self.assertEqual(hol, [])
                qid = expand.rest_qualifier_id(rules.parse("毎週月曜日" + note).rest)
                self.assertEqual([q["qualifier_id"] for q in spec["qualifiers"]], [qid])
                self.assertIn(f"unconsumed_qualifier:{qid}", expand.expand_series(spec)["blocked_by"])
                self.assertTrue(all(i.get("text") for i in m.queue if i["kind"] == "qualifier"))


# ---- 名前のマスの書き添えの待ち行列の項目に文と月 ----
class TestNameCellQueueText(unittest.TestCase):
    H = "<h1>令和8年度 ごみの収集日</h1>"

    def setUp(self):
        self._body, self._bp = stage2.body_of, fetch_docs.load_boilerplate
        fetch_docs.load_boilerplate = lambda host: None

    def tearDown(self):
        stage2.body_of, fetch_docs.load_boilerplate = self._body, self._bp

    def run_page(self, html, ctx_unconsumed=True):
        tr = lambda cells, tag: "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
        body = (f"<html><head><title>令和8年度 ごみ収集日</title></head><body><main>{self.H}{html[0]}<table>" + tr(html[1], "th") +
                "".join(tr(r, "td") for r in html[2:]) + "</table></main></body></html>").encode("utf-8")
        stage2.body_of = lambda url: (body, {"headers": {"content-type": "text/html; charset=utf-8"}})
        m = stage2.Muni(LG, "テスト町")
        stage2.read_source(m, {"url": "https://example.invalid/p0.html", "file_type": "html", "format_class": "html_table",
                               "last_modified": "Wed, 01 Apr 2026 00:00:00 GMT", "fetched_at": "2026-10-19T00:00:00+09:00"},
                           types.SimpleNamespace(llm=False, offline=True), [])
        build.enqueue_source_qualifiers(m, "s1")
        ctx = {"sources": {"s1": {"kind": "html", "sentences": [], "unconsumed": [], "table_pages": None}}}
        doc = stage2.build_doc(m)
        build.ensure_qualifier_items(m, doc, ctx)
        return m, doc

    def test_name_cell_items_have_text_and_months(self):
        m, doc = self.run_page(["<p>祝日も収集します。</p>", ["地区", "燃やすごみ ※年末は別日程", "不燃ごみ"],
                                ["東町（※祝日は収集しません）", "月曜日・木曜日", "第2水曜日"], ["西町", "火曜日・金曜日", "第4水曜日"]])
        items = {i["item_id"].rsplit(":", 1)[-1]: i for i in m.queue if i["kind"] == "qualifier"}
        q_hol, q_ye = expand.rest_qualifier_id("※祝日は収集しません"), expand.rest_qualifier_id("※年末は別日程")
        self.assertEqual({k: (v["text"], v["months"]) for k, v in items.items()},
                         {q_hol: ("※祝日は収集しません", []), q_ye: ("※年末は別日程", [12])})
        self.assertNotIn("文は読みの記録に無い", items[q_hol]["note"])
        need = {b.split(":", 1)[1] for s in doc["schedules"] for b in s["blocked_by"] if b.startswith("unconsumed_qualifier:")}
        need |= {r["note"] for s in doc["schedules"] for r in s.get("unknown_ranges") or [] if r.get("reason") == "unconsumed_qualifier"}
        self.assertTrue(need)
        self.assertLessEqual(need, set(items))

    def test_existing_item_without_text_filled(self):
        m = stage2.Muni(LG, "テスト町")
        m.sources.append({"source_id": "s1", "sha256": "z" * 64, "extra": {"qualifiers_unconsumed": [], "qualifiers_name_cells": [
            {"qualifier_id": "qcccccccccc", "text": "※祝日は収集しません", "months": []}]}})
        m.schedules.append({"district_id": "d1", "category_id": "c1", "period": dict(FY26), "origin": "listed",
                            "printed_dates": ["2026-04-06"], "exceptions": [], "qualifiers": [{"qualifier_id": "qcccccccccc", "months": []}],
                            "markup_unhandled": [], "disputes": [], "provenance": {"readers": [], "status": "agreed"},
                            "ref": {"source_id": "s1"}, "extra": {}, "extra_blocked": []})
        m.queue.append({"item_id": f"{LG}:p1:qualifier:s1:qcccccccccc", "kind": "qualifier", "status": "open", "text": None,
                        "series": [], "note": "x" + build._NO_TEXT})
        build.ensure_qualifier_items(m, {"schedules": []}, {"sources": {}})
        it = [i for i in m.queue if i["kind"] == "qualifier"]
        self.assertEqual(len(it), 1)
        self.assertEqual((it[0]["text"], it[0]["months"], it[0]["note"], it[0]["series"]), ("※祝日は収集しません", [], "x", [["d1", "c1", "p1"]]))


if __name__ == "__main__":
    unittest.main()
