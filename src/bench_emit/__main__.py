from __future__ import annotations

import argparse

from src.bench_emit.emit import emit_run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.bench_emit")
    ap.add_argument("--run", required=True, help="run_root containing output/<owner>/<repo>")
    ap.add_argument("--agent", required=True, choices=["v3", "repo2run", "rat"])
    ap.add_argument("--dest", required=True, help="destination harvest tree root")
    a = ap.parse_args(argv)

    results = emit_run(a.run, a.agent, a.dest)
    n_ok = sum(1 for _, status in results if status == "ok")
    for full_name, status in results:
        print(f"{status:8} {full_name}")
    print(f"\n{n_ok}/{len(results)} ok  ->  {a.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
