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
import logging
from typing import Optional
from anthropic import Anthropic
from engine.model_router import ModelRouter
from engine.memory import Memory
from engine.playbook_schema import PlaybookConfig, ActionApproval

# Tool handlers — each module handles one category
from engine.tools.memory import handle_search_memory, handle_remember, handle_recall, handle_get_cost_summary
from engine.tools.web import handle_web_search, handle_web_fetch
from engine.tools.files import handle_read_file, handle_write_file, safe_path
from engine.tools.status import handle_get_status, handle_get_playbook_info
from engine.tools.self_build import handle_propose_connector

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
        ha=None,                    # legacy HAConnector — only used for server.py sensor endpoints
        memory: Memory = None,
        playbook: PlaybookConfig = None,
        registry=None,              # ConnectorRegistry — handles all tool execution
    ):
        self.router = router
        self.ha = ha
        self.memory = memory
        self.playbook = playbook
        self.registry = registry
        self.pending_approvals: list[dict] = []
        self.last_model_used: str = ""

    # ── Tool List ──────────────────────────────────────────────────────

    def _get_tools(self) -> list[dict]:
        """Return full tool list: core tools + any tools from the connector registry."""
        all_tools = list(TOOLS)
        if self.registry:
            all_tools.extend(self.registry.get_anthropic_tools())
        return all_tools

    def _build_tool_docs(self) -> str:
        """Build a dynamic tool reference from all available tools (core + registry)."""
        lines = []
        for tool in self._get_tools():
            name = tool["name"]
            desc = tool.get("description", "")
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

        connector_info = ""
        if self.registry and self.registry.connector_ids():
            cids = self.registry.connector_ids()
            connector_info = f"\nLOADED CONNECTORS: {', '.join(cids)} ({self.registry.tool_count()} tools total)"

        tool_docs = self._build_tool_docs()

        boundaries_info = ""
        if pb and pb.boundaries:
            boundaries_info = "\n\nSAFETY BOUNDARIES (never violate these):\n"
            boundaries_info += "\n".join(f"  • {b.id}: {b.description} (limit: {b.limit} {b.unit})" for b in pb.boundaries)

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

    # ── Role Enforcement ───────────────────────────────────────────────

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
            return None
        if user.role.value in ("owner", "admin"):
            return None

        permitted = user.permitted_domains
        if "*" in permitted:
            return None

        domain = self._TOOL_DOMAINS.get(tool_name)
        if not domain and self.registry and self.registry.has_tool(tool_name):
            connector_id = self.registry._tool_map.get(tool_name, "")
            domain = connector_id or "external"

        if domain and domain not in permitted:
            return f"Permission denied: your role ({user.role.value}) doesn't have access to '{domain}' tools."
        return None

    # ── Tool Dispatch ──────────────────────────────────────────────────

    def _execute_tool(self, name: str, inp: dict, user_id: str = "default") -> str:
        # Role enforcement
        perm_error = self._check_user_permission(name, user_id)
        if perm_error:
            return perm_error

        # Connector Registry — handles HA, Gmail, and all connector-provided tools
        if self.registry and self.registry.has_tool(name):
            result = self.registry.execute(name, inp, user_id)
            if result.requires_approval:
                self.pending_approvals.append(result.approval_payload or {"tool": name, "inp": inp})
                return f"Queued for approval: {name}"
            return result.content

        # Self-building
        if name == "propose_connector":
            return handle_propose_connector(inp, user_id, self.pending_approvals)

        # Memory tools
        if name == "search_memory":
            return handle_search_memory(inp, self.memory, user_id)
        if name == "remember":
            return handle_remember(inp, self.memory, user_id)
        if name == "recall":
            return handle_recall(inp, self.memory)
        if name == "get_cost_summary":
            return handle_get_cost_summary(inp, self.memory)

        # Web tools
        if name == "web_search":
            return handle_web_search(inp)
        if name == "web_fetch":
            return handle_web_fetch(inp)

        # File tools
        if name in ("read_file", "write_file"):
            safe_roots = self.playbook.security.safe_file_roots if self.playbook else []
            if name == "read_file":
                return handle_read_file(inp, safe_roots)
            return handle_write_file(inp, safe_roots)

        # Status tools
        if name == "get_status":
            return handle_get_status(self)
        if name == "get_playbook_info":
            return handle_get_playbook_info(self.playbook)

        return f"Unknown tool: {name}"

    # ── Chat (non-streaming) ──────────────────────────────────────────

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
        tier = config_name  # preserve original tier for fallback escalation

        # Google and Ollama don't support tool use — redirect to Anthropic for the
        # agentic loop. CrossDomain uses the router directly and bypasses this path.
        if config.provider.value in ("google", "ollama"):
            config_name, config = self.router.pick_anthropic_fallback(tier)

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
                    if getattr(block, "type", None) == "text":
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

    # ── Chat (streaming) ──────────────────────────────────────────────

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
        tier = config_name  # preserve for tier-aware fallback escalation
        self.last_model_used = config.model

        # Google and Ollama don't support tool use — redirect to Anthropic
        if config.provider.value in ("google", "ollama"):
            config_name, config = self.router.pick_anthropic_fallback(tier)
            self.last_model_used = config.model

        api_key = os.getenv(config.api_key_env, "")
        if not api_key:
            yield ("token", "API key not configured.")
            return

        client = Anthropic(api_key=api_key)
        loop_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        for _ in range(10):
            full_text = ""

            stream_kwargs = dict(
                model=config.model,
                max_tokens=config.max_tokens,
                messages=loop_messages,
                system=system_prompt,
                tools=self._get_tools(),
            )
            if config.thinking_budget_tokens:
                stream_kwargs["thinking"] = {"type": "enabled", "budget_tokens": config.thinking_budget_tokens}

            with client.messages.stream(**stream_kwargs) as stream:
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
                # Drop any pre-tool preamble — the frontend clears it on "clear"
                if full_text.strip():
                    yield ("clear", "")
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
