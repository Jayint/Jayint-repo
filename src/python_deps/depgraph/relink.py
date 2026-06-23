"""Stage 4a — certified Import->Package relink from packages_distributions().

After ``install_closure`` has installed the resolved closure, the container can
report the ground-truth import-name -> distribution map via
``importlib.metadata.packages_distributions()`` (Python 3.10+). This stage uses it
to add CERTIFIED ``Import->Package`` edges that the pre-install heuristic
(``resolve.link_imports_to_packages``) missed — e.g. ``import dateutil`` provided
by dist ``python-dateutil``. Discovery only: it adds edges, never node state.

Pure parser + pure edge builder + thin executor orchestrator (repo immutability:
every "mutation" returns a NEW ``DepGraph``).
"""

from __future__ import annotations

import json

PACKAGES_DIST_CMD = (
    'python -c "import importlib.metadata, json; '
    'print(json.dumps(importlib.metadata.packages_distributions()))"'
)


def parse_packages_distributions(stdout: str) -> dict[str, list[str]]:
    """Parse the JSON ``{import_name: [dist, ...]}`` map; ``{}`` if malformed."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, list):
            out[key] = [v for v in val if isinstance(v, str)]
    return out
