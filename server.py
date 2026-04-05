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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise services on startup."""
    global playbook, router, ha, memory, agent

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

    # Home Assistant
    if playbook.home_assistant:
        # When running as HA add-on, use internal supervisor URL + auto token
        supervisor_token = os.getenv("SUPERVISOR_TOKEN")
        if supervisor_token:
            ha_url   = "http://supervisor/core"
            ha_token = supervisor_token
            logger.info("Running as HA add-on — using supervisor internal API")
        else:
            ha_url   = playbook.home_assistant.url
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

    # Memory
    memory = Memory()
    logger.info(f"Memory ready ({memory.get_message_count()} messages stored)")

    # Agent
    agent = Agent(router=router, ha=ha, memory=memory, playbook=playbook)
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
        # Execute the action
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

    agent.pending_approvals.pop(req.index)
    return {"status": "rejected"}


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
    return {
        "name": playbook.name if playbook else "Alf-E",
        "version": playbook.version if playbook else "0.0.0",
        "ha_connected": ha is not None and ha.health_check() if ha else False,
        "message_count": memory.get_message_count() if memory else 0,
        "cost_30d": cost,
        "providers": list(playbook.llm.keys()) if playbook else [],
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
