"""
GuardianBox Telegram Notifier Service

Sends text-only alerts to family via Telegram Bot API.
Implements a strict payload guard that blocks any media sending.

PRD Requirements:
- FR-ALR-1: sendMessage with inline_keyboard, callback handling via getUpdates
- FR-ALR-3: ALLOWLIST of methods - sendMessage, answerCallbackQuery, getUpdates, editMessageText
- Never send photos, videos, audio, or documents
"""

import os
import asyncio
import logging
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import httpx


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"
EGRESS_PROXY_URL = os.getenv("EGRESS_PROXY_URL", "http://egress-proxy:8004")


ALLOWED_METHODS = frozenset([
    "sendMessage",
    "editMessageText",
    "answerCallbackQuery",
    "getUpdates",
])

BLOCKED_METHODS = frozenset([
    "sendPhoto",
    "sendVideo",
    "sendAudio",
    "sendVoice",
    "sendDocument",
    "sendAnimation",
    "sendVideoNote",
    "sendMediaGroup",
    "sendSticker",
    "sendLocation",
    "sendVenue",
    "sendContact",
])


app = FastAPI(title="GuardianBox Notifier")


egress_log: List[Dict] = []
callback_handlers: Dict[str, Dict] = {}
last_update_id = 0
http_client: Optional[httpx.AsyncClient] = None


class SendAlertRequest(BaseModel):
    chat_id: str
    text: str = Field(max_length=1024)
    event_id: str
    alert_id: str
    level: str = "urgent"
    language: str = "en"


class AckCallbackRequest(BaseModel):
    callback_query_id: str


class PayloadGuardError(Exception):
    """Raised when an attempt is made to send media."""
    pass


def sign_callback_data(event_id: str, action: str) -> str:
    """Create signed callback data for inline buttons."""
    data = f"{event_id}:{action}"
    if TELEGRAM_BOT_TOKEN:
        signature = hmac.new(
            TELEGRAM_BOT_TOKEN.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()[:8]
        return f"{data}:{signature}"
    return data


def verify_callback_data(callback_data: str) -> tuple[str, str, bool]:
    """Verify and parse callback data."""
    parts = callback_data.split(":")
    if len(parts) < 2:
        return "", "", False
    
    event_id = parts[0]
    action = parts[1]
    
    if len(parts) >= 3 and TELEGRAM_BOT_TOKEN:
        provided_sig = parts[2]
        expected_data = f"{event_id}:{action}"
        expected_sig = hmac.new(
            TELEGRAM_BOT_TOKEN.encode(),
            expected_data.encode(),
            hashlib.sha256
        ).hexdigest()[:8]
        
        if not hmac.compare_digest(provided_sig, expected_sig):
            return event_id, action, False
    
    return event_id, action, True


def build_inline_keyboard(event_id: str, language: str = "en") -> Dict:
    """Build inline keyboard with alert response buttons."""
    if language == "ms":
        buttons = [
            [
                {"text": "📞 Saya telefon sekarang", "callback_data": sign_callback_data(event_id, "calling")},
                {"text": "🏃 Saya dalam perjalanan", "callback_data": sign_callback_data(event_id, "on_my_way")},
            ],
            [
                {"text": "✅ Amaran palsu", "callback_data": sign_callback_data(event_id, "false_alarm")},
                {"text": "🆘 Apa perlu buat", "callback_data": sign_callback_data(event_id, "call_999_info")},
            ],
        ]
    elif language == "zh":
        buttons = [
            [
                {"text": "📞 我现在打电话", "callback_data": sign_callback_data(event_id, "calling")},
                {"text": "🏃 我在路上", "callback_data": sign_callback_data(event_id, "on_my_way")},
            ],
            [
                {"text": "✅ 误报", "callback_data": sign_callback_data(event_id, "false_alarm")},
                {"text": "🆘 该怎么做", "callback_data": sign_callback_data(event_id, "call_999_info")},
            ],
        ]
    else:
        buttons = [
            [
                {"text": "📞 I'm calling now", "callback_data": sign_callback_data(event_id, "calling")},
                {"text": "🏃 On my way", "callback_data": sign_callback_data(event_id, "on_my_way")},
            ],
            [
                {"text": "✅ False alarm", "callback_data": sign_callback_data(event_id, "false_alarm")},
                {"text": "🆘 What to do", "callback_data": sign_callback_data(event_id, "call_999_info")},
            ],
        ]
    
    return {"inline_keyboard": buttons}


async def get_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    return http_client


async def call_telegram_api(method: str, params: Dict) -> Dict:
    """
    Call the Telegram Bot API with strict method allowlist.
    
    PAYLOAD GUARD: Only methods in ALLOWED_METHODS can be called.
    Any attempt to call a media-sending method raises PayloadGuardError.
    """
    if method in BLOCKED_METHODS:
        error_msg = f"PAYLOAD GUARD: Blocked attempt to call {method}"
        logger.error(error_msg)
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": "api.telegram.org",
            "method": method,
            "bytes_out": 0,
            "bytes_in": 0,
            "allowed": False,
            "error": "blocked_by_payload_guard",
        })
        raise PayloadGuardError(error_msg)
    
    if method not in ALLOWED_METHODS:
        error_msg = f"PAYLOAD GUARD: Unknown method {method} not in allowlist"
        logger.warning(error_msg)
        raise PayloadGuardError(error_msg)
    
    if MOCK_MODE:
        logger.info(f"[MOCK] Telegram API call: {method}")
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": "api.telegram.org",
            "method": method,
            "bytes_out": len(str(params)),
            "bytes_in": 100,
            "allowed": True,
            "mock": True,
        })
        
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 12345}}
        elif method == "editMessageText":
            return {"ok": True, "result": {"message_id": params.get("message_id", 12345)}}
        elif method == "answerCallbackQuery":
            return {"ok": True, "result": True}
        elif method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": {}}
    
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured")
    
    client = await get_client()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    
    try:
        response = await client.post(url, json=params)
        response_data = response.json()
        
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": "api.telegram.org",
            "method": method,
            "bytes_out": len(str(params)),
            "bytes_in": len(response.content),
            "allowed": True,
        })
        
        return response_data
        
    except Exception as e:
        logger.error(f"Telegram API error: {e}")
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": "api.telegram.org",
            "method": method,
            "bytes_out": len(str(params)),
            "bytes_in": 0,
            "allowed": True,
            "error": str(e),
        })
        raise


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "mock_mode": MOCK_MODE,
        "token_configured": bool(TELEGRAM_BOT_TOKEN),
    }


@app.post("/send")
async def send_alert(req: SendAlertRequest):
    """
    Send a text alert to a Telegram chat.
    
    Text only - no media ever. Max 1024 characters.
    """
    if len(req.text) > 1024:
        req.text = req.text[:1021] + "..."
    
    keyboard = build_inline_keyboard(req.event_id, req.language)
    
    params = {
        "chat_id": req.chat_id,
        "text": req.text,
        "reply_markup": keyboard,
        "parse_mode": "HTML",
    }
    
    try:
        result = await call_telegram_api("sendMessage", params)
        
        if result.get("ok"):
            message_id = result["result"]["message_id"]
            
            callback_handlers[req.event_id] = {
                "alert_id": req.alert_id,
                "chat_id": req.chat_id,
                "message_id": message_id,
            }
            
            return {
                "status": "sent",
                "message_id": message_id,
                "event_id": req.event_id,
            }
        else:
            return {
                "status": "failed",
                "error": result.get("description", "Unknown error"),
            }
            
    except PayloadGuardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/update/{event_id}")
async def update_alert(event_id: str, text: str):
    """Update an existing alert message."""
    if event_id not in callback_handlers:
        raise HTTPException(status_code=404, detail="Event not found")
    
    handler = callback_handlers[event_id]
    
    params = {
        "chat_id": handler["chat_id"],
        "message_id": handler["message_id"],
        "text": text,
        "parse_mode": "HTML",
    }
    
    try:
        result = await call_telegram_api("editMessageText", params)
        return {"status": "updated" if result.get("ok") else "failed"}
    except PayloadGuardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/answer-callback")
async def answer_callback(req: AckCallbackRequest):
    """Answer a callback query from an inline button."""
    try:
        result = await call_telegram_api("answerCallbackQuery", {
            "callback_query_id": req.callback_query_id,
        })
        return {"status": "answered" if result.get("ok") else "failed"}
    except PayloadGuardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/poll")
async def poll_updates():
    """
    Poll for updates (callback queries from button presses).
    
    Uses long polling - no inbound port needed.
    """
    global last_update_id
    
    try:
        result = await call_telegram_api("getUpdates", {
            "offset": last_update_id + 1,
            "timeout": 30,
            "allowed_updates": ["callback_query"],
        })
        
        updates = result.get("result", [])
        callbacks = []
        
        for update in updates:
            last_update_id = max(last_update_id, update["update_id"])
            
            if "callback_query" in update:
                cq = update["callback_query"]
                event_id, action, valid = verify_callback_data(cq.get("data", ""))
                
                if valid:
                    callbacks.append({
                        "callback_query_id": cq["id"],
                        "event_id": event_id,
                        "action": action,
                        "from_user": cq["from"]["first_name"],
                        "chat_id": str(cq["message"]["chat"]["id"]),
                    })
        
        return {"updates": callbacks}
        
    except PayloadGuardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/egress")
async def get_egress():
    """Get egress log for monitoring."""
    today = datetime.now(timezone.utc).date()
    today_logs = [e for e in egress_log if e["at"].startswith(str(today))]
    
    return {
        "total_requests": len(egress_log),
        "today_requests": len(today_logs),
        "today_bytes_out": sum(e.get("bytes_out", 0) for e in today_logs),
        "blocked_attempts": len([e for e in egress_log if not e.get("allowed", True)]),
        "log": egress_log[-100:],
    }


@app.post("/test-payload-guard")
async def test_payload_guard():
    """
    Test endpoint to verify payload guard works.
    
    Attempts to call blocked methods and verifies they are rejected.
    """
    results = {}
    
    for method in ["sendPhoto", "sendVideo", "sendAudio", "sendDocument"]:
        try:
            await call_telegram_api(method, {"chat_id": "test"})
            results[method] = "ERROR: Should have been blocked!"
        except PayloadGuardError:
            results[method] = "BLOCKED (correct)"
        except Exception as e:
            results[method] = f"ERROR: {e}"
    
    for method in ["sendMessage", "getUpdates"]:
        try:
            await call_telegram_api(method, {"chat_id": "test", "text": "test"})
            results[method] = "ALLOWED (correct)"
        except PayloadGuardError:
            results[method] = "ERROR: Should have been allowed!"
        except Exception as e:
            results[method] = f"ALLOWED but error: {e}"
    
    return {"payload_guard_test": results}


@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.aclose()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
