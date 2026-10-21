"""礼儀正しく取る係 (nationwide/discover の取得はすべてこれを通る。1 回の実行で 1 つを共有する)

  - robots.txt を守る。RFC 9309 の読み方 (いちばん長く一致する規則、* と $) で決める。urllib.robotparser でも読み、
    Crawl-delay・Request-rate・Sitemap に使う (規則の順番だけで robotparser が禁止とするものは RFC に従って取り、数を記録する)。
    robots.txt はホストごとに 1 回だけ取って覚える (キャッシュは 24 時間)
  - 1 ホストに 1 秒に 1 回まで。前の取得が終わってから数える。robots.txt の Crawl-delay・Request-rate が長ければそれに従う
    (60 秒を超える Crawl-delay は守りきれないので、そのホストは取らない)
  - 同じ IP アドレス (共用のサーバー) には、取り始めの間を ip_gap 秒 (既定 0.25) 空ける
  - 全体で同時に開く接続の数に上限 (既定 16)。同じ URL を 2 つのスレッドが同時に取らない
  - 転送 (リダイレクト) は自分で 1 回ずつ追い (5 回まで)、転送先のホストでも robots.txt と間隔を守る
  - 403・429・ボット対策の確認ページ (Cloudflare・Incapsula など) が返ったら、そのホストを「ブロック」と記録し、
    それ以上取らない。回避はしない
  - 条件つき GET (ETag・Last-Modified) のキャッシュを data/discover/cache/ に置く。
    取ってから max_age (既定 72 時間) のうちは取りに行かない (やり直しても同じサイトをたたかない)
  - 時間切れ・5xx は間を空けて取り直す (retries 回まで)。Retry-After はそのホスト全体に守る (120 秒を超えればその実行では取らない)。
    つながらない・5xx の URL が 3 つ続いたホストはあきらめる
  - 大きすぎるもの (HTML 5MB・PDF 50MB・ほか 20MB を超える) は中身を残さず、大きさだけ記録する
    (Content-Length で分かるときは本文を読まない)
  - 多くのホストで続けてつながらないときは、こちらのネットが落ちたとみなして止める (結果を書かない)
  - 記録 (誤りの文) に手元のファイルの場所を書かない (scrub)
"""
import contextlib, datetime, email.utils, gzip, hashlib, http.client, json, os, pathlib, re, shutil, socket, ssl, tempfile
import threading, time, zlib
import urllib.error, urllib.parse, urllib.request, urllib.robotparser
from collections import Counter

UA = "gomisute-gps-discover (+https://github.com/firemio/gomisute-gps)"
AGENT = "gomisute-gps-discover"                      # robots.txt の User-agent と照らし合わせる名前
MB = 1 << 20
CAP_HTML, CAP_PDF, CAP_OTHER, CAP_ROBOTS = 5 * MB, 50 * MB, 20 * MB, 512 * 1024
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5
RETRY_CODES = (500, 502, 503, 504)
BLOCK_CODES = (403, 429)
WEAK_STATUSES = (202, 403, 429, 503)                  # 弱い印を見る状態 (404 などの「無い」ページの飾りのスクリプトでは止めない)
FORBID_STRIKES = 3                                    # 断られた答え (確認ページでない 403 など) がこの数の別のフォルダで続いたら、そのホストに断られているとみなす
FORBID_URLS = 10                                      # 同じフォルダでも、この数のページで続いたら止める (ホットリンク禁止の PDF の置き場などでサイトごと止めないため)
LOOP_ERRORS = ("転送がループした", "転送が 5 回を超えた")
GIVE_UP_AFTER = 3                                     # つながらない・5xx の URL がこの数続いたホストはあきらめる
MAX_RETRY_AFTER = 120                                 # これより長い Retry-After はその実行では待たない (ホストを止める)
MAX_CRAWL_DELAY = 60.0                                # これより長い Crawl-delay のホストは取らない
NET_DOWN_HOSTS = 20                                   # 成功をはさまずにこの数のホストでつながらなければ、こちらのネットが落ちたとみなす
KEEP_HEADERS = ("content-type", "content-length", "etag", "last-modified", "location", "retry-after", "content-disposition")

# ボット対策の確認ページの印 (小文字で探す)。強い印はどの応答でも。弱い印は 200 以外 (403・503・202 など) の
# 小さく (15KB 未満) 文字がほとんど無いページのときだけ (Cloudflare・Incapsula はふつうの 200 のページにも
# スクリプト (challenge-platform/…/jsd/main.js など) を差し込むので、200 では弱い印を見ない)
CHALLENGE_STRONG = ("cf_chl_opt", "cf-browser-verification", "<title>just a moment...</title>", "attention required! | cloudflare",
                    "request unsuccessful. incapsula incident id", "px-captcha", "distil_r_captcha", "ddos-guard",
                    "checking your browser before accessing")
CHALLENGE_WEAK = ("_incapsula_resource", "challenge-platform", "aws-waf-token", "awswaf", "verify you are human")


# ---- 手元の場所を書かない ----

def _roots():
    out = set()
    for p in (os.path.expanduser("~"), tempfile.gettempdir(), os.getcwd(), str(pathlib.Path(__file__).resolve().parents[2])):
        try:
            for q in {p, os.path.realpath(p)}:
                if q and len(q) > 3:
                    out |= {q, q.replace("\\", "/"), q.replace("\\", "\\\\")}
        except (OSError, ValueError):
            continue
    return sorted(out, key=len, reverse=True)


_ROOTS = _roots()
_WIN_PATH = re.compile(r"(?<![\w/])[A-Za-z]:(?:\\\\|\\|/)[^\s'\"<>|*?]*")
_POSIX_PATH = re.compile(r"(?<![\w.:/~-])/(?:home|Users|tmp|var|private|mnt|root|opt|srv|Volumes)/[^\s'\"<>]*")


def scrub(text):
    """誤りの文などから手元のファイルの場所 (ホーム・一時フォルダ・リポジトリ・ドライブ名つきの場所) を消す"""
    if not text:
        return text
    s = str(text)
    for r in _ROOTS:
        if r in s:
            s = s.replace(r, "<local>")
    s = _WIN_PATH.sub("<path>", s)
    return _POSIX_PATH.sub("<path>", s)


def _err_text(e, n=200):
    return scrub(f"{type(e).__name__}: {e}")[:n]


class Halted(Exception):
    """上限 (--max-requests) に届いたか、止める指示があった"""


class _TooLarge(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib に転送を追わせない (転送先の robots.txt と間隔を守るため、自分で追う)"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def iso(t=None):
    return datetime.datetime.fromtimestamp(t or time.time(), datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def norm_url(url):
    """URL をそろえる (# 以降を外す、ホストを小文字、既定のポートを外す、パスが空なら /、空白や日本語は %)。http(s) 以外は None"""
    try:
        p = urllib.parse.urlsplit(url.strip())
        scheme = p.scheme.lower()
        host = (p.hostname or "").lower().rstrip(".")
        port = p.port
    except (ValueError, AttributeError):
        return None
    if scheme not in ("http", "https") or not host:
        return None
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    path = urllib.parse.quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(p.query, safe="=&%/:+,;@!$'()*-._~?")
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def safe_join(base, href):
    """urljoin。全角の「ｈｔｔｐｓ：」「tel：」のように壊れたリンクで ValueError が出たら None (そのリンクは読み飛ばす)"""
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return None


def netloc_of(url):
    return urllib.parse.urlsplit(url).netloc


# ---- robots.txt ----

def _mine(agent):
    """User-agent の名前が自分に当たるか (robotparser と同じく、名前が自分の名前の一部なら当たる。例: gomisute-gps)"""
    a = agent.split("/")[0].strip()
    return bool(a) and a != "*" and a in AGENT


def parse_rules(lines):
    """RFC 9309: 自分に当たるグループ (すべて合わせる) の規則と Crawl-delay を返す (無ければ * のグループ)"""
    groups, cur, in_rules = [], None, False
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if cur is None or in_rules:
                cur = {"agents": [], "rules": [], "delay": None}
                groups.append(cur)
                in_rules = False
            cur["agents"].append(v.lower())
        elif cur is not None and k in ("allow", "disallow"):
            in_rules = True
            if v:
                cur["rules"].append((k == "allow", v))
        elif cur is not None and k == "crawl-delay":
            in_rules = True
            try:
                cur["delay"] = float(v)
            except ValueError:
                pass
    mine = [g for g in groups if any(_mine(a) for a in g["agents"])]
    chosen = mine or [g for g in groups if "*" in g["agents"]]
    rules = [r for g in chosen for r in g["rules"]]
    delays = [g["delay"] for g in chosen if g["delay"]]
    return rules, max(delays) if delays else None


def rfc_allowed(rules, url):
    """RFC 9309: いちばん長く一致する規則が勝つ。同じ長さなら allow。* は何でも、末尾の $ は終わり"""
    u = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote((u.path or "/") + (("?" + u.query) if u.query else ""))
    best = (-1, True)
    for allow, pat in rules:
        pat = urllib.parse.unquote(pat)
        rx = "^" + re.escape(pat).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        if re.match(rx, path):
            n = len(pat)
            if n > best[0] or (n == best[0] and allow):
                best = (n, allow)
    return best[1]


class Robots:
    """1 つのホスト (scheme://netloc) の robots.txt。unavailable は取れなかったわけ (blocked / unreachable / robots_5xx)。
    offline_miss: --offline でキャッシュに無かった"""

    def __init__(self, text=None, allow_all=False, disallow_all=False, unavailable=None, note=None, offline_miss=False):
        self.rp = urllib.robotparser.RobotFileParser()
        self.rules, self.rfc_delay = [], None
        self.unavailable, self.note, self.offline_miss = unavailable, note, offline_miss
        if text is not None:
            lines = text.splitlines()
            self.rp.parse(lines)
            self.rules, self.rfc_delay = parse_rules(lines)
        elif disallow_all or unavailable:
            self.rp.disallow_all = True
        else:
            self.rp.allow_all = True

    def allowed(self, url):
        """RFC 9309 の読み方で決める (robotparser は始めに当たった規則で決めるので、「Disallow: /」のあとの
        「Allow: /kurashi/」を読めない。そこは RFC に従う)"""
        if self.unavailable or self.rp.disallow_all:
            return False
        if self.rp.allow_all:
            return True
        return rfc_allowed(self.rules, url)

    def rp_disagrees(self, url):
        """RFC では取ってよいが robotparser では禁止 (記録のため)"""
        try:
            return not (self.unavailable or self.rp.allow_all or self.rp.disallow_all) and not self.rp.can_fetch(AGENT, url)
        except Exception:
            return False

    def delay(self):
        """robots.txt が求める間隔 (秒)。Crawl-delay と Request-rate の長い方"""
        d = [self.rfc_delay or 0.0]
        try:
            cd = self.rp.crawl_delay(AGENT)
            if cd:
                d.append(float(cd))
            rr = self.rp.request_rate(AGENT)
            if rr and rr.requests:
                d.append(rr.seconds / rr.requests)
        except (AttributeError, TypeError, ValueError):
            pass
        return max(d)

    def sitemaps(self):
        try:
            return list(self.rp.site_maps() or [])
        except AttributeError:
            return []


# ---- キャッシュ ----

class Cache:
    """条件つき GET のキャッシュ。URL ごとに <ホスト>/<xx>/<sha1>.json (記録) と .body または .body.gz (中身)"""

    def __init__(self, root):
        self.root = pathlib.Path(root)

    def _base(self, url):
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        host = re.sub(r"[^A-Za-z0-9.-]", "_", netloc_of(url)) or "_"
        return self.root / host / h[:2] / h

    def meta(self, url):
        b = self._base(url)
        try:
            return json.loads(b.with_name(b.name + ".json").read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def body_path(self, url, meta):
        if not meta or not meta.get("body"):
            return None
        p = self._base(url).with_name(meta["body"])
        return p if p.exists() else None

    def tmp(self, url):
        b = self._base(url)
        b.parent.mkdir(parents=True, exist_ok=True)
        return b.with_name(f"{b.name}.{threading.get_ident()}.part")

    def put(self, url, meta, tmp=None, compress=False):
        """記録を書く。tmp があれば中身にする (文字のものは gzip で縮める)。どちらも .part に書いてから置き換える"""
        b = self._base(url)
        b.parent.mkdir(parents=True, exist_ok=True)
        if tmp is not None:
            name = b.name + (".body.gz" if compress else ".body")
            dest = b.with_name(name)
            if compress:
                part = dest.with_name(f"{name}.{threading.get_ident()}.part")
                with open(tmp, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                _replace(part, dest)
                pathlib.Path(tmp).unlink(missing_ok=True)
            else:
                _replace(tmp, dest)
            other = b.with_name(b.name + (".body" if compress else ".body.gz"))
            other.unlink(missing_ok=True)                    # 前に別の形で置いた中身
            meta["body"] = name
        meta.setdefault("body", None)
        part = b.with_name(f"{b.name}.json.{threading.get_ident()}.part")
        part.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
        _replace(part, b.with_name(b.name + ".json"))


def _replace(src, dst, tries=5):
    """os.replace (Windows でほかのスレッドが読んでいる間は少し待ってやり直す)"""
    for i in range(tries):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * (i + 1))


class Result:
    """取得の結果。kind: ok / redirect / http_error / robots_disallowed / blocked / unreachable / too_large / timeout / error"""

    def __init__(self, url, kind, status=None, headers=None, size=None, sha256=None, body_file=None,
                 from_cache=None, error=None, fetched_at=None):
        self.url, self.kind, self.status = url, kind, status
        self.headers = headers or {}
        self.size, self.sha256, self.body_file = size, sha256, body_file
        self.from_cache, self.error, self.fetched_at = from_cache, error, fetched_at
        self.final_url, self.redirects = url, []
        self.forbidden = False                      # 確認ページでない 403 (そのページだけ。続けば get() がホストを止める)

    @property
    def ok(self):
        return self.kind == "ok"

    @property
    def content_type(self):
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    def body(self):
        if not self.body_file:
            return b""
        data = pathlib.Path(self.body_file).read_bytes()
        return gzip.decompress(data) if str(self.body_file).endswith(".gz") else data

    def head(self, n=1024):
        """中身の始めの n バイト (大きな PDF を全部読まないため)"""
        if not self.body_file:
            return b""
        opener = gzip.open if str(self.body_file).endswith(".gz") else open
        with opener(self.body_file, "rb") as f:
            return f.read(n)

    def raw_path(self):
        """圧縮していない中身のファイル (PDF など)。無ければ None"""
        return self.body_file if self.body_file and not str(self.body_file).endswith(".gz") else None

    def __repr__(self):
        return f"Result({self.kind} {self.status} {self.final_url})"


def cap_for(url, headers):
    ct = (headers.get("content-type") or "").lower()
    path = urllib.parse.urlsplit(url).path.lower()
    if "pdf" in ct or path.endswith(".pdf"):
        return CAP_PDF
    if "html" in ct or "xml" in ct:
        return CAP_HTML
    return CAP_OTHER


def _binary(url, headers):
    """縮めずに置くもの (PDF・Excel・zip。もともと縮んでいる)"""
    ct = (headers.get("content-type") or "").lower()
    path = urllib.parse.urlsplit(url).path.lower()
    return any(s in ct for s in ("pdf", "spreadsheet", "excel", "zip", "octet-stream", "image/")) or \
        path.endswith((".pdf", ".xlsx", ".xls", ".xlsm", ".zip"))


def challenge_kind(headers, head, status=None):
    """ボット対策の確認ページの印: "strong" (確かな印) / "weak" (弱い印だけ) / None (印なし)。
    head は本文の始め、status は HTTP の状態 (None は分からない)"""
    if (headers.get("cf-mitigated") or "").lower() == "challenge":
        return "strong"
    if (headers.get("x-amzn-waf-action") or "").lower() in ("challenge", "captcha"):
        return "strong"
    t = head[:100_000].decode("utf-8", "replace").lower()
    if any(m in t for m in CHALLENGE_STRONG):
        return "strong"
    if status is not None and status not in WEAK_STATUSES:
        return None                                   # ふつうの 200 や 404 には弱い印を見ない (差し込まれたスクリプトで止めない)
    if len(head) >= 15_000 or not any(m in t for m in CHALLENGE_WEAK):
        return None
    visible = re.sub(r"<(script|style)\b.*?</\1>|<[^>]*>", " ", t, flags=re.S)
    return "weak" if len(re.sub(r"\s+", "", visible)) < 200 else None


def is_challenge(headers, head, status=None):
    """ボット対策の確認ページか (強い印か弱い印)"""
    return challenge_kind(headers, head, status) is not None


def _folder(url):
    """数えるときのフォルダ: /a/b.pdf → /a/、/a/ → /a/ (フォルダの一覧そのもの)"""
    p = urllib.parse.urlsplit(url).path or "/"
    return p if p.endswith("/") else p.rsplit("/", 1)[0] + "/"


def _block_of(meta):
    """キャッシュの記録 → ("host", わけ) そのホストに断られた / ("url", わけ) そのページだけ 403 / (None, None)。
    block_level の無い前の版の記録は、「HTTP 403」をそのページだけの 403 とみなし、
    404 などに付いた弱い印 (「ボット対策の確認ページ (HTTP 404)」) は確認ページとみなさない"""
    if meta.get("forbidden"):
        return "url", meta["forbidden"]
    b = meta.get("blocked")
    if not b:
        return None, None
    if meta.get("block_level") == "host":
        return "host", b
    if b == "HTTP 403":
        return "url", b
    m = re.search(r"\(HTTP (\d+)\)$", b)
    if m and not (200 <= int(m.group(1)) < 300) and int(m.group(1)) not in WEAK_STATUSES:
        return None, None
    return "host", b


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def retry_after(v):
    """Retry-After (秒か HTTP の日時) → 秒。読めなければ None"""
    if v is None:
        return None
    v = str(v).strip()
    if v.isdigit():
        return int(v)
    try:
        d = email.utils.parsedate_to_datetime(v)
        return max(0, int(d.timestamp() - time.time()))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


class _HostStopped(Exception):
    """順番を待つあいだに、ほかのスレッドがそのホストを止めた"""


class Fetcher:
    """ホストごとの間隔・全体の同時接続・robots.txt・キャッシュを守って取る (スレッドから同時に呼んでよい)"""

    def __init__(self, cache_dir, gap=1.0, max_concurrency=16, timeout=30.0, retries=2, backoff=5.0,
                 max_age=72 * 3600, robots_max_age=24 * 3600, max_requests=None, read_deadline=300.0,
                 log_path=None, caps=None, offline=False, ip_gap=0.25, max_crawl_delay=MAX_CRAWL_DELAY,
                 ignore_robots=False):
        self.cache = Cache(cache_dir)
        self.offline = offline                      # True: キャッシュだけを使い、ネットには出ない (古さは問わない)
        self.ignore_robots = ignore_robots          # True: robots.txt の禁止と長い Crawl-delay を守らない (作者の判断。取ったものは記録する)
        self.gap, self.timeout, self.retries, self.backoff = gap, timeout, retries, backoff
        self.max_age, self.robots_max_age, self.max_requests = max_age, robots_max_age, max_requests
        self.read_deadline = read_deadline
        self.ip_gap, self.max_crawl_delay = ip_gap, max_crawl_delay
        self.caps = caps or {}                      # テスト用: {"html": バイト数, "pdf": ..., "other": ...}
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._lock = threading.Lock()
        self._host_locks, self._robots_locks = {}, {}
        self._robots = {}
        self._last, self._hold = {}, {}             # ホスト → 前の取得が終わった時、Retry-After で待つ時まで
        self._ips, self._ip_next = {}, {}           # ホスト → IP、IP → 次に取り始めてよい時
        self._inflight = {}                         # URL → [Lock, 待っている数] (同じ URL を同時に取らない)
        self._fails = Counter()
        self._net_down = set()                      # 成功をはさまずにつながらなかったホスト
        self._forbid = {}                           # ホスト → 続けて 403 (確認ページでない) だった URL
        self._local = threading.local()
        self.stopped = {}                           # netloc → {"kind": blocked/unreachable, "reason", "url"}
        self.hosts = {}                             # netloc → 数
        self.requests = 0
        self.halted = False
        self.halt_reason = None                     # max_requests / interrupted / network_down
        self._log = None
        if log_path:
            pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self._log = open(log_path, "a", encoding="utf-8")

    # ---- 数と記録 ----

    def _host(self, netloc):
        st = self.hosts.get(netloc)
        if st is None:
            st = self.hosts[netloc] = {"requests": 0, "robots": 0, "cache_fresh": 0, "not_modified": 0, "status": Counter(),
                                       "bytes": 0, "retries": 0, "errors": 0, "robots_disallowed": 0, "robots_rfc_only": 0,
                                       "robots_ignored": 0, "forbidden": 0, "wait_s": 0.0}
        return st

    def _count(self, key, n=1):
        """このスレッドの数 (counting の中だけ)"""
        c = getattr(self._local, "counter", None)
        if c is not None:
            c[key] += n

    def _stat(self, netloc, key, n=1):
        with self._lock:
            self._host(netloc)[key] += n

    def _write_log(self, rec):
        if self._log:
            with self._lock:
                self._log.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._log.flush()

    def host_stats(self):
        with self._lock:
            out = {}
            for h, st in sorted(self.hosts.items()):
                d = dict(st)
                d["status"] = dict(sorted((str(k), v) for k, v in st["status"].items()))
                d["wait_s"] = round(st["wait_s"], 1)
                if h in self.stopped:
                    d["stopped"] = self.stopped[h]
                out[h] = d
            return out

    @contextlib.contextmanager
    def counting(self):
        """このスレッドで取った数を数える (自治体ごとの数に使う)"""
        c = Counter()
        self._local.counter = c
        try:
            yield c
        finally:
            self._local.counter = None

    def halt(self, reason="interrupted"):
        self.halted = True
        self.halt_reason = self.halt_reason or reason

    def close(self):
        if self._log:
            self._log.close()
            self._log = None

    def _stop_host(self, netloc, kind, reason, url):
        with self._lock:
            if netloc not in self.stopped:
                self.stopped[netloc] = {"kind": kind, "reason": scrub(reason), "url": url}

    def _sleep(self, s):
        """止める指示 (halt) があればすぐ戻る sleep"""
        end = time.monotonic() + s
        while not self.halted:
            r = end - time.monotonic()
            if r <= 0:
                return
            time.sleep(min(r, 0.25))

    # ---- 間隔と同時接続 ----

    def _lock_for(self, table, key):
        with self._lock:
            lk = table.get(key)
            if lk is None:
                lk = table[key] = threading.Lock()
            return lk

    def _ip_of(self, netloc):
        """ホストの IP (共用のサーバーの間隔のため。引けなければ None で、間隔は見ない)"""
        with self._lock:
            if netloc in self._ips:
                return self._ips[netloc]
        host = urllib.parse.urlsplit("//" + netloc).hostname
        try:
            ip = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)[0][4][0]
        except (OSError, UnicodeError, IndexError, ValueError):
            return None                                     # 引けないものは覚えない (あとで引き直す)
        with self._lock:
            self._ips[netloc] = ip
        return ip

    @contextlib.contextmanager
    def _turn(self, netloc, gap):
        """そのホストの順番を待ち (前の取得が終わってから gap 秒、Retry-After の間)、同じ IP の取り始めの間を空け、
        全体の同時接続の枠を取る"""
        ip = self._ip_of(netloc) if self.ip_gap > 0 else None
        with self._lock_for(self._host_locks, netloc):
            if self.halted:
                raise Halted()
            now = time.monotonic()
            w = max(self._last.get(netloc, -1e9) + gap, self._hold.get(netloc, 0.0)) - now
            if w > 0:
                self._sleep(w)
            if netloc in self.stopped:                    # 待つあいだに止まった (ブロック・3 ページ続けての 403 など) なら取らない
                raise _HostStopped()
            if ip:
                with self._lock:
                    now = time.monotonic()
                    slot = max(now, self._ip_next.get(ip, 0.0))
                    self._ip_next[ip] = slot + self.ip_gap
                if slot > now:
                    self._sleep(slot - now)
                    w = max(w, 0.0) + slot - now
            with self._sem:
                with self._lock:
                    if self.halted or (self.max_requests is not None and self.requests >= self.max_requests):
                        if not self.halted:
                            self.halt_reason = self.halt_reason or "max_requests"
                        self.halted = True
                        raise Halted()
                    self.requests += 1
                    st = self._host(netloc)
                    st["requests"] += 1
                    st["wait_s"] += max(w, 0.0)
                self._count("requests")
                self._local.t_req = time.monotonic()             # 待ちを除いた取得の時間を測るため
                try:
                    yield
                finally:
                    self._last[netloc] = time.monotonic()

    @contextlib.contextmanager
    def _url_turn(self, url):
        """同じ URL を 2 つのスレッドが同時に取らない (あとのものは先のものを待ち、そのキャッシュを使う)"""
        with self._lock:
            ent = self._inflight.get(url)
            if ent is None:
                ent = self._inflight[url] = [threading.Lock(), 0]
            ent[1] += 1
        try:
            with ent[0]:
                yield
        finally:
            with self._lock:
                ent[1] -= 1
                if ent[1] == 0:
                    self._inflight.pop(url, None)

    # ---- 1 回の取得 ----

    def _once(self, url, headers, cap):
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = OPENER.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            hdr = {k.lower(): v for k, v in (e.headers or {}).items()}
            peek, undecodable = b"", False
            try:
                if e.code not in REDIRECT_CODES and e.code != 304:
                    peek = e.read(64 * 1024)
            except Exception:
                undecodable = e.code in WEAK_STATUSES      # 403・429・503・202 の本文を読めない = 確認ページか分からない
            finally:
                e.close()
            enc = (hdr.get("content-encoding") or "").lower().strip()
            if peek and enc in ("gzip", "x-gzip"):
                try:                                      # 途中までの gzip でもほどける分だけ
                    peek = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(peek, 200_000)
                except zlib.error:
                    peek, undecodable = b"", True
            elif peek and enc not in ("", "identity"):
                undecodable = True                        # br など。中身を見られない
            return {"status": e.code, "headers": hdr, "peek": peek, **({"undecodable": True} if undecodable else {})}
        with resp:
            hdr = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status
            cap = cap or self._cap(url, hdr)
            clen = _int(hdr.get("content-length"))
            if clen is not None and clen > cap:
                return {"status": status, "headers": hdr, "too_large": True, "size": clen}
            stream = gzip.GzipFile(fileobj=resp) if (hdr.get("content-encoding") or "").lower().strip() in ("gzip", "x-gzip") else resp
            tmp = self.cache.tmp(url)
            h, n, deadline = hashlib.sha256(), 0, time.monotonic() + self.read_deadline
            try:
                with open(tmp, "wb") as f:
                    while True:
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        n += len(chunk)
                        if n > cap:
                            raise _TooLarge()
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"読み込みが {self.read_deadline} 秒を超えた")
                        h.update(chunk)
                        f.write(chunk)
            except _TooLarge:
                tmp.unlink(missing_ok=True)
                return {"status": status, "headers": hdr, "too_large": True, "size": clen}
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return {"status": status, "headers": hdr, "tmp": tmp, "size": n, "sha256": h.hexdigest()}

    def _cap(self, url, hdr):
        c = cap_for(url, hdr)
        key = {CAP_PDF: "pdf", CAP_HTML: "html"}.get(c, "other")
        return self.caps.get(key, c)

    def _request(self, url, meta, gap, cap=None):
        """1 つの URL を取る (取り直しつき)。返すのは生の結果 (dict)。status が None ならつながらなかった"""
        netloc = netloc_of(url)
        headers = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "ja,en;q=0.5", "Accept": "*/*"}
        if meta and meta.get("status") == 200 and self.cache.body_path(url, meta):
            if meta.get("headers", {}).get("etag"):
                headers["If-None-Match"] = meta["headers"]["etag"]
            if meta.get("headers", {}).get("last-modified"):
                headers["If-Modified-Since"] = meta["headers"]["last-modified"]
        last, delay = None, 0.0
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(delay)
                self._stat(netloc, "retries")
            t0 = time.monotonic()
            err = None
            try:
                with self._turn(netloc, gap):
                    raw = self._once(url, headers, cap)
            except Halted:
                raise
            except _HostStopped:
                s = self.stopped.get(netloc) or {}
                return {"status": None, "error_kind": "stopped", "stopped_kind": s.get("kind", "blocked"),
                        "error": s.get("reason", "止めたホスト")}
            except urllib.error.URLError as e:
                r = e.reason
                kind = "timeout" if isinstance(r, (socket.timeout, TimeoutError)) else \
                    "ssl" if isinstance(r, ssl.SSLError) else "dns" if isinstance(r, socket.gaierror) else "conn"
                err = {"status": None, "error_kind": kind, "error": scrub(str(r))[:200]}
            except (socket.timeout, TimeoutError) as e:
                err = {"status": None, "error_kind": "timeout", "error": scrub(str(e))[:200] or "timeout"}
            except ssl.SSLError as e:
                err = {"status": None, "error_kind": "ssl", "error": scrub(str(e))[:200]}
            except (OSError, EOFError, zlib.error, http.client.HTTPException, ValueError) as e:
                err = {"status": None, "error_kind": "conn", "error": _err_text(e)}
            ms = int((time.monotonic() - max(t0, getattr(self._local, "t_req", t0))) * 1000)
            if err:
                last = err
                self._stat(netloc, "errors")
                self._write_log({"t": iso(), "url": url, "error": f"{err['error_kind']}: {err['error']}", "ms": ms, "attempt": attempt})
                if err["error_kind"] == "ssl":
                    break                                       # 証明書の誤りは取り直しても同じ
                delay = self.backoff * (2 ** attempt)
                continue
            last = raw
            with self._lock:
                st = self._host(netloc)
                st["status"][raw["status"]] += 1
                st["bytes"] += (raw.get("size") or 0) if raw.get("tmp") else 0
            self._write_log({"t": iso(), "url": url, "status": raw["status"], "ms": ms, "bytes": raw.get("size"),
                             "attempt": attempt, **({"note": "too_large"} if raw.get("too_large") else {})})
            if raw["status"] in RETRY_CODES:
                if is_challenge(raw["headers"], raw.get("peek") or b"", raw["status"]):
                    break                                       # 503 の確認ページは取り直さない (ブロックとして扱う)
                ra = retry_after(raw["headers"].get("retry-after"))
                if ra is not None and ra > MAX_RETRY_AFTER:
                    self._stop_host(netloc, "unreachable", f"HTTP {raw['status']}、Retry-After {ra} 秒 (長いのでこの実行では取らない)", url)
                    break
                if ra:
                    with self._lock:                            # Retry-After はそのホスト全体に守る
                        self._hold[netloc] = max(self._hold.get(netloc, 0.0), time.monotonic() + ra)
                if attempt < self.retries:
                    delay = min(max(ra or 0, self.backoff * (2 ** attempt)), float(MAX_RETRY_AFTER))
                    continue
            break
        status = (last or {}).get("status")
        bad = status is None or status >= 500
        with self._lock:
            if bad:
                self._fails[netloc] += 1
            else:
                self._fails[netloc] = 0
            fails = self._fails[netloc]
            if status is None:                                  # こちらのネットが落ちたか (多くのホストで続けてつながらない)
                self._net_down.add(netloc)
                if len(self._net_down) >= NET_DOWN_HOSTS and not self.halted:
                    self.halted, self.halt_reason = True, "network_down"
            else:
                self._net_down.clear()
        kind = (last or {}).get("error_kind")
        if bad and (fails >= GIVE_UP_AFTER or kind == "ssl"):
            why = f"{kind}: {(last or {}).get('error')}" if status is None else f"HTTP {status} が続いた"
            self._stop_host(netloc, "unreachable", why, url)
        return last or {"status": None, "error_kind": "conn", "error": "?"}

    # ---- 結果の読み方 ----

    def _from_meta(self, url, meta, from_cache):
        st = meta.get("status")
        hdr = meta.get("headers") or {}
        netloc = netloc_of(url)
        level, why = _block_of(meta)
        if level == "host":
            self._stop_host(netloc, "blocked", why, url)
            return Result(url, "blocked", st, hdr, error=why, from_cache=from_cache, fetched_at=meta.get("fetched_at"))
        if level == "url":
            # 確認ページでない 403 は、そのページだけ (フォルダの一覧の禁止など)。続いたかどうかは get() が数える。
            # robots.txt は get() を通らないので数えない (RFC 9309: 403 の robots.txt は規則なし)
            r = Result(url, "http_error", 403, hdr, error=f"{why} (このページだけ)", from_cache=from_cache,
                       fetched_at=meta.get("fetched_at"))
            r.forbidden = True
            return r
        if st in REDIRECT_CODES and hdr.get("location"):
            return Result(url, "redirect", st, hdr, from_cache=from_cache, fetched_at=meta.get("fetched_at"))
        if st is not None and 200 <= st < 300:
            if meta.get("too_large"):
                return Result(url, "too_large", st, hdr, size=meta.get("size"), from_cache=from_cache, fetched_at=meta.get("fetched_at"))
            return Result(url, "ok", st, hdr, size=meta.get("size"), sha256=meta.get("sha256"),
                          body_file=self.cache.body_path(url, meta), from_cache=from_cache, fetched_at=meta.get("fetched_at"))
        return Result(url, "http_error", st, hdr, error=f"HTTP {st}", from_cache=from_cache, fetched_at=meta.get("fetched_at"))

    def _interpret(self, url, raw, meta):
        netloc = netloc_of(url)
        if raw.get("error_kind") == "stopped":
            return Result(url, raw.get("stopped_kind") or "blocked", error=raw.get("error"))
        if raw.get("status") is None:
            kind = "timeout" if raw.get("error_kind") == "timeout" else "error"
            if netloc in self.stopped and self.stopped[netloc]["kind"] == "unreachable":
                kind = "unreachable"
            return Result(url, kind, error=f"{raw.get('error_kind')}: {raw.get('error')}")
        st, hdr = raw["status"], raw["headers"]
        now = time.time()
        new = {"url": url, "status": st, "headers": {k: hdr[k] for k in KEEP_HEADERS if k in hdr},
               "checked": now, "fetched_at": iso(now)}
        if st == 304 and meta and meta.get("body"):
            meta["checked"], meta["revalidated_at"] = now, iso(now)
            self.cache.put(url, meta)
            self._stat(netloc, "not_modified")
            return self._from_meta(url, meta, "304")
        if st in REDIRECT_CODES and hdr.get("location"):
            self.cache.put(url, new)
            return self._from_meta(url, new, None)
        head = raw.get("peek") or b""
        if raw.get("tmp"):
            with open(raw["tmp"], "rb") as f:
                head = f.read(100_000)
        ct = (hdr.get("content-type") or "").lower()
        waf = (hdr.get("x-amzn-waf-action") or "").lower() in ("challenge", "captcha")
        ck = "strong" if (hdr.get("cf-mitigated") or "").lower() == "challenge" or waf else \
            challenge_kind(hdr, head, st) if ("html" in ct or not ct) else None
        if st in BLOCK_CODES or ck:
            if st == 429 or ck == "strong" or (ck == "weak" and st != 403) or raw.get("undecodable"):
                # 429・確かな確認ページ・弱い印の 503 などは、そのホストに断られた (止める)
                new["blocked"] = f"ボット対策の確認ページ (HTTP {st})" if ck else \
                    f"HTTP {st} (中身を読めない)" if raw.get("undecodable") else f"HTTP {st}"
                new["block_level"] = "host"
            else:
                # 確認ページでない 403 (弱い印だけのものも) は、そのページだけ (_from_meta が続けば止める)
                new["forbidden"] = "HTTP 403" + (" (弱い印あり)" if ck else "")
            if raw.get("tmp"):
                pathlib.Path(raw["tmp"]).unlink(missing_ok=True)
            self.cache.put(url, new)
            return self._from_meta(url, new, None)
        if 200 <= st < 300:
            if raw.get("too_large"):
                new.update(too_large=True, size=raw.get("size"))
                self.cache.put(url, new)
                return self._from_meta(url, new, None)
            new.update(size=raw["size"], sha256=raw["sha256"])
            self.cache.put(url, new, tmp=raw["tmp"], compress=not _binary(url, hdr))
            return self._from_meta(url, new, None)
        if raw.get("tmp"):
            pathlib.Path(raw["tmp"]).unlink(missing_ok=True)
        if st < 500:
            self.cache.put(url, new)                     # 404 なども覚える (新しいうちは取りに行かない)
        return self._from_meta(url, new, None)

    # ---- robots.txt ----

    def robots(self, url):
        """url のホストの robots.txt (Robots)。ホストごとに 1 回だけ取る"""
        sp = urllib.parse.urlsplit(url)
        key = f"{sp.scheme}://{sp.netloc}"
        with self._lock:
            r = self._robots.get(key)
        if r is not None:
            return r
        with self._lock_for(self._robots_locks, key):
            with self._lock:
                r = self._robots.get(key)
            if r is None:
                r = self._fetch_robots(key)
                with self._lock:
                    self._robots[key] = r
            return r

    def _fetch_robots(self, key):
        url, netloc = key + "/robots.txt", urllib.parse.urlsplit(key).netloc
        for _ in range(MAX_REDIRECTS + 1):
            nl = netloc_of(url)
            if nl in self.stopped:
                return Robots(unavailable=self.stopped[nl]["kind"], note=self.stopped[nl]["reason"])
            meta = self.cache.meta(url)
            if meta and (self.offline or time.time() - meta.get("checked", 0) < self.robots_max_age):
                self._stat(netloc, "cache_fresh")
                r = self._from_meta(url, meta, "fresh")
            elif self.offline:
                return Robots(unavailable="unreachable", note="キャッシュに無い (--offline)", offline_miss=True)
            else:
                self._stat(netloc, "robots")
                r = self._interpret(url, self._request(url, meta, self.gap, cap=CAP_ROBOTS), meta)
            if r.kind == "redirect":
                new = norm_url(urllib.parse.urljoin(url, r.headers.get("location", "")))
                if not new or new == url:
                    return Robots(allow_all=True, note="robots.txt の転送がおかしい")
                url = new
                continue
            if r.kind == "ok":
                text = r.body()[:CAP_ROBOTS].decode("utf-8", "replace").lstrip("﻿")
                if re.match(r"\s*<", text) or "<html" in text[:2000].lower():
                    return Robots(allow_all=True, note="robots.txt の代わりに HTML が返った (規則なしとみなす)")
                return Robots(text)
            if r.kind == "blocked":
                self._stop_host(netloc, "blocked", f"robots.txt が {r.error}", url)
                return Robots(unavailable="blocked", note=r.error)
            if r.kind == "too_large":
                return Robots(disallow_all=True, note="robots.txt が 512KB を超える (読めないので全部禁止とみなす)")
            if r.kind == "http_error" and r.status is not None:
                if r.status == 401:
                    return Robots(disallow_all=True, note="robots.txt が 401 (全部禁止とみなす)")
                if 400 <= r.status < 500:
                    return Robots(allow_all=True, note=f"robots.txt が {r.status} (規則なし)")
                self._stop_host(netloc, "unreachable", f"robots.txt が HTTP {r.status}", url)
                return Robots(unavailable="robots_5xx", note=f"robots.txt が HTTP {r.status} (RFC 9309: 全部禁止とみなす)")
            self._stop_host(netloc, "unreachable", f"robots.txt を取れない ({r.error})", url)
            return Robots(unavailable="unreachable", note=r.error)
        return Robots(allow_all=True, note="robots.txt の転送が 5 回を超えた (規則なし)")

    # ---- 取る ----

    def get(self, url, max_age=None, cap=None):
        """url を取る (転送は 5 回まで自分で追う)。返すのは Result。投げる例外は Halted だけ"""
        url0, chain, live = url, [], False
        for _ in range(MAX_REDIRECTS + 1):
            try:
                r = self._get_one(url, max_age, cap)
                live = live or (r.from_cache in (None, "304") and r.status is not None)   # 転送のどこかでネットに出たか
            except Halted:
                raise
            except Exception as e:                       # キャッシュに書けないなど。1 つの URL の失敗で自治体ごと落とさない
                self._write_log({"t": iso(), "url": url, "error": _err_text(e)})
                r = Result(url0, "error", error=_err_text(e))
                r.final_url, r.redirects = url, chain
                return r
            if r.kind != "redirect":
                r.url, r.final_url, r.redirects = url0, url, chain
                return self._account_403(r, url0, url, live)
            new = norm_url(urllib.parse.urljoin(url, r.headers.get("location", "")))
            chain.append(url)
            if not new or new in chain:
                r = Result(url0, "error", r.status, error=LOOP_ERRORS[0])
                r.final_url, r.redirects = url, chain
                return self._account_403(r, url0, url, live)
            url = new
        r = Result(url0, "error", error=LOOP_ERRORS[1])
        r.final_url, r.redirects = url, chain
        return self._account_403(r, url0, url, live)

    def _account_403(self, r, url0, final, live):
        """断られた答えを数える: 確認ページでない 403、別のホストへの転送の先でのブロック (止めたホストを含む)、転送のループ・長すぎる転送。
        この取得でネットに出たときだけ数え (転送のどこかで出ていれば、最後の答えがキャッシュでも数える)、頼んだ URL で数える
        (転送先が同じ /denied でも別に数える)。数え直すのは、最後の答えそのものがネットから来た断られていない答えのときだけ
        (転送やキャッシュでは数え直さない)。同じホストで FORBID_STRIKES の別のフォルダか FORBID_URLS ページ続いたら、そのホストを止める
        (403 なら最後のホストと頼んだホストの両方、ほかは頼んだホスト)"""
        fh, oh = netloc_of(final), netloc_of(url0)
        cross_block = r.kind == "blocked" and fh != oh
        loop = r.kind == "error" and r.error in LOOP_ERRORS
        if not (r.forbidden or cross_block or loop):
            if r.from_cache in (None, "304") and r.status is not None and r.status < 500:
                with self._lock:
                    self._forbid.pop(fh, None)                  # 断られていない答えが来たら数え直す
            return r
        if not live:
            return r
        hosts = {fh, oh} if r.forbidden else {oh}
        stop, n = False, 0
        with self._lock:
            if r.forbidden:
                self._host(fh)["forbidden"] += 1
            for h in hosts:
                st = self._forbid.setdefault(h, {"urls": set(), "dirs": set()})
                st["urls"].add(url0)
                st["dirs"].add(_folder(url0))
                n = max(n, len(st["urls"]))
                stop = stop or len(st["dirs"]) >= FORBID_STRIKES or len(st["urls"]) >= FORBID_URLS
        if not stop:
            return r
        why = f"断られた答え (403・転送の先のブロックなど) が {n} ページ続いた"
        for h in hosts:
            self._stop_host(h, "blocked", why, final)
        b = Result(url0, "blocked", r.status, r.headers, error=why, from_cache=r.from_cache, fetched_at=r.fetched_at)
        b.final_url, b.redirects = r.final_url, r.redirects
        return b

    def _get_one(self, url, max_age, cap):
        netloc = netloc_of(url)
        if netloc in self.stopped:
            s = self.stopped[netloc]
            return Result(url, s["kind"], error=s["reason"])
        rob = self.robots(url)
        if rob.unavailable:
            if rob.offline_miss:                               # --offline で robots.txt がキャッシュに無い = その URL が無いのと同じ
                self._count("offline_miss")                    # (つながらないとして、サイトごと止めない)
                return Result(url, "error", error=rob.note)
            kind = "blocked" if rob.unavailable == "blocked" else "unreachable"
            return Result(url, kind, error=rob.note)
        override = []                                          # --ignore-robots で robots.txt に反して取るわけ
        if not rob.allowed(url):
            if not self.ignore_robots:
                self._stat(netloc, "robots_disallowed")
                self._write_log({"t": iso(), "url": url, "skipped": "robots.txt で禁止"})
                return Result(url, "robots_disallowed", error="robots.txt で禁止")
            override.append("robots.txt で禁止")
        if rob.rp_disagrees(url):
            self._stat(netloc, "robots_rfc_only")              # robotparser (始めに当たった規則) では禁止。RFC 9309 に従って取る
        delay = rob.delay()
        if delay > self.max_crawl_delay:
            if not self.ignore_robots:
                self._stat(netloc, "robots_disallowed")
                self._write_log({"t": iso(), "url": url, "skipped": f"Crawl-delay {delay:g} 秒"})
                return Result(url, "robots_disallowed",
                              error=f"robots.txt の Crawl-delay が {delay:g} 秒 ({self.max_crawl_delay:g} 秒を超えるので取らない)")
            override.append(f"Crawl-delay {delay:g} 秒 ({self.max_crawl_delay:g} 秒まで守った)")
            delay = self.max_crawl_delay                       # 守りきれない長さは、上限の秒数まで守る
        gap = max(self.gap, delay)
        max_age = self.max_age if max_age is None else max_age
        with self._url_turn(url):
            meta = self.cache.meta(url)
            if meta and time.time() - meta.get("checked", 0) < max_age and (meta.get("body") or meta.get("status") != 200
                                                                             or meta.get("too_large") or meta.get("blocked")):
                self._stat(netloc, "cache_fresh")
                return self._from_meta(url, meta, "fresh")
            if self.offline:
                if meta and (meta.get("body") or meta.get("status") != 200 or meta.get("too_large") or meta.get("blocked")):
                    self._stat(netloc, "cache_fresh")
                    return self._from_meta(url, meta, "fresh")
                self._count("offline_miss")
                return Result(url, "error", error="キャッシュに無い (--offline)")
            r = self._interpret(url, self._request(url, meta, gap, cap), meta)
        if override and r.status is not None:                  # 取りに行った (答えが来た) ものだけ数える
            self._stat(netloc, "robots_ignored")
            self._count("robots_ignored")
            self._write_log({"t": iso(), "url": url, "robots_ignored": "、".join(override)})
        return r
