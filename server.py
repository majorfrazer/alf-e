"""
Alf-E Server — FastAPI backend.

Replaces Streamlit. Serves the PWA frontend and provides
REST API endpoints for chat, sensors, status, and approvals.
"""

import os
import uuid
import json
import asyncio
import logging
import threading
import time
import secrets
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from engine.playbook_loader import load_playbook
from engine.playbook_schema import PlaybookConfig
from engine.model_router import ModelRouter
from engine.ha_connector import HAConnector
from engine.memory import Memory
from engine.agent import Agent
from engine.backup import BackupEngine
from engine.scheduler import Scheduler
from engine.cross_domain import CrossDomainEngine

try:
    from engine.connectors import ConnectorRegistry
except ImportError:
    ConnectorRegistry = None

# ── Setup ────────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("alfe.server")

# ── Load playbook ────────────────────────────────────────────────────────────

PLAYBOOK_PATH = Path(os.getenv("ALFE_PLAYBOOK", "playbooks/example_household.toml"))

playbook: PlaybookConfig = None
router: ModelRouter = None
ha: HAConnector = None
memory: Memory = None
agent: Agent = None
registry = None  # ConnectorRegistry
scheduler: Scheduler = None
cross_domain: CrossDomainEngine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise services on startup."""
    global playbook, router, ha, memory, agent, registry, scheduler, cross_domain

    # Boot time for health endpoint uptime tracking
    global _BOOT_TIME
    import time as _time
    _BOOT_TIME = _time.time()

    # Playbook
    try:
        playbook = load_playbook(PLAYBOOK_PATH)
        logger.info(f"Playbook loaded: {playbook.name} v{playbook.version}")
    except Exception as e:
        logger.error(f"Failed to load playbook: {e}")
        raise

    # Model router
    router = ModelRouter(playbook.llm)
    logger.info(f"Model router ready with {len(playbook.llm)} provider(s)")

    # Home Assistant (legacy connector — kept while migrating)
    if playbook.home_assistant:
        supervisor_token = os.getenv("SUPERVISOR_TOKEN")
        if supervisor_token:
            # HA add-on: use internal supervisor API
            ha_url   = "http://supervisor/core"
            ha_token = supervisor_token
            logger.info("Running as HA add-on — using supervisor internal API")
        else:
            # Standalone / Docker: HA_URL env overrides playbook URL
            ha_url   = os.getenv("HA_URL", playbook.home_assistant.url)
            ha_token = os.getenv(playbook.home_assistant.token_env, "")

        if ha_token:
            ha = HAConnector(ha_url, ha_token)
            if ha.health_check():
                logger.info(f"Home Assistant connected at {ha_url}")
            else:
                logger.warning(f"Home Assistant unreachable at {ha_url}")
                ha = None
        else:
            logger.warning("HA token not set — running without Home Assistant")

    # Connector Registry (new architecture)
    if ConnectorRegistry:
        try:
            registry = ConnectorRegistry(playbook)
            registry.load_all()
            logger.info(f"Connector registry: {registry}")
        except Exception as e:
            logger.warning(f"Connector registry failed to load: {e}")
            registry = None

    # Memory
    memory = Memory()
    logger.info(f"Memory ready ({memory.get_message_count()} messages stored)")

    # Agent
    agent = Agent(router=router, ha=ha, memory=memory, playbook=playbook, registry=registry)
    logger.info("Alf-E agent ready")

    # Scheduler — runs scheduled_ops from the playbook (e.g. morning briefing)
    scheduler = Scheduler(
        ops=playbook.scheduled_ops if playbook else [],
        timezone=playbook.timezone if playbook else "UTC",
    )
    scheduler.attach_agent(agent)
    scheduler.start()

    # Cross-domain reasoning engine — proactive insights every 60 minutes.
    # Pinned to Anthropic Haiku inside cross_domain.py (~$0.04-0.08/day at 60-min cycle).
    cross_domain = CrossDomainEngine(interval_minutes=60, enabled=True)
    cross_domain.attach(agent, memory, registry, playbook)
    cross_domain.start()

    yield  # App runs here

    cross_domain.stop()
    scheduler.stop()
    logger.info("Alf-E shutting down")


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Alf-E",
    description="Your Personal AI Agent",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Auth Middleware ──────────────────────────────────────────────────────────
# When running as HA add-on, ingress handles auth — no token needed.
# In standalone/Docker mode, ALFE_API_TOKEN must be set or one is auto-generated.

_IS_HA_ADDON = bool(os.getenv("SUPERVISOR_TOKEN"))
_API_TOKEN = os.getenv("ALFE_API_TOKEN", "")

if not _IS_HA_ADDON and not _API_TOKEN:
    _API_TOKEN = secrets.token_urlsafe(32)
    logger.warning(
        f"No ALFE_API_TOKEN set — auto-generated token for this session:\n"
        f"  ALFE_API_TOKEN={_API_TOKEN}\n"
        f"  Add this to your .env to keep it stable across restarts."
    )

# Paths that don't need auth (static files, health, docs, first-run setup page)
# Note: /api/setup still requires token — the page is public, the write is not.
_PUBLIC_PATHS = {
    "/", "/setup", "/sw.js", "/docs", "/openapi.json", "/redoc",
    "/api/health", "/api/setup/status", "/api/setup/info",
    "/api/setup", "/api/setup/validate-key", "/api/setup/validate-ha",
    "/api/update/check",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Bearer token auth for all /api/ endpoints in standalone mode."""
    path = request.url.path

    # HA add-on mode — ingress handles auth, skip everything
    if _IS_HA_ADDON:
        return await call_next(request)

    # Static files and public paths — no auth
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)

    # API endpoints — require bearer token
    if path.startswith("/api/"):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization header. Use: Bearer <ALFE_API_TOKEN>"})
        token = auth_header[7:]
        if token != _API_TOKEN:
            return JSONResponse(status_code=403, content={"detail": "Invalid API token"})

    return await call_next(request)


# ── Rate Limiter ────────────────────────────────────────────────────────────
# Uses the SecurityConfig values from the playbook.

_rate_counts: dict[str, list[float]] = {}  # ip → list of timestamps


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Enforce max_actions_per_minute and max_actions_per_hour from SecurityConfig."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    # Get limits from playbook (loaded by lifespan, may be None at startup)
    per_minute = 30
    per_hour = 300
    if playbook and playbook.security:
        per_minute = playbook.security.max_actions_per_minute
        per_hour = playbook.security.max_actions_per_hour

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    if client_ip not in _rate_counts:
        _rate_counts[client_ip] = []

    # Prune timestamps older than 1 hour
    _rate_counts[client_ip] = [t for t in _rate_counts[client_ip] if now - t < 3600]

    timestamps = _rate_counts[client_ip]
    last_minute = sum(1 for t in timestamps if now - t < 60)
    last_hour = len(timestamps)

    if last_minute >= per_minute:
        return JSONResponse(status_code=429, content={"detail": f"Rate limit: max {per_minute} requests/minute"})
    if last_hour >= per_hour:
        return JSONResponse(status_code=429, content={"detail": f"Rate limit: max {per_hour} requests/hour"})

    timestamps.append(now)
    return await call_next(request)


# ── API Models ───────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"
    conversation_id: str = None


from typing import Optional

class ChatResponse(BaseModel):
    response: str
    model_used: str = ""
    pending_approvals: list[dict] = []
    conversation_id: str = ""


class ApprovalRequest(BaseModel):
    index: int
    approved: bool
    user_id: str = "default"


# ── Chat Endpoint ────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message to Alf-E and get a response."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")

    # Generate conversation ID if not provided
    conv_id = req.conversation_id or str(uuid.uuid4())[:8]

    # Load conversation history
    history = memory.load_messages(
        user_id=req.user_id,
        conversation_id=conv_id,
        limit=50,
    )

    # Save user message
    memory.save_message(
        "user", req.message,
        user_id=req.user_id,
        conversation_id=conv_id,
    )

    # Add current message to history
    history.append({"role": "user", "content": req.message})

    # Get response
    try:
        response_text = agent.chat(
            messages=history,
            user_id=req.user_id,
        )
    except Exception as e:
        logger.error(f"Agent error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ChatResponse(
        response=response_text,
        model_used=agent.last_model_used or "",
        pending_approvals=agent.pending_approvals,
        conversation_id=conv_id,
    )


# ── Streaming Chat Endpoint ──────────────────────────────────────────────────

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """Streaming chat via Server-Sent Events. Yields token/tool/done events."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")

    conv_id = req.conversation_id or str(uuid.uuid4())[:8]

    history = memory.load_messages(
        user_id=req.user_id,
        conversation_id=conv_id,
        limit=50,
    )
    memory.save_message(
        "user", req.message,
        user_id=req.user_id,
        conversation_id=conv_id,
    )
    history.append({"role": "user", "content": req.message})

    async def event_stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def producer():
            try:
                for event_type, content in agent.stream_chat(
                    messages=history,
                    user_id=req.user_id,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, (event_type, content))
            except Exception as e:
                logger.error(f"Stream error: {e}")
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=producer, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            event_type, content = item
            yield f"data: {json.dumps({'type': event_type, 'content': content})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'pending_approvals': agent.pending_approvals, 'model_used': agent.last_model_used})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Approval Endpoint ───────────────────────────────────────────────────────

@app.post("/api/approve")
async def approve_action(req: ApprovalRequest):
    """Approve or reject a pending action."""
    if not agent or not agent.pending_approvals:
        raise HTTPException(status_code=404, detail="No pending approvals")

    if req.index >= len(agent.pending_approvals):
        raise HTTPException(status_code=404, detail="Approval index out of range")

    action = agent.pending_approvals[req.index]

    if req.approved:
        # ── HA service call ───────────────────────────────────────────
        if action["type"] == "ha_service_call" and ha:
            success = ha.call_service(
                action["domain"],
                action["service"],
                action["entity_id"],
                action.get("data"),
            )
            memory.log_action(
                req.user_id, "ha_service_call",
                target=action["entity_id"],
                result="success" if success else "failed",
            )
            agent.pending_approvals.pop(req.index)
            return {"status": "executed", "success": success}

        # ── Code proposal (self-building connector) ───────────────────
        if action["type"] == "code_proposal":
            result = await _deploy_connector(action, req.user_id)
            agent.pending_approvals.pop(req.index)
            return result

    agent.pending_approvals.pop(req.index)
    return {"status": "rejected"}


async def _deploy_connector(action: dict, user_id: str) -> dict:
    """Write approved connector code, git commit, trigger restart."""
    import subprocess

    file_path = Path(__file__).parent / action["file_path"]
    code      = action["code"]
    cid       = action["connector_id"]

    # 1. Backup before writing anything
    try:
        backup = BackupEngine()
        result = backup.run(label=f"pre_connector_{cid}")
        if not result.success:
            return {"status": "error", "detail": f"Backup failed: {result.error}"}
        logger.info(f"Backup complete: {result.path}")
    except Exception as e:
        return {"status": "error", "detail": f"Backup error: {e}"}

    # 2. Write the connector file
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")
        logger.info(f"Connector written: {file_path}")
    except Exception as e:
        return {"status": "error", "detail": f"File write error: {e}"}

    # 3. Git commit (best effort — don't block deployment if git fails)
    try:
        repo_root = Path(__file__).parent
        subprocess.run(["git", "add", str(file_path)], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: add {cid} connector (approved by {user_id})"],
            cwd=repo_root, check=True, capture_output=True,
        )
        logger.info(f"Git committed: {cid} connector")
    except Exception as e:
        logger.warning(f"Git commit failed (continuing): {e}")

    # 4. Signal restart (write a flag file — checked by run.sh or supervisor)
    restart_flag = Path(__file__).parent / ".restart_requested"
    restart_flag.write_text(f"connector:{cid}\nuser:{user_id}\n")

    return {
        "status": "deployed",
        "connector_id": cid,
        "file": str(file_path.relative_to(Path(__file__).parent)),
        "restart_pending": True,
    }


@app.get("/api/proposals")
async def get_proposals():
    """List pending code proposals (connector drafts awaiting approval)."""
    if not agent:
        return {"proposals": []}
    proposals = [
        {
            "index":        i,
            "connector_id": p.get("connector_id"),
            "description":  p.get("description"),
            "file_path":    p.get("file_path"),
            "code":         p.get("code"),
            "proposed_by":  p.get("proposed_by"),
        }
        for i, p in enumerate(agent.pending_approvals)
        if p.get("type") == "code_proposal"
    ]
    return {"proposals": proposals}


@app.post("/api/restart")
async def request_restart():
    """Signal Alf-E to restart (e.g. after connector deployment)."""
    restart_flag = Path(__file__).parent / ".restart_requested"
    restart_flag.write_text("manual\n")
    return {"status": "restart_requested"}


@app.post("/api/playbook/reload")
async def reload_playbook():
    """Hot-reload the playbook from disk without restarting the container.

    Reloads personality_prompt, sensors, ha_sites, boundaries, and scheduled_ops.
    Does NOT reload LLM provider configs or connector enable/disable — those
    still require a container restart.
    """
    global playbook
    try:
        new_pb = load_playbook(PLAYBOOK_PATH)
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse playbook: {e}"}

    changed = []
    if playbook and new_pb.personality_prompt != playbook.personality_prompt:
        changed.append("personality_prompt")
    if playbook and dict(new_pb.sensors) != dict(playbook.sensors):
        changed.append("sensors")
    if playbook and [s.model_dump() for s in new_pb.ha_sites] != [s.model_dump() for s in playbook.ha_sites]:
        changed.append("ha_sites")

    playbook = new_pb

    # Thread new references through live components
    if agent:
        agent.playbook = new_pb
    if registry:
        registry.playbook = new_pb
        # Re-thread ha_sites into the ha connector if loaded
        ha_conn = registry._connectors.get("ha") if hasattr(registry, "_connectors") else None
        if ha_conn:
            ha_conn._sites = [s.model_dump() for s in new_pb.ha_sites]
    if scheduler:
        scheduler.set_ops(new_pb.scheduled_ops)

    logger.info(f"Playbook reloaded: {new_pb.name} v{new_pb.version} (changed: {changed or 'none'})")
    return {
        "status": "ok",
        "playbook": new_pb.name,
        "version": new_pb.version,
        "changed": changed,
    }


# ── HA Sites API ──────────────────────────────────────────────────────────────

class SwitchSitePayload(BaseModel):
    name: str


class AddSitePayload(BaseModel):
    name: str
    owner: str
    url: str
    token: str
    notes: str = ""


def _get_ha_connector():
    """Return the live HA connector from the registry, or None."""
    if registry and hasattr(registry, "_connectors"):
        return registry._connectors.get("ha")
    return None


@app.get("/api/ha/sites")
async def get_ha_sites():
    """List all configured HA sites and which is currently active."""
    if not playbook:
        return {"sites": [], "active": "default"}
    sites = [s.model_dump() for s in playbook.ha_sites]
    ha_conn = _get_ha_connector()
    active = ha_conn._active_site if ha_conn else "default"
    return {"sites": sites, "active": active}


@app.post("/api/ha/sites/switch")
async def switch_ha_site(payload: SwitchSitePayload):
    """Switch the active HA site without restarting."""
    ha_conn = _get_ha_connector()
    if not ha_conn:
        raise HTTPException(status_code=503, detail="HA connector not loaded")
    result = ha_conn._switch_site(payload.name)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.content)
    return {"status": "ok", "active": payload.name, "message": result.content}


@app.post("/api/ha/sites")
async def add_ha_site(payload: AddSitePayload):
    """Add a new HA site: writes to playbook TOML + .env, then hot-reloads."""
    name = payload.name.lower().replace(" ", "_")
    env_var = f"HA_TOKEN_{name.upper()}"

    if not name or not payload.url or not payload.token:
        raise HTTPException(status_code=400, detail="name, url, and token are required")
    if not payload.url.startswith("http"):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    if playbook and any(s.name.lower() == name for s in playbook.ha_sites):
        raise HTTPException(status_code=409, detail=f"Site '{name}' already exists")

    # Write token to .env
    env_path = PLAYBOOK_PATH.parent.parent / ".env"
    if not env_path.exists():
        env_path = Path(".env")
    try:
        existing_env = env_path.read_text() if env_path.exists() else ""
        if f"{env_var}=" not in existing_env:
            with env_path.open("a") as f:
                f.write(f"\n# {payload.owner}\n{env_var}={payload.token}\n")
    except Exception as e:
        logger.warning(f"Could not write to .env: {e}")

    # Make token immediately available in this process
    os.environ[env_var] = payload.token

    # Append [[ha_sites]] block to playbook TOML
    notes = payload.notes or f"Added {__import__('datetime').date.today()}"
    toml_block = (
        f'\n[[ha_sites]]\n'
        f'name = "{name}"\n'
        f'owner = "{payload.owner}"\n'
        f'url = "{payload.url}"\n'
        f'token_env = "{env_var}"\n'
        f'notes = "{notes}"\n'
    )
    try:
        with PLAYBOOK_PATH.open("a") as f:
            f.write(toml_block)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not write playbook: {e}")

    # Hot-reload
    try:
        new_pb = load_playbook(PLAYBOOK_PATH)
        global playbook
        playbook = new_pb
        if agent:
            agent.playbook = new_pb
        if registry:
            registry.playbook = new_pb
            ha_conn = _get_ha_connector()
            if ha_conn:
                ha_conn._sites = [s.model_dump() for s in new_pb.ha_sites]
        if scheduler:
            scheduler.set_ops(new_pb.scheduled_ops)
        logger.info(f"Added HA site '{name}' and reloaded playbook")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Site written but playbook reload failed: {e}")

    return {"status": "ok", "name": name, "env_var": env_var}


# ── Memory Export (Claude Code bridge) ───────────────────────────────────────

@app.get("/api/memory/export")
async def memory_export():
    """Export Alf-E's full memory for Claude Code session inheritance.

    Claude Code calls this at session start (via a hook in .claude/settings.json)
    to load everything Alf-E has learned from Fraser's conversations:
      - All stored context facts (domain/key/value)
      - Recent conversation topics (last 7 days)
      - User profiles
      - 30-day cost summary

    This is the bridge between Alf-E's chat memory and Claude Code sessions.
    """
    if not memory:
        return {"error": "Memory not initialised"}
    return memory.export_for_claude_code()


@app.post("/api/memory/context")
async def set_memory_context(domain: str, key: str, value: str, source: str = "claude_code"):
    """Write a context fact into Alf-E's memory from Claude Code.

    This is the reverse bridge — Claude Code can push facts it discovers
    (e.g. from reading code or git history) into Alf-E so it knows about them.
    """
    if not memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    memory.set_context(domain=domain, key=key, value=value, source=source)
    return {"status": "stored", "domain": domain, "key": key}


# ── Sensor Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/sensors")
async def get_sensors():
    """Get all configured sensor values."""
    if not ha or not playbook:
        return {"sensors": {}, "connected": False}

    data = ha.get_sensor_batch(playbook.sensors)
    return {"sensors": data, "connected": True}


@app.get("/api/sensors/{sensor_name}")
async def get_sensor(sensor_name: str):
    """Get a specific sensor value."""
    if not ha or not playbook:
        raise HTTPException(status_code=503, detail="HA not connected")

    if sensor_name not in playbook.sensors:
        raise HTTPException(status_code=404, detail=f"Unknown sensor: {sensor_name}")

    entity_id = playbook.sensors[sensor_name]
    value = ha.get_numeric_value(entity_id)
    return {"sensor": sensor_name, "entity_id": entity_id, "value": value}


# ── Status Endpoint ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Get Alf-E system status."""
    cost = memory.get_cost_summary(30) if memory else {}
    connector_status = registry.get_status() if registry else []
    return {
        "name":          playbook.name if playbook else "Alf-E",
        "version":       playbook.version if playbook else "0.0.0",
        "ha_connected":  ha is not None and ha.health_check() if ha else False,
        "connectors":    connector_status,
        "scheduler":     scheduler.get_status() if scheduler else {},
        "cross_domain":  cross_domain.get_status() if cross_domain else {"enabled": False},
        "message_count": memory.get_message_count() if memory else 0,
        "cost_30d":      cost,
        "providers":     list(playbook.llm.keys()) if playbook else [],
        "active_model":  agent.last_model_used if agent and hasattr(agent, "last_model_used") else "",
    }


# ── Health Endpoint ─────────────────────────────────────────────────────────

_BOOT_TIME = None  # set on startup


@app.get("/api/health")
async def get_health():
    """Compact health summary for the PWA dashboard."""
    import time
    now = time.time()
    uptime_s = int(now - _BOOT_TIME) if _BOOT_TIME else 0

    # Connector health
    conn_ok, conn_total = 0, 0
    if registry:
        for c in registry.get_status():
            conn_total += 1
            if c.get("connected"):
                conn_ok += 1

    # Memory DB size
    db_size_mb = 0.0
    if memory:
        try:
            db_size_mb = round(Path(memory.db_path).stat().st_size / 1024 / 1024, 2)
        except Exception:
            pass

    # Cost: today (24h) and 30d
    cost_24h = memory.get_cost_summary(1) if memory else {"total_usd": 0}
    cost_30d = memory.get_cost_summary(30) if memory else {"total_usd": 0}

    return {
        "uptime_seconds": uptime_s,
        "connectors":     {"ok": conn_ok, "total": conn_total},
        "memory":         {"messages": memory.get_message_count() if memory else 0,
                           "db_size_mb": db_size_mb},
        "cost":           {"last_24h_usd":  round(cost_24h.get("total_usd", 0), 4),
                           "last_30d_usd":  round(cost_30d.get("total_usd", 0), 4)},
        "scheduler":      scheduler.get_status() if scheduler else {},
        "playbook":       {"name": playbook.name, "version": playbook.version} if playbook else {},
    }


# ── First-Run Setup Endpoint ────────────────────────────────────────────────

from pydantic import BaseModel as _PBM


class SetupPayload(_PBM):
    household_name:    str
    owner_name:        str
    timezone:          str = "Australia/Brisbane"
    ha_url:            str = "http://homeassistant:8123"
    ha_token:          str = ""
    anthropic_api_key: str = ""
    enable_gmail:      bool = False
    gmail_user:        str = ""
    enable_bom:        bool = False
    bom_city:          str = "Brisbane"


@app.post("/api/setup")
async def first_run_setup(payload: SetupPayload):
    """Write a customised playbook from the first-run wizard inputs.

    Overwrites the currently configured playbook file (usually
    example_household.toml for gift recipients), then hot-reloads.
    """
    import re as _re

    def _esc(s: str) -> str:
        return (s or "").replace('"', '\\"')

    # Build TOML from template
    gmail_block = f"""
[[connectors]]
id = "gmail"
enabled = true
user = "{_esc(payload.gmail_user)}"
""" if payload.enable_gmail and payload.gmail_user else ""

    bom_geohash_map = {
        "brisbane":  "r7hge7", "sydney":    "r3gx2f", "melbourne": "r1r11df",
        "perth":     "qd66hrh","adelaide":  "r1f9540","hobart":    "r22k2te",
        "darwin":    "qqgcms0","canberra":  "r3dp5hf","gold coast":"r7h6nys",
        "cairns":    "rqgxfwb",
    }
    bom_geohash = bom_geohash_map.get(payload.bom_city.lower().strip(), "r7hge7")
    bom_block = f"""
[[connectors]]
id = "bom"
enabled = true
geohash = "{bom_geohash}"
location = "{_esc(payload.bom_city)}"
""" if payload.enable_bom else ""

    slug = _re.sub(r"[^a-z0-9]+", "_", payload.household_name.lower()).strip("_") or "household"

    toml = f'''[metadata]
name = "{_esc(payload.household_name)}"
description = "Generated by first-run wizard"
version = "1.0.0"
owner = "{_esc(payload.owner_name)}"
timezone = "{_esc(payload.timezone)}"

personality_prompt = """
You are Alf-E: {_esc(payload.owner_name)}'s household AI agent.

PERSONALITY:
- Warm, practical, helpful
- Honest about limitations — no sugarcoating
- Keep responses tight

RESPONSE RULES:
- Never repeat the same opening phrase twice in a conversation.
- Do not re-list capabilities or connector status unless asked.
- One answer per question.
"""

[llm.default]
provider = "anthropic"
model = "claude-sonnet-4-6"
api_key_env = "ANTHROPIC_API_KEY"
max_tokens = 4096
temperature = 0.7
cost_per_1k_input = 0.003
cost_per_1k_output = 0.015
capabilities = ["general", "reasoning"]

[llm.fast]
provider = "anthropic"
model = "claude-haiku-4-5-20251001"
api_key_env = "ANTHROPIC_API_KEY"
max_tokens = 1500
temperature = 0.7
cost_per_1k_input = 0.0008
cost_per_1k_output = 0.004
capabilities = ["quick", "status"]

[home_assistant]
url = "{_esc(payload.ha_url)}"
token_env = "HA_API_TOKEN"

[sensors]

[[users]]
id = "{slug}"
name = "{_esc(payload.owner_name)}"
role = "owner"

[[notifications]]
channel = "pwa_push"
enabled = true
urgency_min = "normal"

[energy]
peak_rate = 0.0
offpeak_rate = 0.0
feed_in_rate = 0.0
peak_start = "06:00"
peak_end = "00:00"
solar_capacity_kw = 0.0
battery_capacity_kwh = 0.0
battery_min_soc = 20
currency = "AUD"

[security]
require_approval_for_writes = true
max_actions_per_minute = 30
max_actions_per_hour = 300
audit_log_retention_days = 90
safe_file_roots = ["/data/alfe_notes", "/data/reports"]

[[connectors]]
id = "ha"
enabled = true
{gmail_block}{bom_block}
'''

    # Persist API key and HA token to .env and live environment
    env_path = PLAYBOOK_PATH.parent.parent / ".env"
    if not env_path.exists():
        env_path = Path(".env")

    def _write_env_var(path: Path, key: str, value: str):
        try:
            existing = path.read_text() if path.exists() else ""
            if f"{key}=" not in existing:
                with path.open("a") as f:
                    f.write(f"\n{key}={value}\n")
        except Exception:
            pass
        os.environ[key] = value

    if payload.anthropic_api_key:
        _write_env_var(env_path, "ANTHROPIC_API_KEY", payload.anthropic_api_key)
    if payload.ha_token:
        _write_env_var(env_path, "HA_API_TOKEN", payload.ha_token)

    # Write to the active playbook path
    try:
        PLAYBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLAYBOOK_PATH.write_text(toml)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Could not write playbook: {e}"})

    # Mark setup complete (for first-run detection)
    try:
        (Path(memory.db_path).parent / ".setup_complete").write_text("1\n")
    except Exception:
        pass

    logger.info(f"First-run setup wrote playbook → {PLAYBOOK_PATH}")
    return {
        "status": "ok",
        "playbook_path": str(PLAYBOOK_PATH),
        "next_step": "Restart the container or call POST /api/playbook/reload to load the new playbook.",
    }


@app.get("/api/setup/status")
async def setup_status():
    """Check if first-run setup has been completed."""
    if not memory:
        return {"setup_complete": False}
    flag = Path(memory.db_path).parent / ".setup_complete"
    return {"setup_complete": flag.exists(), "current_playbook": playbook.name if playbook else None}


@app.get("/api/setup/info")
async def setup_info():
    """Return environment info the wizard needs before submitting."""
    return {
        "is_addon":          _IS_HA_ADDON,
        "anthropic_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "ha_key_set":        bool(os.getenv("HA_API_TOKEN") or os.getenv("SUPERVISOR_TOKEN")),
    }


class ValidateKeyPayload(_PBM):
    anthropic_api_key: str


@app.post("/api/setup/validate-key")
async def validate_api_key(payload: ValidateKeyPayload):
    """Quick smoke-test of an Anthropic API key."""
    import httpx as _httpx
    try:
        r = _httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": payload.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            timeout=10,
        )
        if r.status_code in (200, 400):   # 400 can mean bad request but key is valid
            return {"valid": True}
        if r.status_code == 401:
            return {"valid": False, "error": "Invalid API key — check and try again."}
        return {"valid": False, "error": f"Anthropic returned {r.status_code}"}
    except Exception as e:
        return {"valid": False, "error": f"Could not reach Anthropic: {e}"}


class ValidateHaPayload(_PBM):
    ha_url:   str
    ha_token: str


@app.post("/api/setup/validate-ha")
async def validate_ha(payload: ValidateHaPayload):
    """Test a HA URL + token pair."""
    import httpx as _httpx
    url = payload.ha_url.rstrip("/")
    try:
        r = _httpx.get(
            f"{url}/api/",
            headers={"Authorization": f"Bearer {payload.ha_token}"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            return {"valid": True, "ha_name": data.get("location_name", "Home Assistant")}
        if r.status_code in (401, 403):
            return {"valid": False, "error": "Token rejected — create a new long-lived token in HA Profile → Security."}
        return {"valid": False, "error": f"HA returned {r.status_code}"}
    except Exception as e:
        return {"valid": False, "error": f"Could not reach {url} — check the URL."}


# ── Update Check ─────────────────────────────────────────────────────────────

_ALFE_VERSION = "2.5.0"   # bumped each release


@app.get("/api/update/check")
async def check_for_update():
    """Compare running version against latest published version.json on GitHub."""
    import httpx as _httpx
    try:
        r = _httpx.get(
            "https://raw.githubusercontent.com/majorfrazer/alf-e/main/version.json",
            timeout=6,
        )
        if r.status_code == 200:
            latest = r.json().get("version", _ALFE_VERSION)
            update_available = latest != _ALFE_VERSION
            return {
                "current":          _ALFE_VERSION,
                "latest":           latest,
                "update_available": update_available,
                "is_addon":         _IS_HA_ADDON,
            }
    except Exception:
        pass
    return {"current": _ALFE_VERSION, "latest": _ALFE_VERSION, "update_available": False}


# ── Audit Log Endpoint ──────────────────────────────────────────────────────

@app.get("/api/audit")
async def get_audit(limit: int = 100, user_id: str = None):
    """Return recent tool-call audit entries (most recent first).

    Each entry: timestamp, user_id, action (tool name), target (input JSON),
    result ('ok' / 'error' / 'denied' / 'pending_approval'), details.
    """
    if not memory:
        return {"entries": [], "total": 0}
    entries = memory.get_audit_log(limit=limit, user_id=user_id)
    return {"entries": entries, "total": len(entries)}


# ── Insights Endpoint ───────────────────────────────────────────────────────

@app.get("/api/insights")
async def get_insights(limit: int = 20):
    """Return recent cross-domain insights from the proactive reasoning engine."""
    if not cross_domain:
        return {"insights": [], "engine": {"enabled": False}}
    return {
        "insights": cross_domain.get_insights(limit=limit),
        "engine":   cross_domain.get_status(),
    }


# ── Service Worker (served at root so SW scope covers the whole ingress path) ─

@app.get("/sw.js")
async def service_worker():
    """Serve the service worker with scope override header."""
    sw_path = Path(__file__).parent / "static" / "sw.js"
    if not sw_path.exists():
        raise HTTPException(status_code=404, detail="sw.js not found")
    return FileResponse(
        str(sw_path),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


# ── Static Files (PWA) ──────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def serve_index():
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": "Alf-E API is running. PWA not yet deployed."}

    @app.get("/setup")
    async def serve_setup():
        """First-run wizard page."""
        setup_html = static_dir / "setup.html"
        if setup_html.exists():
            return FileResponse(str(setup_html))
        return {"message": "Setup page missing"}
else:
    @app.get("/")
    async def root():
        return {"message": "Alf-E API is running.", "docs": "/docs"}
