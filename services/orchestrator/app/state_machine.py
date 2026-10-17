"""
GuardianBox Event State Machine (PRD §12)

States and transitions for fall detection events.
Models never change state directly; they return data that the orchestrator evaluates.
"""

from enum import Enum
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class EventState(str, Enum):
    """Event states from PRD §12 state diagram."""
    MONITORING = "MONITORING"
    SUSPECTED = "SUSPECTED"
    CONFIRMING = "CONFIRMING"
    STOOD_DOWN = "STOOD_DOWN"
    CHECKING_IN = "CHECKING_IN"
    LISTENING = "LISTENING"
    DECIDING = "DECIDING"
    RECHECK = "RECHECK"
    ALERTING = "ALERTING"
    ALERT_QUEUED = "ALERT_QUEUED"
    AWAITING_ACK = "AWAITING_ACK"
    ESCALATING = "ESCALATING"
    RESOLVED = "RESOLVED"
    CAMERA_LOST = "CAMERA_LOST"
    PAUSED = "PAUSED"


class TriggerType(str, Enum):
    """Types of pose triggers."""
    FAST_FALL = "fast_fall"           # Rapid descent + low posture + stillness
    SLOW_LOW_POSTURE = "slow_low_posture"  # Low posture for extended period
    WATCHDOG = "watchdog"             # Re-check after stand-down
    MANUAL_TEST = "manual_test"       # For testing/demo


class TransitionReason(str, Enum):
    """Reasons for state transitions (logged for debugging)."""
    POSE_TRIGGER = "pose_trigger"
    VLM_RESULT = "vlm_result"
    VLM_ERROR = "vlm_error"
    VLM_TIMEOUT = "vlm_timeout"
    CHECKIN_ATTEMPT = "checkin_attempt"
    REPLY_CLASSIFIED = "reply_classified"
    NO_RESPONSE = "no_response"
    UNCLEAR_RESPONSE = "unclear_response"
    DECISION_TABLE = "decision_table"
    WATCHDOG_TIMER = "watchdog_timer"
    ALERT_SENT = "alert_sent"
    ALERT_FAILED = "alert_failed"
    ACK_RECEIVED = "ack_received"
    ESCALATION_TIMER = "escalation_timer"
    CAMERA_FRAMES_LOST = "camera_frames_lost"
    CAMERA_FRAMES_RESUMED = "camera_frames_resumed"
    USER_PAUSE = "user_pause"
    USER_RESUME = "user_resume"
    PAUSE_TIMER = "pause_timer"


class StateTransition(BaseModel):
    """Record of a state transition."""
    event_id: str
    from_state: EventState
    to_state: EventState
    reason: TransitionReason
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[dict] = None


VALID_TRANSITIONS: dict[EventState, set[EventState]] = {
    EventState.MONITORING: {
        EventState.SUSPECTED,      # pose trigger
        EventState.CAMERA_LOST,    # no frames 10s
        EventState.PAUSED,         # user pauses
    },
    EventState.CAMERA_LOST: {
        EventState.MONITORING,     # frames resume
    },
    EventState.PAUSED: {
        EventState.MONITORING,     # timer ends / resume
    },
    EventState.SUSPECTED: {
        EventState.CONFIRMING,     # frames sent to VLM
    },
    EventState.CONFIRMING: {
        EventState.STOOD_DOWN,     # D1 (prayer) / D2 (non-fall)
        EventState.CHECKING_IN,    # D3 (soft check) / D4 (check-in) / VLM error
    },
    EventState.STOOD_DOWN: {
        EventState.CHECKING_IN,    # watchdog (posture persists)
        EventState.MONITORING,     # posture normal
    },
    EventState.CHECKING_IN: {
        EventState.LISTENING,      # prompt played
    },
    EventState.LISTENING: {
        EventState.CHECKING_IN,    # unclear/silence, attempts < 3
        EventState.DECIDING,       # reply classified or attempts done
    },
    EventState.DECIDING: {
        EventState.STOOD_DOWN,     # D5 (ok_no_help)
        EventState.RECHECK,        # D6 (ok but still down)
        EventState.ALERTING,       # D7/D8/D9
    },
    EventState.RECHECK: {
        EventState.CHECKING_IN,    # ask if can get up
    },
    EventState.ALERTING: {
        EventState.AWAITING_ACK,   # sent successfully
        EventState.ALERT_QUEUED,   # no internet (D10)
    },
    EventState.ALERT_QUEUED: {
        EventState.AWAITING_ACK,   # delivered later
    },
    EventState.AWAITING_ACK: {
        EventState.ESCALATING,     # 3 min no ack (D11)
        EventState.RESOLVED,       # family acknowledges
    },
    EventState.ESCALATING: {
        EventState.AWAITING_ACK,   # after sending to next contact
    },
    EventState.RESOLVED: {
        EventState.MONITORING,     # back to normal
    },
}


def can_transition(from_state: EventState, to_state: EventState) -> bool:
    """Check if a state transition is valid per the state machine."""
    if from_state not in VALID_TRANSITIONS:
        return False
    return to_state in VALID_TRANSITIONS[from_state]


class EventStateMachine:
    """
    Manages state transitions for a single event.
    Enforces the state machine from PRD §12.
    """
    
    def __init__(self, event_id: str, initial_state: EventState = EventState.MONITORING):
        self.event_id = event_id
        self._state = initial_state
        self.transitions: list[StateTransition] = []
        self.created_at = datetime.now(timezone.utc)
    
    @property
    def state(self) -> EventState:
        return self._state
    
    def transition(
        self,
        to_state: EventState,
        reason: TransitionReason,
        details: Optional[dict] = None
    ) -> StateTransition:
        """
        Attempt a state transition.
        Raises ValueError if the transition is invalid.
        """
        if not can_transition(self._state, to_state):
            raise ValueError(
                f"Invalid transition: {self._state} -> {to_state}. "
                f"Valid targets: {VALID_TRANSITIONS.get(self._state, set())}"
            )
        
        transition = StateTransition(
            event_id=self.event_id,
            from_state=self._state,
            to_state=to_state,
            reason=reason,
            details=details
        )
        
        self.transitions.append(transition)
        self._state = to_state
        
        return transition
    
    def force_state(self, state: EventState, reason: str = "forced"):
        """Force a state without validation (for recovery/testing only)."""
        self._state = state
    
    def to_dict(self) -> dict:
        """Serialize for logging/API."""
        return {
            "event_id": self.event_id,
            "state": self._state.value,
            "created_at": self.created_at.isoformat(),
            "transitions": [
                {
                    "from": t.from_state.value,
                    "to": t.to_state.value,
                    "reason": t.reason.value,
                    "at": t.timestamp.isoformat(),
                    "details": t.details
                }
                for t in self.transitions
            ]
        }
