"""Gate A — cure-recovery measurement (config-lane go/no-go).

A THIN driver over existing infrastructure — it builds no new container harness.
Per corpus repo it:

  1. PROVISION  fetch + ``git reset --hard`` the repo to its pinned commit into
                ``work/<full_name with / -> __>``.
  2. RENDER     shell out to ``scripts/run_v3_e2e.py`` to render the config-lane
                ``setup.sh`` (the ``pip install -e .`` editable capstone included).
  3. BASELINE   derive the no-editable arm by stripping exactly that capstone line
                from the rendered script (``_strip_editable``) — this isolates the
                editable install's contribution.
  4. REPLAY     run BOTH arms through ``run_replay_ladder`` (a fresh mounted
                container) and read ``LadderResult.collect_ok``.

It reports a two-arm table ``{repo: {configlane: collect_ok, baseline: collect_ok}}``
plus the aggregate config-lane-vs-baseline collect-clean counts + lift, and writes
the verdict to ``docs/superpowers/handoffs/2026-07-17-gate-a-result.md``.

NOT run in CI — provisioning needs git + network, rendering needs an LLM key, and
the replay needs Docker. See the result doc for the go/no-go criterion.

Usage:
  # provision only (fetch + reset each corpus repo to its pinned commit, then stop):
  python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json --provision-only

  # full measurement (renders + Docker replay + writes the verdict doc):
  python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json \
         --base-image <arm64-python-image>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# repo root + src/ both on path (mirrors run_v3_e2e.py / replay.py bootstrap):
# `from src.eval...` resolves from root and its `python_deps.*` transitive imports
# resolve from src/, whether run as a script OR imported by pytest from repo root.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Verified against src/eval/build_script_eval/replay.py:
#   run_replay_ladder(repo_dir, image, setup_script, top_import, *, ...) -> LadderResult
#   LadderResult.collect_ok: bool | None
from src.eval.build_script_eval.replay import run_replay_ladder  # noqa: E402
from src.eval.language_package_eval.coverage import top_level_import_name  # noqa: E402

# The capstone line to strip for the baseline arm: a line containing BOTH markers.
_EDITABLE_MARKERS = ("pip install", "-e .")
_WORK_ROOT = _ROOT / "work"
_RESULT_DOC = _ROOT / "docs" / "superpowers" / "handoffs" / "2026-07-17-gate-a-result.md"

# The go/no-go criterion, verbatim from plan Step 5.
_GO_NO_GO_CRITERION = (
    "GO if the config-lane arm materially lifts collect-clean over baseline on the "
    "corpus (target: recovers a meaningful share of the 34-build->14-collect gap — the "
    "project-namespace ModuleNotFoundError class). NO-GO if editable-install + rootdir "
    "does not move collection — then the config lane is not worth building and Stage B "
    "is cancelled."
)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------
def _strip_editable(setup: str) -> str:
    """Derive the BASELINE arm: drop exactly the editable-install capstone line(s)
    — any line containing ALL of ``_EDITABLE_MARKERS`` ("pip install" AND "-e .").

    Every other line is kept, in order — including lines carrying only ONE marker
    (a bare ``pip install foo``, or a comment mentioning ``-e .``). A no-op when no
    capstone is present. (``splitlines``/``join`` also normalizes any trailing
    newline away, which is immaterial to bash.)"""
    return "\n".join(
        line for line in setup.splitlines()
        if not all(m in line for m in _EDITABLE_MARKERS)
    )


def _aggregate(rows: dict) -> dict:
    """Config-lane vs baseline collect-clean COUNTS + the lift. ``collect_ok`` is
    tri-state (True / False / None); only True counts as collect-clean."""
    total = len(rows)
    cfg_clean = sum(1 for r in rows.values() if r.get("configlane") is True)
    base_clean = sum(1 for r in rows.values() if r.get("baseline") is True)
    return {
        "total": total,
        "configlane_collect_clean": cfg_clean,
        "baseline_collect_clean": base_clean,
        "lift": cfg_clean - base_clean,
    }


# ---------------------------------------------------------------------------
# Corpus loading (two shapes: pilot LIST vs rat_python50 {_provenance, repos})
# ---------------------------------------------------------------------------
def _load_entries(corpus_path: str) -> list[dict]:
    """Normalize the two supported corpus shapes into a flat list of repo entries.

    - ``datasets/pilot.json``: a top-level LIST of entries.
    - ``datasets/rat_python50_pinned_m3nothink.json``: a top-level DICT with a
      ``_provenance`` block and a ``repos`` LIST.

    Both entry kinds carry ``full_name`` + a clone URL (``repo_url`` | ``clone_url``)
    + a pinned commit (``sha`` | ``base_commit`` | ``commit``)."""
    data = json.loads(Path(corpus_path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("repos"), list):
            return data["repos"]
        # defensive: any dict-of-entries corpus, minus the provenance block
        return [v for k, v in data.items() if k != "_provenance" and isinstance(v, dict)]
    raise ValueError(
        f"unrecognized corpus shape in {corpus_path!r}: {type(data).__name__}"
    )


def _entry_url(entry: dict) -> str:
    url = entry.get("repo_url") or entry.get("clone_url")
    if not url:
        raise ValueError(
            f"corpus entry {entry.get('full_name')!r} has no repo_url/clone_url"
        )
    return url


def _entry_commit(entry: dict) -> str:
    commit = entry.get("sha") or entry.get("base_commit") or entry.get("commit")
    if not commit:
        raise ValueError(
            f"corpus entry {entry.get('full_name')!r} has no sha/base_commit/commit"
        )
    return commit


def _repo_dir(full_name: str, work_root: Path = _WORK_ROOT) -> Path:
    return work_root / full_name.replace("/", "__")


# ---------------------------------------------------------------------------
# Provisioning (git) + rendering (run_v3_e2e) — side-effecting boundary
# ---------------------------------------------------------------------------
def _git(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=(str(cwd) if cwd else None), check=True)


def _provision(entry: dict, work_root: Path = _WORK_ROOT) -> Path:
    """Fetch + ``git reset --hard`` the entry's pinned commit into
    ``work/<full_name with / -> __>``. Clones on first use, then fetches and hard-
    resets to the pinned commit (idempotent across reruns)."""
    repo_dir = _repo_dir(entry["full_name"], work_root)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    url = _entry_url(entry)
    commit = _entry_commit(entry)
    if not (repo_dir / ".git").is_dir():
        _git(["clone", url, str(repo_dir)])
    _git(["fetch", "--tags", "origin"], cwd=repo_dir)
    _git(["reset", "--hard", commit], cwd=repo_dir)
    return repo_dir


def _render(repo_dir: Path, image: str, out: Path) -> str:
    """Shell out to ``scripts/run_v3_e2e.py`` to render the config-lane setup.sh
    (with the ``-e .`` capstone). argv verified against that script's argparse:
    positional ``repo``, ``--out``, ``--base-image`` (see ``_build_arg_parser``)."""
    subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "run_v3_e2e.py"), str(repo_dir),
         "--out", str(out), "--base-image", image],
        check=True,
    )
    return out.read_text()


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------
def provision_all(corpus: str, *, work_root: Path = _WORK_ROOT) -> dict:
    """Provision every corpus repo. Returns ``{full_name: repo_dir}`` for the ones
    that materialized; every excluded/errored repo is surfaced on stderr (no silent
    caps)."""
    provisioned: dict[str, str] = {}
    errors: dict[str, str] = {}
    for entry in _load_entries(corpus):
        name = entry.get("full_name", "<unknown>")
        try:
            provisioned[name] = str(_provision(entry, work_root))
        except Exception as exc:  # noqa: BLE001 — surface, never swallow
            errors[name] = f"{type(exc).__name__}: {exc}"
    for name, err in sorted(errors.items()):
        print(f"[gate-a] PROVISION ERROR {name}: {err}", file=sys.stderr)
    return provisioned


def measure(corpus: str, image: str, *, work_root: Path = _WORK_ROOT) -> dict:
    """Two-arm collect-recovery table + aggregate over the corpus.

    Returns ``{"rows": {repo: {"configlane": collect_ok, "baseline": collect_ok}},
    "errors": {repo: msg}, "aggregate": {...}}``. Each repo is provisioned, its
    config-lane setup.sh rendered (with the ``-e .`` capstone), the baseline derived
    by stripping that capstone, and BOTH arms replayed via ``run_replay_ladder``.
    Every excluded/errored repo lands in ``errors`` (no silent caps)."""
    rows: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for entry in _load_entries(corpus):
        name = entry.get("full_name", "<unknown>")
        try:
            repo_dir = _provision(entry, work_root)
            top_import = entry.get("top_import") or top_level_import_name(repo_dir)
            cfg = _render(repo_dir, image, repo_dir / "setup.cfg.sh")
            base = _strip_editable(cfg)
            rows[name] = {
                "configlane": run_replay_ladder(
                    str(repo_dir), image, cfg, top_import
                ).collect_ok,
                "baseline": run_replay_ladder(
                    str(repo_dir), image, base, top_import
                ).collect_ok,
            }
        except Exception as exc:  # noqa: BLE001 — surface, never swallow
            errors[name] = f"{type(exc).__name__}: {exc}"
    return {"rows": rows, "errors": errors, "aggregate": _aggregate(rows)}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_table(result: dict) -> None:
    rows, agg, errors = result["rows"], result["aggregate"], result["errors"]
    print(f"{'repo':<50} {'config-lane':>12} {'baseline':>12}")
    for name, r in sorted(rows.items()):
        print(f"{name:<50} {str(r['configlane']):>12} {str(r['baseline']):>12}")
    print(
        f"\naggregate: config-lane {agg['configlane_collect_clean']}/{agg['total']} "
        f"collect-clean vs baseline {agg['baseline_collect_clean']}/{agg['total']} "
        f"(lift +{agg['lift']})"
    )
    if errors:
        print("\nERRORED / EXCLUDED (no silent caps):")
        for name, err in sorted(errors.items()):
            print(f"  {name}: {err}")


def _write_result_doc(corpus: str, image: str, result: dict, *,
                      path: Path = _RESULT_DOC) -> None:
    rows, agg, errors = result["rows"], result["aggregate"], result["errors"]
    lines = [
        "# Gate A — cure-recovery result (config-lane go/no-go)",
        "",
        f"- Corpus: `{corpus}`",
        f"- Base image: `{image}`",
        f"- Config-lane collect-clean: **{agg['configlane_collect_clean']}/{agg['total']}**",
        f"- Baseline collect-clean: **{agg['baseline_collect_clean']}/{agg['total']}**",
        f"- Lift: **+{agg['lift']}**",
        "",
        "## Two-arm table (`collect_ok`)",
        "",
        "| repo | config-lane | baseline |",
        "|---|---|---|",
    ]
    for name, r in sorted(rows.items()):
        lines.append(f"| {name} | {r['configlane']} | {r['baseline']} |")
    lines += ["", "## Errored / excluded (no silent caps)", ""]
    if errors:
        lines += [f"- {name}: {err}" for name, err in sorted(errors.items())]
    else:
        lines.append("- (none)")
    lines += ["", "## Go/no-go criterion (plan Step 5)", "", _GO_NO_GO_CRITERION, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Gate A — config-lane cure-recovery measurement (go/no-go)."
    )
    ap.add_argument("--corpus", required=True,
                    help="Path to the corpus JSON (pilot.json or "
                         "rat_python50_pinned_m3nothink.json).")
    ap.add_argument("--base-image", dest="base_image", default=None,
                    help="Base image tag passed to run_v3_e2e (--base-image) and "
                         "run_replay_ladder. Required for a measurement run; only "
                         "--provision-only may omit it.")
    ap.add_argument("--provision-only", action="store_true", dest="provision_only",
                    help="Fetch + reset each corpus repo to its pinned commit, then "
                         "STOP (no render, no Docker replay).")
    return ap


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.provision_only:
        provisioned = provision_all(args.corpus)
        for name, path in sorted(provisioned.items()):
            print(f"[gate-a] provisioned {name} -> {path}")
        print(f"[gate-a] provision-only: {len(provisioned)} repo(s) materialized.")
        return 0

    if not args.base_image:
        print("ERROR: --base-image is required for a measurement run "
              "(only --provision-only may omit it).", file=sys.stderr)
        return 2

    result = measure(args.corpus, args.base_image)
    _print_table(result)
    _write_result_doc(args.corpus, args.base_image, result)
    print(f"\n[gate-a] wrote verdict -> {_RESULT_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
