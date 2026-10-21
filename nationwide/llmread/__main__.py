"""python -m nationwide.llmread <コマンド> (リポジトリの一番上で動かす)

  plan・prep・smoke       → run.py (計画・入力づくり・試走)
  ask・report・score      → full.py (本番で聞く・まとめる・モデルを呼ばずに採点を作り直す)

  python -m nationwide.llmread score --check   # out/llmread/raw/ の生の答えから採点を作り直し、今のものと同じか確かめる
"""
import pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import full, run                                                  # noqa: E402


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] in ("ask", "report", "score"):
        return full.main(argv)
    return run.main(argv)


if __name__ == "__main__":
    sys.exit(main())
