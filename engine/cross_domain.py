"""
Alf-E CrossDomainEngine — Proactive reasoning across all connected services.

The crown jewel. Runs on a 15-minute cycle, pulls data from every source
Alf-E knows about, feeds it to the heavy model with a "find insights" prompt,
and pushes anything worth knowing as a notification.

"Your electricity usage pattern this week suggests someone left the pool pump
on overnight — that's $12 extra. Want me to check the schedule?"

This is what makes Alf-E indispensable.
"""

import asyncio
import logging
import json
from datetime import datetime
from typing import Optional

logger = logging.getLogger("alfe.cross_domain")


class CrossDomainEngine:
    """Proactive cross-domain reasoning engine."""

    def __init__(
        self,
        interval_minutes: int = 15,
        enabled: bool = True,
    ):
        self.interval = interval_minutes * 60  # seconds
        self.enabled = enabled
        self._agent = None
        self._memory = None
        self._registry = None
        self._playbook = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._insights: list[dict] = []  # recent insights for /api/insights
        self._last_run: str = ""
        self._run_count: int = 0

    def attach(self, agent, memory, registry, playbook) -> None:
        """Attach all the services the engine needs."""
        self._agent = agent
        self._memory = memory
        self._registry = registry
        self._playbook = playbook

    def start(self) -> None:
        """Start the background reasoning loop."""
        if not self.enabled:
            logger.info("CrossDomainEngine disabled")
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        logger.info(f"CrossDomainEngine started (every {self.interval // 60} min)")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("CrossDomainEngine stopped")

    async def _loop(self) -> None:
        # Wait 2 minutes after startup before first run (let connectors settle)
        await asyncio.sleep(120)

        while self._running:
            try:
                await self._reason()
            except Exception as e:
                logger.error(f"CrossDomainEngine error: {e}", exc_info=True)
            await asyncio.sleep(self.interval)

    async def _reason(self) -> None:
        """Core reasoning cycle: gather → analyse → act."""
        if not self._agent:
            return

        self._run_count += 1
        self._last_run = datetime.now().isoformat()
        logger.info(f"CrossDomainEngine cycle #{self._run_count}")

        # 1. Gather all available data
        snapshot = await self._gather_snapshot()
        if not snapshot:
            logger.info("No data available for reasoning — skipping")
            return

        # 2. Build the reasoning prompt
        prompt = self._build_prompt(snapshot)

        # 3. Call the model directly via router (bypasses tool-use loop intentionally —
        #    cross-domain reasoning only needs a plain completion, no tools).
        #    Pinned to Anthropic (Haiku): Gemini free tier 429s on this workload,
        #    and playbook heavy tier is Gemini while Fraser burns Google credits.
        router = self._agent.router
        _, config = router.pick_anthropic_fallback()
        system = self._system_prompt()
        msgs = [{"role": "user", "content": prompt}]

        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None, lambda: router.call_anthropic(config, msgs, system=system)
            )
            result = ""
            for block in raw.content:
                if hasattr(block, "text"):
                    result = block.text
                    break
        except Exception as e:
            logger.error(f"CrossDomain reasoning call failed ({config.provider.value}/{config.model}): {e}")
            return

        # 4. Parse and act on insights
        insights = self._parse_insights(result)
        if insights:
            await self._handle_insights(insights)
        else:
            logger.info("No actionable insights this cycle")

    async def _gather_snapshot(self) -> dict:
        """Pull data from all available sources into a single snapshot."""
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "sensors": {},
            "context_facts": [],
            "recent_messages": [],
            "connector_status": [],
        }

        # Sensor data via registry
        if self._registry and self._registry.has_tool("ha_get_batch"):
            try:
                sensors = self._playbook.sensors if self._playbook else {}
                if sensors:
                    result = self._registry.execute(
                        "ha_get_batch",
                        {"sensor_names": list(sensors.keys())},
                        "cross_domain_engine",
                    )
                    if result.success:
                        snapshot["sensors"] = result.content
            except Exception as e:
                logger.debug(f"Sensor gather failed: {e}")

        # If no registry sensor data, try legacy ha
        if not snapshot["sensors"] and self._agent and self._agent.ha:
            try:
                sensors = self._playbook.sensors if self._playbook else {}
                if sensors:
                    snapshot["sensors"] = self._agent.ha.get_sensor_batch(sensors)
            except Exception as e:
                logger.debug(f"Legacy sensor gather failed: {e}")

        # Context facts from memory
        if self._memory:
            try:
                snapshot["context_facts"] = self._memory.get_context()
            except Exception:
                pass

        # Recent user messages (last 24h summary)
        if self._memory:
            try:
                msgs = self._memory.load_messages(user_id="fraser", limit=20)
                snapshot["recent_messages"] = [
                    {"role": m["role"], "content": m["content"][:150]}
                    for m in msgs[-10:]  # last 10
                ]
            except Exception:
                pass

        # Connector status
        if self._registry:
            try:
                snapshot["connector_status"] = self._registry.get_status()
            except Exception:
                pass

        # Only reason if we have some data
        has_data = (
            snapshot["sensors"]
            or snapshot["context_facts"]
            or snapshot["recent_messages"]
        )
        return snapshot if has_data else {}

    def _system_prompt(self) -> str:
        """System prompt for the reasoning model."""
        energy = ""
        if self._playbook and self._playbook.energy and self._playbook.energy.peak_rate > 0:
            e = self._playbook.energy
            energy = (
                f"\nEnergy tariffs: peak ${e.peak_rate}/kWh ({e.peak_start}-{e.peak_end}), "
                f"off-peak ${e.offpeak_rate}/kWh, feed-in ${e.feed_in_rate}/kWh, "
                f"solar {e.solar_capacity_kw}kW"
            )

        return f"""You are Alf-E's Cross-Domain Reasoning Engine. Your job is ANTICIPATORY INTELLIGENCE.

You receive a snapshot of ALL data Alf-E knows about: sensors, stored facts, recent conversations,
and connector status. Your task is to find INSIGHTS the user hasn't asked about but needs to know.

Look for:
- Anomalies: sensors reading unusually high/low compared to stored baselines
- Cross-domain connections: solar + weather + Tesla + energy costs interacting
- Patterns: recurring issues, trending changes, seasonal effects
- Actionable opportunities: "charge Tesla now during off-peak" or "solar output dropping, expect rain"
- Problems: devices offline, unexpected consumption, automation failures
{energy}
RULES:
- Only report things that are GENUINELY useful or surprising
- Do NOT report normal operation ("solar is generating power during the day" — obviously)
- Do NOT make up data — only reason about what's in the snapshot
- Be specific: include numbers, entity names, time references
- Think like a household energy advisor crossed with a smart home expert

RESPONSE FORMAT — respond with a JSON array of insights:
[
  {{"priority": "high|medium|low", "title": "Short title", "detail": "Full explanation", "action": "Suggested action or null"}}
]

If nothing noteworthy: respond with an empty array []
Do NOT wrap in markdown code fences — just the raw JSON array."""

    def _build_prompt(self, snapshot: dict) -> str:
        """Build the user message from the gathered snapshot."""
        sections = []

        if snapshot.get("sensors"):
            sections.append(f"LIVE SENSORS:\n{snapshot['sensors']}")

        if snapshot.get("context_facts"):
            facts = snapshot["context_facts"][:30]  # cap at 30
            fact_lines = [f"  [{f['domain']}] {f['key']}: {f['value']}" for f in facts]
            sections.append(f"STORED FACTS ({len(facts)}):\n" + "\n".join(fact_lines))

        if snapshot.get("recent_messages"):
            msg_lines = [f"  [{m['role']}]: {m['content']}" for m in snapshot["recent_messages"]]
            sections.append(f"RECENT CONVERSATION:\n" + "\n".join(msg_lines))

        if snapshot.get("connector_status"):
            status_lines = [
                f"  {c.get('connector_id', '?')}: {'connected' if c.get('connected') else 'DISCONNECTED'}"
                for c in snapshot["connector_status"]
            ]
            sections.append(f"CONNECTOR STATUS:\n" + "\n".join(status_lines))

        now = datetime.now()
        sections.append(f"CURRENT TIME: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')})")

        return "\n\n".join(sections)

    def _parse_insights(self, raw: str) -> list[dict]:
        """Parse the model's response into a list of insight dicts."""
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        try:
            insights = json.loads(raw)
            if isinstance(insights, list):
                return [i for i in insights if isinstance(i, dict) and i.get("title")]
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse insights JSON: {raw[:200]}")
        return []

    async def _handle_insights(self, insights: list[dict]) -> None:
        """Store insights and send notifications for high-priority ones."""
        now = datetime.now().isoformat()

        for insight in insights:
            priority = insight.get("priority", "low")
            title = insight.get("title", "Insight")
            detail = insight.get("detail", "")
            action = insight.get("action")

            logger.info(f"Insight [{priority}]: {title}")

            # Store in memory
            entry = {
                "timestamp": now,
                "priority": priority,
                "title": title,
                "detail": detail,
                "action": action,
            }
            self._insights.append(entry)

            # Keep only last 50 insights
            if len(self._insights) > 50:
                self._insights = self._insights[-50:]

            # Store as context fact
            if self._memory:
                self._memory.set_context(
                    domain="insight",
                    key=f"insight_{self._run_count}_{title[:30].replace(' ', '_').lower()}",
                    value=f"[{priority}] {detail[:200]}",
                    source="cross_domain_engine",
                )

            # Push notification for high/medium priority
            if priority in ("high", "medium") and self._agent and self._agent.ha:
                try:
                    msg = f"{detail[:400]}"
                    if action:
                        msg += f"\n\nSuggested: {action}"
                    self._agent.ha.send_notification(
                        message=msg,
                        title=f"Alf-E: {title}",
                    )
                    logger.info(f"Notification sent for insight: {title}")
                except Exception as e:
                    logger.error(f"Failed to send insight notification: {e}")

    def get_insights(self, limit: int = 20) -> list[dict]:
        """Return recent insights for the API."""
        return list(reversed(self._insights[-limit:]))

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "interval_minutes": self.interval // 60,
            "run_count": self._run_count,
            "last_run": self._last_run,
            "insights_total": len(self._insights),
        }
