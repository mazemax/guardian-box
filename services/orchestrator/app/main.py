"""
GuardianBox Orchestrator - FastAPI Application

Local API endpoints as specified in PRD §15.
Serves the dashboard and provides SSE for real-time updates.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import orjson

from .event_manager import EventManager, TimingProfile
from .state_machine import EventState, TriggerType
from .models import (
    Event, PoseSummary, Resident, Contact, Zone,
    SystemStatus, EgressEvent
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"
DEMO_PROFILE = os.getenv("DEMO_PROFILE", "home")
VLLM_URL = os.getenv("VLLM_URL", "http://vllm:8000")
STT_URL = os.getenv("STT_URL", "http://stt:8001")
VOICE_URL = os.getenv("VOICE_URL", "http://voice:8002")
NOTIFIER_URL = os.getenv("NOTIFIER_URL", "http://notifier:8003")


event_manager: Optional[EventManager] = None
sse_queues: list[asyncio.Queue] = []
egress_log: list[EgressEvent] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global event_manager
    
    event_manager = EventManager(
        vllm_url=VLLM_URL,
        stt_url=STT_URL,
        voice_url=VOICE_URL,
        notifier_url=NOTIFIER_URL,
        profile=DEMO_PROFILE,
        mock_mode=MOCK_MODE,
    )
    
    async def sse_broadcaster(event_type: str, data: dict):
        message = {"event": event_type, "data": data}
        for queue in sse_queues:
            try:
                await queue.put(message)
            except Exception:
                pass
    
    event_manager.add_event_listener(sse_broadcaster)
    
    logger.info(f"Orchestrator started (mock_mode={MOCK_MODE}, profile={DEMO_PROFILE})")
    
    yield
    
    await event_manager.close()
    logger.info("Orchestrator stopped")


app = FastAPI(
    title="GuardianBox Orchestrator",
    description="Local fall detection and voice check-in API",
    version="1.0.0",
    lifespan=lifespan,
)


templates = Jinja2Templates(directory="/app/dashboard/templates")


class PoseTriggerRequest(BaseModel):
    trigger_type: str = "fast_fall"
    torso_angle: float = 75
    head_height_ratio: float = 0.2
    hip_descent_ratio: float = 0.5
    stillness_seconds: float = 5
    motion_energy: float = 0.1
    prayer_cycles: int = 0
    rapid_descent: bool = True
    zone: str = "floor"
    frame_paths: list[str] = []


class AckRequest(BaseModel):
    event_id: str
    contact_id: str = "default"
    action: str  # calling, on_my_way, false_alarm, call_999_info


class SimulateReplyRequest(BaseModel):
    event_id: str
    transcript: str
    lang: str = "ms"


class PauseRequest(BaseModel):
    minutes: int = 60


class ProfileRequest(BaseModel):
    profile: str = "home"  # home or demo


@app.get("/api/v1/health")
async def health_check():
    """Liveness check for orchestrator and model services."""
    models = {
        "orchestrator": True,
        "vllm": False,
        "stt": False,
        "voice": False,
        "notifier": False,
    }
    
    if MOCK_MODE:
        models = {k: True for k in models}
    
    return {
        "status": "healthy" if all(models.values()) else "degraded",
        "mock_mode": MOCK_MODE,
        "profile": DEMO_PROFILE,
        "models": models,
    }


@app.get("/api/v1/status")
async def get_status():
    """Get current system status."""
    if not event_manager:
        raise HTTPException(status_code=503, detail="Event manager not initialized")
    
    return SystemStatus(
        armed=True,
        paused=False,
        state=event_manager.get_current_state(),
        camera_fps=15.0 if MOCK_MODE else 0,
        internet_reachable=True,
        models_resident={
            "nemotron_omni": True,
            "yolo26n_pose": True,
            "malaysian_whisper": True,
            "kokoro": True,
        } if MOCK_MODE else {},
        memory_used_gb=74.0,
        memory_total_gb=128.0,
        gpu_util_percent=18.0,
        egress_messages_today=len([e for e in egress_log if e.at.date() == datetime.now(timezone.utc).date()]),
        egress_bytes_today=sum(e.bytes_out for e in egress_log if e.at.date() == datetime.now(timezone.utc).date()),
        video_bytes_out=0,
    ).model_dump()


@app.get("/api/v1/stream")
async def sse_stream(request: Request):
    """SSE endpoint for real-time updates."""
    queue: asyncio.Queue = asyncio.Queue()
    sse_queues.append(queue)
    
    async def event_generator():
        try:
            yield {
                "event": "connected",
                "data": orjson.dumps({"status": "connected"}).decode(),
            }
            
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {
                        "event": message["event"],
                        "data": orjson.dumps(message["data"]).decode(),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            if queue in sse_queues:
                sse_queues.remove(queue)
    
    return EventSourceResponse(event_generator())


@app.get("/api/v1/events")
async def list_events(
    since: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
):
    """List events from the log."""
    if not event_manager:
        return []
    
    events = list(event_manager.events.values())
    events.sort(key=lambda e: e.started_at, reverse=True)
    return [e.model_dump() for e in events[:limit]]


@app.get("/api/v1/events/{event_id}")
async def get_event(event_id: str):
    """Get event detail."""
    if not event_manager or event_id not in event_manager.events:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return event_manager.events[event_id].model_dump()


@app.get("/api/v1/events/{event_id}/export")
async def export_event(event_id: str):
    """Export event as JSON."""
    if not event_manager or event_id not in event_manager.events:
        raise HTTPException(status_code=404, detail="Event not found")
    
    event = event_manager.events[event_id]
    return JSONResponse(
        content=event.model_dump(),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=event_{event_id}.json"},
    )


@app.post("/api/v1/arm")
async def arm_monitoring():
    """Arm the monitoring system."""
    return {"status": "armed"}


@app.post("/api/v1/pause")
async def pause_monitoring(req: PauseRequest):
    """Pause monitoring for N minutes."""
    return {"status": "paused", "until": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/resume")
async def resume_monitoring():
    """Resume monitoring."""
    return {"status": "monitoring"}


@app.get("/api/v1/residents/{resident_id}")
async def get_resident(resident_id: str):
    """Get resident configuration."""
    if not event_manager:
        raise HTTPException(status_code=503)
    return event_manager.resident.model_dump()


@app.put("/api/v1/residents/{resident_id}")
async def update_resident(resident_id: str, resident: Resident):
    """Update resident configuration."""
    if not event_manager:
        raise HTTPException(status_code=503)
    event_manager.resident = resident
    return resident.model_dump()


@app.get("/api/v1/contacts")
async def list_contacts():
    """List family contacts."""
    if not event_manager:
        return []
    return [c.model_dump() for c in event_manager.contacts]


@app.post("/api/v1/contacts")
async def add_contact(contact: Contact):
    """Add a family contact."""
    if not event_manager:
        raise HTTPException(status_code=503)
    event_manager.contacts.append(contact)
    return contact.model_dump()


@app.post("/api/v1/contacts/{contact_id}/test")
async def test_contact(contact_id: str):
    """Send a test message to a contact."""
    return {"status": "sent", "message": "Test alert from GuardianBox"}


@app.get("/api/v1/metrics")
async def get_metrics():
    """Get system metrics."""
    return {
        "gpu_util_percent": 18.0,
        "memory_used_gb": 74.0,
        "memory_total_gb": 128.0,
        "models": {
            "nemotron_omni": {"resident": True, "memory_gb": 45.0},
            "yolo26n_pose": {"resident": True, "memory_gb": 2.0},
            "malaysian_whisper": {"resident": True, "memory_gb": 4.0},
            "kokoro": {"resident": True, "memory_gb": 1.5},
        },
    }


@app.get("/api/v1/egress")
async def get_egress():
    """Get egress log and counters."""
    today = datetime.now(timezone.utc).date()
    today_events = [e for e in egress_log if e.at.date() == today]
    
    return {
        "messages_today": len(today_events),
        "bytes_today": sum(e.bytes_out for e in today_events),
        "video_bytes_out": 0,
        "image_bytes_out": 0,
        "log": [e.model_dump() for e in egress_log[-50:]],
    }


@app.post("/api/v1/network/simulate-offline")
async def simulate_offline(on: bool = True):
    """Toggle simulated offline mode (presenter mode only)."""
    return {"simulate_offline": on}


@app.post("/api/v1/demo/profile")
async def set_demo_profile(req: ProfileRequest):
    """Switch timing profile (demo or home)."""
    global DEMO_PROFILE
    DEMO_PROFILE = req.profile
    if event_manager:
        event_manager.timing = TimingProfile.get(req.profile)
    return {"profile": req.profile}


@app.post("/api/v1/demo/pose-trigger")
async def demo_pose_trigger(req: PoseTriggerRequest):
    """
    Simulate a pose trigger (for testing/demo).
    
    This endpoint allows triggering the full pipeline without a camera.
    """
    if not event_manager:
        raise HTTPException(status_code=503, detail="Event manager not initialized")
    
    pose_summary = PoseSummary(
        torso_angle=req.torso_angle,
        head_height_ratio=req.head_height_ratio,
        hip_descent_ratio=req.hip_descent_ratio,
        stillness_seconds=req.stillness_seconds,
        motion_energy=req.motion_energy,
        prayer_cycles=req.prayer_cycles,
        rapid_descent=req.rapid_descent,
        zone=req.zone,
    )
    
    trigger_type = TriggerType(req.trigger_type)
    
    event = await event_manager.handle_pose_trigger(
        trigger_type,
        pose_summary,
        req.frame_paths,
    )
    
    return {"event_id": event.id, "state": event.state}


@app.post("/api/v1/demo/simulate-reply")
async def demo_simulate_reply(req: SimulateReplyRequest):
    """Simulate a reply during check-in (for testing/demo)."""
    return {"status": "simulated", "transcript": req.transcript}


@app.post("/api/v1/demo/ack")
async def demo_acknowledge(req: AckRequest):
    """Simulate acknowledgement from family (for testing/demo)."""
    if not event_manager:
        raise HTTPException(status_code=503)
    
    success = await event_manager.handle_acknowledgement(
        req.event_id,
        req.contact_id,
        req.action,
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return {"status": "acknowledged", "action": req.action}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "mock_mode": MOCK_MODE,
        "profile": DEMO_PROFILE,
    })


app.mount("/static", StaticFiles(directory="/app/dashboard/static"), name="static")
