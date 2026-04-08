"""Status tool handlers: get_status, get_playbook_info."""

import logging

logger = logging.getLogger("alfe.tools.status")


def handle_get_status(agent) -> str:
    """Build system status string from agent state."""
    pb = agent.playbook
    cost = agent.memory.get_cost_summary(30) if agent.memory else {}
    registry_info = (
        f"Registry:    {agent.registry.connector_ids()} ({agent.registry.tool_count()} tools)"
        if agent.registry else "Registry:    not loaded"
    )
    lines = [
        f"Playbook:    {pb.name} v{pb.version}" if pb else "Playbook:    none",
        f"HA:          {'connected' if agent.ha else 'not connected'}",
        registry_info,
        f"Model:       {agent.last_model_used or 'not yet called'}",
        f"Messages:    {agent.memory.get_message_count() if agent.memory else 0}",
        f"Cost (30d):  ${cost.get('cost_usd', 0):.4f}",
        f"Providers:   {list(pb.llm.keys()) if pb else []}",
        f"Tools:       {len(agent._get_tools())} available",
    ]
    return "\n".join(lines)


def handle_get_playbook_info(playbook) -> str:
    """Build playbook info string."""
    if not playbook:
        return "No playbook loaded."
    pb = playbook
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
