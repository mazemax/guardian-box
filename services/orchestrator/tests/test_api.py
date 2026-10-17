"""
Tests for GuardianBox Orchestrator API endpoints.

These tests verify the FastAPI endpoints work correctly, including
the dashboard and demo endpoints.
"""

import pytest
from fastapi.testclient import TestClient
import os
from pathlib import Path

os.environ["MOCK_MODE"] = "true"
os.environ["DEMO_PROFILE"] = "demo"
os.environ["DASHBOARD_DIR"] = str(Path(__file__).parent.parent.parent.parent / "dashboard")

from app.main import app


@pytest.fixture
def client():
    """Create a test client."""
    with TestClient(app) as c:
        yield c


class TestDashboard:
    """Test dashboard endpoint."""
    
    def test_dashboard_returns_200(self, client):
        """GET / should return 200 with HTML content."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    
    def test_dashboard_contains_guardianbox(self, client):
        """Dashboard HTML should contain GuardianBox."""
        response = client.get("/")
        assert b"GuardianBox" in response.content or b"guardianbox" in response.content.lower()


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    def test_health_returns_200(self, client):
        """GET /api/v1/health should return 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
    
    def test_health_returns_status(self, client):
        """Health endpoint should return status and mock_mode."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "status" in data
        assert "mock_mode" in data
        assert data["mock_mode"] is True


class TestStatusEndpoint:
    """Test status endpoint."""
    
    def test_status_returns_200(self, client):
        """GET /api/v1/status should return 200."""
        response = client.get("/api/v1/status")
        assert response.status_code == 200
    
    def test_status_contains_state(self, client):
        """Status should contain current state."""
        response = client.get("/api/v1/status")
        data = response.json()
        assert "state" in data
        assert "armed" in data


class TestDemoEndpoints:
    """Test demo/testing endpoints."""
    
    def test_pose_trigger_returns_quickly(self, client):
        """POST /api/v1/demo/pose-trigger should return immediately."""
        response = client.post("/api/v1/demo/pose-trigger", json={})
        assert response.status_code == 200
        data = response.json()
        assert "event_id" in data or "state" in data
    
    def test_simulate_reply_without_event(self, client):
        """simulate-reply should fail gracefully when no event is listening."""
        response = client.post("/api/v1/demo/simulate-reply", json={
            "event_id": "nonexistent",
            "transcript": "okey",
            "lang": "ms"
        })
        assert response.status_code == 400


class TestEgressEndpoint:
    """Test egress logging endpoint."""
    
    def test_egress_returns_200(self, client):
        """GET /api/v1/egress should return 200."""
        response = client.get("/api/v1/egress")
        assert response.status_code == 200
    
    def test_egress_contains_video_bytes(self, client):
        """Egress should confirm video_bytes_out is 0."""
        response = client.get("/api/v1/egress")
        data = response.json()
        assert data.get("video_bytes_out", 0) == 0
        assert data.get("image_bytes_out", 0) == 0


class TestEventsEndpoint:
    """Test events listing endpoint."""
    
    def test_events_returns_200(self, client):
        """GET /api/v1/events should return 200."""
        response = client.get("/api/v1/events")
        assert response.status_code == 200
    
    def test_events_returns_list(self, client):
        """Events endpoint should return a list."""
        response = client.get("/api/v1/events")
        data = response.json()
        assert isinstance(data, list)
