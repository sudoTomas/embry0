"""A section that exists only in tests, so the settings foundation is proven before any feature uses it.

Its fields cover each kind a real section will have: a switch, a whole
number with bounds, a number, a choice, a list imported from a
comma-separated variable, and text with a validator. Its ``env`` names real
``Embry0Config`` attributes, so the environment import reads what the
running config was given.
"""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationInfo, field_validator

from embry0.settings.registry import Catalog, EnvImport, Section, SectionModel


def split_commas(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


class SampleSettings(SectionModel):
    enabled: bool = Field(default=False, description="Run the sample loop.")
    per_tick: int = Field(default=3, ge=1, le=20, description="Items the sample loop handles each tick.")
    remind_hours: float = Field(default=24.0, gt=0, description="Hours before a reminder.")
    mode: Literal["off", "shadow", "on"] = Field(default="shadow", description="How the sample acts.")
    labels: list[str] = Field(default_factory=list, max_length=10, description="Labels the sample reads.")
    zone: str = Field(default="UTC", description="The time zone the sample reads hours in.")

    @field_validator("zone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("not a time zone this system knows") from None
        return value


SAMPLE = Section(
    name="sample",
    model=SampleSettings,
    summary="A section that exists only in tests.",
    org_keys=frozenset({"per_tick", "remind_hours"}),
    repo_keys=frozenset({"mode"}),
    env={
        "enabled": "asks_enabled",
        "per_tick": "proposal_briefs_per_tick",
        "remind_hours": "ask_remind_hours",
        "labels": EnvImport("scout_repos", split_commas),
        "zone": "ask_timezone",
    },
)


class OtherSettings(SectionModel):
    limit: int = Field(default=5, ge=0, description="A second section's only key.")


OTHER = Section(name="other", model=OtherSettings, summary="A second section, for documents with two.")

TEST_CATALOG = Catalog((SAMPLE, OTHER))


class BrittleSettings(SectionModel):
    zone: str = Field(default="UTC", description="A zone checked the way zoneinfo checks it.")
    hours: float = Field(default=24.0, gt=0, description="Hours, beside the zone.")

    @field_validator("zone")
    @classmethod
    def _zone(cls, value: str) -> str:
        # Not caught on purpose: ZoneInfoNotFoundError is a KeyError, which
        # pydantic does not turn into a validation error.
        ZoneInfo(value)
        return value


#: A section whose check raises something other than ValueError for a bad value.
BRITTLE = Section(
    name="brittle",
    model=BrittleSettings,
    summary="A section whose check raises KeyError.",
    env={"zone": "ask_timezone", "hours": "ask_remind_hours"},
)


class WindowSettings(SectionModel):
    low: int = Field(default=10, ge=0, le=100, description="The lower bound.")
    high: int = Field(default=50, ge=0, le=100, description="The upper bound, never below the lower one.")
    label: str = Field(default="", max_length=40, description="Text only the deployment sets.")

    @field_validator("high")
    @classmethod
    def _not_below_low(cls, value: int, info: ValidationInfo) -> int:
        low = info.data.get("low")
        if isinstance(low, int) and value < low:
            raise ValueError("high must not be below low")
        return value


#: A section whose fields are checked against each other, so a layer's value
#: can be refused together with what it inherits: ``high`` is checked against
#: ``low``, and the refusal names ``high`` whichever layer set ``low``.
WINDOW = Section(
    name="window",
    model=WindowSettings,
    summary="A section whose keys are checked against each other.",
    org_keys=frozenset({"low", "high"}),
    repo_keys=frozenset({"high"}),
)

#: The test sections with layers: ``sample`` (organization and repository keys),
#: ``other`` (no key a layer may set) and ``window`` (keys checked together).
LAYERED_CATALOG = Catalog((SAMPLE, OTHER, WINDOW))
