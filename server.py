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
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from engine.playbook_loader import load_playbook
from engine.playbook_schema import PlaybookConfig
from engine.model_router import ModelRouter
from engine.ha_connector import HAConnector
from engine.memory import Memory
from engine.agent import Agent
from engine.backup import BackupEngine

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

PLAYBOOK_PATH = Path(os.getenv("ALFE_PLAYBOOK", "playbooks/cole_sandbox.toml"))

playbook: PlaybookConfig = None
router: ModelRouter = None
ha: HAConnector = None
memory: Memory = None
agent: Agent = None
registry = None  # ConnectorRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise services on startup."""
    global playbook, router, ha, memory, agent, registry

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

    yield  # App runs here

    logger.info("Alf-E shutting down")


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Alf-E",
    description="Your Personal AI Agent",
    version="2.0.0",
    lifespan=lifespan,
)


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
        "message_count": memory.get_message_count() if memory else 0,
        "cost_30d":      cost,
        "providers":     list(playbook.llm.keys()) if playbook else [],
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
else:
    @app.get("/")
    async def root():
        return {"message": "Alf-E API is running.", "docs": "/docs"}
