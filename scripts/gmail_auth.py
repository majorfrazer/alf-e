"""
Alf-E Gmail OAuth2 Setup Script — run once on your Mac to generate the token.

Usage:
    cd alf-e-v2.0
    python3 scripts/gmail_auth.py

What it does:
    1. Reads gmail_credentials.json (download from Google Cloud Console)
    2. Opens your browser for OAuth2 consent
    3. Saves gmail_token.json with a refresh token

After running:
    Copy gmail_token.json to your N95 at /data/gmail_token.json

Google Cloud Console setup (if you haven't already):
    1. console.cloud.google.com → New Project (e.g. "Alf-E")
    2. APIs & Services → Enable APIs → search "Gmail API" → Enable
    3. OAuth consent screen → External → fill in app name "Alf-E" → add your email as test user
    4. Credentials → Create Credentials → OAuth 2.0 Client ID → Desktop app
    5. Download JSON → rename to gmail_credentials.json → place in this directory
"""

import os
import sys
from pathlib import Path

# Find the repo root regardless of where this is run from
REPO_ROOT    = Path(__file__).resolve().parent.parent
CREDS_PATH   = REPO_ROOT / "gmail_credentials.json"
TOKEN_PATH   = REPO_ROOT / "gmail_token.json"
SCOPES       = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print(
            "\nMissing Google libraries. Install them:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2\n"
        )
        sys.exit(1)

    if not CREDS_PATH.exists():
        print(f"\nCredentials file not found: {CREDS_PATH}")
        print(
            "\nSteps:\n"
            "  1. Go to console.cloud.google.com\n"
            "  2. Enable Gmail API\n"
            "  3. Create OAuth 2.0 Desktop credentials\n"
            "  4. Download JSON and save as:\n"
            f"     {CREDS_PATH}\n"
            "  5. Re-run this script\n"
        )
        sys.exit(1)

    # Check for existing token
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            if creds.valid:
                print(f"\nExisting token is valid for: {_get_email(creds)}")
                print(f"Token file: {TOKEN_PATH}")
                print("\nAll good — copy this file to your N95 at /data/gmail_token.json")
                return
            if creds.expired and creds.refresh_token:
                print("Existing token is expired — refreshing...")
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json())
                print(f"Refreshed. Account: {_get_email(creds)}")
                print(f"Token file: {TOKEN_PATH}")
                return
        except Exception as e:
            print(f"Existing token invalid ({e}) — starting fresh auth flow...")
            creds = None

    # Run OAuth flow
    print("\nStarting OAuth2 flow — your browser will open for Google sign-in...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_PATH.write_text(creds.to_json())
    print(f"\nAuth complete. Account: {_get_email(creds)}")
    print(f"Token saved to: {TOKEN_PATH}")
    print(f"\nNext steps:")
    print(f"  scp {TOKEN_PATH} user@n95:/data/gmail_token.json")
    print(f"  scp {CREDS_PATH} user@n95:/data/gmail_credentials.json")
    print(f"\nThen restart Alf-E — the Gmail connector will load automatically.")


def _get_email(creds) -> str:
    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds)
        return svc.users().getProfile(userId="me").execute().get("emailAddress", "unknown")
    except Exception:
        return "(could not fetch email)"


if __name__ == "__main__":
    main()
