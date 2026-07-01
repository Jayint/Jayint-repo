"""``TargetEnv`` — the ONE set of facts a resolve must honor: the TARGET
container's python + platform, never the HOST running the resolve.

``packaging.markers.Marker.evaluate(environment)`` fills any PEP 508 key
missing from ``environment`` from ``packaging.markers.default_environment()``
— which reflects the HOST process, not the container being built. Before this
module existed, ``resolve_lock._python_marker_env`` passed only
``python_version`` / ``python_full_version``, so ``sys_platform`` /
``platform_machine`` / ``os_name`` silently leaked from the host. On a
non-x86_64-linux dev host that wrongly prunes (or keeps) platform-gated deps
for the container.

``TargetEnv.marker_env()`` returns every field ``packaging`` may reference so
none is ever left for the host default to fill in. ``detect_target_env`` probes
the container ONCE (python + platform.machine + a libc guess for the
``--python-platform`` tag ``uv lock`` needs) and degrades to sensible defaults
on any failure — detection must never crash the resolve.
"""

from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.executor import Executor

# Single probe: version, os.name, sys.platform, machine, platform.system — one
# process, one round trip into the container.
_PROBE_CMD = (
    'python3 -c "import platform,sys,os; '
    "print(sys.version.split()[0], os.name, sys.platform, "
    'platform.machine(), platform.system())"'
)
_LIBC_PROBE_CMD = "ldd --version"

# Defaults used both as the "never crash" fallback and as the base target this
# codebase already assumes elsewhere (DEFAULT_TARGET_PLATFORM in resolve_lock.py
# is also x86_64-manylinux_2_28 — never manylinux2014, it silently downgrades
# wheels like numpy).
_DEFAULT_PYTHON_FULL = "3.11.0"
_DEFAULT_PYTHON_VERSION = "3.11"
_DEFAULT_MACHINE = "x86_64"
_DEFAULT_SYS_PLATFORM = "linux"
_DEFAULT_OS_NAME = "posix"
_DEFAULT_PLATFORM_SYSTEM = "Linux"
_GLIBC_TAG = "manylinux_2_28"
_MUSL_TAG = "musllinux_1_2"


@dataclass(frozen=True)
class TargetEnv:
    """Facts about the TARGET container a resolve/marker-eval must honor.

    One instance flows from ``detect_target_env`` (or a caller override) through
    ``resolve_closure`` into every marker evaluation and into the ``uv lock
    --python-platform`` flag, so a mismatched dev host never substitutes its own
    platform for the container's.
    """

    python_full: str
    python_version: str
    platform_machine: str
    sys_platform: str
    os_name: str
    platform_system: str
    python_platform_tag: str

    def marker_env(self) -> dict[str, str]:
        """Full PEP 508 marker-evaluation environment for this target.

        Every key ``packaging.markers`` may reference is present here so
        ``Marker.evaluate()`` never falls back to its HOST-derived
        ``default_environment()`` for a value this target controls.
        """
        return {
            "python_version": self.python_version,
            "python_full_version": self.python_full,
            "platform_machine": self.platform_machine,
            "sys_platform": self.sys_platform,
            "os_name": self.os_name,
            "platform_system": self.platform_system,
        }


def _default_target_env() -> TargetEnv:
    return TargetEnv(
        python_full=_DEFAULT_PYTHON_FULL,
        python_version=_DEFAULT_PYTHON_VERSION,
        platform_machine=_DEFAULT_MACHINE,
        sys_platform=_DEFAULT_SYS_PLATFORM,
        os_name=_DEFAULT_OS_NAME,
        platform_system=_DEFAULT_PLATFORM_SYSTEM,
        python_platform_tag=f"{_DEFAULT_MACHINE}-{_GLIBC_TAG}",
    )


def _platform_tag(machine: str, libc: str) -> str:
    suffix = _MUSL_TAG if libc == "musl" else _GLIBC_TAG
    return f"{machine}-{suffix}"


def _detect_libc(executor: Executor) -> str:
    """glibc vs musl guess via ``ldd --version`` (musl prints to stderr, rc!=0).

    Defaults to glibc (the common case, and the non-downgrading choice) on any
    ambiguity or probe failure.
    """
    try:
        result = executor.run(_LIBC_PROBE_CMD)
    except Exception:
        return "glibc"
    text = f"{result.stdout or ''} {result.stderr or ''}".lower()
    return "musl" if "musl" in text else "glibc"


def detect_target_env(executor: Executor) -> TargetEnv:
    """Probe the TARGET container once for the facts a marker-honest resolve
    needs, deriving ``python_platform_tag`` from the machine + a libc guess.

    Degrades to sensible (linux/posix/x86_64/glibc) defaults whenever the probe
    fails, is unparsable, or the executor raises — detection must never crash
    the resolve.
    """
    try:
        result = executor.run(_PROBE_CMD)
    except Exception:
        return _default_target_env()
    if not result.ok:
        return _default_target_env()

    parts = (result.stdout or "").split()
    if len(parts) < 5:
        return _default_target_env()

    python_full, os_name, sys_platform, machine, platform_system = parts[:5]
    version_parts = python_full.split(".")
    python_version = (
        ".".join(version_parts[:2]) if len(version_parts) >= 2 else python_full
    )

    libc = _detect_libc(executor)
    return TargetEnv(
        python_full=python_full,
        python_version=python_version,
        platform_machine=machine,
        sys_platform=sys_platform,
        os_name=os_name,
        platform_system=platform_system,
        python_platform_tag=_platform_tag(machine, libc),
    )
