"""How settings are shown to people: secrets masked, URL passwords masked (#535).

One rule, read by every renderer of a settings model: ``embry0 config``, the
model's own ``repr``/``str`` (which is what structlog prints for a config
object), and the log lines that echo a URL setting. Before #535 the CLI kept
its own list of seven secret names, and every secret added after that list was
written printed in clear text.

A setting is secret when

* its annotation carries :data:`SECRET` (spelled :data:`SecretSetting` for a
  string), or
* its NAME looks secret (:data:`SECRET_NAME_PATTERN`) and its annotation does
  not carry :data:`NOT_SECRET`.

The name rule is default-deny, so a new ``foo_token`` is masked before anybody
classifies it. The classification must still be written down:
:func:`unclassified_secret_looking_settings` lists every secret-looking name
that carries neither marker, and a unit test fails while that list is not
empty. That way the reader of ``config.py`` can see which settings are
credentials, and a name that only looks secret is marked safe on purpose.

A value that is not secret still goes through :func:`mask_url_credentials`.
The whole userinfo of any URL in it (``scheme://USER:PASSWORD@host``, or a
token written as the bare user, ``scheme://TOKEN@host``) and any
secret-looking query parameter are masked, whatever the setting is called.
:func:`validation_error_lines` renders a settings model's load error the
same way: each variable and why, never the value pydantic attaches.

Pure: no embry0 imports, so ``config.py`` can import it.
"""

from __future__ import annotations

import enum
import functools
import re
from typing import Annotated, Any

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo


class Sensitivity(enum.Enum):
    """How a setting may be shown. Put it in the field's ``Annotated[...]``."""

    SECRET = "secret"
    NOT_SECRET = "not_secret"


SECRET = Sensitivity.SECRET
NOT_SECRET = Sensitivity.NOT_SECRET

# A string setting that is a credential. To consumers it is a plain ``str``,
# so nothing that reads it changes; only the renderers here treat it apart.
SecretSetting = Annotated[str, SECRET]

MASK = "****"

# Below this length the masked form shows nothing of the value. At or above
# it, the first and last four characters stay, enough to tell two tokens
# apart. Eight characters of a 12-character password would be most of it.
_REVEAL_MIN_LEN = 24

# Name fragments that make a setting secret by default. Substrings, not
# words, so that `apikey` and `authtoken` match too; a name that only looks
# secret (`credential_quota_quarantine_seconds`) is marked NOT_SECRET.
_SECRET_NAME_PARTS = ("token", "secret", "pass", "key", "credential", "dsn")
SECRET_NAME_PATTERN = re.compile("|".join(_SECRET_NAME_PARTS), re.IGNORECASE)

_SCHEME = r"[A-Za-z][A-Za-z0-9+.\-]{0,31}://"
_USER = r"[^/?#\s:@]*"

# The userinfo as urllib.parse (and so the client) reads it: everything up to
# the LAST '@' of the authority, which ends at the first '/', '?', '#' or
# whitespace. urlparse accepts a raw '@' in a password. All of it is masked,
# the user included: a token is often written as the bare user
# (`https://<token>@host`), and a user name is not worth the risk.
_URL_USERINFO = re.compile(rf"({_SCHEME})[^/?#\s]+(?=@)")

# A raw '/', '?' or '#' in the password (a base64 password has '/'). urlparse
# cannot read such a URL, so the client fails to connect, and that is exactly
# when an operator runs `embry0 config`. Mask up to the first '@', unless the
# text after the ':' is a port (digits, then '/', '?', '#', whitespace or the
# end), which is a host:port and not a userinfo.
_URL_USERINFO_RAW = re.compile(rf"({_SCHEME}){_USER}:(?!\d*(?:[/?#\s]|$))[^\s@]+(?=@)")

# `?password=…`, `&api_key=…` (libpq accepts `?password=` in a URL), and the
# `sig` capability of a signed webhook URL.
_SECRET_QUERY_PARAM = re.compile(
    rf"([?&](?:[^=&#\s]*(?:{'|'.join(_SECRET_NAME_PARTS)})[^=&#\s]*|sig|signature)=)[^&#\s]+",
    re.IGNORECASE,
)


def mask_secret(value: str) -> str:
    """A secret for display: empty stays empty, so an unset secret shows as unset."""
    if not value:
        return ""
    if len(value) < _REVEAL_MIN_LEN:
        return MASK
    return f"{value[:4]}{MASK}{value[-4:]}"


def mask_url_credentials(text: str) -> str:
    """``text`` with every URL's userinfo and secret query value masked.

    The scheme, host, port, path and the other query parameters stay, so
    ``postgresql://app:pw@db:5432/app`` reads ``postgresql://****@db:5432/app``
    and ``https://<token>@host`` reads ``https://****@host``. Text with no URL
    in it comes back unchanged.
    """
    if not text or ("://" not in text and "?" not in text):
        return text
    text = _URL_USERINFO.sub(rf"\g<1>{MASK}", text)
    text = _URL_USERINFO_RAW.sub(rf"\g<1>{MASK}", text)
    return _SECRET_QUERY_PARAM.sub(rf"\g<1>{MASK}", text)


def _variable(model: type[BaseModel], loc: Any) -> str:
    """The environment variable a field of ``model`` is read from: its alias, or its name, in capitals."""
    name = str(loc)
    field = model.model_fields.get(name)
    return ((field.alias if field is not None else None) or name).upper()


def validation_error_lines(model: type[BaseModel], exc: ValidationError) -> list[str]:
    """Why ``model`` (a settings model) did not load: one ``VARIABLE: message`` line per problem.

    The value is never shown: not pydantic's ``input`` (``str(exc)`` repeats
    it), and a check's own message has its URL credentials masked, since a
    check may quote what it refused.
    """
    lines: list[str] = []
    for error in exc.errors():
        loc = tuple(error.get("loc") or ())
        path = ".".join([_variable(model, loc[0]), *map(str, loc[1:])]) if loc else ""
        msg = mask_url_credentials(str(error.get("msg") or "invalid")).removeprefix("Value error, ")
        # A check that names its own variable ("GITLAB_URL must be ...") is not prefixed with it again.
        lines.append(f"{path}: {msg}" if path and not msg.startswith(f"{path} ") else msg)
    return lines


def _marks(field: FieldInfo) -> set[Sensitivity]:
    return {m for m in field.metadata if isinstance(m, Sensitivity)}


@functools.cache
def secret_setting_names(model: type[BaseModel]) -> frozenset[str]:
    """The fields of ``model`` that every renderer masks."""
    names: set[str] = set()
    for name, field in model.model_fields.items():
        marks = _marks(field)
        if SECRET in marks or (SECRET_NAME_PATTERN.search(name) and NOT_SECRET not in marks):
            names.add(name)
    return frozenset(names)


def unclassified_secret_looking_settings(model: type[BaseModel]) -> list[str]:
    """Secret-looking field names that carry neither SECRET nor NOT_SECRET.

    They are masked anyway (the name rule is default-deny); the guard test
    fails on them so the classification is written next to the field.
    """
    return sorted(
        name for name, field in model.model_fields.items() if SECRET_NAME_PATTERN.search(name) and not _marks(field)
    )


def redact_setting(model: type[BaseModel], name: str, value: Any) -> Any:
    """``value`` of setting ``name`` as it may be shown or logged.

    A secret becomes its masked string. Any other string has its URL
    credentials masked. A value of another type is returned unchanged.
    """
    if name in secret_setting_names(model):
        return mask_secret("" if value is None else str(value))
    if isinstance(value, str):
        return mask_url_credentials(value)
    return value


def redacted_settings(settings: BaseModel) -> list[tuple[str, str]]:
    """``(name, display)`` for every field of ``settings``, sorted by name."""
    model = type(settings)
    return [(name, str(redact_setting(model, name, getattr(settings, name)))) for name in sorted(model.model_fields)]
