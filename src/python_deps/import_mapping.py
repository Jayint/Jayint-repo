from __future__ import annotations

import re
from dataclasses import dataclass


CURATED_IMPORT_TO_PACKAGE = {
    "bs4": "beautifulsoup4",
    "crypto": "pycryptodome",
    "cv2": "opencv-python",
    "django_filters": "django-filter",
    "factory": "factory-boy",
    "fitz": "PyMuPDF",
    "github": "PyGithub",
    "lxml": "lxml",
    "mysqldb": "mysqlclient",
    "openssl": "pyOpenSSL",
    "pil": "Pillow",
    "psycopg2": "psycopg2",
    "sklearn": "scikit-learn",
    "socketio": "python-socketio",
    "yaml": "PyYAML",
}

SOURCE_LABELS = {
    "collision_table": "Collision Table",
    "declared_metadata": "Jayint declared metadata extension",
    "direct_name": "Lookup Name Variants",
    "unresolved": "Unresolved (no distribution guess)",
}


@dataclass(frozen=True)
class MappingResult:
    import_name: str
    package_name: str | None
    source: str
    trust: str


UNRESOLVED_SOURCE = "unresolved"


def is_unresolved(result: MappingResult) -> bool:
    """True when the mapper could not resolve the import to a distribution.
    Callers MUST NOT fabricate a root from an unresolved result — skip it and
    let post-install Tier-1 certification (relink) attach a real provider, or
    leave the import honestly unresolved."""
    return result.source == UNRESOLVED_SOURCE


def unresolved_result(import_name: str) -> MappingResult:
    """The canonical 'no distribution guess' result."""
    return MappingResult(
        import_name=import_name,
        package_name=None,
        source=UNRESOLVED_SOURCE,
        trust="none",
    )


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name according to Python packaging conventions."""
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level_import_name(import_name: str) -> str:
    return import_name.split(".", 1)[0]


def declared_metadata_match(
    import_name: str, declared_package_names: "set[str] | frozenset[str] | None"
) -> str | None:
    """The repo's OWN declared distribution matching ``import_name``'s
    top-level module by normalized-name equality, or ``None`` if none matches.

    This is EVIDENCE, not a guess: it only ever returns a name the repo itself
    wrote down as a dependency somewhere in its manifest (any group). Factored
    out of :func:`map_import_to_package` so
    ``depgraph.python.lanes.install.repair.generate_candidates`` (:func:`~graph.python.lanes.install.repair.declared_candidates`)
    can reuse the exact same rung as an additional candidate source, instead of
    re-implementing (and risking drift from) this matching logic.
    """
    top_level = top_level_import_name(import_name)
    declared_by_normalized_name = {
        normalize_package_name(name): name for name in declared_package_names or set()
    }
    normalized_import = normalize_package_name(top_level)
    return declared_by_normalized_name.get(normalized_import)


def map_import_to_package(
    import_name: str,
    declared_package_names: set[str] | None = None,
) -> MappingResult:
    top_level = top_level_import_name(import_name)
    curated_key = top_level.lower()
    if curated_key in CURATED_IMPORT_TO_PACKAGE:
        return MappingResult(
            import_name=top_level,
            package_name=CURATED_IMPORT_TO_PACKAGE[curated_key],
            source="collision_table",
            trust="high",
        )

    declared_match = declared_metadata_match(top_level, declared_package_names)
    if declared_match is not None:
        return MappingResult(
            import_name=top_level,
            package_name=declared_match,
            source="declared_metadata",
            trust="medium",
        )

    return unresolved_result(top_level)


def mapping_source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())
