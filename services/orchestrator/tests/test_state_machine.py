"""
Tests for GuardianBox Event State Machine (PRD §12)

Tests state transitions and validates the state diagram.
"""

import pytest
from app.state_machine import (
    EventState,
    EventStateMachine,
    TransitionReason,
    can_transition,
    VALID_TRANSITIONS,
)


class TestStateTransitionValidity:
    """Test that only valid state transitions are allowed"""
    
    def test_monitoring_to_suspected(self):
        assert can_transition(EventState.MONITORING, EventState.SUSPECTED)
    
    def test_monitoring_to_camera_lost(self):
        assert can_transition(EventState.MONITORING, EventState.CAMERA_LOST)
    
    def test_monitoring_to_paused(self):
        assert can_transition(EventState.MONITORING, EventState.PAUSED)
    
    def test_monitoring_to_alerting_invalid(self):
        """Cannot go directly from MONITORING to ALERTING"""
        assert not can_transition(EventState.MONITORING, EventState.ALERTING)
    
    def test_suspected_to_confirming(self):
        assert can_transition(EventState.SUSPECTED, EventState.CONFIRMING)
    
    def test_confirming_to_stood_down(self):
        assert can_transition(EventState.CONFIRMING, EventState.STOOD_DOWN)
    
    def test_confirming_to_checking_in(self):
        assert can_transition(EventState.CONFIRMING, EventState.CHECKING_IN)
    
    def test_checking_in_to_listening(self):
        assert can_transition(EventState.CHECKING_IN, EventState.LISTENING)
    
    def test_listening_to_deciding(self):
        assert can_transition(EventState.LISTENING, EventState.DECIDING)
    
    def test_deciding_to_alerting(self):
        assert can_transition(EventState.DECIDING, EventState.ALERTING)
    
    def test_alerting_to_awaiting_ack(self):
        assert can_transition(EventState.ALERTING, EventState.AWAITING_ACK)
    
    def test_alerting_to_alert_queued(self):
        """When internet is down"""
        assert can_transition(EventState.ALERTING, EventState.ALERT_QUEUED)
    
    def test_awaiting_ack_to_resolved(self):
        assert can_transition(EventState.AWAITING_ACK, EventState.RESOLVED)
    
    def test_awaiting_ack_to_escalating(self):
        assert can_transition(EventState.AWAITING_ACK, EventState.ESCALATING)


class TestStateMachine:
    """Test EventStateMachine class"""
    
    def test_initial_state(self):
        sm = EventStateMachine("test-1")
        assert sm.state == EventState.MONITORING
    
    def test_valid_transition_records_history(self):
        sm = EventStateMachine("test-2")
        transition = sm.transition(
            EventState.SUSPECTED,
            TransitionReason.POSE_TRIGGER,
            {"torso_angle": 75}
        )
        
        assert sm.state == EventState.SUSPECTED
        assert len(sm.transitions) == 1
        assert transition.from_state == EventState.MONITORING
        assert transition.to_state == EventState.SUSPECTED
        assert transition.reason == TransitionReason.POSE_TRIGGER
        assert transition.details["torso_angle"] == 75
    
    def test_invalid_transition_raises_error(self):
        sm = EventStateMachine("test-3")
        
        with pytest.raises(ValueError) as exc_info:
            sm.transition(EventState.ALERTING, TransitionReason.POSE_TRIGGER)
        
        assert "Invalid transition" in str(exc_info.value)
        assert sm.state == EventState.MONITORING  # State unchanged
    
    def test_full_fall_to_alert_flow(self):
        """Test a complete fall detection → alert flow"""
        sm = EventStateMachine("test-4")
        
        # Pose trigger
        sm.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER)
        assert sm.state == EventState.SUSPECTED
        
        # VLM confirmation
        sm.transition(EventState.CONFIRMING, TransitionReason.VLM_RESULT)
        assert sm.state == EventState.CONFIRMING
        
        # Needs check-in
        sm.transition(EventState.CHECKING_IN, TransitionReason.DECISION_TABLE)
        assert sm.state == EventState.CHECKING_IN
        
        # Listening
        sm.transition(EventState.LISTENING, TransitionReason.CHECKIN_ATTEMPT)
        assert sm.state == EventState.LISTENING
        
        # No response, deciding
        sm.transition(EventState.DECIDING, TransitionReason.NO_RESPONSE)
        assert sm.state == EventState.DECIDING
        
        # Send alert
        sm.transition(EventState.ALERTING, TransitionReason.DECISION_TABLE)
        assert sm.state == EventState.ALERTING
        
        # Alert sent
        sm.transition(EventState.AWAITING_ACK, TransitionReason.ALERT_SENT)
        assert sm.state == EventState.AWAITING_ACK
        
        # Family acknowledges
        sm.transition(EventState.RESOLVED, TransitionReason.ACK_RECEIVED)
        assert sm.state == EventState.RESOLVED
        
        assert len(sm.transitions) == 8
    
    def test_prayer_flow_stand_down(self):
        """Test prayer recognition → stand down flow"""
        sm = EventStateMachine("test-5")
        
        sm.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER)
        sm.transition(EventState.CONFIRMING, TransitionReason.VLM_RESULT)
        sm.transition(EventState.STOOD_DOWN, TransitionReason.DECISION_TABLE, {
            "rule": "D1",
            "class": "prayer_sujud"
        })
        
        assert sm.state == EventState.STOOD_DOWN
        assert sm.transitions[-1].details["rule"] == "D1"
    
    def test_watchdog_from_stood_down(self):
        """Watchdog can re-trigger check-in from stood down"""
        sm = EventStateMachine("test-6")
        
        sm.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER)
        sm.transition(EventState.CONFIRMING, TransitionReason.VLM_RESULT)
        sm.transition(EventState.STOOD_DOWN, TransitionReason.DECISION_TABLE)
        
        # Watchdog triggers after posture persists
        sm.transition(EventState.CHECKING_IN, TransitionReason.WATCHDOG_TIMER)
        
        assert sm.state == EventState.CHECKING_IN
    
    def test_internet_down_flow(self):
        """Test alert queuing when internet is down"""
        sm = EventStateMachine("test-7")
        
        sm.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER)
        sm.transition(EventState.CONFIRMING, TransitionReason.VLM_RESULT)
        sm.transition(EventState.CHECKING_IN, TransitionReason.DECISION_TABLE)
        sm.transition(EventState.LISTENING, TransitionReason.CHECKIN_ATTEMPT)
        sm.transition(EventState.DECIDING, TransitionReason.NO_RESPONSE)
        sm.transition(EventState.ALERTING, TransitionReason.DECISION_TABLE)
        
        # Internet down
        sm.transition(EventState.ALERT_QUEUED, TransitionReason.ALERT_FAILED)
        assert sm.state == EventState.ALERT_QUEUED
        
        # Internet restored
        sm.transition(EventState.AWAITING_ACK, TransitionReason.ALERT_SENT)
        assert sm.state == EventState.AWAITING_ACK
    
    def test_to_dict_serialization(self):
        """Test state machine serialization"""
        sm = EventStateMachine("test-8")
        sm.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER)
        
        data = sm.to_dict()
        
        assert data["event_id"] == "test-8"
        assert data["state"] == "SUSPECTED"
        assert len(data["transitions"]) == 1
        assert data["transitions"][0]["from"] == "MONITORING"
        assert data["transitions"][0]["to"] == "SUSPECTED"


class TestAllValidTransitions:
    """Verify all transitions in VALID_TRANSITIONS are correctly defined"""
    
    def test_all_states_have_transitions(self):
        """Every state except terminal states should have outgoing transitions"""
        terminal_states = {EventState.RESOLVED}
        
        for state in EventState:
            if state not in terminal_states:
                assert state in VALID_TRANSITIONS, f"{state} missing from VALID_TRANSITIONS"
    
    def test_no_self_transitions(self):
        """No state should transition to itself"""
        for state, targets in VALID_TRANSITIONS.items():
            assert state not in targets, f"{state} has self-transition"
