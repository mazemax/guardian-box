"""
GuardianBox Mock VLM Service

Simulates the vLLM OpenAI-compatible API for testing without a GPU.
Provides controllable responses for demo scenarios.
"""

import os
import json
import random
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(title="GuardianBox Mock VLM")


SCENARIO = os.getenv("VLM_SCENARIO", "fall")


MOCK_RESPONSES = {
    "fall": {
        "person_visible": True,
        "scene_class": "fall",
        "posture": "lying_floor",
        "on_prayer_mat_or_area": False,
        "movement": "none_visible",
        "distress_signs": ["not_moving", "awkward_limb_position"],
        "fall_likelihood": 0.87,
        "confidence": 0.84,
        "description": "Person lying on floor near sofa, not moving, appears to have fallen",
        "reasons": ["horizontal posture", "no movement for several seconds", "near furniture edge"]
    },
    "prayer_sujud": {
        "person_visible": True,
        "scene_class": "prayer_sujud",
        "posture": "prostrate_forehead_down",
        "on_prayer_mat_or_area": True,
        "movement": "small",
        "distress_signs": [],
        "fall_likelihood": 0.05,
        "confidence": 0.92,
        "description": "Person in sujud position on prayer mat, normal prayer posture",
        "reasons": ["on prayer mat", "orderly prostration posture", "hands placed beside head"]
    },
    "prayer_other": {
        "person_visible": True,
        "scene_class": "prayer_other_position",
        "posture": "kneeling",
        "on_prayer_mat_or_area": True,
        "movement": "small",
        "distress_signs": [],
        "fall_likelihood": 0.03,
        "confidence": 0.89,
        "description": "Person in sitting prayer position on prayer mat",
        "reasons": ["on prayer mat", "orderly sitting posture", "prayer cycle visible"]
    },
    "sitting_floor": {
        "person_visible": True,
        "scene_class": "sitting_on_floor_resting",
        "posture": "sitting_floor",
        "on_prayer_mat_or_area": False,
        "movement": "small",
        "distress_signs": [],
        "fall_likelihood": 0.12,
        "confidence": 0.78,
        "description": "Person sitting cross-legged on floor, appears to be resting",
        "reasons": ["upright torso", "relaxed posture", "no distress signs"]
    },
    "lying_sofa": {
        "person_visible": True,
        "scene_class": "lying_on_sofa_or_bed",
        "posture": "lying_furniture",
        "on_prayer_mat_or_area": False,
        "movement": "small",
        "distress_signs": [],
        "fall_likelihood": 0.08,
        "confidence": 0.85,
        "description": "Person lying on sofa, appears to be resting comfortably",
        "reasons": ["on furniture", "comfortable position", "no distress signs"]
    },
    "unclear": {
        "person_visible": True,
        "scene_class": "lying_on_floor_unclear_cause",
        "posture": "lying_floor",
        "on_prayer_mat_or_area": False,
        "movement": "none_visible",
        "distress_signs": ["not_moving"],
        "fall_likelihood": 0.65,
        "confidence": 0.52,
        "description": "Person lying on floor, cause unclear, need to check",
        "reasons": ["horizontal on floor", "limited visibility", "no clear context"]
    },
    "no_person": {
        "person_visible": False,
        "scene_class": "no_person_or_unclear",
        "posture": "unknown",
        "on_prayer_mat_or_area": False,
        "movement": "none_visible",
        "distress_signs": [],
        "fall_likelihood": 0.0,
        "confidence": 0.95,
        "description": "No person visible in the frame",
        "reasons": ["empty room", "no human detected"]
    }
}


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list
    temperature: float = 0.2
    max_tokens: int = 500
    response_format: Optional[dict] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list


@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible)."""
    return {
        "object": "list",
        "data": [
            {
                "id": "nemotron_3_nano_omni",
                "object": "model",
                "created": 1700000000,
                "owned_by": "nvidia",
            }
        ]
    }


@app.post("/v1/chat/completions")
async def chat_completion(request: ChatCompletionRequest):
    """
    Mock chat completion endpoint.
    
    Returns pre-defined responses based on the VLM_SCENARIO env var
    or detected hints in the prompt.
    """
    await asyncio.sleep(random.uniform(0.3, 0.8))
    
    scenario = SCENARIO
    
    user_message = ""
    for msg in request.messages:
        if msg.get("role") == "user":
            user_message = msg.get("content", "").lower()
            break
    
    if "prayer" in user_message or "sujud" in user_message:
        scenario = "prayer_sujud"
    elif "sofa" in user_message or "bed" in user_message:
        scenario = "lying_sofa"
    elif "sitting" in user_message:
        scenario = "sitting_floor"
    
    response_data = MOCK_RESPONSES.get(scenario, MOCK_RESPONSES["fall"])
    
    return ChatCompletionResponse(
        id=f"chatcmpl-{random.randint(100000, 999999)}",
        created=int(datetime.now().timestamp()),
        model=request.model,
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(response_data),
                },
                "finish_reason": "stop",
            }
        ],
    )


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "mock": True, "scenario": SCENARIO}


@app.post("/scenario/{scenario_name}")
async def set_scenario(scenario_name: str):
    """Set the mock scenario for testing."""
    global SCENARIO
    if scenario_name not in MOCK_RESPONSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario. Available: {list(MOCK_RESPONSES.keys())}"
        )
    SCENARIO = scenario_name
    return {"scenario": SCENARIO}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
