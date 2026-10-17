"""
Tests for the GuardianBox Decision Table (PRD §11)

Covers every row D1-D12, including precedence rules and
the "agent cannot override alert" constraint.
"""

import pytest
from app.decision_table import (
    apply_decision_table,
    agent_can_override,
    SceneClass,
    ReplyIntent,
    DecisionOutcome,
    AlertLevel,
)


class TestDecisionTableD1Prayer:
    """D1: Prayer recognized → stand down silently"""
    
    def test_sujud_high_confidence_stands_down(self):
        result = apply_decision_table(
            scene_class=SceneClass.PRAYER_SUJUD,
            confidence=0.85,
            reply_intent=None,
        )
        assert result.rule == "D1"
        assert result.outcome == DecisionOutcome.STAND_DOWN_SILENT
        assert result.spoken_phrase_id is None
        assert result.watchdog_minutes == 3
    
    def test_prayer_other_position_stands_down(self):
        result = apply_decision_table(
            scene_class=SceneClass.PRAYER_OTHER,
            confidence=0.75,
            reply_intent=None,
        )
        assert result.rule == "D1"
        assert result.outcome == DecisionOutcome.STAND_DOWN_SILENT
    
    def test_prayer_low_confidence_triggers_checkin(self):
        result = apply_decision_table(
            scene_class=SceneClass.PRAYER_SUJUD,
            confidence=0.55,  # Below 0.7 threshold
            reply_intent=None,
        )
        assert result.rule == "D4"
        assert result.outcome == DecisionOutcome.CHECK_IN


class TestDecisionTableD2NonFall:
    """D2: Non-fall activities → stand down"""
    
    @pytest.mark.parametrize("scene_class", [
        SceneClass.LYING_FURNITURE,
        SceneClass.SITTING_FLOOR,
        SceneClass.KNEELING_PICKUP,
        SceneClass.GARDENING,
        SceneClass.EXERCISING,
    ])
    def test_non_fall_activities_stand_down(self, scene_class):
        result = apply_decision_table(
            scene_class=scene_class,
            confidence=0.8,
            reply_intent=None,
            rapid_descent=False,
        )
        assert result.rule == "D2"
        assert result.outcome == DecisionOutcome.STAND_DOWN
    
    def test_sitting_floor_has_watchdog(self):
        result = apply_decision_table(
            scene_class=SceneClass.SITTING_FLOOR,
            confidence=0.8,
            reply_intent=None,
        )
        assert result.watchdog_minutes == 10


class TestDecisionTableD3SoftCheckIn:
    """D3: Non-fall class but rapid descent → soft check-in"""
    
    def test_rapid_descent_triggers_soft_checkin(self):
        result = apply_decision_table(
            scene_class=SceneClass.SITTING_FLOOR,
            confidence=0.8,
            reply_intent=None,
            rapid_descent=True,  # Key difference from D2
        )
        assert result.rule == "D3"
        assert result.outcome == DecisionOutcome.SOFT_CHECK_IN
        assert result.spoken_phrase_id == "soft"


class TestDecisionTableD4CheckIn:
    """D4: Fall, unclear, or low confidence → check-in"""
    
    def test_fall_triggers_checkin(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.85,
            reply_intent=None,
        )
        assert result.rule == "D4"
        assert result.outcome == DecisionOutcome.CHECK_IN
        assert result.spoken_phrase_id == "checkin_1"
    
    def test_unclear_cause_triggers_checkin(self):
        result = apply_decision_table(
            scene_class=SceneClass.LYING_ON_FLOOR_UNCLEAR,
            confidence=0.7,
            reply_intent=None,
        )
        assert result.rule == "D4"
        assert result.outcome == DecisionOutcome.CHECK_IN
    
    def test_low_confidence_triggers_checkin(self):
        result = apply_decision_table(
            scene_class=SceneClass.SITTING_FLOOR,
            confidence=0.5,  # Below 0.6 threshold
            reply_intent=None,
        )
        assert result.rule == "D4"
        assert result.outcome == DecisionOutcome.CHECK_IN
    
    def test_vlm_error_triggers_checkin(self):
        result = apply_decision_table(
            scene_class=None,
            confidence=0,
            reply_intent=None,
            vlm_error=True,
        )
        assert result.rule == "D4"
        assert result.outcome == DecisionOutcome.CHECK_IN


class TestDecisionTableD5OkNoHelp:
    """D5: ok_no_help reply → stand down"""
    
    def test_ok_stands_down(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=ReplyIntent.OK_NO_HELP,
        )
        assert result.rule == "D5"
        assert result.outcome == DecisionOutcome.STAND_DOWN
        assert result.spoken_phrase_id == "ack_ok"
        assert result.watchdog_minutes == 2


class TestDecisionTableD6Recheck:
    """D6: OK but still on floor → recheck"""
    
    def test_ok_still_on_floor_triggers_recheck(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=ReplyIntent.OK_NO_HELP,
            still_on_floor=True,
            floor_time_minutes=3,  # >= 2 minutes
        )
        assert result.rule == "D6"
        assert result.outcome == DecisionOutcome.RECHECK
        assert result.spoken_phrase_id == "recheck"


class TestDecisionTableD7Help:
    """D7: help/pain/cannot → alert immediately"""
    
    @pytest.mark.parametrize("intent", [
        ReplyIntent.NEEDS_HELP,
        ReplyIntent.PAIN_OR_INJURY,
        ReplyIntent.CANNOT_GET_UP,
        ReplyIntent.CALL_FAMILY,
    ])
    def test_distress_intents_alert_immediately(self, intent):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=intent,
        )
        assert result.rule == "D7"
        assert result.outcome == DecisionOutcome.ALERT_URGENT
        assert result.alert_level == AlertLevel.URGENT
        assert result.spoken_phrase_id == "alert_sent"


class TestDecisionTableD8NoResponse:
    """D8: No response after all attempts → alert"""
    
    def test_silence_after_three_attempts_alerts(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=ReplyIntent.NO_RESPONSE,
            check_in_attempts=3,
        )
        assert result.rule == "D8"
        assert result.outcome == DecisionOutcome.ALERT_NO_RESPONSE
        assert result.alert_level == AlertLevel.URGENT


class TestDecisionTableD9Unclear:
    """D9: Unclear replies on all attempts → alert"""
    
    def test_unclear_after_three_attempts_alerts(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=ReplyIntent.UNCLEAR,
            check_in_attempts=3,
        )
        assert result.rule == "D9"
        assert result.outcome == DecisionOutcome.ALERT_NEEDS_CHECK
        assert result.alert_level == AlertLevel.NEEDS_CHECK


class TestDecisionTableD12Paused:
    """D12: Monitoring paused → no detection"""
    
    def test_paused_stands_down_silently(self):
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.9,
            reply_intent=None,
            is_paused=True,
        )
        assert result.rule == "D12"
        assert result.outcome == DecisionOutcome.STAND_DOWN_SILENT


class TestDecisionPrecedence:
    """Test rule precedence: D7 > D8/D9 > D6 > D5"""
    
    def test_help_wins_over_ok(self):
        # If somehow both are true, help should win
        result = apply_decision_table(
            scene_class=SceneClass.FALL,
            confidence=0.8,
            reply_intent=ReplyIntent.NEEDS_HELP,  # D7
        )
        assert result.rule == "D7"
        assert result.outcome == DecisionOutcome.ALERT_URGENT


class TestAgentOverride:
    """Test that agent cannot override alert into stand down"""
    
    def test_agent_cannot_downgrade_alert_to_stand_down(self):
        # Alert outcomes
        assert not agent_can_override(
            DecisionOutcome.STAND_DOWN,
            DecisionOutcome.ALERT_URGENT
        )
        assert not agent_can_override(
            DecisionOutcome.STAND_DOWN_SILENT,
            DecisionOutcome.ALERT_NO_RESPONSE
        )
        assert not agent_can_override(
            DecisionOutcome.STAND_DOWN,
            DecisionOutcome.ALERT_NEEDS_CHECK
        )
    
    def test_agent_can_add_actions(self):
        # Non-alert to non-alert is fine
        assert agent_can_override(
            DecisionOutcome.CHECK_IN,
            DecisionOutcome.SOFT_CHECK_IN
        )
        # Alert to alert is fine
        assert agent_can_override(
            DecisionOutcome.ALERT_URGENT,
            DecisionOutcome.ALERT_NO_RESPONSE
        )
