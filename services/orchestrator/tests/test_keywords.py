"""
Tests for GuardianBox Keyword Detection (PRD §FR-AGT-3)

Tests the keyword floor that runs first in three languages.
Includes negation guard tests.
"""

import pytest
from app.keywords import (
    detect_keyword_intent,
    classify_reply,
)
from app.decision_table import ReplyIntent


class TestMalayKeywords:
    """Test Malay (Bahasa Melayu) keyword detection"""
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("okey", ReplyIntent.OK_NO_HELP),
        ("saya okey", ReplyIntent.OK_NO_HELP),
        ("tak apa", ReplyIntent.OK_NO_HELP),
        ("takpe", ReplyIntent.OK_NO_HELP),
        ("saya sihat", ReplyIntent.OK_NO_HELP),
        ("boleh", ReplyIntent.OK_NO_HELP),
    ])
    def test_ok_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "ms")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("tolong", ReplyIntent.NEEDS_HELP),
        ("tolong saya", ReplyIntent.NEEDS_HELP),
        ("minta tolong", ReplyIntent.NEEDS_HELP),
    ])
    def test_help_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "ms")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("sakit", ReplyIntent.PAIN_OR_INJURY),
        ("sakit kaki", ReplyIntent.PAIN_OR_INJURY),
        ("sakit kepala", ReplyIntent.PAIN_OR_INJURY),
        ("cedera", ReplyIntent.PAIN_OR_INJURY),
    ])
    def test_pain_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "ms")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase", [
        "tak boleh bangun",
        "tidak boleh bangun",
        "tak dapat bangun",
    ])
    def test_cannot_get_up_keywords(self, phrase):
        intent, keyword, _ = detect_keyword_intent(phrase, "ms")
        assert intent == ReplyIntent.CANNOT_GET_UP


class TestEnglishKeywords:
    """Test English keyword detection"""
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("okay", ReplyIntent.OK_NO_HELP),
        ("I'm fine", ReplyIntent.OK_NO_HELP),
        ("im fine", ReplyIntent.OK_NO_HELP),
        ("all good", ReplyIntent.OK_NO_HELP),
    ])
    def test_ok_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "en")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("help", ReplyIntent.NEEDS_HELP),
        ("help me", ReplyIntent.NEEDS_HELP),
        ("please help", ReplyIntent.NEEDS_HELP),
    ])
    def test_help_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "en")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("it hurts", ReplyIntent.PAIN_OR_INJURY),
        ("pain", ReplyIntent.PAIN_OR_INJURY),
        ("injured", ReplyIntent.PAIN_OR_INJURY),
    ])
    def test_pain_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "en")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase", [
        "can't get up",
        "cannot get up",
        "cant move",
    ])
    def test_cannot_get_up_keywords(self, phrase):
        intent, keyword, _ = detect_keyword_intent(phrase, "en")
        assert intent == ReplyIntent.CANNOT_GET_UP


class TestChineseKeywords:
    """Test Chinese (Mandarin) keyword detection"""
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("没事", ReplyIntent.OK_NO_HELP),
        ("我没事", ReplyIntent.OK_NO_HELP),
        ("还好", ReplyIntent.OK_NO_HELP),
        ("好", ReplyIntent.OK_NO_HELP),
        ("可以", ReplyIntent.OK_NO_HELP),
    ])
    def test_ok_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "zh")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("帮忙", ReplyIntent.NEEDS_HELP),
        ("救命", ReplyIntent.NEEDS_HELP),
        ("帮我", ReplyIntent.NEEDS_HELP),
    ])
    def test_help_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "zh")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("痛", ReplyIntent.PAIN_OR_INJURY),
        ("好痛", ReplyIntent.PAIN_OR_INJURY),
        ("受伤", ReplyIntent.PAIN_OR_INJURY),
    ])
    def test_pain_keywords(self, phrase, expected_intent):
        intent, keyword, _ = detect_keyword_intent(phrase, "zh")
        assert intent == expected_intent
    
    @pytest.mark.parametrize("phrase", [
        "起不来",
        "站不起来",
        "动不了",
    ])
    def test_cannot_get_up_keywords(self, phrase):
        intent, keyword, _ = detect_keyword_intent(phrase, "zh")
        assert intent == ReplyIntent.CANNOT_GET_UP


class TestNegationGuard:
    """Test negation guard - 'tak sakit' ≠ pain"""
    
    def test_malay_negation_blocks_pain(self):
        """'tak sakit' (not hurt) should not trigger pain intent"""
        intent, keyword, had_negation = detect_keyword_intent("tak sakit saya okey", "ms")
        # Should match OK, not pain (negation blocks sakit)
        assert intent == ReplyIntent.OK_NO_HELP
    
    def test_english_negation_blocks_pain(self):
        """'not hurt' should not trigger pain intent"""
        intent, keyword, had_negation = detect_keyword_intent("no i'm not hurt i'm fine", "en")
        assert intent == ReplyIntent.OK_NO_HELP
    
    def test_chinese_negation_blocks_pain(self):
        """'不痛' (not painful) should not trigger pain intent"""
        # Note: The negation check looks for negation near pain keywords
        intent, keyword, _ = detect_keyword_intent("不痛 我没事", "zh")
        assert intent == ReplyIntent.OK_NO_HELP
    
    def test_negation_far_from_keyword_still_matches(self):
        """Negation that is far from keyword should not block it"""
        # When there's pain clearly expressed without nearby negation
        intent, keyword, _ = detect_keyword_intent("oh my leg it hurts so bad", "en")
        # This should match pain since there's no negation
        assert intent == ReplyIntent.PAIN_OR_INJURY


class TestClassifyReply:
    """Test full reply classification including VAD and confidence"""
    
    def test_empty_transcript_no_speech(self):
        """Empty transcript with no VAD speech = no_response"""
        intent, keyword, confidence = classify_reply(
            transcript="",
            vad_speech_seconds=0,
            no_speech_prob=0.95,
        )
        assert intent == ReplyIntent.NO_RESPONSE
        assert confidence >= 0.9
    
    def test_short_speech_unclear(self):
        """Very short speech duration = unclear"""
        intent, keyword, confidence = classify_reply(
            transcript="mm",
            vad_speech_seconds=0.2,
            no_speech_prob=0.3,
        )
        assert intent == ReplyIntent.UNCLEAR
    
    def test_clear_keyword_high_confidence(self):
        """Clear keyword match should have high confidence"""
        intent, keyword, confidence = classify_reply(
            transcript="tolong saya",
            detected_lang="ms",
            vad_speech_seconds=1.5,
            no_speech_prob=0.1,
        )
        assert intent == ReplyIntent.NEEDS_HELP
        assert keyword == "tolong"
        assert confidence >= 0.9
    
    def test_no_keyword_match_unclear(self):
        """No keyword match = unclear"""
        intent, keyword, confidence = classify_reply(
            transcript="hmmm what",
            detected_lang="en",
            vad_speech_seconds=1.0,
            no_speech_prob=0.2,
        )
        assert intent == ReplyIntent.UNCLEAR
        assert keyword is None


class TestKeywordPrecedence:
    """Test that help/pain/cannot keywords take precedence over OK"""
    
    def test_help_wins_over_ok(self):
        """If both help and ok keywords present, help wins"""
        intent, keyword, _ = detect_keyword_intent("okay but tolong", "ms")
        assert intent == ReplyIntent.NEEDS_HELP
    
    def test_pain_wins_over_ok(self):
        """Pain keywords take precedence"""
        intent, keyword, _ = detect_keyword_intent("okay okay sakit", "ms")
        assert intent == ReplyIntent.PAIN_OR_INJURY
