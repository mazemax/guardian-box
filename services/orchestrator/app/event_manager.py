"""
GuardianBox Event Manager

Manages the full lifecycle of detection events, coordinating:
- State machine transitions
- Decision table application
- VLM/STT/TTS service calls
- Alert sending
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable, Awaitable, List
import httpx

from .telegram import (
    should_send_live_telegram,
    get_telegram_chat_ids_from_env,
    build_resolved_telegram_text,
)

from .state_machine import (
    EventState, EventStateMachine, TriggerType, TransitionReason
)
from .decision_table import (
    apply_decision_table, SceneClass, ReplyIntent, DecisionOutcome,
    DecisionResult, agent_can_override
)
from .keywords import classify_reply
from .models import (
    Event, PoseSummary, VLMAssessment, CheckInAttempt,
    Alert, Acknowledgement, ModelRun, Resident, Contact,
    generate_id, utc_now
)


logger = logging.getLogger(__name__)


class TimingProfile:
    """Timing constants for different profiles."""
    
    @staticmethod
    def get(profile: str = "home") -> Dict[str, Any]:
        if profile == "demo":
            return {
                "stillness_before_vlm_s": 3,
                "listen_window_s": 5,
                "listen_max_s": 10,
                "max_check_in_attempts": 3,
                "vlm_timeout_s": 15,
                "ack_timeout_s": 180,  # 3 min
                "escalation_interval_s": 300,  # 5 min
                "watchdog_prayer_min": 3,
                "watchdog_ok_min": 2,
            }
        return {
            "stillness_before_vlm_s": 10,
            "listen_window_s": 8,
            "listen_max_s": 15,
            "max_check_in_attempts": 3,
            "vlm_timeout_s": 15,
            "ack_timeout_s": 180,
            "escalation_interval_s": 300,
            "watchdog_prayer_min": 3,
            "watchdog_ok_min": 2,
        }


class EventManager:
    """
    Manages detection events through their full lifecycle.
    
    The orchestrator works without the LLM (fixed rules) and without
    the VLM (fail-safe to check-in). The pipeline never blocks on the agent.
    """
    
    def __init__(
        self,
        vllm_url: str = "http://vllm:8000",
        stt_url: str = "http://stt:8001",
        voice_url: str = "http://voice:8002",
        notifier_url: str = "http://notifier:8003",
        profile: str = "home",
        mock_mode: bool = False,
        telegram_live_in_mock: bool = True,
    ):
        self.vllm_url = vllm_url
        self.stt_url = stt_url
        self.voice_url = voice_url
        self.notifier_url = notifier_url
        self.timing = TimingProfile.get(profile)
        self.mock_mode = mock_mode
        self.telegram_live_in_mock = telegram_live_in_mock
        
        self.active_event: Optional[Event] = None
        self.state_machine: Optional[EventStateMachine] = None
        self.events: Dict[str, Event] = {}
        self.model_runs: list[ModelRun] = []
        self._event_listeners: list[Callable[[str, dict], Awaitable[None]]] = []
        self._pending_reply: Optional[Dict[str, Any]] = None
        self._reply_event: asyncio.Event = asyncio.Event()
        self.current_scenario: Optional[str] = None  # Demo scenario hint for mock VLM
        
        self.resident = Resident(
            display_name="Ahmad",
            honorific="Pakcik",
            languages=["ms", "en"],
            prays=True,
        )
        self.contacts: list[Contact] = []
        
        self._http_client: Optional[httpx.AsyncClient] = None

        self.sync_contacts_from_env()
    
    def sync_contacts_from_env(self):
        """Load family contacts from TELEGRAM_CHAT_IDS when configured."""
        chat_ids = get_telegram_chat_ids_from_env()
        if not chat_ids:
            return
        self.contacts = [
            Contact(
                name=f"Family {idx + 1}",
                telegram_chat_id=chat_id,
                language="en",
                priority=idx + 1,
            )
            for idx, chat_id in enumerate(chat_ids)
        ]

    def telegram_live_enabled(self) -> bool:
        """Whether to send/poll real Telegram via the notifier."""
        if not self.telegram_live_in_mock and self.mock_mode:
            return False
        return should_send_live_telegram(self.mock_mode)

    async def _emit_telegram_preview(self, alert: Alert):
        """Dashboard phone-style preview of the Telegram alert."""
        await self._emit_event("telegram_preview", {
            "type": "alert",
            "event_id": alert.event_id,
            "alert_id": alert.id,
            "level": alert.level,
            "contact": alert.contact_name,
            "text": alert.text,
            "snapshot_url": "/api/v1/vision/snapshot.jpg",
            "buttons": [
                {"id": "calling", "text": "📞 I'm calling them now"},
                {"id": "on_my_way", "text": "🚗 On my way"},
                {"id": "false_alarm", "text": "✅ False alarm"},
                {"id": "need_more_info", "text": "❓ Need more info"},
            ],
        })

    async def _update_telegram_message(self, event: Event, text: str) -> None:
        """Edit the Telegram alert after stand-down or resolve."""
        if not self.telegram_live_enabled():
            return
        try:
            client = await self.get_http_client()
            await client.post(
                f"{self.notifier_url}/update/{event.id}",
                params={"text": text},
            )
        except Exception as e:
            logger.error(f"Telegram resolve update error: {e}")
    
    async def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    def add_event_listener(self, listener: Callable[[str, dict], Awaitable[None]]):
        """Add a listener for SSE events."""
        self._event_listeners.append(listener)
    
    async def _emit_event(self, event_type: str, data: dict):
        """Emit an event to all listeners (for SSE)."""
        for listener in self._event_listeners:
            try:
                await listener(event_type, data)
            except Exception as e:
                logger.error(f"Error in event listener: {e}")
    
    async def handle_pose_trigger(
        self,
        trigger_type: TriggerType,
        pose_summary: PoseSummary,
        frame_paths: Optional[list[str]] = None,
    ) -> Event:
        """
        Handle a pose trigger from the vision service.
        
        This starts or updates an event and runs through the state machine.
        """
        if self.active_event and self.state_machine:
            self.active_event.pose_summary = pose_summary
            await self._emit_event("pose", pose_summary.model_dump())
            return self.active_event
        
        event_id = generate_id()
        event = Event(
            id=event_id,
            trigger=trigger_type.value,
            state=EventState.SUSPECTED.value,
            pose_summary=pose_summary,
        )
        
        self.state_machine = EventStateMachine(event_id, EventState.MONITORING)
        self.state_machine.transition(EventState.SUSPECTED, TransitionReason.POSE_TRIGGER, {
            "trigger": trigger_type.value,
            "torso_angle": pose_summary.torso_angle,
            "stillness": pose_summary.stillness_seconds,
        })
        
        self.active_event = event
        self.events[event_id] = event
        
        await self._emit_event("state", {
            "event_id": event_id,
            "state": EventState.SUSPECTED.value,
            "trigger": trigger_type.value,
        })
        
        await self._run_vlm_confirmation(event, frame_paths or [])
        
        return event
    
    async def _run_vlm_confirmation(self, event: Event, frame_paths: list[str]):
        """Run VLM confirmation and apply decision table."""
        if not self.state_machine:
            return
        
        self.state_machine.transition(EventState.CONFIRMING, TransitionReason.VLM_RESULT)
        event.state = EventState.CONFIRMING.value
        
        await self._emit_event("state", {
            "event_id": event.id,
            "state": EventState.CONFIRMING.value,
        })
        
        vlm_result = await self._call_vlm(event, frame_paths)
        
        if vlm_result:
            event.vlm_assessment = vlm_result
            event.vlm_valid = True
            
            scene_class = SceneClass(vlm_result.scene_class) if vlm_result.scene_class in [e.value for e in SceneClass] else None
            decision = apply_decision_table(
                scene_class=scene_class,
                confidence=vlm_result.confidence,
                reply_intent=None,
                rapid_descent=event.pose_summary.rapid_descent if event.pose_summary else False,
            )
        else:
            event.vlm_valid = False
            decision = apply_decision_table(
                scene_class=None,
                confidence=0,
                reply_intent=None,
                vlm_error=True,
            )
        
        event.decision_row = decision.rule
        
        await self._emit_event("vlm_result", {
            "event_id": event.id,
            "assessment": vlm_result.model_dump() if vlm_result else None,
            "decision": {
                "rule": decision.rule,
                "outcome": decision.outcome.value,
                "reason": decision.reason,
            }
        })
        
        await self._apply_decision(event, decision)
    
    async def _call_vlm(self, event: Event, frame_paths: list[str]) -> Optional[VLMAssessment]:
        """Call the VLM service for fall confirmation."""
        start_time = datetime.now(timezone.utc)
        
        model_run = ModelRun(
            event_id=event.id,
            role="vlm",
            model_id="nemotron_3_nano_omni",
        )
        
        try:
            if self.mock_mode:
                await asyncio.sleep(0.5)
                result = self._get_mock_vlm_result(event)
            else:
                client = await self.get_http_client()
                
                response = await asyncio.wait_for(
                    client.post(
                        f"{self.vllm_url}/v1/chat/completions",
                        json=self._build_vlm_request(event, frame_paths),
                    ),
                    timeout=self.timing["vlm_timeout_s"]
                )
                
                if response.status_code != 200:
                    raise Exception(f"VLM returned {response.status_code}")
                
                result = self._parse_vlm_response(response.json())
            
            latency = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            model_run.latency_ms = latency
            model_run.ok = True
            event.vlm_latency_ms = latency
            
            await self._emit_event("model_run", model_run.model_dump())
            self.model_runs.append(model_run)
            
            return result
            
        except asyncio.TimeoutError:
            logger.warning("VLM timeout")
            model_run.ok = False
            model_run.error = "timeout"
            await self._emit_event("model_run", model_run.model_dump())
            self.model_runs.append(model_run)
            return None
            
        except Exception as e:
            logger.error(f"VLM error: {e}")
            model_run.ok = False
            model_run.error = str(e)
            await self._emit_event("model_run", model_run.model_dump())
            self.model_runs.append(model_run)
            return None
    
    def _get_mock_vlm_result(self, event: Event) -> VLMAssessment:
        """Generate mock VLM result based on current scenario or pose data."""
        pose = event.pose_summary
        zone = pose.zone if pose else "floor"
        prayer_cycles = pose.prayer_cycles if pose else 0
        
        # Scenario-aware mock VLM responses
        if self.current_scenario == "prayer":
            return VLMAssessment(
                person_visible=True,
                scene_class="prayer_sujud",
                posture="prostrate_forehead_down",
                movement="small",
                fall_likelihood=0.05,
                confidence=0.92,
                description="Person in prayer position on prayer mat, rhythmic movement",
                reasons=["on prayer mat", "sujud posture", "rhythmic cycles detected"],
            )
        
        # If zone is prayer_area with prayer_cycles, return prayer scene
        if zone == "prayer_area" and prayer_cycles >= 2:
            return VLMAssessment(
                person_visible=True,
                scene_class="prayer_sujud",
                posture="prostrate_forehead_down",
                movement="small",
                fall_likelihood=0.05,
                confidence=0.90,
                description="Person in prostration position on prayer mat",
                reasons=["prayer area", "forehead on floor", f"{prayer_cycles} prayer cycles"],
            )
        
        # Default fall scenario for other cases
        return VLMAssessment(
            person_visible=True,
            scene_class="fall",
            posture="lying_floor",
            movement="none_visible",
            fall_likelihood=0.85,
            confidence=0.82,
            description="Person lying on floor near sofa, not moving",
            reasons=["horizontal posture", "no movement", "near furniture"],
        )
    
    def _build_vlm_request(self, event: Event, frame_paths: list[str]) -> dict:
        """Build the VLM request with cultural context."""
        pose = event.pose_summary
        zone_hint = pose.zone if pose else "floor"
        prayer_cycles = pose.prayer_cycles if pose else 0
        
        system_prompt = """You are analyzing frames from a home safety camera to determine if an elderly person has fallen.
        
The resident may perform Muslim prayer (solat), which includes kneeling, prostration with forehead on the floor (sujud) and sitting positions, usually on a prayer mat, in repeated cycles. This is normal, not a fall.

Classify the scene and respond with valid JSON only. Do not identify people or describe appearance beyond posture and location."""
        
        user_prompt = f"""Analyze these frames. Zone hint: {zone_hint}. Prayer rhythm: {prayer_cycles} low-posture cycles in 60s.

Return JSON matching this schema:
{{
  "person_visible": boolean,
  "scene_class": "fall" | "lying_on_floor_unclear_cause" | "prayer_sujud" | "prayer_other_position" | "kneeling_to_pick_up_or_reach" | "sitting_on_floor_resting" | "gardening_or_floor_work" | "lying_on_sofa_or_bed" | "exercising_or_stretching" | "no_person_or_unclear",
  "posture": "standing" | "sitting_chair" | "sitting_floor" | "kneeling" | "prostrate_forehead_down" | "lying_floor" | "lying_furniture" | "crouching" | "unknown",
  "on_prayer_mat_or_area": boolean,
  "movement": "none_visible" | "small" | "purposeful",
  "distress_signs": [],
  "fall_likelihood": 0-1,
  "confidence": 0-1,
  "description": "short neutral description max 160 chars",
  "reasons": ["reason1", "reason2"]
}}"""
        
        return {
            "model": "nemotron_3_nano_omni",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
    
    def _parse_vlm_response(self, response: dict) -> VLMAssessment:
        """Parse VLM response into VLMAssessment."""
        import json
        content = response["choices"][0]["message"]["content"]
        data = json.loads(content)
        return VLMAssessment(**data)
    
    async def _apply_decision(self, event: Event, decision: DecisionResult):
        """Apply the decision result and transition states."""
        if not self.state_machine:
            return
        
        if decision.outcome == DecisionOutcome.STAND_DOWN_SILENT:
            self.state_machine.transition(
                EventState.STOOD_DOWN,
                TransitionReason.DECISION_TABLE,
                {"rule": decision.rule}
            )
            event.state = EventState.STOOD_DOWN.value
            
            if decision.watchdog_minutes:
                asyncio.create_task(self._start_watchdog(event, decision.watchdog_minutes))
            
            await self._emit_event("state", {
                "event_id": event.id,
                "state": EventState.STOOD_DOWN.value,
                "decision": decision.rule,
                "reason": decision.reason,
            })
            
        elif decision.outcome == DecisionOutcome.STAND_DOWN:
            self.state_machine.transition(
                EventState.STOOD_DOWN,
                TransitionReason.DECISION_TABLE,
                {"rule": decision.rule}
            )
            event.state = EventState.STOOD_DOWN.value
            
            if decision.spoken_phrase_id:
                await self._play_phrase(event, decision.spoken_phrase_id)
            
            if decision.watchdog_minutes:
                asyncio.create_task(self._start_watchdog(event, decision.watchdog_minutes))
            
            await self._resolve_event(event)
            
        elif decision.outcome in {DecisionOutcome.SOFT_CHECK_IN, DecisionOutcome.CHECK_IN}:
            self.state_machine.transition(
                EventState.CHECKING_IN,
                TransitionReason.DECISION_TABLE,
                {"rule": decision.rule}
            )
            event.state = EventState.CHECKING_IN.value
            
            await self._emit_event("state", {
                "event_id": event.id,
                "state": EventState.CHECKING_IN.value,
            })
            
            await self._run_check_in(event, decision)
            
        elif decision.outcome == DecisionOutcome.RECHECK:
            self.state_machine.transition(
                EventState.RECHECK,
                TransitionReason.DECISION_TABLE,
                {"rule": decision.rule}
            )
            await self._run_check_in(event, decision, is_recheck=True)
            
        elif decision.outcome in {
            DecisionOutcome.ALERT_URGENT,
            DecisionOutcome.ALERT_NO_RESPONSE,
            DecisionOutcome.ALERT_NEEDS_CHECK,
        }:
            await self._send_alert(event, decision)
    
    async def _run_check_in(
        self,
        event: Event,
        decision: DecisionResult,
        is_recheck: bool = False
    ):
        """Run the check-in loop (up to 3 attempts)."""
        if not self.state_machine:
            return
        
        max_attempts = 1 if decision.outcome == DecisionOutcome.SOFT_CHECK_IN else self.timing["max_check_in_attempts"]
        languages = self.resident.languages
        
        for attempt_num in range(1, max_attempts + 1):
            lang_idx = min(attempt_num - 1, len(languages) - 1)
            lang = languages[lang_idx]
            
            if is_recheck:
                phrase_id = "recheck"
            elif attempt_num == 1:
                phrase_id = decision.spoken_phrase_id or "checkin_1"
            else:
                phrase_id = f"checkin_{attempt_num}"
            
            self.state_machine.transition(
                EventState.LISTENING,
                TransitionReason.CHECKIN_ATTEMPT,
                {"attempt": attempt_num, "phrase": phrase_id}
            )
            event.state = EventState.LISTENING.value
            
            await self._play_phrase(event, phrase_id, lang)
            
            attempt = CheckInAttempt(
                n=attempt_num,
                phrase_id=phrase_id,
                lang=lang,
            )
            
            await self._emit_event("checkin", {
                "event_id": event.id,
                "attempt": attempt_num,
                "phrase_id": phrase_id,
                "lang": lang,
                "state": "listening",
            })
            
            stt_result = await self._listen_for_reply(event)
            
            attempt.listen_ms = stt_result.get("listen_ms", 0)
            attempt.vad_speech_ms = stt_result.get("vad_speech_ms", 0)
            attempt.transcript = stt_result.get("transcript")
            attempt.detected_lang = stt_result.get("detected_lang")
            
            intent, keyword, confidence = classify_reply(
                attempt.transcript or "",
                attempt.detected_lang,
                stt_result.get("vad_speech_ms", 0) / 1000,
                stt_result.get("no_speech_prob", 0),
            )
            
            attempt.intent = intent.value
            attempt.confidence = confidence
            attempt.keyword_hit = keyword
            
            event.check_in_attempts.append(attempt)
            
            await self._emit_event("checkin", {
                "event_id": event.id,
                "attempt": attempt_num,
                "transcript": attempt.transcript,
                "intent": intent.value,
                "keyword": keyword,
                "state": "classified",
            })
            
            next_decision = apply_decision_table(
                scene_class=SceneClass(event.vlm_assessment.scene_class) if event.vlm_assessment else None,
                confidence=event.vlm_assessment.confidence if event.vlm_assessment else 0,
                reply_intent=intent,
                rapid_descent=event.pose_summary.rapid_descent if event.pose_summary else False,
                still_on_floor=True,
                floor_time_minutes=0,
                check_in_attempts=attempt_num,
            )
            
            event.decision_row = next_decision.rule
            
            if next_decision.outcome in {
                DecisionOutcome.ALERT_URGENT,
                DecisionOutcome.ALERT_NO_RESPONSE,
                DecisionOutcome.ALERT_NEEDS_CHECK,
            }:
                self.state_machine.transition(
                    EventState.DECIDING,
                    TransitionReason.REPLY_CLASSIFIED,
                    {"intent": intent.value, "final": True}
                )
                await self._send_alert(event, next_decision)
                return
            
            if next_decision.outcome == DecisionOutcome.STAND_DOWN:
                self.state_machine.transition(
                    EventState.DECIDING,
                    TransitionReason.REPLY_CLASSIFIED,
                    {"intent": intent.value}
                )
                await self._apply_decision(event, next_decision)
                return
            
            if intent not in {ReplyIntent.UNCLEAR, ReplyIntent.NO_RESPONSE}:
                self.state_machine.transition(
                    EventState.DECIDING,
                    TransitionReason.REPLY_CLASSIFIED,
                    {"intent": intent.value}
                )
                break
            
            if attempt_num < max_attempts:
                self.state_machine.transition(
                    EventState.CHECKING_IN,
                    TransitionReason.UNCLEAR_RESPONSE if intent == ReplyIntent.UNCLEAR else TransitionReason.NO_RESPONSE,
                    {"attempt": attempt_num, "reason": "retry"}
                )
                event.state = EventState.CHECKING_IN.value
        
        self.state_machine.transition(
            EventState.DECIDING,
            TransitionReason.REPLY_CLASSIFIED,
            {"attempts_exhausted": True}
        )
        final_decision = apply_decision_table(
            scene_class=None,
            confidence=0,
            reply_intent=ReplyIntent.NO_RESPONSE,
            check_in_attempts=max_attempts,
        )
        event.decision_row = final_decision.rule
        await self._send_alert(event, final_decision)
    
    async def _play_phrase(self, event: Event, phrase_id: str, lang: str = "ms"):
        """Play a voice phrase through the voice service."""
        model_run = ModelRun(
            event_id=event.id,
            role="tts",
            model_id="clips",
        )
        start = datetime.now(timezone.utc)
        
        try:
            if self.mock_mode:
                await asyncio.sleep(0.3)
            else:
                client = await self.get_http_client()
                await client.post(f"{self.voice_url}/play", json={
                    "phrase_id": phrase_id,
                    "lang": lang,
                    "honorific": self.resident.honorific,
                    "name": self.resident.display_name,
                })
            
            model_run.latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            model_run.ok = True
        except Exception as e:
            logger.error(f"Voice error: {e}")
            model_run.ok = False
            model_run.error = str(e)
        
        self.model_runs.append(model_run)
        await self._emit_event("model_run", model_run.model_dump())
    
    def inject_reply(self, transcript: str, lang: str = "ms") -> bool:
        """
        Inject a simulated reply into the active event's listening step.
        Used for demo/testing via the simulate-reply endpoint.
        Returns True if the reply was accepted, False if no event is listening.
        """
        if not self.active_event or self.state_machine is None:
            return False
        if self.state_machine.state != EventState.LISTENING:
            return False
        
        self._pending_reply = {
            "transcript": transcript,
            "detected_lang": lang,
            "no_speech_prob": 0.1 if transcript else 0.9,
            # Enough VAD duration for classify_reply keyword matching (>= 0.3s)
            "vad_speech_ms": max(400, len(transcript) * 80) if transcript else 0,
            "listen_ms": self.timing["listen_window_s"] * 1000,
            "simulated": True,
        }
        self._reply_event.set()
        return True
    
    async def _listen_for_reply(self, event: Event) -> dict:
        """
        Listen for a reply through the STT service or wait for a simulated reply.
        Supports demo replay via inject_reply().
        """
        model_run = ModelRun(
            event_id=event.id,
            role="stt",
            model_id="malaysian_whisper",
        )
        start = datetime.now(timezone.utc)
        
        self._reply_event.clear()
        self._pending_reply = None
        
        try:
            if self.mock_mode:
                try:
                    await asyncio.wait_for(
                        self._reply_event.wait(),
                        timeout=self.timing["listen_window_s"]
                    )
                    if self._pending_reply:
                        result = self._pending_reply
                        self._pending_reply = None
                    else:
                        result = {
                            "transcript": "",
                            "detected_lang": None,
                            "no_speech_prob": 0.9,
                            "vad_speech_ms": 0,
                            "listen_ms": self.timing["listen_window_s"] * 1000,
                        }
                except asyncio.TimeoutError:
                    result = {
                        "transcript": "",
                        "detected_lang": None,
                        "no_speech_prob": 0.9,
                        "vad_speech_ms": 0,
                        "listen_ms": self.timing["listen_window_s"] * 1000,
                    }
            else:
                client = await self.get_http_client()
                response = await client.post(f"{self.stt_url}/listen", json={
                    "duration_s": self.timing["listen_window_s"],
                    "max_duration_s": self.timing["listen_max_s"],
                    "languages": self.resident.languages,
                })
                result = response.json()
            
            model_run.latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            model_run.ok = True
            
        except Exception as e:
            logger.error(f"STT error: {e}")
            model_run.ok = False
            model_run.error = str(e)
            result = {
                "transcript": "",
                "detected_lang": None,
                "no_speech_prob": 1.0,
                "vad_speech_ms": 0,
                "listen_ms": 0,
            }
        
        self.model_runs.append(model_run)
        await self._emit_event("model_run", model_run.model_dump())
        
        return result
    
    async def _send_alert(self, event: Event, decision: DecisionResult):
        """Send alert to family contacts."""
        if not self.state_machine:
            return
        
        self.state_machine.transition(
            EventState.ALERTING,
            TransitionReason.DECISION_TABLE,
            {"rule": decision.rule}
        )
        event.state = EventState.ALERTING.value
        
        if decision.spoken_phrase_id:
            await self._play_phrase(event, decision.spoken_phrase_id)
        
        default_contacts: List[Contact] = []
        if not self.contacts and not self.telegram_live_enabled():
            default_contacts = [
                Contact(name="Family", telegram_chat_id="mock", language="en"),
            ]
        for contact in self.contacts[:1] or default_contacts:
            alert = Alert(
                event_id=event.id,
                contact_id=contact.id,
                contact_name=contact.name,
                level=decision.alert_level.value if decision.alert_level else "urgent",
                text=self._build_alert_text(event, decision, contact),
            )
            event.alerts.append(alert)
            
            success = await self._send_telegram_alert(alert, contact)
            
            if success:
                self.state_machine.transition(
                    EventState.AWAITING_ACK,
                    TransitionReason.ALERT_SENT,
                )
                event.state = EventState.AWAITING_ACK.value
            else:
                self.state_machine.transition(
                    EventState.ALERT_QUEUED,
                    TransitionReason.ALERT_FAILED,
                )
                event.state = EventState.ALERT_QUEUED.value
                await self._play_phrase(event, "offline")
            
            await self._emit_event("alert", {
                "event_id": event.id,
                "alert_id": alert.id,
                "contact": contact.name,
                "status": alert.status,
                "level": alert.level,
            })
    
    def _build_alert_text(self, event: Event, decision: DecisionResult, contact: Contact) -> str:
        """Build the alert message text (max 1024 chars, text only)."""
        resident = self.resident
        name = f"{resident.honorific} {resident.display_name}"
        zone = event.pose_summary.zone if event.pose_summary else "living room"
        time_str = datetime.now(timezone.utc).strftime("%H:%M")
        
        vlm_desc = ""
        if event.vlm_assessment:
            vlm_desc = event.vlm_assessment.description
        
        quote = ""
        if event.check_in_attempts:
            last = event.check_in_attempts[-1]
            if last.transcript:
                quote = f'What they said: "{last.transcript[:60]}"'
        
        if decision.outcome == DecisionOutcome.ALERT_NO_RESPONSE:
            if contact.language == "ms":
                text = f"""🚨 SEGERA — {name} mungkin terjatuh
{resident.display_name} · {zone} · {time_str} (MYT)

Apa yang dilihat: {vlm_desc or 'seseorang terbaring di lantai, tidak bergerak.'}
Apa yang dicuba: ditanya 3 kali. Tiada jawapan.

Sila telefon dia sekarang. Jika tidak dapat dihubungi dan anda rasa ini kecemasan, hubungi 999.
— Tiada gambar atau video dihantar. Semua AI berjalan di rumah."""
            elif contact.language == "zh":
                text = f"""🚨 紧急 — {name}可能跌倒了
{resident.display_name} · {zone} · {time_str}（马来西亚时间）

看到的情况：{vlm_desc or '有人躺在地上，没有动。'}
已尝试：问了3次，没有回应。

请立刻打电话给他们。如果联系不上并且觉得情况紧急，请拨打999。
— 不会发送任何照片或视频。所有AI都在家里运行。"""
            else:
                text = f"""🚨 URGENT — {name} may have fallen
{resident.display_name} · {zone} · {time_str} (MYT)

What GuardianBox saw: {vlm_desc or 'a person lying on the floor, not moving.'}
What it tried: asked 3 times. No reply.

Please call them now. If you can't reach them and think it's an emergency, call 999.
— No photos or video are ever sent. All AI runs at home."""
        
        elif decision.outcome == DecisionOutcome.ALERT_URGENT:
            text = f"""🚨 URGENT — {name} asked for help
{resident.display_name} · {zone} · {time_str} (MYT)

What GuardianBox saw: {vlm_desc or 'a person on the floor.'}
{quote}

They have been told you're being contacted. Please call them now.
— No photos or video are ever sent."""
        
        else:
            text = f"""⚠️ Please check on {name}
{resident.display_name} · {zone} · {time_str} (MYT)

What GuardianBox saw: {vlm_desc or 'a person on the floor.'}
{quote}

Please give them a call.
— No photos or video are ever sent."""
        
        return text[:1024]
    
    async def _send_telegram_alert(self, alert: Alert, contact: Contact) -> bool:
        """Send alert via notifier service."""
        try:
            if self.mock_mode:
                await asyncio.sleep(0.2)
                await self._emit_telegram_preview(alert)

            if not self.telegram_live_enabled():
                if self.mock_mode:
                    alert.status = "sent"
                    alert.sent_at = utc_now()
                    return True
                alert.status = "failed"
                return False

            client = await self.get_http_client()
            response = await client.post(f"{self.notifier_url}/send", json={
                "chat_id": contact.telegram_chat_id,
                "text": alert.text,
                "event_id": alert.event_id,
                "alert_id": alert.id,
                "level": alert.level,
                "language": contact.language,
            })

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "sent":
                    alert.telegram_message_id = data.get("message_id")
                    alert.status = "sent"
                    alert.sent_at = utc_now()
                    return True
                alert.status = "failed"
                return False

            alert.status = "failed"
            return False

        except Exception as e:
            logger.error(f"Telegram error: {e}")
            alert.status = "failed"
            return False
    
    async def handle_acknowledgement(self, event_id: str, contact_id: str, action: str) -> bool:
        """Handle family acknowledgement of an alert."""
        event = self.events.get(event_id)
        if not event:
            return False
        
        ack = Acknowledgement(
            alert_id=event.alerts[0].id if event.alerts else "",
            contact_id=contact_id,
            contact_name=self.contacts[0].name if self.contacts else "Family",
            action=action,
        )
        event.acknowledgements.append(ack)
        
        if self.state_machine:
            self.state_machine.transition(
                EventState.RESOLVED,
                TransitionReason.ACK_RECEIVED,
                {"action": action}
            )
        
        event.state = EventState.RESOLVED.value
        event.resolved_at = utc_now()
        
        if action in {"calling", "on_my_way"}:
            await self._play_phrase(event, "alert_ack")
        
        await self._emit_event("ack", {
            "event_id": event_id,
            "action": action,
            "contact": ack.contact_name,
        })

        await self._resolve_event(event, telegram_action=action)
        return True
    
    async def _start_watchdog(self, event: Event, minutes: int):
        """Start a watchdog timer to re-check if posture persists."""
        await asyncio.sleep(minutes * 60)
        
        if event.state == EventState.STOOD_DOWN.value:
            logger.info(f"Watchdog triggered for event {event.id}")
            decision = DecisionResult(
                rule="D4",
                outcome=DecisionOutcome.CHECK_IN,
                spoken_phrase_id="soft",
                reason="Watchdog: posture persists"
            )
            await self._apply_decision(event, decision)
    
    async def _resolve_event(self, event: Event, telegram_action: Optional[str] = None):
        """Resolve an event and return to monitoring."""
        if self.state_machine and self.state_machine.state != EventState.RESOLVED:
            try:
                self.state_machine.transition(
                    EventState.RESOLVED,
                    TransitionReason.DECISION_TABLE,
                )
            except ValueError:
                pass
        
        event.state = EventState.RESOLVED.value
        event.resolved_at = utc_now()
        event.transitions = self.state_machine.transitions if self.state_machine else []

        await self._update_telegram_message(
            event,
            build_resolved_telegram_text(telegram_action),
        )
        
        self.active_event = None
        self.state_machine = None
        
        await self._emit_event("state", {
            "event_id": event.id,
            "state": EventState.RESOLVED.value,
        })
    
    def get_current_state(self) -> str:
        """Get current system state."""
        if self.state_machine:
            return self.state_machine.state.value
        return EventState.MONITORING.value
    
    def reset(self):
        """Reset the event manager state (for testing)."""
        self.active_event = None
        self.state_machine = None
        self.events.clear()
        self.model_runs.clear()
        self._pending_reply = None
        self._reply_event = asyncio.Event()
        self.current_scenario = None
