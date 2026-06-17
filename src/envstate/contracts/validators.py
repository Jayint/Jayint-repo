# src/envstate/contracts/validators.py
"""Read-only validator registry + host auto-run (spec §5 Validator, §7 rule 4)."""
from __future__ import annotations

from typing import Any, Callable

from . import ids
from .graph import ContractGraph
from .nodes import ContractStatusEvent, Edge, Node
from .schema import redact_secrets

ExecReadonly = Callable[[str], tuple[int, str]]

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


def _build_import_command(dist_name: str) -> tuple[str, str]:
    """Return *(shell_command, import_name)* for a python_package_importable check.

    Resolution strategy
    -------------------
    1. Static map (KNOWN_IMPORT_NAMES, case-insensitive) → if found, emit a
       simple ``python -c "import <import_name>"`` command.
    2. General case → emit the _DYNAMIC_IMPORT_PROBE_TMPL command which
       resolves the top-level module via ``importlib.metadata`` *inside* the
       sandbox container, then falls back to the distribution name.

    The contract ``subject`` (distribution name) is NEVER used as the import
    name directly — only the resolved *import_name* makes it into the command.
    """
    import_name = resolve_import_name(dist_name)
    if import_name != dist_name:
        # Static map resolved — use a clean single-import command.
        cmd = f'python -c "import {import_name}"'
    else:
        # General case: resolve dynamically inside the container.
        cmd = _DYNAMIC_IMPORT_PROBE_TMPL.format(dist=dist_name)
    return cmd, import_name


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------

# kind -> (validator kind, command template using {subject}).
# For python_package_importable the template is retained for documentation;
# the actual command is built by _build_import_command (see run_confirmed_validators).
_REGISTRY: dict[str, tuple[str, str]] = {
    "python_package_importable": ("python_import_check", 'python -c "import {subject}"'),
    # Atomic precondition only — NOT the success gate (real execution stays the done-gate's job).
    "pytest_runnable": ("pytest_collect_check", "python -m pytest --collect-only -q --disable-warnings"),
}


def run_confirmed_validators(
    graph: ContractGraph, exec_readonly: ExecReadonly, revision: int
) -> tuple[list[Node], list[Edge], list[ContractStatusEvent]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    events: list[ContractStatusEvent] = []
    rid = ids.revision_id(revision)

    for contract in graph.nodes_by_type("Contract"):
        if contract.data.get("level") != "atomic":
            continue
        kind = str(contract.data.get("kind", ""))
        spec = _REGISTRY.get(kind)
        if spec is None:
            continue
        vkind, template = spec
        subject = str(contract.data.get("subject", ""))

        if kind == "python_package_importable":
            # Use the resolved import name so dist names like "beautifulsoup4"
            # produce `import bs4`, not `import beautifulsoup4`.
            cmd, import_name = _build_import_command(subject)
        else:
            cmd = template.format(subject=subject)
            import_name = subject

        rc, out = exec_readonly(cmd)

        vid = ids.validator_id(vkind, ids.slug(subject) or subject)
        if not graph.has_node(vid):
            validator_data: dict[str, Any] = {
                "kind": vkind,
                "command_template": template,
                "import_name": import_name,
            }
            nodes.append(Node(vid, "Validator", validator_data))
            edges.append(Edge(contract.id, "verified_by", vid))

        run_id = f"cmd:val:{ids.slug(vid)}:{revision:03d}"
        nodes.append(
            Node(run_id, "CommandExecution",
                 {"command": redact_secrets(cmd), "exit_code": int(rc),
                  "revision_before": rid, "revision_after": rid, "mutation_class": None})
        )
        status = "satisfied" if rc == 0 else "violated"
        events.append(
            ContractStatusEvent(
                contract_id=contract.id, status=status, revision_id=rid,
                evidence_ids=(run_id,), summary=redact_secrets(out[-200:]),
            )
        )
    return nodes, edges, events
