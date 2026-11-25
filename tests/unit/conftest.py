"""The unit suite's own fixtures; the shared ones are in tests/conftest.py."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fixtures.network_guard import allowed_addresses, refusing_network


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every unit test runs refusing connections to anything but loopback and the run's own services (#702):
    a test that reaches a real host fails at once, naming the address, instead of waiting on it."""
    if request.node.get_closest_marker("allow_network") is not None:
        yield
        return
    with refusing_network(allowed_addresses()) as guard:
        yield
    if guard.refused:
        pytest.fail(
            f"{request.node.nodeid} tried to connect to {', '.join(sorted(set(guard.refused)))}: the unit suite "
            "reaches no network but loopback and its test database (tests/fixtures/network_guard.py, #702)",
            pytrace=False,
        )
