"""The ``ui`` section: what the dashboard shows, its game layer and the page it opens on.

An operator writes it the way they would say it::

    ui:
      game: false     # a plain operations dashboard: no guild, levels, achievements or chat box
      home: overview  # with the game on, open on the Overview instead of the guild

Nothing written keeps the dashboard as it was before the section: the game
on, and the guild as its home. ``game: off`` reads as false too (YAML 1.1).

The section is the deployment's alone. An organization cannot turn the game
off for its own people, because the dashboard has no viewer's organization
to read it for: a session carries an email, a name and roles, and an API key
at most a repository allowlist, and neither names an organization. The game
itself is one account per deployment, whose characters are the deployment's
agents.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from embry0.settings.registry import Section, SectionModel

#: A page the dashboard can open on.
Home = Literal["guild", "overview"]


class UiSettings(SectionModel):
    game: bool = Field(
        default=True,
        description=(
            "Show the game layer: the guild, the agents' levels, skills and outfits, the Achievements page, "
            "the chat box and the Game content tab. While false the dashboard opens on the Overview, and "
            "GET /world, its characters and quests, GET /progression and POST /progression/rebuild answer 404. "
            "Reviewing a job and the content pack's routes work either way."
        ),
    )
    home: Home = Field(
        default="guild",
        description=(
            "The page the dashboard opens on: guild or overview. While `game` is false it opens on the "
            "Overview, whatever this says."
        ),
    )


UI = Section(
    name="ui",
    model=UiSettings,
    summary="What the dashboard shows: the game layer, and the page it opens on.",
    # The deployment's alone: the dashboard knows no viewer's organization
    # (see the module docstring), and the game is one account per deployment.
    org_keys=frozenset(),
    repo_keys=frozenset(),
)
