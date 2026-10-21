"""テストの共通の道具: import の道すじと、キャッシュ (data/ の下。git に入らない) があるか"""
import pathlib, sys

TESTS = pathlib.Path(__file__).resolve().parent
LLMREAD = TESTS.parent
ROOT = LLMREAD.parent.parent
for p in (ROOT / "nationwide" / "discover", LLMREAD):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CACHE = ROOT / "data" / "discover" / "cache"
FIXTURES = TESTS / "fixtures"


def cached(url):
    """キャッシュにある中身 (bytes)。無ければ None (そのテストは飛ばす)"""
    import fetch_docs
    return fetch_docs.cached(url, CACHE)


def fixture(name):
    return (FIXTURES / name).read_text("utf-8")
