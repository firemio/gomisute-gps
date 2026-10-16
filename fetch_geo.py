"""python fetch_geo.py <muni_code5> [year=2020]  → e-Stat 国勢調査 小地域境界 Shapefile を取得・展開
  year: 2000 / 2005 / 2010 / 2015 / 2020 (旧町名の系譜は過去年の差分で取る → census_lineage.py)
  出力: 2020 は data/estat_<code>/ 、それ以外は data/estat_<code>_<year>/
URLに & を含むので win-cli 経由で直接渡せない対策"""
import sys, zipfile, io, pathlib, urllib.request
code = sys.argv[1]
year = sys.argv[2] if len(sys.argv) > 2 else "2020"
datum = "2011" if year >= "2015" else "2000"          # 2010年以前は JGD2000 のみ配布
url = ("https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A00200521" + year +
       f"&code={code}&coordSys=1&format=shape&downloadType=5&datum={datum}")
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
b = urllib.request.urlopen(req, timeout=120).read()
out = pathlib.Path(f"data/estat_{code}" if year == "2020" else f"data/estat_{code}_{year}"); out.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(b)) as z:
    z.extractall(out); print(len(b), "bytes", z.namelist())
