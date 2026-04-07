"""
Alf-E Playbook Loader — Parse TOML playbooks into validated config.

Handles two TOML conventions:
- cole_sandbox style: [metadata], [llm.default], [actions.name] dict-of-tables
- device_trader style: [identity], [llm] flat, [[actions]] array-of-tables
"""

import tomllib
from pathlib import Path
from engine.playbook_schema import (
    PlaybookConfig,
    LLMConfig,
    HomeAssistantConfig,
    ConnectorConfig,
    EntityConfig,
    ActionConfig,
    UserConfig,
    NotificationConfig,
    BoundaryConfig,
    ScheduledOpConfig,
    SecurityConfig,
    SelfAssessmentConfig,
    EnergyConfig,
)


def load_playbook(path: Path) -> PlaybookConfig:
    """Load and validate a TOML playbook."""
    if not path.exists():
        raise FileNotFoundError(f"Playbook not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # ── Identity ─────────────────────────────────────────────────────────
    meta = data.get("metadata") or data.get("identity") or {}
    name = meta.get("name", data.get("name", "Alf-E"))
    description = meta.get("description", "")
    version = str(meta.get("version", "1.0"))
    owner = meta.get("owner", "")
    timezone = meta.get("timezone", "UTC")
    personality_prompt = meta.get("personality_prompt", "")

    # ── LLM ──────────────────────────────────────────────────────────────
    llm_dict: dict[str, LLMConfig] = {}
    if "llm" in data:
        raw_llm = data["llm"]
        if "provider" in raw_llm:
            llm_dict["default"] = LLMConfig(**raw_llm)
        else:
            for key, llm_data in raw_llm.items():
                llm_dict[key] = LLMConfig(**llm_data)

    # ── Home Assistant ────────────────────────────────────────────────────
    ha_config = None
    if "home_assistant" in data:
        ha_config = HomeAssistantConfig(**data["home_assistant"])

    # ── Sensors ───────────────────────────────────────────────────────────
    sensors: dict[str, str] = data.get("sensors", {})

    # ── Security ─────────────────────────────────────────────────────────
    security = SecurityConfig(**data.get("security", {}))

    # ── Energy ───────────────────────────────────────────────────────────
    energy = EnergyConfig(**data.get("energy", {}))

    # ── Self-assessment ──────────────────────────────────────────────────
    self_assessment = SelfAssessmentConfig(**data.get("self_assessment", {}))

    # ── Users ────────────────────────────────────────────────────────────
    users = [UserConfig(**u) for u in data.get("users", [])]

    # ── Notifications ────────────────────────────────────────────────────
    notifications = [NotificationConfig(**n) for n in data.get("notifications", [])]

    # ── Connectors ───────────────────────────────────────────────────────
    connectors = [ConnectorConfig(**c) for c in data.get("connectors", [])]

    # ── Entities ─────────────────────────────────────────────────────────
    entities = [EntityConfig(**e) for e in data.get("entities", [])]

    # ── Actions ──────────────────────────────────────────────────────────
    raw_actions = data.get("actions", {})
    actions: list[ActionConfig] = []
    if isinstance(raw_actions, list):
        actions = [ActionConfig(**a) for a in raw_actions]
    elif isinstance(raw_actions, dict):
        for action_id, action_data in raw_actions.items():
            actions.append(ActionConfig(id=action_id, **action_data))

    # ── Boundaries ────────────────────────────────────────────────────────
    boundaries = [BoundaryConfig(**b) for b in data.get("boundaries", [])]

    # ── Scheduled Ops ─────────────────────────────────────────────────────
    scheduled_ops = [ScheduledOpConfig(**s) for s in data.get("scheduled_ops", [])]

    return PlaybookConfig(
        name=name,
        description=description,
        version=version,
        owner=owner,
        timezone=timezone,
        personality_prompt=personality_prompt,
        llm=llm_dict,
        home_assistant=ha_config,
        sensors=sensors,
        energy=energy,
        security=security,
        self_assessment=self_assessment,
        users=users,
        notifications=notifications,
        connectors=connectors,
        entities=entities,
        actions=actions,
        boundaries=boundaries,
        scheduled_ops=scheduled_ops,
    )
