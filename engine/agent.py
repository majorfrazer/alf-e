"""
Alf-E Agent — Agentic tool-use loop.

Full capability set:
- Connector Registry: HA, Gmail, Tesla, and any connector Alf-E builds itself
- Memory: search past conversations, store/retrieve persistent context facts
- Web: search the internet, fetch URLs
- Files: read and write files within safe paths
- Self-awareness: status, cost summary, tool listing
- Self-building: propose_connector generates new connector code for user approval
"""

import os
import json
import logging
import httpx
from pathlib import Path
from typing import Optional
from anthropic import Anthropic
from engine.model_router import ModelRouter
from engine.memory import Memory
from engine.playbook_schema import PlaybookConfig, ActionApproval

# Legacy HA connector — kept for backwards compatibility during migration
try:
    from engine.ha_connector import HAConnector
except ImportError:
    HAConnector = None

# New connector registry — routes tool calls to pluggable connectors
try:
    from engine.connectors import ConnectorRegistry
    from engine.connectors.base import ConnectorResult
except ImportError:
    ConnectorRegistry = None
    ConnectorResult = None

logger = logging.getLogger("alfe.agent")

TOOLS = [
    # ── Home Assistant: Read ───────────────────────────────────────────
    {
        "name": "get_sensor",
        "description": (
            "Get a live reading for a named sensor from the playbook config. "
            "Use this for quick access to pre-configured sensors like solar_watts, house_watts, tesla_soc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_name": {
                    "type": "string",
                    "description": "Sensor key from playbook (e.g. 'solar_watts', 'house_watts', 'tesla_soc', 'grid_watts')",
                }
            },
            "required": ["sensor_name"],
        },
    },
    {
        "name": "get_all_sensors",
        "description": "Get all pre-configured sensor readings at once. Best first call for home/energy status questions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_ha_entity",
        "description": (
            "Get full state and attributes for ANY Home Assistant entity by its entity_id. "
            "Use this when you need an entity not in the pre-configured sensors, or need full attributes. "
            "Examples: lights, switches, climate, covers, automations, people, zones, cameras."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Full HA entity ID (e.g. 'light.kitchen', 'climate.living_room', 'person.fraser')",
                }
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "list_ha_entities",
        "description": (
            "List all Home Assistant entities, optionally filtered by domain. "
            "Use this to discover what's available before querying a specific entity. "
            "Returns entity_id, current state, and friendly name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'light', 'switch', 'sensor', 'climate', 'cover', 'automation'). Omit for all entities.",
                }
            },
        },
    },
    {
        "name": "get_ha_history",
        "description": (
            "Get historical state data for a Home Assistant entity over the last N hours. "
            "Returns min/max/average and sample count. "
            "Use this for trend analysis, daily totals, energy summaries, anomaly detection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Full HA entity ID to query history for",
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours of history to retrieve (default 24, max 168 = 7 days)",
                },
            },
            "required": ["entity_id"],
        },
    },
    # ── Home Assistant: Write ──────────────────────────────────────────
    {
        "name": "ha_service_call",
        "description": (
            "Call a Home Assistant service to control a device. "
            "Examples: turn on/off lights, switches, covers; set thermostat temperature; trigger automations. "
            "Actions are subject to playbook approval tiers — some execute immediately, others require user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain":    {"type": "string", "description": "HA domain (e.g. 'switch', 'light', 'climate', 'cover', 'automation')"},
                "service":   {"type": "string", "description": "Service to call (e.g. 'turn_on', 'turn_off', 'toggle', 'set_temperature', 'trigger')"},
                "entity_id": {"type": "string", "description": "Full HA entity ID"},
                "data":      {"type": "object", "description": "Optional service data (e.g. {'temperature': 22, 'brightness': 200})"},
            },
            "required": ["domain", "service", "entity_id"],
        },
    },
    {
        "name": "send_notification",
        "description": (
            "Send a push notification to Fraser's phone (or other registered HA companion app devices). "
            "Use for alerts, reminders, and proactive updates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification body text"},
                "title":   {"type": "string", "description": "Notification title (default: 'Alf-E')"},
                "target":  {"type": "string", "description": "Optional specific notify service (e.g. 'notify.mobile_app_fraser_iphone'). Omit to notify all devices."},
            },
            "required": ["message"],
        },
    },
    # ── Memory & Context ───────────────────────────────────────────────
    {
        "name": "search_memory",
        "description": (
            "Search past conversations for relevant messages. "
            "Use this to recall what was discussed previously, find decisions made, or look up information the user mentioned before."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":   {"type": "string", "description": "Search term or phrase to look for in past conversations"},
                "limit":   {"type": "integer", "description": "Max results to return (default 10)"},
                "user_id": {"type": "string", "description": "Filter by user ID (default: current user)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Store a persistent fact or piece of context that should be remembered across conversations. "
            "Use this when the user mentions something important to retain: preferences, decisions, dates, goals. "
            "Examples: 'Fraser prefers solar charging over grid', 'Tesla service due May 2026', 'pool pump runs 6am-10am'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Category for this fact (e.g. 'energy', 'tesla', 'household', 'preferences', 'device_trader')"},
                "key":    {"type": "string", "description": "Short identifier for this fact (e.g. 'tesla_service_due', 'preferred_temp')"},
                "value":  {"type": "string", "description": "The fact or value to store"},
            },
            "required": ["domain", "key", "value"],
        },
    },
    {
        "name": "recall",
        "description": (
            "Retrieve stored facts from memory by domain. "
            "Use this to recall persistent context that was previously remembered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain to retrieve facts from (e.g. 'energy', 'tesla', 'household'). Omit for all stored facts."},
            },
        },
    },
    {
        "name": "get_cost_summary",
        "description": "Get a summary of Alf-E's API usage and cost over the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to summarise (default 30)"},
            },
        },
    },
    # ── Web ────────────────────────────────────────────────────────────
    {
        "name": "web_search",
        "description": (
            "Search the web using DuckDuckGo. Use for current information, prices, weather, news, product research. "
            "Returns a list of results with title, URL, and snippet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results to return (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch and read the content of a specific URL. "
            "Use after web_search to read a specific page, or when given a direct URL. "
            "Returns the page text content (HTML stripped)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url":          {"type": "string", "description": "Full URL to fetch"},
                "max_chars":    {"type": "integer", "description": "Max characters to return (default 4000)"},
            },
            "required": ["url"],
        },
    },
    # ── Files ──────────────────────────────────────────────────────────
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Only works within safe paths defined in the playbook. "
            "Use for reading reports, config files, notes, data files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file. Only works within safe paths defined in the playbook. "
            "Use for saving reports, notes, analysis output. Will create the file if it doesn't exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
                "append":  {"type": "boolean", "description": "If true, append to existing file instead of overwriting (default false)"},
            },
            "required": ["path", "content"],
        },
    },
    # ── Self-awareness ─────────────────────────────────────────────────
    {
        "name": "get_status",
        "description": "Get Alf-E's own system status: what's connected, which model is running, playbook details, message count.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_playbook_info",
        "description": "Get the full playbook configuration: sensors, actions, boundaries, users, scheduled ops, connectors.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Self-building ──────────────────────────────────────────────────
    {
        "name": "propose_connector",
        "description": (
            "Draft a new connector that Alf-E can wire itself into. "
            "Use when the user asks to connect a new service (Gmail, Google Calendar, Tesla API, weather, camera, etc.). "
            "Generates a BaseConnector subclass scaffold with all required methods. "
            "The user reviews the generated code in the UI and approves or rejects it. "
            "On approval: code is written to engine/connectors/<id>.py, git committed, server restarted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "snake_case ID for this connector, e.g. 'gmail', 'tesla', 'bom_weather'",
                },
                "connector_type": {
                    "type": "string",
                    "description": "Category, e.g. 'email', 'vehicle', 'weather', 'security', 'calendar'",
                },
                "description": {
                    "type": "string",
                    "description": "One-line description of what this connector does",
                },
                "tools": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":        {"type": "string"},
                            "description": {"type": "string"},
                            "params":      {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "description": "List of tool names and descriptions this connector will expose",
                },
                "auth_method": {
                    "type": "string",
                    "description": "How it authenticates, e.g. 'oauth2', 'api_key', 'bearer_token', 'none'",
                },
                "env_vars": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Environment variable names needed, e.g. ['GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET']",
                },
            },
            "required": ["connector_id", "description"],
        },
    },
]


class Agent:
    """Alf-E agentic assistant with full tool suite."""

    def __init__(
        self,
        router: ModelRouter,
        ha=None,                    # legacy HAConnector — kept during migration
        memory: Memory = None,
        playbook: PlaybookConfig = None,
        registry=None,              # ConnectorRegistry — new architecture
    ):
        self.router = router
        self.ha = ha
        self.memory = memory
        self.playbook = playbook
        self.registry = registry    # ConnectorRegistry or None
        self.pending_approvals: list[dict] = []
        self.last_model_used: str = ""

    # ── Tool List ──────────────────────────────────────────────────────

    def _get_tools(self) -> list[dict]:
        """Return full tool list: core tools + any tools from the connector registry."""
        all_tools = list(TOOLS)  # core non-connector tools
        if self.registry:
            all_tools.extend(self.registry.get_anthropic_tools())
        return all_tools

    def _build_tool_docs(self) -> str:
        """Build a dynamic tool reference from all available tools (core + registry)."""
        lines = []
        for tool in self._get_tools():
            name = tool["name"]
            desc = tool.get("description", "")
            # Truncate long descriptions for the system prompt
            if len(desc) > 120:
                desc = desc[:117] + "..."
            lines.append(f"  • {name:30s} — {desc}")
        return "\n".join(lines)

    # ── System Prompt ──────────────────────────────────────────────────

    def get_system_prompt(self, user_id: str = "default") -> str:
        pb = self.playbook
        personality = pb.personality_prompt if pb and pb.personality_prompt else ""

        sensors_info = ""
        if pb and pb.sensors:
            sensors_info = "\n".join(f"  - {k}: entity {v}" for k, v in pb.sensors.items())

        user_info = ""
        if pb:
            user = pb.get_user(user_id)
            if user:
                user_info = f"\nCurrent user: {user.name} (role: {user.role.value})"

        safe_paths = ""
        if pb and pb.security.safe_file_roots:
            safe_paths = ", ".join(pb.security.safe_file_roots)
        else:
            safe_paths = "/data/alfe_notes (default)"

        ha_status = "connected" if (self.ha or (self.registry and "ha" in self.registry.connector_ids())) else "NOT connected"

        connector_info = ""
        if self.registry and self.registry.connector_ids():
            cids = self.registry.connector_ids()
            connector_info = f"\nLOADED CONNECTORS: {', '.join(cids)} ({self.registry.tool_count()} tools total)"

        # Build dynamic tool docs from all available tools
        tool_docs = self._build_tool_docs()

        # Build boundaries info
        boundaries_info = ""
        if pb and pb.boundaries:
            boundaries_info = "\n\nSAFETY BOUNDARIES (never violate these):\n"
            boundaries_info += "\n".join(f"  • {b.id}: {b.description} (limit: {b.limit} {b.unit})" for b in pb.boundaries)

        # Build energy config info
        energy_info = ""
        if pb and pb.energy and pb.energy.peak_rate > 0:
            e = pb.energy
            energy_info = (
                f"\n\nENERGY TARIFFS ({e.currency}):\n"
                f"  Peak: ${e.peak_rate}/kWh ({e.peak_start}–{e.peak_end})\n"
                f"  Off-peak: ${e.offpeak_rate}/kWh\n"
                f"  Feed-in: ${e.feed_in_rate}/kWh\n"
                f"  Solar capacity: {e.solar_capacity_kw} kW"
            )
            if e.battery_capacity_kwh > 0:
                energy_info += f"\n  Battery: {e.battery_capacity_kwh} kWh (min SOC: {e.battery_min_soc}%)"

        return f"""{personality if personality else '''You are Alf-C: Fraser Cole's personal AI agent running 24/7 on a mini PC in Ormeau, Brisbane.

PERSONALITY:
- Cheeky, Australian (hints of NZ and Canadian)
- Dry humour and dad jokes mandatory
- Honest about limitations — black and white, no sugarcoating
- Builds things alongside Fraser, not just for him
- Talking to a mate, not a customer'''}

═══════════════════════════════════════════
YOU ARE AN AGENT — NOT A CHATBOT.
You have tools. Use them. Don't guess when you can look it up.
═══════════════════════════════════════════

AVAILABLE TOOLS:
{tool_docs}

PRE-CONFIGURED SENSORS:
{sensors_info or "  (none configured)"}
{connector_info}{user_info}{boundaries_info}{energy_info}

OPERATING RULES:
- Always use tools to get real data — never guess sensor values, entity states, or current facts.
- For home/energy questions: call get_all_sensors first, then reason about the data.
- For trend/history questions: use get_ha_history with the correct entity_id.
- For anything unknown in HA: use list_ha_entities to discover, then get_ha_entity to read.
- For current info (weather, prices, news): use web_search.
- When the user mentions something worth remembering: use remember() without being asked.
- When the user asks to connect a new service: use propose_connector to draft the code.
- Be transparent about approval tiers — tell the user when an action needs their confirmation.
- Never expose API keys or tokens.
- Keep responses concise and natural — you're talking to a mate."""

    # ── Tool Execution ─────────────────────────────────────────────────

    def _safe_path(self, path: str) -> Optional[Path]:
        """Return a resolved Path if within allowed roots, else None."""
        pb = self.playbook
        safe_roots = pb.security.safe_file_roots if pb else []
        if not safe_roots:
            # Default safe path when running as HA add-on
            safe_roots = ["/data/alfe_notes"]

        resolved = Path(path).resolve()
        for root in safe_roots:
            if str(resolved).startswith(str(Path(root).resolve())):
                return resolved
        return None

    # Tool name → domain mapping for role enforcement
    _TOOL_DOMAINS: dict[str, str] = {
        "get_sensor": "energy", "get_all_sensors": "energy", "get_ha_entity": "home",
        "list_ha_entities": "home", "get_ha_history": "energy", "ha_service_call": "home",
        "send_notification": "home", "search_memory": "memory", "remember": "memory",
        "recall": "memory", "get_cost_summary": "admin", "web_search": "web",
        "web_fetch": "web", "read_file": "files", "write_file": "files",
        "get_status": "admin", "get_playbook_info": "admin", "propose_connector": "admin",
    }

    def _check_user_permission(self, tool_name: str, user_id: str) -> Optional[str]:
        """Return an error string if the user lacks permission, else None."""
        if not self.playbook:
            return None
        user = self.playbook.get_user(user_id)
        if not user:
            return None  # unknown users get default access
        if user.role.value == "owner":
            return None  # owner can do everything

        # Check tool domain against user's permitted_domains
        permitted = user.permitted_domains
        if "*" in permitted:
            return None

        # Determine domain: check static map first, then connector-provided tools
        domain = self._TOOL_DOMAINS.get(tool_name)
        if not domain and self.registry and self.registry.has_tool(tool_name):
            # Connector tools: use the connector_id as domain (e.g. "ha", "gmail")
            connector_id = self.registry._tool_map.get(tool_name, "")
            domain = connector_id or "external"

        if domain and domain not in permitted:
            return f"Permission denied: your role ({user.role.value}) doesn't have access to '{domain}' tools."
        return None

    def _execute_tool(self, name: str, inp: dict, user_id: str = "default") -> str:

        # ── Role enforcement ───────────────────────────────────────────
        perm_error = self._check_user_permission(name, user_id)
        if perm_error:
            return perm_error

        # ── Connector Registry (new architecture) ──────────────────────
        # Try the registry first — it handles all connector-provided tools.
        # Falls through to legacy handlers if not handled by registry.
        if self.registry and self.registry.has_tool(name):
            result = self.registry.execute(name, inp, user_id)
            if result.requires_approval:
                self.pending_approvals.append(result.approval_payload or {"tool": name, "inp": inp})
                return f"Queued for approval: {name}"
            return result.content

        # ── Self-building: propose_connector ───────────────────────────
        if name == "propose_connector":
            return self._handle_propose_connector(inp, user_id)

        # ── HA: pre-configured sensors (legacy) ───────────────────────
        if name == "get_sensor":
            sensor_name = inp["sensor_name"]
            if not self.playbook or sensor_name not in self.playbook.sensors:
                available = list(self.playbook.sensors.keys()) if self.playbook else []
                return f"Unknown sensor '{sensor_name}'. Available: {available}"
            if not self.ha:
                return "Home Assistant is not connected."
            entity_id = self.playbook.sensors[sensor_name]
            value = self.ha.get_numeric_value(entity_id)
            return f"{sensor_name} = {value}" if value is not None else f"{sensor_name} is unavailable."

        if name == "get_all_sensors":
            if not self.playbook or not self.playbook.sensors:
                return "No sensors configured in playbook."
            if not self.ha:
                return "Home Assistant is not connected."
            data = self.ha.get_sensor_batch(self.playbook.sensors)
            lines = [f"  {k}: {v if v is not None else 'unavailable'}" for k, v in data.items()]
            return "\n".join(lines)

        # ── HA: full entity access ─────────────────────────────────────
        if name == "get_ha_entity":
            if not self.ha:
                return "Home Assistant is not connected."
            entity = self.ha.get_entity_full(inp["entity_id"])
            if not entity:
                return f"Entity '{inp['entity_id']}' not found or unavailable."
            attrs = entity.get("attributes", {})
            attr_str = "\n".join(f"    {k}: {v}" for k, v in attrs.items()) if attrs else "    (none)"
            return (
                f"entity_id:    {entity['entity_id']}\n"
                f"state:        {entity['state']}\n"
                f"last_changed: {entity.get('last_changed', 'unknown')}\n"
                f"attributes:\n{attr_str}"
            )

        if name == "list_ha_entities":
            if not self.ha:
                return "Home Assistant is not connected."
            domain = inp.get("domain")
            entities = self.ha.list_entities(domain)
            if not entities:
                return f"No entities found{f' for domain {domain}' if domain else ''}."
            lines = [f"  {e['entity_id']} [{e['state']}] — {e['friendly_name']}" for e in entities]
            summary = f"{len(entities)} entities{f' in domain {domain}' if domain else ''}:\n"
            # Cap output to avoid flooding context
            if len(lines) > 60:
                return summary + "\n".join(lines[:60]) + f"\n  ... and {len(lines)-60} more (use domain filter to narrow down)"
            return summary + "\n".join(lines)

        if name == "get_ha_history":
            if not self.ha:
                return "Home Assistant is not connected."
            entity_id = inp["entity_id"]
            hours = min(int(inp.get("hours", 24)), 168)
            stats = self.ha.get_history_stats(entity_id, hours)
            if "error" in stats:
                return f"History for {entity_id}: {stats['error']}"
            return (
                f"History for {entity_id} (last {hours}h):\n"
                f"  samples: {stats['samples']}\n"
                f"  min:     {stats['min']}\n"
                f"  max:     {stats['max']}\n"
                f"  avg:     {stats['avg']}\n"
                f"  first:   {stats['first']}\n"
                f"  last:    {stats['last']}"
            )

        # ── HA: service call ───────────────────────────────────────────
        if name == "ha_service_call":
            if not self.ha:
                return "Home Assistant is not connected."
            entity_id = inp["entity_id"]
            domain    = inp["domain"]
            service   = inp["service"]
            data      = inp.get("data", {})

            approval_tier = ActionApproval.confirm
            if self.playbook:
                for action in self.playbook.actions:
                    entity_match = (
                        action.entity == entity_id
                        or action.entity_pattern == "*"
                        or action.entity_pattern == entity_id.split(".")[0]
                    )
                    if entity_match:
                        approval_tier = action.approval
                        break

            if approval_tier == ActionApproval.autonomous:
                success = self.ha.call_service(domain, service, entity_id, data or None)
                return f"Done: {domain}.{service} on {entity_id} ({'ok' if success else 'failed'})."

            self.pending_approvals.append({
                "type": "ha_service_call",
                "domain": domain,
                "service": service,
                "entity_id": entity_id,
                "data": data,
            })
            return f"Queued for approval: {domain}.{service} on {entity_id}."

        # ── HA: notification ───────────────────────────────────────────
        if name == "send_notification":
            if not self.ha:
                return "Home Assistant is not connected — cannot send notification."
            success = self.ha.send_notification(
                message=inp["message"],
                title=inp.get("title", "Alf-E"),
                target=inp.get("target"),
            )
            return "Notification sent." if success else "Notification failed — check HA companion app is installed."

        # ── Memory: search ─────────────────────────────────────────────
        if name == "search_memory":
            if not self.memory:
                return "Memory not available."
            query = inp["query"].lower()
            limit = int(inp.get("limit", 10))
            uid   = inp.get("user_id", user_id)
            all_msgs = self.memory.load_messages(user_id=uid, limit=200)
            matches = [
                m for m in all_msgs
                if query in m.get("content", "").lower()
            ][:limit]
            if not matches:
                return f"No past messages found matching '{inp['query']}'."
            lines = [f"  [{m['role']}]: {m['content'][:200]}" for m in matches]
            return f"Found {len(matches)} matching message(s):\n" + "\n".join(lines)

        # ── Memory: remember ───────────────────────────────────────────
        if name == "remember":
            if not self.memory:
                return "Memory not available."
            self.memory.set_context(
                domain=inp["domain"],
                key=inp["key"],
                value=inp["value"],
                source=f"user:{user_id}",
            )
            return f"Remembered: [{inp['domain']}] {inp['key']} = {inp['value']}"

        # ── Memory: recall ─────────────────────────────────────────────
        if name == "recall":
            if not self.memory:
                return "Memory not available."
            domain = inp.get("domain")
            facts  = self.memory.get_context(domain=domain)
            if not facts:
                return f"No stored facts found{f' for domain {domain}' if domain else ''}."
            lines = [f"  [{f['domain']}] {f['key']}: {f['value']}" for f in facts]
            return f"{len(facts)} stored fact(s):\n" + "\n".join(lines)

        # ── Memory: cost summary ───────────────────────────────────────
        if name == "get_cost_summary":
            if not self.memory:
                return "Memory not available."
            days    = int(inp.get("days", 30))
            summary = self.memory.get_cost_summary(days)
            return (
                f"Last {days} days:\n"
                f"  messages:      {summary['messages']}\n"
                f"  tokens in:     {summary['tokens_input']:,}\n"
                f"  tokens out:    {summary['tokens_output']:,}\n"
                f"  cost (USD):    ${summary['cost_usd']:.4f}"
            )

        # ── Web: search ────────────────────────────────────────────────
        if name == "web_search":
            query       = inp["query"]
            max_results = int(inp.get("max_results", 5))
            try:
                # DuckDuckGo instant answer API (no key required)
                resp = httpx.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
                    timeout=10,
                    headers={"User-Agent": "Alf-E/2.0"},
                )
                data = resp.json()

                results = []

                # Abstract (featured snippet)
                if data.get("Abstract"):
                    results.append({
                        "title":   data.get("Heading", "Featured"),
                        "url":     data.get("AbstractURL", ""),
                        "snippet": data["Abstract"],
                    })

                # Related topics
                for topic in data.get("RelatedTopics", [])[:max_results]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({
                            "title":   topic.get("Text", "")[:80],
                            "url":     topic.get("FirstURL", ""),
                            "snippet": topic.get("Text", ""),
                        })
                    if len(results) >= max_results:
                        break

                if not results:
                    return f"No results found for '{query}'. Try web_fetch with a specific URL instead."

                lines = []
                for i, r in enumerate(results[:max_results], 1):
                    lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}")
                return f"Search results for '{query}':\n\n" + "\n\n".join(lines)

            except Exception as e:
                logger.error(f"Web search error: {e}")
                return f"Web search failed: {e}"

        # ── Web: fetch ─────────────────────────────────────────────────
        if name == "web_fetch":
            url       = inp["url"]
            max_chars = int(inp.get("max_chars", 4000))
            try:
                resp = httpx.get(
                    url,
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "Alf-E/2.0"},
                )
                resp.raise_for_status()

                # Strip HTML tags simply
                import re
                content = resp.text
                content = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.DOTALL | re.IGNORECASE)
                content = re.sub(r"<style[^>]*>.*?</style>",  " ", content, flags=re.DOTALL | re.IGNORECASE)
                content = re.sub(r"<[^>]+>", " ", content)
                content = re.sub(r"\s+", " ", content).strip()

                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n\n[truncated — {len(content)-max_chars} more chars]"

                return f"Content from {url}:\n\n{content}"

            except Exception as e:
                logger.error(f"Web fetch error: {e}")
                return f"Failed to fetch {url}: {e}"

        # ── Files: read ────────────────────────────────────────────────
        if name == "read_file":
            path = self._safe_path(inp["path"])
            if not path:
                return f"Access denied: '{inp['path']}' is outside safe paths."
            if not path.exists():
                return f"File not found: {inp['path']}"
            try:
                content = path.read_text(encoding="utf-8")
                return f"Contents of {path}:\n\n{content}"
            except Exception as e:
                return f"Error reading file: {e}"

        # ── Files: write ───────────────────────────────────────────────
        if name == "write_file":
            path = self._safe_path(inp["path"])
            if not path:
                return f"Access denied: '{inp['path']}' is outside safe paths."
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if inp.get("append") else "w"
                with open(path, mode, encoding="utf-8") as f:
                    f.write(inp["content"])
                action = "Appended to" if inp.get("append") else "Written"
                return f"{action} {path} ({len(inp['content'])} chars)."
            except Exception as e:
                return f"Error writing file: {e}"

        # ── Self: status ───────────────────────────────────────────────
        if name == "get_status":
            pb = self.playbook
            cost = self.memory.get_cost_summary(30) if self.memory else {}
            registry_info = (
                f"Registry:    {self.registry.connector_ids()} ({self.registry.tool_count()} tools)"
                if self.registry else "Registry:    not loaded"
            )
            lines = [
                f"Playbook:    {pb.name} v{pb.version}" if pb else "Playbook:    none",
                f"HA:          {'connected' if self.ha else 'not connected'}",
                registry_info,
                f"Model:       {self.last_model_used or 'not yet called'}",
                f"Messages:    {self.memory.get_message_count() if self.memory else 0}",
                f"Cost (30d):  ${cost.get('cost_usd', 0):.4f}",
                f"Providers:   {list(pb.llm.keys()) if pb else []}",
                f"Tools:       {len(self._get_tools())} available",
            ]
            return "\n".join(lines)

        if name == "get_playbook_info":
            if not self.playbook:
                return "No playbook loaded."
            pb = self.playbook
            lines = [
                f"Name:          {pb.name}",
                f"Version:       {pb.version}",
                f"Owner:         {pb.owner}",
                f"Timezone:      {pb.timezone}",
                f"Sensors:       {list(pb.sensors.keys())}",
                f"Actions:       {[a.id for a in pb.actions]} ({len(pb.actions)} defined)",
                f"Boundaries:    {[b.id for b in pb.boundaries]} ({len(pb.boundaries)} defined)",
                f"Scheduled ops: {[s.id for s in pb.scheduled_ops]}",
                f"Users:         {[u.name for u in pb.users]}",
                f"Connectors:    {[c.id for c in pb.connectors]}",
            ]
            if pb.energy and pb.energy.peak_rate > 0:
                e = pb.energy
                lines.append(f"Energy:        peak=${e.peak_rate}/kWh off-peak=${e.offpeak_rate}/kWh feed-in=${e.feed_in_rate}/kWh solar={e.solar_capacity_kw}kW")
            return "\n".join(lines)

        return f"Unknown tool: {name}"

    # ── Self-building: propose connector ────────────────────────────────

    def _handle_propose_connector(self, inp: dict, user_id: str) -> str:
        """Generate a BaseConnector subclass scaffold and queue it as a code proposal."""
        connector_id   = inp["connector_id"]
        description    = inp["description"]
        connector_type = inp.get("connector_type", "external_service")
        auth_method    = inp.get("auth_method", "api_key")
        env_vars       = inp.get("env_vars", [f"{connector_id.upper()}_API_KEY"])
        tools_spec     = inp.get("tools", [])

        class_name = "".join(w.capitalize() for w in connector_id.split("_")) + "Connector"

        # Build tool definitions
        tool_defs = []
        tool_handlers = []
        for t in tools_spec:
            tname = t.get("name", f"{connector_id}_action")
            tdesc = t.get("description", "")
            params = t.get("params", [])
            props = {p: {"type": "string"} for p in params}
            req = json.dumps(params)
            tool_defs.append(f"""            ToolDefinition(
                name="{tname}",
                description="{tdesc}",
                input_schema={{
                    "type": "object",
                    "properties": {json.dumps(props)},
                    "required": {req},
                }},
                approval_tier="autonomous",
            ),""")
            tool_handlers.append(f"""            elif name == "{tname}":
                # TODO: implement {tname}
                return ConnectorResult(success=False, content="Not yet implemented: {tname}")""")

        tools_block = "\n".join(tool_defs) if tool_defs else f"""            ToolDefinition(
                name="{connector_id}_status",
                description="Get the status of the {connector_id} connection.",
                input_schema={{"type": "object", "properties": {{}}}},
                approval_tier="autonomous",
            ),"""

        handlers_block = "\n".join(tool_handlers) if tool_handlers else f"""            elif name == "{connector_id}_status":
                return ConnectorResult(success=True, content="Connected to {connector_id}.")"""

        env_inits = "\n".join(
            f'        self._{v.lower()} = self._env("{v}") or ""'
            for v in env_vars
        )

        env_check = " and ".join(f"self._{v.lower()}" for v in env_vars) if env_vars else "True"

        code = f'''"""
Alf-E {connector_id} connector — auto-generated scaffold.
Generated by Alf-E self-building loop. Fill in TODO sections before approving.

Auth: {auth_method}
Env vars required: {", ".join(env_vars)}
"""

import logging
from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.connector.{connector_id}")


class {class_name}(BaseConnector):
    """{ description }"""

    connector_id   = "{connector_id}"
    connector_type = "{connector_type}"
    description    = "{description}"

    def __init__(self, config: dict):
        super().__init__(config)
{env_inits}

    def connect(self) -> bool:
        if not ({env_check}):
            logger.error("{connector_id}: missing required environment variables")
            return False
        # TODO: test the connection (e.g. make a lightweight API call)
        logger.info("{connector_id} connector connected.")
        return True

    def disconnect(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        # TODO: implement a lightweight health check
        return self.connected

    def get_tools(self) -> list[ToolDefinition]:
        return [
{tools_block}
        ]

    def execute_tool(self, name: str, inp: dict, user_id: str = "fraser") -> ConnectorResult:
        try:
            if False:
                pass
{handlers_block}
            else:
                return ConnectorResult(success=False, content=f"Unknown tool: {{name}}")
        except Exception as e:
            logger.error(f"{connector_id}.execute_tool({{name}}) error: {{e}}")
            return ConnectorResult(success=False, content=f"Error: {{e}}")
'''

        proposal = {
            "type":         "code_proposal",
            "connector_id": connector_id,
            "class_name":   class_name,
            "description":  description,
            "file_path":    f"engine/connectors/{connector_id}.py",
            "code":         code,
            "proposed_by":  user_id,
        }
        self.pending_approvals.append(proposal)

        tool_names = [t.get("name", "?") for t in tools_spec] if tools_spec else [f"{connector_id}_status"]
        return (
            f"Draft connector ready for review!\n\n"
            f"  File:    engine/connectors/{connector_id}.py\n"
            f"  Class:   {class_name}\n"
            f"  Tools:   {', '.join(tool_names)}\n"
            f"  Auth:    {auth_method}\n"
            f"  Env:     {', '.join(env_vars)}\n\n"
            f"The code is queued — approve it in the UI to write the file, commit, and reload."
        )

    # ── Chat (non-streaming) ───────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        user_id: str = "default",
        system_prompt: str = None,
    ) -> str:
        """Run the agentic tool-use loop. Returns final text response."""
        self.pending_approvals = []
        self.last_model_used = ""

        if system_prompt is None:
            system_prompt = self.get_system_prompt(user_id)

        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"] if isinstance(m["content"], str) else ""
                break

        config_name, config = self.router.route(last_user_msg)
        self.last_model_used = config.model

        if config.provider.value != "anthropic":
            try:
                return self.router.call_google(config, messages, system_prompt)
            except Exception as e:
                logger.error(f"Google call failed: {e}, falling back to default")
                config_name, config = self.router._pick_config("default")
                self.last_model_used = config.model

        loop_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        for _ in range(10):
            response = self.router.call_anthropic(
                config,
                messages=loop_messages,
                system=system_prompt,
                tools=self._get_tools(),
            )

            if response.stop_reason == "end_turn":
                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text = block.text
                        break
                if self.memory and response.usage:
                    cost = self.router.estimate_cost(config, response.usage.input_tokens, response.usage.output_tokens)
                    self.memory.save_message(
                        "assistant", text, user_id=user_id,
                        model_used=config.model, provider=config.provider.value,
                        tokens_input=response.usage.input_tokens,
                        tokens_output=response.usage.output_tokens,
                        cost_usd=cost,
                    )
                return text

            if response.stop_reason == "tool_use":
                loop_messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(block.name, block.input, user_id)
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                loop_messages.append({"role": "user", "content": tool_results})
                continue

            break

        return "I hit my thinking limit — try a more focused question."

    # ── Chat (streaming) ───────────────────────────────────────────────

    def stream_chat(
        self,
        messages: list[dict],
        user_id: str = "default",
        system_prompt: str = None,
    ):
        """Streaming agentic tool-use loop. Yields (type, content) tuples."""
        self.pending_approvals = []
        self.last_model_used = ""

        if system_prompt is None:
            system_prompt = self.get_system_prompt(user_id)

        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"] if isinstance(m["content"], str) else ""
                break

        config_name, config = self.router.route(last_user_msg)
        self.last_model_used = config.model

        if config.provider.value != "anthropic":
            try:
                text = self.router.call_google(config, messages, system_prompt)
                yield ("token", text)
                return
            except Exception as e:
                logger.error(f"Google call failed: {e}, falling back to default")
                config_name, config = self.router._pick_config("default")
                self.last_model_used = config.model

        api_key = os.getenv(config.api_key_env, "")
        if not api_key:
            yield ("token", "API key not configured.")
            return

        client = Anthropic(api_key=api_key)
        loop_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        for _ in range(10):
            full_text = ""

            with client.messages.stream(
                model=config.model,
                max_tokens=config.max_tokens,
                messages=loop_messages,
                system=system_prompt,
                tools=self._get_tools(),
            ) as stream:
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield ("token", chunk)
                final = stream.get_final_message()

            if final.stop_reason == "end_turn":
                if self.memory and final.usage:
                    cost = self.router.estimate_cost(config, final.usage.input_tokens, final.usage.output_tokens)
                    self.memory.save_message(
                        "assistant", full_text, user_id=user_id,
                        model_used=config.model, provider=config.provider.value,
                        tokens_input=final.usage.input_tokens,
                        tokens_output=final.usage.output_tokens,
                        cost_usd=cost,
                    )
                return

            if final.stop_reason == "tool_use":
                loop_messages.append({"role": "assistant", "content": final.content})
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        yield ("tool", block.name)
                        result = self._execute_tool(block.name, block.input, user_id)
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                loop_messages.append({"role": "user", "content": tool_results})
                continue

            break

        yield ("token", "I hit my thinking limit — try a more focused question.")




