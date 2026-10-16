"""汎用フェッチャ: python fetch.py <url> <outfile> [--links]"""
import sys, re, pathlib, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (gomisute-gps research)"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get_content_charset()

def main():
    url, out = sys.argv[1], pathlib.Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    body, cs = get(url)
    out.write_bytes(body)
    print(f"saved {out} {len(body)} bytes charset={cs}")
    if "--links" in sys.argv:
        text = body.decode(cs or "utf-8", errors="replace")
        for m in sorted(set(re.findall(r'href="([^"]+)"', text))):
            if re.search(r"\.(zip|csv|xlsx?|pdf|json|geojson|shp|kml)(\?|$)", m, re.I):
                print(urllib.parse.urljoin(url, m))

if __name__ == "__main__":
    main()
