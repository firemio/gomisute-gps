"""全国 DB (build_check.py の出力) に金沢 10 ケースの調査レコードを入れ、解決アルゴリズムを Python で再現して結果を出す"""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keys import keys

HERE = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(HERE, "addrnorm_check.sqlite"))
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")
db.execute("PRAGMA defer_foreign_keys = ON")
LG, VER, RUN = "172014", "2026.10.17-check", "research:check"
T = lambda s: f"{LG}-{s}"

def ins(table, **kw):
    db.execute(f"INSERT OR REPLACE INTO {table}({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))

SRC = {
 "kz:kukaku_list": ("official_table", "金沢市 土地区画整理事業一覧 (公告日)", "official", None),
 "kz:kyuchomei": ("official_table", "金沢市 旧町名一覧", "official", None),
 "kz:chronology": ("chronology", "金沢市立図書館 市史年表", "secondary", None),
 "wp:article": ("wikipedia", "Wikipedia 町名記事 (告示番号つき)", "secondary", None),
 "wp:yanagimachi_stub": ("wikipedia", "Wikipedia 柳町 (金沢市) stub", "tertiary", None),
 "wp:navbox": ("wikipedia", "Template:金沢市の町名 (2021-11-01)", "tertiary", None),
 "jp:ken_all": ("postal", "日本郵便 郵便番号", "official", None),
 "geolonia:kanazawa": ("derived_dataset", "geolonia japanese-addresses 金沢市", "dataset", None),
 "census:17201": ("census", "e-Stat 小地域境界 2000-2020", "dataset", None),
 "kz:gomi_table": ("municipal_service_table", "金沢市 ごみ収集区域表", "official", "kz:legacy_town_list"),
 "kz:school_table": ("municipal_service_table", "金沢市 校区表", "official", "kz:legacy_town_list"),
}
for k in ["181", "171", "115", "147", "114", "081", "052", "176"]:
    SRC[f"kz:kanchizu:{k}"] = ("official_map", f"換地図 {k}", "primary", None)
for sid, (kind, title, rel, grp) in SRC.items():
    ins("source", source_id=sid, kind=kind, title=title, retrieved_at="2026-10-16", reliability=rel, indep_group=grp)

def claim(cid, aspect, conf, ver, ev=(), conf_before=None, oq=None, basis="research"):
    ins("claim", claim_id=cid, aspect=aspect, basis=basis, confidence=conf, verification=ver, conf_before=conf_before,
        open_questions=oq, reviewed_by="claude:adversarial:2026-10-16" if ver in ("confirmed", "corrected", "insufficient") else None,
        record_ref="research/172014/check.json", run_id=RUN)
    for i, e in enumerate(ev, 1):
        src, stance, strength, obs, finding = (list(e) + [None] * 5)[:5]
        ins("evidence", claim_id=cid, seq=i, source_id=src, stance=stance, strength=strength or "normal", observed_on=obs,
            finding=finding or "(要約)")
    return cid

def town(tid, level, name, status, cid, conf, ver, origin="research", parent=None, **kw):
    assert db.execute("SELECT 1 FROM town WHERE town_id=?", (tid,)).fetchone() is None, tid
    c, f, _ = keys(name)
    oaza = kw.pop("oaza", name)
    row = dict(town_id=tid, lg_code=LG, origin=origin, level=level, parent_id=parent, name=name, oaza=oaza, key_canon=c, key_fold=f,
               status=status, status_auto=kw.pop("status_auto", "uncertain"), status_claim_id=cid, confidence=conf, verification=ver,
               abr_presence=kw.pop("abr_presence", "absent"), updated_in=VER)
    row.update(kw)
    ins("town", **row)

def override(tid, cid, conf, ver, **kw):
    sets = dict(status_claim_id=cid, confidence=conf, verification=ver, updated_in=VER, **kw)
    db.execute(f"UPDATE town SET {','.join(k + '=?' for k in sets)} WHERE town_id=?", list(sets.values()) + [tid])

def name(tid, surface, kind, cid, used_from=None, used_to=None):
    c, f, _ = keys(surface)
    ins("town_name", town_id=tid, surface=surface, lg_code=LG, kind=kind, key_canon=c, key_fold=f, used_from=used_from, used_to=used_to, claim_id=cid)

def event(eid, kind, label, prec, basis, text, cid, lo=None, hi=None, best=None, legal=None, ann=None, proj=None, merged=None, stage=None):
    ins("change_event", event_id=eid, lg_code=LG, kind=kind, label=label, project_ref=proj, stage_label=stage, legal_ref=legal,
        announce_date=ann, eff_lo=lo, eff_hi=hi, eff_best=best, eff_prec=prec, eff_basis=basis, eff_text=text, merged_into=merged, claim_id=cid)

def day_event(eid, kind, label, d, conf, ver, basis="koukoku_plus_1", **kw):
    cid = claim("r:" + eid, "event", conf, ver, [("kz:kukaku_list", "supports")])
    event(eid, kind, label, "day", basis, d, cid, lo=d, hi=d, best=d, **kw)

def tr(eid, frm, to, portion, conf, ver, sf=None, st=None, basis=None, noise=0, detail=None):
    cid = claim(f"r:tr:{eid}:{frm}>{to}", "transfer", conf, ver, [("wp:article", "supports")])
    ins("event_transfer", event_id=eid, from_id=T(frm), to_id=T(to), portion=portion, koaza_detail=detail, share_of_from=sf,
        share_of_to=st, share_basis=basis, is_noise=noise, claim_id=cid)

def census(tid, year, code, sname, rel, share=None):
    ins("town_census", town_id=tid, year=year, key_code=code, s_name=sname, relation=rel, share=share, claim_id="c:census:17201")

claim("c:census:17201", "census", 0.9, "machine", basis="census_diff")

# ===== 1. 木ノ新保7番丁
E181 = "ev:172014:2006-08-12:kz181"
day_event(E181, "kukaku_seiri", "金沢駅北土地区画整理 換地処分", "2006-08-12", 0.9, "confirmed", legal="石川県告示第432号", ann="2006-08-11",
          proj="kz-kukaku-181 金沢駅北 (市施行 11.92ha)")
c = claim("r:172014-X00001:status", "status", 0.85, "confirmed",
          [("kz:kukaku_list", "supports"), ("kz:kanchizu:181", "supports", "strong", "2006-08-12", "七番丁の帯ラベルなし"),
           ("census:17201", "context", "strong", "2005-10-01", "2005年に 56,144㎡"), ("jp:ken_all", "supports", "weak")])
town(T("X00001"), "oaza", "木ノ新保七番丁", "abolished", c, 0.85, "confirmed", from_hi="2000-10-01", from_prec="unknown",
     to_lo="2006-08-12", to_hi="2006-08-12", to_best="2006-08-12", to_prec="day", end_event_id=E181, bulk_event_id=E181)
name(T("X00001"), "木の新保七番丁", "census", "c:census:17201", "2000", "2005")
name(T("X00001"), "木ノ新保7番丁", "doc_label", claim("r:172014-X00001:name:gomi", "name", 0.9, "machine", [("kz:gomi_table", "context")]))
cc = claim("c:census:17201:2005-2010:X00001", "event", 0.7, "machine", basis="census_diff")
event("ev:172014:census:2005-2010:木の新保七番丁", "census_observed", "国勢調査 2005→2010 で消滅", "range", "census_window", "2005-10-02〜2010-10-01",
      cc, lo="2005-10-02", hi="2010-10-01", merged=E181)
tr(E181, "X00001", "0157000", "part", 0.85, "confirmed", 0.636, 0.694, "census_area:2005->2010")
tr(E181, "X00001", "0451000", "part", 0.85, "confirmed", 0.357, 0.215, "census_area:2005->2010")
tr(E181, "X00001", "0247000", "part", 0.5, "machine", 0.007, None, "census_area:2005->2010", noise=1)
census(T("X00001"), 2000, "172010640", "木の新保七番丁", "equal"); census(T("X00001"), 2005, "172010640", "木の新保七番丁", "equal")

# ===== 6. 西堀川町 (同じ E181)
c = claim("r:172014-X00002:status", "status", 0.6, "confirmed",
          [("kz:kanchizu:181", "supports", "strong", "2006-08-12", "事業区域外に西堀川町の帯"), ("wp:navbox", "supports", "weak", "2021-11-01", "※"),
           ("jp:ken_all", "context", "weak", None, "※の町は郵便番号なし")])
town(T("X00002"), "oaza", "西堀川町", "remnant", c, 0.6, "confirmed", flags='["not_in_abr_but_living","census_hidden"]',
     rep_lon=136.652588, rep_lat=36.582815, rep_src="geolonia", rep_conf=0.4)
tr(E181, "X00002", "0451000", "part", 0.8, "confirmed", 0.697, None, "census_area:2005->2010")
census(T("X00002"), 2005, "172015080", "西堀川町", "equal"); census(T("X00002"), 2010, "17201516001", "北安江１丁目", "hosts_remnant", 0.303)

# ===== 2. 柳町
Ea, Eb, Ec = "ev:172014:1965-09-01:jukyo-hosai", "ev:172014:1992-06-10:kz172", "ev:172014:1993-01-09:kz171"
day_event(Ea, "jukyo_hyoji", "芳斎校下 住居表示", "1965-09-01", 0.8, "confirmed", basis="official_list")
day_event(Eb, "chiku_henko", "昭和町地区 町区域変更 (換地処分公告 1998-08-31 より前)", "1992-06-10", 0.7, "researched", basis="legal_ref_secondary", legal="石川県告示第322号")
day_event(Ec, "kukaku_seiri", "金沢駅南土地区画整理 換地処分", "1993-01-09", 0.8, "confirmed", legal="石川県告示第4号", ann="1993-01-08")
c = claim("r:172014-X00006:status", "status", 0.5, "insufficient",
          [("kz:kyuchomei", "supports"), ("wp:yanagimachi_stub", "contradicts", "weak", "2023", "出典なしで現行地名とする"),
           ("kz:kanchizu:171", "context", "normal", "1993-01-09", "隣接ラベルに柳町なし"), ("kz:gomi_table", "context", "weak"), ("kz:school_table", "context", "weak")],
          oq="石川県告示第4号の本文 / 2006年換地図の隣接ラベルで残存の有無が決まる")
town(T("X00006"), "oaza", "柳町", "uncertain", c, 0.5, "insufficient", to_lo="1993-01-09", to_prec="unknown",
     validity_note="1993-01-09 の部分移転より後の不明な日。残存も否定できない")
tr(Ea, "X00006", "0455001", "part", 0.8, "confirmed"); tr(Ea, "X00006", "0247000", "part", 0.8, "confirmed")
tr(Eb, "X00006", "0247000", "part", 0.6, "researched")
# 同じ 1992/1993 事象に柳町→昭和町 が2本 → PK は (event, from, to) なので別事象で OK
tr(Ec, "X00006", "0247000", "part", 0.6, "researched")

# ===== 3. 寺地町
E79, E82, E90 = "ev:172014:1979-06-30:kz115", "ev:172014:1982-06-01:kz129", "ev:172014:1990-11-07:kz147"
day_event(E79, "kukaku_seiri", "寺地第一 換地処分", "1979-06-30", 0.85, "confirmed", ann="1979-06-29")
day_event(E82, "kukaku_seiri", "有松第一 換地処分", "1982-06-01", 0.8, "researched", ann="1982-05-31")
day_event(E90, "kukaku_seiri", "久安第二 換地処分", "1990-11-07", 0.85, "confirmed", ann="1990-11-06")
c = claim("r:172014-X00003:status", "status", 0.45, "corrected",
          [("kz:kanchizu:115", "supports", "strong", "1979-06-30", "ニノ部・ハノ部が事業区域外に残る"), ("kz:kanchizu:147", "supports", "normal", "1990-11-07", "久安4丁目の東端が帯まで延びる")],
          conf_before=0.5, oq="1990 は地図の隣接からの推定。ハの帯の行方は未確認")
town(T("X00003"), "oaza", "寺地町", "abolished", c, 0.45, "corrected", to_lo="1979-07-01", to_hi="2000-10-01", to_best="1990-11-07",
     to_prec="range", validity_note="1990年頃 (地図の隣接から推定)", bulk_event_id=E79, end_event_id=E90)
c4 = claim("r:172014-X00004:status", "status", 0.45, "corrected", [("kz:kanchizu:115", "supports"), ("kz:kanchizu:147", "supports")])
town(T("X00004"), "koaza", "寺地町ニ", "abolished", c4, 0.45, "corrected", parent=T("X00003"), oaza="寺地町", koaza="ニ",
     to_lo="1979-07-01", to_hi="2000-10-01", to_best="1990-11-07", to_prec="range", end_event_id=E90)
name(T("X00004"), "寺地町ニノ部", "map_label", c4)
c5 = claim("r:172014-X00005:status", "status", 0.3, "researched", [("kz:kanchizu:115", "supports")], oq="有松第一で吸収されたかは未確認")
town(T("X00005"), "koaza", "寺地町ハ", "abolished", c5, 0.3, "researched", parent=T("X00003"), oaza="寺地町", koaza="ハ",
     to_lo="1979-07-01", to_hi="2000-10-01", to_best="1982-06-01", to_prec="range", end_event_id=E82)
name(T("X00005"), "寺地町ハノ部", "map_label", c5)
for to in ["0320001", "0320002", "0432001", "0432002"]: tr(E79, "X00003", to, "part", 0.6, "confirmed")
tr(E90, "X00004", "0412004", "whole", 0.45, "corrected", detail="寺地町ニノ部")
tr(E82, "X00005", "0018003", "whole", 0.3, "researched", detail="寺地町ハノ部")

# ===== 4. 窪町 (P0164 は ABR 由来の合成親。調査で status を裏づける)
Ek1, Ek2 = "ev:172014:x:kubo-chome", "ev:172014:1979-03-03:kz114"
ck = claim("r:" + Ek1, "event", 0.5, "researched", [("kz:chronology", "supports", "normal", "1974-09-02", "窪5丁目の呼称")])
event(Ek1, "unknown", "窪1・2・3・6丁目の設定 (仕組み不明)", "decade", "usage_bounds", "1970年代", ck, lo="1970-01-01", hi="1979-03-03")
day_event(Ek2, "kukaku_seiri", "窪第一 換地処分", "1979-03-03", 0.8, "confirmed", ann="1979-03-02")
c = claim("r:172014-P0164:status", "status", 0.6, "corrected",
          [(f"abr:mt_town_all:2026-10-16", "supports", "strong", "2026-10-16", "小字9行、うち7行に筆31"), ("wp:navbox", "supports", "weak")],
          conf_before=0.6, oq="窪1・2・3・6丁目の設定時期と仕組み")
override(T("P0164"), c, 0.6, "corrected", status="remnant", from_lo="1935-12-16", from_hi="1935-12-16", from_best="1935-12-16", from_prec="day",
         rep_lon=136.652698, rep_lat=36.514226, rep_src="geolonia", rep_conf=0.3)
for to in ["0163001", "0163002", "0163003", "0163006"]: tr(Ek1, "P0164", to, "part", 0.5, "researched")
for to in ["0163004", "0163005", "0163007", "0432003", "0412001"]: tr(Ek2, "P0164", to, "part", 0.6, "researched")

# ===== 5. 入江町ヘ (P0607 + ABR 0607101 を調査で上書き) + 1972 の過渡的な字 イ・ロ・ハ・ホ
Ei1, Ei2, Ei3, Ei4 = "ev:172014:1968-04-01:kz052", "ev:172014:1972-01-08:kz081", "ev:172014:x:irie-chome", "ev:172014:x:irie-racecourse"
day_event(Ei1, "kukaku_seiri", "神田第二 換地処分", "1968-04-01", 0.8, "confirmed", ann="1968-03-31")
day_event(Ei2, "kukaku_seiri", "入江第一 換地処分 (町名は字区画イ〜ホで存続)", "1972-01-08", 0.8, "confirmed", ann="1972-01-07")
c3 = claim("r:" + Ei3, "event", 0.5, "researched", [("census:17201", "supports", "strong", "2000-10-01", "2000年は入江1〜3丁目")])
event(Ei3, "unknown", "入江2・3丁目の設定", "range", "usage_bounds", "1972-12-05〜2000-10-01", c3, lo="1972-12-05", hi="2000-10-01")
c4e = claim("r:" + Ei4, "event", 0.4, "researched", [("kz:chronology", "supports", "normal", "1972-12-04", "金沢競馬場 (入江町)")])
event(Ei4, "unknown", "競馬場跡の新神田1丁目編入", "range", "usage_bounds", "1972-12-05〜1977-12-31", c4e, lo="1972-12-05", hi="1977-12-31")
c = claim("r:172014-P0607:status", "status", 0.5, "corrected",
          [("kz:chronology", "context", "normal", "1972-12-04", "競馬場を入江町と呼ぶ"), ("census:17201", "supports", "strong", "2000-10-01"),
           ("wp:navbox", "context", "weak", "2021-11-01", "※なし")], conf_before=0.45)
override(T("P0607"), c, 0.5, "corrected", status="abolished", to_lo="1972-12-05", to_hi="2000-10-01", to_prec="range",
         from_lo="1935-12-16", from_hi="1935-12-16", from_best="1935-12-16", from_prec="day")
ch = claim("r:172014-0607101:status", "status", 0.5, "corrected",
           [(f"abr:mt_town_all:2026-10-16", "contradicts", "normal", "2026-10-16", "ABR に行がある"),
            (f"abr:mt_town_all:2026-10-16", "supports", "normal", "2026-10-16", "筆0、06xxxxx 旧ブロック (木ノ新保四番丁・堀川角場町と同居)")],
           oq="ヘは 1972 年の字区画の一つかもしれない")
override(T("0607101"), ch, 0.5, "corrected", status="abolished", to_lo="1972-12-05", to_hi="2000-10-01", to_prec="range")
for i, kz in enumerate(["イ", "ロ", "ハ", "ホ"]):
    cz = claim(f"r:172014-X{7+i:05d}:status", "status", 0.5, "researched", [("kz:kanchizu:081", "supports", "strong", "1972-01-08")])
    town(T(f"X{7+i:05d}"), "koaza", f"入江町{kz}", "abolished", cz, 0.5, "researched", parent=T("P0607"), oaza="入江町", koaza=kz, transitional=1,
         from_lo="1972-01-08", from_hi="1972-01-08", from_best="1972-01-08", from_prec="day", start_event_id=Ei2,
         to_lo="1972-12-05", to_hi="2000-10-01", to_prec="range", end_event_id=Ei3)
    if kz in "ロホ": name(T(f"X{7+i:05d}"), f"入江町{kz}の部", "map_label", cz)
tr(Ei1, "P0607", "0046001", "part", 0.6, "confirmed")
tr(Ei2, "P0607", "0460001", "part", 0.5, "confirmed"); tr(Ei2, "P0607", "0460002", "part", 0.5, "confirmed")
tr(Ei3, "P0607", "0046002", "part", 0.5, "researched"); tr(Ei3, "P0607", "0046003", "part", 0.5, "researched")
tr(Ei4, "P0607", "0250001", "part", 0.4, "researched")
db.execute("UPDATE town SET end_event_id=? WHERE town_id=?", (None, T("P0607")))

# ===== 7. 湖南第2区画整理地
K1, K2 = "ev:172014:1998-12-05:kz176-1", "ev:172014:2002-02-06:kz176-2"
day_event(K1, "kukaku_seiri", "湖南第二 換地処分", "1998-12-05", 0.85, "confirmed", ann="1998-12-04", stage="第一工区", proj="kz-kukaku-176 湖南第二 (個人施行 40.35ha)")
day_event(K2, "kukaku_seiri", "湖南第二 換地処分", "2002-02-06", 0.85, "confirmed", ann="2002-02-05", stage="第二工区", proj="kz-kukaku-176 湖南第二 (個人施行 40.35ha)")
c = claim("r:172014-X00011:status", "status", 0.85, "confirmed", [("kz:kanchizu:176", "supports", "strong", "2002-02-06", "H14.2.6現在")])
town(T("X00011"), "label", "湖南第2区画整理地", "abolished", c, 0.85, "confirmed", from_lo="1993-01-01", from_prec="unknown",
     to_lo="2002-02-06", to_hi="2002-02-06", to_best="2002-02-06", to_prec="day", end_event_id=K2)
name(T("X00011"), "湖南第二土地区画整理事業", "project", c)
tr(K1, "X00011", "0476001", "part", 0.6, "researched"); tr(K1, "X00011", "0476002", "part", 0.6, "researched")
tr(K2, "X00011", "0476003", "remainder", 0.85, "confirmed"); tr(K2, "X00011", "0476004", "remainder", 0.85, "confirmed")
tr(K2, "0145000", "0476003", "part", 0.7, "machine", None, 0.88, "census_area:2000->2005")
tr(K2, "0082000", "0476003", "part", 0.7, "machine", None, 0.12, "census_area:2000->2005")
tr(K2, "0145000", "0476004", "part", 0.7, "machine", None, 1.0, "census_area:2000->2005")

# ===== 8. 観音町
ER = "ev:172014:2019:kannon-revival"
cr = claim("r:" + ER, "event", 0.6, "researched", [("wp:article", "supports")], oq="市の旧町名復活の資料で月日と由来町を確認")
event(ER, "revival", "旧町名復活 (観音町1〜3丁目)", "year", "secondary", "2019", cr, lo="2019-01-01", hi="2019-12-31")
event("ev:172014:census:2015-2020:観音町", "census_observed", "国勢調査 2015→2020 で消滅", "range", "census_window", "2015-10-02〜2020-10-01",
      claim("c:census:17201:2015-2020:kannon", "event", 0.7, "machine", basis="census_diff"), lo="2015-10-02", hi="2020-10-01", merged=ER)
c = claim("r:172014-X00012:status", "status", 0.6, "researched", [("census:17201", "supports", "strong", "2015-10-01", "2,530㎡")])
town(T("X00012"), "oaza", "観音町", "abolished", c, 0.6, "researched", to_lo="2015-10-02", to_hi="2019-12-31", to_prec="year",
     end_event_id=ER, flags='["name_reused"]', validity_note="2019 (旧町名復活。月日と告示は未確認)")
override(T("P0138"), cr, 0.6, "researched", from_lo="2019-01-01", from_hi="2019-12-31", from_prec="year", start_event_id=ER,
         revives_id=T("X00012"), flags='["name_reused"]')
for m in ["0138001", "0138002", "0138003"]:
    db.execute("UPDATE town SET from_lo='2019-01-01', from_hi='2019-12-31', from_prec='year', start_event_id=? WHERE town_id=?", (ER, T(m)))
tr(ER, "X00012", "0138003", "part", 0.7, "machine", 0.987, None, "census_area:2015->2020")
tr(ER, "X00012", "0198000", "part", 0.5, "machine", 0.013, None, "census_area:2015->2020", noise=1)
for y in (2000, 2005, 2010, 2015): census(T("X00012"), y, "172015700", "観音町", "equal")
census(T("0138003"), 2020, "17201570003", "観音町三丁目", "contains"); census(T("0138003"), 2020, "17201570000", "観音町三丁目", "contains")

# ===== 9. 神田町 / 神田町5丁目
G1, G2, G3 = "ev:172014:1965-11-20:kanda1", "ev:172014:1968-04-01:kz052", "ev:172014:x:kanda5-rename"
day_event(G1, "kukaku_seiri", "神田第一 換地処分", "1965-11-20", 0.7, "researched", ann="1965-11-19")
c3 = claim("r:" + G3, "event", 0.4, "researched", [("kz:kanchizu:052", "supports", "strong", "1967-01-12", "神田町五丁目の表記")])
event(G3, "chomei_chiban_henko", "神田町5丁目 → 神田1・2丁目 (推定)", "range", "usage_bounds", "1967-01-13〜2000-10-01", c3, lo="1967-01-13", hi="2000-10-01")
c = claim("r:172014-X00013:status", "status", 0.5, "confirmed", [("wp:navbox", "supports", "weak", "2021-11-01", "※"),
          ("kz:kanchizu:052", "supports", "strong", "1967-01-12", "ハ/ニ/ホの部が残る")], oq="1935年に大字御供田を改称した可能性")
town(T("X00013"), "oaza", "神田町", "remnant", c, 0.5, "confirmed", bulk_event_id=G2, flags='["not_in_abr_but_living"]')
c = claim("r:172014-X00014:status", "status", 0.5, "researched", [("kz:chronology", "supports", "normal", "1965-09-25", "新町名 神田町5丁目"),
          ("kz:kanchizu:052", "supports", "strong", "1967-01-12")])
town(T("X00014"), "chome", "神田町5丁目", "abolished", c, 0.5, "researched", parent=T("X00013"), oaza="神田町", chome_num=5, transitional=1,
     from_lo="1965-09-25", from_hi="1967-01-12", from_best="1965-11-20", from_prec="range", start_event_id=G1,
     to_lo="1967-01-13", to_hi="2000-10-01", to_prec="range", end_event_id=G3)
name(T("X00014"), "神田町五丁目", "map_label", c)
for i in range(1, 6): tr(G2, "X00013", f"025000{i}", "part", 0.6, "confirmed")
tr(G3, "X00014", "0135001", "unknown", 0.4, "researched"); tr(G3, "X00014", "0135002", "unknown", 0.4, "researched")

db.commit()
print("FK violations:", db.execute("PRAGMA foreign_key_check").fetchall()[:5])

# ===================================================================== resolver (Worker の TS 実装の写し)
def row(tid): return db.execute("SELECT * FROM town WHERE town_id=?", (tid,)).fetchone()
ALIVE = ("current", "remnant")

def valid_at(t, d):
    if t["from_lo"] is None and t["from_hi"] is None: s = "yes"
    elif t["from_hi"] and d >= t["from_hi"]: s = "yes"
    elif t["from_lo"] and d < t["from_lo"]: s = "no"
    else: s = "unknown"
    if t["status"] in ALIVE: e = "no"
    elif t["to_hi"] and d >= t["to_hi"]: e = "yes"
    elif t["to_lo"] and d < t["to_lo"]: e = "no"
    else: e = "unknown"
    if s == "yes" and e == "no": return "yes"
    if s == "no" or e == "yes": return "no"
    return "unknown"

def lookup(lg, col, key):
    ids = [r[0] for r in db.execute(f"SELECT town_id FROM town WHERE lg_code=? AND {col}=? AND {col}<>''", (lg, key))]
    ids += [r[0] for r in db.execute(f"SELECT town_id FROM town_name WHERE lg_code=? AND {col}=?", (lg, key))]
    out = []
    for i in ids:
        r = row(i)
        while r["redirect_to"]: r = row(r["redirect_to"])
        if r["town_id"] not in out: out.append(r["town_id"])
    return out

def family(tid):
    fam, todo = [tid], [tid]
    while todo:
        x = todo.pop()
        for (c,) in db.execute("SELECT town_id FROM town WHERE parent_id=?", (x,)):
            fam.append(c); todo.append(c)
    return fam

def transfers_from(ids):
    q = f"""SELECT t.*, e.eff_lo, e.eff_hi, e.eff_best, e.label, c.confidence conf, c.verification ver
            FROM event_transfer t JOIN change_event e ON e.event_id=t.event_id AND e.merged_into IS NULL
            JOIN claim c ON c.claim_id=t.claim_id WHERE t.from_id IN ({','.join('?' * len(ids))}) AND t.is_noise=0"""
    return db.execute(q, ids).fetchall()

def walk(root):
    fam = set(family(root)); out = []
    def follow(x, path, depth):
        t = row(x)
        if t["status"] in ALIVE or t["status"] == "uncertain" or depth >= 6:
            out.append((x, path)); return
        nxt = [h for h in transfers_from(family(x)) if h["to_id"] not in family(x) and (h["eff_hi"] is None or path[-1]["eff_lo"] is None or h["eff_hi"] >= path[-1]["eff_lo"])]
        if not nxt: out.append((x, path)); return
        for h in nxt: follow(h["to_id"], path + [h], depth + 1)
    for h in transfers_from(list(fam)):
        if h["to_id"] in fam: continue
        follow(h["to_id"], [h], 1)
    return out

def resolve(text, as_of=None, mode="unknown"):
    as_of = as_of or "2026-10-17"
    c, f, q = keys(text)
    C, F = lookup(LG, "key_canon", c), lookup(LG, "key_fold", f)
    warn, other = [], []
    if mode == "authored":
        keep = [x for x in C if valid_at(row(x), as_of) != "no"]
        if keep and len(keep) < len(C): other = [x for x in C if x not in keep]; C = keep; warn.append("NAME_REUSED")
    if len(C) >= 2:
        ms = "ambiguous"; warn.append("NAME_REUSED" if len({(row(x)["from_lo"], row(x)["to_lo"]) for x in C}) > 1 else "CANON_COLLISION")
    elif len(C) == 1:
        e = C[0]; others = [x for x in F if x != e and x not in other]
        is_group = row(e)["origin"] == "abr_parent" and "phantom" not in json.loads(row(e)["flags"])
        ms = "unique" if not others else ("preferred" if not is_group else "ambiguous")
        if others: warn.append("FOLD_COLLISION")
    else:
        ms = "unique" if len(F) == 1 else ("ambiguous" if F else "none")
        if len(F) == 1: warn.append("MATCH_FOLDED")
    res = dict(input=text, key=c, quals=q, match_status=ms, auto_pick=ms in ("unique", "preferred"), warnings=warn,
               candidates=[(x, row(x)["name"], row(x)["status"], valid_at(row(x), as_of)) for x in (C or F)])
    if not res["auto_pick"]:
        return res
    R = row((C or F)[0])
    targets = []
    alive = R["status"] in ALIVE
    is_group = R["origin"] == "abr_parent" and "phantom" not in json.loads(R["flags"])
    if is_group and alive:
        for (m, nm) in db.execute("SELECT town_id, name FROM town WHERE parent_id=? AND level='chome' AND status IN ('current','remnant')", (R["town_id"],)):
            targets.append(dict(town=nm, role="group_member", primary=True))
        res["kind"] = "current_group"; res["targets"] = targets; return res
    if alive or R["status"] == "uncertain":
        targets.append(dict(town=R["name"], role="self", primary=alive))
    paths = walk(R["town_id"]); inherited = None
    P = row(R["parent_id"]) if R["parent_id"] else None
    if not paths and not alive and P is not None and P["status"] in ALIVE:
        targets.append(dict(town=P["name"], role="parent", primary=True)); res["warnings"].append("PARENT_ALIVE")
        res.update(kind="koaza_uncertain", t_star=None, targets=targets); return res
    if not paths and not alive and R["parent_id"]:
        inherited = R["parent_id"]; paths = walk(inherited); res["warnings"].append("INHERITED_FROM_PARENT")
    if alive:
        tstar = as_of if mode == "authored" else None
    elif mode == "authored" and valid_at(R, as_of) != "no":
        tstar = as_of
    else:
        tstar = R["to_lo"] or (as_of if mode == "authored" else None)
    base = row(inherited) if inherited else R
    ends = {base["end_event_id"]} | ({R["end_event_id"]} if R["end_event_id"] else set())
    bulk = base["bulk_event_id"]
    bulk_hi = db.execute("SELECT eff_hi FROM change_event WHERE event_id=?", (bulk,)).fetchone()[0] if bulk else None
    evs = [p[0] for _, p in paths]
    L = max(evs, key=lambda h: h["eff_hi"] or "") if evs else None
    latest_ids = {h["event_id"] for h in evs if L and (h["eff_hi"] or "") >= (L["eff_lo"] or "")}
    for x, p in paths:
        h0 = p[0]
        if h0["event_id"] in ends: stage = "final"
        elif not (ends - {None}) and h0["event_id"] in latest_ids: stage = "latest"
        elif h0["event_id"] == bulk: stage = "bulk"
        elif bulk_hi and h0["eff_lo"] and h0["eff_lo"] > bulk_hi: stage = "intermediate"
        else: stage = "earlier"
        prim = tstar is not None and (h0["eff_hi"] is None or h0["eff_hi"] >= tstar)
        share = None
        if h0["from_id"] == R["town_id"]:
            share = 1.0
            for h in p: share = None if (share is None or h["share_of_from"] is None) else share * h["share_of_from"]
        targets.append(dict(town=row(x)["name"], role="successor" if not alive else "context", primary=prim and not alive or (alive and prim),
                            stage=stage, via=[h["event_id"].split(":")[-1] for h in p], left=(h0["eff_lo"], h0["eff_hi"]),
                            via_child=row(h0["from_id"])["name"] if h0["from_id"] not in (R["town_id"], inherited) else None,
                            share=share, conf=min(h["conf"] for h in p)))
    merged = {}
    RANK = {"final": 5, "latest": 4, "intermediate": 3, "bulk": 2, "earlier": 1}
    for t in targets:
        k = (t["town"], t["role"])
        if k in merged and "stage" in t:
            m = merged[k]; m["via"] = m["via"] + t["via"]; m["primary"] = m["primary"] or t["primary"]
            if RANK[t["stage"]] > RANK[m["stage"]]: m["stage"], m["left"] = t["stage"], t["left"]
            m["conf"] = max(m["conf"], t["conf"])
        else: merged[k] = dict(t)
    targets = list(merged.values())
    if R["status"] == "abolished" and not any(t.get("primary") for t in targets):
        for t in targets:
            if t.get("stage") in ("final", "latest"): t["primary"] = True
        res["warnings"].append("NO_DATED_PRIMARY")
    res.update(kind=("remnant" if R["status"] == "remnant" else "current") if alive else ("provisional" if R["level"] == "label" else ("transitional" if R["transitional"] else ("uncertain" if R["status"] == "uncertain" else "old_name"))),
               t_star=tstar, targets=targets)
    return res

CASES = [("寺地町", "1981-04-01", "authored"), ("観音町", "2016-04-01", "authored"), ("木ノ新保7番丁", "2025-04-01", "authored"), ("木ノ新保7番丁", None, "unknown"), ("木の新保七番丁", None, "unknown"), ("柳町", None, "unknown"), ("寺地町", None, "unknown"),
         ("寺地町ニ", None, "unknown"), ("寺地町", "1985-04-01", "authored"), ("窪町", None, "unknown"), ("窪町ロ", None, "unknown"), ("窪町ニ", None, "unknown"),
         ("入江町ヘ", None, "unknown"), ("入江町", None, "unknown"), ("西堀川町", None, "unknown"), ("西堀川町", "2000-04-01", "authored"),
         ("湖南第2区画整理地", None, "unknown"), ("近岡町土地区画整理地", None, "unknown"), ("観音町", None, "unknown"), ("観音町", "2018-04-01", "authored"),
         ("観音町", "2025-04-01", "authored"), ("観音町", "2019-06-01", "authored"), ("神田町5丁目", None, "unknown"), ("神田町五丁目", None, "unknown"),
         ("神田町", None, "unknown"), ("山の上町", None, "unknown"), ("山ノ上町", None, "unknown"), ("山ノ上町5丁目", None, "unknown"),
         ("山の上町5丁目", None, "unknown"), ("寺地町ニノ部", None, "unknown")]
for text, d, mode in CASES:
    r = resolve(text, d, mode)
    print(f"\n### {text} as_of={d} {mode}: {r['match_status']} auto={r['auto_pick']} kind={r.get('kind')} t*={r.get('t_star')} warn={r['warnings']} q={r['quals']}")
    print("   cand:", r["candidates"])
    for t in r.get("targets", []):
        print("   ", t)
db.execute("VACUUM")
print("\nDB size MB", round(os.path.getsize(os.path.join(HERE, "addrnorm_check.sqlite")) / 1e6, 1))
