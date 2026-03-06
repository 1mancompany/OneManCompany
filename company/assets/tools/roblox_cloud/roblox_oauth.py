"""Roblox OAuth 2.0 helper — token management with auto-refresh.

Usage:
    # One-time setup (opens browser for authorization):
    python roblox_oauth.py setup --client-id YOUR_ID --client-secret YOUR_SECRET

    # As a library (auto-refreshes tokens):
    from roblox_oauth import get_access_token
    token = get_access_token()  # always returns a valid token
"""
from __future__ import annotations

import json
import os
import sys
import time
import hashlib
import base64
import secrets
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

# Try requests, fall back to urllib
try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

# ── Config ────────────────────────────────────────────────

TOKEN_ENDPOINT = "https://apis.roblox.com/oauth/v1/token"
AUTHORIZE_ENDPOINT = "https://apis.roblox.com/oauth/v1/authorize"
DEFAULT_REDIRECT_URI = "http://localhost:8585/callback"
DEFAULT_SCOPES = "openid universe-places:write"

# Token cache file — stored in company/assets/tools/roblox_cloud/ so all projects share it
_TOKEN_CACHE = Path(__file__).parent / ".roblox_tokens.json"
# Also add to .gitignore pattern (contains secrets)


# ── HTTP helper ───────────────────────────────────────────

def _post_form(url: str, data: dict, timeout: int = 30) -> tuple[int, dict]:
    """POST form-encoded data, returns (status, body_dict)."""
    if HAS_REQUESTS:
        resp = _requests.post(url, data=data, timeout=timeout)
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"raw": resp.text[:2000]}
    else:
        encoded = urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=encoded, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                try:
                    return resp.status, json.loads(raw)
                except Exception:
                    return resp.status, {"raw": raw[:2000]}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:2000]}


# ── PKCE helpers ──────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Token cache ───────────────────────────────────────────

def _load_cache() -> dict:
    if _TOKEN_CACHE.exists():
        try:
            return json.loads(_TOKEN_CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(data: dict) -> None:
    _TOKEN_CACHE.write_text(json.dumps(data, indent=2))


def _env_credentials() -> tuple[str, str]:
    """Read client_id and client_secret from environment."""
    client_id = os.environ.get("ROBLOX_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("ROBLOX_OAUTH_CLIENT_SECRET", "")
    return client_id, client_secret


# ── Token exchange & refresh ──────────────────────────────

def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    code_verifier: str = "",
) -> dict:
    """Exchange authorization code for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    status, body = _post_form(TOKEN_ENDPOINT, data)
    if status != 200:
        return {"error": f"Token exchange failed (HTTP {status})", "detail": body}

    # Save tokens
    cache = {
        "access_token": body.get("access_token", ""),
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + body.get("expires_in", 900) - 60,  # 1 min buffer
        "scope": body.get("scope", ""),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    _save_cache(cache)
    return cache


def refresh_access_token(
    refresh_token: str = "",
    client_id: str = "",
    client_secret: str = "",
) -> dict:
    """Use refresh_token to get a new access_token."""
    cache = _load_cache()
    refresh_token = refresh_token or cache.get("refresh_token", "")
    client_id = client_id or cache.get("client_id", "") or os.environ.get("ROBLOX_OAUTH_CLIENT_ID", "")
    client_secret = client_secret or cache.get("client_secret", "") or os.environ.get("ROBLOX_OAUTH_CLIENT_SECRET", "")

    if not refresh_token:
        return {"error": "No refresh_token available. Run setup first."}
    if not client_id or not client_secret:
        return {"error": "Missing client_id or client_secret."}

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    status, body = _post_form(TOKEN_ENDPOINT, data)
    if status != 200:
        return {"error": f"Token refresh failed (HTTP {status})", "detail": body}

    cache.update({
        "access_token": body.get("access_token", ""),
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + body.get("expires_in", 900) - 60,
        "scope": body.get("scope", cache.get("scope", "")),
        "client_id": client_id,
        "client_secret": client_secret,
    })
    _save_cache(cache)
    return cache


def get_access_token() -> str | None:
    """Get a valid access token, auto-refreshing if needed.

    Returns the access_token string, or None if unavailable.
    Falls back to ROBLOX_CLOUD_API_KEY env var if no OAuth tokens exist.
    """
    cache = _load_cache()

    # If we have a valid access token, use it
    if cache.get("access_token") and cache.get("expires_at", 0) > time.time():
        return cache["access_token"]

    # Try to refresh
    if cache.get("refresh_token"):
        result = refresh_access_token()
        if "error" not in result and result.get("access_token"):
            return result["access_token"]

    # Fall back to API key
    api_key = os.environ.get("ROBLOX_CLOUD_API_KEY", "")
    if api_key:
        return api_key

    return None


def get_auth_header() -> dict:
    """Get the appropriate auth header for Roblox API calls.

    Returns {"Authorization": "Bearer ..."} for OAuth,
    or {"x-api-key": "..."} for API key fallback.
    """
    cache = _load_cache()

    # Try OAuth first
    if cache.get("access_token") and cache.get("expires_at", 0) > time.time():
        return {"Authorization": f"Bearer {cache['access_token']}"}

    if cache.get("refresh_token"):
        result = refresh_access_token()
        if "error" not in result and result.get("access_token"):
            return {"Authorization": f"Bearer {result['access_token']}"}

    # Fall back to API key
    api_key = os.environ.get("ROBLOX_CLOUD_API_KEY", "")
    if api_key:
        return {"x-api-key": api_key}

    return {}


# ── One-time browser-based setup ──────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""
    auth_code: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization successful!</h2>"
                             b"<p>You can close this tab and return to the terminal.</p>")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Authorization failed: {error}</h2>".encode())

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def setup_oauth(
    client_id: str,
    client_secret: str,
    scopes: str = DEFAULT_SCOPES,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> dict:
    """Run the full OAuth setup: open browser, capture code, exchange for tokens."""
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTHORIZE_ENDPOINT}?{urlencode(auth_params)}"

    # Parse redirect URI for local server
    parsed = urlparse(redirect_uri)
    port = parsed.port or 8585

    # Start local server
    server = HTTPServer(("localhost", port), _CallbackHandler)
    server.timeout = 120  # 2 minute timeout

    print(f"\nOpening browser for Roblox authorization...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for authorization callback...")
    # Handle requests until we get the code or timeout
    _CallbackHandler.auth_code = None
    deadline = time.time() + 120
    while _CallbackHandler.auth_code is None and time.time() < deadline:
        server.handle_request()

    server.server_close()

    if not _CallbackHandler.auth_code:
        return {"error": "Authorization timed out (2 minutes)"}

    print(f"Got authorization code, exchanging for tokens...")
    result = exchange_code(
        code=_CallbackHandler.auth_code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
    )

    if "error" not in result:
        print(f"\nOAuth setup complete!")
        print(f"  Access token: {result.get('access_token', '')[:20]}...")
        print(f"  Refresh token saved to: {_TOKEN_CACHE}")
        print(f"  Token valid for ~15 min, auto-refreshes for 90 days")
    else:
        print(f"\nSetup failed: {result}")

    return result


# ── CLI ───────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Roblox OAuth 2.0 token manager")
    sub = parser.add_subparsers(dest="command")

    # Setup command
    setup_p = sub.add_parser("setup", help="One-time browser authorization")
    setup_p.add_argument("--client-id", required=True, help="OAuth app client ID")
    setup_p.add_argument("--client-secret", required=True, help="OAuth app client secret")
    setup_p.add_argument("--scopes", default=DEFAULT_SCOPES)
    setup_p.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)

    # Refresh command
    sub.add_parser("refresh", help="Manually refresh access token")

    # Status command
    sub.add_parser("status", help="Show current token status")

    args = parser.parse_args()

    if args.command == "setup":
        setup_oauth(args.client_id, args.client_secret, args.scopes, args.redirect_uri)
    elif args.command == "refresh":
        result = refresh_access_token()
        if "error" in result:
            print(f"Refresh failed: {result['error']}")
        else:
            print(f"Refreshed! Token valid until {time.ctime(result['expires_at'])}")
    elif args.command == "status":
        cache = _load_cache()
        if not cache:
            print("No tokens cached. Run 'setup' first.")
        else:
            exp = cache.get("expires_at", 0)
            valid = exp > time.time()
            print(f"Access token: {'valid' if valid else 'expired'}")
            if valid:
                print(f"  Expires in: {(exp - time.time()) / 60:.1f} minutes")
            print(f"Refresh token: {'present' if cache.get('refresh_token') else 'missing'}")
            print(f"Scope: {cache.get('scope', 'unknown')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
