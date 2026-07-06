"""Eval stage 3 — translate-path quality on the SHIPPED exotic path.

Phase 1 of the service-provisioning eval. Drives the SHIPPED ``translate_service`` over
the exotic corpus (every kind with no deterministic recipe) and reports, N-sampled:

  * ``parse_fail_rate``     — draws where the model reply did not parse       (target 0.0)
  * ``arch_clean_rate``     — draws whose setup carries no foreign-arch literal (target 1.0)
  * ``probe_firewall_rate`` — draws whose setup probe is read-only or empty    (target 1.0)
  * ``verify_catch``        — hallucination-case draws where verify caught the fake URL
                              and demoted ``feasible`` to False                (target 1.0)

This measures the shipped code; it NEVER re-implements translate / verify / arch logic.
``translate_service`` internally runs ``full_translate`` (the LLM boundary) → ``apply_arch``
/ ``apply_env`` → ``verify_plan`` → ``normalize_probe``; we only read its output contract.
``arch_clean`` is the one local predicate — a cheap literal scan over the ALREADY-scrubbed
setup, so a non-zero-miss rate means the shipped ``apply_arch`` left a foreign token behind.

Live model access is opt-in (``--live``); without it the harness prints how to run and
exits with no network. The deterministic tests mock ONLY the LLM boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# SHIPPED functions under test — imported, never copied. ``full_translate`` (the LLM
# boundary) and ``verify_plan`` are driven INSIDE ``translate_service``; the tests
# monkeypatch them on ``envstate.service_translate`` so the rest of the pipeline is real.
from envstate.service_translate import full_translate, translate_service  # noqa: E402,F401
from python_deps.depgraph.patch_gate import is_read_only  # noqa: E402
from python_deps.depgraph.provisioning_spec import parse_provisioning_spec  # noqa: E402
from python_deps.depgraph.service_recipes import RECIPE_KINDS  # noqa: E402
from evals.service_config_detection.provision_corpus import PROVISION_CASES  # noqa: E402

import re as _re  # noqa: E402

# Word-boundary foreign-arch tokens (mirror translate_sanitize's boundaries so a bare
# ``amd64`` is flagged but ``app-amd64bar`` is not).
_AMD64_RE = _re.compile(r"(?<![A-Za-z0-9])amd64(?![A-Za-z0-9])")
_X86_64_RE = _re.compile(r"(?<![A-Za-z0-9])x86_64(?![A-Za-z0-9])")

_ARTIFACT = _ROOT / "evals" / "service_config_detection" / "artifacts" / "stage_translate.json"

_DEFAULT_ARCH = {"dpkg": "arm64", "uname": "aarch64"}


def _exotic_cases():
    """Corpus cases with no deterministic recipe (exotic = kind not in RECIPE_KINDS)."""
    return [c for c in PROVISION_CASES if c.kind not in RECIPE_KINDS]


def _arch_clean(setup: dict, arch: dict) -> bool:
    """True unless a foreign-arch literal survives in the setup's shell strings.

    When the target is not amd64, any word-boundary ``amd64``/``x86_64`` left in an
    ``install``/``start``/``post`` string means the shipped ``apply_arch`` failed to scrub
    it (it only rewrites arch tokens inside URL-context strings). On an amd64 target the
    token is native, so the setup is always clean.
    """
    if arch.get("dpkg") == "amd64":
        return True
    strings: list[str] = []
    for key in ("install", "post"):
        value = setup.get(key)
        if isinstance(value, list):
            strings.extend(s for s in value if isinstance(s, str))
    start = setup.get("start")
    if isinstance(start, str):
        strings.append(start)
    for s in strings:
        if _AMD64_RE.search(s) or _X86_64_RE.search(s):
            return False
    return True


def _draw(res: dict, arch: dict) -> dict:
    """Reduce one ``translate_service`` result to the per-draw metric record."""
    has_setup = res["setup"] is not None
    probe = res["setup"]["probe"] if has_setup else None
    return {
        "parse_failed": res["note"] == "parse-failed",
        "has_setup": has_setup,
        "arch_clean": _arch_clean(res["setup"], arch) if has_setup else True,
        "verify_all_ok": (res["verify"] or {}).get("all_ok"),
        "feasible": res["feasible"],
        "probe_ok": has_setup and (probe == "" or is_read_only(probe)),
        "probe": probe,
    }


def measure_translate(client, model: str, arch: dict, cases=None, n: int = 3) -> dict:
    """Drive the SHIPPED ``translate_service`` over ``cases`` (default: exotic corpus),
    N-sampled, and aggregate the stage-3 rates. See the module docstring for targets.

    ``client``/``model`` are forwarded verbatim into ``translate_service`` (and thus the
    LLM boundary). The deterministic tests pass a stub client and monkeypatch that
    boundary, so no network is touched.
    """
    cases = cases or _exotic_cases()
    per_case: list[dict] = []
    for case in cases:
        spec = parse_provisioning_spec(case.name, yaml.safe_load(case.compose_entry))
        draws = []
        for _ in range(n):
            res = translate_service(client, model, spec, arch)
            draws.append(_draw(res, arch))
        per_case.append({
            "name": case.name,
            "kind": case.kind,
            "known_failure": case.known_failure,
            "draws": draws,
        })

    all_draws = [d for pc in per_case for d in pc["draws"]]
    total = len(all_draws)
    setup_draws = [d for d in all_draws if d["has_setup"]]
    hall_draws = [
        d for pc in per_case if pc["known_failure"] == "hallucination" for d in pc["draws"]
    ]

    def _rate(num: int, den: int, empty: float | None) -> float | None:
        return (num / den) if den else empty

    parse_fail_rate = _rate(sum(1 for d in all_draws if d["parse_failed"]), total, 0.0)
    arch_clean_rate = _rate(sum(1 for d in setup_draws if d["arch_clean"]),
                            len(setup_draws), 1.0)
    probe_firewall_rate = _rate(sum(1 for d in setup_draws if d["probe_ok"]),
                                len(setup_draws), 1.0)
    # verify_catch: over hallucination-case draws, the fraction where verify said "not ok".
    # None (not 1.0) when no hallucination case is in scope — honest: not measured here.
    verify_catch = _rate(sum(1 for d in hall_draws if d["verify_all_ok"] is False),
                         len(hall_draws), None)

    return {
        "n": n,
        "cases": len(cases),
        "total_draws": total,
        "parse_fail_rate": parse_fail_rate,
        "arch_clean_rate": arch_clean_rate,
        "probe_firewall_rate": probe_firewall_rate,
        "verify_catch": verify_catch,
        "per_case": per_case,
    }


def _build_live_client():
    """OpenAI-compatible client from ``OPENROUTER_API_BASE``/``OPENROUTER_API_KEY``.

    No key is ever hardcoded; a missing base/key aborts before any network call.
    """
    import openai

    base = os.environ.get("OPENROUTER_API_BASE")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not (base and key):
        raise SystemExit(
            "--live needs OPENROUTER_API_BASE and OPENROUTER_API_KEY in the env."
        )
    # Bound the client: an unbounded per-request timeout lets ONE hung request wedge the
    # whole sequential N-draw run (observed: a 19-min stall). Per-call cap + 1 retry keeps
    # a slow/hung draw from blocking the measurement.
    return openai.OpenAI(base_url=base, api_key=key, timeout=45, max_retries=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="build a real OpenRouter client and run the LLM translate path")
    ap.add_argument("--n", type=int, default=3, help="draws per case (default 3)")
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash",
                    help="model id for the live run")
    ap.add_argument("--arch", default=json.dumps(_DEFAULT_ARCH),
                    help='target arch as JSON, e.g. \'{"dpkg":"arm64","uname":"aarch64"}\'')
    args = ap.parse_args()

    if not args.live:
        print("[stage_translate] no client — pass --live with OPENROUTER_API_BASE/"
              "OPENROUTER_API_KEY to measure the LLM translate path. "
              "(The deterministic tests run with no network.)")
        return

    arch = json.loads(args.arch)
    client = _build_live_client()
    result = measure_translate(client, args.model, arch, n=args.n)
    _ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    _ARTIFACT.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(
        f"[stage_translate] cases={result['cases']} n={result['n']} "
        f"draws={result['total_draws']} | "
        f"parse_fail_rate={result['parse_fail_rate']:.3f} "
        f"arch_clean_rate={result['arch_clean_rate']:.3f} "
        f"probe_firewall_rate={result['probe_firewall_rate']:.3f} "
        f"verify_catch={result['verify_catch']} -> {_ARTIFACT}"
    )


if __name__ == "__main__":
    main()
