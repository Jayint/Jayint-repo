from __future__ import annotations
import json
from typing import Any, Optional

from src.envstate.acl import apply_llm_proposal
from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.ledger import ActionEvent
from src.envstate.serde import snapshot_to_dict
from src.envstate.types import EnvStateSnapshot

try:  # reuse the existing residual-span extractor
    from src.memory_manager import select_failure_lines
except Exception:  # pragma: no cover - fallback if signature drifts
    def select_failure_lines(observation, max_lines=48):
        lines = [ln for ln in (observation or "").splitlines() if ln.strip()]
        return "\n".join(lines[-max_lines:])


MAINTAINER_SYSTEM_PROMPT = """You are the State Maintainer for DockerAgent environment setup.

You interpret ONE command observation and propose structured updates to the
environment-state map. You are an interpreter, not an authority.

You MAY propose:
- candidate_requirements with status REQUIRED or UNKNOWN only
- open_failure_updates (a signature + a hypothesis)
- diagnose_requests (provider lookups, e.g. which apt package provides a tool)
- probe_requests (host probes the orchestrator should run to certify truth)
- plan_notes (durable cautions)

You MUST NOT emit:
- status=PRESENT or status=MISSING
- any Evidence object
- final Dockerfile lines
- authoritative task completion

Only host probes may certify PRESENT/MISSING with Evidence. If you believe a
capability is present or missing, emit a probe_request so the host can verify it.
When you emit a probe_request, include a `requirement_id` matching the candidate
requirement it should certify (e.g. "tool:pg_config"), so the host's certified
PRESENT/MISSING updates that same requirement.

## EXACT OUTPUT SCHEMA — use these EXACT key names, no aliases

### candidate_requirements (each item):
```json
{
  "id": "tool:pg_config",
  "name": "pg_config",
  "kind": "Tool",
  "status": "REQUIRED",
  "specifier": ">=9.0"
}
```
- "id"       : REQUIRED. Format "<prefix>:<name>". Prefixes: pkg (LanguagePackage), tool (Tool), header (Header), lib (SharedLibrary), pkgconfig (PkgConfig).
- "name"     : REQUIRED. The bare capability name, e.g. "pg_config", "aiohttp", "openssl/ssl.h".
- "kind"     : REQUIRED. One of: LanguagePackage | Tool | Header | SharedLibrary | PkgConfig.
- "status"   : REQUIRED. One of: REQUIRED | UNKNOWN. NEVER use PRESENT, MISSING, or Evidence.
- "specifier": OPTIONAL. Version constraint, e.g. ">=3.8".

### probe_requests (each item):
```json
{
  "kind": "cli",
  "name": "pg_config",
  "predicate": "path exists",
  "requirement_id": "tool:pg_config"
}
```
- "kind"           : REQUIRED. One of: cli | python_import | header | pkgconfig.
- "name"           : REQUIRED. The thing to probe, e.g. "pg_config", "aiohttp".
- "predicate"      : OPTIONAL. Human description of what a passing result means.
- "requirement_id" : REQUIRED. The id of the candidate_requirement this probe certifies.

Return exactly one JSON object inside a ```json fenced block.
"""


def build_maintainer_input(
    previous_env_state_view: dict[str, Any],
    task_spec: dict[str, Any],
    action_event: ActionEvent,
    full_log: str,
) -> dict[str, Any]:
    return {
        "previous_env_state_view": previous_env_state_view,
        "task_spec": task_spec,
        "action_event": {
            "cmd": action_event.cmd,
            "rc": action_event.rc,
            "env_revision_before": action_event.env_revision_before,
            "env_revision_after": action_event.env_revision_after,
        },
        "residual_spans": select_failure_lines(full_log),
    }


# Map prefix strings to canonical kind values.
_ID_PREFIX_TO_KIND: dict[str, str] = {
    "pkg":       "LanguagePackage",
    "tool":      "Tool",
    "header":    "Header",
    "lib":       "SharedLibrary",
    "pkgconfig": "PkgConfig",
}

# Map alias keys → canonical key for candidate_requirements.
_REQ_KEY_ALIASES: dict[str, str] = {
    "req_id":    "id",
    "pkg_name":  "name",
    "package":   "name",
    "tool_name": "name",
    "cmd":       "name",
    "command":   "name",
}

# Aliases whose value maps to "specifier" only when "specifier" is absent.
_SPECIFIER_FALLBACKS = ("detail", "spec", "desc", "version_spec")


def _derive_name_from_id(id_val: str) -> str:
    """Strip a known kind prefix from an id, e.g. "pkg:aiohttp" -> "aiohttp"."""
    for prefix in _ID_PREFIX_TO_KIND:
        prefix_colon = prefix + ":"
        if id_val.startswith(prefix_colon):
            return id_val[len(prefix_colon):]
    # Unknown prefix (e.g. "path:repo_root") — strip up to first colon.
    if ":" in id_val:
        return id_val.split(":", 1)[1]
    return id_val


def _kind_from_id(id_val: str) -> Optional[str]:
    """Infer kind from an id prefix, e.g. "pkg:aiohttp" -> "LanguagePackage"."""
    for prefix, kind in _ID_PREFIX_TO_KIND.items():
        if id_val.startswith(prefix + ":"):
            return kind
    return None


def _kind_prefix(kind: str) -> str:
    """Return the canonical id prefix for a kind, used when synthesising id."""
    _REVERSE = {v: k for k, v in _ID_PREFIX_TO_KIND.items()}
    return _REVERSE.get(kind, "tool")


def _normalise_requirement(raw: Any) -> Any:
    """Normalise a single candidate_requirement dict in-place (returns a new dict).

    Never raises; returns the original object unchanged if it is not a dict.
    """
    if not isinstance(raw, dict):
        return raw
    out: dict[str, Any] = dict(raw)

    # 1. Map aliased keys to canonical.
    for alias, canonical in _REQ_KEY_ALIASES.items():
        if alias in out and canonical not in out:
            out[canonical] = out.pop(alias)
        elif alias in out:
            out.pop(alias)  # canonical already present; discard alias

    # 2. Derive name from id when name still absent.
    if not out.get("name") and out.get("id"):
        out["name"] = _derive_name_from_id(str(out["id"]))

    # 3. Derive kind from id prefix when kind absent.
    if not out.get("kind") and out.get("id"):
        inferred = _kind_from_id(str(out["id"]))
        if inferred:
            out["kind"] = inferred

    # 4. Default kind to "Tool" when still absent.
    if not out.get("kind"):
        out["kind"] = "Tool"

    # 5. Synthesise id from name+kind when id absent.
    if not out.get("id") and out.get("name"):
        prefix = _kind_prefix(out.get("kind", "Tool"))
        out["id"] = f"{prefix}:{out['name']}"

    # 6. Map specifier fallbacks (only when specifier absent).
    if not out.get("specifier"):
        for fb_key in _SPECIFIER_FALLBACKS:
            if fb_key in out:
                out["specifier"] = out.pop(fb_key)
                break
    else:
        # Remove fallback keys if specifier already present to keep the dict clean.
        for fb_key in _SPECIFIER_FALLBACKS:
            out.pop(fb_key, None)

    # 7. Coerce unrecognised source strings to LLM_GUESS so the ACL does not reject
    #    entries whose source field contains free-text (e.g. "likely in requirements.txt").
    _CANONICAL_SOURCES = frozenset(
        {"LLM_GUESS", "MEMORY", "STATIC_SCAN", "PROBE", "DIAGNOSE"}
    )
    if out.get("source") not in _CANONICAL_SOURCES:
        out["source"] = "LLM_GUESS"

    return out


def _normalise_probe_request(raw: Any) -> Any:
    """Normalise a single probe_request dict.

    Derives name from requirement_id or pkg_name when name is absent.
    Never raises.
    """
    if not isinstance(raw, dict):
        return raw
    out: dict[str, Any] = dict(raw)

    # Normalise pkg_name alias → name.
    if not out.get("name") and out.get("pkg_name"):
        out["name"] = out.pop("pkg_name")
    elif "pkg_name" in out:
        out.pop("pkg_name")

    # Derive name from requirement_id when still absent.
    if not out.get("name") and out.get("requirement_id"):
        out["name"] = _derive_name_from_id(str(out["requirement_id"]))

    return out


def parse_maintainer_proposal(content: Optional[str]) -> dict[str, Any]:
    raw = extract_json_object(content) or {}
    if not raw:
        return raw

    # Normalise candidate_requirements.
    if "candidate_requirements" in raw:
        raw["candidate_requirements"] = [
            _normalise_requirement(r) for r in (raw["candidate_requirements"] or [])
        ]

    # Normalise probe_requests.
    if "probe_requests" in raw:
        raw["probe_requests"] = [
            _normalise_probe_request(r) for r in (raw["probe_requests"] or [])
        ]

    return raw


class Maintainer:
    def __init__(self, client, model):
        self.client = client
        self.model = model

    def interpret(
        self,
        snapshot: EnvStateSnapshot,
        task_spec: dict[str, Any],
        action_event: ActionEvent,
        full_log: str,
    ):
        # The maintainer interprets each observation WITH the current map in view
        # (design §10 requires previous_env_state_view), so it can reconcile new
        # evidence against existing hypotheses instead of starting blind.
        payload = build_maintainer_input(
            snapshot_to_dict(snapshot), task_spec, action_event, full_log
        )
        messages = [
            {"role": "system", "content": MAINTAINER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ]
        content, usage, response = complete_with_retry(
            self.client,
            self.model,
            messages,
            accept=None,  # retry only on empty text
            retry_nudge=(
                "Your previous response was empty. "
                "Return exactly one JSON object inside a ```json fenced block."
            ),
            temperature=0,
        )
        proposal = parse_maintainer_proposal(content)
        updated, rejected = apply_llm_proposal(snapshot, proposal)
        parsed_summary = {"proposal_keys": list(proposal.keys()), "rejected_count": len(rejected)}
        log_llm_exchange("maintainer", response, parsed=parsed_summary)
        return updated, proposal, rejected, usage
