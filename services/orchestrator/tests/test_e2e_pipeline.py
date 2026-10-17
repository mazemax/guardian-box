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
            await asyncio.sleep(1.5)
            for _ in range(20):
                if event_manager.state_machine and \
                   event_manager.state_machine.state == EventState.LISTENING:
                    event_manager.inject_reply("我没事", "zh")
                    return True
                await asyncio.sleep(0.2)
            return False
        
        inject_task = asyncio.create_task(inject_chinese_ok())
        
        event = await event_manager.handle_pose_trigger(
            TriggerType.FAST_FALL,
            pose_summary,
            [],
        )
        
        await inject_task
        
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
