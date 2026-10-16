import json, sys, collections, datetime as dt
sys.stdout.reconfigure(encoding="utf-8")
WD = "月火水木金土日"
d = json.load(open("out/nagano.json", encoding="utf-8"))
by = collections.Counter((s["category_label"], s["rule_kind"]) for s in d["schedules"])
for k, v in sorted(by.items()): print(k, v)
print("--- cal01 explicit samples")
for s in d["schedules"]:
    if s["zone_id"] == "20201-cal01" and s["rule_kind"] == "explicit":
        ds = [dt.date.fromisoformat(x) for x in s["dates"]]
        gaps = collections.Counter((b - a).days for a, b in zip(ds, ds[1:]))
        print(s["category_label"], len(ds), "曜", collections.Counter(WD[x.weekday()] for x in ds).most_common(3),
              "第N", collections.Counter((x.day - 1) // 7 + 1 for x in ds).most_common(4), "間隔", gaps.most_common(4))
