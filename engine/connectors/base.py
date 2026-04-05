"""
Alf-E BaseConnector — Abstract base class for all connectors.

Every connector (HA, Gmail, Google Calendar, Tesla, etc.) implements this
interface. Alf-E's agent routes tool calls through the ConnectorRegistry,
which loads and manages connector instances.

Level C foundations: get_test_stub() returns a ready-to-fill pytest skeleton
so future auto-test can validate new connectors before deployment.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import logging

logger = logging.getLogger("alfe.connector")


# ── Tool Definition ───────────────────────────────────────────────────────────

@dataclass
class ToolDefinition:
    """Describes a single tool exposed by a connector.

    Mirrors the shape Anthropic's tool-use API expects, so the agent can
    pass these directly to the Claude API without transformation.
    """
    name: str
    description: str
    input_schema: dict          # JSON Schema dict — {"type": "object", "properties": {...}}
    approval_tier: str = "autonomous"   # autonomous | notify | confirm | admin_only
    connector_id: str = ""      # set automatically by ConnectorRegistry on registration

    def to_anthropic(self) -> dict:
        """Return the Anthropic tool-use dict format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ── Connector Result ──────────────────────────────────────────────────────────

@dataclass
class ConnectorResult:
    """Standardised return from execute_tool().

    success=True  → content is the string result to feed back to the agent
    success=False → content is an error message; agent will report it
    requires_approval=True → agent must queue this for user approval before acting
    """
    success: bool
    content: str
    requires_approval: bool = False
    approval_payload: Optional[dict] = None  # data needed to re-execute after approval
    metadata: dict = field(default_factory=dict)


# ── BaseConnector ABC ─────────────────────────────────────────────────────────

class BaseConnector(ABC):
    """Abstract base for all Alf-E connectors.

    Subclass this, implement the abstract methods, and add the connector
    to a playbook's [[connectors]] section to have it auto-loaded.

    Minimal implementation:
        class MyConnector(BaseConnector):
            connector_id = "my_service"
            connector_type = "my_service"
            description = "Does things with My Service"

            def connect(self) -> bool: ...
            def disconnect(self) -> None: ...
            def health_check(self) -> bool: ...
            def get_tools(self) -> list[ToolDefinition]: ...
            def execute_tool(self, name, inp, user_id) -> ConnectorResult: ...
    """

    # Subclasses MUST set these class-level attributes
    connector_id: str = ""      # unique snake_case ID: "ha", "gmail", "tesla"
    connector_type: str = ""    # category: "home_automation", "email", "vehicle"
    description: str = ""       # one-liner shown in Alf-E's connector status

    def __init__(self, config: dict):
        """
        Args:
            config: the connector's section from the playbook TOML, e.g.
                    {"url": "...", "token_env": "HA_API_TOKEN", ...}
        """
        self.config = config
        self.connected: bool = False
        self._logger = logging.getLogger(f"alfe.connector.{self.connector_id}")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """Initialise connection. Return True if ready, False on failure.

        Called once at startup by ConnectorRegistry. Connectors should
        read credentials from environment variables (never from config directly).
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the connection cleanly. Called on shutdown."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the service is reachable right now."""

    # ── Tool Interface ────────────────────────────────────────────────────

    @abstractmethod
    def get_tools(self) -> list[ToolDefinition]:
        """Return all tools this connector exposes to the agent.

        Called by ConnectorRegistry to build the master tool list passed
        to the Claude API. Each ToolDefinition must have a globally unique
        name — use the connector_id as a prefix: "ha_get_state", "gmail_search".
        """

    @abstractmethod
    def execute_tool(
        self,
        name: str,
        inp: dict,
        user_id: str = "fraser",
    ) -> ConnectorResult:
        """Execute a tool call routed from the agent.

        Args:
            name:    tool name exactly as in get_tools()
            inp:     validated input dict from the Claude API
            user_id: calling user, used for approval tier checks

        Returns:
            ConnectorResult — always returns, never raises.
            Catch all exceptions internally and return success=False.
        """

    # ── Concrete Helpers ──────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return connector status for /api/status endpoint."""
        return {
            "connector_id":   self.connector_id,
            "connector_type": self.connector_type,
            "description":    self.description,
            "connected":      self.connected,
            "healthy":        self.health_check() if self.connected else False,
            "tool_count":     len(self.get_tools()) if self.connected else 0,
        }

    def get_test_stub(self) -> str:
        """Return a pytest stub for this connector. Level C foundation.

        When auto-test is implemented, Alf-E will execute these stubs
        before approving new connector code. For now, they're generated
        alongside every new connector as a quality gate reminder.
        """
        tools = self.get_tools() if self.connected else []
        tool_tests = "\n\n".join([
            f"    def test_{t.name}(self, connector):\n"
            f"        # TODO: assert connector.execute_tool('{t.name}', {{}}, 'test_user').success"
            for t in tools
        ]) if tools else "    # No tools registered yet — connect() first"

        return f'''"""
Auto-generated test stub for {self.connector_id} connector.
Generated by BaseConnector.get_test_stub() — fill in assertions before deploying.
"""
import pytest
from engine.connectors.{self.connector_id} import {self.__class__.__name__}


@pytest.fixture
def connector(monkeypatch):
    """Provide a connected {self.connector_id} connector for testing."""
    # TODO: monkeypatch environment variables and external HTTP calls
    c = {self.__class__.__name__}(config={{}})
    # c.connect()  # uncomment once mocking is in place
    return c


class Test{self.__class__.__name__}:

    def test_health_check(self, connector):
        # TODO: mock the external service and assert health_check() returns True
        pass

{tool_tests}
'''

    def _env(self, key: str) -> Optional[str]:
        """Read an environment variable. Log a warning if missing."""
        import os
        val = os.environ.get(key)
        if not val:
            self._logger.warning(f"Missing env var: {key}")
        return val

    def __repr__(self) -> str:
        status = "connected" if self.connected else "disconnected"
        return f"<{self.__class__.__name__} id={self.connector_id!r} {status}>"
