import unittest

from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    Requirement,
    Source,
    Status,
)
from src.envstate.acl import (
    advance_revision,
    apply_llm_proposal,
    certify_from_probe,
)


def _empty_snapshot(revision=0):
    return EnvStateSnapshot(
        revision=revision,
        container_id="c1",
        base=BaseFacts(image="python:3.11-slim"),
    )


class AclCertifyTests(unittest.TestCase):
    def test_probe_can_set_present_with_current_evidence(self):
        snap = _empty_snapshot(revision=3).__class__(
            revision=3,
            container_id="c1",
            base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.REQUIRED, source=Source.LLM_GUESS),
            ),
        )
        evidence = Evidence(probe_cmd="command -v pg_config", rc=0,
                            stdout_predicate="path exists", env_revision=3, container_id="c1")
        updated = certify_from_probe(snap, "tool:pg_config", Status.PRESENT, evidence)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)
        self.assertEqual(req.evidence, evidence)
        # original snapshot unchanged (immutability)
        self.assertEqual(snap.requirements[0].status, Status.REQUIRED)

    def test_probe_rejects_stale_evidence_revision(self):
        snap = _empty_snapshot(revision=5)
        stale = Evidence(probe_cmd="x", rc=0, stdout_predicate="p", env_revision=4, container_id="c1")
        with self.assertRaises(ValueError):
            certify_from_probe(snap, "tool:x", Status.PRESENT, stale)


class AclLlmProposalTests(unittest.TestCase):
    def test_accepts_required_and_unknown_hypotheses(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
                 "status": "REQUIRED", "source": "LLM_GUESS",
                 "required_by": ["psycopg2==2.8.6"]},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(rejected, [])
        self.assertEqual(updated.requirements[0].name, "pg_config")
        self.assertEqual(updated.requirements[0].status, Status.REQUIRED)

    def test_rejects_llm_attempt_to_assert_present(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
                 "status": "PRESENT", "source": "LLM_GUESS"},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 1)
        self.assertIn("status", rejected[0]["reason"].lower())

    def test_rejects_llm_attempt_to_attach_evidence_or_probe_source(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:x", "name": "x", "kind": "Tool", "status": "REQUIRED",
                 "source": "PROBE"},
                {"id": "tool:y", "name": "y", "kind": "Tool", "status": "UNKNOWN",
                 "source": "LLM_GUESS", "evidence": {"rc": 0}},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 2)


class AclRevisionTests(unittest.TestCase):
    def test_advance_revision_demotes_stale_presence_facts(self):
        evidence = Evidence(probe_cmd="x", rc=0, stdout_predicate="p", env_revision=2, container_id="c1")
        snap = EnvStateSnapshot(
            revision=2, container_id="c1", base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.PRESENT, source=Source.PROBE, evidence=evidence),
            ),
        )
        updated = advance_revision(snap, "system_package_install")
        self.assertEqual(updated.revision, 3)
        req = updated.requirements[0]
        self.assertEqual(req.status, Status.UNKNOWN)   # demoted
        self.assertIsNone(req.evidence)                # evidence cleared from live fact
        self.assertEqual(len(updated.stale_evidence), 1)  # preserved as stale
        self.assertEqual(updated.stale_evidence[0].evidence, evidence)


class AclRobustnessTests(unittest.TestCase):
    def test_candidate_missing_name_derives_from_id(self):
        # After the name-key fix, a candidate with id but no name has its name
        # derived from the id prefix, so it is ACCEPTED (not rejected).
        snap = _empty_snapshot()
        proposal = {"candidate_requirements": [
            {"id": "tool:x", "kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS"},
        ]}
        updated, rejected = apply_llm_proposal(snap, proposal)  # must NOT raise
        self.assertEqual(len(updated.requirements), 1)
        self.assertEqual(updated.requirements[0].name, "x")
        self.assertEqual(rejected, [])

    def test_candidate_missing_both_name_and_id_is_rejected(self):
        # Without either name or id there is nothing to derive from — must be rejected.
        snap = _empty_snapshot()
        proposal = {"candidate_requirements": [
            {"kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS"},
        ]}
        updated, rejected = apply_llm_proposal(snap, proposal)  # must NOT raise
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 1)

    def test_non_dict_candidate_is_rejected_not_raised(self):
        snap = _empty_snapshot()
        proposal = {"candidate_requirements": ["just a string", 42]}
        updated, rejected = apply_llm_proposal(snap, proposal)  # must NOT raise
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 2)

    def test_string_required_by_is_rejected_not_char_exploded(self):
        snap = _empty_snapshot()
        proposal = {"candidate_requirements": [
            {"id": "lang:psycopg2", "name": "psycopg2", "kind": "LanguagePackage",
             "status": "REQUIRED", "source": "LLM_GUESS", "required_by": "psycopg2"},
        ]}
        updated, rejected = apply_llm_proposal(snap, proposal)
        # The string must NOT be exploded into ('p','s','y',...); the candidate is rejected.
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 1)

    def test_llm_cannot_overwrite_diagnose_certified_fact(self):
        # A host-set DIAGNOSE MISSING fact must be protected, same as a PROBE fact.
        snap = EnvStateSnapshot(
            revision=0, container_id="c1", base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.MISSING, source=Source.DIAGNOSE),
            ),
        )
        proposal = {"candidate_requirements": [
            {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
             "status": "REQUIRED", "source": "LLM_GUESS"},
        ]}
        updated, rejected = apply_llm_proposal(snap, proposal)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.MISSING)      # unchanged
        self.assertEqual(req.source, Source.DIAGNOSE)     # not overwritten
        self.assertTrue(any("overwrite" in r["reason"].lower() for r in rejected))

    def test_llm_cannot_overwrite_probe_certified_fact(self):
        evidence = Evidence(probe_cmd="x", rc=0, stdout_predicate="p", env_revision=0, container_id="c1")
        snap = EnvStateSnapshot(
            revision=0, container_id="c1", base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.PRESENT, source=Source.PROBE, evidence=evidence),
            ),
        )
        proposal = {"candidate_requirements": [
            {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
             "status": "REQUIRED", "source": "LLM_GUESS"},
        ]}
        updated, rejected = apply_llm_proposal(snap, proposal)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)      # unchanged
        self.assertEqual(req.source, Source.PROBE)
