"""Unit tests for the repo2run benchmark parallel scheduler (`run_instances`).

The scheduler is the only concurrency-bearing surface added for `--concurrency`;
the per-instance work itself is unchanged. These tests pin down the three
guarantees that matter: submit-order output, real parallelism up to the cap, and
that the cap is respected (concurrency=1 stays sequential).
"""

import threading
import time

from run_repo2run_benchmark import run_instances


def _instances(n: int) -> list[dict]:
    return [{"instance_id": f"i{k}", "full_name": f"org/i{k}"} for k in range(n)]


def test_run_instances_preserves_submit_order():
    def worker(position: int, instance: dict) -> dict:
        return {"position": position, "instance_id": instance["instance_id"]}

    out = run_instances(_instances(8), concurrency=4, worker=worker)

    assert [p["instance_id"] for p in out] == [f"i{k}" for k in range(8)]
    assert [p["position"] for p in out] == list(range(1, 9))


def test_run_instances_runs_in_parallel_up_to_concurrency():
    n = 4
    barrier = threading.Barrier(n, timeout=5)

    def worker(position: int, instance: dict) -> dict:
        # Returns only if all `n` workers reach the barrier simultaneously; a
        # timeout (BrokenBarrierError) would mean parallelism did not happen.
        barrier.wait()
        return {"position": position}

    out = run_instances(_instances(n), concurrency=n, worker=worker)
    assert len(out) == n


def test_run_instances_sequential_when_concurrency_1():
    lock = threading.Lock()
    state = {"inflight": 0, "max": 0}

    def worker(position: int, instance: dict) -> dict:
        with lock:
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
        time.sleep(0.01)
        with lock:
            state["inflight"] -= 1
        return {"position": position}

    out = run_instances(_instances(5), concurrency=1, worker=worker)

    assert state["max"] == 1
    assert [p["position"] for p in out] == [1, 2, 3, 4, 5]


def test_run_instances_caps_in_flight_at_concurrency():
    lock = threading.Lock()
    state = {"inflight": 0, "max": 0}

    def worker(position: int, instance: dict) -> dict:
        with lock:
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
        time.sleep(0.02)
        with lock:
            state["inflight"] -= 1
        return {"position": position}

    run_instances(_instances(12), concurrency=3, worker=worker)

    assert state["max"] <= 3


def test_run_instances_empty_input():
    assert run_instances([], concurrency=4, worker=lambda p, i: {}) == []
