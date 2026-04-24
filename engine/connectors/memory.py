"""
Alf-E Memory connector — semantic long-term memory tools.

Exposes Qdrant vector search to the agent as callable tools.

TOOLS EXPOSED:
  memory_search   — find semantically similar past conversations
  memory_remember — explicitly store a fact or note for future recall
  memory_stats    — show how many memories are stored
"""

import logging
from engine.connectors.base import BaseConnector, ToolDefinition, ConnectorResult

logger = logging.getLogger("alfe.connector.memory")


class MemoryConnector(BaseConnector):
    connector_id   = "memory"
    connector_type = "memory"
    description    = "Semantic long-term memory — store and recall past conversations via Qdrant"

    def connect(self) -> bool:
        try:
            from engine.vector_memory import get_collection_info
            info = get_collection_info()
            status = info.get("status", "unavailable")
            if status == "unavailable":
                logger.warning("Qdrant not reachable — memory connector in degraded mode")
            else:
                pts = info.get("points", 0)
                logger.info(f"Memory connector ready: {pts} memories stored")
            return True  # Always load — degrades gracefully
        except Exception as e:
            logger.warning(f"Memory connector load warning: {e}")
            return True

    def disconnect(self) -> None:
        pass

    def health_check(self) -> bool:
        try:
            from engine.vector_memory import get_collection_info
            return get_collection_info().get("status") != "unavailable"
        except Exception:
            return False

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="memory_search",
                description=(
                    "Search Alf-E's long-term semantic memory for past conversations related to a topic. "
                    "Use this when the user asks about something that may have been discussed before, "
                    "or when you want context from previous sessions. Returns the most relevant exchanges."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for — use natural language, not keywords",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 5)",
                        },
                    },
                    "required": ["query"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="memory_remember",
                description=(
                    "Explicitly store an important fact, preference, or note into long-term memory. "
                    "Use this when the user shares something worth remembering across sessions — "
                    "e.g. preferences, decisions, recurring context, or personal details."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact or note to remember",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags to categorise this memory (e.g. ['preference', 'energy'])",
                        },
                    },
                    "required": ["fact"],
                },
                approval_tier="autonomous",
            ),
            ToolDefinition(
                name="memory_stats",
                description="Show how many memories are stored in Alf-E's long-term memory.",
                input_schema={"type": "object", "properties": {}},
                approval_tier="autonomous",
            ),
        ]

    def execute_tool(self, name: str, inp: dict, user_id: str = "fraser") -> ConnectorResult:
        handlers = {
            "memory_search":   self._search,
            "memory_remember": self._remember,
            "memory_stats":    self._stats,
        }
        handler = handlers.get(name)
        if not handler:
            return ConnectorResult(success=False, content=f"Unknown memory tool: {name!r}")
        try:
            return handler(inp, user_id)
        except Exception as e:
            logger.error(f"Memory {name} failed: {e}")
            return ConnectorResult(success=False, content=f"Memory error: {e}")

    def _search(self, inp: dict, user_id: str) -> ConnectorResult:
        from engine.vector_memory import search_memory
        query = inp.get("query", "").strip()
        limit = int(inp.get("limit") or 5)
        if not query:
            return ConnectorResult(success=False, content="query is required")

        results = search_memory(query=query, user_id=user_id, limit=limit)
        if not results:
            return ConnectorResult(
                success=True,
                content="No relevant memories found. This may be a new topic or Qdrant is warming up.",
            )

        lines = [f"Found {len(results)} relevant memories:\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r['timestamp']} (relevance {r['score']})")
            lines.append(f"  You asked: {r['user_msg'][:150]}")
            lines.append(f"  I said:    {r['assistant_msg'][:300]}")
            if r["tags"]:
                lines.append(f"  Tags: {', '.join(r['tags'])}")
            lines.append("")
        return ConnectorResult(success=True, content="\n".join(lines))

    def _remember(self, inp: dict, user_id: str) -> ConnectorResult:
        from engine.vector_memory import store_fact
        fact = inp.get("fact", "").strip()
        tags = inp.get("tags") or []
        if not fact:
            return ConnectorResult(success=False, content="fact is required")

        ok = store_fact(fact=fact, user_id=user_id, tags=tags)
        if ok:
            return ConnectorResult(success=True, content=f"Remembered: {fact}")
        return ConnectorResult(
            success=False,
            content="Could not store memory — Qdrant may be unavailable.",
        )

    def _stats(self, inp: dict, user_id: str) -> ConnectorResult:
        from engine.vector_memory import get_collection_info
        info = get_collection_info()
        if info.get("status") == "unavailable":
            return ConnectorResult(success=True, content="Qdrant is not reachable — memory unavailable.")
        pts = info.get("points", 0)
        return ConnectorResult(success=True, content=f"Long-term memory: {pts} memories stored.")
