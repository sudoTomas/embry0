import pytest
from pydantic import ValidationError

from embry0.api.schemas import QAPresignBatchRequest


def test_minimal_request():
    req = QAPresignBatchRequest(sandbox_token="x" * 16, paths=["result.json"])
    assert req.expires_seconds == 3600
    assert req.direction == "put"


def test_rejects_short_token():
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="too-short", paths=["x"])


def test_rejects_empty_paths():
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="x" * 16, paths=[])


def test_rejects_too_many_paths():
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="x" * 16, paths=[f"p{i}" for i in range(65)])


def test_rejects_unknown_direction():
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="x" * 16, paths=["x"], direction="delete")


def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="x" * 16, paths=["x"], evil=True)


# Task 9: path safety
@pytest.mark.parametrize(
    "bad_path",
    [
        "../other-job/result.json",
        "screenshots/../../../escape.png",
        "/absolute/path.png",
        "",
        "with space.png",
        "double//slash.png",
        "ends-with-slash/",
    ],
)
def test_rejects_unsafe_paths(bad_path):
    with pytest.raises(ValidationError):
        QAPresignBatchRequest(sandbox_token="x" * 16, paths=[bad_path])


@pytest.mark.parametrize(
    "good_path",
    [
        "result.json",
        "screenshots/login-2026-04-30T12:01:33.png",
        "logs/full.log",
        "traces/criterion-1.zip",
        "har/criterion-1.har",
    ],
)
def test_accepts_safe_paths(good_path):
    QAPresignBatchRequest(sandbox_token="x" * 16, paths=[good_path])
