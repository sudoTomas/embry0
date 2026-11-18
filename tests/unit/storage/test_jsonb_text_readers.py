"""Readers that decode a JSONB value arriving as text read it as the codec does (#597).

A value stored encoded twice, or one from a pool without the codec, arrives as
a ``str``, and a few readers decode it themselves. They used ``json.loads``, a
second copy of the codec's reading: the codec reads a number past a float's
range as its digits (#537), a ``str``, and when a column held only such a
number, these readers took the digits for text to decode and read them back as
``inf``, which JSON cannot write. Each now decodes through
:func:`~embry0.storage.database.read_jsonb_text`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import pytest

from embry0.api.v1 import decisions, job_review
from embry0.api.v1.jobs import _as_object
from embry0.storage.database import _jsonb_decoder
from embry0.storage.repositories import knowledge_maps, leads
from embry0.storage.repositories.jobs import job_repository

PAST = "1" + "0" * 400 + ".5"  # a number past a float's range, as Postgres writes it
TOO_LONG = "1" + "0" * sys.get_int_max_str_digits()  # an integer past int's digit limit (#523)

READERS: dict[str, Callable[[Any], Any]] = {
    "jobs._as_object": _as_object,
    "job_review._json_field": job_review._json_field,
    "decisions._json": decisions._json,
    "knowledge_maps._json": knowledge_maps._json,
    "leads._row": lambda value: leads._row({"data": value})["data"],
}


@pytest.mark.parametrize("name", READERS)
def test_a_column_holding_only_such_a_number_keeps_its_digits(name: str) -> None:
    value = _jsonb_decoder(PAST)  # what the codec gives for the column
    assert value == PAST, "the codec reads it as its digits"
    read = READERS[name](value)
    assert read == PAST, f"{name} reads it as {read!r}"
    json.dumps(read, allow_nan=False)  # what a route writes


@pytest.mark.parametrize("name", READERS)
def test_text_holding_one_is_read_as_the_codec_reads_it(name: str) -> None:
    """A value stored encoded twice still decodes, with such a number and such an integer as their digits."""
    read = READERS[name](f'{{"n": {PAST}, "m": {TOO_LONG}, "x": 2.5}}')
    assert read == {"n": PAST, "m": TOO_LONG, "x": 2.5}, f"{name}: {str(read)[:60]}"


@pytest.mark.parametrize(
    ("name", "fallback"), [("jobs._as_object", "{oops"), ("job_review._json_field", None), ("decisions._json", None)]
)
def test_text_that_is_not_json_keeps_each_readers_fallback(name: str, fallback: Any) -> None:
    assert READERS[name]("{oops") == fallback


def test_a_jobs_repository_from_its_context_text_reads_as_the_codec_does() -> None:
    assert job_repository({"context": json.dumps({"repo": "acme/widgets", "n": 1.5})}) == "acme/widgets"
    assert job_repository({"context": PAST}) is None, "a context that is only such a number names no repository"
    named = job_repository({"context": f'{{"repo": "acme/widgets", "n": {TOO_LONG}}}'})
    assert named == "acme/widgets", "a context holding an integer past int's digit limit loses its repository"
    assert job_repository({"context": "{oops"}) is None
