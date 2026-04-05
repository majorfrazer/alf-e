"""
Alf-E Home Assistant Connector — HA as a proper BaseConnector plugin.

Migrated from engine/ha_connector.py. All entity IDs come from the playbook
config (passed as `config` dict on init), nothing hardcoded.

Tools exposed:
  ha_get_state          — single entity state
  ha_get_numeric        — single entity numeric value
  ha_get_batch          — multiple sensors at once
  ha_list_entities      — list all / by domain
  ha_get_history        — historical states for an entity
  ha_get_history_stats  — min/max/avg from history
  ha_call_service       — call any HA service
  ha_turn_on            — shorthand turn_on
  ha_turn_off           — shorthand turn_off
  ha_toggle             — shorthand toggle
  ha_send_notification  — push notification via HA companion app
  ha_health_check       — connectivity test
"""

import os
import httpx
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.connector.ha")


class HAConnector(BaseConnector):
    """Home Assistant connector — reads sensors and calls services."""

    connector_id   = "ha"
    connector_type = "home_automation"
    description    = "Home Assistant — sensors, service calls, notifications"

    def __init__(self, config: dict):
        super().__init__(config)
        self._base_url: str = ""
        self._token: str = ""
        self._headers: dict = {}
        self._sensors: dict = config.get("sensors", {})

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def connect(self) -> bool:
        url   = self.config.get("url", "")
        token = self._env(self.config.get("token_env", "HA_API_TOKEN"))
        if not url or not token:
            logger.error("HA connector missing url or token")
            return False
        self._base_url = url.rstrip("/")
        self._token    = token
        self._headers  = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }
        ok = self.health_check()
        if ok:
            logger.info(f"HA connected: {self._base_url}")
        else:
            logger.warning(f"HA health check failed: {self._base_url}")
        return ok

    def disconnect(self) -> None:
        self._token   = ""
        self._headers = {}
        self.connected = False

    def health_check(self) -> bool:
        try:
            resp = httpx.get(
                f"{self._base_url}/api/",
                headers=self._headers,
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── Tools ─────────────────────────────────────────────────────────────

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="ha_get_state",
                description="Get the current state of a Home Assistant entity.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "HA entity ID, e.g. sensor.model_3_battery_level"}
                    },
                    "required": ["entity_id"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_get_numeric",
                description="Get the numeric value of a sensor entity.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Sensor entity ID"}
                    },
                    "required": ["entity_id"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_get_batch",
                description="Fetch multiple sensor readings at once. Returns a dict of sensor_name → value.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "sensor_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Keys from the playbook sensors section, e.g. ['solar_watts', 'tesla_soc']",
                        }
                    },
                    "required": ["sensor_keys"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_list_entities",
                description="List Home Assistant entities, optionally filtered by domain (e.g. 'sensor', 'switch', 'light').",
                input_schema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Optional domain filter"}
                    },
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_get_history",
                description="Get historical state values for an entity over the last N hours.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "hours":     {"type": "integer", "default": 24, "description": "How many hours back to look"},
                    },
                    "required": ["entity_id"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_get_history_stats",
                description="Get min/max/avg statistics from an entity's history over the last N hours.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "hours":     {"type": "integer", "default": 24},
                    },
                    "required": ["entity_id"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="ha_call_service",
                description="Call a Home Assistant service (e.g. switch.turn_on, climate.set_temperature).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "domain":    {"type": "string", "description": "Service domain, e.g. 'switch'"},
                        "service":   {"type": "string", "description": "Service name, e.g. 'turn_on'"},
                        "entity_id": {"type": "string", "description": "Target entity ID"},
                        "data":      {"type": "object", "description": "Optional extra service data"},
                    },
                    "required": ["domain", "service"],
                },
                approval_tier="confirm",
            ),
            ToolDefinition(
                name="ha_turn_on",
                description="Turn on a Home Assistant entity.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"}
                    },
                    "required": ["entity_id"],
                },
                approval_tier="confirm",
            ),
            ToolDefinition(
                name="ha_turn_off",
                description="Turn off a Home Assistant entity.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"}
                    },
                    "required": ["entity_id"],
                },
                approval_tier="confirm",
            ),
            ToolDefinition(
                name="ha_toggle",
                description="Toggle a Home Assistant entity on/off.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"}
                    },
                    "required": ["entity_id"],
                },
                approval_tier="confirm",
            ),
            ToolDefinition(
                name="ha_send_notification",
                description="Send a push notification via the Home Assistant companion app.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "title":   {"type": "string", "default": "Alf-E"},
                        "target":  {"type": "string", "description": "Optional: specific notify service, e.g. notify.mobile_app_fraser_iphone"},
                    },
                    "required": ["message"],
                },
                approval_tier="notify",
            ),
            ToolDefinition(
                name="ha_health_check",
                description="Test the connection to Home Assistant. Returns True/False.",
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
        ]

    # ── Tool Execution ────────────────────────────────────────────────────

    def execute_tool(self, name: str, inp: dict, user_id: str = "fraser") -> ConnectorResult:
        try:
            if name == "ha_get_state":
                result = self._get_entity_full(inp["entity_id"])
                if result:
                    return ConnectorResult(success=True, content=str(result))
                return ConnectorResult(success=False, content=f"Entity not found: {inp['entity_id']}")

            elif name == "ha_get_numeric":
                val = self._get_numeric(inp["entity_id"])
                if val is not None:
                    return ConnectorResult(success=True, content=str(val))
                return ConnectorResult(success=False, content=f"No numeric value for {inp['entity_id']}")

            elif name == "ha_get_batch":
                keys = inp.get("sensor_keys", [])
                results = {}
                for key in keys:
                    entity_id = self._sensors.get(key)
                    if entity_id:
                        results[key] = self._get_numeric(entity_id)
                    else:
                        results[key] = f"Unknown sensor key: {key}"
                return ConnectorResult(success=True, content=str(results))

            elif name == "ha_list_entities":
                entities = self._list_entities(domain=inp.get("domain"))
                return ConnectorResult(success=True, content=str(entities))

            elif name == "ha_get_history":
                history = self._get_history(inp["entity_id"], inp.get("hours", 24))
                return ConnectorResult(success=True, content=str(history))

            elif name == "ha_get_history_stats":
                stats = self._get_history_stats(inp["entity_id"], inp.get("hours", 24))
                return ConnectorResult(success=True, content=str(stats))

            elif name == "ha_call_service":
                ok = self._call_service(
                    inp["domain"],
                    inp["service"],
                    entity_id=inp.get("entity_id"),
                    data=inp.get("data"),
                )
                if ok:
                    return ConnectorResult(success=True, content=f"Service {inp['domain']}.{inp['service']} called successfully.")
                return ConnectorResult(success=False, content=f"Service call failed: {inp['domain']}.{inp['service']}")

            elif name == "ha_turn_on":
                ok = self._call_service(inp["entity_id"].split(".")[0], "turn_on", inp["entity_id"])
                return ConnectorResult(success=ok, content="Turned on." if ok else "Failed to turn on.")

            elif name == "ha_turn_off":
                ok = self._call_service(inp["entity_id"].split(".")[0], "turn_off", inp["entity_id"])
                return ConnectorResult(success=ok, content="Turned off." if ok else "Failed to turn off.")

            elif name == "ha_toggle":
                ok = self._call_service(inp["entity_id"].split(".")[0], "toggle", inp["entity_id"])
                return ConnectorResult(success=ok, content="Toggled." if ok else "Failed to toggle.")

            elif name == "ha_send_notification":
                ok = self._send_notification(
                    inp["message"],
                    title=inp.get("title", "Alf-E"),
                    target=inp.get("target"),
                )
                return ConnectorResult(success=ok, content="Notification sent." if ok else "Notification failed.")

            elif name == "ha_health_check":
                ok = self.health_check()
                return ConnectorResult(success=True, content=f"HA reachable: {ok}")

            else:
                return ConnectorResult(success=False, content=f"Unknown tool: {name}")

        except Exception as e:
            logger.error(f"ha.execute_tool({name}) error: {e}")
            return ConnectorResult(success=False, content=f"Error executing {name}: {e}")

    # ── Internal API Methods ──────────────────────────────────────────────

    def _get_state_raw(self, entity_id: str) -> Optional[dict]:
        try:
            resp = httpx.get(
                f"{self._base_url}/api/states/{entity_id}",
                headers=self._headers,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"HA state fetch failed for {entity_id}: {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"HA connection error: {e}")
            return None

    def _get_entity_full(self, entity_id: str) -> Optional[dict]:
        raw = self._get_state_raw(entity_id)
        if not raw:
            return None
        return {
            "entity_id":    raw.get("entity_id"),
            "state":        raw.get("state"),
            "attributes":   raw.get("attributes", {}),
            "last_changed": raw.get("last_changed"),
            "last_updated": raw.get("last_updated"),
        }

    def _get_numeric(self, entity_id: str) -> Optional[float]:
        raw = self._get_state_raw(entity_id)
        if not raw:
            return None
        val = raw.get("state")
        if val in (None, "unavailable", "unknown"):
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _list_entities(self, domain: str = None) -> list[dict]:
        try:
            resp = httpx.get(
                f"{self._base_url}/api/states",
                headers=self._headers,
                timeout=20,
            )
            if resp.status_code != 200:
                return []
            results = []
            for e in resp.json():
                eid = e.get("entity_id", "")
                if domain and not eid.startswith(f"{domain}."):
                    continue
                results.append({
                    "entity_id":    eid,
                    "state":        e.get("state"),
                    "friendly_name": e.get("attributes", {}).get("friendly_name", eid),
                })
            return sorted(results, key=lambda x: x["entity_id"])
        except Exception as e:
            logger.error(f"HA list entities error: {e}")
            return []

    def _get_history(self, entity_id: str, hours: int = 24) -> list[dict]:
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            resp = httpx.get(
                f"{self._base_url}/api/history/period/{start}",
                headers=self._headers,
                params={
                    "filter_entity_id": entity_id,
                    "minimal_response": "true",
                    "no_attributes":    "true",
                },
                timeout=20,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not data or not data[0]:
                return []
            return [
                {"state": s.get("state"), "last_changed": s.get("last_changed")}
                for s in data[0]
                if s.get("state") not in ("unavailable", "unknown")
            ]
        except Exception as e:
            logger.error(f"HA history error: {e}")
            return []

    def _get_history_stats(self, entity_id: str, hours: int = 24) -> dict:
        history = self._get_history(entity_id, hours)
        numeric = []
        for entry in history:
            try:
                numeric.append(float(entry["state"]))
            except (ValueError, TypeError):
                pass
        if not numeric:
            return {"error": "No numeric data available", "samples": 0}
        return {
            "samples": len(numeric),
            "min":     round(min(numeric), 2),
            "max":     round(max(numeric), 2),
            "avg":     round(sum(numeric) / len(numeric), 2),
            "first":   round(numeric[0], 2),
            "last":    round(numeric[-1], 2),
            "hours":   hours,
        }

    def _call_service(
        self,
        domain: str,
        service: str,
        entity_id: str = None,
        data: dict = None,
    ) -> bool:
        payload = {}
        if entity_id:
            payload["entity_id"] = entity_id
        if data:
            payload.update(data)
        try:
            resp = httpx.post(
                f"{self._base_url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(f"HA service call: {domain}.{service} on {entity_id}")
                return True
            logger.warning(f"HA service call failed: {resp.status_code} - {resp.text}")
            return False
        except Exception as e:
            logger.error(f"HA service call error: {e}")
            return False

    def _send_notification(
        self,
        message: str,
        title: str = "Alf-E",
        target: str = None,
    ) -> bool:
        service = target.replace("notify.", "") if target else "notify"
        try:
            resp = httpx.post(
                f"{self._base_url}/api/services/notify/{service}",
                headers=self._headers,
                json={"title": title, "message": message},
                timeout=10,
            )
            ok = resp.status_code == 200
            if not ok:
                logger.warning(f"Notification failed: {resp.status_code} - {resp.text}")
            return ok
        except Exception as e:
            logger.error(f"Notification error: {e}")
            return False

    # ── Legacy compatibility (for code that still imports ha_connector) ───

    def get_state(self, entity_id: str) -> Optional[dict]:
        return self._get_state_raw(entity_id)

    def get_state_value(self, entity_id: str) -> Optional[str]:
        raw = self._get_state_raw(entity_id)
        return raw.get("state") if raw else None

    def get_numeric_value(self, entity_id: str) -> Optional[float]:
        return self._get_numeric(entity_id)

    def get_sensor_batch(self, sensor_map: dict) -> dict:
        return {k: self._get_numeric(v) for k, v in sensor_map.items()}

    def call_service(self, domain, service, entity_id=None, data=None) -> bool:
        return self._call_service(domain, service, entity_id, data)

    def send_notification(self, message, title="Alf-E", target=None) -> bool:
        return self._send_notification(message, title, target)

    def turn_on(self, entity_id: str) -> bool:
        return self._call_service(entity_id.split(".")[0], "turn_on", entity_id)

    def turn_off(self, entity_id: str) -> bool:
        return self._call_service(entity_id.split(".")[0], "turn_off", entity_id)

    def toggle(self, entity_id: str) -> bool:
        return self._call_service(entity_id.split(".")[0], "toggle", entity_id)

    def get_all_entities(self) -> list[str]:
        return [e["entity_id"] for e in self._list_entities()]

    def list_entities(self, domain=None) -> list[dict]:
        return self._list_entities(domain)

    def get_history(self, entity_id: str, hours: int = 24) -> list[dict]:
        return self._get_history(entity_id, hours)

    def get_history_stats(self, entity_id: str, hours: int = 24) -> dict:
        return self._get_history_stats(entity_id, hours)
