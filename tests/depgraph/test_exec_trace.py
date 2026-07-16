from python_deps.depgraph.exec_trace import (
    ParsedFailure, Observation, ObservationOverlay, stable_failure_id,
)


def test_stable_id_is_deterministic_and_volatile_free():
    a = stable_failure_id("module_not_found", "import:psycopg2", "collection")
    b = stable_failure_id("module_not_found", "import:psycopg2", "collection")
    assert a == b and len(a) == 12


def test_overlay_merges_by_stable_id_bumping_sightings():
    o1 = Observation(stable_id="x", anchor="pkg:psycopg2", chain=(), blast_radius=frozenset(),
                     phase="collection", raw_span="...", sightings=1, seen_this_cycle=True)
    overlay = ObservationOverlay().with_observation(o1).with_observation(
        Observation(stable_id="x", anchor="pkg:psycopg2", chain=(), blast_radius=frozenset(),
                    phase="collection", raw_span="...", sightings=1, seen_this_cycle=True)
    )
    assert len(overlay.observations) == 1
    assert overlay.observations[0].sightings == 2
