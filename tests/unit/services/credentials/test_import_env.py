import json
from types import SimpleNamespace

from embry0.execution.xai_credential import DurableState, XaiCredentialStore
from embry0.services.credentials.import_env import import_from_env
from embry0.storage.repositories.credentials import BindingValidationError


class FakeSecrets:
    async def encrypt(self, p):
        return "enc:" + p

    async def decrypt(self, c):
        return c.removeprefix("enc:")


class FakeRepo:
    def __init__(self, known=None):
        self.rows = {}  # (provider,label) -> row
        self.bindings = {}  # (scope_kind, scope_value) -> list
        self.known = known or {}  # forge -> the owners registered there

    async def known_owners(self):
        return {forge: set(owners) for forge, owners in self.known.items()}

    async def find_by_label(self, provider, label):
        return self.rows.get((provider, label))

    async def create(self, **kw):
        cid = f"cred-{kw['label']}"
        if any(r["id"] == cid for r in self.rows.values()):
            cid = f"cred-{kw['provider']}-{kw['label']}"  # ids stay unique across providers
        row = dict(kw, id=cid, status="ok")
        self.rows[(kw["provider"], kw["label"])] = row
        return row

    async def rotate(self, cid, *, secret_ciphertext, masked_preview):
        for row in self.rows.values():
            if row["id"] == cid:
                row["secret_ciphertext"] = secret_ciphertext
                return row

    async def bindings_for_scope(self, k, v):
        return list(self.bindings.get((k, v), []))

    async def replace_bindings(self, k, v, items, *, created_by, repo_forge=None):
        rows = [
            dict(
                {"pinned": False, "priority": 0},
                **i,
                scope_kind=k,
                scope_value=v,
                provider=self.rows_by_id(i["credential_id"])["provider"],
            )
            for i in items
        ]
        # Same uniqueness rule as CredentialsRepository.replace_bindings.
        seen = set()
        for b in rows:
            if k == "global" and b["role"] == "llm":
                continue
            key = (b["role"], b["provider"])
            if key in seen:
                raise BindingValidationError(f"only one {key[0]!r} credential per provider {key[1]!r} at scope {k!r}")
            seen.add(key)
        self.bindings[(k, v)] = rows
        return rows

    def rows_by_id(self, cid):
        return next(r for r in self.rows.values() if r["id"] == cid)


def _cfg(github_token="", gitlab_token=""):
    return SimpleNamespace(
        github_token=github_token, gitlab_token=gitlab_token, gitlab_url="https://gitlab.example.com"
    )


async def test_imports_default_and_owner_tokens_with_bindings() -> None:
    repo = FakeRepo(known={"gitlab": {"group-x"}})
    env = {"GITHUB_TOKEN__ACME": "tok-acme", "GITLAB_TOKEN__GROUP_X": "tok-gl"}
    report = await import_from_env(repo, FakeSecrets(), _cfg(github_token="tok-default"), environ=env)
    assert sorted(report.created) == ["github/acme", "github/default", "gitlab/group_x"]
    assert sorted(report.bound) == [
        "global:forge -> github/default",
        "owner:github:acme:forge -> github/acme",
        "owner:gitlab:group-x:forge -> gitlab/group_x",
    ], f"GITLAB_TOKEN__GROUP_X is bound to group-x, the known group whose key it is: {report.bound}"
    assert repo.rows[("gitlab", "group_x")]["host"] == "gitlab.example.com"
    assert [b["credential_id"] for b in repo.bindings[("global", "")]] == ["cred-default"]
    assert repo.bindings[("owner", "github:acme")][0]["credential_id"] == "cred-acme"
    assert repo.bindings[("owner", "gitlab:group-x")][0]["credential_id"] == "cred-group_x"
    assert repo.rows[("github", "acme")]["secret_ciphertext"] == "enc:tok-acme"


def _owner_bindings(repo: FakeRepo) -> dict[str, str]:
    return {v: rows[0]["credential_id"] for (k, v), rows in repo.bindings.items() if k == "owner" and rows}


async def test_an_owner_key_binds_the_one_known_owner_whose_key_it_is() -> None:
    """An env variable cannot spell '-' or '.': GITLAB_TOKEN__OTHER_GRP is the key of other-grp, other.grp and
    other_grp. The binding names the one of them the deployment knows."""
    repo = FakeRepo(known={"gitlab": {"other-grp", "acme"}, "github": {"other_grp"}})
    report = await import_from_env(repo, FakeSecrets(), _cfg(), environ={"GITLAB_TOKEN__OTHER_GRP": "tok-gl"})
    assert report.bound == ["owner:gitlab:other-grp:forge -> gitlab/other_grp"], (
        f"GITLAB_TOKEN__OTHER_GRP is bound to other-grp, the one known GitLab owner whose key it is: {report.as_dict()}"
    )
    assert _owner_bindings(repo) == {"gitlab:other-grp": "cred-other_grp"}, (
        f"the token is bound to other-grp, the one known GitLab owner whose key it is: {_owner_bindings(repo)}"
    )


async def test_a_github_owner_key_reads_a_dash_and_not_a_dot() -> None:
    """GitHub's key spells '-' as '_' and keeps '.': GITHUB_TOKEN__MY_ORG is my-org's or my_org's, never my.org's."""
    repo = FakeRepo(known={"github": {"my-org", "my.org"}})
    report = await import_from_env(repo, FakeSecrets(), _cfg(), environ={"GITHUB_TOKEN__MY_ORG": "tok-gh"})
    assert report.skipped == [] and _owner_bindings(repo) == {"github:my-org": "cred-my_org"}, (
        f"GITHUB_TOKEN__MY_ORG is bound to my-org, the known owner whose key it is: {report.as_dict()}"
    )


async def test_an_owner_key_several_known_owners_read_is_bound_to_none() -> None:
    repo = FakeRepo(known={"gitlab": {"other-grp", "other_grp", "other.grp"}})
    report = await import_from_env(repo, FakeSecrets(), _cfg(), environ={"GITLAB_TOKEN__OTHER_GRP": "tok-gl"})
    assert report.created == ["gitlab/other_grp"], "the token is still imported"
    assert report.bound == [] and _owner_bindings(repo) == {}, (
        f"a key three known owners read is bound to none of them: {report.as_dict()}"
    )
    assert report.skipped == [
        "gitlab/other_grp: bound to no owner: GITLAB_TOKEN__OTHER_GRP is read by other-grp, other.grp, other_grp; "
        "bind it to the owner it is for"
    ], report.skipped


async def test_an_owner_key_no_known_owner_reads_is_bound_to_none() -> None:
    repo = FakeRepo(known={"gitlab": {"acme"}})
    report = await import_from_env(repo, FakeSecrets(), _cfg(), environ={"GITLAB_TOKEN__OTHER_GRP": "tok-gl"})
    assert report.bound == [] and _owner_bindings(repo) == {}, (
        f"a key that could name several owners, none known, is bound to none: {report.as_dict()}"
    )
    assert [s.split(": ", 2)[1] for s in report.skipped] == ["bound to no owner"], report.skipped


async def test_same_value_under_two_keys_is_one_credential_two_bindings() -> None:
    repo = FakeRepo()
    report = await import_from_env(
        repo, FakeSecrets(), _cfg(github_token="same"), environ={"GITHUB_TOKEN__ACME": "same"}
    )
    assert report.created == ["github/default"]
    assert repo.bindings[("owner", "github:acme")][0]["credential_id"] == "cred-default"


async def test_idempotent_and_replace() -> None:
    repo = FakeRepo()
    await import_from_env(repo, FakeSecrets(), _cfg(github_token="v1"), environ={})
    r2 = await import_from_env(repo, FakeSecrets(), _cfg(github_token="v1"), environ={})
    assert r2.created == [] and r2.skipped == [] and r2.bound == []
    r3 = await import_from_env(repo, FakeSecrets(), _cfg(github_token="v2"), environ={})
    assert r3.skipped == ["github/default: exists with a different secret (pass replace=true)"]
    assert repo.rows[("github", "default")]["secret_ciphertext"] == "enc:v1"
    r4 = await import_from_env(repo, FakeSecrets(), _cfg(github_token="v2"), environ={}, replace=True)
    assert r4.skipped == [] and repo.rows[("github", "default")]["secret_ciphertext"] == "enc:v2"


async def test_nothing_configured_is_an_empty_report() -> None:
    report = await import_from_env(FakeRepo(), FakeSecrets(), _cfg(), environ={})
    assert report.as_dict() == {"created": [], "bound": [], "skipped": []}


async def test_both_forge_defaults_bind_globally_per_provider() -> None:
    repo = FakeRepo()
    report = await import_from_env(repo, FakeSecrets(), _cfg(github_token="gh", gitlab_token="gl"), environ={})
    assert report.skipped == []
    assert sorted(report.bound) == ["global:forge -> github/default", "global:forge -> gitlab/default"]
    assert sorted((b["provider"], b["credential_id"]) for b in repo.bindings[("global", "")]) == [
        ("github", "cred-default"),
        ("gitlab", "cred-gitlab-default"),
    ]


async def test_empty_owner_env_key_is_skipped() -> None:
    repo = FakeRepo()
    report = await import_from_env(repo, FakeSecrets(), _cfg(), environ={"GITHUB_TOKEN__": "tok"})
    assert report.as_dict() == {
        "created": [],
        "bound": [],
        "skipped": ["github/: empty owner in env key GITHUB_TOKEN__"],
    }
    assert repo.rows == {} and repo.bindings == {}


async def test_replace_with_existing_binding_does_not_rebind() -> None:
    repo = FakeRepo()
    await import_from_env(repo, FakeSecrets(), _cfg(github_token="v1"), environ={})
    r2 = await import_from_env(repo, FakeSecrets(), _cfg(github_token="v2"), environ={}, replace=True)
    assert r2.bound == [] and r2.skipped == [] and r2.created == []
    assert len(repo.bindings[("global", "")]) == 1


async def test_conflicting_same_provider_forge_binding_is_left_intact() -> None:
    repo = FakeRepo()
    other = await repo.create(provider="github", label="manual", kind="forge_token", secret_ciphertext="enc:x")
    await repo.replace_bindings("global", "", [{"credential_id": other["id"], "role": "forge"}], created_by="t")
    report = await import_from_env(repo, FakeSecrets(), _cfg(github_token="gh"), environ={})
    assert report.skipped == ["global:forge already bound to cred-manual"]
    assert report.bound == []
    assert [b["credential_id"] for b in repo.bindings[("global", "")]] == ["cred-manual"]


async def test_existing_non_forge_binding_is_preserved() -> None:
    repo = FakeRepo()
    llm = await repo.create(provider="anthropic", label="claude", kind="llm_api_key", secret_ciphertext="enc:k")
    items = [{"credential_id": llm["id"], "role": "llm", "pinned": False, "priority": 7}]
    await repo.replace_bindings("global", "", items, created_by="t")
    report = await import_from_env(repo, FakeSecrets(), _cfg(github_token="gh"), environ={})
    assert report.bound == ["global:forge -> github/default"]
    got = {(b["role"], b["credential_id"]): (b["pinned"], b["priority"]) for b in repo.bindings[("global", "")]}
    assert got == {("llm", "cred-claude"): (False, 7), ("forge", "cred-default"): (False, 0)}


class UndecryptableSecrets(FakeSecrets):
    """A row written under a key this process no longer has (disabled/decryption_failed)."""

    async def decrypt(self, c):
        from cryptography.fernet import InvalidToken

        if c == "undecryptable":
            raise InvalidToken
        return await super().decrypt(c)


def _seed_undecryptable(repo):
    repo.rows[("github", "default")] = {
        "id": "cred-default",
        "provider": "github",
        "label": "default",
        "secret_ciphertext": "undecryptable",
        "status": "disabled",
        "status_reason": "decryption_failed",
    }


async def test_undecryptable_existing_row_is_skipped_not_a_500() -> None:
    repo = FakeRepo()
    _seed_undecryptable(repo)
    report = await import_from_env(repo, UndecryptableSecrets(), _cfg(github_token="v1"), environ={})
    assert report.skipped == ["github/default: stored secret cannot be decrypted (pass replace=true to rotate)"]
    assert report.bound == [] and repo.rows[("github", "default")]["secret_ciphertext"] == "undecryptable"


def _llm_paths(tmp_path, **over):
    paths = {
        "claude_credentials_file": str(tmp_path / "no-claude.json"),
        "xai_store_path": str(tmp_path / "no-xai.enc"),
        "grok_auth_file": str(tmp_path / "no-grok.json"),
    }
    paths.update(over)
    return paths


def _global_llm(repo):
    return [b for b in repo.bindings.get(("global", ""), []) if b["role"] == "llm"]


async def test_imports_three_llm_rows_with_priorities(tmp_path) -> None:
    secrets = FakeSecrets()
    store_path = tmp_path / "xai.enc"
    state = DurableState(
        refresh_token="refresh-file",
        client_id="client-file",
        token_endpoint="https://auth.x.ai/oauth2/token",
    )
    await XaiCredentialStore(store_path, secrets).save(state)
    repo = FakeRepo()
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "oat-env-token", "XAI_API_KEY": "xai-key-value"}
    report = await import_from_env(
        repo, secrets, _cfg(), environ=env, **_llm_paths(tmp_path, xai_store_path=str(store_path))
    )
    assert sorted(report.created) == ["anthropic/default", "xai/api-key", "xai/default"]
    assert repo.rows[("anthropic", "default")]["kind"] == "llm_oauth"
    assert repo.rows[("xai", "default")]["kind"] == "llm_oauth"
    assert repo.rows[("xai", "api-key")]["kind"] == "llm_api_key"
    plain = await secrets.decrypt(repo.rows[("xai", "default")]["secret_ciphertext"])
    assert json.loads(plain) == {
        "refresh_token": "refresh-file",
        "client_id": "client-file",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
    }
    by_account = {
        (repo.rows_by_id(b["credential_id"])["provider"], repo.rows_by_id(b["credential_id"])["label"]): b["priority"]
        for b in _global_llm(repo)
    }
    assert by_account == {("anthropic", "default"): 0, ("xai", "default"): 0, ("xai", "api-key"): 1}
    blob = str(report.as_dict())
    assert "oat-env-token" not in blob and "xai-key-value" not in blob and "refresh-file" not in blob


async def test_env_token_wins_over_credentials_file(tmp_path) -> None:
    cred = tmp_path / "credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "file-token-value"}}), encoding="utf-8")
    repo = FakeRepo()
    report = await import_from_env(
        repo,
        FakeSecrets(),
        _cfg(),
        environ={"CLAUDE_CODE_OAUTH_TOKEN": "env-token-value"},
        **_llm_paths(tmp_path, claude_credentials_file=str(cred)),
    )
    assert report.created == ["anthropic/default"]
    assert any("file" in line for line in report.skipped)
    assert repo.rows[("anthropic", "default")]["secret_ciphertext"] == "enc:env-token-value"
    blob = str(report.as_dict())
    assert "file-token-value" not in blob and "env-token-value" not in blob


async def test_llm_import_is_idempotent(tmp_path) -> None:
    repo = FakeRepo()
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "oat-env-token", "XAI_API_KEY": "xai-key-value"}
    kwargs = _llm_paths(tmp_path)
    first = await import_from_env(repo, FakeSecrets(), _cfg(), environ=env, **kwargs)
    assert sorted(first.created) == ["anthropic/default", "xai/api-key"]
    second = await import_from_env(repo, FakeSecrets(), _cfg(), environ=env, **kwargs)
    assert second.created == [] and second.bound == [] and second.skipped == []


async def test_undecryptable_existing_row_is_rotated_with_replace() -> None:
    repo = FakeRepo()
    _seed_undecryptable(repo)
    report = await import_from_env(repo, UndecryptableSecrets(), _cfg(github_token="v1"), environ={}, replace=True)
    assert report.skipped == [] and report.bound == ["global:forge -> github/default"]
    assert repo.rows[("github", "default")]["secret_ciphertext"] == "enc:v1"
