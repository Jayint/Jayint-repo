"""Static Config-tier discovery: the env vars a repo reads (project-induced).

Pure (no Executor, no network): walks the repo on disk, AST-parses each in-scope
``.py`` file, and records every ``os.environ[...]`` / ``os.environ.get(...)`` /
``os.getenv(...)`` read (and the bare ``environ``/``getenv`` forms).  Mirrors the
directory-exclusion scope of ``scan.py`` so examples/docs/build don't leak.
"""

from __future__ import annotations

import ast
import configparser
import os
import re

from graph.model import DiscoveredBy, Layer, Node, NodeType, State

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - tomli is in requirements.txt
        tomllib = None  # type: ignore[assignment]

_EXCLUDED_SEGMENTS: frozenset[str] = frozenset(
    {
        "examples", "example", "docs", "doc", "build", "dist", "samples",
        "sample", "benchmarks", "benchmark", "bench", "scripts", "script",
        ".github", ".tox", "node_modules", "site-packages", ".venv", "venv",
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    }
)


def _is_excluded(rel: str) -> bool:
    segments = {seg.lower() for seg in re.split(r"[\\/]+", rel) if seg}
    return bool(segments & _EXCLUDED_SEGMENTS)


# ---------------------------------------------------------------------------
# Vendored-fixture exclusion — LAST-RESORT fallback ONLY (`scan_env_defaults`,
# via its `scan_env_defaults_provenance` implementation).
#
# MEASURED REGRESSION (django-oauth-toolkit): a vendored EXAMPLE Django app
# bundled INSIDE the test suite (`tests/app/idp/manage.py`) is real Python code
# with a real `os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')`
# call — indistinguishable, to the AST walk, from a first-party manage.py. But
# it is not the project's own configuration, and baking its value overrode the
# repo's actual, authoritative `tox.ini` setting (`tests.settings`), which then
# broke `import idp` at test time. `_EXCLUDED_SEGMENTS` above already drops
# examples/docs/samples et al., but "app"/"fixtures" cannot be added there as
# bare segment names — many real projects have a legitimate top-level `app/`
# or `fixtures/` directory that IS first-party source, and pruning every such
# name outright would blind the scanner to real projects, not just vendored
# ones. So this check is COMPOUND-PATH-aware (`tests/app`, not bare `app`) and
# is applied ONLY inside the value-producing fallback (`scan_env_defaults` /
# `scan_env_defaults_provenance`) —
# never inside `scan_env_reads`/`scan_framework_config_reads` (the read-only
# detectors that decide which vars deserve a hint CONFIG node at all; a var
# read only by a vendored fixture is still worth surfacing as a hint, just
# never worth trusting for a VALUE). Do not widen this into the shared
# `_EXCLUDED_SEGMENTS` set and do not add plain "app"/"fixtures" as bare
# segments — that would re-introduce the false-positive risk this comment
# describes.
# ---------------------------------------------------------------------------
_VENDORED_FIXTURE_PREFIXES: tuple[str, ...] = (
    "tests/app", "tests/fixtures", "test/fixtures",
)
_VENDORED_SEGMENTS: frozenset[str] = frozenset(
    {
        "examples", "example", "docs", "doc", "samples", "sample",
        "demo", "demos", "vendor", "vendored", "third_party", "thirdparty",
    }
)


def _is_vendored_fixture(rel: str) -> bool:
    """True when ``rel`` (a repo-relative path) lives under a vendored /
    example / fixture directory whose static env defaults must never be
    trusted as the project's own configuration."""
    norm = rel.replace("\\", "/").lower()
    if any(norm == p or norm.startswith(p + "/") for p in _VENDORED_FIXTURE_PREFIXES):
        return True
    segments = {seg for seg in norm.split("/") if seg}
    return bool(segments & _VENDORED_SEGMENTS)


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# ---------------------------------------------------------------------------
# Scope-aware ``os.environ`` / ``os.getenv`` resolution (review round 3).
#
# A file-wide "any rebinding poisons everywhere" check is both unsound (misses
# walrus, match captures, shadowing imports) and too blunt (an unrelated
# ``def helper(os)`` wrongly demoted a genuine top-level ``os.environ.setdefault``
# to 3b). This does a TWO-LEVEL lexical approximation instead, asymmetric and
# fail-closed:
#   * MODULE-level rebindings (assignment / walrus / match capture / ``for`` /
#     ``with as`` / ``except as`` targets, a shadowing import such as
#     ``import fake as os`` or ``from x import os``, or any ``global``/``nonlocal``
#     escape) poison the name at ALL call sites.
#   * FUNCTION-local bindings (params, local assigns/walrus/for/with/except/match/
#     comprehension targets, nested def/class names) poison ONLY call sites
#     lexically inside that scope's subtree.
# A receiver resolves to the genuine import iff its name is not poisoned at that
# site (no module rebinding AND no local binding in any enclosing scope on the
# path up to module level). Ordering is ignored in the CONSERVATIVE direction
# only: a later module-level ``os = fake`` may demote an earlier genuine call to
# 3b (harmless, advisory), but a poisoned name is NEVER promoted to 3a. Any doubt
# -> 3b.
# ---------------------------------------------------------------------------

_SCOPE_NODES: tuple = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _iter_with_scopes(tree: ast.AST):
    """Yield ``(node, scope_chain)`` for every node -- ``scope_chain`` is the tuple
    of enclosing function/lambda/class nodes (module-level nodes have ``()``).
    Iterative (no recursion-limit risk); order is irrelevant to every consumer
    (set-collection and per-node classification)."""
    stack = [(tree, ())]
    while stack:
        node, chain = stack.pop()
        yield node, chain
        child_chain = chain + (node,) if isinstance(node, _SCOPE_NODES) else chain
        for child in ast.iter_child_nodes(node):
            stack.append((child, child_chain))


def _import_bindings(node: ast.AST):
    """Yield ``(local_name, is_genuine, kind)`` for each name an import binds.
    ``is_genuine`` is True only for a real ``os`` module / ``os.environ`` /
    ``os.getenv`` binding; every other import binding (``import fake as os``,
    ``from x import os``, ``from x import y as environ``, ``from os import path``)
    is a NON-genuine binding of ``local_name`` -- i.e. a shadow/rebind."""
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name == "os":
                yield (a.asname or "os", True, "os")
            elif a.name.startswith("os.") and a.asname is None:
                yield ("os", True, "os")                 # `import os.path` binds top-level `os`
            else:
                yield (a.asname or a.name.split(".")[0], False, "")
    elif isinstance(node, ast.ImportFrom):
        genuine = node.module == "os" and (node.level or 0) == 0
        for a in node.names:
            if a.name == "*":
                continue                                 # star import: unknown names, ignore
            local = a.asname or a.name
            if genuine and a.name == "environ":
                yield (local, True, "environ")
            elif genuine and a.name == "getenv":
                yield (local, True, "getenv")
            else:
                yield (local, False, "")


def _binding_target_names(target: ast.AST, out: list[str]) -> None:
    if isinstance(target, ast.Name):
        out.append(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            _binding_target_names(e, out)
    elif isinstance(target, ast.Starred):
        _binding_target_names(target.value, out)
    # Attribute / Subscript targets bind no bare name.


def _local_binding_names(node: ast.AST) -> list[str]:
    """Bare names a SINGLE construct binds in its own scope: assignment / walrus /
    annotated / augmented targets, ``for``/comprehension targets, ``with ... as`` /
    ``except ... as``, function & lambda PARAMS (``ast.arg``), nested def/class
    NAMES, and match captures (``MatchAs``/``MatchStar`` name, ``MatchMapping``
    rest). ``global``/``nonlocal`` are handled separately (module-level escape)."""
    names: list[str] = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            _binding_target_names(t, names)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        _binding_target_names(node.target, names)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        _binding_target_names(node.target, names)
    elif isinstance(node, ast.comprehension):
        _binding_target_names(node.target, names)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                _binding_target_names(item.optional_vars, names)
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            names.append(node.name)
    elif isinstance(node, ast.arg):
        names.append(node.arg)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.append(node.name)                          # binds in the ENCLOSING scope
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
        if node.name:
            names.append(node.name)
    elif isinstance(node, ast.MatchMapping):
        if node.rest:
            names.append(node.rest)
    return names


class _OsUsage:
    """Scope-aware resolution of genuine ``os.environ`` / ``os`` / ``os.getenv``
    names for one file. ``genuine_*`` are bound by a MODULE-level genuine ``os``
    import; a name resolves at a call site only when not poisoned there (see the
    module comment above)."""

    __slots__ = ("genuine_os", "genuine_environ", "genuine_getenv",
                 "module_poison", "scope_locals")

    def __init__(self, genuine_os, genuine_environ, genuine_getenv,
                 module_poison, scope_locals):
        self.genuine_os = genuine_os
        self.genuine_environ = genuine_environ
        self.genuine_getenv = genuine_getenv
        self.module_poison = module_poison
        self.scope_locals = scope_locals          # scope-node -> frozenset[str]

    def _poisoned(self, name: str, chain: tuple) -> bool:
        if name in self.module_poison:
            return True
        for scope in chain:
            if name in self.scope_locals.get(scope, frozenset()):
                return True
        return False

    def is_os_module(self, name: str, chain: tuple) -> bool:
        return name in self.genuine_os and not self._poisoned(name, chain)

    def is_environ_name(self, name: str, chain: tuple) -> bool:
        return name in self.genuine_environ and not self._poisoned(name, chain)

    def is_getenv_name(self, name: str, chain: tuple) -> bool:
        return name in self.genuine_getenv and not self._poisoned(name, chain)


def _analyze_os_usage(tree: ast.AST) -> tuple:
    """Return ``(usage, node_scopes)`` for one file: an ``_OsUsage`` plus a map
    from each ``Call``/``Subscript`` node to its scope chain (so the scanners can
    resolve a receiver per call site while still walking in document order)."""
    genuine_os: set[str] = set()
    genuine_environ: set[str] = set()
    genuine_getenv: set[str] = set()
    module_poison: set[str] = set()
    scope_locals: dict = {}
    node_scopes: dict = {}
    _GENUINE = {"os": genuine_os, "environ": genuine_environ, "getenv": genuine_getenv}

    def bind_local(scope, name: str) -> None:
        if scope is None:
            module_poison.add(name)
        else:
            scope_locals.setdefault(scope, set()).add(name)

    for node, chain in _iter_with_scopes(tree):
        scope = chain[-1] if chain else None
        if isinstance(node, (ast.Call, ast.Subscript)):
            node_scopes[node] = chain
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for local, is_genuine, kind in _import_bindings(node):
                if is_genuine:
                    if scope is None:
                        _GENUINE[kind].add(local)        # module-level genuine import
                    # a genuine import INSIDE a function is ignored (fail-closed)
                else:
                    bind_local(scope, local)             # shadowing / unrelated import
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            module_poison.update(node.names)             # escaping rebind -> poison everywhere
            continue
        for name in _local_binding_names(node):
            bind_local(scope, name)

    usage = _OsUsage(
        frozenset(genuine_os), frozenset(genuine_environ), frozenset(genuine_getenv),
        frozenset(module_poison), {k: frozenset(v) for k, v in scope_locals.items()},
    )
    return usage, node_scopes


def _resolves_to_os_environ(owner: ast.AST, usage: _OsUsage, chain: tuple) -> bool:
    """True only when ``owner`` GENUINELY resolves to ``os.environ`` at this call
    site -- a name bound (non-poisoned) via ``from os import environ [as X]``, or
    ``<genuine-os-alias>.environ`` with a non-poisoned base. A bare
    ``settings.environ`` base, or any poisoned/rebound name, is NOT genuine."""
    if isinstance(owner, ast.Name):
        return usage.is_environ_name(owner.id, chain)
    if isinstance(owner, ast.Attribute) and owner.attr == "environ":
        base = owner.value
        return isinstance(base, ast.Name) and usage.is_os_module(base.id, chain)
    return False


def _is_environ_receiver(owner: ast.AST, usage: _OsUsage, chain: tuple) -> bool:
    """Broad DETECTION of an ``environ``-shaped receiver worth surfacing as a
    config READ (advisory). A strict superset of ``_resolves_to_os_environ``: it
    keeps the pre-alias matcher UNCONDITIONALLY (a bare ``environ`` name, any
    ``*.environ`` attribute), so recall never drops, and additionally accepts a
    genuine, non-poisoned ``environ`` import alias. An unresolved
    ``settings.environ`` is still surfaced -- but classified advisory (3b)."""
    if isinstance(owner, ast.Name):
        return owner.id == "environ" or usage.is_environ_name(owner.id, chain)
    return isinstance(owner, ast.Attribute) and owner.attr == "environ"


def _var_from_call(call: ast.Call, usage: _OsUsage, chain: tuple) -> str | None:
    """``os.getenv('X')`` / ``os.environ.get('X')`` / ``os.environ.setdefault('X', ...)``
    (and import-aliased forms) -> ``'X'`` (first str arg).

    ``setdefault`` is the canonical Django ``manage.py``/``wsgi.py`` idiom
    (``os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')``) — without
    it the scanner is blind to essentially every Django project's entrypoint
    (FIX A1). ``get``/``setdefault`` are accepted only on an environ-shaped
    receiver (``_is_environ_receiver``); a bare ``getenv`` name counts only when
    it genuinely resolves to ``os.getenv`` at this site (``from os import getenv``,
    non-poisoned)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr in ("get", "setdefault"):
            if not _is_environ_receiver(func.value, usage, chain):
                return None
        elif func.attr != "getenv":
            return None
        return _const_str(call.args[0]) if call.args else None
    if isinstance(func, ast.Name) and usage.is_getenv_name(func.id, chain):
        return _const_str(call.args[0]) if call.args else None
    return None


def _default_from_call(call: ast.Call, usage: _OsUsage, chain: tuple) -> tuple[str, str] | None:
    """``os.environ.get('X', 'literal')`` / ``os.getenv('X', 'literal')`` /
    ``os.environ.setdefault('X', 'literal')`` -> ``('X', 'literal')``; only when
    BOTH the name and the default are string literals (f-strings / non-str
    defaults are not statically resolvable).

    For ``setdefault`` the 2nd positional arg is not just a fallback read value
    but the value ``setdefault`` itself WRITES into the environment when the
    var is absent — i.e. it is authoritative, not a guess (FIX A1's bonus:
    ``DJANGO_SETTINGS_MODULE=settings`` is exactly what gold's winning
    Dockerfile hand-writes for a Django ``manage.py``)."""
    var = _var_from_call(call, usage, chain)
    if var is None or len(call.args) < 2:
        return None
    default = _const_str(call.args[1])
    if default is None:
        return None
    return var, default


def _var_from_subscript(sub: ast.Subscript, usage: _OsUsage, chain: tuple) -> str | None:
    """``os.environ['X']`` / ``environ['X']`` (and import-aliased ``environ``) -> ``'X'``."""
    if not _is_environ_receiver(sub.value, usage, chain):
        return None
    return _const_str(sub.slice)


def _scan_source(src: str, rel: str, out: dict[str, str]) -> None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    usage, node_scopes = _analyze_os_usage(tree)
    for node in ast.walk(tree):                     # document order -> first-seen snippet wins
        var = None
        if isinstance(node, ast.Call):
            var = _var_from_call(node, usage, node_scopes.get(node, ()))
        elif isinstance(node, ast.Subscript):
            var = _var_from_subscript(node, usage, node_scopes.get(node, ()))
        if var and var not in out:
            snippet = " ".join((src.splitlines()[node.lineno - 1] if node.lineno else "").split())
            out[var] = f"{rel}:{getattr(node, 'lineno', 0)}  {snippet}"[:200]


def scan_env_reads(repo_path: str) -> dict[str, str]:
    """Map each env var the repo reads -> one-line evidence (file:line snippet)."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, repo_path)
            if _is_excluded(rel):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            _scan_source(src, rel, out)
    return out


# Config-provenance ``source`` vocabulary (Task 3 / B1). This module is the single
# source of truth for what the scanners/classifier can emit; the bake-eligibility
# gate (``build_script._config_bake_eligible``) grounds its allow-set in these
# names (+ ``_ENV_EXAMPLE_FILES`` for rung 2) rather than duplicating magic strings.
#   rung 1 -> ``_SOURCE_AUTHORITATIVE`` (emitted by classify's _resolve_config_value)
#   rung 2 -> a member of ``_ENV_EXAMPLE_FILES`` (the winning .env template file)
#   rung 3 -> ``_SOURCE_SETDEFAULT`` (3a, env-writing entrypoint idiom, bake-eligible)
#             or ``_SOURCE_FALLBACK`` (3b, a read-time get/getenv default, advisory)
_SOURCE_AUTHORITATIVE = "authoritative_config"
_SOURCE_SETDEFAULT = "code_scan_setdefault"
_SOURCE_FALLBACK = "code_scan_fallback"


def _is_setdefault_call(call: ast.Call, usage: _OsUsage, chain: tuple) -> bool:
    """True only for a GENUINE ``os.environ.setdefault(...)`` at this call site --
    the env-*writing* entrypoint idiom that stays bake-eligible (3a). A
    ``setdefault`` on an UNRESOLVED receiver (``settings.environ``, an ``environ``
    name not bound from ``os``, or an ``os``/alias name rebound at module level or
    shadowed by a local binding in some enclosing scope) is fail-closed to advisory
    3b, never promoted to 3a -- baking is safety-sensitive, so an unproven receiver
    must not qualify."""
    func = call.func
    return (isinstance(func, ast.Attribute) and func.attr == "setdefault"
            and _resolves_to_os_environ(func.value, usage, chain))


def scan_env_defaults_provenance(repo_path: str) -> dict[str, tuple[str, str]]:
    """Map env var -> ``(literal_default, source)`` from the ``.py`` code scan,
    tagging each var with its rung-3 SPLIT provenance ``source`` so downstream
    bake-eligibility is decided from DATA, not scan detail:

      * ``code_scan_setdefault`` (3a) -- value written by an
        ``os.environ.setdefault(VAR, 'literal')`` call, the canonical Django
        ``manage.py``/``wsgi.py`` entrypoint idiom. This is what the code puts
        INTO the environment; under pytest the entrypoint never runs, so baking
        is the only delivery path -- it stays bake-eligible.
      * ``code_scan_fallback`` (3b) -- value found ONLY as an
        ``os.environ.get(VAR, 'literal')`` / ``os.getenv(VAR, 'literal')``
        *fallback* default. Advisory-only: the imported code applies that
        default itself at test time, so refusing to bake costs nothing.

    LAST-RESORT fallback value source for CONFIG nodes: ``scan_authoritative_config``
    (tox.ini/pytest.ini/setup.cfg/pyproject.toml) and ``.env.example`` both rank
    above this (see ``classify_services_clean._config_nodes``). Powers the
    Service-tier URL-binding inference too, which needs a parseable value rather
    than ``?``.

    FIX 3 (post-B1): deterministic AND ambiguity-safe. The walk is sorted
    (subdirectories and filenames both) so the same repo always visits files in
    the same order regardless of filesystem/OS iteration order -- a bake
    decision must never depend on that. And when two files assign DIFFERENT
    literal defaults to the SAME var, neither wins: the var is dropped entirely
    rather than picking whichever file happened to be seen first. This mirrors
    ``depgraph.python.lanes.install.ground.choose_provider``'s AMBIGUOUS branch -- never pick a
    variant -- because "first file wins" previously let a value from a test
    fixture or example app silently shadow the real one. When ONE agreed value
    is written by both a setdefault AND a get, the env-writing
    ``code_scan_setdefault`` tag wins (it is what the code actually writes).

    MEASURED REGRESSION FIX: a vendored/example/fixture path (``tests/app/``,
    ``examples/``, ...) is skipped entirely here -- see ``_is_vendored_fixture``
    -- because this scan is the FALLBACK OF LAST RESORT: a value it finds is
    only ever used when no authoritative config file names the var. A bundled
    example app's own default is real code but not the project's configuration.
    """
    values: dict[str, set[str]] = {}
    setdefaulted: dict[str, bool] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS)
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, repo_path)
            if _is_excluded(rel) or _is_vendored_fixture(rel):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    src = fh.read()
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            usage, node_scopes = _analyze_os_usage(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    chain = node_scopes.get(node, ())
                    hit = _default_from_call(node, usage, chain)
                    if hit:
                        values.setdefault(hit[0], set()).add(hit[1])
                        setdefaulted[hit[0]] = (
                            setdefaulted.get(hit[0], False) or _is_setdefault_call(node, usage, chain))
    out: dict[str, tuple[str, str]] = {}
    for var, seen in values.items():
        if len(seen) != 1:
            continue                                 # AMBIGUOUS -> drop, never pick a variant
        source = _SOURCE_SETDEFAULT if setdefaulted[var] else _SOURCE_FALLBACK
        out[var] = (next(iter(seen)), source)
    return out


def scan_env_defaults(repo_path: str) -> dict[str, str]:
    """Map env var -> its literal default from ``os.environ.get(VAR, 'literal')``
    / ``setdefault`` -- a thin ``{var: value}`` projection of
    ``scan_env_defaults_provenance`` (which additionally tags each value with its
    rung-3a/3b source). The value set, deterministic sorted walk, ambiguity drop
    and vendored-fixture exclusion are all inherited byte-for-byte from it.
    """
    return {var: value for var, (value, _src) in scan_env_defaults_provenance(repo_path).items()}


_DECOUPLE_FUNCS = frozenset({"config"})           # decouple.config('X')
_ENVIRONS_METHODS = frozenset({"str", "int", "bool", "float", "list", "url"})  # env.str('X')
_SETTINGS_BASES = frozenset({"BaseSettings"})


def _framework_var_from_call(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _DECOUPLE_FUNCS:
        return _const_str(call.args[0]) if call.args else None
    if isinstance(func, ast.Attribute) and func.attr in _ENVIRONS_METHODS:
        return _const_str(call.args[0]) if call.args else None
    return None


def _settings_fields(tree: ast.AST) -> list[str]:
    """Annotated field names of any class subclassing a *Settings base -> UPPER."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {b.id for b in node.bases if isinstance(b, ast.Name)} | {
            b.attr for b in node.bases if isinstance(b, ast.Attribute)
        }
        if not (base_names & _SETTINGS_BASES):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                out.append(stmt.target.id.upper())
    return out


def scan_framework_config_reads(repo_path: str) -> dict[str, str]:
    """Env vars read via pydantic-settings / decouple / environs (string-literal
    args + BaseSettings fields). NOTE: env_prefix / field aliases not yet resolved.
    """
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, repo_path)
            if _is_excluded(rel):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    src = fh.read()
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    var = _framework_var_from_call(node)
                    if var and var not in out:
                        out[var] = f"{rel}:{getattr(node, 'lineno', 0)}  (framework config)"[:200]
            for field in _settings_fields(tree):
                out.setdefault(field, f"{rel}  (BaseSettings field)")
    return out


_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _read_env_pairs(path: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.lstrip().startswith("#"):
                    continue
                m = _ENV_LINE.match(line)
                if m:
                    pairs[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        return {}
    return pairs


_ENV_EXAMPLE_FILES: tuple[str, ...] = (".env.example", ".env.sample", ".env.template")


def parse_env_example_provenance(repo_path: str) -> dict[str, tuple[str, str]]:
    """`{VAR: (example_value, source_file)}` from .env.example/.sample/.template,
    recording WHICH file each var won from (later files override earlier, matching
    ``parse_env_example``'s precedence). Lets a value won from ``.env.sample``
    anchor its provenance/evidence to ``.env.sample`` rather than a mislabeled
    ``.env.example`` (B1 review #2)."""
    out: dict[str, tuple[str, str]] = {}
    for fname in _ENV_EXAMPLE_FILES:
        for var, value in _read_env_pairs(os.path.join(repo_path, fname)).items():
            out[var] = (value, fname)
    return out


def parse_env_example(repo_path: str) -> dict[str, str]:
    """`{VAR: example_value}` from .env.example/.sample/.template (value hints) --
    a thin value-only projection of ``parse_env_example_provenance``."""
    return {var: value for var, (value, _f) in parse_env_example_provenance(repo_path).items()}


# ---------------------------------------------------------------------------
# Authoritative test-config sources (MEASURED REGRESSION FIX, django-oauth-
# toolkit). These are the repo's OWN declared test-environment configuration --
# read BEFORE any `.py` source is scanned and ranked ABOVE `.env.example` too
# (see `classify_services_clean._config_nodes` for the full precedence chain:
# authoritative config files > `.env.example` > `scan_env_defaults` fallback).
# Each returns a raw, unfiltered `{key: value}` -- the allowlist gate that
# decides which keys are ever safe to bake as a Dockerfile ENV lives downstream
# in `build_script._config_env_marker`; this module only discovers candidate
# values, never gates them.
# ---------------------------------------------------------------------------

_RE_SETENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _read_ini(path: str) -> configparser.ConfigParser | None:
    """A case-preserving ConfigParser for ``path``, or ``None`` if it doesn't
    exist / fails to parse. Case is preserved (``optionxform = str``) because
    env var names are case-sensitive; interpolation is off because tox/ini
    files commonly contain ``%``-bearing values (e.g. ``{posargs}``-adjacent
    tokens) that are not ``configparser`` interpolation syntax."""
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return None
    return parser


def _parse_setenv_block(block: str) -> dict[str, str]:
    """``KEY = value`` lines from a tox ``setenv`` multi-line block -> dict.

    tox's own ini grammar makes ``setenv`` a single option whose VALUE is a
    multi-line string, each line itself a ``KEY = value`` assignment -- e.g.::

        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
            OTHER_VAR = other-value

    ``configparser`` hands us that whole block as one string; this re-parses
    it one line at a time.
    """
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        m = _RE_SETENV_LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def scan_tox_setenv(repo_path: str) -> dict[str, str]:
    """``[testenv] setenv`` (and ``[testenv:*]`` sections) of tox.ini -> dict.

    The base ``[testenv]`` section wins over any ``[testenv:*]`` for the same
    var: per-interpreter environments conventionally REPEAT the base value
    (``py39``/``py311``/... all running the same test settings), not disagree
    with it, so reading base-first and only consulting a ``testenv:*`` section
    when the base is silent avoids manufacturing ambiguity out of that
    repetition. (Real cross-file disagreement is still caught one layer up, in
    ``scan_authoritative_config``.)
    """
    parser = _read_ini(os.path.join(repo_path, "tox.ini"))
    if parser is None:
        return {}
    out: dict[str, str] = {}
    if parser.has_section("testenv") and parser.has_option("testenv", "setenv"):
        out.update(_parse_setenv_block(parser.get("testenv", "setenv")))
    for section in parser.sections():
        if not section.startswith("testenv:") or not parser.has_option(section, "setenv"):
            continue
        for var, value in _parse_setenv_block(parser.get(section, "setenv")).items():
            out.setdefault(var, value)
    return out


def _scan_ini_section(path: str, section: str) -> dict[str, str]:
    """Every ``key = value`` pair of ``section`` in the ini file at ``path`` ->
    dict (raw strings only, in whichever order ``configparser`` gives them)."""
    parser = _read_ini(path)
    if parser is None or not parser.has_section(section):
        return {}
    out: dict[str, str] = {}
    for key, value in parser.items(section):
        if value is not None:
            out[key] = value.strip()
    return out


def scan_pytest_ini(repo_path: str) -> dict[str, str]:
    """``[pytest]`` section of pytest.ini -> dict (pytest-django's own spelling
    of ``DJANGO_SETTINGS_MODULE`` as an ini option lives here)."""
    return _scan_ini_section(os.path.join(repo_path, "pytest.ini"), "pytest")


def scan_setup_cfg_pytest(repo_path: str) -> dict[str, str]:
    """``[tool:pytest]`` section of setup.cfg -> dict (the setup.cfg spelling
    of the same pytest ini options)."""
    return _scan_ini_section(os.path.join(repo_path, "setup.cfg"), "tool:pytest")


def scan_pyproject_pytest(repo_path: str) -> dict[str, str]:
    """``[tool.pytest.ini_options]`` table of pyproject.toml -> dict (the
    modern pyproject spelling of the same pytest ini options).

    Only scalar (str/int/float) values are kept -- a list-shaped option
    (``addopts = ["-v"]``) is never stringified into a guessed value, and a
    bool is skipped outright (not var-shaped). Missing/unparseable
    pyproject.toml, or no ``tomllib``/``tomli`` available at all, is a silent
    empty result -- this is a best-effort discovery layer, never fatal.
    """
    if tomllib is None:
        return {}
    path = os.path.join(repo_path, "pyproject.toml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    section = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if not isinstance(section, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in section.items():
        if isinstance(value, bool):
            continue  # not var-shaped -- exclude before the int check (bool is an int subclass)
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = str(value)
    return out


def _authoritative_hits(repo_path: str) -> dict[str, set[str]]:
    """Every value each authoritative source assigns to each var, un-resolved
    (var -> the SET of distinct values seen across all four sources) -- the
    shared basis for both ``scan_authoritative_config`` (the resolved,
    unambiguous values) and ``authoritative_ambiguous_vars`` (the vars where
    they disagree). Checked in this order (tox.ini, pytest.ini, setup.cfg,
    pyproject.toml) purely for readability/determinism of the merge; order
    does NOT resolve a disagreement -- see the module docstring above."""
    hits: dict[str, set[str]] = {}
    for source in (
        scan_tox_setenv(repo_path),
        scan_pytest_ini(repo_path),
        scan_setup_cfg_pytest(repo_path),
        scan_pyproject_pytest(repo_path),
    ):
        for var, value in source.items():
            hits.setdefault(var, set()).add(value)
    return hits


def scan_authoritative_config(repo_path: str) -> dict[str, str]:
    """The project's OWN declared test-environment config -> {VAR: value},
    merged across tox.ini/pytest.ini/setup.cfg/pyproject.toml. This is the
    TOP-RANKED value source for CONFIG-node baking (see
    ``classify_services_clean._config_nodes``): checked before `.env.example`
    and long before the `.py` code-scan fallback, because these files are
    where a project SAYS what its own test environment needs -- not a guess
    inferred from arbitrary source code (which may include vendored fixtures).

    Ambiguity-safe like ``scan_env_defaults``: when two of these four sources
    name the SAME var with DIFFERENT values, that is a genuine disagreement
    about the project's OWN declared configuration -- picking either one is
    indefensible, so the var is dropped here entirely (never guessed). See
    ``authoritative_ambiguous_vars`` -- callers must consult BOTH (a var absent
    from this dict is either "no authoritative source names it" or
    "authoritative sources disagree on it", and those two cases must be
    handled differently: the first falls through to a lower-ranked source,
    the second must not).
    """
    hits = _authoritative_hits(repo_path)
    return {var: next(iter(values)) for var, values in hits.items() if len(values) == 1}


def authoritative_ambiguous_vars(repo_path: str) -> frozenset[str]:
    """Vars where two or more authoritative sources disagree -- callers must
    treat these as "never bake, and do not fall through to a lower-ranked
    source either" (the project's own declared configuration contradicts
    itself; a `.env.example` or code-scan value agreeing with one side of the
    contradiction does not resolve it -- see `scan_authoritative_config`'s
    docstring)."""
    hits = _authoritative_hits(repo_path)
    return frozenset(var for var, values in hits.items() if len(values) > 1)


def configured_vars(repo_path: str) -> set[str]:
    """Vars already provided at test time -> suppress as false-missing.

    Sources: a real ``.env`` file, and an ``env =`` block in pytest config
    (``pytest.ini`` / ``setup.cfg`` / ``tox.ini``; pytest-env style).
    """
    provided: set[str] = set(_read_env_pairs(os.path.join(repo_path, ".env")))
    for cfg in ("pytest.ini", "setup.cfg", "tox.ini"):
        path = os.path.join(repo_path, cfg)
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        in_env = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^env\s*=", stripped):
                in_env = True
                continue
            if in_env:
                m = _ENV_LINE.match(line)
                if m and (line.startswith(" ") or line.startswith("\t")):
                    provided.add(m.group(1))
                elif stripped and not line[:1].isspace():
                    in_env = False
    return provided


def _config_node(var: str, value: str | None) -> Node:
    """Build the canonical CONFIG node for ``var``, encoding ``value`` (or the
    ``"?"`` unknown sentinel when it is absent/blank) into ``chosen_fix`` as
    ``env:VAR=value`` — the ONE convention downstream readers key off of:
    ``src.envstate.synthesis.bakeable_config_env`` already reads exactly this
    shape (chosen_fix != None, prefix ``env:``, value != ``"?"``) to decide
    which Config-tier vars are safe to bake as a Dockerfile ``ENV`` (FIX B1);
    ``build_script._need_block`` reads the same shape to render the
    ``#@config-env`` marker the adapter turns into that ``ENV`` line.

    ``value`` is never invented here — pass whatever a real static source
    produced (a ``scan_env_defaults``/``setdefault`` literal, an
    ``.env.example`` value, ...); ``None``/``""`` become the honest ``"?"``
    sentinel, never a guessed value.
    """
    fix = f"env:{var}={value if value else '?'}"
    return Node(
        id=f"config:{var}", type=NodeType.CONFIG, name=var, layer=Layer.CONFIG,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        check_command=f"printenv {var}", fix_candidates=(fix,), chosen_fix=fix,
    )
