import asyncio

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from onemancompany.core import product as prod
from onemancompany.core.models import EventType, IssuePriority, IssueResolution, IssueStatus
from onemancompany.core.events import CompanyEvent


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    yield


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_product(status="active", **kw):
    return prod.create_product(
        name=kw.pop("name", "TestProd"),
        owner_id=kw.pop("owner_id", "00004"),
        description=kw.pop("description", "obj"),
        status=prod.ProductStatus(status),
        **kw,
    )


def _make_issue(slug, priority=IssuePriority.P0, **kw):
    return prod.create_issue(
        slug=slug,
        title=kw.pop("title", "Test Issue"),
        description=kw.pop("description", "desc"),
        priority=priority,
        created_by=kw.pop("created_by", "ceo"),
        **kw,
    )


# ---------------------------------------------------------------------------
# handle_issue_created
# ---------------------------------------------------------------------------

class TestIssueCreatedTrigger:
    @pytest.mark.asyncio
    async def test_p0_triggers_project(self):
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product()
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="Critical")
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
    async def test_p3_no_trigger(self):
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product()
        issue = _make_issue(p["slug"], priority=IssuePriority.P3, title="Minor")
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

    @pytest.mark.asyncio
    async def test_issue_not_found_returns_early(self):
        """Line 38-39: issue not found -> early return."""
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product()
        event = CompanyEvent(
            type=EventType.ISSUE_CREATED,
            payload={"product_slug": p["slug"], "issue_id": "nonexistent"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock:
            await handle_issue_created(event)
            mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_planning_gate_skips(self):
        """Line 43-45: product in planning status -> skip."""
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product(status="planning", name="PlanProd")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="Urgent")
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

    @pytest.mark.asyncio
    async def test_priority_enum_value_normalization(self):
        """Line 49-50: priority as enum with .value attr is normalized."""
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product(name="EnumNorm")
        # Create issue with enum priority (IssuePriority stores as .value in YAML,
        # but we want to test the hasattr(priority, 'value') branch)
        issue = _make_issue(p["slug"], priority=IssuePriority.P1, title="EnumTest")
        # Patch load_issue to return priority as the enum object itself
        raw_issue = dict(issue)
        raw_issue["priority"] = IssuePriority.P1  # enum, not string
        with patch("onemancompany.core.product_triggers.prod.load_issue", return_value=raw_issue):
            with patch("onemancompany.core.product_triggers.prod.load_product", return_value={"status": "active"}):
                event = CompanyEvent(
                    type=EventType.ISSUE_CREATED,
                    payload={"product_slug": p["slug"], "issue_id": issue["id"]},
                )
                with patch(
                    "onemancompany.core.product_triggers._create_project_for_issue",
                    new_callable=AsyncMock,
                ) as mock:
                    mock.return_value = "proj-enum"
                    await handle_issue_created(event)
                    mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_p2_priority_skips_with_log(self):
        """Lines 53-58: non-auto-project priority -> skip with debug log."""
        from onemancompany.core.product_triggers import handle_issue_created

        p = _make_product(name="P2Skip")
        issue = _make_issue(p["slug"], priority=IssuePriority.P2, title="Medium")
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


# ---------------------------------------------------------------------------
# _create_project_for_issue
# ---------------------------------------------------------------------------

class TestCreateProjectForIssue:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        """Lines 79-137: full function — mock all external deps, verify call sequence."""
        from onemancompany.core.product_triggers import _create_project_for_issue

        p = _make_product(name="FullFlow")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="CriticalBug")

        mock_async_create = AsyncMock(return_value=("proj-123", "iter-1"))
        mock_get_dir = MagicMock(return_value="/tmp/proj-123")
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.id = "root-1"
        mock_ea_node = MagicMock()
        mock_ea_node.id = "ea-1"
        mock_tree_inst = MagicMock()
        mock_tree_inst.create_root.return_value = mock_root
        mock_tree_inst.add_child.return_value = mock_ea_node
        mock_tree.return_value = mock_tree_inst
        mock_save = MagicMock()
        mock_em = MagicMock()
        mock_em.schedule_node = MagicMock()
        mock_em._schedule_next = MagicMock()

        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create), \
             patch("onemancompany.core.project_archive.get_project_dir", mock_get_dir), \
             patch("onemancompany.core.task_tree.TaskTree", mock_tree), \
             patch("onemancompany.core.vessel._save_project_tree", mock_save), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            result = await _create_project_for_issue(p["slug"], issue)

        assert result == "proj-123"
        mock_async_create.assert_called_once()
        mock_tree.assert_called_once_with(project_id="proj-123/iter-1", mode="standard")
        mock_tree_inst.create_root.assert_called_once()
        mock_tree_inst.add_child.assert_called_once()
        mock_save.assert_called_once_with("/tmp/proj-123", mock_tree_inst)
        mock_em.schedule_node.assert_called_once()
        mock_em._schedule_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """Lines 132-137: exception -> log + return empty string."""
        from onemancompany.core.product_triggers import _create_project_for_issue

        p = _make_product(name="ExcFlow")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="Boom")

        mock_async_create = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create):
            result = await _create_project_for_issue(p["slug"], issue)
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_iter_id(self):
        """Line 94: iter_id is empty -> ctx_id is just project_id."""
        from onemancompany.core.product_triggers import _create_project_for_issue

        p = _make_product(name="NoIter")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="NoIterIssue")

        mock_async_create = AsyncMock(return_value=("proj-abc", ""))
        mock_get_dir = MagicMock(return_value="/tmp/proj-abc")
        mock_tree_inst = MagicMock()
        mock_root = MagicMock()
        mock_root.id = "root-1"
        mock_ea_node = MagicMock()
        mock_ea_node.id = "ea-1"
        mock_tree_inst.create_root.return_value = mock_root
        mock_tree_inst.add_child.return_value = mock_ea_node
        mock_tree_cls = MagicMock(return_value=mock_tree_inst)
        mock_em = MagicMock()

        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create), \
             patch("onemancompany.core.project_archive.get_project_dir", mock_get_dir), \
             patch("onemancompany.core.task_tree.TaskTree", mock_tree_cls), \
             patch("onemancompany.core.vessel._save_project_tree", MagicMock()), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            result = await _create_project_for_issue(p["slug"], issue)

        assert result == "proj-abc"
        # ctx_id should be just project_id when iter_id is empty
        mock_tree_cls.assert_called_once_with(project_id="proj-abc", mode="standard")


# ---------------------------------------------------------------------------
# handle_project_complete
# ---------------------------------------------------------------------------

class TestProjectCompleteTrigger:
    @pytest.mark.asyncio
    async def test_releases_version_and_closes_issues(self):
        from onemancompany.core.product_triggers import handle_project_complete

        p = _make_product(name="VerTrig")
        i1 = _make_issue(p["slug"], priority=IssuePriority.P1, title="Fix A")
        event = CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={
                "product_slug": p["slug"],
                "project_id": "proj-1",
                "resolved_issue_ids": [i1["id"]],
            },
        )
        with patch("onemancompany.core.product_triggers.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            with patch("onemancompany.core.product_triggers.run_product_check", new_callable=AsyncMock), \
                 patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock):
                await handle_project_complete(event)

        loaded = prod.load_product(p["slug"])
        assert loaded["current_version"] == "0.1.1"
        issue = prod.load_issue(p["slug"], i1["id"])
        assert issue["status"] == IssueStatus.RELEASED.value

    @pytest.mark.asyncio
    async def test_empty_slug_skips(self):
        """Lines 147-148: empty slug -> early return."""
        from onemancompany.core.product_triggers import handle_project_complete

        event = CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={"product_slug": "", "project_id": "proj-1", "resolved_issue_ids": ["i1"]},
        )
        # Should not raise, just return
        await handle_project_complete(event)

    @pytest.mark.asyncio
    async def test_empty_resolved_ids_skips_version(self):
        """Lines 157-159: no resolved issues -> skip version release, call run_product_check."""
        from onemancompany.core.product_triggers import handle_project_complete

        p = _make_product(name="NoResolved")
        event = CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={
                "product_slug": p["slug"],
                "project_id": "proj-1",
                "resolved_issue_ids": [],
            },
        )
        with patch("onemancompany.core.product_triggers.run_product_check", new_callable=AsyncMock) as mock_check, \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock):
            await handle_project_complete(event)
            mock_check.assert_called_once_with(p["slug"])

        # Version should NOT have been bumped
        loaded = prod.load_product(p["slug"])
        assert loaded["current_version"] == "0.1.0"


# ---------------------------------------------------------------------------
# sync_issue_statuses
# ---------------------------------------------------------------------------

class TestSyncIssueStatuses:
    def test_delegates_to_prod(self):
        """Line 198: delegates to prod.sync_issue_statuses."""
        from onemancompany.core.product_triggers import sync_issue_statuses

        with patch("onemancompany.core.product_triggers.prod.sync_issue_statuses", return_value=[{"issue_id": "i1"}]) as mock_sync:
            result = sync_issue_statuses("test-slug")
            mock_sync.assert_called_once_with("test-slug")
            assert result == [{"issue_id": "i1"}]


# ---------------------------------------------------------------------------
# check_kr_progress
# ---------------------------------------------------------------------------

class TestKRTrigger:
    @pytest.mark.asyncio
    async def test_kr_behind_creates_issue(self):
        from onemancompany.core.product_triggers import check_kr_progress

        p = _make_product(name="KRCheck")
        kr = prod.add_key_result(p["slug"], title="DAU", target=1000)
        prod.update_kr_progress(p["slug"], kr["id"], current=50)
        created = await check_kr_progress(p["slug"])
        assert len(created) >= 1
        assert "DAU" in created[0]["title"]

    @pytest.mark.asyncio
    async def test_product_not_found(self):
        """Lines 208-209: product not found -> return []."""
        from onemancompany.core.product_triggers import check_kr_progress
        result = await check_kr_progress("nonexistent-slug")
        assert result == []

    @pytest.mark.asyncio
    async def test_kr_at_60_pct_no_issue(self):
        """Line 223: progress >= 50% -> skip."""
        from onemancompany.core.product_triggers import check_kr_progress

        p = _make_product(name="KRGood")
        kr = prod.add_key_result(p["slug"], title="Revenue", target=100)
        prod.update_kr_progress(p["slug"], kr["id"], current=60)
        created = await check_kr_progress(p["slug"])
        assert len(created) == 0

    @pytest.mark.asyncio
    async def test_kr_target_zero_skipped(self):
        """Line 220: target <= 0 -> continue."""
        from onemancompany.core.product_triggers import check_kr_progress

        p = _make_product(name="KRZero")
        prod.add_key_result(p["slug"], title="Bad", target=0)
        created = await check_kr_progress(p["slug"])
        assert len(created) == 0

    @pytest.mark.asyncio
    async def test_already_tracked_kr_skipped(self):
        """Lines 231-235: existing open issue for KR -> skip."""
        from onemancompany.core.product_triggers import check_kr_progress

        p = _make_product(name="KRDup")
        kr = prod.add_key_result(p["slug"], title="Signups", target=1000)
        prod.update_kr_progress(p["slug"], kr["id"], current=10)
        # Create an existing issue mentioning this KR
        prod.create_issue(
            slug=p["slug"], title="Track Signups progress",
            description="", priority=IssuePriority.P2, created_by="system",
        )
        created = await check_kr_progress(p["slug"])
        assert len(created) == 0


# ---------------------------------------------------------------------------
# run_product_check
# ---------------------------------------------------------------------------

class TestRunProductCheck:
    @pytest.fixture(autouse=True)
    def _mock_owner_review(self):
        with patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock):
            yield

    @pytest.mark.asyncio
    async def test_not_found_skips(self):
        """Line 269: product not found."""
        from onemancompany.core.product_triggers import run_product_check
        result = await run_product_check("nonexistent")
        assert result["skipped"] is True
        assert result["reason"] == "not found"

    @pytest.mark.asyncio
    async def test_not_active_skips(self):
        """Product not active -> skip."""
        from onemancompany.core.product_triggers import run_product_check
        _make_product(status="planning", name="PlanOnly")
        result = await run_product_check("planonly")
        assert result["skipped"] is True
        assert "status=" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_owner_skips(self):
        """Lines 275-276: no owner -> skip."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="NoOwner", owner_id="")
        result = await run_product_check(p["slug"])
        assert result["skipped"] is True
        assert result["reason"] == "no owner"

    @pytest.mark.asyncio
    async def test_unassigned_p0_creates_project(self):
        """Lines 289-318: unassigned P0 issue with no linked tasks -> create project."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="P0Gap")
        _make_issue(p["slug"], priority=IssuePriority.P0, title="Critical Gap")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ) as mock_create:
                mock_create.return_value = "proj-gap"
                result = await run_product_check(p["slug"])

        assert result["skipped"] is False
        assert any("Critical Gap" in a for a in result["actions"])
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_assigned_issue_no_project_creates_project(self):
        """Lines 321-332: issue with assignee but no linked tasks -> create project."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="AssignGap")
        issue = _make_issue(p["slug"], priority=IssuePriority.P3, title="Assigned Task")
        # Set assignee on issue
        prod.update_issue(p["slug"], issue["id"], assignee_id="emp-1")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ) as mock_create:
                mock_create.return_value = "proj-assign"
                result = await run_product_check(p["slug"])

        assert result["skipped"] is False
        assert any("Assigned Task" in a for a in result["actions"])

    @pytest.mark.asyncio
    async def test_kr_no_issue_creates_issue(self):
        """Lines 334-360: KR with no matching issue -> create issue."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="KRNoIssue")
        prod.add_key_result(p["slug"], title="Revenue Growth", target=100)
        # Update progress to be incomplete but not 0
        kr = prod.load_product(p["slug"])["key_results"][0]
        prod.update_kr_progress(p["slug"], kr["id"], current=30)

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ):
                result = await run_product_check(p["slug"])

        assert result["skipped"] is False
        assert any("Revenue Growth" in a for a in result["actions"])

    @pytest.mark.asyncio
    async def test_everything_handled_no_action(self):
        """Lines 362-365: all issues handled -> no actions."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="AllGood")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="Done Issue")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ):
                result = await run_product_check(p["slug"])

        assert result["skipped"] is False
        assert result["actions"] == []

    @pytest.mark.asyncio
    async def test_three_plus_active_projects_skips_creation(self):
        """Lines 307-309: 3+ active projects -> skip further project creation."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="TooMany")
        pid = p.get("id", "prod_x")
        _make_issue(p["slug"], priority=IssuePriority.P0, title="Should Skip")

        active_projects = [
            {"project_id": f"proj-{i}", "status": "active", "product_id": pid}
            for i in range(3)
        ]

        with patch("onemancompany.core.project_archive.list_projects", return_value=active_projects):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ) as mock_create:
                result = await run_product_check(p["slug"])

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_with_active_project_skipped(self):
        """Lines 298-299: issue already has active project -> skip."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="ActiveProj")
        pid = p.get("id", "prod_x")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="InProgress")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj-existing"])

        active_projects = [
            {"project_id": "proj-existing", "status": "active", "product_id": pid}
        ]

        with patch("onemancompany.core.project_archive.list_projects", return_value=active_projects):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ) as mock_create:
                result = await run_product_check(p["slug"])

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_assignee_path_three_plus_cap(self):
        """Line 323: assigned issue path also respects 3+ active projects cap."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="AssignCap")
        pid = p.get("id", "prod_x")
        issue = _make_issue(p["slug"], priority=IssuePriority.P3, title="LowPri Assigned")
        prod.update_issue(p["slug"], issue["id"], assignee_id="emp-1")

        active_projects = [
            {"project_id": f"proj-{i}", "status": "active", "product_id": pid}
            for i in range(3)
        ]

        with patch("onemancompany.core.project_archive.list_projects", return_value=active_projects):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ) as mock_create:
                result = await run_product_check(p["slug"])

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_kr_met_or_zero_target_skipped(self):
        """Line 340: KR with target<=0 or current>=target is skipped."""
        from onemancompany.core.product_triggers import run_product_check

        p = _make_product(name="KRMet")
        # KR with target already met
        kr1 = prod.add_key_result(p["slug"], title="Done KR", target=100)
        prod.update_kr_progress(p["slug"], kr1["id"], current=100)
        # KR with zero target
        prod.add_key_result(p["slug"], title="Zero KR", target=0)

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            with patch(
                "onemancompany.core.product_triggers._create_project_for_issue",
                new_callable=AsyncMock,
            ):
                result = await run_product_check(p["slug"])

        # No issues should be created for met/zero KRs
        kr_actions = [a for a in result.get("actions", []) if "Done KR" in a or "Zero KR" in a]
        assert len(kr_actions) == 0



# ---------------------------------------------------------------------------
# product_health_check
# ---------------------------------------------------------------------------

class TestProductHealthCheck:
    @pytest.mark.asyncio
    async def test_calls_sync_and_check_for_each_product(self):
        """Lines 383-404: iterates products, calls sync + run_product_check."""
        from onemancompany.core.product_triggers import product_health_check

        p1 = _make_product(name="Prod1")
        p2 = _make_product(name="Prod2")

        with patch(
            "onemancompany.core.product_triggers.sync_issue_statuses",
            return_value=[{"issue_id": "i1", "old": "backlog", "new": "in_progress"}],
        ) as mock_sync, \
             patch(
                 "onemancompany.core.product_triggers.run_product_check",
                 new_callable=AsyncMock,
                 return_value={"actions": ["Created project for P0/P1 issue: X"]},
             ) as mock_check:
            events = await product_health_check()

        assert mock_sync.call_count == 2
        assert mock_check.call_count == 2
        assert events is not None
        assert len(events) == 2
        assert events[0].type == EventType.ACTIVITY

    @pytest.mark.asyncio
    async def test_no_changes_returns_none(self):
        """Lines 383-404: no changes -> returns None."""
        from onemancompany.core.product_triggers import product_health_check

        _make_product(name="Quiet")

        with patch("onemancompany.core.product_triggers.sync_issue_statuses", return_value=[]), \
             patch(
                 "onemancompany.core.product_triggers.run_product_check",
                 new_callable=AsyncMock,
                 return_value={"actions": []},
             ):
            events = await product_health_check()

        assert events is None

    @pytest.mark.asyncio
    async def test_skips_products_without_slug(self):
        """Line 387-388: product with empty slug is skipped."""
        from onemancompany.core.product_triggers import product_health_check

        with patch("onemancompany.core.product_triggers.prod.list_products", return_value=[{"slug": "", "name": "NoSlug"}]):
            with patch("onemancompany.core.product_triggers.sync_issue_statuses") as mock_sync:
                events = await product_health_check()
                mock_sync.assert_not_called()
        assert events is None


# ---------------------------------------------------------------------------
# handle_issue_assigned
# ---------------------------------------------------------------------------

class TestHandleIssueAssigned:
    @pytest.mark.asyncio
    async def test_normal_flow_creates_project(self):
        """Lines 409-439: assigned issue with no linked tasks -> create project."""
        from onemancompany.core.product_triggers import handle_issue_assigned

        p = _make_product(name="AssignFlow")
        issue = _make_issue(p["slug"], priority=IssuePriority.P1, title="Assign Me")
        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"], "assignee_id": "emp-1"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = "proj-assigned"
            await handle_issue_assigned(event)
            mock_create.assert_called_once()

        # Verify issue updated with linked task
        updated = prod.load_issue(p["slug"], issue["id"])
        assert "proj-assigned" in updated.get("linked_task_ids", [])

    @pytest.mark.asyncio
    async def test_issue_not_found(self):
        """Line 414-416: issue not found -> return."""
        from onemancompany.core.product_triggers import handle_issue_assigned

        p = _make_product(name="AssignNoIssue")
        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={"product_slug": p["slug"], "issue_id": "nope", "assignee_id": "emp-1"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock_create:
            await handle_issue_assigned(event)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_planning_gate(self):
        """Lines 420-422: product in planning -> skip."""
        from onemancompany.core.product_triggers import handle_issue_assigned

        p = _make_product(status="planning", name="PlanAssign")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="NoPlan")
        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"], "assignee_id": "emp-1"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock_create:
            await handle_issue_assigned(event)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_done_issue_skips(self):
        """Lines 425-427: done issue -> skip."""
        from onemancompany.core.product_triggers import handle_issue_assigned

        p = _make_product(name="DoneAssign")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="AlreadyDone")
        prod.close_issue(p["slug"], issue["id"], resolution=IssueResolution.FIXED)
        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"], "assignee_id": "emp-1"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock_create:
            await handle_issue_assigned(event)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_linked_tasks_skips(self):
        """Lines 430-433: issue already has linked tasks -> skip."""
        from onemancompany.core.product_triggers import handle_issue_assigned

        p = _make_product(name="LinkedAssign")
        issue = _make_issue(p["slug"], priority=IssuePriority.P0, title="AlreadyLinked")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj-old"])
        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={"product_slug": p["slug"], "issue_id": issue["id"], "assignee_id": "emp-1"},
        )
        with patch(
            "onemancompany.core.product_triggers._create_project_for_issue",
            new_callable=AsyncMock,
        ) as mock_create:
            await handle_issue_assigned(event)
            mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# register_product_triggers
# ---------------------------------------------------------------------------

class TestRegisterProductTriggers:
    @pytest.mark.asyncio
    async def test_dispatch_loop_routes_events(self):
        """Lines 459-482: verify dispatch loop routes events correctly."""
        from onemancompany.core.product_triggers import register_product_triggers

        events_to_send = [
            CompanyEvent(type=EventType.ISSUE_CREATED, payload={"product_slug": "s", "issue_id": "i1"}),
            CompanyEvent(type=EventType.ISSUE_ASSIGNED, payload={"product_slug": "s", "issue_id": "i2", "assignee_id": "e1"}),
            CompanyEvent(type=EventType.AGENT_DONE, payload={"product_slug": "s", "project_id": "p1"}),
            CompanyEvent(type=EventType.AGENT_DONE, payload={"project_id": "p2"}),  # no slug -> skip
        ]

        mock_queue = asyncio.Queue()
        for e in events_to_send:
            mock_queue.put_nowait(e)

        with patch("onemancompany.core.product_triggers.event_bus") as mock_bus, \
             patch("onemancompany.core.product_triggers.handle_issue_created", new_callable=AsyncMock) as mock_ic, \
             patch("onemancompany.core.product_triggers.handle_issue_assigned", new_callable=AsyncMock) as mock_ia, \
             patch("onemancompany.core.product_triggers.handle_project_complete", new_callable=AsyncMock) as mock_pc:
            mock_bus.subscribe.return_value = mock_queue
            task = register_product_triggers()

            # Let the loop process all events
            await asyncio.sleep(0.05)

            # Cancel the infinite loop
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_ic.assert_called_once()
        mock_ia.assert_called_once()
        mock_pc.assert_called_once()  # only the one with product_slug

    @pytest.mark.asyncio
    async def test_dispatch_loop_handles_exception(self):
        """Lines 475-478: exception in handler -> logged, loop continues."""
        from onemancompany.core.product_triggers import register_product_triggers

        events_to_send = [
            CompanyEvent(type=EventType.ISSUE_CREATED, payload={"product_slug": "s", "issue_id": "i1"}),
            CompanyEvent(type=EventType.ISSUE_ASSIGNED, payload={"product_slug": "s", "issue_id": "i2", "assignee_id": "e1"}),
        ]

        mock_queue = asyncio.Queue()
        for e in events_to_send:
            mock_queue.put_nowait(e)

        with patch("onemancompany.core.product_triggers.event_bus") as mock_bus, \
             patch("onemancompany.core.product_triggers.handle_issue_created", new_callable=AsyncMock, side_effect=RuntimeError("boom")) as mock_ic, \
             patch("onemancompany.core.product_triggers.handle_issue_assigned", new_callable=AsyncMock) as mock_ia:
            mock_bus.subscribe.return_value = mock_queue
            task = register_product_triggers()

            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Both handlers were attempted despite the first one raising
        mock_ic.assert_called_once()
        mock_ia.assert_called_once()


# ---------------------------------------------------------------------------
# Sprint-related trigger tests
# ---------------------------------------------------------------------------


class TestSprintExpiryCheck:
    @pytest.mark.asyncio
    async def test_expired_sprint_triggers_action(self):
        """When active sprint is past end_date, an action is logged."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="SprintExpiry")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-01-01", end_date="2026-01-15")
        prod.update_sprint(slug, s["id"], status="active")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=True):
            result = await run_product_check(slug)

        action_str = " ".join(result.get("actions", []))
        assert "expired" in action_str.lower() or "Sprint" in action_str

    @pytest.mark.asyncio
    async def test_active_sprint_not_expired_no_action(self):
        """Active sprint within date range should not trigger expiry action."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="SprintCurrent")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-01-01", end_date="2099-12-31")
        prod.update_sprint(slug, s["id"], status="active")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=False):
            result = await run_product_check(slug)

        action_str = " ".join(result.get("actions", []))
        assert "expired" not in action_str.lower()

    @pytest.mark.asyncio
    async def test_invalid_end_date_no_crash(self):
        """Sprint with invalid end_date should not crash the health check."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="BadDate")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-01-01", end_date="not-a-date")
        prod.update_sprint(slug, s["id"], status="active")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=False):
            result = await run_product_check(slug)

        # Should not crash, and should not falsely report expiry
        assert not result.get("skipped")


class TestBacklogGrooming:
    @pytest.mark.asyncio
    async def test_grooming_reminder_when_threshold_reached(self):
        """5+ P2/P3 unscheduled issues triggers grooming action."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="GroomProd")
        slug = p["slug"]
        for i in range(5):
            _make_issue(slug, priority=IssuePriority.P2, title=f"Low {i}")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=True):
            result = await run_product_check(slug)

        action_str = " ".join(result.get("actions", []))
        assert "grooming" in action_str.lower() or "unscheduled" in action_str.lower()

    @pytest.mark.asyncio
    async def test_no_grooming_below_threshold(self):
        """Fewer than 5 P2/P3 unscheduled issues should not trigger grooming."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="NoGroom")
        slug = p["slug"]
        for i in range(4):
            _make_issue(slug, priority=IssuePriority.P3, title=f"Low {i}")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=False):
            result = await run_product_check(slug)

        action_str = " ".join(result.get("actions", []))
        assert "grooming" not in action_str.lower() and "unscheduled" not in action_str.lower()

    @pytest.mark.asyncio
    async def test_scheduled_issues_excluded(self):
        """Issues with sprint assigned should not count toward grooming threshold."""
        from onemancompany.core.product_triggers import run_product_check
        p = _make_product(name="ScheduledProd")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        # 5 P2 issues but all assigned to a sprint
        for i in range(5):
            _make_issue(slug, priority=IssuePriority.P2, title=f"Scheduled {i}", sprint=s["id"])

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers.notify_owner", new_callable=AsyncMock, return_value=False):
            result = await run_product_check(slug)

        action_str = " ".join(result.get("actions", []))
        assert "grooming" not in action_str.lower() and "unscheduled" not in action_str.lower()
