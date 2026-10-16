"""docs/address-normalize-schema.sql に全国 ABR を流し込み、容量・件数を確かめる (スキーマ設計パネルの審査役が作った検証用の試作)
前提: python fetch_abr.py 172014 --all (data/abr/ に全国 町字フルセット・市区町村・金沢の地番・石川の位置参照)
次に cases_check.py で金沢の 10 ケースを入れて解決結果を出す。出力 DB は addrnorm_check.sqlite (git 管理外)"""
import csv, collections, json, os, re, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keys import keys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ABR = os.path.join(REPO, "data", "abr")
DB = os.path.join(HERE, "addrnorm_check.sqlite")
SNAP = "2026-10-16"
VER = "2026.10.17-check"
LABEL_RE = re.compile(r"(土地)?区画整理(事業)?(地|地内|区域)?$")
PLACEHOLDER = {"1947-04-17", "1970-01-01"}

if os.path.exists(DB): os.remove(DB)
db = sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")
db.executescript(open(os.path.join(REPO, "docs", "address-normalize-schema.sql"), encoding="utf-8").read())
t0 = time.time()

def ins(table, **kw):
    db.execute(f"INSERT INTO {table}({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))

# ---------------- provenance rows
ins("ingest_run", run_id=f"abr:{SNAP}", kind="abr", code_ref="check", started_at=SNAP)
ins("ingest_run", run_id="research:check", kind="research", code_ref="check", started_at=SNAP)
ins("ingest_run", run_id="census:172014:2000-2020", kind="census", code_ref="check", started_at=SNAP)
ins("source", source_id=f"abr:mt_town_all:{SNAP}", kind="abr", title="ABR 町字マスター", retrieved_at=SNAP, reliability="dataset", license="CC BY 4.0")
for cid, conf in [(f"abr:{SNAP}", 0.9), (f"abr:{SNAP}:phantom_parcels", 0.8), (f"abr:{SNAP}:no_parcels", 0.3),
                  (f"abr:{SNAP}:phantom_unchecked", 0.5), (f"abr:{SNAP}:label_rule", 0.9), (f"abr:{SNAP}:group_parent", 0.9)]:
    ins("claim", claim_id=cid, aspect="batch", basis="abr_snapshot" if cid.count(":") == 1 else "rule", confidence=conf,
        verification="machine", run_id=f"abr:{SNAP}")

# ---------------- muni
for r in csv.DictReader(open(os.path.join(ABR, "mt_city_all.csv"), encoding="utf-8-sig")):
    name = r["pref"] + r["county"] + r["city"] + r["ward"]
    ins("muni", lg_code=r["lg_code"], lg5=r["lg_code"][:5], pref=r["pref"], county=r["county"] or None, city=r["city"] or None,
        ward=r["ward"] or None, name=name, abr_first_seen=SNAP, abr_last_seen=SNAP)
# 政令市の区 → 市
db.execute("""UPDATE muni SET parent_lg_code = (SELECT m2.lg_code FROM muni m2 WHERE m2.pref = muni.pref AND m2.city = muni.city
              AND m2.ward IS NULL AND m2.lg_code <> muni.lg_code) WHERE ward IS NOT NULL""")

# ---------------- parcels (金沢だけ手元にある)
parcels = collections.Counter()
for r in csv.DictReader(open(os.path.join(ABR, "mt_parcel_city172014.csv"), encoding="utf-8-sig")):
    parcels[r["machiaza_id"]] += 1
parcel_lgs = {"172014"}
pos = {}
for r in csv.DictReader(open(os.path.join(ABR, "mt_town_pos_pref17.csv"), encoding="utf-8-sig")):
    pos[(r["lg_code"], r["machiaza_id"])] = (float(r["rep_lon"]), float(r["rep_lat"]), r["pos_oaza_cho_chome_code"])

# ---------------- ABR towns
rows = {}
with open(os.path.join(ABR, "mt_town_fullset_all.csv"), encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r["machiaza_type"] not in ("1", "2", "3"):
            continue                     # type 4/5 は v0.1 では取り込まない
        k = (r["lg_code"], r["machiaza_id"])
        if k in rows:
            rows[k]["rsdt"].add(r["rsdt_addr_flg"]); continue
        rows[k] = dict(t=r["machiaza_type"], oaza=r["oaza_cho"], chome=r["chome"], cnum=r["chome_number"], koaza=r["koaza"],
                       rsdt={r["rsdt_addr_flg"]}, st=r["status_flg"], efct=r["efct_date"], post=r["post_code"])
print("abr towns", len(rows), flush=True); print("", round(time.time() - t0, 1), "s")

by4 = collections.defaultdict(list)
for (lg, mid), v in rows.items():
    by4[(lg, mid[:4])].append((mid, v))
canon_full = collections.defaultdict(dict)   # lg -> canon(name) -> town_id (type1/2)
chome_id = {}
for (lg, mid), v in rows.items():
    if v["t"] in ("1", "2"):
        nm = v["oaza"] + v["chome"]
        canon_full[lg].setdefault(keys(nm)[0], f"{lg}-{mid}")
    if v["t"] == "2":
        chome_id[(lg, mid[:4], v["chome"])] = f"{lg}-{mid}"

parent_of, parents, adopted = {}, {}, 0
phantom_groups = set()
for (lg, o4), members in by4.items():
    types = {v["t"] for _, v in members}
    if types == {"3"} and o4 != "0000": phantom_groups.add((lg, o4))
    if o4 == "0000":                      # 大字なしの小字 (北海道の号など)。親を作らない
        continue
    t1 = [mid for mid, v in members if v["t"] == "1"]
    if t1:
        pid = f"{lg}-{t1[0]}"
    else:
        oaza = members[0][1]["oaza"]
        phantom = "2" not in types
        hit = canon_full[lg].get(keys(oaza)[0]) if phantom else None
        if hit:                                   # 幽霊大字の名前が既存の町字と同じキー (山ノ上町五丁目 → 0523005)
            pid = hit; adopted += 1
        else:
            pid = f"{lg}-P{o4}"
            parents[pid] = dict(lg=lg, oaza=oaza, phantom=phantom, adopted=False)
    for mid, v in members:
        tid = f"{lg}-{mid}"
        if v["t"] == "1":
            continue
        if v["t"] == "3" and v["chome"] and (lg, o4, v["chome"]) in chome_id:
            parent_of[tid] = chome_id[(lg, o4, v["chome"])]
        else:
            parent_of[tid] = pid
print("parents", len(parents), "phantom", sum(p["phantom"] for p in parents.values()), "adopted phantom groups", adopted)

def numbering(s):
    return "mixed" if len(s) > 1 else ("jukyo" if "1" in s else "chiban")

CONF = dict(db.execute("SELECT claim_id, confidence FROM claim").fetchall())
batch = []
phantom_children = collections.defaultdict(list)
for (lg, mid), v in rows.items():
    tid = f"{lg}-{mid}"
    level = {"1": "oaza", "2": "chome", "3": "koaza"}[v["t"]]
    name = v["oaza"] + v["chome"] + v["koaza"]
    flags, status, claim = [], "current", f"abr:{SNAP}"
    pc = parcels[mid] if lg in parcel_lgs else None
    pid = parent_of.get(tid)
    in_phantom = v["t"] == "3" and (lg, mid[:4]) in phantom_groups
    if LABEL_RE.search(v["oaza"]):
        level, claim = "label", f"abr:{SNAP}:label_rule"; flags.append("label_rule")
    if v["st"] == "0": flags.append("abr_unconfirmed")
    if mid[:4] == "0000": flags.append("no_oaza")
    if v["t"] == "3":
        if in_phantom:
            flags.append("phantom" if pid in parents else "phantom_adopted")
            if pc is None:
                status, claim = "uncertain", f"abr:{SNAP}:phantom_unchecked"; flags.append("parcels_unchecked")
            elif pc == 0:
                status, claim = "uncertain", f"abr:{SNAP}:no_parcels"; flags.append("no_parcels")
            else:
                claim = f"abr:{SNAP}:phantom_parcels"
        elif pc == 0 and v["st"] == "0":
            status, claim = "uncertain", f"abr:{SNAP}:no_parcels"; flags.append("no_parcels")
    elif pc == 0 and "0" in v["rsdt"] and len(v["rsdt"]) == 1:
        flags.append("no_parcels")
    if pid in parents:
        phantom_children[pid].append((status, pc))
    c, fo, _ = keys(name)
    p = pos.get((lg, mid))
    batch.append((tid, lg, "abr", level, pid, name, v["oaza"], int(v["cnum"]) if v["cnum"] else None, v["koaza"] or None, c, fo,
                  status, status, claim, CONF[claim],
                  "machine", "present", numbering(v["rsdt"]), int(v["st"]) if v["st"] else None,
                  None if v["efct"] in PLACEHOLDER or not v["efct"] else v["efct"], SNAP, SNAP, pc, v["post"] or None,
                  p[2] if p else None, p[0] if p else None, p[1] if p else None, "abr_town_pos" if p else None, json.dumps(flags), VER))
for pid, pv in parents.items():
    lg = pv["lg"]; ch = phantom_children.get(pid, [])
    if pv["phantom"]:
        known = [pc for _, pc in ch if pc is not None]
        st = "remnant" if any(pc for pc in known) else "uncertain"
        flags = ["phantom"] + ([] if known else ["parcels_unchecked"])
        pc = sum(known) if known else None
    else:
        st, flags, pc = "current", [], None
    c, fo, _ = keys(pv["oaza"])
    batch.append((pid, lg, "abr_parent", "oaza", None, pv["oaza"], pv["oaza"], None, None, c, fo, st, st, f"abr:{SNAP}:group_parent",
                  0.9 if st == "current" else (0.8 if st == "remnant" else 0.5), "machine", "via_children", None, None, None, SNAP, SNAP,
                  pc, None, None, None, None, None, json.dumps(flags), VER))
db.execute("PRAGMA defer_foreign_keys = ON")
order = {"abr_parent": 0}
batch.sort(key=lambda r: (0 if r[2] == "abr_parent" else {"oaza": 1, "label": 1, "chome": 2, "koaza": 3}[r[3]]))
db.executemany("""INSERT INTO town(town_id,lg_code,origin,level,parent_id,name,oaza,chome_num,koaza,key_canon,key_fold,status,status_auto,
  status_claim_id,confidence,verification,abr_presence,numbering,abr_status_flg,abr_efct,abr_first_seen,abr_last_seen,parcel_count,
  post_code,pos_code,rep_lon,rep_lat,rep_src,flags,updated_in) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
db.commit()
print("town rows", db.execute("SELECT count(*) FROM town").fetchone()[0], round(time.time() - t0, 1), "s")
print(db.execute("SELECT origin, level, status, count(*) FROM town GROUP BY 1,2,3 ORDER BY 4 DESC").fetchall())

# canon / fold collisions (distinct live entities, same muni, oaza/chome/label level)
canon_coll = db.execute("""SELECT count(*) FROM (SELECT lg_code, key_canon FROM town WHERE level IN ('oaza','chome','label')
   GROUP BY 1,2 HAVING count(*) > 1)""").fetchone()[0]
fold_coll = db.execute("""SELECT count(*) FROM (SELECT lg_code, key_fold FROM town WHERE level IN ('oaza','chome','label')
   GROUP BY 1,2 HAVING count(DISTINCT key_canon) > 1)""").fetchone()[0]
print("canon collisions (oaza/chome/label)", canon_coll, "fold-only collisions", fold_coll)
db.commit()
db.execute("VACUUM")
print("national DB size MB", round(os.path.getsize(DB) / 1e6, 1))
db.close()
