"""
GuardianBox Mock Vision Service

Simulates YOLO pose detection and fall heuristics for testing.
Can replay scripted scenarios or generate random events.
"""

import os
import asyncio
import random
import httpx
from datetime import datetime
from typing import Optional
from enum import Enum

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel


app = FastAPI(title="GuardianBox Mock Vision")


ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
SCENARIO_INTERVAL = float(os.getenv("SCENARIO_INTERVAL", "30"))


class ScenarioType(str, Enum):
    FALL = "fall"
    PRAYER = "prayer"
    SITTING = "sitting"
    WALKING = "walking"
    NONE = "none"


current_scenario: ScenarioType = ScenarioType.NONE
running = False
http_client: Optional[httpx.AsyncClient] = None


SCENARIO_POSES = {
    ScenarioType.FALL: {
        "trigger_type": "fast_fall",
        "torso_angle": 78,
        "head_height_ratio": 0.15,
        "hip_descent_ratio": 0.55,
        "stillness_seconds": 5,
        "motion_energy": 0.05,
        "prayer_cycles": 0,
        "rapid_descent": True,
        "zone": "floor",
    },
    ScenarioType.PRAYER: {
        "trigger_type": "slow_low_posture",
        "torso_angle": 85,
        "head_height_ratio": 0.1,
        "hip_descent_ratio": 0.3,
        "stillness_seconds": 8,
        "motion_energy": 0.1,
        "prayer_cycles": 3,
        "rapid_descent": False,
        "zone": "prayer_area",
    },
    ScenarioType.SITTING: {
        "trigger_type": "slow_low_posture",
        "torso_angle": 45,
        "head_height_ratio": 0.4,
        "hip_descent_ratio": 0.2,
        "stillness_seconds": 15,
        "motion_energy": 0.15,
        "prayer_cycles": 0,
        "rapid_descent": False,
        "zone": "floor",
    },
}


class TriggerRequest(BaseModel):
    scenario: ScenarioType


async def get_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    return http_client


async def send_trigger(scenario: ScenarioType):
    """Send a pose trigger to the orchestrator."""
    if scenario not in SCENARIO_POSES:
        return
    
    client = await get_client()
    pose_data = SCENARIO_POSES[scenario].copy()
    
    try:
        response = await client.post(
            f"{ORCHESTRATOR_URL}/api/v1/demo/pose-trigger",
            json=pose_data,
        )
        print(f"Sent {scenario} trigger: {response.status_code}")
    except Exception as e:
        print(f"Failed to send trigger: {e}")


async def scenario_loop():
    """Background loop that runs scenarios."""
    global running, current_scenario
    
    while running:
        if current_scenario != ScenarioType.NONE:
            await send_trigger(current_scenario)
            current_scenario = ScenarioType.NONE
        
        await asyncio.sleep(1)


@app.on_event("startup")
async def startup():
    global running
    running = True
    asyncio.create_task(scenario_loop())


@app.on_event("shutdown")
async def shutdown():
    global running, http_client
    running = False
    if http_client:
        await http_client.aclose()


@app.get("/health")
async def health():
    return {"status": "healthy", "mock": True}


@app.post("/trigger")
async def trigger_scenario(req: TriggerRequest):
    """Trigger a specific scenario."""
    global current_scenario
    current_scenario = req.scenario
    return {"status": "triggered", "scenario": req.scenario}


@app.post("/trigger/{scenario}")
async def trigger_scenario_path(scenario: str):
    """Trigger a scenario by path parameter."""
    global current_scenario
    try:
        current_scenario = ScenarioType(scenario)
        return {"status": "triggered", "scenario": scenario}
    except ValueError:
        return {"status": "error", "message": f"Unknown scenario: {scenario}"}


@app.get("/scenarios")
async def list_scenarios():
    """List available scenarios."""
    return {
        "scenarios": [s.value for s in ScenarioType],
        "current": current_scenario.value,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
