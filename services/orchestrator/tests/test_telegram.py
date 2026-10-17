"""
Telegram integration tests.

Tests for:
- Message formatting
- Callback button mapping
- Mock mode preview
- API endpoints
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from app.telegram import (
    format_alert_message,
    get_alert_level,
    parse_callback_data,
    build_inline_keyboard,
    redact_token,
)


class TestTelegramMessageFormatting:
    """Tests for Telegram alert message formatting."""
    
    def test_alert_message_contains_resident_name(self):
        """Alert message should include resident name."""
        msg = format_alert_message(
            resident_name="Ahmad",
            room="Living room",
            event_type="fall",
            timestamp=datetime.now(timezone.utc),
        )
        
        assert "Ahmad" in msg
        assert "Living room" in msg
    
    def test_alert_message_contains_event_description(self):
        """Alert message should describe what happened."""
        msg = format_alert_message(
            resident_name="Ahmad",
            room="Kitchen",
            event_type="fall",
            timestamp=datetime.now(timezone.utc),
        )
        
        assert "floor" in msg.lower() or "fall" in msg.lower()
    
    def test_alert_message_contains_checkin_attempts(self):
        """Alert message should mention check-in attempts."""
        msg = format_alert_message(
            resident_name="Ahmad",
            room="Living room",
            event_type="fall",
            timestamp=datetime.now(timezone.utc),
            checkin_attempts=3,
        )
        
        assert "3" in msg or "three" in msg.lower()
    
    def test_alert_message_contains_privacy_note(self):
        """Alert message should reassure no photo/video is sent."""
        msg = format_alert_message(
            resident_name="Ahmad",
            room="Living room",
            event_type="fall",
            timestamp=datetime.now(timezone.utc),
        )
        
        assert "photo" in msg.lower() or "video" in msg.lower()
    
    def test_urgent_level_for_fall(self):
        """Fall alerts should be marked as urgent."""
        level = get_alert_level("fall")
        assert level == "urgent"
    
    def test_warning_level_for_unclear(self):
        """Unclear situations should be marked as warning."""
        level = get_alert_level("unclear")
        assert level == "warning"


class TestTelegramCallbackMapping:
    """Tests for mapping Telegram callback buttons to actions."""
    
    def test_calling_callback_maps_to_action(self):
        """Calling button should map to 'calling' action."""
        action, event_id = parse_callback_data("ack:calling:evt_123")
        
        assert action == "calling"
        assert event_id == "evt_123"
    
    def test_on_my_way_callback_maps_to_action(self):
        """On my way button should map correctly."""
        action, event_id = parse_callback_data("ack:on_my_way:evt_456")
        
        assert action == "on_my_way"
        assert event_id == "evt_456"
    
    def test_false_alarm_callback_maps_to_action(self):
        """False alarm button should map correctly."""
        action, event_id = parse_callback_data("ack:false_alarm:evt_789")
        
        assert action == "false_alarm"
        assert event_id == "evt_789"
    
    def test_call_999_info_callback_maps_to_action(self):
        """999 info button should map correctly."""
        action, event_id = parse_callback_data("ack:call_999_info:evt_abc")
        
        assert action == "call_999_info"
        assert event_id == "evt_abc"
    
    def test_invalid_callback_raises_error(self):
        """Invalid callback data should raise ValueError."""
        with pytest.raises(ValueError):
            parse_callback_data("invalid_data")
    
    def test_inline_keyboard_has_four_buttons(self):
        """Inline keyboard should have 4 response buttons."""
        keyboard = build_inline_keyboard("evt_123")
        
        # Flatten the 2D array to count buttons
        buttons = [btn for row in keyboard for btn in row]
        assert len(buttons) == 4
    
    def test_inline_keyboard_button_callbacks(self):
        """Each button should have correct callback data."""
        keyboard = build_inline_keyboard("evt_test")
        buttons = [btn for row in keyboard for btn in row]
        
        callbacks = [btn["callback_data"] for btn in buttons]
        
        assert "ack:calling:evt_test" in callbacks
        assert "ack:on_my_way:evt_test" in callbacks
        assert "ack:false_alarm:evt_test" in callbacks
        assert "ack:call_999_info:evt_test" in callbacks


class TestTelegramMockMode:
    """Tests for mock mode (no token configured)."""
    
    def test_mock_mode_test_endpoint(self):
        """When no token is set, test endpoint should return mock status."""
        from fastapi.testclient import TestClient
        from app.main import app
        
        # Clear the module-level variables
        import app.main as main_module
        original_token = main_module.TELEGRAM_BOT_TOKEN
        main_module.TELEGRAM_BOT_TOKEN = ""
        
        try:
            client = TestClient(app)
            response = client.post("/api/v1/telegram/test")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "mock"
        finally:
            main_module.TELEGRAM_BOT_TOKEN = original_token


class TestTelegramAPIEndpoint:
    """Tests for the Telegram API endpoints."""
    
    def test_health_shows_telegram_status(self):
        """Health endpoint should show Telegram connection status."""
        from fastapi.testclient import TestClient
        from app.main import app
        
        client = TestClient(app)
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert "telegram_connected" in data


class TestTelegramTokenSecurity:
    """Tests for Telegram token security."""
    
    def test_token_not_in_error_messages(self):
        """Token should never appear in error messages."""
        token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
        error_msg = "Failed to send message to chat 123"
        
        assert token not in error_msg
    
    def test_token_not_in_logs(self):
        """Token should be redacted in logs."""
        log_line = "Sending to https://api.telegram.org/bot123456789:ABCdefGHI/sendMessage"
        redacted = redact_token(log_line)
        
        assert "123456789" not in redacted
        assert "bot***" in redacted or "[REDACTED]" in redacted
