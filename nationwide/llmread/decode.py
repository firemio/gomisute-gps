"""HTML・CSV・テキストの文字コードの判定 (資料の読み込みで 1 つだけ。prep.py・fetch_docs.py が使う)

順番: BOM → HTTP の charset → <meta charset> (HTML だけ。XML の宣言も) → UTF-8 として読めるか → cp932。
どれでも読めなければ止める (DecodeError)。置き換え文字で無理に読まない。
BOM を先に見るのは、杉並・日高・多摩の CSV が BOM つきの UTF-8 で、サーバーの charset の見出しが違っていても BOM のほうが確かだから。
見出しと BOM・<meta> がぶつかったことは conflicts に記録する。
"""
import codecs, re

ALIAS = {"shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932", "windows-31j": "cp932",
         "ms_kanji": "cp932", "csshiftjis": "cp932", "cp932": "cp932", "euc-jp": "euc_jp", "x-euc-jp": "euc_jp",
         "eucjp": "euc_jp", "eucjp-ms": "euc_jp", "iso-2022-jp": "iso2022_jp", "utf8": "utf-8", "utf-8": "utf-8",
         "unicode": "utf-8", "utf-16": "utf-16", "utf-16le": "utf-16-le", "utf-16be": "utf-16-be"}
# 同じ系統で広いもの (EUC-JP の NEC の特殊文字 ①② は euc_jis_2004 で読める)。宣言されたものが厳しく読めないときだけ試す
WIDER = {"euc_jp": "euc_jis_2004", "cp932": "shift_jis_2004", "iso2022_jp": "iso2022_jp_2004"}
BOMS = ((codecs.BOM_UTF8, "utf-8-sig", "utf-8"), (codecs.BOM_UTF16_LE, "utf-16", "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16", "utf-16-be"))


class DecodeError(ValueError):
    """どの文字コードでも厳しく読めない"""


class Decoded:
    """読んだ結果: text、encoding (使った文字コード)、how (bom / http / meta / utf8 / cp932)、conflicts (ぶつかったこと)、tried"""

    def __init__(self, text, encoding, how, conflicts, tried):
        self.text, self.encoding, self.how, self.conflicts, self.tried = text, encoding, how, conflicts, tried

    def record(self):
        return {"encoding": self.encoding, "how": self.how, "conflicts": self.conflicts}

    def __repr__(self):
        return f"Decoded({self.encoding} via {self.how}, conflicts={self.conflicts})"


def norm_charset(name):
    """charset の名前 → Python の名前 (知らないものは codecs で引けるか見る)。読めなければ None"""
    n = (name or "").strip().strip("\"'").lower()
    if not n:
        return None
    n = ALIAS.get(n, n)
    try:
        codecs.lookup(n)
        return n
    except LookupError:
        return None


def _family(enc):
    """BOM と宣言を比べるための系統の名前"""
    e = (enc or "").replace("_", "-").lower()
    if e.startswith("utf-8"):
        return "utf-8"
    if e.startswith("utf-16"):
        return "utf-16"
    return e


def http_charset(content_type):
    m = re.search(r"charset\s*=\s*[\"']?([\w\-.:]+)", content_type or "", re.I)
    return m.group(1) if m else None


def meta_charset(head):
    """先頭の bytes から <meta charset> / <meta http-equiv content="…charset=…"> / <?xml encoding="…"?> の名前"""
    m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w\-.:]+)", head, re.I)
    if m:
        return m.group(1).decode("ascii", "ignore")
    m = re.match(rb"\s*<\?xml[^>]+encoding\s*=\s*[\"']([\w\-.:]+)", head, re.I)
    return m.group(1).decode("ascii", "ignore") if m else None


def _strict(body, enc):
    try:
        return body.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return None


def _halfkana_heavy(t):
    wide = [c for c in t[:20000] if ord(c) > 0x7f]
    return len(wide) >= 20 and sum(1 for c in wide if "｡" <= c <= "ﾟ") / len(wide) > 0.3


def decode(body, content_type="", html=True):
    """bytes → Decoded。html=False (CSV・テキスト) なら <meta> を見ない"""
    conflicts, tried = [], []
    hdr_raw = http_charset(content_type)
    hdr = norm_charset(hdr_raw)
    if hdr_raw and not hdr:
        conflicts.append(f"HTTP の charset「{hdr_raw}」を知らない")
    meta_raw = meta_charset(body[:4096]) if html else None
    meta = norm_charset(meta_raw)
    for bom, enc, fam in BOMS:
        if body.startswith(bom):
            t = _strict(body, enc)
            if t is not None:
                for name, v in (("HTTP の charset", hdr), ("<meta charset>", meta)):
                    if v and _family(v) != _family(fam):
                        conflicts.append(f"BOM ({fam}) と {name} ({v}) がぶつかった。BOM を採った")
                if t.startswith("﻿"):
                    t = t[1:]
                return Decoded(t, enc, "bom", conflicts, [enc])
            tried.append(enc)
    steps = []
    if hdr:
        steps.append((hdr, "http"))
    if meta:
        if hdr and meta != hdr:
            conflicts.append(f"HTTP の charset ({hdr}) と <meta charset> ({meta}) がぶつかった")
        steps.append((meta, "meta"))
    steps += [("utf-8", "utf8"), ("cp932", "cp932")]
    for enc, how in steps:
        for e in (enc, WIDER.get(enc)) if how in ("http", "meta") else (enc,):
            if not e or e in tried:
                continue
            tried.append(e)
            t = _strict(body, e)
            if t is not None and how == "cp932" and _halfkana_heavy(t):
                raise DecodeError("宣言が無く、cp932 で読むと半角カナだらけになる (EUC-JP などの疑い)。読まずに止める")
            if t is not None:
                if how in ("utf8", "cp932") and (hdr or meta):
                    conflicts.append(f"宣言された文字コード ({hdr or meta}) では読めず、{e} で読んだ")
                return Decoded(t, e, how, conflicts, tried)
    raise DecodeError(f"どの文字コードでも読めない (試した: {', '.join(tried)})")
