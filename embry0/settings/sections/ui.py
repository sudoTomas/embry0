"""The ``ui`` section: what the dashboard shows, its game layer and the page it opens on.

An operator writes it the way they would say it::

    ui:
      game: false     # a plain operations dashboard: no guild, levels, achievements or chat box
      home: overview  # with the game on, open on the Overview instead of the guild

Nothing written keeps the dashboard as it was before the section: the game
on, and the guild as its home. ``game: off`` reads as false too (YAML 1.1).

An organization may set both keys for the people who see only its work: a
caller whose API key has a repository allowlist reads the values of the
organizations its list covers (ADR 0029, ``embry0/api/v1/ui.py``). Every
other caller, every SSO session among them, reads the deployment's: a session
carries an email, a name and roles, and names no organization. A repository
cannot set either: no caller is one repository's. The game itself stays one
account per deployment, whose characters are the deployment's agents, so an
organization's value changes what its people are shown, not what is counted.
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
            "Reviewing a job and the content pack's routes work either way. An organization's value is read by "
            "API keys whose repository allowlist covers its repositories: such a key sees the game when any "
            "organization it covers has it on. Every other caller, every SSO session among them, reads the "
            "deployment's."
        ),
    )
    home: Home = Field(
        default="guild",
        description=(
            "The page the dashboard opens on: guild or overview. While `game` is false it opens on the "
            "Overview, whatever this says. A key whose allowlist covers several organizations opens on the "
            "page those with the game on agree on, else on the deployment's."
        ),
    )


UI = Section(
    name="ui",
    model=UiSettings,
    summary="What the dashboard shows: the game layer, and the page it opens on.",
    # Read for a caller whose key covers only some organizations' repositories (ADR 0029); no caller is one
    # repository's, so a repository's layer would be read by no one.
    org_keys=frozenset({"game", "home"}),
    repo_keys=frozenset(),
)
