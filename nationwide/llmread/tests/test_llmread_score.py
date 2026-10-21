"""score.py・full.py score: git に入れた生の答え (out/llmread/raw/) から、モデルを呼ばずに採点を作り直す

女川・金沢の正解は out/ にある (git に入れたもの)。japan-gomi-data の正解は data/readers/eval/ にあるときだけ使う
(無ければ python nationwide/llmread/jgd.py fetch && python nationwide/llmread/jgd.py convert)。
"""
import json, os, subprocess, sys, unittest

import _util
import calc, jgd, runners, score

OUT = _util.ROOT / "out" / "llmread"


def raw_answer(model, doc_id):
    return json.loads((OUT / "raw" / runners.model_tag(model) / f"{doc_id}.json").read_text("utf-8"))["answer"]


def saved_summary(name, model):
    return json.loads((OUT / "scores" / f"{name}.json").read_text("utf-8"))[model]["summary"]


class TestKnownCases(unittest.TestCase):
    def test_onagawa_month_opus(self):
        """女川 2026 年 4 月 (Opus): マスの採点が保存した採点と同じ"""
        ans = raw_answer("opus", "045811-2026-04")
        self.assertEqual(calc.validate(ans), [])
        ex = calc.expand_answer(ans, ("2026-04-01", "2026-04-30"))
        r = score.score_onagawa("2026-04", ans, ex)
        self.assertEqual(r["summary"], saved_summary("onagawa_2026-04", "opus"))
        self.assertEqual(r["summary"]["units"], 26)

    def test_onagawa_merged_cell_error_deepseek(self):
        """女川 2026 年 7 月 (DeepSeek): 結合したマスの読み違い (燃やせないごみを粗大ごみの日と読んだ) が違う日として出る"""
        ans = raw_answer("deepseek", "045811-2026-07")
        ex = calc.expand_answer(ans, ("2026-07-01", "2026-07-31"))
        r = score.score_onagawa("2026-07", ans, ex)
        self.assertEqual(r["summary"], saved_summary("onagawa_2026-07", "deepseek"))
        self.assertGreater(r["summary"]["answered_wrong"], 0)

    def test_kanazawa_district(self):
        """金沢の三和 (DeepSeek): 正解 (v0.1 の RRULE を広げたもの) と比べた採点が保存した採点と同じ"""
        ans = raw_answer("deepseek", "172014-三和")
        ex = calc.expand_answer(ans, ("2026-04-01", "2027-03-31"))
        r = score.score_kanazawa("三和", ans, ex)
        self.assertEqual(r["summary"], saved_summary("kanazawa_三和", "deepseek"))
        self.assertEqual(r["summary"]["units"], 5)

    def test_legacy_occurrences(self):
        s = {"valid_from": "2026-04-01", "valid_to": "2026-04-30", "rule_kind": "rrule", "rrule": "FREQ=WEEKLY;BYDAY=MO",
             "exdates": ["2026-04-13"], "rdates": ["2026-04-04"]}
        got = sorted(d.isoformat() for d in score.legacy_occurrences(s))
        self.assertEqual(got, ["2026-04-04", "2026-04-06", "2026-04-20", "2026-04-27"])


class TestReproduce(unittest.TestCase):
    def test_score_check(self):
        """python -m nationwide.llmread score --check: 生の答えから作り直した results.json と scores/ が、今のものと同じ"""
        if jgd.load_gt("112275") is None or jgd._cached_body(jgd.TARBALL_URL) is None:
            self.skipTest("japan-gomi-data の正解が無い (jgd.py fetch・convert)")
        p = subprocess.run([sys.executable, "-m", "nationwide.llmread", "score", "--check"], cwd=_util.ROOT,
                           capture_output=True, text=True, encoding="utf-8", timeout=600,
                           env={**os.environ, "GOMISUTE_LLM_API_KEY": "", "PYTHONIOENCODING": "utf-8"})   # 鍵を渡さない
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertIn("すべて同じ", p.stdout)


if __name__ == "__main__":
    unittest.main()
