"""採点の入力: 2つの方式の答えを混ぜ、方式を隠した ID (A01...) を付けて、問いごとの参照と組にする
  python bench/grading/make_batches.py → out/cli_bench/grading/batch_*.json (採点するエージェントに渡す)、鍵は out/cli_bench/grading_key/key.json
採点は Claude Code のワークフロー bench/grading/grade_workflow.js (束ごとに採点者2人 → 食い違いと指摘を3人目が資料で裁定)。
結果を python bench/grading/merge_scores.py <ワークフローの結果.json> で方式ごとにまとめ、python bench/cli_summary.py out/cli_bench/grading/scores.json"""
import json, os, random, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "out", "cli_bench", "grading")
refs = json.load(open(os.path.join(HERE, "refs.json"), encoding="utf-8"))
items, key = [], {}
for mode in ("mcp", "web"):
    for qid in refs:
        p = os.path.join(ROOT, "out", "cli_bench", mode, qid + ".json")
        if not os.path.exists(p): continue
        d = json.load(open(p, encoding="utf-8"))
        items.append({"qid": qid, "mode": mode, "answer": d.get("result") or ""})
rnd = random.Random(7)
rnd.shuffle(items)
batches = {"kanazawa_1": [], "kanazawa_2": [], "onagawa": []}
for i, it in enumerate(items):
    aid = f"A{i + 1:02d}"
    key[aid] = {"qid": it["qid"], "mode": it["mode"]}
    entry = {"answer_id": aid, "question_id": it["qid"], "answer": it["answer"]}
    if it["qid"].startswith("c"):
        b = "kanazawa_1" if int(it["qid"][1:]) <= 14 else "kanazawa_2"
    else:
        b = "onagawa"
    batches[b].append(entry)
for b, es in batches.items():
    qids = sorted({e["question_id"] for e in es})
    json.dump({"references": {q: refs[q] for q in qids}, "answers": sorted(es, key=lambda e: (e["question_id"], e["answer_id"]))},
              open(os.path.join(HERE, f"batch_{b}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(b, len(es))
os.makedirs(os.path.join(ROOT, "out", "cli_bench", "grading_key"), exist_ok=True)
json.dump(key, open(os.path.join(ROOT, "out", "cli_bench", "grading_key", "key.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
