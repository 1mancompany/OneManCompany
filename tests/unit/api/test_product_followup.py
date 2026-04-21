"""Tests for product-owner routing in task_followup and product selector behavior.

Bug 1: task_followup always routes to EA even for product-linked projects.
        Should route to product owner instead.
Bug 2: CEO console product selector gives no visual feedback on change.
        (Frontend test — verified via app.js code inspection.)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.config import CEO_ID, EA_ID, TASK_TREE_FILENAME
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OWNER_ID = "00010"
PRODUCT_ID = "prod_abc"
PRODUCT_SLUG = "my-product"
PROJECT_ID = "proj_001"


def _make_project_doc(product_id: str = "") -> dict:
    return {
        "project_id": PROJECT_ID,
        "task": "Build feature X",
        "status": "completed",
        "product_id": product_id,
        "completed_at": "2026-01-01",
    }


def _make_tree_with_root(project_id: str = PROJECT_ID) -> TaskTree:
    """Create a minimal tree with CEO root + completed EA child."""
    tree = TaskTree(project_id=project_id)
    root = tree.create_root(employee_id=CEO_ID, description="Build feature X")
    root.node_type = NodeType.CEO_PROMPT
    root.set_status(TaskPhase.PROCESSING)
    ea_child = tree.add_child(
        parent_id=root.id,
        employee_id=EA_ID,
        description="Execute feature X",
        acceptance_criteria=[],
    )
    ea_child.set_status(TaskPhase.PROCESSING)
    ea_child.set_status(TaskPhase.COMPLETED)
    return tree


def _mock_product(product_id: str = PRODUCT_ID, owner_id: str = OWNER_ID) -> dict:
    return {
        "id": product_id,
        "slug": PRODUCT_SLUG,
        "name": "My Product",
        "owner_id": owner_id,
        "status": "active",
    }


# ---------------------------------------------------------------------------
# Bug 1: task_followup should route to product owner for product-linked projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_followup_routes_to_product_owner_when_product_linked(tmp_path):
    """When a project is linked to a product with an owner, the followup
    node should be assigned to the product owner, NOT the EA."""
    from onemancompany.api.routes import task_followup

    # Set up project dir with tree file present so tree_path.exists() is True
    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree = _make_tree_with_root()
    tree_path = pdir / TASK_TREE_FILENAME
    tree_path.write_text("{}")  # dummy content, get_tree is mocked

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()
    mock_em._schedule_next = MagicMock()

    mock_agent_loop = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel._save_project_tree"), \
         patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_agent_loop), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.core.project_archive._save_resolved"), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=PRODUCT_SLUG), \
         patch("onemancompany.core.product.load_product", return_value=_mock_product()):

        result = await task_followup(PROJECT_ID, {"instructions": "Update the KR progress"})

    assert result["status"] == "ok"

    # The key assertion: schedule_node should be called with the product OWNER,
    # not the EA.
    scheduled_employee_id = mock_em.schedule_node.call_args[0][0]
    assert scheduled_employee_id == OWNER_ID, (
        f"Expected followup to be routed to product owner {OWNER_ID}, "
        f"but was routed to {scheduled_employee_id}"
    )


@pytest.mark.asyncio
async def test_task_followup_routes_to_ea_when_no_product(tmp_path):
    """When a project is NOT linked to any product, followup should still
    route to EA as before."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree = _make_tree_with_root()
    tree_path = pdir / TASK_TREE_FILENAME
    tree_path.write_text("{}")  # dummy, get_tree is mocked

    project_doc = _make_project_doc(product_id="")  # no product

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()
    mock_em._schedule_next = MagicMock()

    mock_agent_loop = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel._save_project_tree"), \
         patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_agent_loop), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.core.project_archive._save_resolved"), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        result = await task_followup(PROJECT_ID, {"instructions": "Check status"})

    assert result["status"] == "ok"

    # Should route to EA when no product
    scheduled_employee_id = mock_em.schedule_node.call_args[0][0]
    assert scheduled_employee_id == EA_ID, (
        f"Expected followup to be routed to EA {EA_ID}, "
        f"but was routed to {scheduled_employee_id}"
    )


@pytest.mark.asyncio
async def test_task_followup_falls_back_to_ea_when_product_has_no_owner(tmp_path):
    """When project has product_id but product has no owner_id, fallback to EA."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree = _make_tree_with_root()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    no_owner_product = _mock_product()
    no_owner_product["owner_id"] = ""

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()
    mock_em._schedule_next = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel._save_project_tree"), \
         patch("onemancompany.core.agent_loop.get_agent_loop", return_value=MagicMock()), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.core.project_archive._save_resolved"), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=PRODUCT_SLUG), \
         patch("onemancompany.core.product.load_product", return_value=no_owner_product):

        result = await task_followup(PROJECT_ID, {"instructions": "Review progress"})

    assert result["status"] == "ok"
    assert mock_em.schedule_node.call_args[0][0] == EA_ID


@pytest.mark.asyncio
async def test_task_followup_falls_back_to_ea_when_product_slug_not_found(tmp_path):
    """When project has product_id but slug lookup returns None (deleted product), fallback to EA."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree = _make_tree_with_root()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()
    mock_em._schedule_next = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel._save_project_tree"), \
         patch("onemancompany.core.agent_loop.get_agent_loop", return_value=MagicMock()), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.core.project_archive._save_resolved"), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=None):

        result = await task_followup(PROJECT_ID, {"instructions": "Check KRs"})

    assert result["status"] == "ok"
    assert mock_em.schedule_node.call_args[0][0] == EA_ID
