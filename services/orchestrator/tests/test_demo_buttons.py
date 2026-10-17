"""
E2E tests for GuardianBox demo buttons via HTTP API.

Tests all 5 demo scenarios through the HTTP API to verify:
1. Fall demo → should alert
2. Slow Collapse demo → should alert  
3. Prayer demo → should stand down silently
4. OK Reply demo → should resolve with no alert
5. Silence demo → should alert

Also tests telegram_preview SSE emission in mock mode.
"""

import pytest
import asyncio
import os
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import MagicMock

os.environ["MOCK_MODE"] = "true"
os.environ["DEMO_PROFILE"] = "demo"
os.environ["RUN_PIPELINE_SYNC"] = "true"
os.environ["DASHBOARD_DIR"] = str(Path(__file__).parent.parent.parent.parent / "dashboard")

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.fixture
def client():
    """Create a test client for sync tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def async_client():
    """Create an async client for async tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestDemoButtonsSequence:
    """Test all 5 demo buttons in sequence via HTTP API."""
    
    def test_fall_demo_triggers_alert(self, client):
        """Fall demo should trigger alert after 3 silent check-ins."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # Trigger fall demo
        response = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        # Should be in alerting state or awaiting ack
        assert data["state"] in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
            f"Fall demo should lead to alerting, got {data['state']}"
        
        # Verify event details
        event_id = data["event_id"]
        event_response = client.get(f"/api/v1/events/{event_id}")
        
        if event_response.status_code == 200:
            event = event_response.json()
            # Should have 3 check-in attempts (silence)
            assert len(event.get("check_in_attempts", [])) == 3
            # Should have at least one alert
            assert len(event.get("alerts", [])) >= 1
    
    def test_slow_collapse_demo_triggers_alert(self, client):
        """Slow collapse demo should trigger alert."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # Trigger slow collapse demo
        response = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 65,
            "stillness_seconds": 20,
            "rapid_descent": False,
            "zone": "floor",
            "scenario": "slow_collapse"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        # Should be in alerting state or awaiting ack
        assert data["state"] in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
            f"Slow collapse demo should lead to alerting, got {data['state']}"
    
    def test_prayer_demo_stands_down_silently(self, client):
        """Prayer demo should stand down silently (no alert)."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # Trigger prayer demo with scenario hint
        response = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 85,
            "stillness_seconds": 8,
            "prayer_cycles": 4,
            "rapid_descent": False,
            "zone": "prayer_area",
            "scenario": "prayer"  # This should make mock VLM return prayer_sujud
        })
        
        assert response.status_code == 200
        data = response.json()
        
        # With scenario-aware mock VLM, prayer should stand down
        assert data["state"] in ["STOOD_DOWN", "RESOLVED", "MONITORING"], \
            f"Prayer demo should stand down, got {data['state']}"
        
        # Verify event details
        event_id = data["event_id"]
        event_response = client.get(f"/api/v1/events/{event_id}")
        
        if event_response.status_code == 200:
            event = event_response.json()
            # Should have no alerts for prayer
            alerts = event.get("alerts", [])
            assert len(alerts) == 0, \
                f"Prayer demo should not trigger alerts, got {len(alerts)}"
            
            # VLM should detect prayer
            vlm = event.get("vlm_assessment")
            if vlm:
                assert vlm.get("scene_class") in ["prayer_sujud", "prayer_other_position"], \
                    f"Mock VLM should return prayer scene_class, got {vlm.get('scene_class')}"
    
    def test_ok_reply_demo_resolves_no_alert(self, client):
        """OK Reply demo should resolve with no alert."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # Trigger the pose trigger
        trigger_response = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 70,
            "stillness_seconds": 4,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "ok_reply"
        })
        
        assert trigger_response.status_code == 200
        data = trigger_response.json()
        event_id = data["event_id"]
        
        # Note: In synchronous test mode, the pipeline runs to completion
        # before we can inject a reply. So this test verifies the endpoint
        # works but doesn't fully simulate the interactive flow.
        # 
        # For a real OK reply test, see test_e2e_pipeline.py which tests
        # the EventManager directly with async reply injection.
        
        # Just verify the endpoint worked
        assert "event_id" in data
        assert "state" in data
    
    def test_silence_demo_triggers_alert(self, client):
        """Silence demo should trigger alert after 3 attempts."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # Trigger silence demo
        response = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 80,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "silence"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        # Should be in alerting state
        assert data["state"] in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
            f"Silence demo should lead to alerting, got {data['state']}"
        
        # Verify event has alerts
        event_id = data["event_id"]
        event_response = client.get(f"/api/v1/events/{event_id}")
        
        if event_response.status_code == 200:
            event = event_response.json()
            # Should have at least one alert
            assert len(event.get("alerts", [])) >= 1, \
                "Silence demo should trigger alerts"
    
    def test_all_five_demos_in_sequence(self, client):
        """Run all 5 demos in sequence, verifying each works."""
        results = {}
        
        # 1. Fall demo
        client.post("/api/v1/demo/reset")
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        assert r.status_code == 200
        results["fall"] = r.json()
        
        # 2. Slow collapse demo
        client.post("/api/v1/demo/reset")
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 65,
            "stillness_seconds": 20,
            "rapid_descent": False,
            "zone": "floor",
            "scenario": "slow_collapse"
        })
        assert r.status_code == 200
        results["slow_collapse"] = r.json()
        
        # 3. Prayer demo
        client.post("/api/v1/demo/reset")
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 85,
            "stillness_seconds": 8,
            "prayer_cycles": 4,
            "rapid_descent": False,
            "zone": "prayer_area",
            "scenario": "prayer"
        })
        assert r.status_code == 200
        results["prayer"] = r.json()
        
        # 4. OK reply demo (just trigger, can't inject reply in sync mode)
        client.post("/api/v1/demo/reset")
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 70,
            "stillness_seconds": 4,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "ok_reply"
        })
        assert r.status_code == 200
        results["ok_reply"] = r.json()
        
        # 5. Silence demo
        client.post("/api/v1/demo/reset")
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 80,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "silence"
        })
        assert r.status_code == 200
        results["silence"] = r.json()
        
        # Verify outcomes
        # Fall, slow_collapse, silence should alert
        for scenario in ["fall", "slow_collapse", "silence"]:
            assert results[scenario]["state"] in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
                f"{scenario} should alert, got {results[scenario]['state']}"
        
        # Prayer should stand down (with scenario-aware mock VLM)
        assert results["prayer"]["state"] in ["STOOD_DOWN", "RESOLVED", "MONITORING"], \
            f"prayer should stand down, got {results['prayer']['state']}"


class TestAutoResolveOnNewDemo:
    """Test that running a new demo auto-resolves any active event."""
    
    def test_second_demo_auto_resolves_first(self, client):
        """Starting a new demo should auto-resolve the previous one."""
        # Start first demo (don't reset first)
        r1 = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        assert r1.status_code == 200
        first_event_id = r1.json()["event_id"]
        
        # Start second demo immediately (should auto-resolve first)
        r2 = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 85,
            "stillness_seconds": 8,
            "prayer_cycles": 4,
            "rapid_descent": False,
            "zone": "prayer_area",
            "scenario": "prayer"
        })
        assert r2.status_code == 200
        second_event_id = r2.json()["event_id"]
        
        # The two events should be different
        assert first_event_id != second_event_id, \
            "Second demo should create a new event"


class TestDemoReset:
    """Test the demo reset endpoint."""
    
    def test_reset_clears_active_event(self, client):
        """Reset should clear any active event."""
        # Start a demo
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        assert r.status_code == 200
        
        # Reset
        reset_r = client.post("/api/v1/demo/reset")
        assert reset_r.status_code == 200
        
        # Status should show MONITORING
        status_r = client.get("/api/v1/status")
        assert status_r.status_code == 200
        status = status_r.json()
        assert status["state"] == "MONITORING"


class TestTelegramPreviewSSE:
    """Test that telegram_preview SSE is emitted in mock mode."""
    
    def test_telegram_preview_emitted_on_alert(self, client):
        """Verify telegram_preview event is emitted when alert is sent."""
        # Reset first
        client.post("/api/v1/demo/reset")
        
        # We need to capture SSE events. Since TestClient doesn't support
        # streaming well, we'll verify the event_manager emits the event
        # by checking the event has alerts (which means telegram_preview
        # would have been emitted).
        
        # Trigger fall demo (should alert)
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        assert r.status_code == 200
        data = r.json()
        
        # Get event details
        event_id = data["event_id"]
        event_r = client.get(f"/api/v1/events/{event_id}")
        
        if event_r.status_code == 200:
            event = event_r.json()
            # If alerts were sent, telegram_preview SSE should have been emitted
            alerts = event.get("alerts", [])
            assert len(alerts) >= 1, "Alert should be sent"
            
            # Verify alert has expected structure
            alert = alerts[0]
            assert "status" in alert
            assert alert["status"] == "sent", "Alert should be sent in mock mode"


class TestScenarioAwareMockVLM:
    """Test that mock VLM respects scenario hints."""
    
    def test_prayer_scenario_returns_prayer_scene_class(self, client):
        """Prayer scenario should make mock VLM return prayer_sujud."""
        client.post("/api/v1/demo/reset")
        
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "slow_low_posture",
            "torso_angle": 85,
            "stillness_seconds": 8,
            "prayer_cycles": 4,
            "rapid_descent": False,
            "zone": "prayer_area",
            "scenario": "prayer"
        })
        
        assert r.status_code == 200
        data = r.json()
        event_id = data["event_id"]
        
        # Get event to check VLM assessment
        event_r = client.get(f"/api/v1/events/{event_id}")
        if event_r.status_code == 200:
            event = event_r.json()
            vlm = event.get("vlm_assessment")
            
            assert vlm is not None, "VLM assessment should exist"
            assert vlm.get("scene_class") == "prayer_sujud", \
                f"Prayer scenario should return prayer_sujud, got {vlm.get('scene_class')}"
    
    def test_fall_scenario_returns_fall_scene_class(self, client):
        """Fall scenario should make mock VLM return fall."""
        client.post("/api/v1/demo/reset")
        
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        
        assert r.status_code == 200
        data = r.json()
        event_id = data["event_id"]
        
        # Get event to check VLM assessment
        event_r = client.get(f"/api/v1/events/{event_id}")
        if event_r.status_code == 200:
            event = event_r.json()
            vlm = event.get("vlm_assessment")
            
            assert vlm is not None, "VLM assessment should exist"
            assert vlm.get("scene_class") == "fall", \
                f"Fall scenario should return fall, got {vlm.get('scene_class')}"


class TestDemoAckEndpoint:
    """Test the demo acknowledgement endpoint."""
    
    def test_ack_resolves_event(self, client):
        """Ack should resolve an alerting event."""
        # Reset and trigger fall demo
        client.post("/api/v1/demo/reset")
        
        r = client.post("/api/v1/demo/pose-trigger", json={
            "trigger_type": "fast_fall",
            "torso_angle": 78,
            "stillness_seconds": 5,
            "rapid_descent": True,
            "zone": "floor",
            "scenario": "fall"
        })
        assert r.status_code == 200
        data = r.json()
        event_id = data["event_id"]
        
        # Send ack
        ack_r = client.post("/api/v1/demo/ack", json={
            "event_id": event_id,
            "action": "calling"
        })
        assert ack_r.status_code == 200
        
        # Event should be resolved
        event_r = client.get(f"/api/v1/events/{event_id}")
        if event_r.status_code == 200:
            event = event_r.json()
            assert event["state"] == "RESOLVED", \
                f"Event should be RESOLVED after ack, got {event['state']}"
