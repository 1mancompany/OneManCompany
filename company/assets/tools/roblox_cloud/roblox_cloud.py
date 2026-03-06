"""Roblox Open Cloud tool — pure HTTP + OAuth, no SDK dependency.

Provides LangChain @tool functions for DataStore, MessagingService,
Place management, and experience info via Roblox Open Cloud REST API.

Auth: Uses roblox_oauth.get_auth_header() which tries OAuth first,
then falls back to ROBLOX_CLOUD_API_KEY env var.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlencode, quote

from langchain_core.tools import tool

# ── API base URLs ────────────────────────────────────────

_CLOUD_V2 = "https://apis.roblox.com/cloud/v2"
_DATASTORES_V1 = "https://apis.roblox.com/datastores/v1"
_MESSAGING_V1 = "https://apis.roblox.com/messaging-service/v1"
_PUBLISH_V1 = "https://apis.roblox.com/universes/v1"


# ── Helpers ──────────────────────────────────────────────

def _roblox_oauth_config():
    """Lazy-load Roblox OAuth config from core.oauth."""
    try:
        from onemancompany.core.oauth import OAuthServiceConfig
        return OAuthServiceConfig(
            service_name="roblox",
            authorize_url="https://apis.roblox.com/oauth/v1/authorize",
            token_url="https://apis.roblox.com/oauth/v1/token",
            scopes="openid universe-places:write",
            client_id_env="ROBLOX_OAUTH_CLIENT_ID",
            client_secret_env="ROBLOX_OAUTH_CLIENT_SECRET",
        )
    except ImportError:
        return None


def _get_auth_and_universe() -> tuple[dict, int, str | None]:
    """Get auth header and universe_id from employee context or env.

    Returns (auth_header, universe_id, error_message).
    Auth priority: OAuth token > API key env var.
    If no auth exists and OAuth credentials are configured, triggers popup for CEO.
    """
    auth_header = {}

    # Try OAuth first
    config = _roblox_oauth_config()
    if config:
        from onemancompany.core.oauth import ensure_oauth_token
        token = ensure_oauth_token(config)
        if token:
            auth_header = {"Authorization": f"Bearer {token}"}

    # Fallback to API key
    if not auth_header:
        api_key = os.environ.get("ROBLOX_CLOUD_API_KEY", "")
        if api_key:
            auth_header = {"x-api-key": api_key}

    if not auth_header:
        # If OAuth credentials aren't configured, request them from CEO
        if config and not os.environ.get(config.client_id_env):
            from onemancompany.core.oauth import request_credentials
            request_credentials(
                service_name="roblox",
                title="Roblox API Credentials Required",
                message="Enter your Roblox API Key or OAuth credentials.\n"
                        "Get an API key at: https://create.roblox.com/dashboard/credentials",
                fields=[
                    {"name": "cloud_api_key", "label": "API Key", "secret": True,
                     "placeholder": "Paste your Open Cloud API key"},
                    {"name": "oauth_client_id", "label": "OAuth Client ID (optional)",
                     "placeholder": "For OAuth — leave blank to use API key only"},
                    {"name": "oauth_client_secret", "label": "OAuth Client Secret (optional)",
                     "secret": True, "placeholder": "For OAuth — leave blank to use API key only"},
                ],
            )
            return {}, 0, "Roblox credentials required — popup sent to CEO. Please retry after submitting."
        if config and os.environ.get(config.client_id_env):
            return {}, 0, "OAuth authorization required — popup sent to CEO. Please retry after authorizing."
        return {}, 0, "No auth available. Set ROBLOX_CLOUD_API_KEY or configure OAuth."

    # Get universe_id: try employee profile, then env
    universe_id = 0
    try:
        from onemancompany.core.agent_loop import _current_loop
        loop = _current_loop.get(None)
        if loop:
            from onemancompany.core.config import EMPLOYEES_DIR
            import yaml
            profile_path = EMPLOYEES_DIR / loop.agent.employee_id / "profile.yaml"
            if profile_path.exists():
                with open(profile_path) as f:
                    profile = yaml.safe_load(f) or {}
                universe_id = profile.get("roblox_universe_id", 0)
    except Exception:
        pass

    if not universe_id:
        universe_id = int(os.environ.get("ROBLOX_UNIVERSE_ID", "0"))

    if not universe_id:
        return auth_header, 0, (
            "No roblox_universe_id configured. Set ROBLOX_UNIVERSE_ID env var "
            "or add roblox_universe_id to employee profile.yaml."
        )

    return auth_header, universe_id, None


def _api_request(
    method: str, url: str, headers: dict,
    body: bytes | None = None, timeout: int = 15,
) -> tuple[int, dict | str]:
    """Make an HTTP request, return (status_code, parsed_body)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return e.code, {"raw": raw[:2000]}
    except Exception as e:
        return 0, {"error": str(e)}


def _get(url: str, headers: dict, params: dict | None = None) -> tuple[int, dict | str]:
    if params:
        url = f"{url}?{urlencode(params)}"
    return _api_request("GET", url, headers)


def _post(url: str, headers: dict, body: dict | bytes | None = None,
          content_type: str = "application/json") -> tuple[int, dict | str]:
    hdrs = {**headers, "Content-Type": content_type}
    if isinstance(body, dict):
        data = json.dumps(body).encode()
    elif isinstance(body, bytes):
        data = body
    else:
        data = None
    return _api_request("POST", url, hdrs, data)


def _delete(url: str, headers: dict) -> tuple[int, dict | str]:
    return _api_request("DELETE", url, headers)


# ── Experience info ──────────────────────────────────────

@tool
def roblox_get_experience_info() -> dict:
    """Get information about the Roblox experience (universe).

    Returns name, description, visibility, age rating, creation/update times.
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    status, body = _get(f"{_CLOUD_V2}/universes/{uid}", auth)
    if status == 200:
        return {"status": "ok", **body}
    return {"status": "error", "http": status, "detail": body}


@tool
def roblox_get_place_info(place_id: str) -> dict:
    """Get information about a specific place in the experience.

    Args:
        place_id: The place ID to query.
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    status, body = _get(f"{_CLOUD_V2}/universes/{uid}/places/{place_id}", auth)
    if status == 200:
        return {"status": "ok", **body}
    return {"status": "error", "http": status, "detail": body}


# ── DataStore ────────────────────────────────────────────

@tool
def roblox_list_datastores(limit: int = 20) -> dict:
    """List all DataStores in the experience.

    Args:
        limit: Max number of DataStores to return (default: 20).
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_DATASTORES_V1}/universes/{uid}/standard-datastores"
    status, body = _get(url, auth, {"limit": limit})
    if status == 200:
        stores = [ds.get("name", "") for ds in body.get("datastores", [])]
        return {"status": "ok", "datastores": stores, "count": len(stores)}
    return {"status": "error", "http": status, "detail": body}


@tool
def roblox_datastore_get(datastore_name: str, key: str, scope: str = "global") -> dict:
    """Read a value from a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        key: The key to look up.
        scope: DataStore scope (default: "global").
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_DATASTORES_V1}/universes/{uid}/standard-datastores/datastore/entries/entry"
    params = {"datastoreName": datastore_name, "entryKey": key, "scope": scope}
    status, body = _get(url, auth, params)
    if status == 200:
        return {"status": "ok", "key": key, "value": body}
    return {"status": "error", "http": status, "detail": body}


@tool
def roblox_datastore_set(
    datastore_name: str, key: str, value: str, scope: str = "global"
) -> dict:
    """Write a value to a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        key: The key to set.
        value: JSON string of the value to store.
        scope: DataStore scope (default: "global").
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_DATASTORES_V1}/universes/{uid}/standard-datastores/datastore/entries/entry"
    params = {"datastoreName": datastore_name, "entryKey": key, "scope": scope}
    full_url = f"{url}?{urlencode(params)}"

    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        data = value

    status, body = _post(full_url, auth, body=json.dumps(data).encode(),
                         content_type="application/json")
    if status == 200:
        return {"status": "ok", "key": key}
    return {"status": "error", "http": status, "detail": body}


@tool
def roblox_datastore_list_keys(
    datastore_name: str, scope: str = "global", limit: int = 20
) -> dict:
    """List keys in a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        scope: DataStore scope (default: "global").
        limit: Max number of keys to return (default: 20).
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_DATASTORES_V1}/universes/{uid}/standard-datastores/datastore/entries"
    params = {"datastoreName": datastore_name, "scope": scope, "limit": limit}
    status, body = _get(url, auth, params)
    if status == 200:
        keys = [k.get("key", "") for k in body.get("keys", [])]
        return {"status": "ok", "keys": keys, "count": len(keys)}
    return {"status": "error", "http": status, "detail": body}


@tool
def roblox_datastore_delete(
    datastore_name: str, key: str, scope: str = "global"
) -> dict:
    """Delete a key from a Roblox DataStore.

    Args:
        datastore_name: Name of the DataStore.
        key: The key to delete.
        scope: DataStore scope (default: "global").
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_DATASTORES_V1}/universes/{uid}/standard-datastores/datastore/entries/entry"
    params = {"datastoreName": datastore_name, "entryKey": key, "scope": scope}
    full_url = f"{url}?{urlencode(params)}"
    status, body = _delete(full_url, auth)
    if status in (200, 204):
        return {"status": "ok", "key": key, "deleted": True}
    return {"status": "error", "http": status, "detail": body}


# ── Messaging ────────────────────────────────────────────

@tool
def roblox_publish_message(topic: str, message: str) -> dict:
    """Publish a message to live game servers via MessagingService.

    Sends to ALL running servers subscribed to the topic.
    Useful for: announcements, config hot-reload, event triggers.

    Args:
        topic: The topic name to publish to.
        message: The message content (max 1KB).
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    url = f"{_MESSAGING_V1}/universes/{uid}/topics/{quote(topic, safe='')}"
    status, body = _post(url, auth, body={"message": message})
    if status == 200:
        return {"status": "ok", "topic": topic, "message_length": len(message)}
    return {"status": "error", "http": status, "detail": body}


# ── Place publishing ─────────────────────────────────────

@tool
def roblox_publish_place(place_id: str, rbxl_file_path: str) -> dict:
    """Publish a .rbxl/.rbxlx place file to Roblox.

    Args:
        place_id: The place ID to publish to.
        rbxl_file_path: Absolute path to the .rbxl or .rbxlx file.
    """
    auth, uid, err = _get_auth_and_universe()
    if err:
        return {"status": "error", "message": err}

    file_path = Path(rbxl_file_path)
    if not file_path.exists():
        return {"status": "error", "message": f"File not found: {rbxl_file_path}"}

    is_xml = file_path.suffix.lower() == ".rbxlx"
    content_type = "application/xml" if is_xml else "application/octet-stream"

    url = (f"{_PUBLISH_V1}/{uid}/places/{place_id}/versions"
           f"?versionType=Published")

    file_data = file_path.read_bytes()
    status, body = _post(url, auth, body=file_data, content_type=content_type)
    if status == 200:
        return {"status": "ok", "place_id": place_id, "version": body.get("versionNumber", "")}
    return {"status": "error", "http": status, "detail": body}


# ── Convenience: list all tool functions ─────────────────

ALL_TOOLS = [
    roblox_get_experience_info,
    roblox_get_place_info,
    roblox_list_datastores,
    roblox_datastore_get,
    roblox_datastore_set,
    roblox_datastore_list_keys,
    roblox_datastore_delete,
    roblox_publish_message,
    roblox_publish_place,
]
