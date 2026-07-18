"""Curated Debian/Ubuntu provider tables for native/system needs.

Seeded from ``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 11.
This module EXTENDS (does not import) ``failure_classifier``: the classifier
recognizes the *shape* of a native failure; these tables map a concrete soname /
tool / header onto the apt package that provides it.

Targeting Debian/Ubuntu only in V1 (design 10.5).  An unknown soname/tool maps to
``None`` (no LLM fallback in this plan; the node stays ``missing`` with evidence).
PACKAGE_TO_SYSTEM_DEPS (the curated package->syslib prediction table) was deleted 2026-07-01 — see construction-enrichment cluster 1a; the one remaining pre-install native prediction derives only from the resolver's wheel/sdist signal (seed.py).
The soname / build-tool / header -> apt tables that used to live here were unified
into ``os_resolver.PROVIDER_TABLE`` (capability-keyed); this module keeps only the
runtime-CLI authority and the native-risk gate, which the resolver does not cover.
"""

from __future__ import annotations

# Runtime CLI binaries a repo's OWN code shells out to (subprocess/os.system) ->
# apt package. Distinct from the pip *build* tools in ``os_resolver.PROVIDER_TABLE``
# (headers/config binaries): those are surfaced by ldd/apt-on-build, whereas these
# are external programs the code invokes at run time, which that path never sees.
# Deliberately SMALL and curated: only unambiguous, well-known external tools go
# here so the subprocess scanner (subprocess_scan.py) is a strict allowlist and
# never flags shell builtins, coreutils, or project-local scripts. Keep DISJOINT
# from the resolver's ``binary`` providers so the two tool sources cannot mint two
# nodes for one apt pkg.
CLI_TOOL_TO_APT: dict[str, str] = {
    "git": "git",
    "adb": "adb",
    "sqlite3": "sqlite3",
    "java": "default-jre-headless",
    "ffmpeg": "ffmpeg",
    "pandoc": "pandoc",
    "curl": "curl",
    "wget": "wget",
    "unzip": "unzip",
    "gpg": "gnupg",
    "openssl": "openssl",
    "dot": "graphviz",
    "pdftoppm": "poppler-utils",
}

# Distributions whose import may need a system lib (whom to deep-probe).
NATIVE_RISK_PACKAGES: frozenset[str] = frozenset(
    {
        "opencv-python",
        "opencv-python-headless",
        "psycopg2",
        "mysqlclient",
        "lxml",
        "cryptography",
        "numpy",
        "scipy",
        "pandas",
        "torch",
        "tensorflow",
        "playwright",
        "selenium",
    }
)

from graph.python.util.import_mapping import normalize_package_name

# Curated dist -> runtime CLI tools the package ITSELF shells out to, keyed by
# canonical (PEP 503) distribution name. The RUN-time analogue of
# PACKAGE_TO_BUILD_NEEDS: a universal, package-intrinsic fact that no sensor can
# observe (GitPython invokes git through a variable executable path; a source
# scan sees no literal). Minted via apt_for_cli_tool (NOT the resolve path,
# which lacks ("binary","git") in PROVIDER_TABLE). Deliberately SMALL — every
# entry must be a universal package fact, and every tool a CLI_TOOL_TO_APT key.
PACKAGE_TO_RUNTIME_TOOLS: dict[str, tuple[str, ...]] = {
    "gitpython": ("git",),      # git.Git shells out to the git binary
    "pre-commit": ("git",),     # a git-hook manager; core function IS git
    "dvc": ("git",),            # data VCS layered on git
    "copier": ("git",),         # clones template repos via git
    "pypandoc": ("pandoc",),    # thin wrapper over the pandoc binary
    "graphviz": ("dot",),       # renders via the dot binary
    "pdf2image": ("pdftoppm",), # calls poppler's pdftoppm/pdfinfo
}


def runtime_tools_for(pkg_name: str) -> tuple[str, ...]:
    """Runtime CLI tools a distribution shells out to (``()`` when none)."""
    return PACKAGE_TO_RUNTIME_TOOLS.get(normalize_package_name(pkg_name), ())


def apt_for_cli_tool(tool: str) -> str | None:
    """Apt package providing a runtime CLI binary (e.g. ``adb`` -> ``adb``); None
    when ``tool`` is not on the curated subprocess allowlist."""
    return CLI_TOOL_TO_APT.get(tool)
