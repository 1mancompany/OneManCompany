import pytest
from unittest.mock import patch, AsyncMock
from onemancompany.core import product as prod
from onemancompany.core.models import EventType, IssuePriority, IssueResolution, IssueStatus
from onemancompany.core.events import CompanyEvent


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    yield


class TestIssueCreatedTrigger:
    @pytest.mark.asyncio
    async def test_p0_triggers_project(self, tmp_path):
        from onemancompany.core.product_triggers import handle_issue_created

        p = prod.create_product(name="TriggerTest", owner_id="00004", description="obj")
        issue = prod.create_issue(
            slug=p["slug"], title="Critical", description="desc",
            priority=IssuePriority.P0, created_by="ceo",
        )
        event = CompanyEvent(
            type=EventType.ISSUE_CREATED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"]},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = "test-project"
            await handle_issue_created(event)
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_p3_no_trigger(self, tmp_path):
        from onemancompany.core.product_triggers import handle_issue_created

        p = prod.create_product(name="NoTrig", owner_id="00004", description="obj")
        issue = prod.create_issue(
            slug=p["slug"], title="Minor", description="desc",
            priority=IssuePriority.P3, created_by="ceo",
        )
        event = CompanyEvent(
            type=EventType.ISSUE_CREATED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"]},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock:
            await handle_issue_created(event)
            mock.assert_not_called()


class TestProjectCompleteTrigger:
    @pytest.mark.asyncio
    async def test_releases_version_and_closes_issues(self, tmp_path):
        from onemancompany.core.product_triggers import handle_project_complete

        p = prod.create_product(name="VerTrig", owner_id="00004", description="obj")
        i1 = prod.create_issue(
            slug=p["slug"], title="Fix A", description="desc",
            priority=IssuePriority.P1, created_by="ceo",
        )
        event = CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={
                "product_slug": p["slug"],
                "project_id": "proj-1",
                "resolved_issue_ids": [i1["id"]],
            },
        )
        with patch(
            "onemancompany.core.product_triggers.event_bus",
        ) as mock_bus:
            mock_bus.publish = AsyncMock()
            await handle_project_complete(event)

        loaded = prod.load_product(p["slug"])
        assert loaded["current_version"] == "0.1.1"
        issue = prod.load_issue(p["slug"], i1["id"])
        assert issue["status"] == IssueStatus.RELEASED.value


class TestKRTrigger:
    @pytest.mark.asyncio
    async def test_kr_behind_creates_issue(self, tmp_path):
        from onemancompany.core.product_triggers import check_kr_progress

        p = prod.create_product(name="KRCheck", owner_id="00004", description="obj")
        kr = prod.add_key_result(p["slug"], title="DAU", target=1000)
        prod.update_kr_progress(p["slug"], kr["id"], current=50)
        created = await check_kr_progress(p["slug"])
        assert len(created) >= 1
        assert "DAU" in created[0]["title"]
