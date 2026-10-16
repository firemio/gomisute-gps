"""デジタル庁 アドレス・ベース・レジストリ(ABR) 取得: python fetch_abr.py [lg_code6=172014] [--all]

配布元は 2025年に catalog.registries.digital.go.jp → data.address-br.digital.go.jp へ移転
(索引: https://dataset.address-br.digital.go.jp/api/feed/dcat-us/1.1.json)
  町字 / 町字フルセット(廃止含む) / 地番 / 地番位置参照 は市区町村単位、
  町字位置参照 / 住居表示 は都道府県単位でしか配布されていない
--all で全国 町字(フルセット) も取得 (スキーマ検討用)
出力: data/abr/*.csv (UTF-8, ヘッダ付)
"""
import sys, io, zipfile, pathlib, urllib.request

BASE = "https://data.address-br.digital.go.jp"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
lg = args[0] if args else "172014"
pref = lg[:2]
targets = [
    f"mt_town/city/mt_town_city{lg}.csv.zip",
    f"mt_town_fullset/city/mt_town_fullset_city{lg}.csv.zip",
    f"mt_parcel/city/mt_parcel_city{lg}.csv.zip",
    f"mt_parcel_pos/city/mt_parcel_pos_city{lg}.csv.zip",
    f"mt_town_pos/pref/mt_town_pos_pref{pref}.csv.zip",
    f"mt_rsdtdsp_blk/pref/mt_rsdtdsp_blk_pref{pref}.csv.zip",
    f"mt_rsdtdsp_blk_pos/pref/mt_rsdtdsp_blk_pos_pref{pref}.csv.zip",
    "mt_city/mt_city_all.csv.zip",
]
if "--all" in sys.argv:
    targets.append("mt_town_fullset/mt_town_fullset_all.csv.zip")

out = pathlib.Path("data/abr"); out.mkdir(parents=True, exist_ok=True)

def unzip(b):
    """配布zipは zip の中に csv.zip が入れ子になっていることがあるので再帰的に展開"""
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for n in z.namelist():
            data = z.read(n)
            if n.endswith(".zip"): yield from unzip(data)
            elif n.endswith(".csv"): yield pathlib.Path(n).name, data

for t in targets:
    req = urllib.request.Request(f"{BASE}/{t}", headers={"User-Agent": "Mozilla/5.0 (gomisute-gps research)"})
    b = urllib.request.urlopen(req, timeout=300).read()
    for name, data in unzip(b):
        (out / name).write_bytes(data)
        print(f"{t}  {len(b):>10,}B zip → {name} {len(data):,}B")
