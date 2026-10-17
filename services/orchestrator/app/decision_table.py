"""
GuardianBox Decision Table (PRD §11)

This is the AUTHORITATIVE policy. The agent can ADD actions but CANNOT override
an ALERT outcome into STAND_DOWN. If the agent's proposal conflicts, the
orchestrator uses this table and logs the conflict.

Rules of precedence: D7 > D8/D9 > D6 > D5
A keyword hit for help, pain, or can't-get-up always wins.
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass


class SceneClass(str, Enum):
    """VLM scene classifications from vlm-assessment.v1 schema."""
    FALL = "fall"
    LYING_ON_FLOOR_UNCLEAR = "lying_on_floor_unclear_cause"
    PRAYER_SUJUD = "prayer_sujud"
    PRAYER_OTHER = "prayer_other_position"
    KNEELING_PICKUP = "kneeling_to_pick_up_or_reach"
    SITTING_FLOOR = "sitting_on_floor_resting"
    GARDENING = "gardening_or_floor_work"
    LYING_FURNITURE = "lying_on_sofa_or_bed"
    EXERCISING = "exercising_or_stretching"
    NO_PERSON = "no_person_or_unclear"


class ReplyIntent(str, Enum):
    """Classified reply intents from the check-in."""
    OK_NO_HELP = "ok_no_help"        # "Saya okey", "I'm fine", "没事"
    NEEDS_HELP = "needs_help"        # "tolong", "help", "帮忙"
    PAIN_OR_INJURY = "pain_or_injury"  # "sakit", "pain", "痛"
    CANNOT_GET_UP = "cannot_get_up"  # "tak boleh bangun", "can't get up", "起不来"
    CALL_FAMILY = "call_family"      # "panggil anak saya", "call my son"
    UNCLEAR = "unclear"              # anything else, mumbling
    NO_RESPONSE = "no_response"      # silence after listen window


class DecisionOutcome(str, Enum):
    """Possible decision outcomes."""
    STAND_DOWN_SILENT = "stand_down_silent"  # D1: prayer, no speech
    STAND_DOWN = "stand_down"                # D2, D5: normal activity or "I'm fine"
    SOFT_CHECK_IN = "soft_check_in"          # D3: gentle single prompt
    CHECK_IN = "check_in"                    # D4: full 3-attempt check-in
    RECHECK = "recheck"                      # D6: "can you get up?"
    ALERT_URGENT = "alert_urgent"            # D7: help/pain/cannot
    ALERT_NO_RESPONSE = "alert_no_response"  # D8: silence after all attempts
    ALERT_NEEDS_CHECK = "alert_needs_check"  # D9: unclear replies


class AlertLevel(str, Enum):
    """Alert severity levels for Telegram messages."""
    URGENT = "urgent"           # D7, D8
    NEEDS_HELP = "needs_help"   # D6 escalation
    NEEDS_CHECK = "needs_check" # D9
    SYSTEM = "system"           # Camera offline, etc.


@dataclass
class DecisionResult:
    """Result from applying the decision table."""
    rule: str                          # D1-D12
    outcome: DecisionOutcome
    alert_level: Optional[AlertLevel] = None
    spoken_phrase_id: Optional[str] = None
    watchdog_minutes: Optional[int] = None
    reason: str = ""


PRAYER_CLASSES = {SceneClass.PRAYER_SUJUD, SceneClass.PRAYER_OTHER}

NON_FALL_CLASSES = {
    SceneClass.LYING_FURNITURE,
    SceneClass.SITTING_FLOOR,
    SceneClass.KNEELING_PICKUP,
    SceneClass.GARDENING,
    SceneClass.EXERCISING,
}

ESCALATING_INTENTS = {
    ReplyIntent.NEEDS_HELP,
    ReplyIntent.PAIN_OR_INJURY,
    ReplyIntent.CANNOT_GET_UP,
    ReplyIntent.CALL_FAMILY,
}


def apply_decision_table(
    scene_class: Optional[SceneClass],
    confidence: float,
    reply_intent: Optional[ReplyIntent],
    rapid_descent: bool = False,
    still_on_floor: bool = False,
    floor_time_minutes: float = 0,
    check_in_attempts: int = 0,
    is_paused: bool = False,
    vlm_error: bool = False,
) -> DecisionResult:
    """
    Apply the decision table from PRD §11.
    
    This function is deterministic and is the authoritative policy.
    The agent can add actions but cannot override ALERT outcomes.
    
    Args:
        scene_class: VLM classification result
        confidence: VLM confidence (0-1)
        reply_intent: Classified reply from check-in (if done)
        rapid_descent: Whether FR-POSE-3a (rapid descent) occurred
        still_on_floor: Whether person is still in low posture
        floor_time_minutes: How long they've been on the floor
        check_in_attempts: Number of check-in attempts made
        is_paused: Whether monitoring is paused by resident
        vlm_error: Whether VLM failed/timed out
    
    Returns:
        DecisionResult with the rule applied and outcome
    """
    
    # D12: Paused - no detection at all
    if is_paused:
        return DecisionResult(
            rule="D12",
            outcome=DecisionOutcome.STAND_DOWN_SILENT,
            reason="Monitoring paused by resident"
        )
    
    # If we have a reply intent, prioritize that (D5-D9)
    if reply_intent is not None:
        # D7: Needs help, pain, injury, cannot get up, or call family
        # Keyword floor always wins - this is the highest priority
        if reply_intent in ESCALATING_INTENTS:
            return DecisionResult(
                rule="D7",
                outcome=DecisionOutcome.ALERT_URGENT,
                alert_level=AlertLevel.URGENT,
                spoken_phrase_id="alert_sent",
                reason=f"Reply indicates distress: {reply_intent.value}"
            )
        
        # D8: No response after all attempts
        if reply_intent == ReplyIntent.NO_RESPONSE and check_in_attempts >= 3:
            return DecisionResult(
                rule="D8",
                outcome=DecisionOutcome.ALERT_NO_RESPONSE,
                alert_level=AlertLevel.URGENT,
                spoken_phrase_id="alert_sent",
                reason="No response after 3 check-in attempts"
            )
        
        # D9: Unclear on all attempts
        if reply_intent == ReplyIntent.UNCLEAR and check_in_attempts >= 3:
            return DecisionResult(
                rule="D9",
                outcome=DecisionOutcome.ALERT_NEEDS_CHECK,
                alert_level=AlertLevel.NEEDS_CHECK,
                spoken_phrase_id="alert_sent",
                reason="Unclear reply after 3 check-in attempts"
            )
        
        # D6: Said OK but still on floor after 2 minutes
        if reply_intent == ReplyIntent.OK_NO_HELP and still_on_floor and floor_time_minutes >= 2:
            return DecisionResult(
                rule="D6",
                outcome=DecisionOutcome.RECHECK,
                spoken_phrase_id="recheck",
                reason="Said OK but still on floor - asking if can get up"
            )
        
        # D5: OK and moving/getting up (or just said OK)
        if reply_intent == ReplyIntent.OK_NO_HELP:
            return DecisionResult(
                rule="D5",
                outcome=DecisionOutcome.STAND_DOWN,
                spoken_phrase_id="ack_ok",
                watchdog_minutes=2,  # Re-check if still on floor after 2 min
                reason="Resident said they're okay"
            )
        
        # Still unclear but not all attempts done - continue check-in
        if reply_intent in {ReplyIntent.UNCLEAR, ReplyIntent.NO_RESPONSE}:
            return DecisionResult(
                rule="D4",
                outcome=DecisionOutcome.CHECK_IN,
                spoken_phrase_id=f"checkin_{check_in_attempts + 1}",
                reason=f"Reply unclear, attempt {check_in_attempts + 1}/3"
            )
    
    # VLM-based decisions (before check-in)
    if vlm_error:
        # VLM failed - fail-safe to check-in (never stand down on error)
        return DecisionResult(
            rule="D4",
            outcome=DecisionOutcome.CHECK_IN,
            spoken_phrase_id="checkin_1",
            reason="VLM error/timeout - fail-safe to check-in"
        )
    
    # D1: Prayer recognized with high confidence
    if scene_class in PRAYER_CLASSES and confidence >= 0.7:
        return DecisionResult(
            rule="D1",
            outcome=DecisionOutcome.STAND_DOWN_SILENT,
            watchdog_minutes=3,  # Re-check if posture persists > 3 min
            reason=f"Prayer recognized ({scene_class.value}, conf={confidence:.2f})"
        )
    
    # D2: Non-fall activity with high confidence, no rapid descent
    if scene_class in NON_FALL_CLASSES and confidence >= 0.7 and not rapid_descent:
        watchdog = 10 if scene_class == SceneClass.SITTING_FLOOR else None
        return DecisionResult(
            rule="D2",
            outcome=DecisionOutcome.STAND_DOWN,
            watchdog_minutes=watchdog,
            reason=f"Non-fall activity ({scene_class.value}, conf={confidence:.2f})"
        )
    
    # D3: Non-fall class but rapid descent preceded - soft check-in
    if scene_class in NON_FALL_CLASSES and confidence >= 0.7 and rapid_descent:
        return DecisionResult(
            rule="D3",
            outcome=DecisionOutcome.SOFT_CHECK_IN,
            spoken_phrase_id="soft",
            reason=f"Non-fall class but rapid descent - soft check"
        )
    
    # D4: Fall, unclear cause, low confidence, or no person
    # This is the catch-all that triggers check-in
    fall_classes = {SceneClass.FALL, SceneClass.LYING_ON_FLOOR_UNCLEAR, SceneClass.NO_PERSON}
    if scene_class in fall_classes or confidence < 0.6:
        return DecisionResult(
            rule="D4",
            outcome=DecisionOutcome.CHECK_IN,
            spoken_phrase_id="checkin_1",
            reason=f"Fall suspected or low confidence ({scene_class}, conf={confidence:.2f})"
        )
    
    # Default: check-in (err on the side of safety)
    return DecisionResult(
        rule="D4",
        outcome=DecisionOutcome.CHECK_IN,
        spoken_phrase_id="checkin_1",
        reason="Default: check-in for safety"
    )


def agent_can_override(proposed_outcome: DecisionOutcome, table_outcome: DecisionOutcome) -> bool:
    """
    Check if the agent's proposed outcome can override the table outcome.
    
    Per PRD §FR-AGT-2: The agent can ADD actions but cannot override
    an ALERT outcome into STAND_DOWN.
    """
    alert_outcomes = {
        DecisionOutcome.ALERT_URGENT,
        DecisionOutcome.ALERT_NO_RESPONSE,
        DecisionOutcome.ALERT_NEEDS_CHECK,
    }
    
    stand_down_outcomes = {
        DecisionOutcome.STAND_DOWN,
        DecisionOutcome.STAND_DOWN_SILENT,
    }
    
    # If table says alert and agent says stand down, agent loses
    if table_outcome in alert_outcomes and proposed_outcome in stand_down_outcomes:
        return False
    
    return True
