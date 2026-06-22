from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .import_mapping import normalize_package_name
from .models import PackageCandidate


PYPI_BASE_URL = "https://pypi.org/pypi"
DEFAULT_TIMEOUT_SECONDS = 10

# Regex for a valid normalized PyPI package name (post normalize_package_name).
# Only lowercase alphanumerics and hyphens are permitted — no '/', '%', or other chars.
_VALID_PYPI_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _is_valid_pypi_name(name: str) -> bool:
    """Return True iff *name* is a safe, normalized PyPI distribution name.

    A valid name must:
    - contain only lowercase alphanumerics and hyphens (post-normalization)
    - not contain '/', '%', or any other character that could alter a URL path

    Names are expected to have already been run through ``normalize_package_name``.
    """
    return bool(_VALID_PYPI_NAME_RE.match(name))


def _linux_marker_environment(python_version: str) -> dict[str, str]:
    env = default_environment()
    env.update(
        {
            "implementation_name": "cpython",
            "implementation_version": python_version,
            "os_name": "posix",
            "platform_machine": "x86_64",
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": "Linux",
            "platform_version": "",
            "python_full_version": python_version,
            "python_version": python_version,
            "sys_platform": "linux",
            "extra": "",
        }
    )
    return env


class PyPIMetadataClient:
    def __init__(
        self,
        *,
        cache_path: str | Path | None = None,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        offline: bool = False,
    ) -> None:
        self.cache_path = Path(cache_path) if cache_path else None
        self.offline = offline
        self.fetch_json = fetch_json or self._fetch_json
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, Any] = self._load_cache()

    def package_exists(self, package_name: str) -> bool:
        key = f"exists:{normalize_package_name(package_name)}"
        cached = self._cache.get(key)
        if isinstance(cached, bool):
            return cached
        if self.offline:
            return False
        try:
            self._get_project_json(package_name)
        except Exception:
            self._cache[key] = False
            self._save_cache()
            return False
        self._cache[key] = True
        self._save_cache()
        return True

    def get_package_candidates(
        self,
        package_name: str,
        *,
        python_versions: list[str] | None = None,
        max_versions: int = 8,
        preferred_versions: set[str] | None = None,
        required_specifiers: list[str] | None = None,
    ) -> list[PackageCandidate]:
        project_data = self._get_project_json(package_name)
        candidates = summarize_project_releases(project_data, package_name)
        candidates = [
            candidate
            for candidate in candidates
            if not candidate.yanked
            and _candidate_supports_any_python(candidate, python_versions or [])
        ]
        selected = select_era_biased_candidates(
            candidates,
            max_versions=max_versions,
            preferred_versions=preferred_versions or set(),
            required_specifiers=required_specifiers or [],
        )
        enriched: list[PackageCandidate] = []
        for candidate in selected:
            enriched.append(self.enrich_candidate(candidate))
        return enriched

    def enrich_candidate(self, candidate: PackageCandidate) -> PackageCandidate:
        normalized = normalize_package_name(candidate.name)
        key = f"version:{normalized}:{candidate.version}"
        cached = self._cache.get(key)
        if isinstance(cached, dict):
            return _candidate_with_version_metadata(candidate, cached)
        if self.offline:
            return candidate
        # Security: validate the normalized name before interpolating into a URL.
        if not _is_valid_pypi_name(normalized):
            return candidate
        try:
            data = self.fetch_json(
                f"{PYPI_BASE_URL}/{normalized}/{candidate.version}/json"
            )
        except Exception as error:
            self._cache[key] = {
                "error": str(error),
                "cached_at": int(time.time()),
                "requires_dist": list(candidate.requires_dist),
                "requires_python": candidate.requires_python,
            }
            self._save_cache()
            return candidate
        summary = {
            "requires_dist": data.get("info", {}).get("requires_dist") or [],
            "requires_python": data.get("info", {}).get("requires_python") or candidate.requires_python,
            "cached_at": int(time.time()),
        }
        self._cache[key] = summary
        self._save_cache()
        return _candidate_with_version_metadata(candidate, summary)

    def _get_project_json(self, package_name: str) -> dict[str, Any]:
        normalized = normalize_package_name(package_name)
        key = f"project:{normalized}"
        cached = self._cache.get(key)
        if isinstance(cached, dict) and "releases" in cached:
            return cached
        if self.offline:
            # Return a minimal empty project structure; no network call.
            # Intentionally not cached: offline empty stubs must not pollute the persistent cache.
            return {"info": {"name": package_name}, "releases": {}}
        # Security: validate the normalized name before interpolating it into a URL.
        # An invalid name (containing '/', '%', etc.) could alter the request path.
        if not _is_valid_pypi_name(normalized):
            return {"info": {"name": package_name}, "releases": {}}
        data = self.fetch_json(f"{PYPI_BASE_URL}/{normalized}/json")
        self._cache[key] = data
        self._save_cache()
        return data

    def _fetch_json(self, url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path or not self.cache_path.is_file():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return


def prime_metadata_cache(
    client: "PyPIMetadataClient",
    diagnostics: dict[str, Any],
) -> None:
    """Pre-warm the client's cache for the scoped package set (declared + mapped imports).

    Fetches candidates for:
    - All declared dependencies (any ``kind``).
    - All imported packages that have an ``import_package_mappings`` entry and whose
      import is ``external`` (regardless of ``source_files`` — the mapping is the signal).

    Errors for individual packages are silently ignored so that a missing package does not
    block the rest of the priming pass.  The client persists each successful fetch to its
    ``cache_path`` automatically.
    """
    packages_to_prime: set[str] = set()

    for dep in diagnostics.get("declared_dependencies") or []:
        name = dep.get("name", "").strip()
        if name:
            packages_to_prime.add(normalize_package_name(name))

    import_to_package: dict[str, str] = {}
    for mapping in diagnostics.get("import_package_mappings") or []:
        import_name = mapping.get("import_name", "").strip()
        pkg_name = mapping.get("package_name", "").strip()
        if import_name and pkg_name:
            import_to_package[import_name] = normalize_package_name(pkg_name)

    for imp in diagnostics.get("imports") or []:
        if imp.get("classification") != "external":
            continue
        import_name = imp.get("import_name", "").strip()
        if import_name and import_name in import_to_package:
            packages_to_prime.add(import_to_package[import_name])

    for pkg_name in sorted(packages_to_prime):
        try:
            client.get_package_candidates(pkg_name)
        except Exception:
            pass


def summarize_project_releases(data: dict[str, Any], package_name: str) -> list[PackageCandidate]:
    candidates: list[PackageCandidate] = []
    releases = data.get("releases") or {}
    canonical_name = data.get("info", {}).get("name") or package_name
    for version, files in releases.items():
        if not isinstance(files, list) or not files:
            continue
        upload_time = _first_upload_time(files)
        has_wheel = any(item.get("packagetype") == "bdist_wheel" for item in files if isinstance(item, dict))
        yanked = all(bool(item.get("yanked")) for item in files if isinstance(item, dict))
        requires_python = _first_requires_python(files) or ""
        candidates.append(
            PackageCandidate(
                name=normalize_package_name(canonical_name),
                version=version,
                requires_python=requires_python,
                upload_time=upload_time,
                has_wheel=has_wheel,
                yanked=yanked,
                source="pypi",
            )
        )
    return _sort_candidates(candidates)


def select_era_biased_candidates(
    candidates: list[PackageCandidate],
    *,
    max_versions: int = 8,
    preferred_versions: set[str] | None = None,
    required_specifiers: list[str] | None = None,
) -> list[PackageCandidate]:
    preferred_versions = preferred_versions or set()
    required_specifiers = required_specifiers or []
    if len(candidates) <= max_versions:
        return [
            _with_rank(candidate, rank)
            for rank, candidate in enumerate(_sort_candidates(candidates))
        ]

    selected: list[PackageCandidate] = []
    by_version = {candidate.version: candidate for candidate in candidates}
    for version in sorted(preferred_versions):
        candidate = by_version.get(version)
        if candidate and candidate not in selected:
            selected.append(candidate)

    for specifier in required_specifiers:
        matching_required = [
            candidate
            for candidate in candidates
            if _candidate_satisfies_any_specifier(candidate, [specifier])
            and candidate not in selected
        ]
        matching_required = sorted(
            matching_required,
            key=lambda candidate: _version_sort_key(candidate.version),
            reverse=True,
        )
        if matching_required:
            selected.append(matching_required[0])
        if len(selected) >= max_versions:
            break

    era = _estimate_era(candidates)
    ranked_by_era = sorted(
        candidates,
        key=lambda candidate: (
            _time_distance(candidate.upload_time, era),
            not candidate.has_wheel,
            _version_sort_key(candidate.version),
        ),
    )
    for candidate in ranked_by_era:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= min(5, max_versions):
            break

    remaining = [candidate for candidate in _sort_candidates(candidates) if candidate not in selected]
    slots = max_versions - len(selected)
    for candidate in _deterministic_sample(remaining, slots):
        selected.append(candidate)

    return [_with_rank(candidate, rank) for rank, candidate in enumerate(selected[:max_versions])]


def parse_requires_dist(requires_dist: tuple[str, ...] | list[str], python_version: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for requirement_text in requires_dist or []:
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            continue
        if requirement.marker is not None:
            env = _linux_marker_environment(python_version)
            try:
                if not requirement.marker.evaluate(env):
                    continue
            except Exception:
                continue
        parsed.append((normalize_package_name(requirement.name), str(requirement.specifier)))
    return parsed


def marker_applies_to_python(marker_text: str, python_version: str) -> bool:
    marker_text = (marker_text or "").strip()
    if not marker_text:
        return True
    try:
        requirement = Requirement(f"marker-probe; {marker_text}")
    except InvalidRequirement:
        return False
    if requirement.marker is None:
        return True
    env = _linux_marker_environment(python_version)
    try:
        return bool(requirement.marker.evaluate(env))
    except Exception:
        return False


def python_satisfies_specifier(python_version: str, specifier: str) -> bool:
    if not specifier:
        return True
    try:
        return Version(python_version) in SpecifierSet(specifier)
    except (InvalidVersion, InvalidSpecifier):
        return True


def _candidate_supports_any_python(candidate: PackageCandidate, python_versions: list[str]) -> bool:
    if not python_versions:
        return True
    return any(python_satisfies_specifier(version, candidate.requires_python) for version in python_versions)


def _candidate_satisfies_any_specifier(candidate: PackageCandidate, specifiers: list[str]) -> bool:
    specifiers = [specifier for specifier in specifiers if specifier]
    if not specifiers:
        return False
    for specifier in specifiers:
        try:
            if Version(candidate.version) in SpecifierSet(specifier):
                return True
        except (InvalidVersion, InvalidSpecifier):
            continue
    return False


def _candidate_with_version_metadata(candidate: PackageCandidate, metadata: dict[str, Any]) -> PackageCandidate:
    requires_dist = metadata.get("requires_dist") or []
    requires_python = metadata.get("requires_python") or candidate.requires_python
    return PackageCandidate(
        name=candidate.name,
        version=candidate.version,
        requires_python=requires_python,
        requires_dist=tuple(str(item) for item in requires_dist if isinstance(item, str)),
        upload_time=candidate.upload_time,
        has_wheel=candidate.has_wheel,
        yanked=candidate.yanked,
        source=candidate.source,
        rank=candidate.rank,
    )


def _with_rank(candidate: PackageCandidate, rank: int) -> PackageCandidate:
    return PackageCandidate(
        name=candidate.name,
        version=candidate.version,
        requires_python=candidate.requires_python,
        requires_dist=candidate.requires_dist,
        upload_time=candidate.upload_time,
        has_wheel=candidate.has_wheel,
        yanked=candidate.yanked,
        source=candidate.source,
        rank=rank,
    )


def _sort_candidates(candidates: list[PackageCandidate]) -> list[PackageCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            not candidate.has_wheel,
            _version_sort_key(candidate.version),
        ),
    )


def _version_sort_key(version: str) -> tuple[int, Any]:
    try:
        return (0, Version(version))
    except InvalidVersion:
        return (1, version)


def _first_upload_time(files: list[dict[str, Any]]) -> str | None:
    upload_times = [
        item.get("upload_time_iso_8601") or item.get("upload_time")
        for item in files
        if isinstance(item, dict) and (item.get("upload_time_iso_8601") or item.get("upload_time"))
    ]
    return sorted(upload_times)[0] if upload_times else None


def _first_requires_python(files: list[dict[str, Any]]) -> str | None:
    for item in files:
        if isinstance(item, dict) and item.get("requires_python"):
            return item["requires_python"]
    return None


def _estimate_era(candidates: list[PackageCandidate]) -> datetime | None:
    times = sorted(_parse_time(candidate.upload_time) for candidate in candidates)
    times = [item for item in times if item is not None]
    if not times:
        return None
    return times[len(times) // 2]


def _time_distance(upload_time: str | None, era: datetime | None) -> float:
    if era is None:
        return 0.0
    parsed = _parse_time(upload_time)
    if parsed is None:
        return float("inf")
    return abs((parsed - era).total_seconds())


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _deterministic_sample(candidates: list[PackageCandidate], count: int) -> list[PackageCandidate]:
    if count <= 0 or not candidates:
        return []
    if len(candidates) <= count:
        return candidates
    if count == 1:
        return [candidates[len(candidates) // 2]]
    selected = []
    last_index = len(candidates) - 1
    for index in range(count):
        sample_index = round(index * last_index / (count - 1))
        selected.append(candidates[sample_index])
    return selected
