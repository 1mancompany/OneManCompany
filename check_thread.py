import urllib.request
import json
import base64
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
url = "https://gmail.googleapis.com/gmail/v1/users/me/threads/19cda084067d156f?format=full"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        for msg in data.get("messages", []):
            headers = msg.get("payload", {}).get("headers", [])
            from_hdr = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
            print("From:", from_hdr)
            
            # Decode body
            payload = msg.get("payload", {})
            body = ""
            if payload.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            else:
                for part in payload.get("parts", []):
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break
            print("Body:", body[:500])
            print("-" * 40)
except Exception as e:
    print(e)
