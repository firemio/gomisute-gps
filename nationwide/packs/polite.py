"""データを取りに行くときの礼儀 (build_packs.py --fetch-missing と golden.py の gps2city で使う)

  - robots.txt を読んで守る (urllib.robotparser。ホストごとに1回だけ取って覚える)
  - 1ホストに 1秒に1回まで (robots.txt に Crawl-delay・Request-rate があれば、そのうち長い方)
  - User-Agent で名乗る
  - 別のホストへ移る (リダイレクト) ときも、移った先の robots.txt と間隔を守る

robots.txt が 404 などなら「規則なし」、401・403 なら全部禁止 (robotparser.read() と同じ)。
5xx・つながらないときは全部禁止とみなして止める (RFC 9309。覚えないので、次に呼ぶとまた取りに行く)。
"""
import time, urllib.error, urllib.parse, urllib.request, urllib.robotparser

UA = "gomisute-gps (+https://github.com/firemio/gomisute-gps)"
AGENT = "gomisute-gps"            # robots.txt の User-agent と照らし合わせる名前
GAP = 1.0                         # 1ホストに何秒に1回まで

_last = {}                        # ホスト → 最後に取りに行った時刻
_robots = {}                      # ホスト → RobotFileParser


class RobotsDisallowed(PermissionError):
    pass


def _wait(host, gap=GAP):
    w = gap - (time.time() - _last.get(host, 0))
    if w > 0: time.sleep(w)
    _last[host] = time.time()


def robots(url):
    """url のホストの robots.txt (ホストごとに1回だけ取る)"""
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    if host in _robots: return _robots[host]
    rp = urllib.robotparser.RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    _wait(host)
    try:
        with urllib.request.urlopen(urllib.request.Request(rp.url, headers={"User-Agent": UA}), timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
            html = "html" in (r.headers.get("Content-Type") or "").lower()
        if html: rp.allow_all = True          # robots.txt の代わりに HTML の案内ページを返すサーバーがある → 規則なし
        else: rp.parse(text.splitlines())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403): rp.disallow_all = True
        elif 400 <= e.code < 500: rp.allow_all = True
        else: raise RobotsDisallowed(f"robots.txt を読めない (HTTP {e.code}) ので取りに行かない: {rp.url}") from e
    except urllib.error.URLError as e:
        raise RobotsDisallowed(f"robots.txt を読めない ({e.reason}) ので取りに行かない: {rp.url}") from e
    _robots[host] = rp
    return rp


def gap(url):
    """そのホストに次に取りに行くまでの秒数 (1秒か、robots.txt の Crawl-delay・Request-rate の長い方)"""
    rp = robots(url)
    g = GAP
    d = rp.crawl_delay(AGENT)
    if d: g = max(g, float(d))
    rr = rp.request_rate(AGENT)
    if rr and rr.requests: g = max(g, rr.seconds / rr.requests)
    return g


def check(url):
    """robots.txt で許されているかを確かめ、間隔を空ける (許されていなければ RobotsDisallowed)"""
    if not robots(url).can_fetch(AGENT, url): raise RobotsDisallowed(f"robots.txt で禁止されている: {url}")
    _wait(urllib.parse.urlsplit(url).netloc.lower(), gap(url))


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check(newurl)                          # 移った先のホストの robots.txt と間隔も守る
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirect)


def urlopen(url, data=None, headers=None, method=None, timeout=300):
    """urllib.request.urlopen と同じ (返すのは応答)。robots.txt・間隔・User-Agent を守る"""
    check(url)
    return _opener.open(urllib.request.Request(url, data=data, headers={**(headers or {}), "User-Agent": UA}, method=method), timeout=timeout)


def get(url, timeout=300):
    """GET して本文 (bytes) を返す"""
    with urlopen(url, timeout=timeout) as r: return r.read()
