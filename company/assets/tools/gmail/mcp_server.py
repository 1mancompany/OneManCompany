"""Gmail MCP server — exposes Gmail tools to self-hosted Claude CLI employees.

Runs as a stdio subprocess. Reuses the same OAuth + HTTP logic from gmail.py.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP

# ── Gmail API base ─────────────────────────────────────

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

mcp = FastMCP("gmail")


# ── OAuth ──────────────────────────────────────────────

def _get_auth_header() -> tuple[dict, str | None]:
    """Get OAuth auth header for Gmail API."""
    try:
        from onemancompany.core.oauth import OAuthServiceConfig, ensure_oauth_token
    except ImportError:
        return {}, "OAuth module not available"

    config = OAuthServiceConfig(
        service_name="gmail",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes="https://www.googleapis.com/auth/gmail.modify",
        client_id_env="GOOGLE_OAUTH_CLIENT_ID",
        client_secret_env="GOOGLE_OAUTH_CLIENT_SECRET",
    )
    token = ensure_oauth_token(config)
    if token is None:
        return {}, "Gmail OAuth authorization required. A popup has been sent to CEO."

    return {"Authorization": f"Bearer {token}"}, None


def _api_request(method: str, path: str, body: dict | None = None,
                 params: dict | None = None) -> dict:
    """Make a Gmail API request."""
    auth, err = _get_auth_header()
    if err:
        return {"status": "error", "message": err}

    url = f"{_GMAIL_API}/{path}"
    if params:
        url += "?" + urlencode(params)

    data = json.dumps(body).encode() if body else None
    headers = {**auth, "Content-Type": "application/json"}

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        return {"status": "error", "code": e.code, "message": body_text[:500]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Helpers ────────────────────────────────────────────

def _decode_body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        if mime.startswith("multipart/") and part.get("parts"):
            result = _decode_body(part)
            if result:
                return result
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return "[HTML] " + base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")[:2000]
    return ""


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _format_message(msg: dict) -> dict:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
        "body": _decode_body(payload),
    }


# ── MCP Tools ──────────────────────────────────────────

@mcp.tool()
def gmail_search(query: str, max_results: int = 10) -> str:
    """Search Gmail messages.

    Args:
        query: Gmail search query (same syntax as Gmail search box).
        max_results: Maximum number of results (default 10, max 50).
    """
    max_results = min(max_results, 50)
    result = _api_request("GET", "messages", params={"q": query, "maxResults": max_results})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)

    messages = result.get("messages", [])
    if not messages:
        return json.dumps({"status": "ok", "count": 0, "messages": []})

    detailed = []
    for msg_ref in messages[:max_results]:
        msg = _api_request("GET", f"messages/{msg_ref['id']}", params={
            "format": "metadata", "metadataHeaders": "From,To,Subject,Date"})
        if "error" not in msg.get("status", ""):
            headers = msg.get("payload", {}).get("headers", [])
            detailed.append({
                "id": msg.get("id", ""),
                "thread_id": msg.get("threadId", ""),
                "from": _get_header(headers, "From"),
                "to": _get_header(headers, "To"),
                "subject": _get_header(headers, "Subject"),
                "date": _get_header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
            })
    return json.dumps({"status": "ok", "count": len(detailed), "messages": detailed}, ensure_ascii=False)


@mcp.tool()
def gmail_read(message_id: str) -> str:
    """Read a specific Gmail message by ID.

    Args:
        message_id: The Gmail message ID.
    """
    result = _api_request("GET", f"messages/{message_id}", params={"format": "full"})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"status": "ok", "message": _format_message(result)}, ensure_ascii=False)


@mcp.tool()
def gmail_read_thread(thread_id: str) -> str:
    """Read all messages in a Gmail thread.

    Args:
        thread_id: The Gmail thread ID.
    """
    result = _api_request("GET", f"threads/{thread_id}", params={"format": "full"})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    messages = [_format_message(m) for m in result.get("messages", [])]
    return json.dumps({"status": "ok", "thread_id": thread_id, "message_count": len(messages), "messages": messages}, ensure_ascii=False)


@mcp.tool()
def gmail_send(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Send an email via Gmail.

    Args:
        to: Recipient email address(es), comma-separated for multiple.
        subject: Email subject line.
        body: Email body text (plain text).
        cc: CC recipients (optional).
        bcc: BCC recipients (optional).
    """
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = _api_request("POST", "messages/send", body={"raw": raw})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"status": "ok", "message_id": result.get("id", ""), "thread_id": result.get("threadId", "")}, ensure_ascii=False)


@mcp.tool()
def gmail_create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Create a Gmail draft (does not send).

    Args:
        to: Recipient email address(es), comma-separated for multiple.
        subject: Email subject line.
        body: Email body text (plain text).
        cc: CC recipients (optional).
        bcc: BCC recipients (optional).
    """
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = _api_request("POST", "drafts", body={"message": {"raw": raw}})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"status": "ok", "draft_id": result.get("id", ""), "message": "Draft created"}, ensure_ascii=False)


@mcp.tool()
def gmail_reply(message_id: str, body: str) -> str:
    """Reply to an existing Gmail message.

    Args:
        message_id: The Gmail message ID to reply to.
        body: Reply body text (plain text).
    """
    original = _api_request("GET", f"messages/{message_id}", params={
        "format": "metadata", "metadataHeaders": "From,To,Subject,Message-ID"})
    if "error" in original.get("status", ""):
        return json.dumps(original, ensure_ascii=False)

    headers = original.get("payload", {}).get("headers", [])
    reply_to = _get_header(headers, "From") or _get_header(headers, "To")
    subject = _get_header(headers, "Subject")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    thread_id = original.get("threadId", "")
    orig_msg_id = _get_header(headers, "Message-ID")

    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = reply_to
    msg["Subject"] = subject
    if orig_msg_id:
        msg["In-Reply-To"] = orig_msg_id
        msg["References"] = orig_msg_id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = _api_request("POST", "messages/send", body={"raw": raw, "threadId": thread_id})
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"status": "ok", "message_id": result.get("id", ""), "thread_id": result.get("threadId", "")}, ensure_ascii=False)


@mcp.tool()
def gmail_get_profile() -> str:
    """Get the authenticated Gmail user's profile (email address, total messages, etc.)."""
    result = _api_request("GET", "profile")
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({
        "status": "ok",
        "email": result.get("emailAddress", ""),
        "total_messages": result.get("messagesTotal", 0),
        "total_threads": result.get("threadsTotal", 0),
    }, ensure_ascii=False)


@mcp.tool()
def gmail_list_labels() -> str:
    """List all Gmail labels (folders/categories)."""
    result = _api_request("GET", "labels")
    if "error" in result.get("status", ""):
        return json.dumps(result, ensure_ascii=False)
    labels = [{"id": lb["id"], "name": lb["name"], "type": lb.get("type", "")}
              for lb in result.get("labels", [])]
    return json.dumps({"status": "ok", "labels": labels}, ensure_ascii=False)


# ── Entry point ────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
