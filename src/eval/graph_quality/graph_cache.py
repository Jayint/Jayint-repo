"""Graph cache — mint real graphs ONCE under Docker; check them offline forever
after (design spec §5.1, plan Task 5).

WHY: `build_graph_construction_only` (`src/eval/language_package_eval/coverage.py`,
`build_graph_construction_only`) opens a `DockerExecutor`. Real graphs CANNOT be
built offline — there is no way around a real container for a real install. So
this module's `mint()` runs Docker exactly once, per repo, and commits the
resulting `DepGraph.to_dict()` JSON to `src/eval/graph_quality/graphs/<repo>.json`.
Every check downstream (`block_parity.py`) loads that committed JSON via
`load_graphs()` and never touches Docker, the network, or an LLM again. This
mirrors what `src/eval/package_installability/answer_keys.json` already does for
its own corpus: pay the Docker cost once, check it in, grade offline forever.

CORPUS: the 16 repos already cloned under `outputs/build_script_eval/_smoke/`
(click, cryptography, flask, httpx, jinja, lxml, pillow, psycopg2, pygraphviz,
python-dotenv, python-semantic-release, pyyaml, pyzmq, requests, rich, typer).
These are the right ones: they include the native-build repos (psycopg2,
pygraphviz, lxml, pillow, cryptography) whose graphs actually carry
SystemLib/Tool nodes worth checking in `block_parity.py`. Nothing here clones a
new repo — `mint()` only ever reads directories that are already on disk.

A repo that fails to mint (network hiccup, a repo this pipeline doesn't yet
support, an image pull failure) is a FINDING to report, not a reason to abort
the other 15 — `mint()` catches per-repo, bounds it with a wall-clock timeout,
and keeps going.

🔴 MINT INCREMENTALLY AND RESUMABLY. Each repo's JSON is written the moment that
repo succeeds, and a repo whose JSON already exists is SKIPPED (unless `force`).
Holding 16 graphs in memory and writing at the end means a crash on repo 5 costs
all four already-paid-for Docker builds; this way a crash costs one repo. These
builds are real: several of the 16 (cryptography, lxml, pillow, pyzmq) resolve
and compile native extensions, and the whole pass is tens of minutes.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.schema import DepGraph  # noqa: E402
from src.eval.language_package_eval.coverage import (  # noqa: E402
    base_image_for_repo, build_graph_construction_only,
)

_DEFAULT_SMOKE_ROOT = str(_REPO_ROOT / "outputs" / "build_script_eval" / "_smoke")
_DEFAULT_OUT_DIR = str(Path(__file__).resolve().parent / "graphs")

# Wall-clock bound per repo. A construction pass that has not finished in this long is
# wedged (a hung `docker exec`, a resolver stuck on a network read) -- bound it, record
# it as a FINDING for that repo, and move on to the other fifteen.
_DEFAULT_TIMEOUT_S = 1800


class MintTimeout(Exception):
    """`build_graph_construction_only` exceeded this repo's wall-clock budget."""


@contextmanager
def _time_limit(seconds: int):
    """SIGALRM-bounded block. The construction pass spends nearly all its time blocked
    in `subprocess.run` (docker exec), which SIGALRM interrupts cleanly; the
    `DockerExecutor`'s own `__exit__` then still runs and force-removes the container,
    so a timed-out repo does not leak a container into the next one."""
    def _fire(_signum, _frame):
        raise MintTimeout(f"exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def mint(smoke_root: str = _DEFAULT_SMOKE_ROOT, out_dir: str = _DEFAULT_OUT_DIR, *,
         force: bool = False, timeout_s: int = _DEFAULT_TIMEOUT_S) -> dict[str, str]:
    """Build each repo under `smoke_root` into a real `DepGraph` (Docker, once) and
    write it to `<out_dir>/<repo>.json` AS SOON AS THAT REPO SUCCEEDS. Returns
    `{repo: "ok" | "skipped (cached)" | "<ExceptionType>: <msg>"}`.

    RESUMABLE: a repo whose JSON already exists is skipped unless `force`. INCREMENTAL:
    each success is flushed to disk immediately, so a crash costs one repo, not the run.
    BOUNDED: each repo gets `timeout_s` wall-clock; a wedged one is recorded and skipped.

    One repo's crash must never abort the corpus -- 16 independent containers, so a
    single bad one (an image pull failure, an unsupported build system, a genuine
    construction bug) is caught, recorded under its own name, and every other repo
    still gets its shot. That is a finding to report, not something to hide.
    """
    smoke = Path(smoke_root)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for repo_dir in sorted(p for p in smoke.iterdir() if p.is_dir()):
        repo = repo_dir.name
        target = out / f"{repo}.json"
        if target.is_file() and not force:
            status[repo] = "skipped (cached)"
            print(f"SKIP {repo} (already cached)", flush=True)
            continue
        try:
            image, minor, _reason = base_image_for_repo(repo_dir)
            print(f"MINT {repo} image={image} py={minor}", flush=True)
            with _time_limit(timeout_s):
                graph = build_graph_construction_only(str(repo_dir), image, minor)
        except Exception as exc:  # noqa: BLE001 — one repo's crash must not abort the corpus
            status[repo] = f"{type(exc).__name__}: {exc}"
            print(f"FAIL {repo}: {status[repo]}", flush=True)
            continue
        payload = json.dumps(graph.to_dict(), indent=2, sort_keys=True) + "\n"
        target.write_text(payload, encoding="utf-8")   # flush THIS repo before the next
        status[repo] = "ok"
        print(f"OK   {repo}: {len(graph.nodes)} nodes, {len(graph.edges)} edges", flush=True)
    return status


def load_graphs(out_dir: str = _DEFAULT_OUT_DIR) -> dict[str, DepGraph]:
    """Offline: load every committed `<repo>.json` under `out_dir` into a `DepGraph`
    via `DepGraph.from_dict` (T1). No Docker, no network — pure file I/O + parsing."""
    out = Path(out_dir)
    graphs: dict[str, DepGraph] = {}
    for path in sorted(out.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        graphs[path.stem] = DepGraph.from_dict(data)
    return graphs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mint", action="store_true",
                     help="Docker: build the real graphs and write them to --out-dir "
                          "(resumable: already-cached repos are skipped)")
    ap.add_argument("--force", action="store_true", help="re-mint even already-cached repos")
    ap.add_argument("--timeout-s", type=int, default=_DEFAULT_TIMEOUT_S)
    ap.add_argument("--smoke-root", default=_DEFAULT_SMOKE_ROOT)
    ap.add_argument("--out-dir", default=_DEFAULT_OUT_DIR)
    args = ap.parse_args(argv)

    if args.mint:
        status = mint(args.smoke_root, args.out_dir,
                      force=args.force, timeout_s=args.timeout_s)
        ok = sorted(r for r, s in status.items() if s in ("ok", "skipped (cached)"))
        failed = sorted(r for r, s in status.items() if s not in ("ok", "skipped (cached)"))
        print(f"\ncached {len(ok)}/{len(status)}")
        for repo in ok:
            print(f"  OK   {repo}  ({status[repo]})")
        for repo in failed:
            print(f"  FAIL {repo}: {status[repo]}")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
