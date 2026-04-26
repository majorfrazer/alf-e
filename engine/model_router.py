"""
Alf-E Model Router — Provider-agnostic AI model selection.

Routes each task to the most cost-efficient capable model based on:
- Task complexity classification
- Available providers from playbook config
- Cost per token
- Capability tags
"""

import os
import logging
from typing import Optional
from anthropic import Anthropic
import httpx

from engine.playbook_schema import LLMConfig

logger = logging.getLogger("alfe.router")


class ModelRouter:
    """Route tasks to the best available AI model."""

    def __init__(self, llm_configs: dict[str, LLMConfig]):
        """
        Args:
            llm_configs: Named LLM configs from playbook (e.g. {"default": ..., "fast": ..., "heavy": ...})
        """
        self.configs = llm_configs
        self._clients: dict[str, object] = {}

    def _get_tier(self, user_input: str) -> str:
        """Classify task into a routing tier: fast, default, or heavy."""
        text = user_input.lower()
        length = len(user_input)

        # FAST: simple lookups, status checks, short questions
        fast_signals = [
            "status", "how's", "what's", "check", "what time",
            "weather", "turn on", "turn off", "toggle",
        ]
        if any(k in text for k in fast_signals) and length < 120:
            return "fast"

        # HEAVY: complex reasoning, code, analysis, multi-step
        heavy_signals = [
            "analyse", "analyze", "build", "implement", "design",
            "compare", "prepare", "scenario", "comprehensive",
            "rewrite", "refactor", "proposal", "architecture",
        ]
        if any(k in text for k in heavy_signals) or length > 300:
            return "heavy"

        # DEFAULT: everything else
        return "default"

    def _pick_config(self, tier: str) -> tuple[str, LLMConfig]:
        """Pick the best available config for a tier.

        Tries exact match first (e.g. "fast"), then falls back to "default".
        """
        if tier in self.configs:
            return tier, self.configs[tier]
        if "default" in self.configs:
            return "default", self.configs["default"]
        # Last resort: first available
        name = next(iter(self.configs))
        return name, self.configs[name]

    def pick_anthropic_fallback(self, tier: str = "default") -> tuple[str, LLMConfig]:
        """Return the best Anthropic config for the given tier.

        Tries tier-specific Claude configs first (claude_fast / claude_default /
        claude_heavy), then any Anthropic config, then falls back to default.
        This preserves the fast→Haiku, default→Sonnet, heavy→Opus escalation even
        when Google/Ollama are the primary providers.
        """
        preference = {
            "fast":    ["claude_fast",    "claude_default", "claude_heavy"],
            "default": ["claude_default", "claude_heavy",   "claude_fast"],
            "heavy":   ["claude_heavy",   "claude_default", "claude_fast"],
        }.get(tier, ["claude_default", "claude_heavy", "claude_fast"])

        for candidate in preference:
            if candidate in self.configs and self.configs[candidate].provider.value == "anthropic":
                return candidate, self.configs[candidate]

        # Fall through: any Anthropic config
        for name, cfg in self.configs.items():
            if cfg.provider.value == "anthropic":
                return name, cfg

        return self._pick_config("default")

    def route(self, user_input: str) -> tuple[str, LLMConfig]:
        """Route a user message to the best model.

        Returns:
            (config_name, LLMConfig)
        """
        tier = self._get_tier(user_input)
        name, config = self._pick_config(tier)
        logger.info(f"Routed to '{name}' ({config.provider.value}/{config.model}) for tier '{tier}'")
        return name, config

    def call_anthropic(
        self,
        config: LLMConfig,
        messages: list[dict],
        system: str = "",
        tools: list = None,
        max_tokens: int = None,
    ):
        """Make a Claude API call."""
        api_key = os.getenv(config.api_key_env, "")
        if not api_key:
            raise ValueError(
                f"API key not set — check {config.api_key_env} in your environment. "
                f"On HA Green: set it in the add-on Configuration tab. "
                f"On N95: set it in .env"
            )

        client = Anthropic(api_key=api_key)

        kwargs = {
            "model": config.model,
            "max_tokens": max_tokens or config.max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if config.thinking_budget_tokens:
            # Claude 4+ uses adaptive thinking; Claude 3.x used enabled+budget_tokens
            is_claude4 = any(f"claude-{n}-4" in config.model for n in ("opus", "sonnet", "haiku"))
            if is_claude4:
                kwargs["thinking"] = {"type": "adaptive"}
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": config.thinking_budget_tokens}

        return client.messages.create(**kwargs)

    def call_google(
        self,
        config: LLMConfig,
        messages: list[dict],
        system: str = "",
    ) -> str:
        """Make a Gemini API call via REST."""
        api_key = os.getenv(config.api_key_env, "")
        if not api_key:
            raise ValueError(f"API key not found in env: {config.api_key_env}")

        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": config.temperature,
                "maxOutputTokens": config.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.model}:generateContent",
            params={"key": api_key},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            logger.error(f"Gemini response parse error: {data}")
            return "Sorry, I couldn't process that."

    def call_ollama(
        self,
        config: LLMConfig,
        messages: list[dict],
        system: str = "",
    ) -> str:
        """Make an Ollama API call (local model fallback)."""
        base_url = os.getenv("OLLAMA_URL", "http://ollama:11434")

        # Convert messages to Ollama format
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        for msg in messages:
            content = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            ollama_messages.append({"role": msg["role"], "content": content})

        try:
            resp = httpx.post(
                f"{base_url}/api/chat",
                json={
                    "model": config.model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {
                        "temperature": config.temperature,
                        "num_predict": config.max_tokens,
                    },
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "Sorry, I couldn't process that.")
        except Exception as e:
            logger.error(f"Ollama call failed: {e}")
            return f"Local model unavailable: {e}"

    def estimate_cost(self, config: LLMConfig, tokens_in: int, tokens_out: int) -> float:
        """Estimate cost in USD for a call."""
        cost_in = (tokens_in / 1000) * config.cost_per_1k_input
        cost_out = (tokens_out / 1000) * config.cost_per_1k_output
        return round(cost_in + cost_out, 6)
