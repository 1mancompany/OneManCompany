import sys
import json
import urllib.request
import urllib.error
import base64

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

def _get_auth_header():
    from onemancompany.core.oauth import OAuthServiceConfig, ensure_oauth_token
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
        return {}, "Gmail OAuth authorization required."
    return {"Authorization": f"Bearer {token}"}, None

def _decode_body(payload):
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
    return ""

def _get_header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

def _format_message(msg):
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "body": _decode_body(payload),
    }

def get_thread(thread_id):
    auth, err = _get_auth_header()
    if err:
        print(json.dumps({"error": err}))
        return
    url = f"{_GMAIL_API}/threads/{thread_id}?format=full"
    req = urllib.request.Request(url, headers=auth)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            messages = [_format_message(m) for m in data.get("messages", [])]
            print(json.dumps({"messages": messages}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    get_thread(sys.argv[1])
