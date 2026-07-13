"""Labelled (stderr, before, after) -> Label pairs mined from real repo2run_benchmark
runs (design spec docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md §2).

THE IDEA: the label is the agent's OWN repair. Diff the Dockerfile before vs after a
build failure; the apt/pip packages it ADDED are, by construction, what that failure
actually required. That is free, real, supervised signal -- no hand-labelling.

Two verified traps this parser is built around (spec §2.1/§2.2):
  1. `run_summary.failed_actions[].observation_summary` is NOT raw stderr -- it is
     repo2run's own `[SYSTEM] ...` wrapper prose. We never read it. The only raw
     signal used here is `docker_build.stderr` (when the build itself failed) or the
     captured pytest stdout (when the build succeeded but the test run after it did
     not -- see `_failure_text`).
  2. Every install in this corpus is buried in a `JAYINT_PIP_ATTEMPT` /
     `JAYINT_APT_ATTEMPT` retry loop, with the real command inside
     `/bin/sh -lc '<cmd>'`. A naive line-anchored parser ("does this line START WITH
     `RUN pip install`?") finds NOTHING here. `dockerfile_installs` scans for the
     keyword anywhere in the text (and additionally isolates every `/bin/sh -lc`
     payload) so the retry wrapper cannot hide an install from it.

A THIRD thing, not anticipated by the plan, that this file had to adapt to: the
plan asserted `before(round i) = dockerfile_repair_rounds[i-1].dockerfile_text`.
That holds for most rounds, but NOT all of them -- repo2run's own harness
sometimes inserts an automatic pip-constraints-pinning `RUN printf ... >
/tmp/jayint-pip-constraints.txt` line into the Dockerfile between a successful
build and the NEXT round's LLM call, and that harness-inserted line is never
written back into `dockerfile_repair_rounds`. Reconstructing `before` from the
previous round's `dockerfile_text` silently loses it (verified: ~35% of
multi-round repos in this corpus diverge this way). So `before` -- and the raw
failure text -- are instead read directly out of the `Input JSON` embedded in
`eval_artifacts/<repo>/dockerfile_repair_round_N.md`: that is *exactly* what the
repair LLM saw for that round, self-consistent by construction, and it needs no
reconstruction at all.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_RESULTS_DIR = "outputs/repo2run_benchmark/results"
_DEFAULT_ARTIFACTS_DIR = "outputs/repo2run_benchmark/eval_artifacts"

_KINDS = ("os-package", "python-package", "not-an-env-fix")


@dataclass(frozen=True)
class Label:
    apt: frozenset[str]
    pip: frozenset[str]
    kind: str  # one of _KINDS


@dataclass(frozen=True)
class Pair:
    repo: str
    round_index: int
    stderr: str
    before: str
    after: str
    label: Label


# --------------------------------------------------------------------------- #
# dockerfile_installs -- what does this Dockerfile text actually try to install?
# --------------------------------------------------------------------------- #

_SH_LC_RE = re.compile(r"/bin/sh\s+-lc\s+'([^']*)'")
_CONTINUATION_RE = re.compile(r"\\\n[ \t]*")   # Dockerfile/shell line-continuation
_STOP_RE = re.compile(r"&&|\|\||;|\n")          # where a shell command clause ends

_APT_RE = re.compile(r"\bapt(?:-get)?\s+install\b")
_PIP_RE = re.compile(r"\bpip3?\s+install\b")

# pip options that consume the FOLLOWING token as a value (a file path, URL, etc.)
# rather than a package name -- e.g. `-r requirements.txt`, `-e .`, `-e /app`.
_PIP_CONSUMES_NEXT = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable",
    "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "-t", "--target", "--no-binary", "--only-binary", "--python-version",
    "--implementation", "--abi", "--platform", "--proxy", "--retries",
    "--timeout", "--cache-dir", "--src", "--prefix", "--root",
    "--trusted-host", "--build-option", "--global-option", "--install-option",
})

_VERSION_SPLIT_RE = re.compile(r"[=<>!~\[]")


def _shell_payloads(text: str) -> list[str]:
    """Every place a real install command could be hiding: the (continuation-collapsed)
    text itself -- which covers plain `RUN apt-get install ...` / `RUN pip install ...`
    -- plus every payload of a `/bin/sh -lc '<cmd>'` wrapper, which covers the
    JAYINT_*_ATTEMPT retry loops that bury the real command mid-line (trap 2)."""
    collapsed = _CONTINUATION_RE.sub(" ", text)
    payloads = [m.group(1) for m in _SH_LC_RE.finditer(collapsed)]
    return [collapsed, *payloads]


def _command_tail(text: str, start: int) -> str:
    """The rest of this shell clause: from `start` up to the next `&&`, `||`, `;`,
    or newline (whichever comes first), or to the end of the string."""
    m = _STOP_RE.search(text, start)
    return text[start:m.start()] if m else text[start:]


def _extract_names(text: str, keyword_re: re.Pattern[str],
                    consumes_next: frozenset[str]) -> frozenset[str]:
    names: set[str] = set()
    for m in keyword_re.finditer(text):
        skip_next = False
        for tok in _command_tail(text, m.end()).split():
            if skip_next:
                skip_next = False
                continue
            # strip wrapping quotes AND parens -- a `RUN (cmd1 || cmd2 || cmd3)`
            # fallback chain leaves the closing `)` glued to the last alternative's
            # final token (e.g. `poetry)`) since it isn't followed by a stop token.
            tok = tok.strip("'\"()")
            if not tok:
                continue
            if tok.startswith("-"):
                base = tok.split("=", 1)[0]
                if base in consumes_next and "=" not in tok:
                    skip_next = True
                continue
            if tok.startswith(".") or "/" in tok or "*" in tok:
                continue  # local/glob path (`-e .`, `-e /app`, `dist/*.whl`), not a named package
            if "://" in tok or tok.startswith("git+"):
                continue  # VCS/URL install spec, not a named registry package
            name = _VERSION_SPLIT_RE.split(tok, maxsplit=1)[0]
            # a bare `@` is what's left of a PEP 508 direct reference ("name @ url")
            # once its own url half is filtered out above -- not a package name.
            if name and any(c.isalnum() for c in name):
                names.add(name)
    return frozenset(names)


def dockerfile_installs(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(apt_names, pip_names) invoked anywhere in `text` -- including inside a
    `/bin/sh -lc '...'` retry-loop wrapper (see module docstring, trap 2)."""
    apt: set[str] = set()
    pip: set[str] = set()
    for block in _shell_payloads(text):
        apt |= _extract_names(block, _APT_RE, frozenset())
        pip |= _extract_names(block, _PIP_RE, _PIP_CONSUMES_NEXT)
    return frozenset(apt), frozenset(pip)


def label_for(before: str, after: str) -> Label:
    """The label IS the agent's own repair: whatever apt/pip package names `after`
    installs that `before` did not. A round that added neither is `not-an-env-fix`
    (spec §2.2) -- a source patch or bespoke build recipe, not an environment fact."""
    before_apt, before_pip = dockerfile_installs(before)
    after_apt, after_pip = dockerfile_installs(after)
    added_apt = after_apt - before_apt
    added_pip = after_pip - before_pip
    if added_apt:
        kind = "os-package"
    elif added_pip:
        kind = "python-package"
    else:
        kind = "not-an-env-fix"
    return Label(apt=added_apt, pip=added_pip, kind=kind)


# --------------------------------------------------------------------------- #
# load_pairs -- walk the real corpus on disk
# --------------------------------------------------------------------------- #

def _read_llm_input(artifacts_dir: Path, repo: str, round_num: int) -> dict | None:
    """Parse the `Input JSON:` block embedded in a repair-round prompt log -- this
    is exactly what the repair LLM saw for that round: the before Dockerfile AND
    the raw failure feedback, self-consistently (see module docstring, trap 3)."""
    path = artifacts_dir / repo / f"dockerfile_repair_round_{round_num}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = text.find("Input JSON:")
    if marker == -1:
        return None
    try:
        start = text.index("{", marker)
    except ValueError:
        return None
    try:
        payload, _end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _failure_text(payload: dict) -> str:
    """The raw failure text behind this round: `docker_build.stderr` when the build
    itself failed; otherwise (roughly half this corpus -- the build succeeded but the
    pytest run after it did not) the captured pytest output, since a clean build log
    carries no error signal at all. Never `observation_summary` (trap 1)."""
    docker_build = payload.get("docker_build") or {}
    returncode = docker_build.get("returncode")
    if returncode not in (0, None):
        return docker_build.get("stderr") or ""
    chunks: list[str] = []
    for result in payload.get("test_execution") or []:
        classification = result.get("classification") or {}
        failed = (classification.get("effective") is False
                  or result.get("returncode") not in (0, None))
        if not failed:
            continue
        piece = (result.get("stdout") or "") + (result.get("stderr") or "")
        if not piece.strip():
            # e.g. a bare timeout with no captured output -- still a real, factual
            # (not repo2run-authored-prose) description of what happened.
            reason = classification.get("reason", "test_failed")
            piece = f"[{reason}] {result.get('test_command', '')}"
        chunks.append(piece)
    return "\n".join(chunks) or (docker_build.get("stderr") or "")


def load_pairs(results_dir: str, artifacts_dir: str) -> list[Pair]:
    """Every labelled (stderr, before, after) triple this corpus actually contains.

    A round contributes a Pair only if it produced a non-empty `dockerfile_text`
    (`source == "llm_error"` rounds -- a dead LLM API call -- carry no usable
    Dockerfile and are skipped: 3 of 114 rounds in the current corpus) and its
    `Input JSON` prompt log is present and parses (111/111 in the current corpus).
    """
    results_path = Path(results_dir)
    artifacts_path = Path(artifacts_dir)
    pairs: list[Pair] = []
    for result_file in sorted(results_path.glob("*.json")):
        repo = result_file.stem
        try:
            record = json.loads(result_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rounds = record.get("dockerfile_repair_rounds") or []
        for j, round_ in enumerate(rounds):
            after = round_.get("dockerfile_text")
            if not after:
                continue  # llm_error round: no repair was actually produced
            round_num = round_.get("round", j + 1)
            payload = _read_llm_input(artifacts_path, repo, round_num)
            if payload is None:
                continue
            before = payload.get("dockerfile")
            if not before:
                continue
            pairs.append(Pair(
                repo=repo,
                round_index=round_num,
                stderr=_failure_text(payload),
                before=before,
                after=after,
                label=label_for(before, after),
            ))
    return pairs


# --------------------------------------------------------------------------- #
# --smoke: run the parser over the REAL corpus and report what it actually found
# --------------------------------------------------------------------------- #

def _smoke(results_dir: str, artifacts_dir: str) -> None:
    pairs = load_pairs(results_dir, artifacts_dir)
    by_kind = Counter(p.label.kind for p in pairs)

    print(f"total pairs: {len(pairs)}")
    for kind in _KINDS:
        print(f"  {kind}: {by_kind.get(kind, 0)}")
    unexpected = set(by_kind) - set(_KINDS)
    for kind in sorted(unexpected):
        print(f"  {kind} (UNEXPECTED kind!): {by_kind[kind]}")

    # By construction, kind != "not-an-env-fix" implies label.apt or label.pip is
    # non-empty (that is literally label_for's branch condition) -- so this count
    # is expected to be 0. Reported anyway per the eval's own honesty check.
    empty_but_labelled = sum(
        1 for p in pairs
        if p.label.kind != "not-an-env-fix" and not p.label.apt and not p.label.pip
    )
    print(f"labelled env-fix with EMPTY apt+pip (parser/label inconsistency): {empty_but_labelled}")

    # A more useful diagnostic in the same spirit: of the not-an-env-fix pairs, how
    # many nonetheless show a NEW apt/pip "install" keyword appearing in `after`
    # that wasn't in `before` -- i.e. a real install-shaped line the structured
    # name-extractor may have failed to pull a package name out of.
    def _install_keyword_count(text: str) -> int:
        return sum(1 for block in _shell_payloads(text)
                   for _ in _APT_RE.finditer(block)) + \
               sum(1 for block in _shell_payloads(text)
                   for _ in _PIP_RE.finditer(block))

    suspect = sum(
        1 for p in pairs
        if p.label.kind == "not-an-env-fix"
        and _install_keyword_count(p.after) > _install_keyword_count(p.before)
    )
    print(f"not-an-env-fix pairs with a NEW install-keyword line (possible parser gap): {suspect}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="run over the real corpus and report")
    ap.add_argument("--results-dir", default=_DEFAULT_RESULTS_DIR)
    ap.add_argument("--artifacts-dir", default=_DEFAULT_ARTIFACTS_DIR)
    args = ap.parse_args(argv)

    if args.smoke:
        _smoke(args.results_dir, args.artifacts_dir)
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
