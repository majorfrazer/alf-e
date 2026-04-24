"""
Alf-E Scheduler — runs scheduled_ops from the playbook.

Uses asyncio background tasks (no external deps like APScheduler).
Checks the clock every 30 seconds, fires ops when their at_time matches.
Each op runs the agent's chat() with the op's prompt, then optionally
sends a push notification with the result.
"""

import asyncio
import logging
from datetime import datetime, time as dt_time
from typing import Optional

from engine.playbook_schema import ScheduledOpConfig

logger = logging.getLogger("alfe.scheduler")


class Scheduler:
    """Background scheduler for playbook scheduled_ops."""

    def __init__(self, ops: list[ScheduledOpConfig], timezone: str = "UTC"):
        self.ops = ops
        self.timezone = timezone
        self._task: Optional[asyncio.Task] = None
        self._fired_today: set[str] = set()  # op IDs that already ran today
        self._last_date: str = ""             # track date rollover to reset _fired_today
        self._agent = None                     # set via attach_agent()
        self._running = False

    def attach_agent(self, agent) -> None:
        """Attach the Alf-E agent for executing scheduled prompts."""
        self._agent = agent

    def set_ops(self, ops: list) -> None:
        """Replace the scheduled ops list at runtime (for playbook hot-reload)."""
        self.ops = ops
        self._fired_today.clear()
        logger.info(f"Scheduler ops reloaded: {len(ops)} op(s)")

    def start(self) -> None:
        """Start the background scheduler loop."""
        if not self.ops:
            logger.info("No scheduled ops configured — scheduler idle")
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())
        op_names = [f"{op.id} @ {op.at_time}" for op in self.ops]
        logger.info(f"Scheduler started: {', '.join(op_names)}")

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        """Main loop — checks every 30 seconds if any op is due."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Scheduler tick error: {e}")
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        """Check current time against all ops and fire any that are due."""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Reset fired set on date rollover
        if today_str != self._last_date:
            self._fired_today.clear()
            self._last_date = today_str

        current_time = now.strftime("%H:%M")

        for op in self.ops:
            if op.id in self._fired_today:
                continue

            if current_time == op.at_time:
                self._fired_today.add(op.id)
                logger.info(f"Firing scheduled op: {op.id} ({op.name})")
                asyncio.ensure_future(self._run_op(op))

    async def _run_op(self, op: ScheduledOpConfig) -> None:
        """Execute a scheduled op by running its prompt through the agent."""
        if not self._agent:
            logger.error(f"Cannot run op {op.id} — no agent attached")
            return

        if not op.prompt:
            logger.warning(f"Scheduled op {op.id} has no prompt — skipping")
            return

        try:
            # Run the agent chat in a thread to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._agent.chat(
                    messages=[{"role": "user", "content": op.prompt}],
                    user_id="scheduler",
                    system_prompt=f"You are running a scheduled task: {op.name}. {op.description}",
                ),
            )
            logger.info(f"Scheduled op {op.id} completed ({len(result)} chars)")

            # Send notification if configured
            if op.notify_on_complete and self._agent.ha:
                self._agent.ha.send_notification(
                    message=result[:500],
                    title=f"Alf-E: {op.name}",
                )
                logger.info(f"Notification sent for op {op.id}")

        except Exception as e:
            logger.error(f"Scheduled op {op.id} failed: {e}")

    def get_status(self) -> dict:
        """Return scheduler status for /api/status."""
        return {
            "running": self._running,
            "ops_configured": len(self.ops),
            "ops_fired_today": list(self._fired_today),
            "ops": [
                {"id": op.id, "name": op.name, "at_time": op.at_time, "fired": op.id in self._fired_today}
                for op in self.ops
            ],
        }
