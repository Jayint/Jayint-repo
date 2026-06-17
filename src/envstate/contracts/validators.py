# src/envstate/contracts/validators.py
"""One-shot import sweep + host-satisfied set + attempt outcome derivation (spec §6.3)."""
from __future__ import annotations

import json as _json
from typing import Any

from .graph import ContractGraph, project_status

# ---------------------------------------------------------------------------
# Distribution-name → import-name resolution (Bug 1 fix)
# ---------------------------------------------------------------------------

# Static map from PyPI distribution name to Python import name.
# Keys are stored with canonical PyPI casing; lookup is case-insensitive (see
# resolve_import_name).  Extend as sensible — only "offenders" where the two
# names differ are needed here.
KNOWN_IMPORT_NAMES: dict[str, str] = {
    "beautifulsoup4": "bs4",
    "Jinja2": "jinja2",
    "PyYAML": "yaml",
    "scikit-learn": "sklearn",
    "Pillow": "PIL",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
    "python-dotenv": "dotenv",
    "Werkzeug": "werkzeug",
    "setuptools": "setuptools",   # same but guards against Setuptools capitalisation
}


def resolve_import_name(dist_name: str) -> str:
    """Resolve a PyPI distribution name to the corresponding Python import name.

    Tries an exact match first, then a case-insensitive search through
    KNOWN_IMPORT_NAMES.  Returns *dist_name* unchanged when not in the map
    (i.e. assumes import name == distribution name, which is true for the
    majority of packages).
    """
    if dist_name in KNOWN_IMPORT_NAMES:
        return KNOWN_IMPORT_NAMES[dist_name]
    lower = dist_name.lower()
    for k, v in KNOWN_IMPORT_NAMES.items():
        if k.lower() == lower:
            return v
    return dist_name


# Probe template for packages NOT in the static map.  The probe runs INSIDE
# the sandbox container (via exec_readonly / /bin/sh -lc) so it has access to
# the container's importlib.metadata — NOT the host venv, which has a
# completely different package set.
#
# {dist!r} is replaced with the repr() of the distribution name (adds safe
# single-quotes).  {{...}} is literal { } after .format().
#
# Resolution order inside the container:
#   1. Query importlib.metadata.packages_distributions() → invert to get a
#      dist→top-level map.
#   2. Fall back to the distribution name itself if metadata is unavailable or
#      the distribution is not listed (packages where dist == import pass
#      through naturally).
_DYNAMIC_IMPORT_PROBE_TMPL = (
    "python - <<'_E_'\n"
    "import importlib as _m\n"
    "_d={dist!r}\n"
    "_n=_d\n"
    "try:\n"
    "    from importlib.metadata import packages_distributions as _pd\n"
    "    _inv={{dl.lower(): t for t, dls in _pd().items() for dl in dls}}\n"
    "    _n=_inv.get(_d.lower(), _d)\n"
    "except Exception:\n"
    "    pass\n"
    "_m.import_module(_n)\n"
    "_E_"
)


# ---------------------------------------------------------------------------
# One-shot import sweep command
# ---------------------------------------------------------------------------

def build_import_sweep_command(declared_dist_names: list[str]) -> str:
    """One /bin/sh -lc-safe heredoc that imports each declared dep and prints JSON {import_name: ok}."""
    imports = [resolve_import_name(d) for d in declared_dist_names]
    py_list = "[" + ",".join(repr(i) for i in imports) + "]"
    return (
        "python - <<'_E_'\n"
        "import importlib, json\n"
        f"_names={py_list}\n"
        "_res={}\n"
        "for _n in _names:\n"
        "    try:\n        importlib.import_module(_n); _res[_n]=True\n"
        "    except Exception:\n        _res[_n]=False\n"
        "print(json.dumps(_res))\n"
        "_E_"
    )


def parse_import_sweep(stdout: str) -> tuple[tuple[str, bool], ...]:
    """Parse the JSON output of build_import_sweep_command into (import_name, ok) pairs."""
    try:
        d = _json.loads(stdout.strip().splitlines()[-1])
        return tuple((str(k), bool(v)) for k, v in d.items())
    except Exception:
        return ()


# ---------------------------------------------------------------------------
# Host satisfaction + attempt outcome derivation
# ---------------------------------------------------------------------------

def host_satisfied_set(
    graph: ContractGraph,
    world_map: Any,
    ledger_events: list[Any],
) -> frozenset[str]:
    """Contract ids the host certifies this cycle (spec §6.3)."""
    sat: set[str] = set()
    ok_imports = {name for name, ok in getattr(world_map, "import_results", ()) if ok}
    for c in graph.contracts():
        if c.data.get("kind") == "python_import":
            subject = c.data.get("subject", "")
            if resolve_import_name(subject) in ok_imports or subject in ok_imports:
                sat.add(c.id)
    # goal: repo_tests_pass is host-certified by the done-gate (handled in projection refresh)
    return frozenset(sat)


def derive_attempt_outcome(
    graph: ContractGraph,
    attempt_id: str,
    host_satisfied: frozenset[str],
    step_failed: bool,
) -> str:
    """Return an AttemptOutcome value for the given attempt node."""
    if step_failed:
        return "failed"
    node = graph.node(attempt_id)
    targets: list[str] = (node.data.get("created_from_target_node_ids") or []) if node else []
    if not targets:
        return "ok"
    statuses = [project_status(graph, t, host_satisfied) for t in targets]
    if all(s == "satisfied" for s in statuses):
        return "ok"
    if any(s == "violated" for s in statuses):
        return "ok_but_still_blocked"
    return "ok"
