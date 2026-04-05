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
            raise ValueError(f"API key not found in env: {config.api_key_env}")

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

    def estimate_cost(self, config: LLMConfig, tokens_in: int, tokens_out: int) -> float:
        """Estimate cost in USD for a call."""
        cost_in = (tokens_in / 1000) * config.cost_per_1k_input
        cost_out = (tokens_out / 1000) * config.cost_per_1k_output
        return round(cost_in + cost_out, 6)
