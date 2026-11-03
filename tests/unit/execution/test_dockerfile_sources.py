"""The build-context paths a Dockerfile reads (#320): what the sandbox image's build hash covers."""

from __future__ import annotations

from pathlib import Path

import pytest

from embry0.execution.dockerfile_sources import context_sources, instructions

ROOT = Path(__file__).resolve().parents[3]


def test_comments_inside_a_continued_instruction_are_dropped_and_the_lines_joined() -> None:
    text = "RUN apt-get install \\\n    # a comment Docker drops\n    git \\\n    curl\nCOPY a b\n"
    assert instructions(text) == ["RUN apt-get install git curl", "COPY a b"]


def test_copy_and_add_sources_from_the_build_context() -> None:
    text = (
        "COPY embry0/sandbox/ /app/embry0/sandbox/\n"
        "COPY --chown=agent:agent --chmod=0755 one.py two.py /app/\n"
        'COPY ["with space.py", "/app/with space.py"]\n'
        "ADD infra/files.tar /opt/\n"
        "copy lower.py /app/\n"
    )
    assert context_sources(text) == [
        "embry0/sandbox/",
        "one.py",
        "two.py",
        "with space.py",
        "infra/files.tar",
        "lower.py",
    ]


def test_sources_from_another_stage_an_image_or_a_url_are_not_the_build_context() -> None:
    text = (
        "COPY --from=builder /out/app /app\n"
        "COPY --from=ghcr.io/acme/tool:1 /bin/tool /usr/local/bin/tool\n"
        "ADD https://example.com/tool.tgz /opt/\n"
        "ADD local.txt https://example.com/x.tgz /opt/\n"
    )
    assert context_sources(text) == ["local.txt"]


def test_bind_mounts_from_the_build_context_and_not_from_a_stage_or_a_cache() -> None:
    text = (
        "RUN --mount=from=uv,source=/uv,target=/opt/uv/uv \\\n"
        "    --mount=type=bind,source=uv.lock,target=/opt/lock/uv.lock \\\n"
        "    --mount=src=pyproject.toml,target=/opt/lock/pyproject.toml \\\n"
        "    --mount=type=cache,target=/root/.cache \\\n"
        "    --mount=type=secret,id=token \\\n"
        "    pip install --mount=type=bind,source=not-a-flag -r x\n"
    )
    assert context_sources(text) == ["uv.lock", "pyproject.toml"]


def test_each_source_once_in_order() -> None:
    assert context_sources("COPY a /x\nCOPY b /y\nCOPY a /z\n") == ["a", "b"]


@pytest.mark.parametrize(
    "text",
    [
        "COPY <<EOF /app/x\nhello\nEOF\n",
        "RUN --mount=type=bind,target=/ctx ls /ctx\n",
        "COPY onlyone\n",
    ],
)
def test_a_form_the_hash_cannot_follow_is_refused_not_skipped(text: str) -> None:
    with pytest.raises(ValueError):
        context_sources(text)


@pytest.mark.parametrize("name", ["Dockerfile.sandbox", "Dockerfile.sandbox.qa"])
def test_the_sandbox_dockerfiles_parse_and_every_source_exists(name: str) -> None:
    sources = context_sources((ROOT / "infra" / name).read_text(encoding="utf-8"))
    assert sources, f"infra/{name} reads files from the build context"
    missing = [s for s in sources if not (ROOT / s).exists()]
    assert not missing, f"every source infra/{name} names exists: {missing}"
