"""Tests for product management agent tools (B5)."""

import pytest

from onemancompany.core import product as prod


@pytest.fixture(autouse=True)
def _clean_products(tmp_path, monkeypatch):
    """Redirect PRODUCTS_DIR to tmp for isolation."""
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    yield


class TestUpdateProductTool:
    """Agent tool: update_product_tool wraps prod.update_product."""

    @pytest.mark.asyncio
    async def test_update_name_and_description(self):
        from onemancompany.agents.product_tools import update_product_tool

        p = prod.create_product(name="OrigName", owner_id="00010", description="Old desc")
        result = await update_product_tool.ainvoke(
            {"product_slug": p["slug"], "name": "NewName", "description": "New desc"}
        )
        assert "Updated" in result
        loaded = prod.load_product(p["slug"])
        assert loaded["name"] == "NewName"
        assert loaded["description"] == "New desc"

    @pytest.mark.asyncio
    async def test_update_nonexistent_product(self):
        from onemancompany.agents.product_tools import update_product_tool

        result = await update_product_tool.ainvoke({"product_slug": "nope", "name": "X"})
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_update_partial_fields(self):
        from onemancompany.agents.product_tools import update_product_tool

        p = prod.create_product(name="Partial", owner_id="00010", description="Keep me")
        result = await update_product_tool.ainvoke(
            {"product_slug": p["slug"], "name": "Changed"}
        )
        assert "Updated" in result
        loaded = prod.load_product(p["slug"])
        assert loaded["name"] == "Changed"
        assert loaded["description"] == "Keep me"


class TestDeleteProductTool:
    """Agent tool: delete_product_tool wraps prod.delete_product."""

    @pytest.mark.asyncio
    async def test_delete_existing_product(self):
        from onemancompany.agents.product_tools import delete_product_tool

        p = prod.create_product(name="ToDelete", owner_id="00010")
        result = await delete_product_tool.ainvoke({"product_slug": p["slug"]})
        assert "Deleted" in result
        assert prod.load_product(p["slug"]) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_product(self):
        from onemancompany.agents.product_tools import delete_product_tool

        result = await delete_product_tool.ainvoke({"product_slug": "ghost"})
        assert "error" in result.lower() or "not found" in result.lower()
