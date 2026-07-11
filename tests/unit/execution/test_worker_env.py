"""Tests for embry0.execution.worker_env — worker-cap env var computation.

The sandbox runs with --cpus=N (cgroup cpu.max quota) but Node.js
os.cpus() returns the HOST cpu count, causing build tools to spawn
per-host-CPU workers that exceed the sandbox's pids/memory limits.

These tests verify that compute_worker_env() produces the correct
env-var caps for each supported tool.
"""

import pytest

from embry0.execution.worker_env import _floor_cpus, compute_worker_env

# ---------------------------------------------------------------------------
# _floor_cpus — parse Docker --cpus value to an integer ≥ 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("4", 4),
        ("6", 6),
        ("1", 1),
        (4, 4),
        (4.0, 4),
        # Fractional values are floored (Docker allows --cpus=2.5)
        ("2.5", 2),
        ("0.5", 0),  # floored to 0, clamped to 1 below
        (0.5, 0),  # same
        # Edge: sub-1 clamps to 1
        ("0", 1),
        (0, 1),
        ("-1", 1),
    ],
)
def test_floor_cpus(input_val, expected):
    # Sub-1 values always clamp to 1
    assert _floor_cpus(input_val) == max(1, expected)


def test_floor_cpus_invalid_string_returns_1():
    """Non-numeric strings default to 1 CPU (safe minimum)."""
    assert _floor_cpus("bogus") == 1
    assert _floor_cpus("") == 1


def test_floor_cpus_none_returns_1():
    """None (e.g. missing profile key) defaults to 1 CPU."""
    assert _floor_cpus(None) == 1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compute_worker_env — full env-var dict
# ---------------------------------------------------------------------------


def test_compute_worker_env_keys():
    """All expected env vars are present in the output."""
    env = compute_worker_env("4")
    assert set(env.keys()) == {
        "UV_THREADPOOL_SIZE",
        "NEXT_PRIVATE_WORKER_THREADS",
        "GOMAXPROCS",
        "MAKEFLAGS",
    }


def test_compute_worker_env_values_match_cpus():
    """All vars are sized from the cpus argument."""
    env = compute_worker_env("6")
    assert env["UV_THREADPOOL_SIZE"] == "6"
    assert env["NEXT_PRIVATE_WORKER_THREADS"] == "6"
    assert env["GOMAXPROCS"] == "6"
    assert env["MAKEFLAGS"] == "-j6"


def test_compute_worker_env_fractional_cpus():
    """Fractional --cpus values are floored."""
    env = compute_worker_env("2.5")
    assert env["UV_THREADPOOL_SIZE"] == "2"
    assert env["MAKEFLAGS"] == "-j2"


def test_compute_worker_env_single_cpu():
    """With 1 CPU, all vars are set to 1."""
    env = compute_worker_env("1")
    assert env["UV_THREADPOOL_SIZE"] == "1"
    assert env["GOMAXPROCS"] == "1"
    assert env["MAKEFLAGS"] == "-j1"


def test_compute_worker_env_returns_strings():
    """All values must be strings (Docker -e KEY=VAL requires strings)."""
    env = compute_worker_env(4)
    assert all(isinstance(v, str) for v in env.values())
