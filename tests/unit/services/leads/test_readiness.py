"""How ready a backlog ticket reads, in its organization's words (spec 2026-11-23, #168).

- **Golden.** The default section reads a fixed set of tickets exactly as
  the code did before the vocabulary became settings: each ticket's score,
  reasons, effort and severity in :data:`TODAY` were recorded by running
  the code on main before this change over :data:`CORPUS`.
- **An organization's words.** Its labels, weights, done phrases, epic title
  words and estimate cut-offs decide its tickets' readings.
- **Phrases.** Matched as written, in any case and language, at word
  boundaries where a phrase starts or ends with a word character, and never
  made into a pattern: no phrase makes reading a ticket slow.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from embry0.services.leads.readiness import Readiness
from embry0.settings.sections.leads import LeadsSettings
from tests.fixtures.leads import leads_policy

#: Tickets of every kind the default vocabulary reads: (title, body, labels, estimate, priority).

LONG = "The nightly export job writes every invoice of the month into one CSV file. " * 6
CORPUS = [
    (
        "Export fails on an empty start date",
        "The export fails with `ValueError: invalid date` when the start date is empty.\nIt happens in "
        "services/export/report.py when the form posts no date.\n\nExpected behavior: an empty start date "
        "exports everything.\n- [ ] export with no start date works\n",
        ["bug"],
        None,
        0,
    ),
    ("Improve things", "", [], None, 0),
    ("Epic: rebuild the billing pipeline", LONG, ["bug"], None, 0),
    ("[Epic] new onboarding flow", "Make onboarding nicer.", [], None, 0),
    ("epic(billing): split invoices", LONG + " Acceptance criteria: invoices split.", [], None, 0),
    ("EPIC — move the queue to the new broker", LONG, ["chore"], None, 0),
    (
        "fix(leads): epic filter drops contacts",
        "The filter in app/leads/filter.ts drops contacts tagged epic. It should keep them.",
        ["bug"],
        None,
        0,
    ),
    (
        "Crash on login with SSO enabled",
        "Traceback (most recent call last): File auth/sso.py line 40 KeyError: 'email'",
        ["Bug", "P1"],
        1.0,
        0,
    ),
    ("Question about the API rate limits", "How many calls can a client make per minute?", ["question"], None, 0),
    ("Refactor the date helpers into one module", LONG, ["refactor", "good first issue"], None, 0),
    (
        "Migrate the database driver",
        "Steps to reproduce: run the migration with the new driver. ERROR: relation missing.",
        ["tech-debt", "size/xl"],
        8.0,
        2,
    ),
    (
        "Docs: explain the settings document",
        "Add a section to docs/configuration.md describing layers. Done when the page lists every layer.",
        ["documentation"],
        2.0,
        4,
    ),
    ("Spike the new search engine", "Research options.", ["spike"], None, 0),
    (
        "Bump dependencies",
        "Run the updater and fix what breaks in package.json and requirements.txt.",
        ["dependencies", "chore"],
        1.0,
        3,
    ),
    (
        "Security: sign the session cookies",
        "The cookie set in web/session.py is not signed; an HTTP 500 response follows a tampered one. "
        "Definition of done: every cookie is signed.",
        ["security"],
        None,
        0,
    ),
    (
        "Flaky test in the scheduler",
        "tests/test_scheduler.py fails one run in ten with a timeout. Expected result: it passes every run.",
        ["test", "regression"],
        3.0,
        0,
    ),
    ("Add dark mode", "Users want dark mode.", ["enhancement"], None, 0),
    ("Fix", "x", ["bug"], None, 0),
    ("Blocked: waiting on the vendor", LONG, ["blocked", "bug"], None, 0),
    (
        "Rename the report columns to match the spec",
        "The columns in report.py and report_test.py use old names; rename them as in docs/spec.md. "
        "Should match exactly.",
        ["chore", "small"],
        None,
        0,
    ),
    ("Large rewrite of the importer", LONG + " panic: runtime error in importer.go", ["large"], 13.0, 1),
    (
        "Payment page returns 502",
        "Customers get HTTP 502 on checkout; the gateway times out. Expected results: checkout completes.",
        ["critical", "sev1"],
        None,
        0,
    ),
    ("Discussion: monorepo or not", LONG, ["discussion", "needs design"], None, 0),
    (
        "Handle null customer names in the CSV export",
        "NullPointer when a customer has no name; see src/export/Csv.java. Steps to reproduce: export a "
        "customer without name.",
        [" Bug ", "easy"],
        0.5,
        0,
    ),
    ("Speed up the dashboard", "It feels slow.", ["performance"], 5.0, 0),
    (
        "Write unit tests for the parser",
        "Cover parser.py with tests for every branch.\n- [ ] empty input\n- [ ] bad header",
        ["tests", "xs"],
        None,
        0,
    ),
    (
        "Expected behaviours of the queue",
        "Document the expected behaviours of the queue in docs/queue.md.",
        ["DOCS"],
        None,
        0,
    ),
    (
        "Upgrade the runtime",
        "Upgrade to the next runtime version. Acceptance Criteria: CI green.",
        ["maintenance", "size:s"],
        None,
        3,
    ),
    ("RFC: plugin API", LONG, ["rfc"], None, 0),
    (
        "Shoulder the load of the old importer",
        "The old importer in legacy/import.py still runs; move its jobs.",
        ["chore"],
        None,
        0,
    ),
    (
        "Login button misaligned",
        "On mobile the login button overlaps the logo in web/login.tsx.",
        ["bug", "l"],
        None,
        0,
    ),
    (
        "Sync fails for archived projects",
        "segfault in the sync worker when a project is archived (worker/sync.rs).",
        ["bug", "high"],
        None,
        2,
    ),
    ("A ticket with an estimate only", LONG, [], 2.0, 0),
    ("Another ticket with a large estimate", LONG, [], 5.0, 0),
    ("Idea: gamify the backlog", "What if tickets gave points?", ["idea"], None, 0),
    ("Wontfix: old browser support", LONG, ["wontfix"], None, 0),
    (
        "The importer shouldn't drop empty rows",
        "When a row is empty the importer skips it silently in importer/rows.go.",
        ["bug", "Größe/XS"],
        None,
        0,
    ),
    ("Löschen der alten Rechnungen", "Die alten Rechnungen werden nicht gelöscht.", ["Fehler"], None, 4),
]

#: What the code on main before #168 read in each ticket of :data:`CORPUS`: (score, reasons, effort, severity).
TODAY = [
    (
        75,
        ["a description", "names files", "quotes an error", "says what done looks like", "labelled bug"],
        "medium",
        "medium",
    ),
    (0, ["a short title"], "small", "medium"),
    (5, ["a full description", "labelled bug", "an epic by its title"], "medium", "medium"),
    (0, ["an epic by its title"], "small", "medium"),
    (
        0,
        ["a full description", "says what done looks like", "an epic by its title", "a short title"],
        "medium",
        "medium",
    ),
    (5, ["a full description", "labelled chore", "an epic by its title"], "medium", "medium"),
    (65, ["a description", "names files", "says what done looks like", "labelled bug"], "medium", "medium"),
    (55, ["names files", "quotes an error", "labelled bug", "small"], "small", "high"),
    (0, ["labelled question"], "small", "medium"),
    (45, ["a full description", "labelled refactor", "small"], "small", "medium"),
    (
        45,
        ["a description", "quotes an error", "says what done looks like", "labelled tech-debt", "large"],
        "large",
        "high",
    ),
    (
        75,
        ["a description", "names files", "says what done looks like", "labelled documentation", "small"],
        "small",
        "low",
    ),
    (0, ["labelled spike"], "small", "medium"),
    (35, ["names files", "labelled chore", "small", "a short title"], "small", "medium"),
    (65, ["a description", "names files", "quotes an error", "says what done looks like"], "medium", "critical"),
    (65, ["a description", "names files", "says what done looks like", "labelled test"], "medium", "high"),
    (0, ["a short title"], "small", "medium"),
    (5, ["labelled bug", "a short title"], "small", "medium"),
    (5, ["a full description", "labelled bug", "labelled blocked"], "medium", "medium"),
    (75, ["a description", "names files", "says what done looks like", "labelled chore", "small"], "small", "medium"),
    (45, ["a full description", "names files", "quotes an error", "large"], "large", "critical"),
    (45, ["a description", "quotes an error", "says what done looks like"], "medium", "critical"),
    (0, ["a full description", "labelled discussion"], "medium", "medium"),
    (
        85,
        ["a description", "names files", "quotes an error", "says what done looks like", "labelled bug", "small"],
        "small",
        "medium",
    ),
    (0, ["large"], "large", "medium"),
    (60, ["names files", "says what done looks like", "labelled tests", "small"], "small", "medium"),
    (50, ["names files", "says what done looks like", "labelled docs"], "small", "medium"),
    (30, ["says what done looks like", "labelled maintenance", "small", "a short title"], "small", "medium"),
    (0, ["a full description", "labelled rfc", "a short title"], "medium", "medium"),
    (35, ["names files", "labelled chore"], "small", "medium"),
    (15, ["names files", "labelled bug", "large", "a short title"], "large", "medium"),
    (45, ["names files", "quotes an error", "labelled bug"], "small", "high"),
    (35, ["a full description", "small"], "small", "medium"),
    (15, ["a full description", "large"], "large", "medium"),
    (0, ["labelled idea"], "small", "medium"),
    (0, ["a full description", "labelled wontfix"], "medium", "medium"),
    (35, ["names files", "labelled bug"], "small", "medium"),
    (5, [], "small", "low"),
]


def _reading(**vocabulary: Any) -> Readiness:
    """The reading an organization's layer holding ``vocabulary`` gives its repositories, through the settings
    documents as the loop reads them: its label roles and signals merged over the defaults, the rest replaced."""
    return Readiness.of(leads_policy(organizations={"acme": vocabulary}).repo("acme/web"))


DEFAULT = Readiness.of(LeadsSettings())


def test_the_default_section_reads_every_ticket_of_the_fixed_set_as_the_code_did_before() -> None:
    assert len(CORPUS) == len(TODAY), "a reading was recorded for each ticket"
    differ = []
    for (title, body, labels, estimate, priority), today in zip(CORPUS, TODAY, strict=True):
        score, why = DEFAULT.score(title, body, labels, estimate)
        now = (score, why, DEFAULT.effort(labels, estimate, body), DEFAULT.severity(labels, priority))
        if now != today:
            differ.append((title, now, today))
    assert differ == [], f"the defaults must read each ticket as the code did before #168 (now, before): {differ}"


def test_an_organization_s_own_labels_decide_its_tickets_readiness() -> None:
    title, body = "Export fails on an empty start date", CORPUS[0][1]
    labels = ["kind/bug", "T-Shirt/S"]
    assert DEFAULT.score(title, body, labels)[0] == 65, "by default neither label means anything"
    own = _reading(readiness_labels={"ready": ["Kind/Bug"], "small": ["t-shirt/s "], "urgent": ["kind/bug"]})
    score, why = own.score(title, body, labels)
    assert (score, why[-2:]) == (85, ["labelled kind/bug", "small"]), (
        f"its ready and small labels add their points, matched without regard to case: {score} {why}"
    )
    assert (own.severity(labels), DEFAULT.severity(labels)) == ("critical", "medium"), "its urgent labels too"
    assert own.score(title, body, ["bug"])[0] == 65, "the role it names is replaced whole: bug is no longer ready"
    assert own.score(title, body, ["epic"])[0] == 35, "a role it does not name keeps the default's labels"
    unlabelled = _reading(readiness_labels={"not_ready": []})
    assert unlabelled.score(title, body, ["epic"])[0] == 65, "an empty role gives that role no label"


def test_an_organization_s_weights_and_estimates_decide_its_points() -> None:
    title, body = "Export fails on an empty start date", CORPUS[0][1]
    score, why = _reading(readiness_weights={"done": 0, "paths": 40}).score(title, body, ["bug"])
    assert score == 80, f"its weights replace the default's points: {score} {why}"
    assert "says what done looks like" not in why, f"a signal it weighs at nothing is no reason: {why}"
    assert _reading(readiness_weights={"short_title": -50}).score("Fix", "x", ["bug"])[0] == 0
    big = _reading(readiness_weights={"description_full": 100}).score("Rewrite app/main.py", "x" * 400, [])
    assert big == (100, ["a full description", "names files", "a short title"]), f"kept within 0 to 100: {big}"
    points = _reading(small_estimate_max=3, large_estimate_min=13)
    assert (points.effort([], 3, ""), DEFAULT.effort([], 3, "")) == ("small", "small"), "both read 3 as small"
    assert (points.effort([], 8, ""), DEFAULT.effort([], 8, "")) == ("small", "large"), "8 is large by default only"
    assert points.effort([], 13, "") == "large"
    both = _reading(small_estimate_max=8, large_estimate_min=5)
    assert (both.effort([], 6, ""), both.score("Move the jobs to the queue", "", [], 6)[1]) == (
        "large",
        ["small", "large"],
    ), "an estimate that reads as both small and large takes both signals and a large effort"


@pytest.mark.parametrize(
    ("text", "done"),
    [
        ("Done when: the export runs.", True),
        ("It is undone whenever the export runs.", False),
        ("DONE WHEN the export runs", True),
        ("done  when the export runs", False),
        ("it should work", True),
        ("it shouldn't break", False),
        ("Shoulder the load", False),
        ("a list:\n- [ ] first", True),
        ("x- [ ]x", True),
        ("acceptance criteria:", True),
        ("acceptance criterias", False),
        ("expected behaviors", True),
        ("expected behaviorally", False),
        ("criteria acceptance", False),
        ("", False),
    ],
)
def test_a_default_done_phrase_reads_at_word_boundaries(text: str, done: bool) -> None:
    assert DEFAULT.says_done(text) is done, f"{text!r} {'says' if done else 'does not say'} what done looks like"


def test_a_phrase_inside_a_longer_word_no_longer_reads_as_done() -> None:
    """The one change the defaults make, and why the fixed set has no such ticket: the pattern before #168
    found its words inside longer ones, so "undone whenever" said what done looks like (+15)."""
    score, why = DEFAULT.score("Fix the importer for empty rows", "Undone whenever a row is empty.", [])
    assert (score, why) == (5, []), f"a short description, and no done phrase: {score} {why}"


@pytest.mark.parametrize(
    ("phrase", "text", "done"),
    [
        ("Akzeptanzkriterien", "AKZEPTANZKRITERIEN: der Export läuft.", True),
        ("Akzeptanzkriterien", "Keine Akzeptanzkriterienliste.", False),
        ("straße", "Die STRASSE ist gesperrt.", True),
        ("критерии приёмки", "Критерии приёмки: экспорт работает.", True),
        ("критерии приёмки", "Подкритерии приёмки: нет.", False),
        ("完成条件", "完成条件：导出成功。", True),
        ("a.b", "axb", False),
        ("(?:", "a literal (?: in the text", True),
        ("#done", "x#done", True),
        ("#done", "#doneness", False),
        ("done#", "done#x", True),
    ],
)
def test_an_organization_s_phrases_read_in_any_language_and_literally(phrase: str, text: str, done: bool) -> None:
    reading = _reading(done_phrases=[phrase])
    assert reading.says_done(text) is done, f"{phrase!r} in {text!r}: {done}"
    assert not reading.says_done("Done when: the export runs."), "the list replaces the default phrases whole"


def test_a_mark_character_in_a_ticket_reads_as_a_space() -> None:
    reading = _reading(done_phrases=["done when"])
    assert reading.says_done("done\x00when"), "the mark a boundary is written with reads as the space it stands for"
    assert not reading.says_done("un\x00\x00done whenever")


def test_no_phrase_makes_reading_a_long_ticket_slow() -> None:
    """A hundred phrases of 80 characters that each nearly match, on a 64 KiB description: a pattern built of
    them backtracks at every position; a substring search does not. About 10 ms; the bound is generous."""
    reading = _reading(done_phrases=[f"{'a ' * 38}{chr(0x62 + n % 24)}{n}" for n in range(100)])
    body = "a " * 32_768
    started = time.perf_counter()
    assert not reading.says_done(body)
    assert time.perf_counter() - started < 2.0, "reading one ticket takes time about linear in its text"


@pytest.mark.parametrize(
    ("title", "epic"),
    [
        ("Initiative: rebuild billing", True),
        ("[ INITIATIVE ] rebuild billing", True),
        ("initiative(billing) \u2013 split invoices", True),
        ("Initiative (billing)   : split invoices", True),
        ("Initiative" + " " * 5000 + "x", False),
        ("Initiatives: rebuild billing", False),
        ("Rebuild billing, an initiative: plan", False),
        ("Epic: rebuild billing", False),
    ],
)
def test_an_organization_s_epic_title_words_make_a_title_an_epic_s(title: str, epic: bool) -> None:
    reading = _reading(epic_title_words=["Initiative"])
    why = reading.score(title, "", [])[1]
    assert ("an epic by its title" in why) is epic, f"{title!r}: {why}"


def test_no_epic_title_words_read_no_title_as_an_epic() -> None:
    assert "an epic by its title" not in _reading(epic_title_words=[]).score("Epic: rebuild billing", "", [])[1]
