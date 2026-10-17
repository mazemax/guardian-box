"""
GuardianBox Mock STT Service

Simulates Malaysian Whisper STT for testing.
Can return pre-configured responses for demo scenarios.
"""

import os
import asyncio
import random
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="GuardianBox Mock STT")


RESPONSE_MODE = os.getenv("STT_RESPONSE_MODE", "silence")


MOCK_RESPONSES = {
    "silence": {
        "transcript": "",
        "detected_lang": None,
        "no_speech_prob": 0.95,
        "vad_speech_ms": 0,
    },
    "ok_malay": {
        "transcript": "okey saya okey",
        "detected_lang": "ms",
        "no_speech_prob": 0.1,
        "vad_speech_ms": 1200,
    },
    "ok_english": {
        "transcript": "I'm fine",
        "detected_lang": "en",
        "no_speech_prob": 0.08,
        "vad_speech_ms": 900,
    },
    "ok_chinese": {
        "transcript": "没事",
        "detected_lang": "zh",
        "no_speech_prob": 0.12,
        "vad_speech_ms": 600,
    },
    "help_malay": {
        "transcript": "tolong sakit",
        "detected_lang": "ms",
        "no_speech_prob": 0.05,
        "vad_speech_ms": 1500,
    },
    "help_english": {
        "transcript": "help me it hurts",
        "detected_lang": "en",
        "no_speech_prob": 0.06,
        "vad_speech_ms": 1800,
    },
    "help_chinese": {
        "transcript": "救命 好痛",
        "detected_lang": "zh",
        "no_speech_prob": 0.07,
        "vad_speech_ms": 1400,
    },
    "cannot_get_up": {
        "transcript": "tak boleh bangun",
        "detected_lang": "ms",
        "no_speech_prob": 0.1,
        "vad_speech_ms": 1600,
    },
    "unclear": {
        "transcript": "mmm ah",
        "detected_lang": None,
        "no_speech_prob": 0.4,
        "vad_speech_ms": 400,
    },
    "not_hurt": {
        "transcript": "tak sakit saya okey",
        "detected_lang": "ms",
        "no_speech_prob": 0.08,
        "vad_speech_ms": 1400,
    },
}


class ListenRequest(BaseModel):
    duration_s: float = 8.0
    max_duration_s: float = 15.0
    languages: List[str] = ["ms", "en", "zh"]


class TranscribeRequest(BaseModel):
    audio_path: str
    languages: List[str] = ["ms", "en", "zh"]


class SetResponseRequest(BaseModel):
    mode: str


@app.get("/health")
async def health():
    return {"status": "healthy", "mock": True, "response_mode": RESPONSE_MODE}


@app.post("/listen")
async def listen(req: ListenRequest):
    """Simulate listening for speech."""
    await asyncio.sleep(req.duration_s)
    
    response = MOCK_RESPONSES.get(RESPONSE_MODE, MOCK_RESPONSES["silence"]).copy()
    response["listen_ms"] = int(req.duration_s * 1000)
    
    return response


@app.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    """Simulate transcribing audio file."""
    await asyncio.sleep(0.5)
    
    response = MOCK_RESPONSES.get(RESPONSE_MODE, MOCK_RESPONSES["silence"]).copy()
    response["listen_ms"] = 0
    
    return response


@app.post("/response-mode")
async def set_response_mode(req: SetResponseRequest):
    """Set the mock response mode."""
    global RESPONSE_MODE
    
    if req.mode not in MOCK_RESPONSES:
        return {
            "status": "error",
            "message": f"Unknown mode. Available: {list(MOCK_RESPONSES.keys())}"
        }
    
    RESPONSE_MODE = req.mode
    return {"status": "ok", "mode": RESPONSE_MODE}


@app.post("/response-mode/{mode}")
async def set_response_mode_path(mode: str):
    """Set the mock response mode via path."""
    global RESPONSE_MODE
    
    if mode not in MOCK_RESPONSES:
        return {
            "status": "error",
            "message": f"Unknown mode. Available: {list(MOCK_RESPONSES.keys())}"
        }
    
    RESPONSE_MODE = mode
    return {"status": "ok", "mode": RESPONSE_MODE}


@app.get("/modes")
async def list_modes():
    """List available response modes."""
    return {
        "modes": list(MOCK_RESPONSES.keys()),
        "current": RESPONSE_MODE,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
