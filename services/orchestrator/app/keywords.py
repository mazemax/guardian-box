"""
GuardianBox Keyword Detection (PRD §FR-AGT-3)

Keyword floor runs first in three languages. A keyword hit for help, pain,
or can't-get-up escalates regardless of the LLM output.

Includes negation guard: "tak sakit" / "not hurt" / "不痛" should not
trigger pain detection.
"""

import re
from typing import Optional, Tuple
from .decision_table import ReplyIntent


NEGATION_PATTERNS = {
    "ms": [
        r"\btak\b", r"\btidak\b", r"\bbukan\b", r"\bbelum\b", r"\btakde\b",
    ],
    "en": [
        r"\bnot\b", r"\bno\b", r"\bdon'?t\b", r"\bdoesn'?t\b", r"\bnever\b",
        r"\bwasn'?t\b", r"\bisn'?t\b", r"\baren'?t\b", r"\bweren'?t\b",
    ],
    "zh": [
        r"不", r"没", r"无", r"别",
    ],
}

KEYWORDS = {
    ReplyIntent.OK_NO_HELP: {
        "ms": [
            "okey", "ok", "saya okey", "tak apa", "takpe", "saya sihat",
            "boleh", "baik", "saya baik",
        ],
        "en": [
            "okay", "ok", "i'm fine", "im fine", "i am fine", "i'm okay",
            "im okay", "i am okay", "no problem", "yes", "all good",
            "fine", "good", "alright",
        ],
        "zh": [
            "没事", "我没事", "还好", "好", "可以", "无事", "没问题",
            "我很好", "我好", "好的", "行",
        ],
    },
    ReplyIntent.NEEDS_HELP: {
        "ms": [
            "tolong", "bantu", "tolong saya", "minta tolong", "pertolongan",
        ],
        "en": [
            "help", "help me", "please help", "i need help", "need help",
        ],
        "zh": [
            "帮忙", "救命", "帮我", "救我", "需要帮助", "帮帮我",
        ],
    },
    ReplyIntent.PAIN_OR_INJURY: {
        "ms": [
            "sakit", "sakit kaki", "sakit kepala", "sakit pinggang",
            "cedera", "luka", "pedih", "ngilu",
        ],
        "en": [
            "pain", "it hurts", "hurt", "hurts", "injured", "injury",
            "ow", "ouch", "aching", "sore",
        ],
        "zh": [
            "痛", "好痛", "受伤", "疼", "很痛", "伤了",
        ],
    },
    ReplyIntent.CANNOT_GET_UP: {
        "ms": [
            "tak boleh bangun", "tak boleh", "tidak boleh bangun",
            "tak dapat bangun", "susah bangun",
        ],
        "en": [
            "cant get up", "cannot get up",
            "cant stand", "cannot stand",
            "cant move", "cannot move",
        ],
        "zh": [
            "起不来", "站不起来", "不可以", "动不了", "爬不起来",
        ],
    },
    ReplyIntent.CALL_FAMILY: {
        "ms": [
            "panggil anak", "telefon anak", "panggil anak saya",
            "hubungi keluarga", "telefon keluarga",
        ],
        "en": [
            "call my son", "call my daughter", "call my family",
            "call someone", "phone my",
        ],
        "zh": [
            "打电话给", "叫我女儿", "叫我儿子", "通知家人",
        ],
    },
}


def _normalize_text(text: str) -> str:
    """Normalize text for keyword matching."""
    text = text.lower().strip()
    text = text.replace("'", "")
    text = text.replace("'", "")
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _has_nearby_negation(text: str, keyword_pos: int, lang: str, window: int = 30) -> bool:
    """
    Check if there's a negation word near the keyword.
    
    Args:
        text: The normalized text
        keyword_pos: Starting position of the keyword match
        lang: Language code (ms, en, zh)
        window: Character window to check before the keyword
    
    Returns:
        True if negation found in window before keyword
    """
    start = max(0, keyword_pos - window)
    prefix = text[start:keyword_pos]
    
    for pattern in NEGATION_PATTERNS.get(lang, []):
        if re.search(pattern, prefix):
            return True
    
    return False


def detect_keyword_intent(
    transcript: str,
    detected_lang: Optional[str] = None
) -> Tuple[Optional[ReplyIntent], Optional[str], bool]:
    """
    Detect intent from transcript using keyword matching.
    
    The keyword floor runs first. A hit for help, pain, or can't-get-up
    escalates regardless of LLM output.
    
    Args:
        transcript: The STT transcript
        detected_lang: Detected language (ms, en, zh) or None to check all
    
    Returns:
        Tuple of (intent, matched_keyword, had_negation)
        - intent: The detected intent, or None if no keyword matched
        - matched_keyword: The keyword that matched
        - had_negation: True if a negation was found near the keyword
    """
    if not transcript or not transcript.strip():
        return None, None, False
    
    text = _normalize_text(transcript)
    
    langs_to_check = [detected_lang] if detected_lang else ["ms", "en", "zh"]
    
    escalating_intents = [
        ReplyIntent.NEEDS_HELP,
        ReplyIntent.PAIN_OR_INJURY, 
        ReplyIntent.CANNOT_GET_UP,
        ReplyIntent.CALL_FAMILY,
    ]
    
    for intent in escalating_intents:
        for lang in langs_to_check:
            if lang not in KEYWORDS.get(intent, {}):
                continue
            
            for keyword in KEYWORDS[intent][lang]:
                pattern = re.escape(keyword)
                match = re.search(pattern, text)
                
                if match:
                    has_negation = _has_nearby_negation(text, match.start(), lang)
                    
                    if has_negation and intent in {ReplyIntent.PAIN_OR_INJURY, ReplyIntent.NEEDS_HELP}:
                        continue
                    
                    return intent, keyword, has_negation
    
    for lang in langs_to_check:
        if lang not in KEYWORDS.get(ReplyIntent.OK_NO_HELP, {}):
            continue
        
        for keyword in KEYWORDS[ReplyIntent.OK_NO_HELP][lang]:
            if keyword in text:
                return ReplyIntent.OK_NO_HELP, keyword, False
    
    return None, None, False


def classify_reply(
    transcript: str,
    detected_lang: Optional[str] = None,
    vad_speech_seconds: float = 0,
    no_speech_prob: float = 0,
) -> Tuple[ReplyIntent, Optional[str], float]:
    """
    Full reply classification combining keyword detection with heuristics.
    
    Args:
        transcript: The STT transcript
        detected_lang: Detected language
        vad_speech_seconds: Duration of detected speech
        no_speech_prob: Probability of no speech (from STT)
    
    Returns:
        Tuple of (intent, matched_keyword, confidence)
    """
    if not transcript or not transcript.strip():
        if vad_speech_seconds < 0.3:
            return ReplyIntent.NO_RESPONSE, None, 1.0
        return ReplyIntent.UNCLEAR, None, 0.5
    
    if no_speech_prob > 0.8 or vad_speech_seconds < 0.3:
        return ReplyIntent.UNCLEAR, None, 0.3
    
    keyword_intent, matched_keyword, had_negation = detect_keyword_intent(
        transcript, detected_lang
    )
    
    if keyword_intent:
        confidence = 0.95 if not had_negation else 0.6
        return keyword_intent, matched_keyword, confidence
    
    return ReplyIntent.UNCLEAR, None, 0.4
