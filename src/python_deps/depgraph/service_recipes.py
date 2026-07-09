"""Probe admissibility + bounded-poll rendering for SERVICE nodes. Pure, LLM-free.

The per-kind provisioning recipe tables (the start/bind/setup renderers keyed on a
recognized service *kind*) were removed once construction moved to the evidence-only
pipeline (Task 10). What remains is the admissibility firewall every service probe must
pass before node admission, plus the bounded readiness loop that wraps an admitted probe.
"""
from __future__ import annotations


def normalize_probe(probe: str | None, port: int | None, kind: str | None = None) -> str:
    """Return an admissible (read-only) probe command — the admissibility firewall
    every service probe must pass through before it can reach node admission:
    - if the given probe is already read-only -> return it verbatim
    - else if a port is known -> 'nc -z 127.0.0.1 <port>'
    - else -> '' (no admissible probe; caller lets the node demote at certify)

    ``kind`` is accepted for backward-compatible call sites but is no longer consulted:
    the per-kind recipe table it used to short-circuit through was removed in Task 10.
    """
    # Lazy import: patch_gate imports this module (render_probe_poll), so a top-level
    # `from patch_gate import is_read_only` here would be circular.
    from python_deps.depgraph.patch_gate import is_read_only

    if probe and is_read_only(probe):
        return probe
    if port:
        return f"nc -z 127.0.0.1 {port}"
    return ""


def render_probe_poll(probe: str) -> str:
    """Bounded readiness loop. Input is ALWAYS a normalize_probe output."""
    return f"for i in $(seq 1 15); do {probe} && exit 0; sleep 2; done; exit 1"
