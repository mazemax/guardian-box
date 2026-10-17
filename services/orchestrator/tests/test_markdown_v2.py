"""
MarkdownV2 escaping for Telegram messages.
"""

from app.markdown_v2 import (
    escape_markdown_v2,
    validate_markdown_v2_literals,
    validate_markdown_v2_with_bold_spans,
)
from app.telegram import build_test_message_markdown, format_alert_markdown_v2
from app.event_manager import EventManager
from app.models import Contact, PoseSummary, Event, generate_id
from app.decision_table import DecisionOutcome, DecisionResult, AlertLevel


class TestMarkdownV2Escape:
    def test_escapes_timestamp_dashes_and_colons(self):
        raw = "2026-10-17 09:15:30"
        escaped = escape_markdown_v2(raw)
        assert "\\-" in escaped
        assert validate_markdown_v2_literals(escaped) == []

    def test_build_test_message_passes_bold_span_validator(self):
        message = build_test_message_markdown()
        assert validate_markdown_v2_with_bold_spans(message) == []
        time_part = message.split("Time: ", 1)[1].rsplit(" UTC", 1)[0]
        assert validate_markdown_v2_literals(time_part) == []
        assert "\\-" in time_part

    def test_sample_alert_text_escapes_to_valid_markdown_literals(self):
        em = EventManager(mock_mode=True)
        contact = Contact(name="Family", telegram_chat_id="1", language="en")
        event = Event(
            id=generate_id(),
            trigger="fast_fall",
            state="ALERTING",
            pose_summary=PoseSummary(zone="living room", stillness_seconds=5),
        )
        decision = DecisionResult(
            rule="D8",
            outcome=DecisionOutcome.ALERT_NO_RESPONSE,
            alert_level=AlertLevel.URGENT,
        )
        text = em._build_alert_text(event, decision, contact)
        escaped = format_alert_markdown_v2(text)
        assert validate_markdown_v2_literals(escaped) == []
