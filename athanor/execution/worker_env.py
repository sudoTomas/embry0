"""Compute worker-cap environment variables from container CPU limits.

Node.js ``os.cpus()`` returns the **host** CPU count when the container
uses ``--cpus`` (cgroup v2 ``cpu.max`` quota) without ``--cpuset-cpus``.
Build tools (Next.js, Webpack, Jest, Turbopack, …) size their worker
pools from ``os.cpus().length``, spawning far more workers than the
container can sustain under its pids/memory limits — manifesting as
EAGAIN on ``fork()`` / ``pthread_create()``.

Observed on job-2ca9a8715f9a and job-86f3561c47b9 (client-project/
command-center, 2026-07-08): the host exposes 47 CPUs, the sandbox has
``--cpus=4 --pids-limit=256``, and Next.js tries to spawn 47
page-data-collection workers → EAGAIN → build exits 1.

This module computes env vars that cap build-tool parallelism to match
the container's actual CPU allocation.
"""

from __future__ import annotations

import math


def compute_worker_env(cpus: str | int | float) -> dict[str, str]:
    """Return env vars that cap build-tool parallelism to *cpus*.

    Callers should merge these into the sandbox env via ``setdefault()``
    so user-provided overrides (set in the Athanor env-vars UI) take
    precedence.

    Variables injected
    ------------------
    ``UV_THREADPOOL_SIZE``
        libuv threadpool cap — controls the async-I/O worker pool that
        backs ``fs``, ``dns``, ``zlib`` in Node.js.  Default (when
        unset) is 4; here we set it explicitly to match the container
        CPU count so the sandbox doesn't accidentally inherit a
        host-side override that's higher than its resource limits allow.

    ``NEXT_PRIVATE_WORKER_THREADS``
        Next.js internal: caps the page-data-collection worker pool.
        When absent, Next.js falls back to ``os.cpus().length``, which
        returns the *host* count and triggers the EAGAIN cascade.

    ``GOMAXPROCS``
        Go runtime: caps OS-thread count for goroutine scheduling.
        Defense-in-depth for any Go tooling that runs inside the sandbox
        (e.g. esbuild, Turbo).

    ``MAKEFLAGS``
        GNU Make: ``-j<N>`` caps parallel recipe execution.
        Defense-in-depth for native-addon builds (node-gyp → make).
    """
    cpu_count = _floor_cpus(cpus)
    return {
        "UV_THREADPOOL_SIZE": str(cpu_count),
        "NEXT_PRIVATE_WORKER_THREADS": str(cpu_count),
        "GOMAXPROCS": str(cpu_count),
        "MAKEFLAGS": f"-j{cpu_count}",
    }


def _floor_cpus(cpus: str | int | float) -> int:
    """Parse *cpus* (Docker ``--cpus`` value) to an integer ≥ 1.

    Docker allows fractional values (``--cpus=2.5``); we floor them
    because worker counts must be whole numbers, and rounding up would
    overshoot the allocation.
    """
    try:
        n = math.floor(float(cpus))
    except (TypeError, ValueError):
        n = 1
    return max(1, n)
