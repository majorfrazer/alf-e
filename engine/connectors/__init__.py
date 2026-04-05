"""
Alf-E ConnectorRegistry — loads, manages, and routes through all connectors.

Usage in agent:
    registry = ConnectorRegistry(playbook)
    registry.load_all()

    tools = registry.get_all_tools()          # flat list → Claude API
    result = registry.execute("ha_get_state", {"entity_id": "..."}, user_id)
"""

import logging
import importlib
from typing import Optional

from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.registry")


# ── Built-in connector map ────────────────────────────────────────────────────
# Maps connector_id → module path. New connectors are added here when Alf-E
# generates and the user approves them.

CONNECTOR_MODULES: dict[str, str] = {
    "ha":      "engine.connectors.ha",
    "gmail":   "engine.connectors.gmail",
    "gcal":    "engine.connectors.gcal",
    "weather": "engine.connectors.weather",
    "tesla":   "engine.connectors.tesla",
    "eufy":    "engine.connectors.eufy",
}


class ConnectorRegistry:
    """Loads connectors declared in the playbook and routes tool calls."""

    def __init__(self, playbook: object):
        """
        Args:
            playbook: loaded PlaybookConfig (pydantic model from playbook_loader)
        """
        self.playbook = playbook
        self._connectors: dict[str, BaseConnector] = {}  # connector_id → instance
        self._tool_map: dict[str, str] = {}              # tool_name → connector_id

    # ── Loading ───────────────────────────────────────────────────────────

    def load_all(self) -> None:
        """Instantiate and connect all connectors declared in the playbook."""
        declared = self._get_declared_connectors()
        for connector_id, config in declared.items():
            self._load_connector(connector_id, config)
        self._rebuild_tool_map()
        logger.info(
            f"Registry ready: {len(self._connectors)} connectors, "
            f"{len(self._tool_map)} tools"
        )

    def _get_declared_connectors(self) -> dict[str, dict]:
        """Pull connector declarations out of the playbook.

        Supports two formats:
          1. playbook.connectors dict  (preferred going forward)
          2. Inferred from known playbook sections (ha, gmail, etc.) for
             backwards compatibility with cole_sandbox.toml which has
             [home_assistant] and no [[connectors]] section yet.
        """
        declared: dict[str, dict] = {}

        # Format 1: explicit [[connectors]] or [connectors.*] section
        if hasattr(self.playbook, "connectors") and self.playbook.connectors:
            for item in self.playbook.connectors:
                # item is a ConnectorConfig pydantic model — dump all fields including extras
                if hasattr(item, "model_dump"):
                    raw = item.model_dump()
                elif hasattr(item, "dict"):
                    raw = item.dict()
                else:
                    raw = dict(item)
                cid = raw.get("id") or raw.get("connector_id")
                if cid and raw.get("enabled", True):
                    declared[cid] = raw
            # Also add HA config to the ha connector entry if home_assistant section exists
            if "ha" in declared and hasattr(self.playbook, "home_assistant") and self.playbook.home_assistant:
                ha_cfg = self.playbook.home_assistant
                declared["ha"].update({
                    "url":       getattr(ha_cfg, "url", ""),
                    "token_env": getattr(ha_cfg, "token_env", "HA_API_TOKEN"),
                    "sensors":   dict(self.playbook.sensors) if hasattr(self.playbook, "sensors") else {},
                })
            return declared

        # Format 2: infer from known top-level sections (backwards compat)
        if hasattr(self.playbook, "home_assistant") and self.playbook.home_assistant:
            ha_cfg = self.playbook.home_assistant
            declared["ha"] = {
                "url":       getattr(ha_cfg, "url", ""),
                "token_env": getattr(ha_cfg, "token_env", "HA_API_TOKEN"),
                "sensors":   dict(self.playbook.sensors) if hasattr(self.playbook, "sensors") else {},
            }

        return declared

    def _load_connector(self, connector_id: str, config: dict) -> None:
        """Import, instantiate, and connect a single connector."""
        module_path = CONNECTOR_MODULES.get(connector_id)
        if not module_path:
            logger.warning(f"No module registered for connector_id={connector_id!r}")
            return

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            logger.info(f"Connector {connector_id!r} not available (module missing): {e}")
            return

        # Find the BaseConnector subclass in the module
        cls = None
        for attr in dir(module):
            obj = getattr(module, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseConnector)
                and obj is not BaseConnector
            ):
                cls = obj
                break

        if not cls:
            logger.error(f"No BaseConnector subclass found in {module_path}")
            return

        try:
            instance: BaseConnector = cls(config=config)
            ok = instance.connect()
            instance.connected = ok
            if ok:
                self._connectors[connector_id] = instance
                logger.info(f"Connector loaded: {connector_id} ({len(instance.get_tools())} tools)")
            else:
                logger.warning(f"Connector {connector_id!r} failed to connect — skipped")
        except Exception as e:
            logger.error(f"Error loading connector {connector_id!r}: {e}")

    def _rebuild_tool_map(self) -> None:
        """Rebuild tool_name → connector_id index."""
        self._tool_map.clear()
        for connector_id, connector in self._connectors.items():
            for tool in connector.get_tools():
                if tool.name in self._tool_map:
                    logger.warning(
                        f"Tool name collision: {tool.name!r} "
                        f"(connectors {self._tool_map[tool.name]!r} and {connector_id!r})"
                    )
                else:
                    self._tool_map[tool.name] = connector_id
                    tool.connector_id = connector_id  # stamp for tracing

    # ── Tool Interface ────────────────────────────────────────────────────

    def get_all_tools(self) -> list[ToolDefinition]:
        """Return all tools from all loaded connectors — passed to Claude API."""
        tools = []
        for connector in self._connectors.values():
            tools.extend(connector.get_tools())
        return tools

    def get_anthropic_tools(self) -> list[dict]:
        """Return tools in Anthropic API format."""
        return [t.to_anthropic() for t in self.get_all_tools()]

    def execute(
        self,
        tool_name: str,
        inp: dict,
        user_id: str = "fraser",
    ) -> ConnectorResult:
        """Route a tool call to the correct connector.

        Returns ConnectorResult(success=False) if tool is unknown.
        """
        connector_id = self._tool_map.get(tool_name)
        if not connector_id:
            return ConnectorResult(
                success=False,
                content=f"Unknown tool: {tool_name!r}. Available: {sorted(self._tool_map.keys())}",
            )
        connector = self._connectors[connector_id]
        try:
            return connector.execute_tool(tool_name, inp, user_id)
        except Exception as e:
            logger.error(f"Unhandled error in {connector_id}.execute_tool({tool_name!r}): {e}")
            return ConnectorResult(success=False, content=f"Internal error: {e}")

    # ── Status + Discovery ────────────────────────────────────────────────

    def get_status(self) -> list[dict]:
        """Status of all connectors — for /api/status endpoint."""
        return [c.get_status() for c in self._connectors.values()]

    def get_connector(self, connector_id: str) -> Optional[BaseConnector]:
        return self._connectors.get(connector_id)

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_map

    def tool_count(self) -> int:
        return len(self._tool_map)

    def connector_ids(self) -> list[str]:
        return list(self._connectors.keys())

    def __repr__(self) -> str:
        return (
            f"<ConnectorRegistry connectors={self.connector_ids()} "
            f"tools={self.tool_count()}>"
        )
