"""PUT /repos/{owner}/{repo}/-/preferences is a partial update (#350), end to end on a real database."""

import pytest

pytestmark = pytest.mark.asyncio

URL = "/api/v1/repos/acme/partial-prefs/-/preferences"

FULL = {
    "language_hint": "python",
    "notes": "keep me",
    "git_author_name": "Bot",
    "git_author_email": "bot@example.com",
    "dev_checks": [{"name": "py", "paths": [], "command": "pytest -q"}],
    "base_branch": "dev",
}


async def test_a_put_naming_one_field_leaves_the_others_untouched(api_client):
    await api_client.delete(URL)
    assert (await api_client.put(URL, json=FULL)).status_code == 200

    resp = await api_client.put(URL, json={"notes": "only this"})
    assert resp.status_code == 200, resp.text

    stored = (await api_client.get(URL)).json()
    kept = {k: stored[k] for k in FULL}
    assert kept == {**FULL, "notes": "only this"}, f"a one-field PUT wiped the others: {kept}"


async def test_an_explicit_null_clears_a_field(api_client):
    await api_client.delete(URL)
    await api_client.put(URL, json=FULL)
    resp = await api_client.put(URL, json={"base_branch": None, "git_author_email": None, "dev_checks": []})
    body = resp.json()
    assert (body["base_branch"], body["git_author_email"], body["dev_checks"]) == (None, None, [])
    assert body["language_hint"] == "python"
