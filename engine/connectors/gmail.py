"""
Alf-E Gmail Connector — reads, searches, and drafts email via Gmail API.

SETUP (one-time, run on your Mac before deploying to N95):

  1. Go to console.cloud.google.com
  2. Create a project → Enable Gmail API
  3. OAuth consent screen → External → add your Gmail address as test user
  4. Credentials → Create → OAuth 2.0 Client ID → Desktop app
  5. Download JSON → save as gmail_credentials.json
  6. Run:  python3 scripts/gmail_auth.py
     Opens browser, completes OAuth flow, saves gmail_token.json
  7. Copy gmail_token.json to N95 at /data/gmail_token.json

ENVIRONMENT VARIABLES:
  GMAIL_CREDENTIALS_PATH  — path to client_secret JSON  (default: /data/gmail_credentials.json)
  GMAIL_TOKEN_PATH        — path to token JSON          (default: /data/gmail_token.json)
  GMAIL_USER              — Gmail address               (default: alfe.cole@gmail.com)

TOOLS EXPOSED:
  gmail_get_profile     — account info, total message count
  gmail_list_unread     — list unread messages, newest first
  gmail_search          — search with any Gmail query syntax
  gmail_read_message    — read full message content by ID
  gmail_create_draft    — create a draft (subject/confirm tier — Fraser reviews before sending)
  gmail_send_draft      — send a draft (confirm tier)
"""

import os
import base64
import json
import logging
import email as email_lib
from pathlib import Path
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.connector.gmail")

# ── Optional Google API imports ───────────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    _GOOGLE_LIBS = True
except ImportError:
    _GOOGLE_LIBS = False
    logger.warning(
        "Google API libraries not installed. "
        "Run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
    )

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailConnector(BaseConnector):
    """Gmail connector — reads family emails, drafts replies for approval."""

    connector_id   = "gmail"
    connector_type = "email"
    description    = "Gmail — read, search, and draft emails via the Gmail API"

    def __init__(self, config: dict):
        super().__init__(config)
        self._service = None
        self._user = config.get("user", os.environ.get("GMAIL_USER", "alfe.cole@gmail.com"))
        self._credentials_path = Path(
            config.get("credentials_path")
            or os.environ.get("GMAIL_CREDENTIALS_PATH", "/data/gmail_credentials.json")
        )
        self._token_path = Path(
            config.get("token_path")
            or os.environ.get("GMAIL_TOKEN_PATH", "/data/gmail_token.json")
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not _GOOGLE_LIBS:
            logger.error(
                "Cannot connect: google-api-python-client not installed. "
                "Add it to requirements.txt and rebuild."
            )
            return False

        creds = self._load_credentials()
        if not creds:
            logger.error(
                f"Gmail token not found at {self._token_path}. "
                "Run scripts/gmail_auth.py on your Mac to generate it."
            )
            return False

        try:
            self._service = build("gmail", "v1", credentials=creds)
            # Quick connectivity test
            profile = self._service.users().getProfile(userId="me").execute()
            self._user = profile.get("emailAddress", self._user)
            logger.info(f"Gmail connected as {self._user}")
            return True
        except Exception as e:
            logger.error(f"Gmail connect failed: {e}")
            return False

    def disconnect(self) -> None:
        self._service = None
        self.connected = False

    def health_check(self) -> bool:
        if not self._service:
            return False
        try:
            self._service.users().getProfile(userId="me").execute()
            return True
        except Exception:
            return False

    # ── Tools ──────────────────────────────────────────────────────────────

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="gmail_get_profile",
                description="Get the Gmail account profile: email address, total messages, total threads.",
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="gmail_list_unread",
                description=(
                    "List unread emails in the inbox. Returns subject, sender, date, and snippet. "
                    "Great first call for 'any new emails?' or 'what's in the inbox?'"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Max messages to return (default 10, max 50)",
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional label to filter by (default INBOX). E.g. SENT, SPAM.",
                        },
                    },
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="gmail_search",
                description=(
                    "Search Gmail using standard Gmail query syntax. "
                    "Examples: 'from:dad@gmail.com', 'subject:invoice', 'is:unread after:2025/01/01', "
                    "'has:attachment larger:5mb'. Returns list of matching messages."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max messages to return (default 10)",
                        },
                    },
                    "required": ["query"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="gmail_read_message",
                description=(
                    "Read the full content of a specific Gmail message by its ID. "
                    "Use after gmail_search or gmail_list_unread to get the full body text."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "Gmail message ID from a previous search or list call",
                        },
                    },
                    "required": ["message_id"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="gmail_create_draft",
                description=(
                    "Create a draft email. Use when Alf-E wants to compose a reply or new message. "
                    "The draft is NOT sent automatically — Fraser reviews and sends it. "
                    "Returns the draft ID for reference."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "to":          {"type": "string", "description": "Recipient email address"},
                        "subject":     {"type": "string", "description": "Email subject line"},
                        "body":        {"type": "string", "description": "Plain-text email body"},
                        "reply_to_id": {
                            "type": "string",
                            "description": "Optional: message ID to reply to (sets threading headers)",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
                approval_tier="notify",
            ),
            ToolDefinition(
                name="gmail_send_draft",
                description=(
                    "Send an existing draft by its ID. "
                    "Requires Fraser's confirmation — never send without approval."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "draft_id": {
                            "type": "string",
                            "description": "Draft ID returned by gmail_create_draft",
                        },
                    },
                    "required": ["draft_id"],
                },
                approval_tier="confirm",
            ),
        ]

    # ── Tool Execution ─────────────────────────────────────────────────────

    def execute_tool(self, name: str, inp: dict, user_id: str = "fraser") -> ConnectorResult:
        if not self._service:
            return ConnectorResult(success=False, content="Gmail not connected. Check token setup.")
        try:
            if   name == "gmail_get_profile":    return self._get_profile()
            elif name == "gmail_list_unread":    return self._list_unread(inp)
            elif name == "gmail_search":         return self._search(inp)
            elif name == "gmail_read_message":   return self._read_message(inp)
            elif name == "gmail_create_draft":   return self._create_draft(inp)
            elif name == "gmail_send_draft":     return self._send_draft(inp)
            else:
                return ConnectorResult(success=False, content=f"Unknown tool: {name}")
        except Exception as e:
            logger.error(f"gmail.execute_tool({name}) error: {e}")
            return ConnectorResult(success=False, content=f"Gmail error: {e}")

    # ── Internal Handlers ──────────────────────────────────────────────────

    def _get_profile(self) -> ConnectorResult:
        profile = self._service.users().getProfile(userId="me").execute()
        return ConnectorResult(
            success=True,
            content=(
                f"Gmail account: {profile.get('emailAddress')}\n"
                f"Total messages: {profile.get('messagesTotal', 'unknown'):,}\n"
                f"Total threads:  {profile.get('threadsTotal', 'unknown'):,}"
            ),
        )

    def _list_unread(self, inp: dict) -> ConnectorResult:
        max_results = min(int(inp.get("max_results", 10)), 50)
        label = inp.get("label", "INBOX")
        query = f"is:unread label:{label}"
        return self._run_search(query, max_results)

    def _search(self, inp: dict) -> ConnectorResult:
        query       = inp["query"]
        max_results = min(int(inp.get("max_results", 10)), 50)
        return self._run_search(query, max_results)

    def _run_search(self, query: str, max_results: int) -> ConnectorResult:
        result = self._service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return ConnectorResult(success=True, content=f"No messages found for query: {query!r}")

        summaries = []
        for m in messages:
            meta = self._get_message_meta(m["id"])
            summaries.append(meta)

        lines = [f"Found {len(messages)} message(s) for {query!r}:"]
        for s in summaries:
            lines.append(
                f"\n  ID:      {s['id']}\n"
                f"  From:    {s['from']}\n"
                f"  Subject: {s['subject']}\n"
                f"  Date:    {s['date']}\n"
                f"  Snippet: {s['snippet']}"
            )
        return ConnectorResult(success=True, content="\n".join(lines))

    def _get_message_meta(self, message_id: str) -> dict:
        """Get lightweight message metadata (no body)."""
        msg = self._service.users().messages().get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        return {
            "id":      message_id,
            "from":    headers.get("From", "unknown"),
            "to":      headers.get("To", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date":    headers.get("Date", "unknown"),
            "snippet": msg.get("snippet", ""),
        }

    def _read_message(self, inp: dict) -> ConnectorResult:
        message_id = inp["message_id"]
        msg = self._service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = self._extract_body(msg.get("payload", {}))

        # Truncate very long bodies
        max_body = 4000
        truncated = ""
        if len(body) > max_body:
            truncated = f"\n\n[... {len(body) - max_body} more chars truncated]"
            body = body[:max_body]

        return ConnectorResult(
            success=True,
            content=(
                f"From:    {headers.get('From', 'unknown')}\n"
                f"To:      {headers.get('To', '')}\n"
                f"Subject: {headers.get('Subject', '(no subject)')}\n"
                f"Date:    {headers.get('Date', 'unknown')}\n"
                f"\n{body}{truncated}"
            ),
        )

    def _extract_body(self, payload: dict, depth: int = 0) -> str:
        """Recursively extract plain text from a MIME payload."""
        if depth > 5:  # guard against deeply nested MIME
            return ""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if mime_type == "text/plain" and body_data:
            return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

        # For multipart, prefer text/plain over text/html
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

        # HTML fallback — strip tags minimally
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    import re
                    html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
                    html = re.sub(r"<[^>]+>", " ", html)
                    html = re.sub(r"\s+", " ", html).strip()
                    return html

        # Recurse into multipart children
        for part in parts:
            result = self._extract_body(part, depth + 1)
            if result:
                return result

        return "(no readable body)"

    def _create_draft(self, inp: dict) -> ConnectorResult:
        to         = inp["to"]
        subject    = inp["subject"]
        body       = inp["body"]
        reply_id   = inp.get("reply_to_id")

        msg = MIMEMultipart()
        msg["to"]      = to
        msg["from"]    = self._user
        msg["subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Threading headers for replies
        if reply_id:
            original = self._service.users().messages().get(
                userId="me", id=reply_id, format="metadata",
                metadataHeaders=["Message-ID", "References"],
            ).execute()
            orig_headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
            orig_msg_id = orig_headers.get("Message-ID", "")
            if orig_msg_id:
                msg["In-Reply-To"] = orig_msg_id
                msg["References"]  = orig_msg_id
            msg["threadId"] = original.get("threadId", "")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        body_payload = {"message": {"raw": raw}}
        if reply_id:
            try:
                original = self._service.users().messages().get(
                    userId="me", id=reply_id, format="metadata",
                ).execute()
                body_payload["message"]["threadId"] = original.get("threadId", "")
            except Exception:
                pass

        draft = self._service.users().drafts().create(
            userId="me",
            body=body_payload,
        ).execute()

        draft_id = draft.get("id", "unknown")
        return ConnectorResult(
            success=True,
            content=(
                f"Draft created.\n"
                f"  Draft ID: {draft_id}\n"
                f"  To:       {to}\n"
                f"  Subject:  {subject}\n\n"
                f"To send it, call gmail_send_draft with draft_id={draft_id!r} (requires confirmation)."
            ),
        )

    def _send_draft(self, inp: dict) -> ConnectorResult:
        draft_id = inp["draft_id"]
        result = self._service.users().drafts().send(
            userId="me",
            body={"id": draft_id},
        ).execute()
        sent_id = result.get("id", "unknown")
        return ConnectorResult(
            success=True,
            content=f"Email sent. Message ID: {sent_id}",
        )

    # ── Auth Helpers ───────────────────────────────────────────────────────

    def _load_credentials(self) -> Optional["Credentials"]:
        """Load and refresh OAuth2 credentials from token file."""
        if not self._token_path.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
        except Exception as e:
            logger.error(f"Could not load Gmail token from {self._token_path}: {e}")
            return None

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    # Save refreshed token
                    self._save_token(creds)
                    logger.info("Gmail token refreshed and saved.")
                except Exception as e:
                    logger.error(f"Gmail token refresh failed: {e}")
                    return None
            else:
                logger.error("Gmail credentials invalid and no refresh token available.")
                return None

        return creds

    def _save_token(self, creds: "Credentials") -> None:
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(creds.to_json())
        except Exception as e:
            logger.warning(f"Could not save refreshed Gmail token: {e}")
