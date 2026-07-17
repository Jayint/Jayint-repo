"""LLM import->distribution guesser — the install-lane candidate rung.

Injectable, network-free factory mirroring ``llm_classifier.make_llm_classifier``:
``make_dist_guesser(complete_fn)`` returns a ``(import_name, symbols) -> list[str]``
callable matching ``repair.DistGuesser``. The pure ``python_deps`` repair ladder
stays LLM-free; this src.envstate module is the allowed bridge. Every returned
name is still RECORD-grounded downstream — hallucinations are denied there.
"""
from __future__ import annotations

from collections.abc import Callable

from graph.util import extract_json_object

_SYSTEM_PROMPT = (
    "You map a Python import to the PyPI distribution(s) that provide it. You are "
    "given the import's top-level name and the attributes/functions the code uses on "
    "it — the usage disambiguates look-alike names. Respond with ONLY a JSON object "
    '{"distributions": [names...]}: real PyPI distribution names, most-likely first, '
    "or an empty list if you do not know. Do NOT return the import name itself unless "
    "you are certain it is the real PyPI distribution name."
)


def _build_messages(import_name: str, symbols: tuple[str, ...]) -> list[dict]:
    used = ", ".join(sorted(symbols)) or "(none observed)"
    user = (
        f"import: {import_name}\nsymbols used: {used}\n\n"
        "Respond with ONLY the JSON object."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def make_dist_guesser(
    complete_fn: Callable[[list[dict]], str],
    *,
    cache: dict[tuple[str, tuple[str, ...]], list[str]] | None = None,
) -> Callable[[str, tuple[str, ...]], list[str]]:
    store = cache if cache is not None else {}

    def guess(import_name: str, symbols: tuple[str, ...]) -> list[str]:
        key = (import_name, tuple(sorted(symbols)))
        if key in store:
            return store[key]
        try:
            raw = complete_fn(_build_messages(import_name, symbols))
        except Exception:
            store[key] = []
            return []
        obj = extract_json_object(raw) or {}
        dists = obj.get("distributions") if isinstance(obj, dict) else None
        out = (
            [d.strip() for d in dists if isinstance(d, str) and d.strip()]
            if isinstance(dists, list)
            else []
        )
        store[key] = out
        return out

    return guess
