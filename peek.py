"""python peek.py <file> [n] : 文字コード自動判定してn行表示 / zipなら中身一覧"""
import sys, zipfile, pathlib

p = pathlib.Path(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
sys.stdout.reconfigure(encoding="utf-8")
if zipfile.is_zipfile(p):
    with zipfile.ZipFile(p) as z:
        for i in z.infolist():
            name = i.filename
            try: name = i.filename.encode("cp437").decode("cp932")
            except Exception: pass
            print(f"{i.file_size:>10}  {name}")
    sys.exit()
b = p.read_bytes()
for enc in ("utf-8-sig", "cp932"):
    try: t = b.decode(enc); break
    except UnicodeDecodeError: pass
print(f"[{enc}] lines={t.count(chr(10))}")
for line in t.splitlines()[:n]: print(line)
