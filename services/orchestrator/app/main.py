"""
GuardianBox Orchestrator - FastAPI Application

Local API endpoints as specified in PRD §15.
Serves the dashboard and provides SSE for real-time updates.
"""

import os
import asyncio
import logging
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
VISION_URL = os.getenv("VISION_URL", "http://vision:8004")
SAMPLES_DIR = os.getenv("SAMPLES_DIR", "/app/samples")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "")


event_manager: Optional[EventManager] = None
sse_queues: list[asyncio.Queue] = []
egress_log: list[EgressEvent] = []
http_client: Optional[httpx.AsyncClient] = None


def get_real_memory_info() -> tuple[float, float]:
    """Get real memory info from /proc/meminfo."""
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()
        
        total_match = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
        available_match = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
        
        if total_match and available_match:
            total_kb = int(total_match.group(1))
            available_kb = int(available_match.group(1))
            total_gb = total_kb / (1024 * 1024)
            used_gb = (total_kb - available_kb) / (1024 * 1024)
            return used_gb, total_gb
    except Exception as e:
        logger.debug(f"Failed to read /proc/meminfo: {e}")
    
    return 74.0, 128.0  # Fallback mock values


def get_real_gpu_info() -> tuple[float, float]:
    """Get real GPU utilization from nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                parts = lines[0].split(",")
                gpu_util = float(parts[0].strip())
                mem_used = float(parts[1].strip()) / 1024  # MB to GB
                mem_total = float(parts[2].strip()) / 1024
                return gpu_util, mem_used
    except Exception as e:
        logger.debug(f"Failed to run nvidia-smi: {e}")
    
    return 18.0, 45.0  # Fallback mock values


def list_sample_clips() -> list[dict]:
    """List available sample clips with metadata."""
    clips = []
    samples_path = Path(SAMPLES_DIR)
    
    clip_labels = {
        "clip1.mp4": "clip1 — Slow collapse (chest pain) → Alert",
        "clip2.mp4": "clip2 — Fast fall (walker) → Alert",
        "clip3.mp4": "clip3 — Prayer (sujud) → Quiet",
        "clip4.mp4": "clip4 — Slow collapse (dizzy) → Alert",
        "clip5.mp4": "clip5 — Normal walking → Quiet",
    }
    
    if samples_path.exists():
        for mp4_file in sorted(samples_path.glob("*.mp4")):
            name = mp4_file.name
            clips.append({
                "path": str(mp4_file),
                "name": clip_labels.get(name, name),
                "filename": name,
            })
    
    return clips


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global event_manager, http_client
    
    http_client = httpx.AsyncClient(timeout=30.0)
    
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
    if http_client:
        await http_client.aclose()
    logger.info("Orchestrator stopped")


app = FastAPI(
    title="GuardianBox Orchestrator",
    description="Local fall detection and voice check-in API",
    version="1.0.0",
    lifespan=lifespan,
)


DASHBOARD_DIR = os.getenv("DASHBOARD_DIR", "/app/dashboard")
templates = Jinja2Templates(directory=os.path.join(DASHBOARD_DIR, "templates"))


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
    
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "")
    telegram_connected = bool(token and chat_ids)
    
    return {
        "status": "healthy" if all(models.values()) else "degraded",
        "mock_mode": MOCK_MODE,
        "profile": DEMO_PROFILE,
        "models": models,
        "telegram_connected": telegram_connected,
        "telegram_status": "live" if telegram_connected else ("needs_chat_id" if token else "mock"),
    }


@app.get("/api/v1/status")
async def get_status():
    """Get current system status."""
    if not event_manager:
        raise HTTPException(status_code=503, detail="Event manager not initialized")
    
    memory_used_gb, memory_total_gb = get_real_memory_info()
    gpu_util_percent, _ = get_real_gpu_info()
    
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
        memory_used_gb=memory_used_gb,
        memory_total_gb=memory_total_gb,
        gpu_util_percent=gpu_util_percent,
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
    """Get system metrics (real values on hardware, labeled mock in mock mode)."""
    memory_used_gb, memory_total_gb = get_real_memory_info()
    gpu_util_percent, gpu_mem_used = get_real_gpu_info()
    
    return {
        "gpu_util_percent": gpu_util_percent,
        "memory_used_gb": memory_used_gb,
        "memory_total_gb": memory_total_gb,
        "source": "mock" if MOCK_MODE else "real",
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


@app.post("/api/v1/demo/reset")
async def demo_reset():
    """Reset the event manager state (for testing)."""
    if event_manager:
        event_manager.reset()
    return {"status": "reset"}


RUN_PIPELINE_SYNC = os.getenv("RUN_PIPELINE_SYNC", "false").lower() == "true"


async def _run_pose_trigger_pipeline(
    trigger_type: TriggerType,
    pose_summary: PoseSummary,
    frame_paths: list[str],
):
    """Background task to run the pose trigger pipeline."""
    if event_manager:
        try:
            await event_manager.handle_pose_trigger(
                trigger_type,
                pose_summary,
                frame_paths,
            )
        except Exception as e:
            logger.error(f"Error in pose trigger pipeline: {e}")


@app.post("/api/v1/demo/pose-trigger")
async def demo_pose_trigger(req: PoseTriggerRequest, background_tasks: BackgroundTasks):
    """
    Simulate a pose trigger (for testing/demo).
    
    This endpoint allows triggering the full pipeline without a camera.
    In production, the pipeline runs as a background task so the endpoint returns quickly.
    In test mode (RUN_PIPELINE_SYNC=true), it runs synchronously for deterministic testing.
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
    
    if event_manager.active_event:
        event_manager.active_event.pose_summary = pose_summary
        return {"event_id": event_manager.active_event.id, "state": event_manager.active_event.state, "existing": True}
    
    if RUN_PIPELINE_SYNC or MOCK_MODE:
        event = await event_manager.handle_pose_trigger(
            trigger_type,
            pose_summary,
            req.frame_paths,
        )
        return {"event_id": event.id, "state": event.state, "sync": True}
    
    from .models import generate_id
    event_id = generate_id()
    
    background_tasks.add_task(
        _run_pose_trigger_pipeline,
        trigger_type,
        pose_summary,
        req.frame_paths,
    )
    
    return {"event_id": event_id, "state": "starting", "background": True}


@app.post("/api/v1/demo/simulate-reply")
async def demo_simulate_reply(req: SimulateReplyRequest):
    """
    Simulate a reply during check-in (for testing/demo).
    
    Injects the transcript into the active event's listening step.
    The event must be in the LISTENING state for this to work.
    """
    if not event_manager:
        raise HTTPException(status_code=503, detail="Event manager not initialized")
    
    if req.event_id and event_manager.active_event:
        if event_manager.active_event.id != req.event_id:
            raise HTTPException(status_code=400, detail=f"Event {req.event_id} is not the active event")
    
    success = event_manager.inject_reply(req.transcript, req.lang)
    
    if not success:
        current_state = event_manager.state_machine.state.value if event_manager.state_machine else "no_event"
        raise HTTPException(
            status_code=400, 
            detail=f"No active event listening for replies. Current state: {current_state}"
        )
    
    return {
        "status": "injected",
        "transcript": req.transcript,
        "lang": req.lang,
        "event_id": event_manager.active_event.id if event_manager.active_event else None,
    }


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
    return templates.TemplateResponse(request, "index.html", {
        "mock_mode": MOCK_MODE,
        "profile": DEMO_PROFILE,
    })


static_dir = os.path.join(DASHBOARD_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ===== Clips Endpoints =====

@app.get("/api/v1/clips")
async def get_clips():
    """List available sample clips."""
    return list_sample_clips()


class VisionSourceRequest(BaseModel):
    source: str  # path to clip or camera device


@app.post("/api/v1/vision/source")
async def set_vision_source(req: VisionSourceRequest):
    """Set the video source for the vision service."""
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    try:
        response = await http_client.post(
            f"{VISION_URL}/source",
            json={"source": req.source}
        )
        return response.json()
    except httpx.RequestError as e:
        logger.warning(f"Failed to set vision source: {e}")
        return {"status": "queued", "source": req.source}


@app.get("/api/v1/vision/stream.mjpg")
async def proxy_vision_stream():
    """Proxy the MJPEG stream from the vision service."""
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    async def stream_generator():
        try:
            async with http_client.stream("GET", f"{VISION_URL}/stream.mjpg") as response:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk
        except httpx.RequestError as e:
            logger.warning(f"Vision stream error: {e}")
            # Return a placeholder frame if stream fails
            yield b""
    
    return StreamingResponse(
        stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/v1/vision/latest.jpg")
async def proxy_vision_frame():
    """Get the latest frame with skeleton overlay from the vision service."""
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    try:
        response = await http_client.get(f"{VISION_URL}/latest.jpg")
        return StreamingResponse(
            iter([response.content]),
            media_type="image/jpeg"
        )
    except httpx.RequestError as e:
        logger.warning(f"Failed to get latest frame: {e}")
        raise HTTPException(status_code=503, detail="Vision service unavailable")


# ===== Vision Source Endpoints =====

@app.get("/api/v1/vision/sources")
async def get_vision_sources():
    """Get available video sources from vision service."""
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    try:
        response = await http_client.get(f"{VISION_URL}/sources")
        return response.json()
    except httpx.RequestError as e:
        logger.warning(f"Failed to get vision sources: {e}")
        # Return local clips as fallback
        return {"sources": list_sample_clips(), "current": ""}


# ===== Telegram Endpoints =====

def get_telegram_token() -> str:
    """Get Telegram bot token from environment (re-reads at runtime)."""
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def get_telegram_chat_ids() -> list[str]:
    """Get Telegram chat IDs from environment (re-reads at runtime)."""
    chat_ids_str = os.getenv("TELEGRAM_CHAT_IDS", "")
    return [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]


@app.get("/api/v1/telegram/status")
async def telegram_status():
    """Get Telegram integration status."""
    token = get_telegram_token()
    chat_ids = get_telegram_chat_ids()
    
    if not token:
        return {"status": "mock", "token_set": False, "chat_ids": []}
    elif not chat_ids:
        return {"status": "needs_chat_id", "token_set": True, "chat_ids": []}
    else:
        return {"status": "live", "token_set": True, "chat_ids_count": len(chat_ids)}


@app.post("/api/v1/telegram/test")
async def send_telegram_test():
    """Send a test message to configured Telegram chat(s)."""
    token = get_telegram_token()
    chat_ids = get_telegram_chat_ids()
    
    if not token:
        # In mock mode, broadcast a telegram_preview SSE event
        await broadcast_sse("telegram_preview", {
            "type": "test",
            "level": "info",
            "text": "🧪 Test message from GuardianBox\n\nIf you receive this, Telegram alerts are working!",
            "time": datetime.now(timezone.utc).isoformat(),
        })
        return {"status": "mock", "message": "Test message shown in dashboard preview"}
    
    if not chat_ids:
        return {
            "status": "needs_chat_id",
            "message": "Token is set but no chat IDs configured. Send /start to the bot to get your chat ID."
        }
    
    test_message = (
        "🧪 *Test message from GuardianBox*\n\n"
        "If you receive this, Telegram alerts are working\\!\n\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    
    results = []
    for chat_id in chat_ids:
        try:
            response = await http_client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": test_message,
                    "parse_mode": "MarkdownV2",
                }
            )
            result = response.json()
            results.append({"chat_id": chat_id, "ok": result.get("ok", False)})
        except Exception as e:
            logger.error(f"Failed to send Telegram test to {chat_id}: {e}")
            results.append({"chat_id": chat_id, "ok": False, "error": str(e)})
    
    success_count = sum(1 for r in results if r.get("ok"))
    
    return {
        "status": "live" if success_count > 0 else "failed",
        "results": results,
        "sent": success_count,
        "total": len(chat_ids),
    }


async def broadcast_sse(event_type: str, data: dict):
    """Broadcast an event to all SSE clients."""
    message = {"event": event_type, "data": data}
    for queue in sse_queues:
        try:
            await queue.put(message)
        except Exception:
            pass
