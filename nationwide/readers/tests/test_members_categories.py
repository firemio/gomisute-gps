"""members.py (地区の文法・条件の正規形・address-normalize の差し替え) と categories.py (品目の辞書) (ネットにつながない)"""
import unittest

import _util  # noqa: F401
import categories, members


def one(text, **kw):
    ms = members.parse_cell(text, **kw)
    assert len(ms) == 1, ms
    return ms[0]


def conds(m):
    return [{k: v for k, v in c.items() if k != "text"} for c in m["conditions"]]


class TestMembers(unittest.TestCase):
    def test_names_verbatim_and_no_snapping(self):
        ms = members.parse_cell("岡・大字岡")
        self.assertEqual([m["extra"]["name_verbatim"] for m in ms], ["岡", "大字岡"])      # 「大字」を外さない (fold の段だけ)
        for m in ms:
            self.assertEqual((m["match"], m["machiaza_id"], m["geometry"]), ("unchecked", None, "unresolved"))
        self.assertEqual(ms[1]["extra"]["key_fold"], "岡")

    def test_chome_ranges(self):
        self.assertEqual(conds(one("幸町1～2丁目")), [{"kind": "range", "unit": "chome", "from": "1", "to": "2"}])
        self.assertEqual(conds(one("青柳1から8丁目")), [{"kind": "range", "unit": "chome", "from": "1", "to": "8"}])
        self.assertEqual(conds(one("江北１～５、７丁目")), [{"kind": "range", "unit": "chome", "from": "1", "to": "5"},
                                                         {"kind": "range", "unit": "chome", "from": "7", "to": "7"}])
        self.assertEqual(conds(one("1・3～5", town="赤堤")), [{"kind": "range", "unit": "chome", "from": "1", "to": "1"},
                                                            {"kind": "range", "unit": "chome", "from": "3", "to": "5"}])   # 世田谷
        self.assertEqual(one("全域", town="明石町")["conditions"], [])                                                       # 中央
        self.assertEqual(one("―", town="鶯谷町")["conditions"], [])                                                          # 渋谷

    def test_block_ranges(self):
        m = one("あけぼの町6-2，6-10～6-17")
        self.assertEqual(m["unit"], "block_range")
        self.assertEqual(conds(m), [{"kind": "range", "unit": "banchi", "from": "6-2", "to": "6-2"},
                                    {"kind": "range", "unit": "banchi", "from": "6-10", "to": "6-17"}])
        m = one("松が岡五丁目133-3～21,177～")
        self.assertEqual(conds(m)[-1], {"kind": "range", "unit": "banchi", "from": "177", "to": None})   # 終わりの無い範囲
        self.assertEqual(conds(one("あけぼの町5-13，5-25～5-末"))[-1]["to"], "end")
        m = one("膝折町2丁目1～6番・7番の一部（黒目川右岸）・8～21番")                                   # 朝霞
        kinds = [c["kind"] for c in m["conditions"]]
        self.assertEqual(kinds, ["range", "range", "partial", "range", "line_side", "range"])

    def test_conditions(self):
        self.assertEqual(one("あけぼの町（上記以外）")["unit"], "remainder")
        self.assertEqual(conds(one("粕壁(1〜4丁目を除く)")),
                         [{"kind": "except", "of": [{"kind": "range", "unit": "chome", "from": "1", "to": "4"}]}])
        self.assertEqual(conds(one("新方袋(飛地を除く)")), [{"kind": "enclave", "negate": True}])
        m = one("下牧（11班・12班以外）")
        self.assertEqual((m["unit"], conds(m)), ("community", [{"kind": "han", "values": ["11", "12"], "negate": True}]))
        self.assertEqual(conds(one("旭町1から3丁目（旭町団地を除く）"))[-1], {"kind": "facility", "name": "旭町団地", "negate": True})
        ls = conds(one("谷塚町（国道4号バイパスの東側・県道川口草加線の北側）"))
        self.assertEqual([(c["line"], c["side"]) for c in ls], [("国道4号バイパス", "東側"), ("県道川口草加線", "北側")])
        self.assertEqual(conds(one("千里浜町（大宮、白山、宇賀、金比羅、なぎさ、はまなす団地）"))[0]["kind"], "note")
        m = one("大字浜崎の一部（県道和光志木線北側）")         # 分け方は線の片側として書いてある (partial にしない)
        self.assertEqual((m["unit"], [c["kind"] for c in m["conditions"]]), ("line_side", ["line_side"]))
        self.assertEqual([c["kind"] for c in one("箪笥町（44番地の一部）")["conditions"]], ["partial"])
        a = members.parse_cell("1・2", town="駒沢")
        b = [{"name_verbatim": "駒沢2丁目", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": "1", "to": "2"}]}]
        self.assertTrue(members.same(a, b))                        # 「1・2」と「1～2」、名前に付いた丁目は同じに比べる
        self.assertEqual(one("自衛隊")["unit"], "facility")

    def test_katakana_ni_is_fold_only(self):        # 鶴ヶ島「脚折町ニ～六丁目」・小松「向本折第ニ」
        m = one("脚折町ニ～六丁目")
        self.assertEqual(m["match"], "fold")
        self.assertEqual(conds(m), [{"kind": "range", "unit": "chome", "from": "2", "to": "6"}])
        self.assertEqual(one("向本折第ニ")["match"], "fold")
        self.assertEqual(members.canon("向本折第ニ"), "向本折第ニ")                 # canon の段では寄せない
        self.assertEqual(members.canon("湖南第二"), "湖南第2")

    def test_title_and_compare_key(self):
        t = members.strip_district_title("令和8年度 大沢3丁目・深大寺全域・井口全域・野崎2・3丁目地区")
        ms = members.parse_cell(t)
        self.assertEqual([m["extra"]["name_verbatim"] for m in ms], ["大沢", "深大寺", "井口", "野崎"])
        a = members.parse_cell("青柳1から3丁目")
        b = [{"name_verbatim": "青柳", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": "1", "to": "3"}]}]
        self.assertTrue(members.same(a, b))                       # 言い回し (「から」と「～」) では食い違いにしない
        b[0]["conditions"][0]["to"] = "4"
        self.assertFalse(members.same(a, b))

    def test_stub_contract(self):
        res = members.NORMALIZER.resolve_label("112275", "大字岡")
        self.assertEqual(res["match_status"], "unchecked")
        self.assertEqual(members.to_member_match({"match_status": "unique", "auto_pick": True, "match": "canonical",
                                                  "town": {"machiaza_id": "0001001"}}), ("exact", "0001001"))
        self.assertEqual(members.to_member_match({"match_status": "unique", "match": "folded"}), ("fold", None))
        self.assertEqual(members.to_member_match({"match_status": "ambiguous"}), ("ambiguous", None))
        self.assertEqual(members.to_member_match({"match_status": "none"}), ("none", None))


class TestCategories(unittest.TestCase):
    def test_coarse(self):
        c = categories.coarse
        self.assertEqual(c("プラスチック資源\n燃やせないごみ"), ["non_burnable", "plastic"])
        self.assertEqual(c("空きびん・缶"), ["beverage_can", "glass_bottle"])
        self.assertEqual(c("スプレー缶等\n有害ごみ"), ["spray_can", "hazardous"])
        self.assertEqual(c("古紙・古着"), ["paper", "cloth"])
        self.assertEqual(c("資源（週1回）"), ["recyclable"])
        self.assertEqual(c("燃やすごみ"), ["burnable"])
        self.assertEqual(c("ガラス類・ペットボトル"), ["pet_bottle", "glass_bottle"])           # 東秩父
        self.assertEqual(c("年末年始の収集日は広報紙でお知らせします"), ["other"])              # 「広報紙」の「紙」は品目でない
        self.assertEqual(c("紙・布"), ["paper", "cloth"])

    def test_chome_range_written_twice(self):   # 三鷹「上連雀1丁目～5丁目」(丁目を 2 回書く) = 1～5丁目
        m = members.parse_cell("野崎1丁目・上連雀6丁目～9丁目")
        self.assertEqual([(x["unit"], x["extra"]["name_verbatim"], conds(x)) for x in m],
                         [("machiaza", "野崎", [{"kind": "range", "unit": "chome", "from": "1", "to": "1"}]),
                          ("machiaza", "上連雀", [{"kind": "range", "unit": "chome", "from": "6", "to": "9"}])])

    def test_same_groups_ranges(self):            # 同じ町の丁目を 1 つに書いても丁目ごとに書いても同じ (§3.9 の言い回しの違い)
        a = members.parse_cell("野崎4丁目・大沢1・2・4・5・6丁目")
        b = [{"name_verbatim": "野崎", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": "4", "to": "4"}]}] + \
            [{"name_verbatim": "大沢", "unit": "machiaza", "conditions": [{"kind": "range", "unit": "chome", "from": str(n), "to": str(n)}]}
             for n in (1, 2, 4, 5, 6)]
        self.assertTrue(members.same(a, b))
        self.assertFalse(members.same(a, b[:-1]))
        self.assertFalse(members.same(members.parse_cell("新川1・4・5丁目"),
                                      [{"name_verbatim": "新川", "unit": "machiaza", "conditions": [{"kind": "partial", "split_known": False}]}]))

    def test_named_in(self):                     # 文・見出しが名指しする品目 (止める向き・値を当てる向きの両方で使う)
        cats = {"c1": {"coarse": ["burnable"], "official_label": "燃やすごみ"}, "c2": {"coarse": ["recyclable"], "official_label": "資源"},
                "c3": {"coarse": ["non_burnable"], "official_label": "燃やさないごみ"}}
        self.assertEqual(categories.named_in("燃やすごみは祝日も収集します", cats), ({"c1"}, set()))
        self.assertEqual(categories.named_in("ごみ・資源収集に関する内容は、12月のあだち広報でお知らせします", cats, generic_labels=False),
                         (set(), {"ごみ", "資源"}))
        self.assertEqual(categories.named_in("資源は祝日も収集します", cats), ({"c2"}, set()))            # 公式の名前そのもの
        self.assertEqual(categories.named_in("資源は祝日も収集します", cats, generic_labels=False), (set(), {"資源"}))
        self.assertEqual(categories.named_in("年末年始の収集日は広報紙でお知らせします", cats), (set(), set()))

    def test_official_label_verbatim(self):
        self.assertEqual(categories.official_label("燃えるごみ\n（メタン発酵処理）"), "燃えるごみ （メタン発酵処理）")
        self.assertEqual(categories.official_label("資源　ごみ"), "資源　ごみ")             # 全角の空白はそのまま

    def test_mode(self):
        self.assertEqual(categories.mode("粗大ごみ", "自己搬入 原則として一辺が40cmを超えるもの")[0], "dropoff")
        self.assertEqual(categories.mode("粗大ごみ", "電話で申し込み")[0], "reservation")
        self.assertEqual(categories.mode("粗大ごみ")[0], None)                               # 決める言葉が無ければ決めない
        self.assertEqual(categories.mode("燃やすごみ")[0], "curbside")
        self.assertEqual(categories.mode("粗大可燃ごみ", "持込(三宅村クリーンセンター) 月曜日・水曜日・金曜日")[0], "dropoff")


if __name__ == "__main__":
    unittest.main()
