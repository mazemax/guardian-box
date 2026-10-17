"""
GuardianBox Mock Voice/TTS Service

Simulates voice clip playback and TTS for testing.
Logs all phrases that would be spoken.
"""

import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="GuardianBox Mock Voice")


played_phrases: List[Dict] = []


PHRASE_DURATIONS_MS = {
    "soft": 2000,
    "checkin_1": 4000,
    "checkin_2": 4000,
    "checkin_3": 4000,
    "ack_ok": 3000,
    "recheck": 4000,
    "alert_sent": 5000,
    "alert_ack": 2000,
    "offline": 5000,
}


class PlayRequest(BaseModel):
    phrase_id: str
    lang: str = "ms"
    honorific: str = "Pakcik"
    name: str = "Ahmad"


class SpeakRequest(BaseModel):
    text: str
    lang: str = "en"
    speed: float = 0.9


class GenerateRequest(BaseModel):
    phrase_id: str
    lang: str
    text: str
    voice: str = "default"


@app.get("/health")
async def health():
    return {"status": "healthy", "mock": True}


@app.post("/play")
async def play_phrase(req: PlayRequest):
    """Simulate playing a pre-rendered phrase."""
    duration_ms = PHRASE_DURATIONS_MS.get(req.phrase_id, 3000)
    
    await asyncio.sleep(duration_ms / 1000)
    
    played = {
        "phrase_id": req.phrase_id,
        "lang": req.lang,
        "honorific": req.honorific,
        "name": req.name,
        "duration_ms": duration_ms,
        "played_at": datetime.now().isoformat(),
    }
    played_phrases.append(played)
    
    return {
        "status": "played",
        "phrase_id": req.phrase_id,
        "duration_ms": duration_ms,
    }


@app.post("/speak")
async def speak_text(req: SpeakRequest):
    """Simulate TTS synthesis and playback."""
    words = len(req.text.split())
    duration_ms = int(words * 300 / req.speed)
    
    await asyncio.sleep(duration_ms / 1000)
    
    played = {
        "text": req.text,
        "lang": req.lang,
        "duration_ms": duration_ms,
        "played_at": datetime.now().isoformat(),
    }
    played_phrases.append(played)
    
    return {
        "status": "spoken",
        "text": req.text[:50] + "..." if len(req.text) > 50 else req.text,
        "duration_ms": duration_ms,
    }


@app.post("/generate")
async def generate_clip(req: GenerateRequest):
    """Simulate generating a voice clip."""
    await asyncio.sleep(2.0)
    
    return {
        "status": "generated",
        "phrase_id": req.phrase_id,
        "lang": req.lang,
        "path": f"/clips/{req.phrase_id}_{req.lang}.wav",
    }


@app.get("/played")
async def get_played_phrases():
    """Get list of played phrases (for testing)."""
    return {"phrases": played_phrases[-50:]}


@app.delete("/played")
async def clear_played_phrases():
    """Clear the played phrases log."""
    global played_phrases
    played_phrases = []
    return {"status": "cleared"}


@app.get("/clips")
async def list_clips():
    """List available clips (mock)."""
    return {
        "clips": [
            {"phrase_id": pid, "lang": lang, "available": True}
            for pid in PHRASE_DURATIONS_MS.keys()
            for lang in ["ms", "en", "zh"]
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
