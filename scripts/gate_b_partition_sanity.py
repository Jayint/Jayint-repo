"""Gate B — partition-sanity measurement (the go/no-go before the Stage-C flip).

A THIN driver over existing infrastructure — it builds no new container harness.
Per corpus repo it:

  1. PROVISION  fetch + ``git reset --hard`` the repo to its pinned commit into
                ``work/<full_name with / -> __>`` — REUSING Gate A's provisioning
                (``scripts.gate_a_cure_recovery._provision`` / ``provision_all``),
                which already handles both corpus shapes (pilot LIST and
                ``rat_python50`` ``{_provenance, repos}``).
  2. CONSTRUCT run graph construction with the SHADOW config-lane pass ON
                (``build_dep_graph(..., shadow_config_lane=True)``) inside a fresh
                scratch ``DockerExecutor`` — mirroring
                ``advise.build_advisory_for_repo``'s executor setup. The shadow
                pass (Task 8) classifies -> cures -> arbitrates, appends one
                ``ShadowRecord`` per repo to ``V3_SHADOW_RECORD_PATH``, and
                DISCARDS its graph effect (real construction is byte-identical).
  3. AGGREGATE read ONLY the records THIS run appended — the driver captures the
                record file's byte size before construction and reads from that
                offset to EOF (``_read_records_since``), so prior-run records are
                excluded and the file is NEVER truncated or erased — then reduce to
                the partition-sanity report (``aggregate_shadow_records`` — a PURE
                function, no I/O).

Why ``build_dep_graph`` directly and not ``build_advisory_for_repo``: the advisory
wrapper does NOT thread ``shadow_config_lane`` (verified — its signature has no such
param, advise.py:311). ``build_dep_graph`` is the verified entrypoint that accepts
the flag (build.py:1089) and forwards it through the ecosystem seam
(``EcosystemProvider.package_obligations`` -> ``PythonProvider`` ->
``_python_package_obligations``) to the shadow pass. So the driver mirrors the
advisory's fresh-scratch-container construction but calls ``build_dep_graph`` itself.

NOT run in CI — provisioning needs git + network, and construction needs Docker.
See the result doc for the go/no-go criterion.

Usage:
  # provision only (fetch + reset each corpus repo to its pinned commit, then stop):
  python scripts/gate_b_partition_sanity.py --corpus datasets/pilot.json --provision-only

  # full measurement (V3_SHADOW_RECORD_PATH names the shadow JSONL the pass appends
  # to AND that this driver aggregates; --base-image is the scratch container image):
  V3_SHADOW_RECORD_PATH=/tmp/shadow.jsonl \
      python scripts/gate_b_partition_sanity.py --corpus datasets/pilot.json \
             --base-image <arm64-python-image>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# repo root + src/ both on path (mirrors run_v3_e2e.py / gate_a bootstrap):
# `from scripts.gate_a_cure_recovery import ...` resolves from root (namespace pkg)
# and `from python_deps.depgraph.* import ...` resolves from src/, whether run as a
# script OR imported by pytest from repo root.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from python_deps.depgraph.build import build_dep_graph  # noqa: E402
from python_deps.depgraph.executor import (  # noqa: E402
    DockerExecutor,
    LocalSubprocessExecutor,
)

# REUSE Gate A's provisioning verbatim — do not rebuild a parallel copy. These
# helpers already normalize both corpus shapes and drive git fetch + hard-reset.
from scripts.gate_a_cure_recovery import (  # noqa: E402
    _WORK_ROOT,
    _load_entries,
    _provision,
    provision_all,
)

_RESULT_DOC = _ROOT / "docs" / "superpowers" / "handoffs" / "2026-07-17-gate-b-result.md"

# The Gate B go/no-go criterion, VERBATIM from plan Step 3.
_GO_NO_GO_CRITERION = (
    "GO if: the collision zone is a small minority of imports; the classifier's "
    "internal/external split matches expectation on spot-checked repos; cure-recovery "
    "tracks Gate A; and the provisional-flag (false-green) rate is low enough to report "
    "honestly. NO-GO if the collision zone is huge, the classifier misroutes, or "
    "false-greens are common — then rethink before the flip."
)


# ---------------------------------------------------------------------------
# Pure aggregation (the unit-testable core — NO I/O)
# ---------------------------------------------------------------------------
def _ratio(num: int, den: int) -> float:
    """Safe ratio: 0.0 when the denominator is 0 (never raises, never NaN)."""
    return (num / den) if den else 0.0


def _is_errored(record: dict) -> bool:
    """A record carrying an ``"error"`` key is an errored/excluded repo (a synthetic
    dict the driver injects per construction failure), not a shadow measurement."""
    return "error" in record


def aggregate_shadow_records(records: list[dict]) -> dict:
    """Reduce parsed shadow records into the Gate B partition-sanity report. PURE:
    takes already-parsed dicts, returns the aggregate, does NO I/O.

    Each measurement record is a ``ShadowRecord.__dict__`` (Task 8): ``repo``,
    ``n_internal``/``n_external``/``n_deferred`` (the classifier partition sizes),
    ``cure_ok``/``cure_rung``/``collect_ok`` (the in-container cure), and the
    ``resolves_local``/``fallthrough``/``unresolved``/``provisional_flags`` tuples
    (arbitration; serialized as JSON lists — only their lengths are read here).

    A record carrying an ``"error"`` key is an errored/excluded repo: it is listed
    in ``errored`` and kept OUT of every numeric aggregate — **no silent caps**.
    Missing keys fall back to safe defaults so a malformed line never crashes the
    report (system-boundary input validation)."""
    errored = sorted(
        (
            {"repo": r.get("repo"), "error": r.get("error")}
            for r in records
            if _is_errored(r)
        ),
        key=lambda e: str(e["repo"]),
    )
    ok = [r for r in records if not _is_errored(r)]
    total = len(ok)

    # --- partition-size distribution (n_internal / n_external / n_deferred) ---
    total_internal = sum(int(r.get("n_internal", 0)) for r in ok)
    total_external = sum(int(r.get("n_external", 0)) for r in ok)
    total_deferred = sum(int(r.get("n_deferred", 0)) for r in ok)
    total_partitioned = total_internal + total_external + total_deferred

    # --- collision-zone frequency (deferred == the ambiguous collision zone) ---
    repos_with_collision = sum(1 for r in ok if int(r.get("n_deferred", 0)) > 0)

    # --- cure-recovery rate (plan: cure_ok / collect_ok) ---
    n_cure_ok = sum(1 for r in ok if r.get("cure_ok") is True)
    n_collect_ok = sum(1 for r in ok if r.get("collect_ok") is True)
    n_cure_and_collect = sum(
        1 for r in ok if r.get("cure_ok") is True and r.get("collect_ok") is True
    )

    # --- fallthrough count (the false-green install vector) ---
    total_fallthrough = sum(len(r.get("fallthrough") or ()) for r in ok)
    repos_with_fallthrough = sum(1 for r in ok if r.get("fallthrough"))

    # --- provisional-flag rate (== false-green rate; mirrors fallthrough) ---
    total_provisional_flags = sum(len(r.get("provisional_flags") or ()) for r in ok)
    n_provisional_repos = sum(1 for r in ok if r.get("provisional_flags"))

    return {
        "total": total,
        "n_errored": len(errored),
        "errored": errored,
        # partition-size distribution
        "total_internal": total_internal,
        "total_external": total_external,
        "total_deferred": total_deferred,
        "mean_internal": _ratio(total_internal, total),
        "mean_external": _ratio(total_external, total),
        "mean_deferred": _ratio(total_deferred, total),
        # collision-zone frequency
        "repos_with_collision": repos_with_collision,
        "collision_zone_fraction": _ratio(total_deferred, total_partitioned),
        # cure-recovery rate
        "n_cure_ok": n_cure_ok,
        "n_collect_ok": n_collect_ok,
        "n_cure_and_collect": n_cure_and_collect,
        "cure_recovery_rate": _ratio(n_cure_ok, n_collect_ok),
        # fallthrough count
        "total_fallthrough": total_fallthrough,
        "repos_with_fallthrough": repos_with_fallthrough,
        # provisional-flag rate (false-green)
        "n_provisional_repos": n_provisional_repos,
        "total_provisional_flags": total_provisional_flags,
        "provisional_flag_rate": _ratio(n_provisional_repos, total),
    }


# ---------------------------------------------------------------------------
# Shadow JSONL reading (I/O boundary — kept OUT of the pure aggregator)
# ---------------------------------------------------------------------------
def _read_records_since(path: str, start_offset: int) -> list[dict]:
    """Read the shadow records this run appended AT OR AFTER byte ``start_offset``.

    NON-DESTRUCTIVE by construction — this ONLY reads: it seeks to ``start_offset``
    and reads to EOF, and it NEVER truncates, overwrites, or otherwise erases the
    file. ``start_offset`` is the file's byte size captured BEFORE this run's
    construction (``os.path.getsize`` — or ``0`` if the file did not pre-exist), so:

      * every record a PRIOR run left in the file lives below the offset and is
        excluded — a re-run of the documented ``V3_SHADOW_RECORD_PATH=...`` command
        aggregates only ITS OWN appended records (solves the original P2), and
      * because the driver reads instead of clearing, a mis-set ``V3_SHADOW_RECORD_PATH``
        pointing at an unrelated file can no longer erase it (solves the destructive
        risk). If the file did not pre-exist, ``start_offset`` is ``0`` and every
        record (all of them this run's) is read.

    Reading is done in BINARY and decoded, so ``start_offset`` is an exact byte
    boundary (multibyte-safe) that lines up with ``os.path.getsize``. One
    ``ShadowRecord.__dict__`` per line; blank lines are skipped and a malformed line
    is surfaced on stderr (no silent caps) then skipped. Returns ``[]`` if the file
    does not exist (an empty run — reported as such)."""
    records: list[dict] = []
    p = Path(path)
    if not p.exists():
        print(f"[gate-b] WARNING: shadow record file not found: {path}", file=sys.stderr)
        return records
    with p.open("rb") as fh:
        fh.seek(start_offset)
        chunk = fh.read()
    text = chunk.decode("utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[gate-b] SKIP malformed JSONL line {i}: {exc}", file=sys.stderr)
    return records


# ---------------------------------------------------------------------------
# Construction (side-effecting boundary — Docker)
# ---------------------------------------------------------------------------
def _make_scratch_executor(repo_dir: str, image: str) -> DockerExecutor:
    """Build the scratch container the shadow pass cures INSIDE.

    This is factored out of ``_construct_shadow`` so the executor kwargs are
    unit-inspectable without Docker (constructing a ``DockerExecutor`` starts no
    container — only ``__enter__`` does).

    The cure (``cure.run_cure``) and arbitration (``arbitrate.probe_name``) ``cd``
    into ``getattr(executor, "repo_mount_dir", "/workspace/repo")`` and there run
    ``pip install -e .`` + ``python3 -c 'import <name>'`` probes. So that directory
    MUST be the provisioned repo — otherwise it is empty, every cure fails silently
    (recorded ``cure_ok=False``, not raised), arbitration marks all deferred names
    unresolved, and the Gate B rates are corrupted. The kwargs make a real run
    genuinely install:

      * ``mount_repo=repo_dir`` — bind-mounts the provisioned repo at the default
        ``repo_mount_dir`` (``/workspace/repo``) the cure/arbitration ``cd`` into.
        THE must-fix: without it the cure runs against an empty dir.
      * ``network=True`` — ``pip install -e .`` resolves the editable package's
        dependencies from PyPI; a no-network container cannot install them.
      * ``cache_volumes=True`` — reuse the shared pip/uv cache volumes so the
        editable install is fast. (advise.py keeps construction's cache OFF to
        "observe every gap cold"; that does not apply here — the shadow graph is
        discarded, this is a measurement-only cure, so caching only speeds it up.)
      * ``bootstrap_uv=True`` — install ``uv`` into the probe (kept from the
        original; the closure/cure install path uses ``uv pip``).
    """
    return DockerExecutor(
        image,
        bootstrap_uv=True,
        mount_repo=repo_dir,
        network=True,
        cache_volumes=True,
    )


def _construct_shadow(repo_dir: str, image: str) -> None:
    """Run construction with the shadow config-lane pass ON, mirroring
    ``advise.build_advisory_for_repo``'s executor setup (a fresh scratch
    ``DockerExecutor`` + host ``LocalSubprocessExecutor``).

    We call ``build_dep_graph`` DIRECTLY, not ``build_advisory_for_repo``, because
    the advisory wrapper does not thread ``shadow_config_lane``. ``build_dep_graph``
    is the verified entrypoint that accepts the flag (build.py:1089) and forwards it
    through the ecosystem seam to ``_python_package_obligations``. The shadow pass
    appends a ``ShadowRecord`` to ``V3_SHADOW_RECORD_PATH`` (read from the env by
    ``shadow._write_shadow_record``); the returned graph is intentionally discarded
    (measured, not wired — real construction stays byte-identical).

    The scratch container is built by ``_make_scratch_executor`` so it mounts the
    provisioned repo (with network + cache) — see that helper for why the mount is
    load-bearing for the in-container cure."""
    host = LocalSubprocessExecutor()
    with _make_scratch_executor(repo_dir, image) as scratch:
        build_dep_graph(repo_dir, scratch, host_executor=host, shadow_config_lane=True)


def run_shadow_construction(corpus: str, image: str, *, work_root: Path = _WORK_ROOT) -> dict:
    """Provision + shadow-construct every corpus repo. Returns ``{repo: error_msg}``
    for the repos that failed to construct (the JSONL is the record channel; this
    return is only the error side). Every excluded/errored repo is surfaced on
    stderr (no silent caps)."""
    errored: dict[str, str] = {}
    for entry in _load_entries(corpus):
        name = entry.get("full_name", "<unknown>")
        try:
            repo_dir = _provision(entry, work_root)
            _construct_shadow(str(repo_dir), image)
        except Exception as exc:  # noqa: BLE001 — surface, never swallow
            errored[name] = f"{type(exc).__name__}: {exc}"
    for name, err in sorted(errored.items()):
        print(f"[gate-b] CONSTRUCT ERROR {name}: {err}", file=sys.stderr)
    return errored


def aggregate_run(record_path: str, errored: dict, *, start_offset: int = 0) -> dict:
    """Combine the shadow JSONL records THIS run appended (the bytes at or after
    ``start_offset``) with one synthetic error dict per errored repo, then reduce via
    the pure ``aggregate_shadow_records``. The errored repos are represented as
    ``{"repo": name, "error": msg}`` dicts so the aggregate lists them without them
    polluting the numeric buckets.

    ``start_offset`` is the record file's byte size captured BEFORE construction, so
    prior-run records (below the offset) are excluded WITHOUT touching the file — no
    truncation, no erase. It defaults to ``0`` (read the whole file) for callers that
    start from an empty/fresh record path."""
    records = _read_records_since(record_path, start_offset)
    combined = records + [{"repo": n, "error": e} for n, e in sorted(errored.items())]
    return aggregate_shadow_records(combined)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_summary(agg: dict) -> None:
    print(
        f"[gate-b] repos measured: {agg['total']} (errored: {agg['n_errored']})\n"
        f"  partition means  internal={agg['mean_internal']:.2f} "
        f"external={agg['mean_external']:.2f} deferred={agg['mean_deferred']:.2f}\n"
        f"  collision zone   {agg['repos_with_collision']} repo(s); "
        f"fraction {agg['collision_zone_fraction']:.3f} of classified imports\n"
        f"  cure recovery    cure_ok={agg['n_cure_ok']} collect_ok={agg['n_collect_ok']} "
        f"(rate {agg['cure_recovery_rate']:.3f})\n"
        f"  fallthrough      {agg['total_fallthrough']} name(s) over "
        f"{agg['repos_with_fallthrough']} repo(s)\n"
        f"  provisional/FG   {agg['n_provisional_repos']} repo(s), rate "
        f"{agg['provisional_flag_rate']:.3f}"
    )
    if agg["errored"]:
        print("  ERRORED / EXCLUDED (no silent caps):")
        for e in agg["errored"]:
            print(f"    {e['repo']}: {e['error']}")


def _write_result_doc(corpus: str, image: str, agg: dict, *, path: Path = _RESULT_DOC) -> None:
    """Write the measured aggregate + verdict scaffold to the result doc. Invoked
    only on a real run; overwrites the deferred note this task commits."""
    lines = [
        "# Gate B — partition-sanity result (config-lane go/no-go)",
        "",
        f"- Corpus: `{corpus}`",
        f"- Base image: `{image}`",
        f"- Repos measured: **{agg['total']}** (errored: **{agg['n_errored']}**)",
        "",
        "## Partition-size distribution (per-repo means)",
        "",
        "| partition | total | mean/repo |",
        "|---|---|---|",
        f"| internal | {agg['total_internal']} | {agg['mean_internal']:.2f} |",
        f"| external | {agg['total_external']} | {agg['mean_external']:.2f} |",
        f"| deferred (collision zone) | {agg['total_deferred']} | {agg['mean_deferred']:.2f} |",
        "",
        "## Collision zone",
        "",
        f"- Repos with a nonempty deferred set: **{agg['repos_with_collision']}/{agg['total']}**",
        f"- Collision-zone fraction of classified imports: "
        f"**{agg['collision_zone_fraction']:.3f}**",
        "",
        "## Cure recovery",
        "",
        f"- cure_ok: **{agg['n_cure_ok']}**  collect_ok: **{agg['n_collect_ok']}**  "
        f"(cure_ok AND collect_ok: **{agg['n_cure_and_collect']}**)",
        f"- Cure-recovery rate (cure_ok / collect_ok): **{agg['cure_recovery_rate']:.3f}**",
        "",
        "## Fallthrough / provisional (false-green vector)",
        "",
        f"- Fallthrough names: **{agg['total_fallthrough']}** over "
        f"**{agg['repos_with_fallthrough']}** repo(s)",
        f"- Provisional-flagged repos: **{agg['n_provisional_repos']}/{agg['total']}** "
        f"(rate **{agg['provisional_flag_rate']:.3f}**, "
        f"**{agg['total_provisional_flags']}** flag(s))",
        "",
        "## Errored / excluded (no silent caps)",
        "",
    ]
    if agg["errored"]:
        lines += [f"- {e['repo']}: {e['error']}" for e in agg["errored"]]
    else:
        lines.append("- (none)")
    lines += ["", "## Go/no-go criterion (plan Step 3)", "", _GO_NO_GO_CRITERION,
              "", "## Verdict", "", "- TODO: fill GO / NO-GO from the numbers above.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Gate B — config-lane partition-sanity measurement (go/no-go)."
    )
    ap.add_argument("--corpus", required=True,
                    help="Path to the corpus JSON (pilot.json or "
                         "rat_python50_pinned_m3nothink.json).")
    ap.add_argument("--base-image", dest="base_image", default=None,
                    help="Scratch container image for construction. Required for a "
                         "measurement run; only --provision-only may omit it.")
    ap.add_argument("--provision-only", action="store_true", dest="provision_only",
                    help="Fetch + reset each corpus repo to its pinned commit, then "
                         "STOP (no construction, no Docker).")
    return ap


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.provision_only:
        provisioned = provision_all(args.corpus)  # REUSE Gate A's provisioning
        print(f"[gate-b] provision-only: {len(provisioned)} repo(s) materialized.")
        return 0

    if not args.base_image:
        print("ERROR: --base-image is required for a measurement run "
              "(only --provision-only may omit it).", file=sys.stderr)
        return 2

    record_path = os.environ.get("V3_SHADOW_RECORD_PATH")
    if not record_path:
        print("ERROR: V3_SHADOW_RECORD_PATH must be set — it names the shadow JSONL "
              "the pass appends to AND that this driver aggregates.", file=sys.stderr)
        return 2

    # Capture the record file's byte size BEFORE construction. The shadow pass
    # APPENDS, so aggregating only the bytes AT OR AFTER this offset excludes any
    # prior-run records WITHOUT clearing the file — a re-run is not polluted, and a
    # mis-set V3_SHADOW_RECORD_PATH is never erased (we only ever READ from it).
    start_offset = os.path.getsize(record_path) if os.path.exists(record_path) else 0
    errored = run_shadow_construction(args.corpus, args.base_image)
    agg = aggregate_run(record_path, errored, start_offset=start_offset)
    _print_summary(agg)
    _write_result_doc(args.corpus, args.base_image, agg)
    print(f"[gate-b] wrote verdict -> {_RESULT_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
