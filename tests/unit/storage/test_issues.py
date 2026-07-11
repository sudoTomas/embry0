"""Tests for IssuesRepository."""

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import StatusTransitionConflict

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def issues_repo(db_with_migrations: DatabasePool) -> IssuesRepository:
    return IssuesRepository(db_with_migrations)


class TestCreateIssue:
    @pytest.mark.asyncio
    async def test_create_returns_id(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(title="Test issue")
        assert issue_id.startswith("iss-")
        assert len(issue_id) == 16  # "iss-" + 12 hex chars

    @pytest.mark.asyncio
    async def test_create_minimal(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(title="Minimal issue")
        issue = await issues_repo.get(issue_id)
        assert issue is not None
        assert issue["title"] == "Minimal issue"
        assert issue["body"] == ""
        assert issue["status"] == "open"
        assert issue["priority"] == "medium"
        assert issue["labels"] == []
        assert issue["repo"] is None
        assert issue["parent_issue_id"] is None
        assert issue["github_sync_enabled"] is False
        assert issue["created_by"] == "user"

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(
            title="Full issue",
            body="Detailed description",
            priority="high",
            labels=["bug", "urgent"],
            repo="owner/repo",
            github_sync_enabled=True,
            created_by="admin",
        )
        issue = await issues_repo.get(issue_id)
        assert issue is not None
        assert issue["title"] == "Full issue"
        assert issue["body"] == "Detailed description"
        assert issue["priority"] == "high"
        assert issue["labels"] == ["bug", "urgent"]
        assert issue["repo"] == "owner/repo"
        assert issue["github_sync_enabled"] is True
        assert issue["created_by"] == "admin"


class TestGetIssue:
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, issues_repo: IssuesRepository):
        result = await issues_repo.get("iss-doesnotexist")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_includes_counts(self, issues_repo: IssuesRepository):
        parent_id = await issues_repo.create(title="Parent issue")
        await issues_repo.create(title="Child 1", parent_issue_id=parent_id)
        await issues_repo.create(title="Child 2", parent_issue_id=parent_id)

        issue = await issues_repo.get(parent_id)
        assert issue is not None
        assert issue["children_count"] == 2
        assert issue["jobs_count"] == 0
        assert issue["active_agent"] is None


class TestListIssues:
    @pytest.mark.asyncio
    async def test_list_empty(self, issues_repo: IssuesRepository):
        # Session-scoped DB may have rows from other tests; just check shape
        rows, total = await issues_repo.list_all()
        assert isinstance(total, int)
        assert isinstance(rows, list)

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, issues_repo: IssuesRepository):
        id1 = await issues_repo.create(title="Open issue unique-filter-status")
        id2 = await issues_repo.create(title="Closed issue unique-filter-status")
        await issues_repo.update(id2, status="closed")

        rows, total = await issues_repo.list_all(search="unique-filter-status")
        assert total == 2
        open_rows, _ = await issues_repo.list_all(search="unique-filter-status", status="open")
        assert any(r["id"] == id1 for r in open_rows)

        closed_rows, _ = await issues_repo.list_all(search="unique-filter-status", status="closed")
        assert any(r["id"] == id2 for r in closed_rows)

    @pytest.mark.asyncio
    async def test_list_filters_by_priority(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Low priority unique-filter-prio", priority="low")
        await issues_repo.create(title="High priority unique-filter-prio", priority="high")

        rows, total = await issues_repo.list_all(search="unique-filter-prio", priority="high")
        assert total == 1
        assert rows[0]["title"] == "High priority unique-filter-prio"

    @pytest.mark.asyncio
    async def test_list_filters_by_repo(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Repo A issue", repo="owner/repo-a-unique-filter")
        await issues_repo.create(title="Repo B issue", repo="owner/repo-b-unique-filter")

        rows, total = await issues_repo.list_all(repo="owner/repo-a-unique-filter")
        assert total == 1
        assert rows[0]["repo"] == "owner/repo-a-unique-filter"

    @pytest.mark.asyncio
    async def test_list_top_level_only(self, issues_repo: IssuesRepository):
        parent_id = await issues_repo.create(title="Parent unique-top-level")
        child_id = await issues_repo.create(title="Child unique-top-level", parent_issue_id=parent_id)

        rows, _ = await issues_repo.list_all(search="unique-top-level", top_level_only=True)
        ids = {r["id"] for r in rows}
        assert parent_id in ids
        assert child_id not in ids

    @pytest.mark.asyncio
    async def test_list_search(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Fix the zqxlogin99 bug", body="Users cannot log in")
        await issues_repo.create(title="Add zqxdarkmode99 feature", body="Feature request zqxdarkmode99")

        rows, total = await issues_repo.list_all(search="zqxlogin99")
        assert total == 1
        assert rows[0]["title"] == "Fix the zqxlogin99 bug"

        rows, total = await issues_repo.list_all(search="zqxdarkmode99")
        assert total == 1
        assert rows[0]["title"] == "Add zqxdarkmode99 feature"

    @pytest.mark.asyncio
    async def test_list_pagination(self, issues_repo: IssuesRepository):
        for i in range(5):
            await issues_repo.create(title=f"Pagination Issue {i} unique-pagination")

        rows, total = await issues_repo.list_all(search="unique-pagination", limit=2, offset=0)
        assert total == 5
        assert len(rows) == 2

        rows2, total2 = await issues_repo.list_all(search="unique-pagination", limit=2, offset=2)
        assert total2 == 5
        assert len(rows2) == 2

        # Pages should not overlap
        ids1 = {r["id"] for r in rows}
        ids2 = {r["id"] for r in rows2}
        assert ids1.isdisjoint(ids2)


class TestUpdateIssue:
    @pytest.mark.asyncio
    async def test_update_fields(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(title="Original", priority="low")
        await issues_repo.update(issue_id, title="Updated", priority="high", status="in_progress")

        issue = await issues_repo.get(issue_id)
        assert issue is not None
        assert issue["title"] == "Updated"
        assert issue["priority"] == "high"
        assert issue["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_update_sets_updated_at(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(title="Timestamp test")
        issue_before = await issues_repo.get(issue_id)
        assert issue_before is not None
        created_at = issue_before["created_at"]
        updated_at_before = issue_before["updated_at"]

        await issues_repo.update(issue_id, title="Changed")

        issue_after = await issues_repo.get(issue_id)
        assert issue_after is not None
        # updated_at should be >= created_at
        assert issue_after["updated_at"] >= created_at
        # updated_at should not decrease
        assert issue_after["updated_at"] >= updated_at_before


class TestGetChildren:
    @pytest.mark.asyncio
    async def test_get_children(self, issues_repo: IssuesRepository):
        parent_id = await issues_repo.create(title="Parent")
        child1_id = await issues_repo.create(title="Child A", parent_issue_id=parent_id)
        child2_id = await issues_repo.create(title="Child B", parent_issue_id=parent_id)
        # Unrelated issue should not appear
        await issues_repo.create(title="Unrelated")

        children = await issues_repo.get_children(parent_id)
        child_ids = {c["id"] for c in children}
        assert child_ids == {child1_id, child2_id}
        assert all(c["parent_issue_id"] == parent_id for c in children)


class TestGetByGitHub:
    @pytest.mark.asyncio
    async def test_get_by_github(self, issues_repo: IssuesRepository):
        issue_id = await issues_repo.create(title="GitHub issue", repo="owner/repo")
        await issues_repo.update(issue_id, github_number=42, github_url="https://github.com/owner/repo/issues/42")

        result = await issues_repo.get_by_github("owner/repo", 42)
        assert result is not None
        assert result["id"] == issue_id
        assert result["github_number"] == 42

    @pytest.mark.asyncio
    async def test_get_by_github_not_found(self, issues_repo: IssuesRepository):
        result = await issues_repo.get_by_github("owner/nonexistent", 999)
        assert result is None


class TestIssueStatusTransitionConflict:
    @pytest.mark.asyncio
    async def test_cas_valid_transition_succeeds(self, issues_repo: IssuesRepository):
        """A valid status transition applies correctly via the CAS path."""
        issue_id = await issues_repo.create(title="CAS test")
        await issues_repo.update(issue_id, status="in_progress")
        issue = await issues_repo.get(issue_id)
        assert issue is not None
        assert issue["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_status_transition_conflict_is_runtime_error(self, issues_repo: IssuesRepository):
        """StatusTransitionConflict must be importable and a RuntimeError subclass."""
        exc = StatusTransitionConflict("conflict")
        assert isinstance(exc, RuntimeError)
        assert "conflict" in str(exc)


class TestFindByIdPrefix:
    @pytest.mark.asyncio
    async def test_find_by_id_prefix_finds_match(self, issues_repo: IssuesRepository):
        """Basic prefix match returns the right issue."""
        iid = await issues_repo.create(title="t", body="b", repo="o/r", created_by="user")
        short = iid[:8]  # e.g. "iss-abc1"
        matches = await issues_repo.find_by_id_prefix(short)
        assert len(matches) == 1
        assert matches[0]["id"] == iid

    @pytest.mark.asyncio
    async def test_find_by_id_prefix_no_match(self, issues_repo: IssuesRepository):
        """Prefix that matches no issue returns empty list."""
        matches = await issues_repo.find_by_id_prefix("iss-zzzzz")
        assert matches == []

    @pytest.mark.asyncio
    async def test_find_by_id_prefix_escapes_underscore(self, issues_repo: IssuesRepository):
        """Underscore must not act as a single-char wildcard."""
        # Create an issue so there is something in the table that a wildcard would match
        await issues_repo.create(title="t", body="b", repo="o/r", created_by="user")
        # 'iss-_' as a prefix would match anything starting with 'iss-' if not escaped
        matches = await issues_repo.find_by_id_prefix("iss-_")
        # Only literal "iss-_..." would match; no real ID starts with underscore after "iss-"
        assert matches == []

    @pytest.mark.asyncio
    async def test_find_by_id_prefix_escapes_percent(self, issues_repo: IssuesRepository):
        """Percent must not act as multi-char wildcard."""
        await issues_repo.create(title="t", body="b", repo="o/r", created_by="user")
        matches = await issues_repo.find_by_id_prefix("iss-%")
        assert matches == []

    @pytest.mark.asyncio
    async def test_find_by_id_prefix_empty(self, issues_repo: IssuesRepository):
        """Empty prefix returns empty list without querying DB."""
        matches = await issues_repo.find_by_id_prefix("")
        assert matches == []
