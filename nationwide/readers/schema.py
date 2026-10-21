"""収集日のスキーマ v0.2 (gomisute-schedule/0.2) の型と検査 (DESIGN.md §2)

  python nationwide/readers/schema.py check out/schedules/172014.json …   # 検査 (誤りがあれば 1 で終わる)
  python nationwide/readers/schema.py json-schema                       # JSON Schema を schedule-0.2.schema.json に書く

検査は 2 段:
  1. 形 (JSON Schema。jsonschema が無くても動くよう、使う範囲だけを読む小さな検査器を持つ)
  2. 中身 (id の参照、日付が期間の中か、answerable と blocked_by・provenance の食い違い、published の条件、
     版の証拠がそろわない期間の系列に version_unproven があるか、地区の place_resolution が中身のいちばん弱いものか など)

v0.1 からの読み替え (§2.11) は eval/regress.py。日ごとの答え (day_answer) は回帰 (K1) と MCP の両方で使う。
"""
import datetime as dt, json, pathlib, re, sys

SCHEMA = "gomisute-schedule/0.2"
HERE = pathlib.Path(__file__).resolve().parent
JSON_SCHEMA_FILE = HERE / "schedule-0.2.schema.json"

# ---- 決まった値 ----
BLOCKED_PLAIN = ("pending_printed", "rule_unparsed", "rule_ambiguous", "no_anchor", "period_unknown", "version_unproven",
                 "holiday_policy_missing", "holiday_shift_target_missing", "holiday_policy_conflict", "year_end_missing",
                 "year_end_conflict", "single_reading", "source_changed", "doc_not_fetched", "provisional_month", "reader_not_built")
BLOCKED_PREFIX = ("deferred_undated", "unconsumed_qualifier", "markup_unhandled", "disputed")   # <理由>:<中身>
UNKNOWN_REASONS = ("holiday_kind_not_covered", "year_end_window", "closure_month", "deferred", "unconsumed_qualifier")
DATES_ORIGIN = ("printed", "listed", "expanded")
OK_STATUS = ("agreed", "agreed_weak", "adjudicated")          # answerable にしてよい読みの状態 (§2.8)
PROV_STATUS = OK_STATUS + ("single", "disputed")
INDEPENDENCE = ("code_llm", "llm_diff_model", "llm_same_model")
GATE_UNITS = ("A", "B", "C", "D", "E", "F", "G", "H", "J", "expanded")    # §4.4 の 5 (資料の型 9 つ + 規則から作った日付)
FORMATS = ("csv", "xlsx", "html_table", "html_text", "pdf_text", "pdf_outline", "pdf_image", "js_view", "dynamic_month")
COARSE = ("burnable", "kitchen", "non_burnable", "plastic", "pet_bottle", "beverage_can", "glass_bottle", "paper_cloth",
          "cloth", "paper", "hazardous", "spray_can", "oversized", "metal", "recyclable", "other")
MODES = ("curbside", "station", "dropoff", "reservation")
DATED_MODES = ("curbside", "station")                          # 日付で答えるのはこの 2 つだけ (§2.4)
BASIS = ("residence", "collection_point", "unknown")
LABEL_KINDS = ("official", "schedule", "derived")
UNITS = ("municipality", "ward", "machiaza", "block_range", "remainder", "line_side", "community", "facility",
         "former_municipality", "island", "map_only",
         "unparsed")                                           # unparsed: 地区の文法で読めなかった行 (文のまま。当てない)
GEOMETRY = ("polygon", "points", "needs_line", "unresolved")   # 強い順
MATCH = ("exact", "fold", "ambiguous", "none",
         "unchecked")                                          # unchecked: address-normalize をまだ通していない
COND_KINDS = ("range", "except", "remainder", "partial", "han", "facility", "line_side", "enclave", "note")
RANGE_UNITS = ("chome", "gaiku", "ban", "go", "banchi", "gotou")
IR_KINDS = ("weekly", "monthly_nth", "monthly_last", "monthly_day", "ambiguous_week", "calendar_week", "biweekly",
            "cycle", "months", "seasonal", "subcycle", "on_demand")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
EXC_KINDS = ("holiday_policy", "year_end", "deferred", "caveat", "contingency", "fifth_week", "moved", "added",
             "cancelled", "seasonal", "inline", "long_holiday")
HOLIDAY_VALUES = ("collected", "not_collected", "moved", "unknown")
COVERS = ("national", "substitute", "citizens", "all_weekdays")
SHIFT_TO = ("next_day", "next_weekday", "next_same_weekday", "prev_weekday", "date")
YEAR_END_TREATMENT = ("closure", "skip", "makeup", "shift", "dates")
OTHERS = ("unchanged", "shifted")
TOKEN_WHERE = ("in_file", "file_name", "anchor_only")
TIME_KINDS = ("last_modified", "page_updated", "pdf_modified", "pdf_created", "xlsx_modified", "xlsx_created")
ANSWER_TYPES = ("dates", "ask", "rule_contact", "no_public_collection", "out_of_scope")

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
LG_RE = r"^\d{6}$"


# ---- JSON Schema (draft 2020-12) ----
def _obj(props, required=(), extra=False):
    o = {"type": "object", "properties": props, "additionalProperties": extra}
    if required:
        o["required"] = list(required)
    return o


def _enum(vals, null=False):
    return {"enum": list(vals) + ([None] if null else [])}


S = {"type": "string"}
SN = {"type": ["string", "null"]}
DATE = {"type": "string", "pattern": DATE_RE}
DATEN = {"type": ["string", "null"], "pattern": DATE_RE}
FREE = {"type": "object"}
STRS = {"type": "array", "items": S}

DEFS = {
    "ref": _obj({"source_id": S, "page": {"type": "integer", "minimum": 1}, "table": {"type": "integer"},
                 "row": {"type": "integer"}, "col": {"type": "integer"}, "bbox": {"type": "array", "items": {"type": "number"}},
                 "selector": S, "note": S}, ["source_id"]),
    "span": _obj({"source_id": S, "page": {"type": "integer", "minimum": 1}, "selector": S,
                  "bbox": {"type": "array", "items": {"type": "number"}}, "start": {"type": "integer"},
                  "end": {"type": "integer"}, "text": S, "heading_path": STRS}, ["source_id", "text"]),
    "condition": {"type": "object", "required": ["kind"], "additionalProperties": False, "properties": {
        "kind": _enum(COND_KINDS), "text": S,
        "unit": _enum(RANGE_UNITS), "from": SN, "to": SN,                 # range (終わりの無い範囲は to: null、「末」は "end")
        "of": {"type": "array", "items": {"$ref": "#/$defs/condition"}},  # except
        "split_known": {"type": "boolean"},                               # partial
        "values": STRS, "negate": {"type": "boolean"},                    # han・facility・enclave
        "name": S, "line": S, "side": S}},
    "member": _obj({"text": S, "unit": _enum(UNITS), "machiaza_id": SN, "match": _enum(MATCH),
                    "conditions": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
                    "geometry": _enum(GEOMETRY), "ref": {"$ref": "#/$defs/ref"}, "extra": FREE},
                   ["text", "unit", "match", "conditions", "geometry"]),
    "year_evidence": _obj({
        "token": {"type": ["object", "null"], "additionalProperties": False, "required": ["text", "where"], "properties": {
            "text": S, "where": _enum(TOKEN_WHERE), "place": S, "visible": {"type": "boolean"},
            "fiscal_year": {"type": ["integer", "null"]}, "ref": {"$ref": "#/$defs/ref"}}},
        "anchor_tokens": STRS,
        "time": {"type": "array", "items": _obj({"kind": _enum(TIME_KINDS), "value": S, "source_id": S, "in_window": {"type": "boolean"},
                                                 "note": S}, ["kind", "value"])},
        "conflict": {"type": ["object", "null"]},
        "ok": {"type": "boolean"}, "reasons": STRS}, ["token", "time", "conflict", "ok"]),
    "provenance": _obj({
        "readers": {"type": "array", "items": _obj({"reader": S, "kind": _enum(("code", "llm", "human")), "model": SN,
                                                    "prompt": SN, "inputs": FREE, "agree": {"type": ["boolean", "null"]},
                                                    "note": S}, ["reader", "kind"])},
        "independence": _enum(INDEPENDENCE, null=True), "status": _enum(PROV_STATUS),
        "spans": {"type": "array", "items": {"$ref": "#/$defs/span"}},
        "adjudication": {"type": ["object", "null"]}, "note": S}, ["readers", "status"]),
    "ir": {"type": "object", "required": ["kind"], "properties": {
        "kind": _enum(IR_KINDS), "weekdays": {"type": "array", "items": _enum(WEEKDAYS)}, "weekday": _enum(WEEKDAYS),
        "nth": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
        "except_nth": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
        "weeks": {"type": "array", "items": {"type": "integer"}}, "week_starts": _enum(WEEKDAYS),
        "days": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 31}},
        "anchor": DATEN, "every_weeks": {"type": "integer", "minimum": 1}, "every": {"type": "integer", "minimum": 1},
        "months": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}},
        "from": S, "to": S, "rule": {"$ref": "#/$defs/ir"}, "of": {"$ref": "#/$defs/ir"}, "adds_to": S}},
}

SCHEDULE = _obj({
    "district_id": S, "category_id": S, "period_id": S,
    "dates_origin": _enum(DATES_ORIGIN, null=True),
    "rule": {"type": ["object", "null"], "additionalProperties": False, "properties": {
        "text": S, "span": {"$ref": "#/$defs/span"}, "ir": {"anyOf": [{"$ref": "#/$defs/ir"}, {"type": "null"}]},
        "rule_kind": _enum(("rrule", "segments", "explicit")), "rrule": S, "exdates": {"type": "array", "items": DATE},
        "rdates": {"type": "array", "items": DATE}, "segments": {"type": "array", "items": FREE}}},
    "dates": {"type": "array", "items": DATE},
    "unknown_ranges": {"type": "array", "items": _obj({"from": DATE, "to": DATE, "reason": _enum(UNKNOWN_REASONS),
                                                       "note": S, "exception_id": S}, ["from", "to", "reason"])},
    "answerable": {"type": "boolean"}, "blocked_by": STRS,
    "gate": _obj({"unit": _enum(GATE_UNITS), "open": {"type": "boolean"}}, ["unit", "open"]),
    "published": {"type": "boolean"},
    "caveats": STRS, "expanded_with": STRS,
    "rule_mismatches": {"type": "array", "items": _obj({"month": S, "rule_dates": {"type": "array", "items": DATE},
                                                        "printed_dates": {"type": "array", "items": DATE},
                                                        "explained_by": STRS, "note": S},
                                                       ["month", "rule_dates", "printed_dates", "explained_by"])},
    "provenance": {"$ref": "#/$defs/provenance"}, "ref": {"$ref": "#/$defs/ref"}, "extra": FREE},
    ["district_id", "category_id", "period_id", "dates_origin", "dates", "unknown_ranges", "answerable", "blocked_by",
     "gate", "published", "provenance"])

EXCEPTION = _obj({
    "exception_id": S, "kind": _enum(EXC_KINDS),
    "scope": _obj({"category_ids": {"anyOf": [{"const": "*"}, STRS]}, "district_ids": STRS, "period_ids": STRS}),
    "value": S, "covers": {"type": "array", "items": _enum(COVERS)},
    "shift": _obj({"to": _enum(SHIFT_TO), "date": DATE}, ["to"]),
    "treatment": _enum(YEAR_END_TREATMENT), "last": DATEN, "first": DATEN, "removed": DATEN, "to": DATEN,
    "dates": {"type": "array", "items": DATE}, "others": _enum(OTHERS, null=True),
    "period_name": S, "window": {"type": ["object", "null"]}, "overrides_printed": {"type": "boolean"},
    "test": FREE, "date": DATEN, "months": {"type": "array", "items": {"type": "integer"}},
    "text": S, "context": S, "span": {"$ref": "#/$defs/span"}, "template": S, "extra": FREE}, ["exception_id", "kind", "text"])

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/firemio/gomisute-gps/nationwide/readers/schedule-0.2.schema.json",
    "title": "gomisute-schedule/0.2 (自治体 1 つの収集日)",
    "$defs": DEFS,
    **_obj({
        "schema": {"const": SCHEMA}, "lg_code": {"type": "string", "pattern": LG_RE}, "name": S,
        "periods": {"type": "array", "items": _obj({
            "period_id": S, "valid_from": DATE, "valid_to": DATEN,
            "basis": _obj({"text": S, "ref": {"$ref": "#/$defs/ref"}, "kind": _enum(("stated", "undated"))}, ["text"]),
            "year_evidence": {"$ref": "#/$defs/year_evidence"}, "note": S}, ["period_id", "valid_from", "valid_to", "basis",
                                                                              "year_evidence"])},
        "partitions": {"type": "array", "items": _obj({
            "partition_id": S, "label": S, "category_ids": STRS, "basis": _enum(BASIS),
            "basis_span": {"anyOf": [{"$ref": "#/$defs/span"}, {"type": "null"}]}}, ["partition_id", "category_ids", "basis"])},
        "districts": {"type": "array", "items": _obj({
            "district_id": S, "partition_id": S, "label": S, "label_kind": _enum(LABEL_KINDS),
            "members": {"type": "array", "items": {"$ref": "#/$defs/member"}},
            "place_resolution": _enum(GEOMETRY), "ref": {"$ref": "#/$defs/ref"}, "extra": FREE},
            ["district_id", "partition_id", "label", "label_kind", "members", "place_resolution"])},
        "categories": {"type": "array", "items": _obj({
            "category_id": S, "official_label": S, "coarse": {"type": "array", "items": _enum(COARSE), "minItems": 1},
            "mode": _enum(MODES), "ref": {"$ref": "#/$defs/ref"},
            "notes_by_period": {"type": "array", "items": _obj({"period_id": S, "text": S, "ref": {"$ref": "#/$defs/ref"}},
                                                               ["period_id", "text"])}, "extra": FREE},
            ["category_id", "official_label", "coarse", "mode"])},
        "schedules": {"type": "array", "items": SCHEDULE},
        "exceptions": {"type": "array", "items": EXCEPTION},
        "sources": {"type": "array", "items": _obj({
            "source_id": S, "url": S, "source_key": S, "found_from": SN, "anchor_text": SN,
            "anchor_context": {"type": ["object", "null"]}, "title": SN, "publisher": SN, "format": _enum(FORMATS),
            "pages": {"type": ["integer", "null"]}, "sha256": SN, "content_sha256": SN, "last_modified": SN,
            "fetched_at": SN, "note": S, "extra": FREE}, ["source_id", "url", "source_key", "format", "sha256"])},
        "watch": _obj({"sources": {"type": "array", "items": _obj({"source_id": S, "content_sha256": SN, "sha256": SN},
                                                                  ["source_id"])},
                       "link_sets": {"type": "array", "items": _obj({"source_id": S, "link_set_sha256": S, "links": {"type": "integer"}},
                                                                    ["source_id", "link_set_sha256"])}},
                      ["sources"]),
        "contact": FREE, "put_out_by": FREE, "not_covered": STRS,
        "coverage": _obj({"answer_type": _enum(ANSWER_TYPES), "by_district": FREE, "population_share": {"type": ["object", "null"]},
                          "population_basis": SN, "blocked_series": FREE, "gate_pending": FREE,
                          "quality": {"type": ["object", "null"]}}, ["answer_type"]),
        "build": FREE, "aux": FREE},
        ["schema", "lg_code", "name", "periods", "partitions", "districts", "categories", "schedules", "exceptions",
         "sources", "watch", "coverage", "build"]),
}


# ---- 小さな検査器 (JSON Schema の使う範囲だけ: type・properties・required・additionalProperties・enum・const・
#      pattern・items・minItems・minimum・maximum・anyOf・$ref) ----
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def _resolve(schema, root):
    while "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        schema = root["$defs"][name]
    return schema


def structural_errors(v, schema=None, path="$", root=None, out=None):
    """JSON Schema の使う範囲で形を確かめる → 誤りの文のリスト"""
    root = root or JSON_SCHEMA
    schema = _resolve(schema if schema is not None else JSON_SCHEMA, root)
    out = [] if out is None else out
    if "anyOf" in schema:
        if not any(not structural_errors(v, s, path, root, []) for s in schema["anyOf"]):
            out.append(f"{path}: どの形 (anyOf) にも合わない")
        return out
    if "const" in schema and v != schema["const"]:
        out.append(f"{path}: {schema['const']!r} でない")
    if "enum" in schema and v not in schema["enum"]:
        out.append(f"{path}: {v!r} は決まった値 {schema['enum']} に無い")
        return out
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(v, x) for x in ts):
            out.append(f"{path}: 型が {t} でない ({type(v).__name__})")
            return out
    if isinstance(v, str) and "pattern" in schema and not re.search(schema["pattern"], v):
        out.append(f"{path}: {v!r} が形 {schema['pattern']} に合わない")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in schema and v < schema["minimum"]:
            out.append(f"{path}: {v} < {schema['minimum']}")
        if "maximum" in schema and v > schema["maximum"]:
            out.append(f"{path}: {v} > {schema['maximum']}")
    if isinstance(v, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in v:
                out.append(f"{path}: {k} が無い")
        for k, x in v.items():
            if k in props:
                structural_errors(x, props[k], f"{path}.{k}", root, out)
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: 知らない項目 {k}")
    if isinstance(v, list):
        if "minItems" in schema and len(v) < schema["minItems"]:
            out.append(f"{path}: 要素が {schema['minItems']} 個より少ない")
        if "items" in schema:
            for i, x in enumerate(v):
                structural_errors(x, schema["items"], f"{path}[{i}]", root, out)
    return out


# ---- 中身の検査 ----
def check_digit_ok(lg):
    """6 桁の lg_code の検査数字 (JIS X 0402)"""
    if not re.fullmatch(LG_RE, lg or ""):
        return False
    s = sum(int(c) * w for c, w in zip(lg[:5], (6, 5, 4, 3, 2)))
    return str((11 - s % 11) % 10) == lg[5]


def blocked_ok(reason):
    """blocked_by の 1 つが決まった形か (<理由>:<中身> の中身は空白だけ・前後の空白を許さない)"""
    if reason in BLOCKED_PLAIN:
        return True
    head, sep, rest = reason.partition(":")
    return bool(sep) and head in BLOCKED_PREFIX and bool(rest.strip()) and rest == rest.strip()


def weakest(geoms):
    """place_resolution = 中身のうちいちばん弱いもの (polygon > points > needs_line > unresolved)"""
    return max(geoms, key=GEOMETRY.index) if geoms else "unresolved"


def _d(s):
    return dt.date.fromisoformat(s)


def time_window(valid_from, valid_to):
    """時の証拠の窓 (§3.3 の 2): 有効期間の始まりの 15 か月前から終わりまで (version.window と同じ)"""
    import version
    return version.window(valid_from, valid_to)


def year_evidence_errors(p):
    """期間の year_evidence が ok なら、その中身が ok の決まり (§3.3) を満たすか → 誤りの文のリスト。
    窓は書いてある in_window を信じずに計算し直す"""
    ye = p.get("year_evidence") or {}
    if not ye.get("ok"):
        return []
    at = f"期間 {p['period_id']}"
    tok = ye.get("token") or {}
    err = []
    if tok.get("where") not in ("in_file", "file_name") or not ye.get("time") or ye.get("conflict"):
        err.append(f"{at}: 年の印 (ファイルの中か名前) と時の証拠がそろわず、ぶつかりも無いときだけ ok にできる")
    if not p.get("valid_to"):
        return err
    lo, hi = time_window(p["valid_from"], p["valid_to"])
    counted = [t for t in ye.get("time", []) if t.get("note") != "dynamic"]
    days = []
    for t in counted:
        try:
            days.append((t, _d(str(t["value"])[:10])))
        except ValueError:
            err.append(f"{at}: 時の証拠の日付 {t['value']!r} が読めない")
    inside = [t for t, d in days if lo <= d <= hi]
    if not inside or any(d < lo for _, d in days):
        err.append(f"{at}: 版の証拠が ok なのに、時の証拠が窓 ({lo}〜{hi}) の中に無いか、窓より古いものがある")
    for t, d in days:
        if "in_window" in t and t["in_window"] != (lo <= d <= hi):
            err.append(f"{at}: 時の証拠 {t['value']} の in_window が窓 ({lo}〜{hi}) と合わない")
    fy = tok.get("fiscal_year")
    if isinstance(fy, int):
        a, b = _d(p["valid_from"]), _d(p["valid_to"])
        mid = a + (b - a) / 2
        if not dt.date(fy, 4, 1) <= mid <= dt.date(fy + 1, 3, 31):
            err.append(f"{at}: 年の印の年度 {fy} が期間 ({p['valid_from']}〜{p['valid_to']}) と合わない")
    return err


def _in_scope(e, s):
    sc = e.get("scope") or {}
    cs, ds, ps = sc.get("category_ids"), sc.get("district_ids"), sc.get("period_ids")
    return (cs in (None, "*") or s["category_id"] in cs) and (not ds or s["district_id"] in ds) \
        and (not ps or s["period_id"] in ps)


def deferred_errors(e, schedules, periods):
    """deferred (§2.6) は印字・一覧の日付より強い: 当たる系列で答えるとしたものは、窓の日がすべて unknown_ranges に入っていること。
    窓の無い deferred (日付の無い年末年始以外の期間) に当たる系列は答えない (deferred_undated)"""
    err = []
    w = e.get("window") or {}
    for s in schedules:
        if not s.get("answerable") or not _in_scope(e, s):
            continue
        at = f"例外 {e['exception_id']} (deferred) と系列 {'/'.join(series_key(s))}"
        if not (w.get("from") and w.get("to")):
            err.append(f"{at}: 窓の無い deferred に当たる系列を答えている (deferred_undated で止める)")
            continue
        p = periods.get(s["period_id"]) or {}
        lo, hi = max(w["from"], p.get("valid_from", w["from"])), min(w["to"], p.get("valid_to") or w["to"])
        if lo > hi:
            continue
        need, d = set(), _d(lo)
        while d <= _d(hi):
            need.add(d)
            d += dt.timedelta(days=1)
        if not need <= unknown_days(s):
            err.append(f"{at}: 窓 ({lo}〜{hi}) の日が unknown_ranges に入っていない (印字・一覧の日付より deferred が強い)")
    return err


def _iter_spans(o, path="$"):
    """どこにあっても、span / ref の形のもの (source_id を持つ dict) を拾う (sources の外で呼ぶ)"""
    if isinstance(o, dict):
        if isinstance(o.get("source_id"), str):
            yield path, o
        for k, v in o.items():
            yield from _iter_spans(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _iter_spans(v, f"{path}[{i}]")


def semantic_errors(doc):
    """id の参照・日付・answerable の決まりなど、形では見られないもの → 誤りの文のリスト"""
    err = []
    lg = doc.get("lg_code", "")
    if not check_digit_ok(lg):
        err.append(f"lg_code {lg} の検査数字が合わない")
    def ids(key, idk):
        seen, dup = set(), set()
        for x in doc.get(key, []):
            i = x.get(idk)
            (dup if i in seen else seen).add(i)
        for i in dup:
            err.append(f"{key}: {idk} {i} が 2 つ以上ある")
        return seen
    periods = {p["period_id"]: p for p in doc.get("periods", [])}
    ids("periods", "period_id")
    parts = {p["partition_id"]: p for p in doc.get("partitions", [])}
    ids("partitions", "partition_id")
    dists = {d["district_id"]: d for d in doc.get("districts", [])}
    ids("districts", "district_id")
    cats = {c["category_id"]: c for c in doc.get("categories", [])}
    ids("categories", "category_id")
    excs = {e["exception_id"]: e for e in doc.get("exceptions", [])}
    ids("exceptions", "exception_id")
    srcs = ids("sources", "source_id")
    for p in periods.values():
        if p.get("valid_to") and p["valid_to"] < p["valid_from"]:
            err.append(f"期間 {p['period_id']}: valid_to が valid_from より前")
        if p.get("valid_to") is None and (p.get("basis") or {}).get("kind") != "undated":
            err.append(f"期間 {p['period_id']}: valid_to が null なのに basis.kind が undated でない")
        err += year_evidence_errors(p)
    for p in parts.values():
        for c in p.get("category_ids", []):
            if c not in cats:
                err.append(f"区分け {p['partition_id']}: 品目 {c} が無い")
    cat_part = {}
    for p in parts.values():
        for c in p.get("category_ids", []):
            if c in cat_part:
                err.append(f"品目 {c} が 2 つの区分け ({cat_part[c]}, {p['partition_id']}) にある")
            cat_part[c] = p["partition_id"]
    for d in dists.values():
        if d.get("partition_id") not in parts:
            err.append(f"地区 {d['district_id']}: 区分け {d.get('partition_id')} が無い")
        if not d["district_id"].startswith(lg + "-"):
            err.append(f"地区 {d['district_id']}: 頭に lg_code ({lg}-) が無い")
        geoms = [m["geometry"] for m in d.get("members", [])]
        if d.get("place_resolution") != weakest(geoms):
            err.append(f"地区 {d['district_id']}: place_resolution {d.get('place_resolution')} が中身のいちばん弱いもの {weakest(geoms)} でない")
    seen = set()
    for i, s in enumerate(doc.get("schedules", [])):
        key = (s["district_id"], s["category_id"], s["period_id"])
        at = f"系列 {'/'.join(key)}"
        if key in seen:
            err.append(f"{at}: 2 つある")
        seen.add(key)
        d, c, p = dists.get(s["district_id"]), cats.get(s["category_id"]), periods.get(s["period_id"])
        if d is None or c is None or p is None:
            err.append(f"{at}: 地区・品目・期間のどれかが無い")
            continue
        if cat_part.get(c["category_id"]) != d["partition_id"]:
            err.append(f"{at}: 品目が地区の区分けに入っていない")
        lo, hi = p["valid_from"], p.get("valid_to") or "9999-12-31"
        ds = s.get("dates", [])
        if ds != sorted(set(ds)):
            err.append(f"{at}: dates が並んでいないか、同じ日が 2 つある")
        out = [x for x in ds if not lo <= x <= hi]
        if out:
            err.append(f"{at}: 期間の外の日付 {out[:3]}")
        for u in s.get("unknown_ranges", []):
            if u["to"] < u["from"]:
                err.append(f"{at}: unknown_ranges の to が from より前")
            if u["from"] < lo or u["to"] > hi:
                err.append(f"{at}: unknown_ranges ({u['from']}〜{u['to']}) が期間の外にはみ出す")
        for b in s.get("blocked_by", []):
            if not blocked_ok(b):
                err.append(f"{at}: blocked_by の理由 {b!r} が決まった形でない")
        if len(s.get("blocked_by", [])) != len(set(s.get("blocked_by", []))):
            err.append(f"{at}: blocked_by に同じ理由が 2 つ以上ある")
        status = (s.get("provenance") or {}).get("status")
        if s.get("answerable"):
            if s.get("blocked_by"):
                err.append(f"{at}: blocked_by が空でないのに answerable")
            if status not in OK_STATUS:
                err.append(f"{at}: 読みの状態 {status} なのに answerable (agreed・agreed_weak・adjudicated だけ)")
            if c.get("mode") not in DATED_MODES:
                err.append(f"{at}: mode {c.get('mode')} の品目を日付で答えている")
            if not s.get("dates_origin"):
                err.append(f"{at}: answerable なのに dates_origin が無い")
        elif not s.get("blocked_by"):
            err.append(f"{at}: answerable でないのに blocked_by (理由) が空")
        if s.get("published") and not (s.get("answerable") and (s.get("gate") or {}).get("open")):
            err.append(f"{at}: published は answerable で関門が開いているときだけ")
        if s.get("dates_origin") == "expanded" and (s.get("gate") or {}).get("unit") != "expanded":
            err.append(f"{at}: 規則から作った系列の関門の単位は expanded")
        if s.get("dates_origin") in ("printed", "listed") and (s.get("gate") or {}).get("unit") == "expanded":
            err.append(f"{at}: 印字・一覧の系列の関門の単位は資料の型 (A〜J) で、expanded にしない")
        if s.get("dates_origin") == "expanded" and not ((s.get("rule") or {}).get("ir")):
            err.append(f"{at}: 規則から作ったのに rule.ir が無い")
        if not (p.get("year_evidence") or {}).get("ok") and "version_unproven" not in s.get("blocked_by", []) \
                and (p.get("basis") or {}).get("kind") != "undated":
            err.append(f"{at}: 期間の版の証拠がそろわないのに blocked_by に version_unproven が無い")
        if (p.get("basis") or {}).get("kind") == "undated" and s.get("answerable"):
            err.append(f"{at}: 期間の書いていない資料 (undated) の系列を日付で答えている")
        for e in s.get("expanded_with", []) + s.get("caveats", []):
            if e not in excs:
                err.append(f"{at}: 例外 {e} が無い")
        for e in s.get("caveats", []):
            if e in excs and excs[e]["kind"] not in ("caveat", "contingency"):
                err.append(f"{at}: caveats に {excs[e]['kind']} の例外 {e} がある (caveat・contingency だけ)")
        for m in s.get("rule_mismatches", []):
            for e in m.get("explained_by", []):
                if e not in excs:
                    err.append(f"{at}: rule_mismatches の説明の例外 {e} が無い")
    for e in excs.values():
        sc = e.get("scope") or {}
        cs = sc.get("category_ids")
        for c in ([] if cs in (None, "*") else cs):
            if c not in cats:
                err.append(f"例外 {e['exception_id']}: 品目 {c} が無い")
        for d in sc.get("district_ids", []) or []:
            if d not in dists:
                err.append(f"例外 {e['exception_id']}: 地区 {d} が無い")
        if e["kind"] == "holiday_policy" and e.get("value") == "moved" and not e.get("shift"):
            err.append(f"例外 {e['exception_id']}: moved なのに行き先 (shift) が無い")
        if e["kind"] == "deferred":
            err += deferred_errors(e, doc.get("schedules", []), periods)
    for path, o in _iter_spans({k: v for k, v in doc.items() if k != "sources"}):
        if o["source_id"] not in srcs:
            err.append(f"{path}: 出典 {o['source_id']} が sources に無い")
    return err


def validate(doc):
    """形と中身の検査 → 誤りの文のリスト (空なら通った)"""
    err = structural_errors(doc)
    if err:
        return err
    return semantic_errors(doc)


def check(doc):
    err = validate(doc)
    if err:
        raise ValueError(f"スキーマ v0.2 の検査に通らない ({len(err)} 件): " + "; ".join(err[:10]))
    return doc


# ---- 使い道 ----
def series_key(s):
    return (s["district_id"], s["category_id"], s["period_id"])


def unknown_days(s):
    """系列の unknown_ranges の日の集まり"""
    out = set()
    for u in s.get("unknown_ranges", []):
        d, e = _d(u["from"]), _d(u["to"])
        while d <= e:
            out.add(d)
            d += dt.timedelta(days=1)
    return out


def day_answer(doc, district_id, day, only_published=False):
    """地区のその日の答え → {"date", "status": "dates" | "partial" | "unknown", "categories": [品目の id], "unknown": [(品目, 理由)]}
    status: dates = どの品目も分かる (categories が空なら、その日は収集なし)。partial = 分からない品目がある
    (categories は分かった品目のうちその日に収集するもの。unknown の品目を「収集なし」と言わない)。unknown = どの品目も分からない。
    期間の外・unknown_ranges・答えない系列は「不明」で、「収集なし」とは言わない (unknown_ranges は dates より強い)。
    only_published=False (回帰のため) なら answerable も published も見ずに dates を読む"""
    day = day if isinstance(day, dt.date) else _d(day)
    iso = day.isoformat()
    dist = next(d for d in doc["districts"] if d["district_id"] == district_id)
    periods = {p["period_id"]: p for p in doc["periods"]}
    part = next(p for p in doc["partitions"] if p["partition_id"] == dist["partition_id"])
    cats, unknown = [], []
    for cid in part["category_ids"]:
        ss = [s for s in doc["schedules"] if s["district_id"] == district_id and s["category_id"] == cid
              and periods[s["period_id"]]["valid_from"] <= iso <= (periods[s["period_id"]].get("valid_to") or "9999-12-31")]
        if not ss:
            unknown.append((cid, "out_of_period"))
            continue
        s = ss[0]
        if only_published and not s.get("published"):
            unknown.append((cid, "not_published"))
            continue
        if day in unknown_days(s):
            unknown.append((cid, next(u["reason"] for u in s["unknown_ranges"] if u["from"] <= iso <= u["to"])))
            continue
        if iso in s.get("dates", []):
            cats.append(cid)
    status = "dates" if not unknown else ("unknown" if len(unknown) == len(part["category_ids"]) else "partial")
    return {"date": iso, "status": status, "categories": cats, "unknown": unknown}


def write_json_schema(path=JSON_SCHEMA_FILE):
    pathlib.Path(path).write_text(json.dumps(JSON_SCHEMA, ensure_ascii=False, indent=1) + "\n", "utf-8", newline="\n")
    return path


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["json-schema"]:
        p = write_json_schema()
        print(f"書いた: {p.name}")
        return 0
    if argv[:1] == ["check"]:
        bad = 0
        for f in argv[1:]:
            err = validate(json.loads(pathlib.Path(f).read_text("utf-8")))
            print(f"{pathlib.Path(f).name}: {'OK' if not err else f'{len(err)} 件の誤り'}")
            for e in err[:30]:
                print("  ", e)
            bad += bool(err)
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
