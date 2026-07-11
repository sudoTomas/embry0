import pytest
from pydantic import ValidationError

from embry0.api.schemas import ContextType, JobContext


def test_git_context_valid():
    c = JobContext(type=ContextType.git, repo="owner/name")
    assert c.type == ContextType.git and c.repo == "owner/name"


def test_git_context_requires_repo():
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.git)


def test_git_context_rejects_bad_repo_and_extras():
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.git, repo="not-a-repo")
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.git, repo="owner/name", url="https://x")


def test_http_context_valid_and_requires_url():
    assert JobContext(type=ContextType.http, url="https://example.com/data.csv").url
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.http)
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.http, url="ftp://nope")


def test_local_context_absolute_only():
    assert JobContext(type=ContextType.local, path="/data/in.csv").path
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.local, path="relative/path")
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.local, path="/a/../etc/passwd")


def test_none_context_takes_no_fields():
    assert JobContext(type=ContextType.none).type == ContextType.none
    with pytest.raises(ValidationError):
        JobContext(type=ContextType.none, repo="owner/name")
