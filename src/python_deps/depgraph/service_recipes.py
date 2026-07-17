"""Probe admissibility + bounded-poll rendering for SERVICE nodes. Pure, LLM-free.

The per-kind provisioning recipe tables (the start/bind/setup renderers keyed on a
recognized service *kind*) were removed once construction moved to the evidence-only
pipeline (Task 10). What remains is the admissibility firewall every service probe must
pass before node admission, plus the bounded readiness loop that wraps an admitted probe.
"""
from __future__ import annotations

# ``render_probe_poll`` was hoisted to patch_gate (the admission gate that consumes
# it) so mutate imports no service module upward. With that edge gone, patch_gate no
# longer imports this module, so ``is_read_only`` can be a plain top-level import
# (the former cycle band-aid, a lazy import inside normalize_probe, is dissolved).
# ``render_probe_poll`` is re-exported here for existing service-side callers/tests.
from python_deps.depgraph.patch_gate import is_read_only, render_probe_poll  # noqa: F401


def normalize_probe(probe: str | None, port: int | None, kind: str | None = None) -> str:
    """Return an admissible (read-only) probe command — the admissibility firewall
    every service probe must pass through before it can reach node admission:
    - if the given probe is already read-only -> return it verbatim
    - else if a port is known -> 'nc -z 127.0.0.1 <port>'
    - else -> '' (no admissible probe; caller lets the node demote at certify)

    ``kind`` is accepted for backward-compatible call sites but is no longer consulted:
    the per-kind recipe table it used to short-circuit through was removed in Task 10.
    """
    if probe and is_read_only(probe):
        return probe
    if port:
        return f"nc -z 127.0.0.1 {port}"
    return ""
