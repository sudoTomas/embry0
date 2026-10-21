import pytest

from embry0.safety import redaction

# These tests never read the real environment (environ={}) and never assert on
# a value list directly: a failure must not print local secrets.


@pytest.fixture(autouse=True)
def _empty_registry():
    redaction.register_known_secrets([], reset=True)
    yield
    redaction.register_known_secrets([], reset=True)


def test_registered_secret_is_scrubbed_even_when_not_in_env():
    redaction.register_known_secrets(["ghp_registryonly123"])
    values = redaction.secret_env_values(environ={})
    found = "ghp_registryonly123" in values
    assert found is True
    out = redaction.redact_secrets("push failed for ghp_registryonly123 at origin", values)
    assert "ghp_registryonly123" not in out


def test_short_values_are_not_registered():
    redaction.register_known_secrets(["abc"], reset=True)
    registered = "abc" in redaction.known_secret_values()
    assert registered is False


def test_unregister_removes_a_registered_value():
    redaction.register_known_secrets(["bearer-one-aaaaaaaa", "bearer-two-bbbbbbbb"])
    redaction.unregister_known_secrets(["bearer-one-aaaaaaaa"])
    values = redaction.known_secret_values()
    assert "bearer-one-aaaaaaaa" not in values, "an unregistered value is no longer a known secret"
    assert "bearer-two-bbbbbbbb" in values, "unregistering one value leaves the others registered"
    assert "bearer-one-aaaaaaaa" not in redaction.secret_env_values(environ={}), "the scrub list forgets it too"


def test_unregister_tolerates_values_never_registered():
    redaction.unregister_known_secrets(["never-registered-value", "x"])
    assert redaction.known_secret_values() == []


def _reference(environ: dict[str, str]) -> list[str]:
    env = {v for k, v in environ.items() if redaction._SECRET_ENV_NAME.search(k) and len(v) >= 8}
    return sorted(env | set(redaction.known_secret_values()), key=lambda v: (-len(v), v))


def test_secret_env_values_is_the_merged_list_longest_first_across_cache_changes():
    environ = {
        "GITHUB_TOKEN": "env-token-cccccccccc",
        "API_KEY": "shared-value-dddddd",  # also registered: appears once
        "HOME": "/home/not-a-secret",
    }
    redaction.register_known_secrets(["registered-a-eeeeeeeeeeee", "shared-value-dddddd", "reg-b-ffffffff"])
    first = redaction.secret_env_values(environ=environ)
    assert first == _reference(environ), "merged list matches the reference sort (first call fills the cache)"
    assert redaction.secret_env_values(environ=environ) == first, "a cached call returns the same list"

    redaction.register_known_secrets(["a-much-longer-registered-value-gggggggggg"])
    assert redaction.secret_env_values(environ=environ) == _reference(environ), "register invalidates the cache"
    redaction.unregister_known_secrets(["registered-a-eeeeeeeeeeee"])
    assert redaction.secret_env_values(environ=environ) == _reference(environ), "unregister invalidates the cache"
    redaction.register_known_secrets(["only-one-left-hhhhhhhh"], reset=True)
    assert redaction.secret_env_values(environ=environ) == _reference(environ), "reset invalidates the cache"


def test_secret_env_values_result_is_a_copy():
    redaction.register_known_secrets(["registered-iiiiiiiiii"])
    redaction.secret_env_values(environ={}).append("mutated-jjjjjjjjjj")
    assert "mutated-jjjjjjjjjj" not in redaction.secret_env_values(environ={}), "callers cannot mutate the cache"


def test_extra_values_join_the_scrub_list_with_the_same_floor():
    """A caller holding secrets this environment never had (a job's env vars,
    which only the sandbox carried) passes them as ``extra``."""
    environ = {"GITHUB_TOKEN": "env-token-cccccccccc"}
    values = redaction.secret_env_values(environ=environ, extra=["job-secret-value-gggggggg", "short"])
    assert "job-secret-value-gggggggg" in values, "an extra value is not scrubbed"
    assert "short" not in values, "a short extra value would redact ordinary words"
    assert values == _reference(environ | {"EXTRA_SECRET": "job-secret-value-gggggggg"}), (
        "extras keep the longest-first order"
    )
