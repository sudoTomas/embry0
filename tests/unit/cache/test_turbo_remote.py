from athanor.cache.turbo_remote import (
    TurboRemoteConfig,
    parse_turbo_stdout_for_hits,
    turbo_env_vars,
)


def test_turbo_env_vars_includes_all_three_when_config_complete():
    cfg = TurboRemoteConfig(
        api_url="https://turbo.example.com",
        team="team-1",
        token="secret-token",
    )
    env = turbo_env_vars(cfg)
    assert env["TURBO_API"] == "https://turbo.example.com"
    assert env["TURBO_TEAM"] == "team-1"
    assert env["TURBO_TOKEN"] == "secret-token"


def test_turbo_env_vars_returns_empty_dict_when_config_is_none():
    assert turbo_env_vars(None) == {}


def test_parse_stdout_extracts_hits_and_misses():
    """Turbo's --output-logs=hash-only emits hash + cache status per task.
    Format (rough):  apps/hub#build: cache hit, replaying logs <hash>
                     apps/companion#build: cache miss, executing <hash>
    """
    stdout = """
    apps/hub#build: cache hit, replaying logs abc123def
    apps/companion#build: cache miss, executing 4a5b6c7d
    apps/lane#build: cache hit, replaying logs deadbeef
    @raven/auth#build: cache hit (full), replaying logs fffeeedd
    """
    hits, misses = parse_turbo_stdout_for_hits(stdout)
    assert "apps/hub#build" in hits
    assert "apps/lane#build" in hits
    assert "@raven/auth#build" in hits
    assert "apps/companion#build" in misses


def test_parse_stdout_handles_empty_output():
    hits, misses = parse_turbo_stdout_for_hits("")
    assert hits == []
    assert misses == []
