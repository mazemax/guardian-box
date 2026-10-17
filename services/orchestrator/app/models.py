"""
GuardianBox Data Models (PRD §14)

Pydantic models for events, assessments, check-ins, alerts, and configuration.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_id() -> str:
    return str(uuid.uuid4())[:8]


class Posture(str, Enum):
    STANDING = "standing"
    SITTING_CHAIR = "sitting_chair"
    SITTING_FLOOR = "sitting_floor"
    KNEELING = "kneeling"
    PROSTRATE_FOREHEAD_DOWN = "prostrate_forehead_down"
    LYING_FLOOR = "lying_floor"
    LYING_FURNITURE = "lying_furniture"
    CROUCHING = "crouching"
    UNKNOWN = "unknown"


class Movement(str, Enum):
    NONE_VISIBLE = "none_visible"
    SMALL = "small"
    PURPOSEFUL = "purposeful"


class DistressSign(str, Enum):
    NOT_MOVING = "not_moving"
    AWKWARD_LIMB_POSITION = "awkward_limb_position"
    FACE_DOWN_NOT_PRAYER = "face_down_not_prayer"
    OBJECT_KNOCKED_OVER = "object_knocked_over"
    NEAR_FURNITURE_EDGE = "near_furniture_edge"
    HOLDING_BODY_PART = "holding_body_part"


class VLMAssessment(BaseModel):
    """VLM assessment result following vlm-assessment.v1 schema."""
    person_visible: bool
    scene_class: str
    posture: Posture
    on_prayer_mat_or_area: bool = False
    movement: Movement
    distress_signs: List[DistressSign] = Field(default_factory=list, max_length=5)
    fall_likelihood: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    description: str = Field(max_length=160)
    reasons: List[str] = Field(default_factory=list, max_length=4)


class PoseSummary(BaseModel):
    """Summary of pose features (numbers only, no images)."""
    torso_angle: float = 0
    head_height_ratio: float = 0
    hip_descent_ratio: float = 0
    stillness_seconds: float = 0
    motion_energy: float = 0
    prayer_cycles: int = 0
    rapid_descent: bool = False
    zone: str = "floor"


class CheckInAttempt(BaseModel):
    """Record of a single check-in attempt."""
    n: int
    phrase_id: str
    lang: str
    played_at: datetime = Field(default_factory=utc_now)
    listen_ms: int = 0
    vad_speech_ms: int = 0
    transcript: Optional[str] = None
    detected_lang: Optional[str] = None
    intent: Optional[str] = None
    confidence: float = 0
    keyword_hit: Optional[str] = None


class Alert(BaseModel):
    """Telegram alert record."""
    id: str = Field(default_factory=generate_id)
    event_id: str
    contact_id: str
    contact_name: str
    level: str  # urgent, needs_help, needs_check, system
    text: str
    telegram_message_id: Optional[int] = None
    status: str = "queued"  # queued, sent, failed, delayed_sent
    attempts: int = 0
    sent_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


class Acknowledgement(BaseModel):
    """Family acknowledgement of an alert."""
    alert_id: str
    contact_id: str
    contact_name: str
    action: str  # calling, on_my_way, false_alarm, call_999_info
    at: datetime = Field(default_factory=utc_now)


class ModelRun(BaseModel):
    """Record of a model inference run."""
    event_id: Optional[str] = None
    role: str  # pose, vlm, agent, stt, tts, classifier
    model_id: str
    started_at: datetime = Field(default_factory=utc_now)
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    ok: bool = True
    error: Optional[str] = None


class EgressEvent(BaseModel):
    """Record of outbound network request."""
    at: datetime = Field(default_factory=utc_now)
    host: str
    method: str  # Telegram Bot API method name
    bytes_out: int
    bytes_in: int
    allowed: bool = True


class Event(BaseModel):
    """A detection event from trigger to resolution."""
    id: str = Field(default_factory=generate_id)
    resident_id: str = "default"
    camera_id: str = "cam1"
    trigger: str  # fast_fall, slow_low_posture, watchdog, manual_test
    state: str = "MONITORING"
    decision_row: Optional[str] = None  # D1-D12
    started_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = None
    
    pose_summary: Optional[PoseSummary] = None
    vlm_assessment: Optional[VLMAssessment] = None
    vlm_latency_ms: int = 0
    vlm_valid: bool = True
    vlm_attempts: int = 0
    
    check_in_attempts: List[CheckInAttempt] = Field(default_factory=list)
    alerts: List[Alert] = Field(default_factory=list)
    acknowledgements: List[Acknowledgement] = Field(default_factory=list)
    model_runs: List[ModelRun] = Field(default_factory=list)
    
    transitions: List[Dict[str, Any]] = Field(default_factory=list)


class Resident(BaseModel):
    """Resident configuration."""
    id: str = Field(default_factory=generate_id)
    display_name: str
    honorific: str = "Pakcik"  # Pakcik, Makcik, Uncle, Auntie, 阿伯, etc.
    languages: List[str] = Field(default=["ms", "en"])
    hearing_profile: str = "normal"  # normal, loud
    prays: bool = False
    notes: Optional[str] = None


class Contact(BaseModel):
    """Family contact configuration."""
    id: str = Field(default_factory=generate_id)
    name: str
    relation: str = ""
    telegram_chat_id: str
    language: str = "en"
    priority: int = 1
    quiet_hours_start: Optional[int] = None  # Hour (0-23)
    quiet_hours_end: Optional[int] = None


class Zone(BaseModel):
    """Camera zone definition."""
    camera_id: str
    kind: str  # floor, sofa_bed, prayer_area, chair, ignore
    polygon: List[List[float]] = Field(default_factory=list)
    floor_line: float = 0.8  # Normalized Y coordinate of floor


class Household(BaseModel):
    """Household configuration."""
    id: str = Field(default_factory=generate_id)
    name: str = "Home"
    timezone: str = "Asia/Kuala_Lumpur"
    armed: bool = False
    pause_until: Optional[datetime] = None
    residents: List[Resident] = Field(default_factory=list)
    contacts: List[Contact] = Field(default_factory=list)
    zones: List[Zone] = Field(default_factory=list)


class SystemStatus(BaseModel):
    """System status for dashboard."""
    armed: bool = False
    paused: bool = False
    pause_until: Optional[datetime] = None
    state: str = "MONITORING"
    camera_fps: float = 0
    internet_reachable: bool = True
    models_resident: Dict[str, bool] = Field(default_factory=dict)
    memory_used_gb: float = 0
    memory_total_gb: float = 128
    gpu_util_percent: float = 0
    egress_messages_today: int = 0
    egress_bytes_today: int = 0
    video_bytes_out: int = 0  # Should always be 0
    queued_alerts: int = 0
