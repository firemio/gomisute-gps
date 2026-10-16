import zipfile, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
out = pathlib.Path("data/nagano_2026"); out.mkdir(exist_ok=True)
with zipfile.ZipFile("data/nagano_2026.zip") as z:
    for i in z.infolist():
        b = z.read(i)
        try: t = b.decode("utf-8-sig")
        except UnicodeDecodeError: t = b.decode("cp932")
        (out / i.filename).write_text(t, encoding="utf-8")
print("extracted", len(list(out.iterdir())))
