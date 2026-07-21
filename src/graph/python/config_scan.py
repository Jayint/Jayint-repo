"""Static Config-tier discovery: the env vars a repo reads (project-induced).

Pure (no Executor, no network): walks the repo on disk, AST-parses each in-scope
``.py`` file, and records every ``os.environ[...]`` / ``os.environ.get(...)`` /
``os.getenv(...)`` read (and the bare ``environ``/``getenv`` forms).  Mirrors the
directory-exclusion scope of ``scan.py`` so examples/docs/build don't leak.

Rung-3 SAFETY POLARITY (review round 4): the ``os.environ.setdefault`` -> 3a
(bake-eligible) classifier resolves a receiver as the genuine ``os.environ`` ONLY
through an explicit HANDLED-SET of binding constructs; every OTHER construct that
can bind or mutate a tracked name demotes it to advisory 3b. Constructs outside
the handled set demote to 3b -- never extend 3a without extending the handled set
(see ``_analyze_os_usage`` and the scope-resolution comment below).
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
# Scope-aware ``os.environ`` / ``os.getenv`` resolution (review rounds 3-4).
#
# POLARITY (round-4 inversion): the analyzer AFFIRMATIVELY understands a fixed
# HANDLED-SET of binding constructs; every OTHER construct that can bind or mutate
# a tracked name DEMOTES it (fail-closed to advisory 3b) instead of being silently
# ignored. Earlier rounds enumerated POISON, so any binding form the analyzer did
# not know about defaulted a receiver to GENUINE and leaked a 3a promotion. Now
# unknowns demote. NEVER extend 3a (add a genuine binding / widen resolution)
# without extending the handled-set below in lock-step.
#
# The HANDLED-SET (constructs whose binding of a bare name we model precisely):
#   * imports -- a MODULE-level genuine ``os`` / ``environ`` / ``getenv`` binding
#     is genuine file-wide; a genuine import INSIDE a function/class body is
#     genuine WITHIN that subtree only; any non-canonical import (``import fake as
#     os``, ``from os import environ as os``) is a shadow -> module-level poisons
#     file-wide, function-local poisons that subtree.
#   * Assign / AugAssign / AnnAssign / NamedExpr(walrus) bare-name targets,
#     ``for``/``with as``/``except as``/comprehension targets, function & lambda
#     params, nested def/class names, match captures (see ``_local_binding_names``)
#     -- module-level poison file-wide, function-local poison their own subtree.
#   * ``global`` / ``nonlocal`` -- an escaping rebind, poison file-wide.
#
# The CONSERVATIVE CATCH-ALL (rarer constructs that can bind/mutate a tracked name
# but carry no useful per-site value -- so a FILE-WIDE demote is cheap and closes
# the CLASS of holes, not one instance):
#   * ``from x import *`` (module OR function level) -- may shadow any tracked
#     name; poison ALL tracked names file-wide.
#   * ``del <tracked>`` (and ``del os.environ``) -- poison that name file-wide.
#   * ``type os = ...`` (``ast.TypeAlias``, 3.12+) -- poison that name file-wide.
#   * ``os.environ = ...`` / ``+=`` / annotated -- an Attribute ``X.environ``
#     assignment target replaces the mapping; poison the base name ``X`` file-wide.
#
# A receiver resolves GENUINE only when its name resolves (innermost enclosing
# scope first, then module scope) to a genuine binding of the expected kind and is
# not poisoned. MODULE-level ORDERING guard: a module-level call site lexically
# BEFORE the first module-level genuine import of its receiver name is unresolved
# -> 3b (the binding does not exist yet at import time). Ordering is NOT applied
# inside function bodies -- they run after module import time, so a call in a def
# defined above a bottom-of-file ``import os`` is still genuine. Any doubt -> 3b;
# a demote only loses bake-eligibility, never the env READ (recall is unaffected).
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


_POISON = "_poison"          # sentinel binding kind: name unusable in this scope


class _OsUsage:
    """Scope-aware resolution of genuine ``os`` / ``os.environ`` / ``os.getenv``
    receiver names for one file.

    ``scope_genuine`` maps each scope node (``None`` = module scope) to
    ``{name: kind}`` where ``kind`` is ``"os"`` / ``"environ"`` / ``"getenv"`` -- a
    genuine binding introduced IN that scope. ``scope_poison`` maps a function/
    class scope to the set of names it locally shadows (subtree-only demote).
    ``module_poison`` names are unusable FILE-WIDE: module-level rebinds,
    ``global``/``nonlocal`` escapes, and every conservative catch-all construct.
    ``genuine_lineno`` records the first module-level genuine-binding line per name
    for the module-level ordering guard.

    A name resolves at a call site only when the innermost binding on its scope
    chain (then module scope) is a genuine binding of the expected kind and it is
    not poisoned. Poison is checked BEFORE genuine within each scope, so the result
    is independent of AST walk order (a name both imported genuine and later
    rebound in the same scope resolves to poisoned)."""

    __slots__ = ("scope_genuine", "scope_poison", "module_poison", "genuine_lineno")

    def __init__(self, scope_genuine, scope_poison, module_poison, genuine_lineno):
        self.scope_genuine = scope_genuine        # scope|None -> {name: kind}
        self.scope_poison = scope_poison          # scope -> frozenset[str] (function-local)
        self.module_poison = module_poison        # frozenset[str] (file-wide)
        self.genuine_lineno = genuine_lineno      # name -> first module-level genuine lineno

    def _resolve_kind(self, name: str, chain: tuple, lineno: int | None) -> str | None:
        if name in self.module_poison:
            return None                           # file-wide poison wins over everything
        for scope in reversed(chain):             # innermost enclosing scope first
            if name in self.scope_poison.get(scope, frozenset()):
                return None                       # local shadow wins over genuine in-scope
            g = self.scope_genuine.get(scope)
            if g is not None and name in g:
                return g[name]
        g = self.scope_genuine.get(None)          # module scope
        if g is not None and name in g:
            if not chain and lineno is not None:  # ordering guard: MODULE-level sites only
                first = self.genuine_lineno.get(name)
                if first is not None and lineno < first:
                    return None                   # call precedes the genuine import
            return g[name]
        return None

    def is_os_module(self, name: str, chain: tuple, lineno: int | None = None) -> bool:
        return self._resolve_kind(name, chain, lineno) == "os"

    def is_environ_name(self, name: str, chain: tuple, lineno: int | None = None) -> bool:
        return self._resolve_kind(name, chain, lineno) == "environ"

    def is_getenv_name(self, name: str, chain: tuple, lineno: int | None = None) -> bool:
        return self._resolve_kind(name, chain, lineno) == "getenv"


def _poison_environ_attr_targets(node: ast.AST, module_poison: set) -> None:
    """``os.environ = {}`` / ``os.environ += x`` / ``os.environ: T = {}`` -- an
    assignment whose TARGET is an Attribute ``X.environ`` replaces (or mutates) the
    mapping, so ``X``'s ``.environ`` can no longer be proven genuine. Poison the
    base name ``X`` FILE-WIDE (conservative catch-all; rare in real repos)."""
    if isinstance(node, ast.Assign):
        targets: tuple = tuple(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = (node.target,)
    else:
        return
    for t in targets:
        if (isinstance(t, ast.Attribute) and t.attr == "environ"
                and isinstance(t.value, ast.Name)):
            module_poison.add(t.value.id)


def _analyze_os_usage(tree: ast.AST) -> tuple:
    """Return ``(usage, node_scopes)`` for one file: an ``_OsUsage`` plus a map
    from each ``Call``/``Subscript`` node to its scope chain (so the scanners can
    resolve a receiver per call site while still walking in document order).

    Polarity (round 4): only the handled-set of binding constructs is modeled
    affirmatively; every other name-binding/mutating construct in the conservative
    catch-all DEMOTES (poisons) rather than being ignored -- see the module comment
    above. Do NOT add a genuine binding here without extending the handled-set."""
    scope_genuine: dict = {}                             # scope|None -> {name: kind}
    scope_poison: dict = {}                              # scope -> set[str] (function-local)
    module_poison: set = set()                           # file-wide poison
    genuine_lineno: dict = {}                            # name -> first module-level genuine line
    genuine_names: set = set()                           # every name ever bound genuine
    node_scopes: dict = {}
    has_star = False

    def add_genuine(scope, name: str, kind: str, lineno: int) -> None:
        scope_genuine.setdefault(scope, {})[name] = kind
        genuine_names.add(name)
        if scope is None:
            genuine_lineno[name] = min(genuine_lineno.get(name, lineno), lineno)

    def add_poison(scope, name: str) -> None:
        if scope is None:
            module_poison.add(name)                      # module-level -> file-wide
        else:
            scope_poison.setdefault(scope, set()).add(name)  # function-local -> subtree

    for node, chain in _iter_with_scopes(tree):
        scope = chain[-1] if chain else None
        if isinstance(node, (ast.Call, ast.Subscript)):
            node_scopes[node] = chain
            continue                                     # a call/subscript binds no name
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            has_star = True                              # `from x import *` may shadow any name
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for local, is_genuine, kind in _import_bindings(node):
                if is_genuine:
                    add_genuine(scope, local, kind, getattr(node, "lineno", 0))
                else:
                    add_poison(scope, local)             # shadowing / unrelated import
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            module_poison.update(node.names)             # escaping rebind -> poison everywhere
            continue
        if isinstance(node, ast.Delete):
            for t in node.targets:                       # `del os` / `del os.environ`
                if isinstance(t, ast.Name):
                    module_poison.add(t.id)
                elif (isinstance(t, ast.Attribute) and t.attr == "environ"
                        and isinstance(t.value, ast.Name)):
                    module_poison.add(t.value.id)
            continue
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            if isinstance(node.name, ast.Name):          # `type os = ...` (3.12+)
                module_poison.add(node.name.id)
            continue
        _poison_environ_attr_targets(node, module_poison)   # `os.environ = {}` etc.
        for name in _local_binding_names(node):          # handled-set local bindings
            add_poison(scope, name)

    if has_star:
        module_poison |= genuine_names                   # a star import may shadow any tracked name

    usage = _OsUsage(
        {s: dict(b) for s, b in scope_genuine.items()},
        {s: frozenset(v) for s, v in scope_poison.items()},
        frozenset(module_poison),
        dict(genuine_lineno),
    )
    return usage, node_scopes


def _resolves_to_os_environ(owner: ast.AST, usage: _OsUsage, chain: tuple,
                            lineno: int | None = None) -> bool:
    """True only when ``owner`` GENUINELY resolves to ``os.environ`` at this call
    site -- a name bound (non-poisoned) via ``from os import environ [as X]``, or
    ``<genuine-os-alias>.environ`` with a non-poisoned base. A bare
    ``settings.environ`` base, or any poisoned/rebound name, is NOT genuine.
    ``lineno`` (the call-site line) feeds the module-level ordering guard."""
    if isinstance(owner, ast.Name):
        return usage.is_environ_name(owner.id, chain, lineno)
    if isinstance(owner, ast.Attribute) and owner.attr == "environ":
        base = owner.value
        return isinstance(base, ast.Name) and usage.is_os_module(base.id, chain, lineno)
    return False


def _is_environ_receiver(owner: ast.AST, usage: _OsUsage, chain: tuple,
                         lineno: int | None = None) -> bool:
    """Broad DETECTION of an ``environ``-shaped receiver worth surfacing as a
    config READ (advisory). A strict superset of ``_resolves_to_os_environ``: it
    keeps the pre-alias matcher UNCONDITIONALLY (a bare ``environ`` name, any
    ``*.environ`` attribute), so recall never drops, and additionally accepts a
    genuine, non-poisoned ``environ`` import alias. An unresolved
    ``settings.environ`` is still surfaced -- but classified advisory (3b)."""
    if isinstance(owner, ast.Name):
        return owner.id == "environ" or usage.is_environ_name(owner.id, chain, lineno)
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
    lineno = getattr(call, "lineno", None)
    if isinstance(func, ast.Attribute):
        if func.attr in ("get", "setdefault"):
            if not _is_environ_receiver(func.value, usage, chain, lineno):
                return None
        elif func.attr != "getenv":
            return None
        return _const_str(call.args[0]) if call.args else None
    if isinstance(func, ast.Name) and usage.is_getenv_name(func.id, chain, lineno):
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
    if not _is_environ_receiver(sub.value, usage, chain, getattr(sub, "lineno", None)):
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
            and _resolves_to_os_environ(func.value, usage, chain, getattr(call, "lineno", None)))


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
