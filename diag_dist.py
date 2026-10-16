import csv, sys, collections
sys.stdout.reconfigure(encoding="utf-8")
c = collections.Counter(r["district"] for r in csv.DictReader(open("data/kanazawa_rules.csv", encoding="utf-8")))
for d, n in sorted(c.items(), key=lambda x: -len(x[0]))[:8]: print(len(d), n, d)
for r in csv.DictReader(open("data/kanazawa_rules.csv", encoding="utf-8")):
    if "注意" in r["district"]: print(r)
