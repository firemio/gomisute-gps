import json, sys, difflib, geopandas as gpd
sys.stdout.reconfigure(encoding="utf-8")
sys.argv = ["x"]; exec(open("split_blocks.py", encoding="utf-8").read().split("# ---- 入力 ----")[0])
poly = gpd.read_file("data/estat_17201/r2ka17201.shp")
names = sorted({norm_town(n) for n in poly["S_NAME"]})
used = set(json.load(open("out/kanazawa_report.json", encoding="utf-8"))["missing_poly"])
for m in json.load(open("out/kanazawa_report.json", encoding="utf-8"))["missing_poly"]:
    pre = [n for n in names if n.startswith(m) or m.startswith(n)]
    close = difflib.get_close_matches(m, names, n=3, cutoff=0.6)
    print(m, "| prefix:", pre[:6], "| close:", close)
