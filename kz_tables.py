"""金沢の区域表HTML → tables をダンプ"""
import sys, re, html, json
sys.stdout.reconfigure(encoding="utf-8")
t = open("data/kanazawa_rules.html", encoding="utf-8", errors="replace").read()
tables = re.findall(r"<table.*?</table>", t, re.S)
print("tables:", len(tables))
def cells(tr):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip().replace("\n", " ")
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
allrows = []
for i, tb in enumerate(tables):
    rows = [cells(tr) for tr in re.findall(r"<tr.*?</tr>", tb, re.S)]
    allrows += [[i] + r for r in rows]
    if i < 3 or "-v" in sys.argv:
        print(f"== table {i} rows={len(rows)}")
        for r in rows[:8]: print("  ", r)
json.dump(allrows, open("data/kanazawa_rows.json", "w", encoding="utf-8"), ensure_ascii=False)
print("total rows", len(allrows))
# 見出し
for h in re.findall(r"<h[2-4][^>]*>(.*?)</h[2-4]>", t, re.S)[:20]:
    print("H:", re.sub(r"<[^>]+>", "", h).strip())
