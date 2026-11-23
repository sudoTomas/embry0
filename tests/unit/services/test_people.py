"""What a person's fields and an identity may hold (#179, ``embry0.services.people``)."""

from __future__ import annotations

from typing import Any

import pytest

try:
    from embry0.services.people import IDENTITY_KINDS, PersonInvalid, check_email, check_fields, check_identity
except ImportError:
    pytest.fail("there is nothing that checks a person's fields and identities", pytrace=False)


def _refused(call: Any, *args: Any) -> PersonInvalid:
    with pytest.raises(PersonInvalid) as refused:
        call(*args)
    return refused.value


def test_the_identity_kinds_are_the_forges_and_the_chats() -> None:
    assert IDENTITY_KINDS == ("github", "gitlab", "telegram", "slack", "teams", "discord")


@pytest.mark.parametrize(
    ("kind", "value", "stored"),
    [
        ("github", " @Ada-L ", "Ada-L"),
        ("gitlab", "ada.l_2", "ada.l_2"),
        ("telegram", "123456789", "123456789"),
        ("slack", "U024BE7LH", "U024BE7LH"),
        ("teams", "ada@example.com", "ada@example.com"),
        ("discord", "80351110224678912", "80351110224678912"),
    ],
)
def test_an_identity_is_stored_trimmed_and_a_forge_login_without_its_at(kind: str, value: str, stored: str) -> None:
    assert check_identity(kind, value) == (kind, stored)


@pytest.mark.parametrize(
    ("kind", "value", "words"),
    [
        ("myspace", "ada", "an identity is one of github, gitlab"),
        ("github", "  ", "give the github identity"),
        ("github", "-ada", "not a github login"),
        ("github", "ada--l", "not a github login"),
        ("github", "a" * 40, "not a github login"),
        ("gitlab", "ada l", "not a gitlab login"),
        ("slack", "U02 4BE", "one word of plain text"),
        ("telegram", "x" * 256, "at most 255 characters"),
        ("discord", "8035\x00", "one word of plain text"),
    ],
)
def test_an_identity_no_forge_or_chat_could_have_is_refused_without_repeating_it(
    kind: str, value: str, words: str
) -> None:
    refused = _refused(check_identity, kind, value)
    assert words in str(refused), str(refused)
    assert value.strip() not in refused.problem or not value.strip(), f"the refusal repeats the value: {refused}"


def test_an_email_is_optional_trimmed_and_checked() -> None:
    assert check_email(None) is None and check_email("  ") is None
    assert check_email(" Ada@Example.com ") == "Ada@Example.com"
    refused = _refused(check_email, "ada at example")
    assert refused.field == "email" and "ada at example" not in str(refused)


def test_fields_are_checked_by_the_asks_grammar_and_none_clears_an_optional_one() -> None:
    assert check_fields(
        {"name": "  Ada  ", "hours": " 9 - 18 ", "weekdays": " Mon - Fri ", "timezone": " Europe/Berlin "}
    ) == {"name": "Ada", "hours": "09-18", "weekdays": "mon-fri", "timezone": "Europe/Berlin"}
    assert check_fields({"email": None, "user_id": None, "hours": None, "disabled": True}) == {
        "email": None,
        "user_id": None,
        "hours": None,
        "disabled": True,
    }


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"name": " "}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"name": "two\nlines"}, "name"),
        ({"hours": "18-09"}, "hours"),
        ({"weekdays": "monday"}, "weekdays"),
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"disabled": "yes"}, "disabled"),
        ({"user_id": " "}, "user_id"),
        ({"nickname": "Ada"}, "nickname"),
    ],
)
def test_a_field_a_person_cannot_hold_is_refused_naming_it(fields: dict[str, Any], field: str) -> None:
    refused = _refused(check_fields, fields)
    assert refused.field == field, refused
    value = next(iter(fields.values()))
    if isinstance(value, str) and value.strip():
        assert value not in refused.problem, f"the refusal repeats the value: {refused}"
