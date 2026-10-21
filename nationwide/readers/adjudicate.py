"""裁定の待ち行列 (DESIGN.md §3.12)。この版は待ち行列をファイルに書くだけで、どの食い違いも自分では決めない

  python nationwide/readers/adjudicate.py summary [--codes 112429,…]     # 自治体ごとの待ち行列の数 (kind・行き先ごと)

- 待ち行列は data/readers/adjudications/queue/<lg_code>.json (git に入れない)。1 件は
  {"item_id", "kind", "route", "priority", "status": "open", "readings", "evidence", "series", "source_sha256"}
- kind ごとの行き先 (§3.12):
    structure                                 → コードのやり方 3 (まだ無い) → 人。LLM の票では決めない
    cell・label・category・members・rule・policy・mode → 強いモデル (Claude Opus 5.5。§9 の 2 で決めるまで呼ばない) → 人
    version・rule_vs_printed・markup・qualifier、policy のうち推し量りを認めるもの (others) と deferred を caveat に下げるもの
                                              → 人 → 自治体への問い合わせ
- 人の裁定の記録は data/readers/adjudications/decisions/<lg_code>.json と、回帰に使う eval/fixtures/ (金沢の rule_vs_printed)。
  記録は資料の sha256 ごとで、sha256 が今の資料と違えば使わない。表の形 (structure) は人 (human) か、コードのやり方 3 (code3) が
  ほかの 2 つのどちらかと一致した記録だけを使う。強いモデル (opus) の記録は、2 つの読みのどちらかと一致したときだけ adjudicated にする
- 優先 (priority) は §3.12 の 4 のとおり当たる人口 × 当たる日の数。人口はまだ持っていないので、当たる系列の数 × 日の数で代える
"""
import collections, json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
QUEUE_DIR = ROOT / "data" / "readers" / "adjudications" / "queue"
DECISION_DIR = ROOT / "data" / "readers" / "adjudications" / "decisions"
FIXTURES = HERE / "eval" / "fixtures"

MODEL_KINDS = ("cell", "label", "category", "members", "rule", "policy", "mode")
HUMAN_KINDS = ("version", "rule_vs_printed", "markup", "qualifier")
KINDS = ("structure",) + MODEL_KINDS + HUMAN_KINDS
ROUTES = {"structure": ["code3", "human"], **{k: ["strong_model", "human"] for k in MODEL_KINDS},
          **{k: ["human", "inquiry"] for k in HUMAN_KINDS}}
HUMAN_POLICY = ("others", "deferred_to_caveat")            # policy のうち人だけが決めるもの
ALLOWED_BY = {"structure": ("human", "code3", "inquiry"), **{k: ("opus", "human", "inquiry") for k in MODEL_KINDS},
              **{k: ("human", "inquiry") for k in HUMAN_KINDS}}


def route(item):
    """待ち行列の 1 件 → 行き先のリスト"""
    k = item.get("kind")
    if k == "policy" and item.get("policy_part") in HUMAN_POLICY:
        return ["human", "inquiry"]
    return list(ROUTES.get(k, ["human"]))


def priority(item):
    """当たる系列の数 × 日の数 (人口の代わり。§3.12 の 4)"""
    n_series = max(1, len(item.get("series") or []))
    n_days = max(1, int(item.get("days") or 1))
    return n_series * n_days


def normalize(items):
    """build・validate が足した待ち行列の項目 → 行き先と優先と状態をそろえたもの (同じ item_id は 1 つにまとめ、当たる系列を足す)"""
    by = collections.OrderedDict()
    for it in items:
        k = it.get("kind")
        if k not in KINDS:
            raise ValueError(f"知らない kind: {k} ({it.get('item_id')})")
        cur = by.get(it["item_id"])
        if cur is None:
            cur = dict(it)
            cur.setdefault("status", "open")
            cur["series"] = list(it.get("series") or [])
            by[it["item_id"]] = cur
        else:
            for s in it.get("series") or []:
                if s not in cur["series"]:
                    cur["series"].append(s)
    out = []
    for it in by.values():
        it["route"] = route(it)
        it["next"] = it["route"][0]
        it["priority"] = priority(it)
        out.append(it)
    out.sort(key=lambda x: (-x["priority"], x["item_id"]))
    return out


def write_queue(lg, items, queue_dir=QUEUE_DIR):
    """待ち行列を書く (自治体ごとに 1 ファイル。前の中身は置きかえる)"""
    queue_dir = pathlib.Path(queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    p = queue_dir / f"{lg}.json"
    body = {"lg_code": lg, "items": items, "counts": dict(collections.Counter(i["kind"] for i in items)),
            "routes": dict(collections.Counter(i["next"] for i in items)),
            "note": "裁定の待ち行列 (DESIGN.md §3.12)。この版は書くだけで、どの項目も自分では決めない"}
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    return p


def load_decisions(lg, decision_dir=DECISION_DIR):
    """人などの裁定の記録 → {item_id: 記録}。記録の形: {"item_id", "by": "human" | "opus" | "code3" | "inquiry", "at",
    "decision": "A" | "B" | "value" | "meaningless" | "consumed" | "already_printed" | "rule:<名前>" | "printed",
    "value": …, "source_sha256": "…", "matches": "A" | "B" | null, "note": "…"}"""
    out = {}
    p = pathlib.Path(decision_dir) / f"{lg}.json"
    if p.exists():
        for d in json.loads(p.read_text("utf-8")).get("decisions", []):
            out[d["item_id"]] = d
    return out


def decision_usable(item, dec, source_sha256s):
    """裁定の記録を、この待ち行列の項目に使ってよいか → (よいか, わけ)"""
    if dec is None:
        return False, "記録が無い"
    by = dec.get("by")
    if by not in ALLOWED_BY.get(item["kind"], ("human",)):
        return False, f"{item['kind']} は {by} の記録では決めない"
    if item["kind"] == "policy" and item.get("policy_part") in HUMAN_POLICY and by != "human":
        return False, "推し量りを認める・deferred を caveat に下げるのは人だけ"
    if by == "opus" and dec.get("matches") not in ("A", "B"):
        return False, "強いモデルの記録は 2 つの読みのどちらかと一致したときだけ使う"
    if by == "code3" and dec.get("matches") not in ("A", "B"):
        return False, "コードのやり方 3 は、ほかの 2 つのどちらかと一致したときだけ使う"
    sha = dec.get("source_sha256")
    if not sha or sha not in set(source_sha256s or ()):
        return False, "記録の資料の sha256 が今の資料と違う (か書いていない)"
    return True, ""


def apply_decisions(doc, queue, decisions):
    """使ってよい裁定の記録がある項目について、系列の blocked_by の disputed:<item_id> を外し、provenance.adjudication を書く →
    使った記録の数。日付は作り直さない (呼ぶ側が読み直す)。記録が無ければ何もしない"""
    srcs = [s.get("sha256") for s in doc.get("sources", [])]
    by_item = {q["item_id"]: q for q in queue}
    used = 0
    for s in doc.get("schedules", []):
        for b in list(s.get("blocked_by", [])):
            if not b.startswith("disputed:"):
                continue
            iid = b.split(":", 1)[1]
            it = by_item.get(iid)
            dec = decisions.get(iid)
            ok, _ = decision_usable(it, dec, srcs) if it else (False, "")
            if not ok:
                continue
            s["blocked_by"].remove(b)
            prov = s.setdefault("provenance", {})
            prov["adjudication"] = {"item_id": iid, "by": dec["by"], "at": dec.get("at"), "note": dec.get("note")}
            used += 1
    return used


def summary(codes=None, queue_dir=QUEUE_DIR):
    out = {}
    for p in sorted(pathlib.Path(queue_dir).glob("*.json")):
        lg = p.stem
        if codes and lg not in codes:
            continue
        q = json.loads(p.read_text("utf-8"))
        out[lg] = {"items": len(q["items"]), "kinds": q["counts"], "next": q["routes"]}
    return out


def main(argv=None):
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="裁定の待ち行列")
    ap.add_argument("cmd", choices=["summary"])
    ap.add_argument("--codes", default="")
    a = ap.parse_args(argv)
    codes = {c.strip() for c in a.codes.split(",") if c.strip()}
    for lg, r in summary(codes).items():
        print(lg, json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
