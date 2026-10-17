"""
Tests for real Telegram delivery while MOCK_MODE is enabled (Spark demos).
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("DEMO_PROFILE", "demo")
os.environ.setdefault("RUN_PIPELINE_SYNC", "true")
os.environ.setdefault(
    "DASHBOARD_DIR",
    str(Path(__file__).parent.parent.parent.parent / "dashboard"),
)

from app.telegram import should_send_live_telegram, telegram_live_in_mock_enabled
from app.event_manager import EventManager
from app.state_machine import TriggerType
from app.models import PoseSummary


class TestTelegramLiveInMockFlags:
    def test_should_send_when_mock_and_live_flag_default(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        monkeypatch.delenv("TELEGRAM_LIVE_IN_MOCK", raising=False)
        assert telegram_live_in_mock_enabled()
        assert should_send_live_telegram(mock_mode=True)

    def test_should_not_send_when_live_in_mock_disabled(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        monkeypatch.setenv("TELEGRAM_LIVE_IN_MOCK", "false")
        assert not should_send_live_telegram(mock_mode=True)

    def test_should_not_send_without_credentials(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_IDS", raising=False)
        assert not should_send_live_telegram(mock_mode=False)


@pytest.mark.asyncio
async def test_mock_mode_calls_notifier_when_telegram_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "999888")
    monkeypatch.setenv("TELEGRAM_LIVE_IN_MOCK", "true")

    em = EventManager(
        profile="demo",
        mock_mode=True,
        notifier_url="http://mock-notifier:8003",
    )
    assert len(em.contacts) == 1
    assert em.contacts[0].telegram_chat_id == "999888"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "sent", "message_id": 42}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    em._http_client = mock_client

    preview_events = []

    async def capture(event_type, data):
        preview_events.append((event_type, data))

    em.add_event_listener(capture)

    await em.handle_pose_trigger(
        TriggerType.FAST_FALL,
        PoseSummary(
            torso_angle=78,
            stillness_seconds=5,
            rapid_descent=True,
            zone="floor",
        ),
    )

    assert preview_events, "telegram_preview should be emitted in mock mode"
    assert any(t == "telegram_preview" for t, _ in preview_events)
    assert mock_client.post.called
    send_calls = [
        c for c in mock_client.post.call_args_list
        if c.args and str(c.args[0]).endswith("/send")
    ]
    assert send_calls, "notifier /send should be called when Telegram is configured"
    payload = send_calls[0].kwargs.get("json") or send_calls[0].args[1]
    assert payload["chat_id"] == "999888"


def test_telegram_test_uses_notifier_not_direct_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("TELEGRAM_LIVE_IN_MOCK", "true")

    from fastapi.testclient import TestClient
    from app.main import app

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [{"chat_id": "111", "ok": True}],
    }

    with patch("app.main.http_client") as mock_http:
        mock_http.post = AsyncMock(return_value=mock_response)
        client = TestClient(app)
        response = client.post("/api/v1/telegram/test")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert data["sent"] == 1
    assert "secret-token" not in response.text

    called_url = mock_http.post.call_args.args[0]
    assert called_url.endswith("/test")
    assert "api.telegram.org" not in called_url
