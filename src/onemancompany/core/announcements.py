"""Fetch announcements from GitHub Discussions (Announcements category).

Polls the GitHub API periodically and pushes new announcements to the frontend
via a REST endpoint. No auth required — public repo, public API.
"""

from __future__ import annotations

import httpx
from loguru import logger

REPO_OWNER = "1mancompany"
REPO_NAME = "OneManCompany"
DISCUSSIONS_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/discussions"
ANNOUNCEMENTS_CATEGORY = "announcements"
CHECK_INTERVAL_SECONDS = 3600  # 1 hour


async def fetch_announcements(since: str = "") -> list[dict]:
    """Fetch discussions from the Announcements category.

    Args:
        since: ISO 8601 timestamp. Only return discussions created after this time.

    Returns:
        List of announcement dicts: {id, title, body, url, created_at, author}
    """
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(DISCUSSIONS_URL, headers=headers, params={"per_page": 20})
            if resp.status_code != 200:
                logger.debug("[announcements] GitHub API returned {}: {}", resp.status_code, resp.text[:200])
                return []
            discussions = resp.json()

        announcements = []
        for d in discussions:
            # Filter by Announcements category
            cat = d.get("category", {})
            if cat.get("slug", "").lower() != ANNOUNCEMENTS_CATEGORY:
                continue
            created = d.get("created_at", "")
            # Filter by since timestamp
            if since and created <= since:
                continue
            announcements.append({
                "id": d.get("number", 0),
                "title": d.get("title", ""),
                "body": d.get("body", ""),
                "url": d.get("html_url", ""),
                "created_at": created,
                "author": d.get("user", {}).get("login", ""),
            })

        announcements.sort(key=lambda a: a["created_at"], reverse=True)
        logger.debug("[announcements] Fetched {} announcements (since={})", len(announcements), since or "all")
        return announcements

    except Exception as exc:
        logger.debug("[announcements] Failed to fetch: {}", exc)
        return []
