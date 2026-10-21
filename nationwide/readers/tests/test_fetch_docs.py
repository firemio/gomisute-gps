"""fetch_docs.py: 段 0 の資料の一覧 (ネットにつながない。発見の結果とキャッシュは一時フォルダに作る)"""
import json, os, pathlib, tempfile, time, unittest

import _util
import fetch_docs, fetcher

HUB = "https://www.example.lg.jp/gomi/index.html"
HUB_HTML = """<html><body><div id="contents"><h1>ごみの収集日</h1>
<h2>令和８年度 地区別カレンダー</h2>
<p>※収集地区で斜体で記載したところは、番地により別の地区があります。</p>
<ul>""" + "".join(f'<li><a href="/files/R8_chiku{i:02d}.pdf">第{i}地区 [PDF]</a></li>' for i in range(1, 7)) + """</ul>
<h2>お知らせ</h2><p><a href="/files/nenmatsu.pdf">年末年始の収集</a> は 12 月に載せます。</p>
</div><div id="footer">閉庁日：土曜日・日曜日・祝日、年末年始</div></body></html>"""


def put(cache, url, body, ctype, lm=None):
    u = fetcher.norm_url(url)
    tmp = cache.tmp(u)
    pathlib.Path(tmp).write_bytes(body)
    meta = {"url": u, "status": 200, "headers": {"content-type": ctype, **({"last-modified": lm} if lm else {})},
            "checked": time.time(), "fetched_at": "2026-10-19T03:00:00+09:00", "size": len(body), "sha256": "x" * 64}
    cache.put(u, meta, tmp=tmp, compress="html" in ctype)


def discover_result(lg="999990"):
    docs = [{"url": f"https://www.example.lg.jp/files/R8_chiku{i:02d}.pdf", "text": f"第{i}地区 [PDF]", "format_class": "pdf_unchecked",
             **({"not_fetched": "hub_max_docs"} if i > 2 else {})} for i in range(1, 7)]
    docs.append({"url": "https://www.example.lg.jp/files/nenmatsu.pdf", "text": "年末年始の収集", "format_class": "pdf_unchecked",
                 "not_fetched": "robots_disallowed"})
    return {"lg_code": lg, "name": "テスト市", "complete": True, "finished_at": "2026-10-19T03:00:00+09:00", "format_class": "html_text",
            "best": {"url": HUB},
            "candidates": [{"url": HUB, "role": "hub", "score": 60, "format_class": "html_text", "file_type": "html",
                            "kinds": ["calendar"], "anchors": ["ごみ"], "found_from": "https://www.example.lg.jp/", "documents": docs},
                           {"url": "https://www.example.lg.jp/garbage.php?mode=cal&ac=1", "role": "schedule", "score": 70,
                            "format_class": "html_table", "file_type": "html", "kinds": ["calendar"], "anchors": ["カレンダー"],
                            "found_from": HUB}]}


class TestFetchDocs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.cache_dir, self.out, self.results = root / "cache", root / "readers", root / "discover"
        self.results.mkdir()
        self.cache = fetcher.Cache(self.cache_dir)
        put(self.cache, HUB, HUB_HTML.encode("utf-8"), "text/html")
        for i in (1, 2):
            put(self.cache, f"https://www.example.lg.jp/files/R8_chiku{i:02d}.pdf", b"%PDF-1.4 test", "application/pdf",
                "Tue, 07 Apr 2026 00:26:27 GMT")
        p = self.results / "999990.json"
        p.write_text(json.dumps(discover_result(), ensure_ascii=False), "utf-8")
        old = time.time() - 600
        os.utime(p, (old, old))

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, **kw):
        return fetch_docs.run("999990", self.cache_dir, self.out, results=self.results, **kw)

    def test_list(self):
        r = self.run_()
        self.assertEqual(r["docs"], 9)
        self.assertEqual(r["cache"], {"cached": 3, "missing": 5, "robots_disallowed": 1})
        out = json.loads((self.out / "999990" / "docs.json").read_text("utf-8"))
        by = {d["url"]: d for d in out["docs"]}
        self.assertEqual(by[HUB]["cache"], "cached")
        self.assertTrue(by[HUB]["best"])
        nen = by["https://www.example.lg.jp/files/nenmatsu.pdf"]
        self.assertEqual(nen["cache"], "robots_disallowed")
        self.assertEqual(nen["blocked_by"], ["doc_not_fetched"])            # 取れない資料も黙って落とさない
        self.assertEqual(r["district_sets"], 1)
        self.assertTrue(all(by[f"https://www.example.lg.jp/files/R8_chiku{i:02d}.pdf"].get("district_set") for i in range(1, 7)))

    def test_anchor_context(self):
        self.run_()
        out = json.loads((self.out / "999990" / "docs.json").read_text("utf-8"))
        d = next(x for x in out["docs"] if x["url"].endswith("R8_chiku03.pdf"))
        ac = d["anchor_context"]
        self.assertEqual(ac["anchor_text"], "第3地区 [PDF]")
        self.assertEqual(ac["heading_path"], ["ごみの収集日", "令和８年度 地区別カレンダー"])
        self.assertIn("<li>", ac["block_html"])                               # タグを残す
        self.assertEqual(ac["page"], HUB)

    def test_dynamic_month(self):
        self.run_()
        out = json.loads((self.out / "999990" / "docs.json").read_text("utf-8"))
        d = next(x for x in out["docs"] if "garbage.php" in x["url"])
        self.assertTrue(d["dynamic_month"])
        self.assertEqual(len(d["source_keys"]), 12)
        self.assertTrue(d["source_keys"][0].endswith("&year=2026&month=4"))
        self.assertTrue(d["source_keys"][-1].endswith("&year=2027&month=3"))

    def test_limits_exempt_sets(self):
        docs = fetch_docs.collect(discover_result(), self.cache)
        fetch_docs.mark_sets(docs)
        used_n, _ = fetch_docs.apply_limits(docs, max_docs=2)
        self.assertEqual(used_n, 2)                                            # 組の 6 本は数えない
        over = [d for d in docs.values() if d.get("over_limit")]
        self.assertEqual(len(over), 1)
        self.assertFalse(any(d.get("district_set") and d.get("over_limit") for d in docs.values()))

    def test_fetch_missing_uses_fetcher(self):
        class FakeResult:
            def __init__(self, ok):
                self.ok, self.kind = ok, "ok" if ok else "robots_disallowed"
                self.status, self.error, self.sha256, self.size = (200 if ok else None), None, "y" * 64, 10
                self.fetched_at, self.headers = "2026-10-19T09:00:00+09:00", {"last-modified": "Tue, 07 Apr 2026 00:26:27 GMT"}

        class FakeFetcher:
            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)
                return FakeResult(not url.endswith("06.pdf"))

        f = FakeFetcher()
        r = self.run_(fetch=True, max_requests=10, f=f)
        self.assertEqual(r["fetched_now"], 4)
        self.assertTrue(all("example.lg.jp/files/R8_chiku" in u for u in f.urls))   # 一覧の資料だけ。日ごとに変わるページは取らない
        out = json.loads((self.out / "999990" / "docs.json").read_text("utf-8"))
        d6 = next(x for x in out["docs"] if x["url"].endswith("06.pdf"))
        self.assertEqual(d6["cache"], "robots_disallowed")

    def test_skip_unfinished(self):
        p = self.results / "999991.json"
        p.write_text(json.dumps({**discover_result("999991"), "complete": False}), "utf-8")
        old = time.time() - 600
        os.utime(p, (old, old))
        self.assertEqual(fetch_docs.run("999991", self.cache_dir, self.out, results=self.results)["skipped"], "incomplete")
        p2 = self.results / "999992.json"
        p2.write_text(json.dumps(discover_result("999992")), "utf-8")         # 書いたばかり (書いている途中かもしれない)
        self.assertEqual(fetch_docs.run("999992", self.cache_dir, self.out, results=self.results)["skipped"], "being_written")
        self.assertEqual(fetch_docs.run("999993", self.cache_dir, self.out, results=self.results)["skipped"], "no_result")

    def test_boilerplate_written(self):
        self.run_()
        hosts = list((self.out / "boilerplate").glob("*.json"))
        self.assertEqual(len(hosts), 1)
        self.assertEqual(fetch_docs.load_boilerplate("www.example.lg.jp", self.out / "boilerplate"), set())   # ページ 1 つでは数えない
        d = json.loads(hosts[0].read_text("utf-8"))
        self.assertEqual(set(d["regions"]), set(d["pages"]))              # 関係の有る無しに使う本文の範囲の行も残す


if __name__ == "__main__":
    unittest.main()
