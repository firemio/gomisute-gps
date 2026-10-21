"""llmread の共通: 道すじと、使い回すモジュールの読み込み (このフォルダ・nationwide/discover の fetcher・bench)"""
import datetime as dt, hashlib, importlib.util, json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (HERE, ROOT / "nationwide" / "discover"):
    if str(p) not in sys.path:
        sys.path.insert(1, str(p))

DATA = ROOT / "data" / "llmread"          # 入力 (画像・文字) と答えの生の記録。git に入れない
OUT = ROOT / "out" / "llmread"            # 計画・試走の結果 (git に入れる。家のフォルダの道すじは伏せる)
INPUTS = DATA / "inputs"
ANSWERS = DATA / "answers"


def bench_cli():
    """bench/cli_bench.py (環境変数の外し方 DROP_ENV と redact) を、bench/score.py とぶつからないように読み込む"""
    spec = importlib.util.spec_from_file_location("cli_bench", ROOT / "bench" / "cli_bench.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha256(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), "utf-8")
    os.replace(tmp, path)


def read_json(path):
    return json.loads(pathlib.Path(path).read_text("utf-8"))


def say(*a):
    print(*a, file=sys.stderr, flush=True)
