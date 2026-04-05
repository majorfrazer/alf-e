"""
Alf-E Agent — Agentic tool-use loop.

Talks to Claude (or routed model) with tools for:
- Reading HA sensors
- Calling HA services (turn on/off, etc)
- Querying playbook config
"""

import os
import logging
from anthropic import Anthropic
from engine.model_router import ModelRouter
from engine.ha_connector import HAConnector
from engine.memory import Memory
from engine.playbook_schema import PlaybookConfig, ActionApproval

logger = logging.getLogger("alfe.agent")

TOOLS = [
    {
        "name": "get_sensor",
        "description": (
            "Get a live sensor reading from Home Assistant. "
            "Returns the current numeric value for a named sensor from the playbook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_name": {
                    "type": "string",
                    "description": "Sensor key from the playbook (e.g. 'solar_watts', 'house_watts', 'tesla_soc')",
                }
            },
            "required": ["sensor_name"],
        },
    },
    {
        "name": "get_all_sensors",
        "description": "Get all configured sensor readings at once. Returns a dict of sensor_name → value.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ha_service_call",
        "description": (
            "Call a Home Assistant service to control a device. "
            "Examples: turn on a light, turn off a switch, set a thermostat temperature. "
            "This action requires user approval unless the playbook marks it as autonomous."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "HA domain (e.g. 'switch', 'light', 'climate', 'cover')",
                },
                "service": {
                    "type": "string",
                    "description": "Service to call (e.g. 'turn_on', 'turn_off', 'toggle', 'set_temperature')",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Full HA entity ID (e.g. 'switch.pool_pump', 'light.kitchen')",
                },
                "data": {
                    "type": "object",
                    "description": "Optional service data (e.g. {'temperature': 22, 'brightness': 200})",
                },
            },
            "required": ["domain", "service", "entity_id"],
        },
    },
    {
        "name": "get_playbook_info",
        "description": "Get information about the current playbook configuration: available sensors, actions, boundaries, and scheduled operations.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class Agent:
    """Alf-E agentic assistant with tool use."""

    def __init__(
        self,
        router: ModelRouter,
        ha: HAConnector = None,
        memory: Memory = None,
        playbook: PlaybookConfig = None,
    ):
        self.router = router
        self.ha = ha
        self.memory = memory
        self.playbook = playbook
        self.pending_approvals: list[dict] = []
        self.last_model_used: str = ""

    def get_system_prompt(self, user_id: str = "default") -> str:
        """Build the system prompt with playbook context."""
        pb = self.playbook
        name = pb.name if pb else "Alf-E"
        personality = pb.personality_prompt if pb and pb.personality_prompt else ""

        sensors_info = ""
        if pb and pb.sensors:
            sensors_info = "\n".join(f"  - {k}: {v}" for k, v in pb.sensors.items())

        user_info = ""
        if pb:
            user = pb.get_user(user_id)
            if user:
                user_info = f"\nCurrent user: {user.name} (role: {user.role.value})"

        return f"""You are {name}: a personal AI assistant.

{personality if personality else '''PERSONALITY:
- Cheeky, Australian (hints of NZ and Canadian)
- Dry humour and dad jokes mandatory
- Honest about limitations
- Helpful and practical — you're talking to a mate, not a customer'''}

YOU HAVE TOOLS — USE THEM:
- get_sensor: read a specific sensor from Home Assistant
- get_all_sensors: read all configured sensors at once
- ha_service_call: control a device (turn on/off, set temperature, etc.)
- get_playbook_info: check what's configured in the playbook

AVAILABLE SENSORS:
{sensors_info or "  (none configured)"}
{user_info}

RULES:
- When asked about home status, energy, etc — read the actual sensors, don't guess.
- When asked to control something, use ha_service_call.
- Be transparent about what you can and can't do.
- Never expose API keys or secrets.
- Keep responses concise and natural."""

    def _execute_tool(self, name: str, inp: dict) -> str:
        """Execute a tool call and return the result as a string."""

        if name == "get_sensor":
            sensor_name = inp["sensor_name"]
            if not self.playbook or sensor_name not in self.playbook.sensors:
                return f"Unknown sensor: {sensor_name}. Available: {list(self.playbook.sensors.keys()) if self.playbook else '(none)'}"
            if not self.ha:
                return "Home Assistant is not connected."
            entity_id = self.playbook.sensors[sensor_name]
            value = self.ha.get_numeric_value(entity_id)
            if value is not None:
                return f"{sensor_name} = {value}"
            return f"{sensor_name} is unavailable."

        if name == "get_all_sensors":
            if not self.playbook or not self.playbook.sensors:
                return "No sensors configured."
            if not self.ha:
                return "Home Assistant is not connected."
            data = self.ha.get_sensor_batch(self.playbook.sensors)
            lines = []
            for k, v in data.items():
                lines.append(f"  {k}: {v if v is not None else 'unavailable'}")
            return "\n".join(lines)

        if name == "ha_service_call":
            if not self.ha:
                return "Home Assistant is not connected."

            entity_id = inp["entity_id"]
            domain = inp["domain"]
            service = inp["service"]
            data = inp.get("data", {})

            # Determine approval tier from playbook
            approval_tier = ActionApproval.confirm  # safe default
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

            # Queue for approval
            self.pending_approvals.append({
                "type": "ha_service_call",
                "domain": domain,
                "service": service,
                "entity_id": entity_id,
                "data": data,
            })
            return f"Queued: {domain}.{service} on {entity_id} — awaiting approval."

        if name == "get_playbook_info":
            if not self.playbook:
                return "No playbook loaded."
            pb = self.playbook
            info = [
                f"Name: {pb.name}",
                f"Version: {pb.version}",
                f"Sensors: {list(pb.sensors.keys())}",
                f"Actions: {[a.id for a in pb.actions]}",
                f"Boundaries: {[b.id for b in pb.boundaries]}",
                f"Scheduled ops: {[s.id for s in pb.scheduled_ops]}",
                f"Users: {[u.name for u in pb.users]}",
            ]
            return "\n".join(info)

        return f"Unknown tool: {name}"

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

        # Get the latest user message for routing
        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"] if isinstance(m["content"], str) else ""
                break

        # Route to best model
        config_name, config = self.router.route(last_user_msg)
        self.last_model_used = config.model

        # Only Anthropic supports tool use in this version
        if config.provider.value != "anthropic":
            # Fall back to simple text completion for non-Anthropic
            try:
                text = self.router.call_google(config, messages, system_prompt)
                return text
            except Exception as e:
                logger.error(f"Google call failed: {e}, falling back to default")
                config_name, config = self.router._pick_config("default")

        # Anthropic tool-use loop
        loop_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        for _ in range(10):  # max 10 tool rounds
            response = self.router.call_anthropic(
                config,
                messages=loop_messages,
                system=system_prompt,
                tools=TOOLS,
            )

            if response.stop_reason == "end_turn":
                # Extract text and save usage
                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text = block.text
                        break

                # Track costs
                if self.memory and response.usage:
                    cost = self.router.estimate_cost(
                        config,
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                    )
                    self.memory.save_message(
                        "assistant", text,
                        user_id=user_id,
                        model_used=config.model,
                        provider=config.provider.value,
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
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                loop_messages.append({"role": "user", "content": tool_results})
                continue

            break

        return "I hit my thinking limit — try a more focused question."

    def stream_chat(
        self,
        messages: list[dict],
        user_id: str = "default",
        system_prompt: str = None,
    ):
        """Agentic tool-use loop with streaming final response.

        Yields (type, content) tuples:
          ("token", str)  — text chunk for the final response
          ("tool",  str)  — name of a tool being called (UI indicator)
        Returns when done; caller sends the done event.
        """
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

        # Non-Anthropic: no SDK streaming — yield full response as one chunk
        if config.provider.value != "anthropic":
            try:
                text = self.router.call_google(config, messages, system_prompt)
                yield ("token", text)
                return
            except Exception as e:
                logger.error(f"Google call failed: {e}, falling back to default")
                config_name, config = self.router._pick_config("default")
                self.last_model_used = config.model

        # Anthropic streaming tool-use loop
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
                tools=TOOLS,
            ) as stream:
                # stream.text_stream yields nothing during tool_use rounds
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield ("token", chunk)
                final = stream.get_final_message()

            if final.stop_reason == "end_turn":
                if self.memory and final.usage:
                    cost = self.router.estimate_cost(
                        config,
                        final.usage.input_tokens,
                        final.usage.output_tokens,
                    )
                    self.memory.save_message(
                        "assistant", full_text,
                        user_id=user_id,
                        model_used=config.model,
                        provider=config.provider.value,
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
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                loop_messages.append({"role": "user", "content": tool_results})
                continue

            break

        yield ("token", "I hit my thinking limit — try a more focused question.")
