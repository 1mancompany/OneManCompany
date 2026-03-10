import sys
import json
import urllib.request
from onemancompany.core.oauth import OAuthServiceConfig, ensure_oauth_token

def stop_cron():
    print("Stopping cron job...")

if __name__ == "__main__":
    stop_cron()
