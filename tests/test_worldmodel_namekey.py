"""
Tests for the name-key schema mismatch fix.

The Maintainer (MiniMax) emits candidate_requirements with improvised keys like
req_id/pkg_name/detail instead of the canonical id/name/kind/status.
These tests verify that parse_maintainer_proposal normalises those aliases,
apply_llm_proposal yields a non-empty snapshot, and the trust boundary is
preserved throughout.
"""
import unittest
from types import SimpleNamespace

from src.envstate.acl import apply_llm_proposal
from src.envstate.maintainer import parse_maintainer_proposal, MAINTAINER_SYSTEM_PROMPT
from src.envstate.probes import ProbeSpec
from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    Requirement,
    Source,
    Status,
)


def _empty_snapshot(revision=0):
    return EnvStateSnapshot(
        revision=revision,
        container_id="c1",
        base=BaseFacts(image="python:3.11-slim"),
    )


# ---------------------------------------------------------------------------
# 1. parse_maintainer_proposal normalisation
# ---------------------------------------------------------------------------

class ParseNormalisationTests(unittest.TestCase):
    """parse_maintainer_proposal must normalise aliased keys to canonical form."""

    def _wrap_json(self, obj):
        import json
        return f"```json\n{json.dumps(obj)}\n```"

    # Real output observed in logs:
    # {"req_id":"pkg:aiohttp","pkg_name":"aiohttp","status":"UNKNOWN","source":"likely in requirements.txt"}
    def test_req_id_and_pkg_name_aliases_normalised(self):
        raw = {
            "candidate_requirements": [
                {
                    "req_id": "pkg:aiohttp",
                    "pkg_name": "aiohttp",
                    "status": "UNKNOWN",
                    "source": "likely in requirements.txt",
                }
            ],
            "probe_requests": [],
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        reqs = proposal.get("candidate_requirements", [])
        self.assertEqual(len(reqs), 1, "should have one candidate requirement")
        req = reqs[0]
        self.assertEqual(req.get("id"), "pkg:aiohttp", "id should be normalised from req_id")
        self.assertEqual(req.get("name"), "aiohttp", "name should be normalised from pkg_name")
        self.assertIn(req.get("kind"), ("LanguagePackage", "Tool"), "kind should be inferred from id prefix")
        self.assertIn(req.get("status"), ("UNKNOWN", "REQUIRED"), "status should be preserved")

    # Real output observed in logs:
    # {"id":"path:repo_root","status":"UNKNOWN","detail":"Actual repository root path"}
    def test_path_prefixed_id_derives_name_and_kind(self):
        raw = {
            "candidate_requirements": [
                {
                    "id": "path:repo_root",
                    "status": "UNKNOWN",
                    "detail": "Actual repository root path",
                }
            ],
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        reqs = proposal.get("candidate_requirements", [])
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertEqual(req.get("name"), "repo_root", "name should be derived from id by stripping path: prefix")
        # detail → specifier (only if specifier absent)
        self.assertIsNotNone(req.get("specifier"), "detail should map to specifier")

    def test_tool_prefix_id_derives_name_and_kind(self):
        raw = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "status": "REQUIRED"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("name"), "pg_config")
        self.assertEqual(req.get("kind"), "Tool")

    def test_pkg_prefix_id_derives_kind_language_package(self):
        raw = {
            "candidate_requirements": [
                {"id": "pkg:numpy", "status": "UNKNOWN"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("name"), "numpy")
        self.assertEqual(req.get("kind"), "LanguagePackage")

    def test_header_prefix_id_derives_kind_header(self):
        raw = {
            "candidate_requirements": [
                {"id": "header:openssl/ssl.h", "status": "UNKNOWN"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("kind"), "Header")

    def test_lib_prefix_id_derives_kind_shared_library(self):
        raw = {
            "candidate_requirements": [
                {"id": "lib:libssl", "status": "UNKNOWN"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("kind"), "SharedLibrary")

    def test_pkgconfig_prefix_id_derives_kind_pkg_config(self):
        raw = {
            "candidate_requirements": [
                {"id": "pkgconfig:libpq", "status": "UNKNOWN"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("kind"), "PkgConfig")

    def test_name_and_kind_present_but_no_id_synthesises_id(self):
        raw = {
            "candidate_requirements": [
                {"name": "cmake", "kind": "Tool", "status": "REQUIRED"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("id"), "tool:cmake")

    def test_package_alias_normalised_to_name(self):
        raw = {
            "candidate_requirements": [
                {"id": "pkg:requests", "package": "requests", "status": "UNKNOWN"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("name"), "requests")

    def test_tool_name_alias_normalised_to_name(self):
        raw = {
            "candidate_requirements": [
                {"id": "tool:cmake", "tool_name": "cmake", "status": "REQUIRED"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("name"), "cmake")

    def test_cmd_alias_normalised_to_name(self):
        raw = {
            "candidate_requirements": [
                {"id": "tool:make", "cmd": "make", "status": "REQUIRED"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("name"), "make")

    def test_specifier_absent_detail_maps_to_specifier(self):
        raw = {
            "candidate_requirements": [
                {"id": "pkg:aiohttp", "name": "aiohttp", "status": "UNKNOWN",
                 "detail": ">=3.8"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("specifier"), ">=3.8")

    def test_existing_specifier_not_overwritten_by_detail(self):
        raw = {
            "candidate_requirements": [
                {"id": "pkg:aiohttp", "name": "aiohttp", "status": "UNKNOWN",
                 "specifier": ">=3.8", "detail": "something else"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req.get("specifier"), ">=3.8")

    def test_probe_request_name_derived_from_requirement_id(self):
        raw = {
            "probe_requests": [
                {"kind": "cli", "requirement_id": "tool:pg_config",
                 "predicate": "path exists"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        probe = proposal["probe_requests"][0]
        self.assertEqual(probe.get("name"), "pg_config",
                         "name should be derived from requirement_id when absent")

    def test_probe_request_pkg_name_alias_normalised(self):
        raw = {
            "probe_requests": [
                {"kind": "python_import", "pkg_name": "aiohttp",
                 "requirement_id": "pkg:aiohttp"},
            ]
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        probe = proposal["probe_requests"][0]
        self.assertEqual(probe.get("name"), "aiohttp")

    def test_malformed_entry_not_raised(self):
        """Completely unrecognisable entries should not raise; they can be left for ACL to drop."""
        raw = {
            "candidate_requirements": [
                None,
                42,
                "just a string",
                {},
            ]
        }
        # Must not raise
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        # We just want it to not raise; the ACL will drop invalid entries
        self.assertIn("candidate_requirements", proposal)

    def test_canonical_well_formed_entry_unchanged(self):
        """A well-formed canonical entry must not be mangled by normalisation."""
        raw = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
                 "status": "REQUIRED", "source": "LLM_GUESS",
                 "required_by": ["psycopg2==2.8.6"]},
            ],
            "probe_requests": [
                {"kind": "cli", "name": "pg_config", "predicate": "path exists",
                 "requirement_id": "tool:pg_config"},
            ],
        }
        proposal = parse_maintainer_proposal(self._wrap_json(raw))
        req = proposal["candidate_requirements"][0]
        self.assertEqual(req["id"], "tool:pg_config")
        self.assertEqual(req["name"], "pg_config")
        self.assertEqual(req["kind"], "Tool")
        self.assertEqual(req["status"], "REQUIRED")
        probe = proposal["probe_requests"][0]
        self.assertEqual(probe["name"], "pg_config")


# ---------------------------------------------------------------------------
# 2. End-to-end: apply_llm_proposal on REAL outputs yields n_requirements > 0
# ---------------------------------------------------------------------------

class ApplyLlmProposalRealOutputTests(unittest.TestCase):
    """apply_llm_proposal on normalised real-world outputs must yield a non-empty snapshot."""

    def _parse_and_apply(self, raw_json_obj):
        import json
        content = f"```json\n{json.dumps(raw_json_obj)}\n```"
        proposal = parse_maintainer_proposal(content)
        return apply_llm_proposal(_empty_snapshot(), proposal)

    def test_real_output_req_id_pkg_name_yields_nonempty_requirements(self):
        """The exact aliased output seen in live logs must survive the pipeline."""
        raw = {
            "candidate_requirements": [
                {
                    "req_id": "pkg:aiohttp",
                    "pkg_name": "aiohttp",
                    "status": "UNKNOWN",
                    "source": "likely in requirements.txt",
                }
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertGreater(len(updated.requirements), 0,
                           "n_requirements must be > 0 after normalisation (was always 0 before fix)")

    def test_real_output_path_prefixed_id_yields_nonempty_requirements(self):
        """The path: prefixed real output must also survive the pipeline."""
        raw = {
            "candidate_requirements": [
                {
                    "id": "path:repo_root",
                    "status": "UNKNOWN",
                    "detail": "Actual repository root path",
                }
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertGreater(len(updated.requirements), 0,
                           "path:repo_root must yield at least 1 requirement after normalisation")

    def test_multiple_real_outputs_all_accepted(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "UNKNOWN"},
                {"id": "tool:pg_config", "status": "REQUIRED"},
                {"name": "cmake", "kind": "Tool", "status": "REQUIRED"},
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        # All three should survive
        self.assertGreaterEqual(len(updated.requirements), 3,
                                f"Expected >=3 requirements, got {len(updated.requirements)}; rejected={rejected}")

    def test_accepted_requirements_have_llm_allowed_status(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "UNKNOWN"},
                {"id": "tool:pg_config", "status": "REQUIRED"},
            ]
        }
        updated, _ = self._parse_and_apply(raw)
        from src.envstate.types import LLM_ALLOWED_STATUSES
        for req in updated.requirements:
            self.assertIn(req.status, LLM_ALLOWED_STATUSES,
                          f"Requirement {req.id} has disallowed status {req.status!r}")

    def test_accepted_requirements_have_llm_allowed_source(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "UNKNOWN"},
            ]
        }
        updated, _ = self._parse_and_apply(raw)
        from src.envstate.types import LLM_ALLOWED_SOURCES
        for req in updated.requirements:
            self.assertIn(req.source, LLM_ALLOWED_SOURCES,
                          f"Requirement {req.id} has disallowed source {req.source!r}")


# ---------------------------------------------------------------------------
# 3. Trust boundary — must be preserved after the fix
# ---------------------------------------------------------------------------

class TrustBoundaryPreservedTests(unittest.TestCase):
    """Fix is about key names only. Status/source/evidence rules are unchanged."""

    def _parse_and_apply(self, raw_json_obj):
        import json
        content = f"```json\n{json.dumps(raw_json_obj)}\n```"
        proposal = parse_maintainer_proposal(content)
        return apply_llm_proposal(_empty_snapshot(), proposal)

    def test_status_present_still_rejected_after_normalisation(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "PRESENT"},
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertEqual(len(updated.requirements), 0,
                         "PRESENT status must still be rejected even after key normalisation")
        self.assertEqual(len(rejected), 1)

    def test_status_missing_still_rejected_after_normalisation(self):
        raw = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool", "status": "MISSING"},
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertEqual(len(updated.requirements), 0)
        self.assertEqual(len(rejected), 1)

    def test_evidence_still_rejected_after_normalisation(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "UNKNOWN",
                 "evidence": {"rc": 0, "probe_cmd": "python3 -c 'import aiohttp'"}},
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertEqual(len(updated.requirements), 0,
                         "Evidence must still be rejected even after key normalisation")
        self.assertEqual(len(rejected), 1)

    def test_probe_source_still_rejected_after_normalisation(self):
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp",
                 "status": "REQUIRED", "source": "PROBE"},
            ]
        }
        updated, rejected = self._parse_and_apply(raw)
        self.assertEqual(len(updated.requirements), 0,
                         "PROBE source must still be rejected even after key normalisation")
        self.assertEqual(len(rejected), 1)

    def test_llm_cannot_certify_present_via_normalised_req(self):
        """Even if normalisation succeeds, PRESENT can never reach the snapshot via LLM path."""
        raw = {
            "candidate_requirements": [
                {"req_id": "pkg:aiohttp", "pkg_name": "aiohttp", "status": "PRESENT"},
            ]
        }
        import json
        content = f"```json\n{json.dumps(raw)}\n```"
        proposal = parse_maintainer_proposal(content)
        snap, rejected = apply_llm_proposal(_empty_snapshot(), proposal)
        # If somehow one slipped through, it must not be PRESENT
        for req in snap.requirements:
            self.assertNotEqual(req.status, Status.PRESENT,
                                f"Requirement {req.id} must never be PRESENT via LLM path")


# ---------------------------------------------------------------------------
# 4. Observer probe loop: name derived from requirement_id
# ---------------------------------------------------------------------------

class ObserverProbeDeriveNameTests(unittest.TestCase):
    """ProbeSpec can be constructed from requirement_id alone (no explicit name)."""

    def _derive_probe_name(self, request: dict) -> str:
        """Replicate the derivation logic from agent.py observer probe loop (post-fix)."""
        name = request.get("name")
        if not name:
            req_id = request.get("requirement_id", "")
            # Strip known kind prefixes
            for prefix in ("pkg:", "tool:", "header:", "lib:", "pkgconfig:", "path:"):
                if req_id.startswith(prefix):
                    name = req_id[len(prefix):]
                    break
            if not name:
                name = request.get("pkg_name", "")
        return name

    def test_requirement_id_without_name_produces_runnable_probe_spec(self):
        request = {
            "kind": "cli",
            "requirement_id": "tool:pg_config",
            "predicate": "path exists",
            # NO "name" key — the bug scenario
        }
        name = self._derive_probe_name(request)
        self.assertEqual(name, "pg_config")
        # Must be able to construct a ProbeSpec without error
        spec = ProbeSpec(
            kind=request.get("kind", "cli"),
            name=name,
            predicate=request.get("predicate", ""),
        )
        self.assertEqual(spec.name, "pg_config")
        self.assertEqual(spec.kind, "cli")

    def test_python_import_probe_requirement_id_no_name(self):
        request = {
            "kind": "python_import",
            "requirement_id": "pkg:aiohttp",
        }
        name = self._derive_probe_name(request)
        self.assertEqual(name, "aiohttp")

    def test_probe_with_explicit_name_unchanged(self):
        request = {
            "kind": "cli",
            "name": "pg_config",
            "requirement_id": "tool:pg_config",
        }
        name = self._derive_probe_name(request)
        self.assertEqual(name, "pg_config")

    def test_probe_pkg_name_fallback(self):
        request = {
            "kind": "python_import",
            "pkg_name": "aiohttp",
            "requirement_id": "pkg:aiohttp",
        }
        name = self._derive_probe_name(request)
        self.assertEqual(name, "aiohttp")


# ---------------------------------------------------------------------------
# 5. Prompt schema test
# ---------------------------------------------------------------------------

class PromptSchemaTests(unittest.TestCase):
    """MAINTAINER_SYSTEM_PROMPT must specify EXACT field names for the canonical schema."""

    def test_prompt_specifies_canonical_candidate_requirement_keys(self):
        for key in ("id", "name", "kind", "status", "specifier"):
            self.assertIn(key, MAINTAINER_SYSTEM_PROMPT,
                          f"Prompt must mention canonical key '{key}' for candidate_requirements")

    def test_prompt_specifies_canonical_probe_request_keys(self):
        for key in ("kind", "name", "predicate", "requirement_id"):
            self.assertIn(key, MAINTAINER_SYSTEM_PROMPT,
                          f"Prompt must mention canonical key '{key}' for probe_requests")

    def test_prompt_specifies_allowed_kind_values(self):
        for kind in ("LanguagePackage", "Tool", "Header", "SharedLibrary", "PkgConfig"):
            self.assertIn(kind, MAINTAINER_SYSTEM_PROMPT,
                          f"Prompt must list kind value '{kind}'")

    def test_prompt_still_forbids_presence_and_evidence(self):
        """Existing trust-boundary constraints must remain in the prompt."""
        self.assertIn("PRESENT", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("MISSING", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("Evidence", MAINTAINER_SYSTEM_PROMPT)

    def test_prompt_includes_json_example(self):
        """Prompt must include a concrete JSON example object."""
        self.assertIn("```json", MAINTAINER_SYSTEM_PROMPT,
                      "Prompt must include a ```json fenced example block")


if __name__ == "__main__":
    unittest.main()
