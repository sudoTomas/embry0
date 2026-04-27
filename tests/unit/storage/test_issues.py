"""Tests for IssuesRepository."""

import os

import asyncpg
import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations
from athanor.storage.repositories.issues import IssuesRepository


@pytest.fixture
async def issues_repo(pg_pool: asyncpg.Pool) -> IssuesRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    repo = IssuesRepository(db)
    yield repo
    await db.close()


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
        rows, total = await issues_repo.list()
        assert total == 0
        assert rows == []

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, issues_repo: IssuesRepository):
        id1 = await issues_repo.create(title="Open issue")
        id2 = await issues_repo.create(title="Closed issue")
        await issues_repo.update(id2, status="closed")

        rows, total = await issues_repo.list(status="open")
        assert total == 1
        assert rows[0]["id"] == id1

        rows, total = await issues_repo.list(status="closed")
        assert total == 1
        assert rows[0]["id"] == id2

    @pytest.mark.asyncio
    async def test_list_filters_by_priority(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Low priority", priority="low")
        await issues_repo.create(title="High priority", priority="high")

        rows, total = await issues_repo.list(priority="high")
        assert total == 1
        assert rows[0]["title"] == "High priority"

    @pytest.mark.asyncio
    async def test_list_filters_by_repo(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Repo A issue", repo="owner/repo-a")
        await issues_repo.create(title="Repo B issue", repo="owner/repo-b")

        rows, total = await issues_repo.list(repo="owner/repo-a")
        assert total == 1
        assert rows[0]["repo"] == "owner/repo-a"

    @pytest.mark.asyncio
    async def test_list_top_level_only(self, issues_repo: IssuesRepository):
        parent_id = await issues_repo.create(title="Parent")
        await issues_repo.create(title="Child", parent_issue_id=parent_id)

        rows, total = await issues_repo.list(top_level_only=True)
        assert total == 1
        assert rows[0]["id"] == parent_id

    @pytest.mark.asyncio
    async def test_list_search(self, issues_repo: IssuesRepository):
        await issues_repo.create(title="Fix the login bug", body="Users cannot log in")
        await issues_repo.create(title="Add dark mode", body="Feature request")

        rows, total = await issues_repo.list(search="login")
        assert total == 1
        assert rows[0]["title"] == "Fix the login bug"

        rows, total = await issues_repo.list(search="feature")
        assert total == 1
        assert rows[0]["title"] == "Add dark mode"

    @pytest.mark.asyncio
    async def test_list_pagination(self, issues_repo: IssuesRepository):
        for i in range(5):
            await issues_repo.create(title=f"Issue {i}")

        rows, total = await issues_repo.list(limit=2, offset=0)
        assert total == 5
        assert len(rows) == 2

        rows2, total2 = await issues_repo.list(limit=2, offset=2)
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
