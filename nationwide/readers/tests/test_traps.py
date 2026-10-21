"""罠の再現セット (eval/traps.json) の文法・広げ方・HTML の表の段 (stage2) と、型 D・比べ・待ち行列の段 (stage3) が
すべて期待する安全な結果になるか (ネットにつながない。stage3 は build.py をキャッシュだけで通す)"""
import json, unittest

import _util  # noqa: F401
import traps


class TestTraps(unittest.TestCase):
    def test_traps_json_shape(self):
        ts = traps.load()
        ids = [t["trap_id"] for t in ts]
        self.assertEqual(len(ids), len(set(ids)))
        for t in ts:
            self.assertIn(t["stage"], ("stage1", "stage2", "stage3", "later"), t["trap_id"])
            self.assertTrue(t["expected"] and t["trap"], t["trap_id"])
        self.assertEqual(len([t for t in ts if t["stage"] != "stage3"]), 21)   # DESIGN §6.2 の表の行 (三宅・川北などは 1 行)
        checked = {t["trap_id"] for t in ts if t["stage"] in ("stage2", "stage3")}
        self.assertEqual(checked, set(traps.CHECKS))

    def test_stage2_traps(self):
        res = traps.run_all()
        bad = [r for r in res if not r["ok"] and not r.get("skipped")]
        self.assertEqual(bad, [], json.dumps(bad, ensure_ascii=False))
        skipped = [r["trap_id"] for r in res if r.get("skipped")]
        if skipped:
            self.skipTest(f"資料がキャッシュに無い罠を飛ばした: {skipped}")

    def test_stage3_traps(self):
        res = traps.run_all("stage3")
        bad = [r for r in res if not r["ok"] and not r.get("skipped")]
        self.assertEqual(bad, [], json.dumps(bad, ensure_ascii=False))
        skipped = [r["trap_id"] for r in res if r.get("skipped")]
        if skipped:
            self.skipTest(f"資料がキャッシュに無い罠を飛ばした: {skipped}")


if __name__ == "__main__":
    unittest.main()
