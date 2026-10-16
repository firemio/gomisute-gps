import sys, zipfile, io, pandas as pd, geopandas as gpd
sys.stdout.reconfigure(encoding="utf-8")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
with zipfile.ZipFile("data/gsi_jukyo_17201.zip") as z:
    raw = z.read("17201/j17201k_20250731.csv")
for enc in ("utf-8-sig", "cp932"):
    try: txt = raw.decode(enc); break
    except UnicodeDecodeError: pass
df = pd.read_csv(io.StringIO(txt), dtype=str)
df.to_csv("data/gsi_jukyo_17201.csv", index=False, encoding="utf-8")
print("GSI", enc, df.shape); print(df.head(5)); print(df.columns.tolist())
g = gpd.read_file("data/estat_17201/r2ka17201.shp")
print("ESTAT", g.shape, g.crs); print(g.drop(columns="geometry").head(5))
print(g.columns.tolist())
