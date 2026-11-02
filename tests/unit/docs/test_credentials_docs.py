import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_credentials_doc_exists_and_names_the_surfaces():
    text = (ROOT / "docs" / "credentials.md").read_text()
    for needle in (
        "/api/v1/credentials",
        "import-env",
        "credential_env_fallback",
        "GITHUB_TOKEN__",
        "repo > owner > host > global > env",
    ):
        assert needle in text, needle
    # Names that must never ship are checked by scripts/check_denylist.py,
    # whose terms live outside the repository (docs/ops/verification.md).


def test_adr_0006_indexed():
    assert (ROOT / "docs" / "adr" / "0006-credential-registry.md").exists()
    assert "0006-credential-registry.md" in (ROOT / "docs" / "adr" / "README.md").read_text()


def test_adr_0006_cites_no_commit_hash():
    """A squash-merge orphans branch commits; the ADR names the landing by migration number instead."""
    text = (ROOT / "docs" / "adr" / "0006-credential-registry.md").read_text()
    hashes = re.findall(r"\b[0-9a-f]{8}\b", text)
    assert not hashes, f"ADR 0006 cites commit hash(es) {hashes}"
    assert "migration 86" in text


def test_glossary_has_the_terms():
    text = (ROOT / "docs" / "glossary.md").read_text()
    for term in ("### Credential", "### Binding", "### Pool"):
        assert term in text, term


def test_credentials_doc_has_a_dashboard_section():
    text = (ROOT / "docs" / "credentials.md").read_text()
    assert "## Dashboard" in text


def test_credentials_doc_covers_llm_accounts():
    """The forge-only intro is stale once LLM accounts ship."""
    text = (ROOT / "docs" / "credentials.md").read_text()
    assert "This release handles `forge_token` for `github` and `gitlab` only." not in text, (
        "credentials.md still says this release is forge tokens only"
    )
    for needle in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "XAI_CREDENTIAL_PATH",
        "ERR_PROVIDER_RATE_LIMITED",
        "ERR_PROVIDER_QUOTA_EXHAUSTED",
        "ERR_AUTH_REJECTED",
        "CREDENTIAL_RATE_LIMIT_QUARANTINE_SECONDS",
        "CREDENTIAL_QUOTA_QUARANTINE_SECONDS",
        "ERR_CREDENTIAL_MISSING",
        "agent definitions",
        "/home/orchestrator/.grok/auth.json",
        "proxy-image-builder",
    ):
        assert needle in text, needle
    later = text.split("## Later releases", 1)[1]
    assert "LLM accounts" not in later, "LLM accounts are still listed as a later release"


def test_credentials_doc_takes_the_sandbox_sdk_version_from_the_lock():
    """The sandbox images install claude-agent-sdk from uv.lock (#288), so the doc names no version."""
    text = (ROOT / "docs" / "credentials.md").read_text()
    paragraphs = [p for p in text.split("\n\n") if p.startswith("**Sandbox SDK")]
    assert len(paragraphs) == 1, "credentials.md lost its paragraph on the sandbox's Claude Agent SDK"
    paragraph = paragraphs[0]
    assert "claude-agent-sdk" in paragraph and "uv.lock" in paragraph, (
        f"the sandbox SDK paragraph must say the version comes from uv.lock: {paragraph!r}"
    )
    pinned = re.findall(r"==\s*\S+|\b\d+\.\d+\.\d+\b", paragraph)
    assert not pinned, f"the sandbox SDK paragraph names a version, which the lock moves without it: {pinned}"


def test_providers_doc_uses_the_registry():
    text = (ROOT / "docs" / "providers.md").read_text()
    assert "credential registry" in text, "providers.md still tells the env-only story"
    assert "XAI_API_KEY" in text


def test_env_example_documents_xai_keys_and_quarantine_windows():
    text = (ROOT / ".env.example").read_text()
    for needle in (
        "XAI_API_KEY",
        "XAI_CREDENTIAL_PATH",
        "XAI_GROK_CLI_STORE",
        "XAI_PROXY_ENABLED",
        "CREDENTIAL_RATE_LIMIT_QUARANTINE_SECONDS",
        "CREDENTIAL_QUOTA_QUARANTINE_SECONDS",
    ):
        assert needle in text, needle


def test_claude_md_environment_names_llm_accounts():
    text = (ROOT / "CLAUDE.md").read_text()
    env = text.split("## Environment", 1)[1].split("\n## ", 1)[0]
    assert "LLM accounts" in env, "the Environment section does not say the registry holds LLM accounts"
    assert "docs/ops/deploy.md" in env, "the Environment section does not point at the proxy-rebuild recipe"


def test_deploy_runbook_has_the_proxy_rebuild_recipe():
    """#529 moved the recipe out of CLAUDE.md into the deploy runbook that CLAUDE.md points at."""
    runbook = ROOT / "docs" / "ops" / "deploy.md"
    assert runbook.is_file(), f"{runbook} is missing"
    text = runbook.read_text()
    assert "proxy-image-builder" in text, "the proxy-rebuild deploy note is missing"
    assert "embry0 credentials import-env" in text, "the import after a proxy release is missing"


def test_env_example_credentials_note_is_current():
    """The dashboard ships; the example must not still call it a later release."""
    lines = [line for line in (ROOT / ".env.example").read_text().splitlines() if "docs/credentials.md" in line]
    assert lines, "credentials note missing from .env.example"
    for line in lines:
        assert "later release" not in line, line
