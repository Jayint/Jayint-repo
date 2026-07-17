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

from graph.schema import DiscoveredBy, Layer, Node, NodeType, State

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
# Vendored-fixture exclusion — LAST-RESORT fallback ONLY (`scan_env_defaults`).
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
# is applied ONLY inside `scan_env_defaults` (the value-producing fallback) —
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


def _var_from_call(call: ast.Call) -> str | None:
    """``os.getenv('X')`` / ``os.environ.get('X')`` / ``os.environ.setdefault('X', ...)``
    -> ``'X'`` (first str arg).

    ``setdefault`` is the canonical Django ``manage.py``/``wsgi.py`` idiom
    (``os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')``) — without
    it the scanner is blind to essentially every Django project's entrypoint
    (FIX A1)."""
    func = call.func
    name = None
    if isinstance(func, ast.Attribute):
        name = func.attr
    if name not in ("getenv", "get", "setdefault"):
        return None
    # environ.get / environ.setdefault must be on an `environ` object to count
    # (both are extremely common method names on plain dicts too).
    if name in ("get", "setdefault"):
        owner = func.value
        owner_ok = (isinstance(owner, ast.Name) and owner.id == "environ") or (
            isinstance(owner, ast.Attribute) and owner.attr == "environ"
        )
        if not owner_ok:
            return None
    return _const_str(call.args[0]) if call.args else None


def _default_from_call(call: ast.Call) -> tuple[str, str] | None:
    """``os.environ.get('X', 'literal')`` / ``os.getenv('X', 'literal')`` /
    ``os.environ.setdefault('X', 'literal')`` -> ``('X', 'literal')``; only when
    BOTH the name and the default are string literals (f-strings / non-str
    defaults are not statically resolvable).

    For ``setdefault`` the 2nd positional arg is not just a fallback read value
    but the value ``setdefault`` itself WRITES into the environment when the
    var is absent — i.e. it is authoritative, not a guess (FIX A1's bonus:
    ``DJANGO_SETTINGS_MODULE=settings`` is exactly what gold's winning
    Dockerfile hand-writes for a Django ``manage.py``)."""
    var = _var_from_call(call)
    if var is None or len(call.args) < 2:
        return None
    default = _const_str(call.args[1])
    if default is None:
        return None
    return var, default


def _var_from_subscript(sub: ast.Subscript) -> str | None:
    """``os.environ['X']`` / ``environ['X']`` -> ``'X'``."""
    value = sub.value
    is_environ = (isinstance(value, ast.Name) and value.id == "environ") or (
        isinstance(value, ast.Attribute) and value.attr == "environ"
    )
    if not is_environ:
        return None
    return _const_str(sub.slice)


def _scan_source(src: str, rel: str, out: dict[str, str]) -> None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        var = None
        if isinstance(node, ast.Call):
            var = _var_from_call(node)
        elif isinstance(node, ast.Subscript):
            var = _var_from_subscript(node)
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


def scan_env_defaults(repo_path: str) -> dict[str, str]:
    """Map env var -> its literal default from ``os.environ.get(VAR, 'literal')``.

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
    ``depgraph.repair.choose_provider``'s AMBIGUOUS branch -- never pick a
    variant -- because "first file wins" previously let a value from a test
    fixture or example app silently shadow the real one.

    MEASURED REGRESSION FIX: a vendored/example/fixture path (``tests/app/``,
    ``examples/``, ...) is skipped entirely here -- see ``_is_vendored_fixture``
    -- because this scan is the FALLBACK OF LAST RESORT: a value it finds is
    only ever used when no authoritative config file names the var. A bundled
    example app's own default is real code but not the project's configuration.
    """
    hits: dict[str, set[str]] = {}
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
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    hit = _default_from_call(node)
                    if hit:
                        hits.setdefault(hit[0], set()).add(hit[1])
    return {var: next(iter(values)) for var, values in hits.items() if len(values) == 1}


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


def parse_env_example(repo_path: str) -> dict[str, str]:
    """`{VAR: example_value}` from .env.example/.sample/.template (value hints)."""
    out: dict[str, str] = {}
    for fname in (".env.example", ".env.sample", ".env.template"):
        out.update(_read_env_pairs(os.path.join(repo_path, fname)))
    return out


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
