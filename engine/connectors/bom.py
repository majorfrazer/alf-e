"""
Alf-E BOM (Bureau of Meteorology) connector — Australian weather.

Uses the public BOM API at api.weather.bom.gov.au. No auth, no API key.

CONFIG (from playbook [[connectors]] entry):
  id          = "bom"                       (required)
  enabled     = true
  geohash     = "r7hge7"                    (Brisbane CBD — see readme below)
  location    = "Brisbane"                  (friendly name shown in responses)

GEOHASH LOOKUP:
  BOM uses 6-char geohashes to identify locations. Find yours by visiting
  https://weather.bom.gov.au/location/<geohash>/<slug> — the first URL path
  segment after /location/ is the geohash.

  Common Australian cities:
    Brisbane       r7hge7
    Sydney         r3gx2f
    Melbourne      r1r11g
    Perth          qd66hr
    Adelaide       r1f94f
    Gold Coast     r7gg59
    Sunshine Coast r7nydz

TOOLS EXPOSED:
  bom_current        — current observed conditions at the configured location
  bom_forecast_hourly — next 72 hours hour-by-hour
  bom_forecast_daily — 7-day daily forecast
  bom_warnings       — active severe weather warnings for the location
"""

import logging
from typing import Optional

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.connector.bom")

BOM_BASE = "https://api.weather.bom.gov.au/v1"
DEFAULT_GEOHASH = "r7hge7"      # Brisbane CBD
DEFAULT_LOCATION = "Brisbane"
HTTP_TIMEOUT = 10


class BOMConnector(BaseConnector):
    """BOM weather connector — free, no auth, Australian-specific."""

    connector_id = "bom"
    connector_type = "weather"
    description = "BOM — Australian weather: current, forecast, warnings"

    def __init__(self, config: dict):
        super().__init__(config)
        self._geohash = config.get("geohash", DEFAULT_GEOHASH)
        self._location = config.get("location", DEFAULT_LOCATION)
        self._session: Optional["requests.Session"] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not _REQUESTS:
            logger.error("requests not installed — cannot connect to BOM")
            return False
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Alf-E/1.0 (weather connector)"})
        if not self.health_check():
            logger.warning("BOM API reachability check failed, but connector will still load")
            return True  # Don't block startup on transient network
        logger.info(f"BOM connected for {self._location} (geohash={self._geohash})")
        return True

    def disconnect(self) -> None:
        if self._session:
            self._session.close()
        self._session = None
        self.connected = False

    def health_check(self) -> bool:
        if not self._session:
            return False
        try:
            r = self._session.get(
                f"{BOM_BASE}/locations/{self._geohash}",
                timeout=HTTP_TIMEOUT,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── Tools ──────────────────────────────────────────────────────────────

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="bom_current",
                description=(
                    f"Get current observed weather conditions at {self._location} from the "
                    "Australian Bureau of Meteorology. Returns temperature, 'feels like', humidity, "
                    "wind speed/direction, and recent rainfall."
                ),
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="bom_forecast_daily",
                description=(
                    f"Get the 7-day weather forecast for {self._location}. "
                    "Returns min/max temp, chance of rain, short summary per day."
                ),
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="bom_forecast_hourly",
                description=(
                    f"Get the hour-by-hour weather forecast for {self._location} (next ~3 days). "
                    "Great for 'will it rain this afternoon' or 'what's the temp at 5pm'."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "hours": {
                            "type": "integer",
                            "description": "How many upcoming hours to return (default 12, max 72)",
                        }
                    },
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="bom_warnings",
                description=(
                    f"Get active severe weather warnings for {self._location}. "
                    "Use when the user asks about storms, cyclones, flood warnings, or general risk."
                ),
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
        ]

    # ── Dispatch ───────────────────────────────────────────────────────────

    def execute_tool(self, name: str, inp: dict, user_id: str = "fraser") -> ConnectorResult:
        handlers = {
            "bom_current":          self._current,
            "bom_forecast_daily":   self._forecast_daily,
            "bom_forecast_hourly":  self._forecast_hourly,
            "bom_warnings":         self._warnings,
        }
        handler = handlers.get(name)
        if not handler:
            return ConnectorResult(success=False, content=f"Unknown BOM tool: {name!r}")
        try:
            return handler(inp)
        except Exception as e:
            logger.error(f"BOM {name} failed: {e}")
            return ConnectorResult(success=False, content=f"BOM API error: {e}")

    # ── Handlers ───────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict:
        """GET {BOM_BASE}{path} and return parsed JSON payload under 'data' key."""
        r = self._session.get(f"{BOM_BASE}{path}", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("data", {})

    def _current(self, inp: dict) -> ConnectorResult:
        data = self._get(f"/locations/{self._geohash}/observations")
        if not data:
            return ConnectorResult(success=True, content=f"No current observation available for {self._location}.")
        temp = data.get("temp")
        feels = data.get("temp_feels_like")
        humidity = data.get("humidity")
        wind = data.get("wind") or {}
        rain = data.get("rain_since_9am")
        station = data.get("station", {}).get("name", "nearest station")

        parts = [f"{self._location} — current conditions (via {station}):"]
        if temp is not None:
            parts.append(f"  Temperature: {temp}°C" + (f" (feels {feels}°C)" if feels is not None else ""))
        if humidity is not None:
            parts.append(f"  Humidity: {humidity}%")
        if wind:
            parts.append(f"  Wind: {wind.get('speed_kilometre', '?')} km/h {wind.get('direction', '')}")
        if rain is not None:
            parts.append(f"  Rain since 9am: {rain} mm")
        return ConnectorResult(success=True, content="\n".join(parts))

    def _forecast_daily(self, inp: dict) -> ConnectorResult:
        days = self._get(f"/locations/{self._geohash}/forecasts/daily")
        if not days:
            return ConnectorResult(success=True, content=f"No daily forecast available for {self._location}.")
        out = [f"{self._location} — 7-day forecast:"]
        for d in days[:7]:
            date = d.get("date", "")[:10]
            tmin = d.get("temp_min")
            tmax = d.get("temp_max")
            rain = d.get("rain") or {}
            pop = rain.get("chance")
            amount = rain.get("amount") or {}
            amt_max = amount.get("max")
            short = d.get("short_text") or ""
            line = f"  {date}: {tmin}°–{tmax}°C, {short}"
            if pop is not None:
                line += f" (rain {pop}%"
                if amt_max:
                    line += f", up to {amt_max}mm"
                line += ")"
            out.append(line)
        return ConnectorResult(success=True, content="\n".join(out))

    def _forecast_hourly(self, inp: dict) -> ConnectorResult:
        hours = int(inp.get("hours") or 12)
        hours = max(1, min(hours, 72))
        data = self._get(f"/locations/{self._geohash}/forecasts/hourly")
        if not data:
            return ConnectorResult(success=True, content=f"No hourly forecast available for {self._location}.")
        out = [f"{self._location} — next {hours} hours:"]
        for h in data[:hours]:
            time = h.get("time", "")[:16].replace("T", " ")
            temp = h.get("temp")
            rain = h.get("rain") or {}
            pop = rain.get("chance")
            amount = (rain.get("amount") or {}).get("max")
            wind = (h.get("wind") or {}).get("speed_kilometre")
            line = f"  {time}: {temp}°C"
            if pop is not None:
                line += f", rain {pop}%"
                if amount:
                    line += f" ({amount}mm)"
            if wind is not None:
                line += f", wind {wind} km/h"
            out.append(line)
        return ConnectorResult(success=True, content="\n".join(out))

    def _warnings(self, inp: dict) -> ConnectorResult:
        warnings = self._get(f"/locations/{self._geohash}/warnings")
        if not warnings:
            return ConnectorResult(
                success=True,
                content=f"No active severe weather warnings for {self._location}. All clear.",
            )
        out = [f"{self._location} — active warnings ({len(warnings)}):"]
        for w in warnings:
            title = w.get("title", "Warning")
            warning_type = w.get("warning_group_type", "")
            issued = w.get("issue_time", "")[:16].replace("T", " ")
            out.append(f"  • [{warning_type}] {title} (issued {issued})")
        return ConnectorResult(success=True, content="\n".join(out))
