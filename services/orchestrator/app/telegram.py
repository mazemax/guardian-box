"""
Telegram integration helpers.

Provides message formatting, callback parsing, and inline keyboard building
for GuardianBox Telegram alerts.
"""

import os
import re
from datetime import datetime, timezone
from typing import Tuple, Optional, List


def format_alert_message(
    resident_name: str,
    room: str,
    event_type: str,
    timestamp: datetime,
    checkin_attempts: int = 3,
    timezone_name: str = "MYT",
) -> str:
    """
    Format an alert message for Telegram.
    
    Args:
        resident_name: Name of the resident
        room: Room where the event occurred
        event_type: Type of event (fall, collapse, unclear)
        timestamp: When the event was detected
        checkin_attempts: Number of voice check-in attempts
        timezone_name: Timezone abbreviation for display
    
    Returns:
        Formatted message text
    """
    time_str = timestamp.strftime("%H:%M:%S")
    
    event_descriptions = {
        "fall": "Person on the floor, not moving.",
        "collapse": "Person collapsed slowly, not moving.",
        "unclear": "Unusual posture detected, not responding.",
    }
    
    description = event_descriptions.get(event_type, event_descriptions["unclear"])
    
    return f"""{resident_name} · {room} · {time_str} ({timezone_name})

What GuardianBox saw: {description}
What it tried: Asked {checkin_attempts} times. No reply.

Please call now. If it's an emergency, call 999.

(No photos or video are ever sent.)"""


def get_alert_level(event_type: str) -> str:
    """
    Determine alert level based on event type.
    
    Args:
        event_type: Type of event
    
    Returns:
        'urgent' or 'warning'
    """
    urgent_types = {"fall", "collapse", "fast_fall", "slow_collapse"}
    
    if event_type.lower() in urgent_types:
        return "urgent"
    
    return "warning"


def parse_callback_data(callback_data: str) -> Tuple[str, str]:
    """
    Parse a Telegram callback button press.
    
    Expected format: "ack:{action}:{event_id}"
    
    Args:
        callback_data: Raw callback data string
    
    Returns:
        Tuple of (action, event_id)
    
    Raises:
        ValueError: If callback data format is invalid
    """
    if not callback_data or ":" not in callback_data:
        raise ValueError(f"Invalid callback data format: {callback_data}")
    
    parts = callback_data.split(":")
    
    if len(parts) != 3 or parts[0] != "ack":
        raise ValueError(f"Invalid callback data format: {callback_data}")
    
    action = parts[1]
    event_id = parts[2]
    
    valid_actions = {"calling", "on_my_way", "false_alarm", "call_999_info"}
    if action not in valid_actions:
        raise ValueError(f"Unknown action: {action}")
    
    return action, event_id


def build_inline_keyboard(event_id: str) -> list[list[dict]]:
    """
    Build the inline keyboard markup for alert response buttons.
    
    Args:
        event_id: The event ID to include in callbacks
    
    Returns:
        2D list of button dictionaries for Telegram inline keyboard
    """
    return [
        [
            {"text": "📞 Calling", "callback_data": f"ack:calling:{event_id}"},
            {"text": "🏃 On my way", "callback_data": f"ack:on_my_way:{event_id}"},
        ],
        [
            {"text": "✅ False alarm", "callback_data": f"ack:false_alarm:{event_id}"},
            {"text": "🆘 999 info", "callback_data": f"ack:call_999_info:{event_id}"},
        ],
    ]


def redact_token(text: str) -> str:
    """
    Redact Telegram bot tokens from log messages.
    
    Args:
        text: Text that may contain a bot token
    
    Returns:
        Text with tokens redacted
    """
    token_pattern = r"bot(\d+:[A-Za-z0-9_-]+)"
    return re.sub(token_pattern, "bot[REDACTED]", text)


def get_telegram_chat_ids_from_env() -> List[str]:
    """Parse TELEGRAM_CHAT_IDS from the environment."""
    chat_ids_str = os.getenv("TELEGRAM_CHAT_IDS", "")
    return [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]


def telegram_live_in_mock_enabled() -> bool:
    """When MOCK_MODE is on, still send real Telegram if true (default)."""
    return os.getenv("TELEGRAM_LIVE_IN_MOCK", "true").lower() == "true"


def telegram_credentials_configured() -> bool:
    """True when bot token and at least one chat ID are set."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    return bool(token) and bool(get_telegram_chat_ids_from_env())


def should_send_live_telegram(mock_mode: bool) -> bool:
    """Whether alerts/tests should hit the notifier (real Telegram)."""
    if not telegram_credentials_configured():
        return False
    if not mock_mode:
        return True
    return telegram_live_in_mock_enabled()


def build_test_message() -> str:
    """Build a test message for Telegram integration testing."""
    return (
        "🧪 Test message from GuardianBox\n\n"
        "If you receive this, Telegram alerts are working!"
    )


def build_test_message_markdown() -> str:
    """MarkdownV2 test message for Telegram sendMessage."""
    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🧪 *Test message from GuardianBox*\n\n"
        "If you receive this, Telegram alerts are working\\!\n\n"
        f"Time: {time_str} UTC"
    )


def build_resolved_telegram_text(action: Optional[str] = None) -> str:
    """Short text for editMessageText when an event is resolved."""
    if action == "false_alarm":
        return "✅ <b>Resolved</b> — marked as false alarm."
    if action in {"calling", "on_my_way"}:
        return "✅ <b>Resolved</b> — family is responding."
    return "✅ <b>Resolved</b> — event closed."


def build_start_response(chat_id: int) -> str:
    """Build the response for /start command."""
    return (
        f"👋 Hello! Your chat ID is: {chat_id}\n\n"
        "Add this to your GuardianBox .env file:\n"
        f"`TELEGRAM_CHAT_IDS={chat_id}`\n\n"
        "You will receive alerts here when GuardianBox detects a potential emergency."
    )
