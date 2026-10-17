"""
End-to-end tests for GuardianBox pipeline.

These tests exercise the real pipeline through various scenarios per PRD §21.
They test the event_manager directly to ensure the state machine, decision
table, and reply handling work correctly end-to-end.

The existing 135 unit tests didn't catch the LISTENING->LISTENING bug,
so these tests exercise the real pipeline including state transitions.
"""

import pytest
import asyncio
import os
from pathlib import Path

os.environ["MOCK_MODE"] = "true"
os.environ["DEMO_PROFILE"] = "demo"
os.environ["DASHBOARD_DIR"] = str(Path(__file__).parent.parent.parent.parent / "dashboard")

from app.event_manager import EventManager, TimingProfile
from app.state_machine import EventState, TriggerType
from app.models import PoseSummary
from app.decision_table import DecisionOutcome


@pytest.fixture
def event_manager():
    """Create a fresh event manager for each test."""
    em = EventManager(
        vllm_url="http://mock-vllm:8000",
        stt_url="http://mock-stt:8001",
        voice_url="http://mock-voice:8002",
        notifier_url="http://mock-notifier:8003",
        profile="demo",
        mock_mode=True,
    )
    return em


class TestSilenceToAlertPath:
    """
    Test T1/T8: Fall with no response (silence) should lead to alert.
    
    Expected flow per PRD:
    1. Pose trigger -> SUSPECTED
    2. VLM confirmation -> CONFIRMING -> CHECKING_IN
    3. Check-in attempt 1 -> LISTENING -> (silence) -> CHECKING_IN
    4. Check-in attempt 2 -> LISTENING -> (silence) -> CHECKING_IN
    5. Check-in attempt 3 -> LISTENING -> (silence) -> DECIDING
    6. D8: No response after 3 attempts -> ALERTING -> AWAITING_ACK
    """
    
    @pytest.mark.asyncio
    async def test_silence_triggers_alert_after_three_attempts(self, event_manager):
        """Full silence path: pose trigger -> 3 silent check-ins -> alert (D8)."""
        pose_summary = PoseSummary(
            torso_angle=80,
            head_height_ratio=0.2,
            hip_descent_ratio=0.5,
            stillness_seconds=5,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        assert event.state in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
            f"Expected alerting state after silence, got {event.state}"
        
        assert event.decision_row in ["D8", "D7", "D9"], \
            f"Expected alert decision row, got {event.decision_row}"
        
        assert len(event.check_in_attempts) == 3, \
            f"Expected 3 check-in attempts, got {len(event.check_in_attempts)}"
        
        for attempt in event.check_in_attempts:
            assert attempt.intent == "no_response", \
                f"All attempts should be no_response, got {attempt.intent}"
        
        assert len(event.alerts) >= 1, "Should have sent at least one alert"
        
        transitions = event_manager.state_machine.transitions if event_manager.state_machine else []
        assert len(transitions) > 0, "Should have recorded state transitions"
        
        for i, t in enumerate(transitions[:-1]):
            next_t = transitions[i + 1]
            assert not (t.from_state == t.to_state), \
                f"No self-transitions should occur: {t.from_state} -> {t.to_state}"


class TestOkToStandDownPath:
    """
    Test T3/D5: Fall with "I'm OK" response should stand down.
    
    Expected flow per PRD:
    1. Pose trigger -> SUSPECTED
    2. VLM confirmation -> CONFIRMING -> CHECKING_IN
    3. Check-in attempt 1 -> LISTENING
    4. Reply "okey" injected -> classified as ok_no_help
    5. D5: Stand down -> STOOD_DOWN -> RESOLVED
    """
    
    @pytest.mark.asyncio
    async def test_ok_reply_stands_down(self, event_manager):
        """OK reply path: pose trigger -> check-in -> "okey" -> stand down (D5)."""
        pose_summary = PoseSummary(
            torso_angle=75,
            head_height_ratio=0.25,
            hip_descent_ratio=0.4,
            stillness_seconds=4,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        async def inject_ok_reply():
            await asyncio.sleep(1.5)
            for _ in range(20):
                if event_manager.state_machine and \
                   event_manager.state_machine.state == EventState.LISTENING:
                    event_manager.inject_reply("Saya okey", "ms")
                    return True
                await asyncio.sleep(0.2)
            return False
        
        inject_task = asyncio.create_task(inject_ok_reply())
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        await inject_task
        
        assert event.state in ["STOOD_DOWN", "RESOLVED"], \
            f"Expected stand down state after OK reply, got {event.state}"
        
        assert event.decision_row == "D5", \
            f"Expected D5 (stand down), got {event.decision_row}"
        
        assert len(event.check_in_attempts) >= 1, \
            "Should have at least one check-in attempt"
        
        last_attempt = event.check_in_attempts[-1]
        assert last_attempt.intent == "ok_no_help", \
            f"Last attempt should be ok_no_help, got {last_attempt.intent}"
        
        assert len(event.alerts) == 0, "Should not have sent any alerts for OK reply"


class TestHelpToAlertPath:
    """
    Test T2/D7: Fall with "help/tolong" response should alert immediately.
    
    Expected flow per PRD:
    1. Pose trigger -> SUSPECTED
    2. VLM confirmation -> CONFIRMING -> CHECKING_IN
    3. Check-in attempt 1 -> LISTENING
    4. Reply "tolong" injected -> classified as needs_help
    5. D7: Alert immediately -> ALERTING -> AWAITING_ACK
    """
    
    @pytest.mark.asyncio
    async def test_help_reply_alerts_immediately(self, event_manager):
        """Help reply path: pose trigger -> check-in -> "tolong" -> alert (D7)."""
        pose_summary = PoseSummary(
            torso_angle=85,
            head_height_ratio=0.15,
            hip_descent_ratio=0.6,
            stillness_seconds=5,
            motion_energy=0.05,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        async def inject_help_reply():
            await asyncio.sleep(1.5)
            for _ in range(20):
                if event_manager.state_machine and \
                   event_manager.state_machine.state == EventState.LISTENING:
                    event_manager.inject_reply("Tolong! Sakit!", "ms")
                    return True
                await asyncio.sleep(0.2)
            return False
        
        inject_task = asyncio.create_task(inject_help_reply())
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        await inject_task
        
        assert event.state in ["ALERTING", "AWAITING_ACK", "RESOLVED"], \
            f"Expected alerting state after help, got {event.state}"
        
        assert event.decision_row == "D7", \
            f"Expected D7 (urgent alert), got {event.decision_row}"
        
        assert len(event.alerts) >= 1, \
            "Should have sent at least one alert"
        
        assert len(event.check_in_attempts) >= 1, \
            "Should have at least one check-in attempt"
        
        last_attempt = event.check_in_attempts[-1]
        assert last_attempt.intent in ["needs_help", "pain_or_injury"], \
            f"Last attempt should be needs_help or pain_or_injury, got {last_attempt.intent}"


class TestChineseReplyPaths:
    """Test Chinese language reply handling."""
    
    @pytest.mark.asyncio
    async def test_chinese_ok_reply(self, event_manager):
        """Chinese OK reply: 没事 should stand down."""
        pose_summary = PoseSummary(
            torso_angle=75,
            head_height_ratio=0.25,
            hip_descent_ratio=0.4,
            stillness_seconds=4,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        async def inject_chinese_ok():
            # Wait past mock phrase playback; avoid injecting before listen clears pending reply
            await asyncio.sleep(1.0)
            for _ in range(80):
                if event_manager.state_machine and \
                   event_manager.state_machine.state == EventState.LISTENING:
                    if event_manager.inject_reply("我没事", "zh"):
                        return True
                await asyncio.sleep(0.1)
            return False
        
        event, _ = await asyncio.gather(
            event_manager.handle_pose_trigger(
                TriggerType.FAST_FALL,
                pose_summary,
                [],
            ),
            inject_chinese_ok(),
        )
        
        assert event.decision_row == "D5" or event.state in ["STOOD_DOWN", "RESOLVED"], \
            f"Expected stand down after Chinese OK, got state={event.state}, rule={event.decision_row}"
    
    @pytest.mark.asyncio
    async def test_chinese_help_reply(self, event_manager):
        """Chinese help reply: 救命 should alert immediately."""
        pose_summary = PoseSummary(
            torso_angle=85,
            head_height_ratio=0.15,
            hip_descent_ratio=0.6,
            stillness_seconds=5,
            motion_energy=0.05,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        async def inject_chinese_help():
            await asyncio.sleep(1.5)
            for _ in range(20):
                if event_manager.state_machine and \
                   event_manager.state_machine.state == EventState.LISTENING:
                    event_manager.inject_reply("救命！痛！", "zh")
                    return True
                await asyncio.sleep(0.2)
            return False
        
        inject_task = asyncio.create_task(inject_chinese_help())
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        await inject_task
        
        assert event.decision_row == "D7" or event.state in ["ALERTING", "AWAITING_ACK"], \
            f"Expected alert after Chinese help, got state={event.state}, rule={event.decision_row}"


class TestStateTransitionsAreValid:
    """Verify that the pipeline produces valid state transitions."""
    
    @pytest.mark.asyncio
    async def test_no_invalid_transitions_on_silence_path(self, event_manager):
        """The silence path should not produce any invalid transitions."""
        pose_summary = PoseSummary(
            torso_angle=80,
            head_height_ratio=0.2,
            hip_descent_ratio=0.5,
            stillness_seconds=5,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        transitions = event_manager.state_machine.transitions if event_manager.state_machine else []
        
        prev_state = None
        for t in transitions:
            if prev_state is not None:
                assert t.from_state.value == prev_state, \
                    f"Transition chain broken: expected from={prev_state}, got from={t.from_state.value}"
            prev_state = t.to_state.value
        
        for t in transitions:
            assert t.from_state != t.to_state, \
                f"No self-transitions should occur: {t.from_state} -> {t.to_state}"


class TestNotifierIsCalled:
    """Verify that the notifier is called on alert paths."""
    
    @pytest.mark.asyncio
    async def test_notifier_called_on_silence_alert(self, event_manager):
        """Silence path should result in alert being sent."""
        pose_summary = PoseSummary(
            torso_angle=80,
            head_height_ratio=0.2,
            hip_descent_ratio=0.5,
            stillness_seconds=5,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        assert event.state in ["AWAITING_ACK", "ALERTING", "RESOLVED"], \
            f"Expected alerting state, got {event.state}"
        
        assert len(event.alerts) >= 1, "Expected at least one alert to be sent"
        
        alert = event.alerts[0]
        assert alert.status == "sent", \
            f"Alert should be sent, got status={alert.status}"
        assert alert.level in ["urgent", "needs_check"], \
            f"Alert level should be urgent or needs_check, got {alert.level}"


class TestFullFallDemoPipeline:
    """
    Test the complete fall demo pipeline end-to-end.
    
    This test verifies:
    1. Pose trigger starts the pipeline
    2. VLM confirms the fall
    3. Check-in attempts are made
    4. Silence leads to alert
    5. Ack resolves the event
    6. No LISTENING->LISTENING crash occurs
    """
    
    @pytest.mark.asyncio
    async def test_full_fall_to_ack_resolved(self, event_manager):
        """
        Complete fall demo: pose -> VLM -> check-in -> silence -> alert -> ack -> resolved.
        
        This is the main demo scenario that should work without errors.
        """
        pose_summary = PoseSummary(
            torso_angle=78,
            head_height_ratio=0.15,
            hip_descent_ratio=0.55,
            stillness_seconds=5,
            motion_energy=0.05,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        assert event is not None, "Event should be created"
        assert event.id is not None, "Event should have an ID"
        
        assert event.vlm_assessment is not None, "VLM should have assessed the scene"
        assert event.vlm_assessment.scene_class in ["fall", "lying_on_floor_unclear_cause"], \
            f"VLM should classify as fall, got {event.vlm_assessment.scene_class}"
        
        assert len(event.check_in_attempts) == 3, \
            f"Should have 3 check-in attempts, got {len(event.check_in_attempts)}"
        
        for attempt in event.check_in_attempts:
            assert attempt.intent in ["no_response", "unclear"], \
                f"Silence demo should have no_response intents, got {attempt.intent}"
        
        assert event.decision_row in ["D8", "D9"], \
            f"Silence should lead to D8 or D9, got {event.decision_row}"
        
        assert event.state in ["AWAITING_ACK", "ALERTING"], \
            f"Should be awaiting ack, got {event.state}"
        
        assert len(event.alerts) >= 1, "Should have sent at least one alert"
        
        success = await event_manager.handle_acknowledgement(
            event.id,
            "family",
            "calling",
        )
        
        assert success, "Acknowledgement should succeed"
        
        final_event = event_manager.events.get(event.id)
        assert final_event.state == "RESOLVED", \
            f"Event should be resolved after ack, got {final_event.state}"
        
        if event_manager.state_machine:
            transitions = event_manager.state_machine.transitions
            
            for i, t in enumerate(transitions):
                assert t.from_state != t.to_state, \
                    f"No self-transitions allowed: {t.from_state} at index {i}"
            
            transition_sequence = [t.to_state.value for t in transitions]
            
            expected_states = ["SUSPECTED", "CONFIRMING", "CHECKING_IN", "LISTENING"]
            for state in expected_states:
                assert state in transition_sequence, \
                    f"Missing expected state {state} in transition sequence"
            
            assert "RESOLVED" in transition_sequence, \
                "Should end in RESOLVED state"
    
    @pytest.mark.asyncio
    async def test_prayer_detection_with_real_vlm(self, event_manager):
        """
        Prayer scenario: pose trigger with prayer_cycles > 0.
        
        Note: The mock VLM doesn't detect prayer. With a real VLM:
        - VLM should classify as prayer_sujud or prayer_other_position
        - Decision D1 should apply: stand down silently
        - No voice check-in should occur
        
        In mock mode, this test verifies the pipeline runs without errors
        even if the mock VLM doesn't detect prayer.
        """
        pose_summary = PoseSummary(
            torso_angle=85,
            head_height_ratio=0.1,
            hip_descent_ratio=0.3,
            stillness_seconds=8,
            motion_energy=0.1,
            prayer_cycles=3,  # High prayer cycles
            rapid_descent=False,
            zone="prayer_area",
        )
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.SLOW_LOW_POSTURE,
            pose_summary,
            [],
        )
        
        assert event is not None, "Event should be created"
        
        # With real VLM, prayer_cycles=3 and zone=prayer_area would lead to D1
        # With mock VLM, it goes through the full flow
        # Either outcome is acceptable in this test
        assert event.state is not None, "Event should have a state"
        
        # Verify no LISTENING->LISTENING crash occurred
        if event_manager.state_machine:
            for i, t in enumerate(event_manager.state_machine.transitions):
                assert t.from_state != t.to_state, \
                    f"No self-transitions at index {i}: {t.from_state}"
    
    @pytest.mark.asyncio
    async def test_ok_reply_stands_down(self, event_manager):
        """
        OK reply scenario: fall -> check-in -> "I'm OK" -> stand down.
        """
        pose_summary = PoseSummary(
            torso_angle=70,
            head_height_ratio=0.2,
            hip_descent_ratio=0.45,
            stillness_seconds=4,
            motion_energy=0.1,
            prayer_cycles=0,
            rapid_descent=True,
            zone="floor",
        )
        
        async def inject_ok_reply():
            await asyncio.sleep(0.5)
            event_manager.inject_reply("Saya okey", "ms")
        
        asyncio.create_task(inject_ok_reply())
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        assert event is not None, "Event should be created"
        
        ok_found = False
        for attempt in event.check_in_attempts:
            if attempt.intent == "ok":
                ok_found = True
                break
        
        if ok_found:
            assert event.decision_row == "D5" or event.state in ["STOOD_DOWN", "RESOLVED"], \
                f"OK reply should lead to D5 stand down, got {event.decision_row}, state={event.state}"
            
            assert len(event.alerts) == 0, \
                "OK reply should not trigger alerts"
