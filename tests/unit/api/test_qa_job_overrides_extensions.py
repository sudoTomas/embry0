"""Phase-C1: QAJobOverrides has base_branch + force_all_apps."""

from __future__ import annotations

import pytest


def test_qa_overrides_accepts_base_branch():
    from embry0.api.schemas import QAJobOverrides

    o = QAJobOverrides(base_branch="master")
    assert o.base_branch == "master"


def test_qa_overrides_default_base_branch_is_none():
    from embry0.api.schemas import QAJobOverrides

    o = QAJobOverrides()
    assert o.base_branch is None


def test_qa_overrides_accepts_force_all_apps():
    from embry0.api.schemas import QAJobOverrides

    o = QAJobOverrides(force_all_apps=True)
    assert o.force_all_apps is True


def test_qa_overrides_default_force_all_apps_is_false():
    from embry0.api.schemas import QAJobOverrides

    o = QAJobOverrides()
    assert o.force_all_apps is False


def test_qa_overrides_rejects_unknown_extras():
    """extra='forbid' is set on QAJobOverrides; unknown keys -> 422."""
    from embry0.api.schemas import QAJobOverrides

    with pytest.raises(Exception):
        QAJobOverrides(unknown_field="x")


def test_qa_overrides_base_branch_max_length():
    """A reasonable upper bound prevents pathological inputs from sneaking
    into shell-quoted git commands. 255 is the conventional ref-length cap."""
    from embry0.api.schemas import QAJobOverrides

    QAJobOverrides(base_branch="x" * 255)
    with pytest.raises(Exception):
        QAJobOverrides(base_branch="x" * 256)
