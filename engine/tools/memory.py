"""Memory tool handlers: search_memory, remember, recall, get_cost_summary."""

import logging

logger = logging.getLogger("alfe.tools.memory")


def handle_search_memory(inp: dict, memory, user_id: str) -> str:
    if not memory:
        return "Memory not available."
    query = inp["query"].lower()
    limit = int(inp.get("limit", 10))
    uid = inp.get("user_id", user_id)
    all_msgs = memory.load_messages(user_id=uid, limit=200)
    matches = [
        m for m in all_msgs
        if query in m.get("content", "").lower()
    ][:limit]
    if not matches:
        return f"No past messages found matching '{inp['query']}'."
    lines = [f"  [{m['role']}]: {m['content'][:200]}" for m in matches]
    return f"Found {len(matches)} matching message(s):\n" + "\n".join(lines)


def handle_remember(inp: dict, memory, user_id: str) -> str:
    if not memory:
        return "Memory not available."
    memory.set_context(
        domain=inp["domain"],
        key=inp["key"],
        value=inp["value"],
        source=f"user:{user_id}",
    )
    return f"Remembered: [{inp['domain']}] {inp['key']} = {inp['value']}"


def handle_recall(inp: dict, memory) -> str:
    if not memory:
        return "Memory not available."
    domain = inp.get("domain")
    facts = memory.get_context(domain=domain)
    if not facts:
        return f"No stored facts found{f' for domain {domain}' if domain else ''}."
    lines = [f"  [{f['domain']}] {f['key']}: {f['value']}" for f in facts]
    return f"{len(facts)} stored fact(s):\n" + "\n".join(lines)


def handle_get_cost_summary(inp: dict, memory) -> str:
    if not memory:
        return "Memory not available."
    days = int(inp.get("days", 30))
    summary = memory.get_cost_summary(days)
    return (
        f"Last {days} days:\n"
        f"  messages:      {summary['messages']}\n"
        f"  tokens in:     {summary['tokens_input']:,}\n"
        f"  tokens out:    {summary['tokens_output']:,}\n"
        f"  cost (USD):    ${summary['cost_usd']:.4f}"
    )
