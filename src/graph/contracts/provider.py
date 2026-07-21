"""Neutral ecosystem seam: the two-axis Protocol + closure/certify enums.

Sits ABOVE ``python_deps`` (Python is one provider among peers). Imports the
SHARED ``DepGraph`` schema but NEVER the Python pipeline (``build.py``), so
Rust/Node providers can depend on this module without pulling in Python code.
Keep the interface minimal — expand toward the full spec §4 (resolve_closure,
project_install, bulk_certify, verify commands) in later slices.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Protocol

from graph.model import DepGraph
from graph.runtime_plan import RuntimePlan


class ClosureMode(enum.Enum):
    """How a repo's transitive closure is obtained (per-REPO)."""

    LOCK = "lock"        # committed lockfile present -> parse offline. Preferred.
    RESOLVE = "resolve"  # no lock -> run the resolver, then pin. Python is RESOLVE.
    COMPUTE = "compute"  # no lock, no cheap resolver (Java/Gradle). Admitted, deferred.


class CertifyMode(enum.Enum):
    """How the host establishes a PACKAGE-tier node's truth (per-PROVIDER).

    Resource tiers (SystemLib/Tool/Runtime) are ALWAYS presence-certified in every
    ecosystem, regardless of this value; the scheduler routes by (tier, certify_mode).
    """

    INSTALL = "install"  # each Package node certified by one check_command. Python, Node.
    COMPILE = "compile"  # one bulk build certifies the whole closure; per-node attributed. Rust, Go.


class EcosystemProvider(Protocol):
    """The construction subset of the provider interface THIS branch needs."""

    name: str                 # "python" | "rust" | "node"
    certify_mode: CertifyMode

    def detect(self, repo: str) -> float:
        """Confidence 0..1 that ``repo`` belongs to this ecosystem (dispatch gate)."""
        ...

    def closure_mode_for(self, repo: str) -> ClosureMode:
        """Per-repo: LOCK if a committed lock is present, else RESOLVE (COMPUTE deferred)."""
        ...

    def package_obligations(
        self,
        repo: str,
        container_executor: object,
        *,
        host_executor: object | None = None,
        target_python: str | None = None,
        target_platform: str | None = None,
        exclude_newer: str | None = None,
        needed_extras: frozenset[str] = frozenset(),
        record_provider: object | None = None,
        uv_sources_enabled: bool = False,
        llm_dist_guesser: Callable[[str, tuple[str, ...]], list[str]] | None = None,
        shadow_config_lane: bool = False,
    ) -> tuple[DepGraph, list, object, str | None]:
        """PHASE 1 body. Returns ``(graph, roots, target_env, exclude_newer)``;
        only ``graph`` flows onward (the rest are provider-composition / test-
        visibility surface). ``record_provider`` is an opaque provider-specific
        grounding-oracle injection (test seam); Python uses it, other ecosystems
        accept-and-ignore ``None``. Surfaced for signature stability
        (``research_zero_impact.md`` §3). ``uv_sources_enabled`` (V3_UV_SOURCES,
        default OFF) is a Python/uv-specific false-green gate — see
        ``graph.python.pipeline._python_package_obligations``'s docstring;
        other ecosystems accept-and-ignore it exactly like ``record_provider``.
        ``llm_dist_guesser`` is the opaque install-lane dist guesser
        ``(import_name, symbols) -> [dist, ...]`` the Python fixpoint calls on a
        pipreqs map MISS; annotated structurally (this module stays
        ecosystem-agnostic and never imports ``python_deps``). Non-Python
        providers accept-and-ignore ``None`` exactly like ``record_provider``.
        ``shadow_config_lane`` (default OFF) is a plain ecosystem-agnostic bool
        gating a Python-specific shadow config-lane pass; non-Python providers
        accept-and-ignore it exactly like ``uv_sources_enabled``."""
        ...

    def native_obligations(self, graph: DepGraph, container_executor: object) -> DepGraph:
        """PHASE 2 "look then derive": relink -> ldd -> dlopen backstop -> probe restamp."""
        ...

    def service_obligations(
        self,
        graph: DepGraph,
        repo: str,
        service_classifier: object | None = None,
    ) -> "tuple[DepGraph, RuntimePlan]":
        """PHASE 3 — service tier (Task 4 contract). Runs the (opaque,
        ecosystem-supplied) service classifier over the converged graph and returns
        ``(graph, plan)``: the GRAPH is passed through UNCHANGED (Service/Config no
        longer live in the constructed graph — they are the construction artifact
        :class:`~graph.runtime_plan.RuntimePlan`, which the v3-arm loop re-admits into
        its working graph at loop start). ``service_classifier is None`` => returns
        ``(graph, EMPTY_PLAN)``. The classifier is an injected
        ``Callable[[DepGraph, str], RuntimePlan]`` (envstate owns it); providers never
        import envstate."""
        ...
