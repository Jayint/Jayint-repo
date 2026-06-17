"""Enums, the closed edge-validity table, ownership sets, and secret redaction."""
from __future__ import annotations

import enum
import re


class NodeType(enum.Enum):
    REPO_ARTIFACT = "RepoArtifact"
    REQUIREMENT = "Requirement"
    CONTRACT = "Contract"
    CAPABILITY = "Capability"
    FAILURE = "Failure"
    TRANSITION = "Transition"
    VALIDATOR = "Validator"
    COMMAND_EXECUTION = "CommandExecution"
    ENVIRONMENT_REVISION = "EnvironmentRevision"
    VERIFICATION_TARGET = "VerificationTarget"
    OPEN_PROBLEM = "OpenProblem"


class EdgeType(enum.Enum):
    DECLARES = "declares"                  # RepoArtifact -> Requirement
    IMPLIES_CONTRACT = "implies_contract"  # Requirement -> Contract
    DEPENDS_ON = "depends_on"              # Contract -> Contract
    VIOLATES = "violates"                  # Failure -> Contract
    REPAIRED_BY = "repaired_by"            # Contract -> Transition
    TARGETS = "targets"                    # Transition -> Contract|Failure|OpenProblem
    VERIFIED_BY = "verified_by"            # Contract -> Validator
    SATISFIED_BY = "satisfied_by"          # Contract -> Capability
    BLOCKS = "blocks"                      # OpenProblem -> Contract
    CREATES_REVISION = "creates_revision"  # CommandExecution -> EnvironmentRevision
    OBSERVED_IN = "observed_in"            # Failure -> CommandExecution
    EXECUTED_AS = "executed_as"            # Transition -> CommandExecution


class ContractStatus(enum.Enum):
    UNKNOWN = "unknown"
    VIOLATED = "violated"
    REPAIR_ATTEMPTED = "repair_attempted"
    SATISFIED = "satisfied"
    INVALIDATED = "invalidated"


class ValidationState(enum.Enum):
    UNKNOWN = "validator_unknown"
    CANDIDATE = "validator_candidate"
    CONFIRMED = "validator_confirmed"


class ContractLevel(enum.Enum):
    ATOMIC = "atomic"
    GOAL = "goal"


_NT = NodeType
# Closed edge set (spec §6). value -> (allowed source types, allowed target types).
EDGE_RULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    EdgeType.DECLARES.value: (frozenset({_NT.REPO_ARTIFACT.value}), frozenset({_NT.REQUIREMENT.value})),
    EdgeType.IMPLIES_CONTRACT.value: (frozenset({_NT.REQUIREMENT.value}), frozenset({_NT.CONTRACT.value})),
    EdgeType.DEPENDS_ON.value: (frozenset({_NT.CONTRACT.value}), frozenset({_NT.CONTRACT.value})),
    EdgeType.VIOLATES.value: (frozenset({_NT.FAILURE.value}), frozenset({_NT.CONTRACT.value})),
    EdgeType.REPAIRED_BY.value: (frozenset({_NT.CONTRACT.value}), frozenset({_NT.TRANSITION.value})),
    EdgeType.TARGETS.value: (
        frozenset({_NT.TRANSITION.value}),
        frozenset({_NT.CONTRACT.value, _NT.FAILURE.value, _NT.OPEN_PROBLEM.value}),
    ),
    EdgeType.VERIFIED_BY.value: (frozenset({_NT.CONTRACT.value}), frozenset({_NT.VALIDATOR.value})),
    EdgeType.SATISFIED_BY.value: (frozenset({_NT.CONTRACT.value}), frozenset({_NT.CAPABILITY.value})),
    EdgeType.BLOCKS.value: (frozenset({_NT.OPEN_PROBLEM.value}), frozenset({_NT.CONTRACT.value})),
    EdgeType.CREATES_REVISION.value: (
        frozenset({_NT.COMMAND_EXECUTION.value}),
        frozenset({_NT.ENVIRONMENT_REVISION.value}),
    ),
    EdgeType.OBSERVED_IN.value: (frozenset({_NT.FAILURE.value}), frozenset({_NT.COMMAND_EXECUTION.value})),
    EdgeType.EXECUTED_AS.value: (frozenset({_NT.TRANSITION.value}), frozenset({_NT.COMMAND_EXECUTION.value})),
}

# Locked decision 3: host owns factual nodes; Maintainer adds only semantic nodes.
HOST_OWNED_NODE_TYPES: frozenset[str] = frozenset(
    {
        _NT.REPO_ARTIFACT.value,
        _NT.REQUIREMENT.value,
        _NT.CAPABILITY.value,
        _NT.FAILURE.value,
        _NT.OPEN_PROBLEM.value,
        _NT.COMMAND_EXECUTION.value,
        _NT.ENVIRONMENT_REVISION.value,
        _NT.VERIFICATION_TARGET.value,
    }
)
MAINTAINER_NODE_TYPES: frozenset[str] = frozenset(
    {_NT.CONTRACT.value, _NT.TRANSITION.value, _NT.VALIDATOR.value}
)

VALID_NODE_TYPES: frozenset[str] = frozenset(nt.value for nt in NodeType)
VALID_EDGE_TYPES: frozenset[str] = frozenset(et.value for et in EdgeType)
VALID_STATUSES: frozenset[str] = frozenset(s.value for s in ContractStatus)

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bgh[ps]_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def redact_secrets(text: str | None) -> str:
    """Mask common secret shapes before any text enters the graph (spec §15)."""
    if not text:
        return ""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out
