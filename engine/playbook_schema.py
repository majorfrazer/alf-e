"""
Alf-E Playbook Schema — Pydantic models for TOML playbook validation.

Defines the complete configuration structure for an Alf-E instance:
identity, LLM providers, Home Assistant, sensors, actions, users,
notifications, boundaries, scheduled ops, and security.
"""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class LLMProvider(str, Enum):
    anthropic = "anthropic"
    google = "google"
    openai = "openai"
    ollama = "ollama"
    mistral = "mistral"


class ActionVerb(str, Enum):
    read = "read"
    propose = "propose"
    execute = "execute"
    ha_query = "ha_query"
    ha_call = "ha_call"


class ActionApproval(str, Enum):
    autonomous = "autonomous"      # Just do it, no approval
    notify = "notify"              # Do it, tell the owner after
    confirm = "confirm"            # Propose, wait for yes/no
    admin_only = "admin_only"      # Only owner/admin can approve


class UserRole(str, Enum):
    owner = "owner"
    member = "member"
    guest = "guest"


class BoundaryType(str, Enum):
    monetary = "monetary"
    rate = "rate"
    custom = "custom"


class NotificationChannel(str, Enum):
    pwa_push = "pwa_push"
    voice = "voice"
    chat_app = "chat_app"
    email = "email"
    briefing = "briefing"


class NotificationUrgency(str, Enum):
    critical = "critical"    # Immediate push + voice
    normal = "normal"        # Push notification
    low = "low"              # Queue for briefing


# ── Config Models ────────────────────────────────────────────────────────────

class LLMConfig(BaseModel):
    """Configuration for a single AI model provider."""
    provider: LLMProvider
    model: str
    api_key_env: str
    max_tokens: int = 2000
    temperature: float = 0.7
    cost_per_1k_input: float = 0.0    # USD per 1K input tokens
    cost_per_1k_output: float = 0.0   # USD per 1K output tokens
    capabilities: List[str] = Field(default_factory=list)  # e.g. ["code", "reasoning", "quick"]


class HomeAssistantConfig(BaseModel):
    """Home Assistant connection settings."""
    url: str
    token_env: str


class ConnectorConfig(BaseModel):
    """External service connector (POS, email, CRM, etc)."""
    id: str
    type: str
    description: str = ""
    settings: Dict[str, str] = Field(default_factory=dict)


class EntityConfig(BaseModel):
    """A business/home entity exposed through a connector."""
    id: str
    name: str
    type: str
    connector: str
    readable: bool = True
    writable: bool = False
    description: str = ""


class ActionConfig(BaseModel):
    """An action Alf-E can take, with approval tier."""
    id: str
    description: str
    connector: Optional[str] = None
    verb: ActionVerb = ActionVerb.propose
    entity_pattern: str = "*"
    entity: Optional[str] = None
    command: Optional[str] = None
    approval: ActionApproval = ActionApproval.confirm
    require_confidence: Optional[float] = None


class UserConfig(BaseModel):
    """A user with role-based access to Alf-E."""
    id: str
    name: str
    role: UserRole = UserRole.member
    pin: Optional[str] = None           # Optional PIN for voice auth
    voice_id: Optional[str] = None      # For speaker identification
    permitted_domains: List[str] = Field(default_factory=lambda: ["*"])
    parental_oversight: bool = False     # Admin can see this user's conversations


class NotificationConfig(BaseModel):
    """Notification routing preferences."""
    channel: NotificationChannel
    enabled: bool = True
    urgency_min: NotificationUrgency = NotificationUrgency.normal
    settings: Dict[str, str] = Field(default_factory=dict)


class BoundaryConfig(BaseModel):
    """Safety boundary / spending limit."""
    id: str
    description: str
    type: BoundaryType
    limit: float
    unit: str
    escalation_message: str


class ScheduledOpConfig(BaseModel):
    """A scheduled task (e.g. morning briefing, daily report)."""
    id: str
    name: str
    description: str = ""
    at_time: str
    notify_on_complete: bool = False
    connectors_needed: List[str] = Field(default_factory=list)
    prompt: str = ""


class SecurityConfig(BaseModel):
    """Security settings for the playbook."""
    require_approval_for_writes: bool = True
    safe_file_roots: List[str] = Field(default_factory=list)
    blocked_files: List[str] = Field(default_factory=list)
    blocked_entity_patterns: List[str] = Field(default_factory=list)
    max_actions_per_minute: int = 60
    max_actions_per_hour: int = 500
    audit_log_retention_days: int = 90
    enable_kill_switch: bool = False


class SelfAssessmentConfig(BaseModel):
    """Self-assessment / continuous improvement settings."""
    enabled: bool = False
    review_frequency: str = "weekly"
    track_prediction_accuracy: bool = False
    track_approval_rate: bool = False
    confidence_threshold_warn: float = 0.5
    multiple_options_threshold: float = 0.7


# ── Root Playbook Config ─────────────────────────────────────────────────────

class PlaybookConfig(BaseModel):
    """Complete Alf-E playbook configuration."""

    # Identity
    name: str
    description: str = ""
    version: str = "1.0"
    owner: str = ""
    timezone: str = "UTC"
    personality_prompt: str = ""

    # LLM providers — keyed dict (default, fast, code, etc.)
    llm: Dict[str, LLMConfig]

    # Home Assistant integration
    home_assistant: Optional[HomeAssistantConfig] = None
    sensors: Dict[str, str] = Field(default_factory=dict)

    # Security
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Self-assessment
    self_assessment: SelfAssessmentConfig = Field(default_factory=SelfAssessmentConfig)

    # Users
    users: List[UserConfig] = Field(default_factory=list)

    # Notifications
    notifications: List[NotificationConfig] = Field(default_factory=list)

    # Business / connector objects
    connectors: List[ConnectorConfig] = Field(default_factory=list)
    entities: List[EntityConfig] = Field(default_factory=list)
    actions: List[ActionConfig] = Field(default_factory=list)
    boundaries: List[BoundaryConfig] = Field(default_factory=list)
    scheduled_ops: List[ScheduledOpConfig] = Field(default_factory=list)

    # Convenience properties
    @property
    def safe_file_roots(self) -> List[str]:
        return self.security.safe_file_roots

    @property
    def blocked_files(self) -> List[str]:
        return self.security.blocked_files

    def get_user(self, user_id: str) -> Optional[UserConfig]:
        """Look up a user by ID."""
        for u in self.users:
            if u.id == user_id:
                return u
        return None

    def get_owner(self) -> Optional[UserConfig]:
        """Get the owner/admin user."""
        for u in self.users:
            if u.role == UserRole.owner:
                return u
        return None

    @model_validator(mode="after")
    def validate_cross_references(self) -> "PlaybookConfig":
        connector_ids = {c.id for c in self.connectors}
        if not connector_ids:
            return self

        for entity in self.entities:
            if entity.connector not in connector_ids:
                raise ValueError(
                    f"Entity '{entity.id}' references unknown connector '{entity.connector}'"
                )

        for action in self.actions:
            if action.connector and action.connector not in connector_ids:
                raise ValueError(
                    f"Action '{action.id}' references unknown connector '{action.connector}'"
                )

        for op in self.scheduled_ops:
            for conn_id in op.connectors_needed:
                if conn_id not in connector_ids:
                    raise ValueError(
                        f"Scheduled op '{op.id}' references unknown connector '{conn_id}'"
                    )

        return self
