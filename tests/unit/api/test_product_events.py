"""Tests that product API routes publish the correct events to event_bus.

Batch 1 audit fix: Sprint and review routes were not publishing events,
which broke the entire trigger→review→activity pipeline.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from onemancompany.core.models import EventType


def _make_test_app() -> FastAPI:
    from onemancompany.api.routes import router
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Sprint event publishing
# ---------------------------------------------------------------------------


class TestSprintEventPublishing:
    """Sprint routes must publish SPRINT_CREATED / SPRINT_CLOSED events."""

    @pytest.mark.asyncio
    async def test_create_sprint_publishes_event(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        fake_sprint = {"id": "sprint_abc", "name": "Sprint 1", "status": "planning"}
        fake_product = {"id": "prod_1", "name": "Test", "slug": "test"}

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.load_product", return_value=fake_product), \
             patch("onemancompany.core.product.create_sprint", return_value=fake_sprint):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/product/test/sprint", json={
                    "name": "Sprint 1",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-14",
                })

        assert resp.status_code == 200
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == EventType.SPRINT_CREATED
        assert event.payload["product_slug"] == "test"
        assert event.payload["sprint_id"] == "sprint_abc"

    @pytest.mark.asyncio
    async def test_close_sprint_publishes_event(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        fake_result = {"id": "sprint_abc", "status": "closed", "velocity": 21}

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.close_sprint", return_value=fake_result):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/product/test/sprint/sprint_abc/close")

        assert resp.status_code == 200
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == EventType.SPRINT_CLOSED
        assert event.payload["product_slug"] == "test"
        assert event.payload["sprint_id"] == "sprint_abc"

    @pytest.mark.asyncio
    async def test_start_sprint_publishes_event(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        fake_result = {"id": "sprint_abc", "status": "active"}

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.start_sprint", return_value=fake_result):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/product/test/sprint/sprint_abc/start")

        assert resp.status_code == 200
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == EventType.SPRINT_STARTED
        assert event.payload["product_slug"] == "test"
        assert event.payload["sprint_id"] == "sprint_abc"

    @pytest.mark.asyncio
    async def test_delete_sprint_publishes_no_event(self):
        """Delete sprint should NOT publish an event (no lifecycle significance)."""
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.delete_sprint", return_value=None):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/product/test/sprint/sprint_abc")

        assert resp.status_code == 200
        mock_bus.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Review event publishing
# ---------------------------------------------------------------------------


class TestReviewEventPublishing:
    """Review routes must publish REVIEW_CREATED / REVIEW_COMPLETED events."""

    @pytest.mark.asyncio
    async def test_create_review_publishes_event(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        fake_review = {"id": "rev_abc", "product_slug": "test", "status": "open"}

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.create_review", return_value=fake_review):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/product/test/review", json={
                    "trigger": "manual",
                    "owner": "00010",
                })

        assert resp.status_code == 200
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == EventType.REVIEW_CREATED
        assert event.payload["product_slug"] == "test"
        assert event.payload["review_id"] == "rev_abc"

    @pytest.mark.asyncio
    async def test_complete_review_publishes_event(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        mock_bus = MagicMock(publish=AsyncMock())

        fake_review = {"id": "rev_abc", "status": "completed"}

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.product.complete_review", return_value=fake_review):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/product/test/review/rev_abc/complete")

        assert resp.status_code == 200
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == EventType.REVIEW_COMPLETED
        assert event.payload["product_slug"] == "test"
        assert event.payload["review_id"] == "rev_abc"
