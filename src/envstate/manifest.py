# src/envstate/manifest.py
"""Host-side manifest parser (shallow). Reads declared deps + build system
from a checked-out repo. Pure, never raises. Uses tomllib + packaging.
"""
from __future__ import annotations

import os
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 (e.g. the 3.10 benchmark box)
    import tomli as tomllib
from dataclasses import dataclass

from packaging.requirements import Requirement

from src.envstate.world_model import Fact


@dataclass(frozen=True)
class ManifestResult:
    build_system: str             # poetry|pip|setuptools|hatchling|flit|pipenv|unknown
    required: tuple[Fact, ...]    # Fact(name, detail=specifier); declared names only


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _req_fact(spec: str) -> Fact | None:
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return None
    try:
        r = Requirement(spec)
        return Fact(name=r.name, detail=str(r.specifier))
    except Exception:
        token = spec.split(";")[0].split("#")[0].strip()
        for sep in ("==", ">=", "<=", "~=", ">", "<", "[", " "):
            token = token.split(sep)[0].strip()
        return Fact(name=token) if token else None


def _parse_requirements_txt(workplace: str, filename: str, seen: set[str]) -> list[Fact]:
    path = os.path.join(workplace, filename)
    if path in seen:
        return []
    seen.add(path)
    text = _read_text(path)
    if text is None:
        return []
    facts: list[Fact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                facts.extend(_parse_requirements_txt(workplace, parts[1].strip(), seen))
            continue
        if line.startswith("-"):
            continue  # other pip flags (-e, --index-url, ...)
        f = _req_fact(line)
        if f:
            facts.append(f)
    return facts


def parse_manifests(workplace: str) -> ManifestResult:
    build_system = "unknown"
    facts: list[Fact] = []

    pyproject = None
    raw = _read_text(os.path.join(workplace, "pyproject.toml"))
    if raw is not None:
        try:
            pyproject = tomllib.loads(raw)
        except Exception:
            pyproject = None

    poetry = (pyproject or {}).get("tool", {}).get("poetry") if pyproject else None
    has_poetry_lock = os.path.exists(os.path.join(workplace, "poetry.lock"))
    has_pipfile = os.path.exists(os.path.join(workplace, "Pipfile"))
    has_setup = os.path.exists(os.path.join(workplace, "setup.py")) or \
        os.path.exists(os.path.join(workplace, "setup.cfg"))
    try:
        req_files = sorted(
            fn for fn in os.listdir(workplace)
            if fn.startswith("requirements") and fn.endswith(".txt")
        )
    except OSError:
        req_files = []

    # build_system precedence
    if has_poetry_lock or poetry:
        build_system = "poetry"
    elif pyproject and "build-system" in pyproject:
        backend = str(pyproject["build-system"].get("build-backend", ""))
        if "hatch" in backend:
            build_system = "hatchling"
        elif "flit" in backend:
            build_system = "flit"
        elif "poetry" in backend:
            build_system = "poetry"
        else:
            build_system = "setuptools"
    elif has_pipfile:
        build_system = "pipenv"
    elif req_files:
        build_system = "pip"
    elif has_setup:
        build_system = "setuptools"

    # required extraction
    if pyproject:
        for dep in (pyproject.get("project", {}).get("dependencies") or []):
            f = _req_fact(str(dep))
            if f:
                facts.append(f)
        if poetry:
            for name, val in (poetry.get("dependencies") or {}).items():
                if name.lower() == "python":
                    continue
                facts.append(Fact(name=name, detail=val if isinstance(val, str) else ""))

    seen: set[str] = set()
    for fn in req_files:
        facts.extend(_parse_requirements_txt(workplace, fn, seen))

    if has_pipfile:
        ptext = _read_text(os.path.join(workplace, "Pipfile"))
        if ptext is not None:
            try:
                pip = tomllib.loads(ptext)
                for name, val in (pip.get("packages") or {}).items():
                    detail = val if isinstance(val, str) and val != "*" else ""
                    facts.append(Fact(name=name, detail=detail))
            except Exception:
                pass

    # dedup by lowercased name (keep first)
    seen_names: set[str] = set()
    deduped: list[Fact] = []
    for f in facts:
        key = f.name.lower()
        if key and key not in seen_names:
            seen_names.add(key)
            deduped.append(f)

    return ManifestResult(build_system=build_system, required=tuple(deduped))
