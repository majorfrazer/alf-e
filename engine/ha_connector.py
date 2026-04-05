"""
Alf-E Home Assistant Connector — Read sensors AND call services.

All entity IDs come from the playbook config, nothing hardcoded.
Supports both read (sensor states) and write (service calls) operations.
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger("alfe.ha")


class HAConnector:
    """Home Assistant API connector for Alf-E."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ── Read Operations ──────────────────────────────────────────────────

    def get_state(self, entity_id: str) -> Optional[dict]:
        """Get a single entity's full state object."""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"HA state fetch failed for {entity_id}: {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"HA connection error: {e}")
            return None

    def get_state_value(self, entity_id: str) -> Optional[str]:
        """Get just the state value string for an entity."""
        state = self.get_state(entity_id)
        if state and "state" in state:
            return state["state"]
        return None

    def get_numeric_value(self, entity_id: str) -> Optional[float]:
        """Get a numeric state value, or None if unavailable/non-numeric."""
        val = self.get_state_value(entity_id)
        if val is None or val in ("unavailable", "unknown"):
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def get_sensor_batch(self, sensor_map: dict[str, str]) -> dict[str, Optional[float]]:
        """Fetch multiple sensors from a playbook sensor map.

        Args:
            sensor_map: {"solar_watts": "sensor.xxx", "house_watts": "sensor.yyy", ...}

        Returns:
            {"solar_watts": 3200.0, "house_watts": 1500.0, ...}
        """
        results = {}
        for key, entity_id in sensor_map.items():
            results[key] = self.get_numeric_value(entity_id)
        return results

    # ── Write Operations (Service Calls) ─────────────────────────────────

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str = None,
        data: dict = None,
    ) -> bool:
        """Call a Home Assistant service.

        Examples:
            call_service("switch", "turn_on", "switch.pool_pump")
            call_service("climate", "set_temperature", "climate.living_room", {"temperature": 22})
            call_service("light", "turn_on", "light.kitchen", {"brightness": 200})
        """
        payload = {}
        if entity_id:
            payload["entity_id"] = entity_id
        if data:
            payload.update(data)

        try:
            resp = httpx.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self.headers,
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

    def turn_on(self, entity_id: str) -> bool:
        """Turn on a switch/light/etc."""
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_on", entity_id)

    def turn_off(self, entity_id: str) -> bool:
        """Turn off a switch/light/etc."""
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_off", entity_id)

    def toggle(self, entity_id: str) -> bool:
        """Toggle a switch/light/etc."""
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "toggle", entity_id)

    # ── Health ───────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Test the connection to Home Assistant."""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/",
                headers=self.headers,
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_all_entities(self) -> list[str]:
        """Get a list of all entity IDs (useful for onboarding/discovery)."""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/states",
                headers=self.headers,
                timeout=15,
            )
            if resp.status_code == 200:
                return [e["entity_id"] for e in resp.json()]
            return []
        except Exception:
            return []
