"""Tests for product_tools — LangChain @tool wrappers over product.py CRUD."""

import pytest
import re
from onemancompany.core import product as prod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    prod.create_product(name="ToolTest", owner_id="00004", description="test product")
    yield


@pytest.fixture
def product_slug():
    return prod.list_products()[0]["slug"]


class TestProductTools:
    @pytest.mark.asyncio
    async def test_create_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import create_product_issue

        result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Performance bug",
                "description": "Page loads slowly",
                "priority": "P1",
            }
        )
        assert "issue_" in result
        assert "Performance bug" in result

    @pytest.mark.asyncio
    async def test_create_issue_with_labels(self, product_slug):
        from onemancompany.agents.product_tools import create_product_issue

        result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "CSS bug",
                "description": "broken layout",
                "priority": "P2",
                "labels": "frontend,css",
            }
        )
        assert "issue_" in result
        # Verify labels persisted
        issues = prod.list_issues(product_slug)
        css_issue = [i for i in issues if i["title"] == "CSS bug"][0]
        assert "frontend" in css_issue["labels"]

    @pytest.mark.asyncio
    async def test_update_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            update_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Bug",
                "description": "desc",
                "priority": "P2",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "status": "in_progress",
            }
        )
        assert "in_progress" in result

    @pytest.mark.asyncio
    async def test_close_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            close_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Fix me",
                "description": "broken",
                "priority": "P1",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await close_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "resolution": "fixed",
            }
        )
        assert "closed" in result.lower()

    @pytest.mark.asyncio
    async def test_close_issue_invalid_resolution(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            close_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Bug2",
                "description": "d",
                "priority": "P1",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await close_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "resolution": "invalid_resolution",
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_get_product_context_tool(self, product_slug):
        from onemancompany.agents.product_tools import get_product_context_tool

        result = await get_product_context_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "ToolTest" in result

    @pytest.mark.asyncio
    async def test_get_product_context_not_found(self):
        from onemancompany.agents.product_tools import get_product_context_tool

        result = await get_product_context_tool.ainvoke(
            {"product_slug": "nonexistent-slug"}
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_issues_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            list_product_issues_tool,
        )

        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Issue A",
                "description": "a",
                "priority": "P0",
            }
        )
        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "Issue A" in result

    @pytest.mark.asyncio
    async def test_list_issues_empty(self, product_slug):
        from onemancompany.agents.product_tools import list_product_issues_tool

        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug, "status": "closed"}
        )
        assert "no issues" in result.lower()

    @pytest.mark.asyncio
    async def test_update_kr_tool(self, product_slug):
        from onemancompany.agents.product_tools import update_kr_progress_tool

        kr = prod.add_key_result(product_slug, title="DAU", target=1000)
        result = await update_kr_progress_tool.ainvoke(
            {
                "product_slug": product_slug,
                "kr_id": kr["id"],
                "current_value": 500,
            }
        )
        assert "500" in result

    @pytest.mark.asyncio
    async def test_update_kr_not_found(self, product_slug):
        from onemancompany.agents.product_tools import update_kr_progress_tool

        result = await update_kr_progress_tool.ainvoke(
            {
                "product_slug": product_slug,
                "kr_id": "kr_nonexistent",
                "current_value": 100,
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_product_tools_list(self):
        from onemancompany.agents.product_tools import PRODUCT_TOOLS

        assert len(PRODUCT_TOOLS) == 6
        names = {t.name for t in PRODUCT_TOOLS}
        assert "create_product_issue" in names
        assert "update_product_issue" in names
        assert "close_product_issue" in names
        assert "get_product_context_tool" in names
        assert "list_product_issues_tool" in names
        assert "update_kr_progress_tool" in names
