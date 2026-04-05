"""
Alf-E Home Assistant Connector — Read sensors AND call services.

All entity IDs come from the playbook config, nothing hardcoded.
Supports both read (sensor states) and write (service calls) operations,
plus full entity discovery and historical state queries.
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
        """Fetch multiple sensors from a playbook sensor map."""
        results = {}
        for key, entity_id in sensor_map.items():
            results[key] = self.get_numeric_value(entity_id)
        return results

    def get_entity_full(self, entity_id: str) -> Optional[dict]:
        """Get an entity's state, attributes, and metadata as a clean dict."""
        raw = self.get_state(entity_id)
        if not raw:
            return None
        return {
            "entity_id":   raw.get("entity_id"),
            "state":       raw.get("state"),
            "attributes":  raw.get("attributes", {}),
            "last_changed": raw.get("last_changed"),
            "last_updated": raw.get("last_updated"),
        }

    def list_entities(self, domain: str = None) -> list[dict]:
        """List all entities, optionally filtered by domain.

        Returns list of {entity_id, state, friendly_name}.
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/api/states",
                headers=self.headers,
                timeout=20,
            )
            if resp.status_code != 200:
                return []
            all_entities = resp.json()
            results = []
            for e in all_entities:
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

    def get_history(
        self,
        entity_id: str,
        hours: int = 24,
    ) -> list[dict]:
        """Get historical states for an entity over the last N hours.

        Returns list of {state, last_changed} dicts, oldest first.
        """
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            resp = httpx.get(
                f"{self.base_url}/api/history/period/{start}",
                headers=self.headers,
                params={
                    "filter_entity_id": entity_id,
                    "minimal_response": "true",
                    "no_attributes": "true",
                },
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning(f"HA history fetch failed: {resp.status_code}")
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

    def get_history_stats(self, entity_id: str, hours: int = 24) -> dict:
        """Compute min/max/avg/first/last from historical numeric states."""
        history = self.get_history(entity_id, hours)
        numeric = []
        for entry in history:
            try:
                numeric.append(float(entry["state"]))
            except (ValueError, TypeError):
                pass
        if not numeric:
            return {"error": "No numeric data available", "samples": 0}
        return {
            "samples":    len(numeric),
            "min":        round(min(numeric), 2),
            "max":        round(max(numeric), 2),
            "avg":        round(sum(numeric) / len(numeric), 2),
            "first":      round(numeric[0], 2),
            "last":       round(numeric[-1], 2),
            "hours":      hours,
        }

    # ── Write Operations (Service Calls) ─────────────────────────────────

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str = None,
        data: dict = None,
    ) -> bool:
        """Call a Home Assistant service."""
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

    def send_notification(
        self,
        message: str,
        title: str = "Alf-E",
        target: str = None,
    ) -> bool:
        """Send a push notification via HA companion app.

        target: optional specific notify service (e.g. 'notify.mobile_app_fraser_iphone')
                defaults to 'notify.notify' (all devices)
        """
        service = target.replace("notify.", "") if target else "notify"
        domain  = "notify"
        try:
            resp = httpx.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self.headers,
                json={"title": title, "message": message},
                timeout=10,
            )
            success = resp.status_code == 200
            if not success:
                logger.warning(f"Notification failed: {resp.status_code} - {resp.text}")
            return success
        except Exception as e:
            logger.error(f"Notification error: {e}")
            return False

    def turn_on(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_on", entity_id)

    def turn_off(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_off", entity_id)

    def toggle(self, entity_id: str) -> bool:
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
        """Get a list of all entity IDs."""
        return [e["entity_id"] for e in self.list_entities()]
