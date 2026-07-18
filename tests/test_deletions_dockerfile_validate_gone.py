"""Sentinel: ``dockerfile_validate`` was shed in Phase 2 T7 of the src/ stage-refactor.

Its only production caller was the dead ``synthesizer.generate_dockerfile`` method
(zero live/test callers); the module + its dedicated test were deleted. Filesystem-based
(imports nothing) so it holds regardless of pytest partition. It must stay gone — a
re-add or a fresh importer trips this.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_dockerfile_validate_module_is_gone() -> None:
    assert not (_ROOT / "src" / "envstate" / "dockerfile_validate.py").exists()
    assert not (_ROOT / "src" / "orchestrate" / "loop" / "dockerfile_validate.py").exists()


def test_no_dockerfile_validate_importers() -> None:
    offenders = []
    for py in (_ROOT / "src").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for line in py.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith(("import", "from")) and "dockerfile_validate" in s:
                offenders.append(f"{py.relative_to(_ROOT)}: {s}")
    assert offenders == [], f"dockerfile_validate must have no importers: {offenders}"
