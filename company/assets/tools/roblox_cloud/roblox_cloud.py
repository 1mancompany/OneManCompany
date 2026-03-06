"""Roblox Open Cloud tool — interact with Roblox game services via Open Cloud API.

Uses the `rblx-open-cloud` Python SDK (pip install rblx-open-cloud~=2.0).
Provides LangChain @tool functions for DataStore, MessagingService,
Place management, and asset operations.

API Key should be configured in the employee's profile or connection settings.
Create an API key at: https://create.roblox.com/dashboard/credentials
"""

from __future__ import annotations

import json

from langchain_core.tools import tool


def _get_roblox_client():
    """Get rblx-open-cloud Experience client from employee context."""
    try:
        import rblxopencloud
    except ImportError:
        return None, "rblx-open-cloud not installed. Run: pip install rblx-open-cloud~=2.0"

    from onemancompany.core.agent_loop import _current_loop

    loop = _current_loop.get(None)
    if not loop:
        return None, "No agent context — cannot determine Roblox config."

    employee_id = loop.agent.employee_id

    from onemancompany.core.config import EMPLOYEES_DIR
    import yaml

    # Read roblox config from employee's profile or connection
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if not profile_path.exists():
        return None, f"No profile found for employee {employee_id}"

    with open(profile_path) as f:
        profile = yaml.safe_load(f) or {}

    api_key = profile.get("roblox_api_key", "")
    universe_id = profile.get("roblox_universe_id", 0)

    if not api_key:
        # Try connection.json
        conn_path = EMPLOYEES_DIR / employee_id / "connection.json"
        if conn_path.exists():
            conn = json.loads(conn_path.read_text())
            api_key = conn.get("roblox_api_key", "")
            universe_id = universe_id or conn.get("roblox_universe_id", 0)

    if not api_key:
        # Fallback: read from environment variable (.env)
        import os
        api_key = os.environ.get("ROBLOX_CLOUD_API_KEY", "")

    if not api_key:
        return None, (
            "No roblox_api_key configured. Add it to profile.yaml or connection.json. "
            "Create one at https://create.roblox.com/dashboard/credentials"
        )
    if not universe_id:
        return None, (
            "No roblox_universe_id configured. Add it to profile.yaml or connection.json. "
            "Find it in Roblox Studio → Game Settings → Security."
        )

    experience = rblxopencloud.Experience(universe_id, api_key=api_key)
    return experience, None


# ---------------------------------------------------------------------------
# DataStore tools
# ---------------------------------------------------------------------------

@tool
def roblox_datastore_get(datastore_name: str, key: str, scope: str = "global") -> dict:
    """Read a value from a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        key: The key to look up.
        scope: DataStore scope (default: "global").

    Returns:
        Dict with status, value, and metadata (userIds, attributes).
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        datastore = experience.get_data_store(datastore_name, scope=scope)
        value, info = datastore.get(key)
        return {
            "status": "ok",
            "key": key,
            "value": value,
            "version": info.version if hasattr(info, "version") else "",
            "users": info.users if hasattr(info, "users") else [],
            "metadata": info.metadata if hasattr(info, "metadata") else {},
        }
    except Exception as e:
        return {"status": "error", "message": f"DataStore get failed: {e}"}


@tool
def roblox_datastore_set(
    datastore_name: str, key: str, value: str, scope: str = "global"
) -> dict:
    """Write a value to a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        key: The key to set.
        value: JSON string of the value to store. Will be parsed as JSON.
        scope: DataStore scope (default: "global").

    Returns:
        Dict with status and version info.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError:
        parsed_value = value  # store as string if not valid JSON

    try:
        datastore = experience.get_data_store(datastore_name, scope=scope)
        version = datastore.set(key, parsed_value)
        return {
            "status": "ok",
            "key": key,
            "version": str(version) if version else "",
        }
    except Exception as e:
        return {"status": "error", "message": f"DataStore set failed: {e}"}


@tool
def roblox_datastore_list_keys(
    datastore_name: str, scope: str = "global", limit: int = 20
) -> dict:
    """List keys in a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        scope: DataStore scope (default: "global").
        limit: Max number of keys to return (default: 20).

    Returns:
        Dict with status and list of keys.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        datastore = experience.get_data_store(datastore_name, scope=scope)
        keys = []
        for key in datastore.list_keys():
            keys.append(str(key))
            if len(keys) >= limit:
                break
        return {"status": "ok", "keys": keys, "count": len(keys)}
    except Exception as e:
        return {"status": "error", "message": f"DataStore list keys failed: {e}"}


@tool
def roblox_list_datastores(limit: int = 20) -> dict:
    """List all DataStores in the Roblox experience.

    Args:
        limit: Max number of DataStores to return (default: 20).

    Returns:
        Dict with status and list of DataStore names.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        stores = []
        for store in experience.list_data_stores():
            stores.append(str(store))
            if len(stores) >= limit:
                break
        return {"status": "ok", "datastores": stores, "count": len(stores)}
    except Exception as e:
        return {"status": "error", "message": f"List DataStores failed: {e}"}


# ---------------------------------------------------------------------------
# Messaging tools
# ---------------------------------------------------------------------------

@tool
def roblox_publish_message(topic: str, message: str) -> dict:
    """Publish a message to live game servers via MessagingService.

    This sends a message to ALL running game servers subscribed to the topic.
    Useful for: server announcements, config hot-reload, event triggers.

    Args:
        topic: The topic name to publish to.
        message: The message content (max 1KB).

    Returns:
        Dict with status.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        experience.publish_message(topic, message)
        return {"status": "ok", "topic": topic, "message_length": len(message)}
    except Exception as e:
        return {"status": "error", "message": f"Publish message failed: {e}"}


# ---------------------------------------------------------------------------
# Place management tools
# ---------------------------------------------------------------------------

@tool
def roblox_restart_servers() -> dict:
    """Restart all game servers (rolling restart).

    Shuts down all running servers so players rejoin fresh instances.
    Use after publishing a new version or fixing a critical bug.

    Returns:
        Dict with status.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        experience.restart_servers()
        return {"status": "ok", "message": "Server restart initiated"}
    except Exception as e:
        return {"status": "error", "message": f"Server restart failed: {e}"}


# ---------------------------------------------------------------------------
# Experience info tools
# ---------------------------------------------------------------------------

@tool
def roblox_get_experience_info() -> dict:
    """Get information about the Roblox experience (game).

    Returns:
        Dict with experience name, description, playing count, visits, etc.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        info = experience.fetch_info()
        return {
            "status": "ok",
            "name": getattr(info, "name", ""),
            "description": getattr(info, "description", ""),
            "playing": getattr(info, "playing", 0),
            "visits": getattr(info, "visits", 0),
            "max_players": getattr(info, "max_players", 0),
            "created": str(getattr(info, "created", "")),
            "updated": str(getattr(info, "updated", "")),
        }
    except Exception as e:
        return {"status": "error", "message": f"Fetch experience info failed: {e}"}


# ---------------------------------------------------------------------------
# Monetization tools
# ---------------------------------------------------------------------------

@tool
def roblox_list_game_passes(limit: int = 20) -> dict:
    """List all Game Passes for the experience.

    Args:
        limit: Max number of game passes to return.

    Returns:
        Dict with status and list of game passes (id, name, price).
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        passes = []
        for gp in experience.list_game_passes():
            passes.append({
                "id": getattr(gp, "id", ""),
                "name": getattr(gp, "name", ""),
                "price": getattr(gp, "price", 0),
            })
            if len(passes) >= limit:
                break
        return {"status": "ok", "game_passes": passes, "count": len(passes)}
    except Exception as e:
        return {"status": "error", "message": f"List game passes failed: {e}"}


@tool
def roblox_list_developer_products(limit: int = 20) -> dict:
    """List all Developer Products for the experience.

    Args:
        limit: Max number of developer products to return.

    Returns:
        Dict with status and list of developer products.
    """
    experience, err = _get_roblox_client()
    if err:
        return {"status": "error", "message": err}

    try:
        products = []
        for dp in experience.list_developer_products():
            products.append({
                "id": getattr(dp, "id", ""),
                "name": getattr(dp, "name", ""),
                "price": getattr(dp, "price", 0),
            })
            if len(products) >= limit:
                break
        return {"status": "ok", "developer_products": products, "count": len(products)}
    except Exception as e:
        return {"status": "error", "message": f"List developer products failed: {e}"}
