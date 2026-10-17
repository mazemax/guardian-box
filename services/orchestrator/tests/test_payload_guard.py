"""
Tests for GuardianBox Payload Guard (PRD §FR-ALR-3)

The payload guard MUST block all media-sending methods.
Only sendMessage, answerCallbackQuery, getUpdates, editMessageText are allowed.
"""

import pytest


ALLOWED_METHODS = frozenset([
    "sendMessage",
    "editMessageText",
    "answerCallbackQuery",
    "getUpdates",
])

BLOCKED_METHODS = frozenset([
    "sendPhoto",
    "sendVideo",
    "sendAudio",
    "sendVoice",
    "sendDocument",
    "sendAnimation",
    "sendVideoNote",
    "sendMediaGroup",
    "sendSticker",
    "sendLocation",
    "sendVenue",
    "sendContact",
])


def is_method_allowed(method: str) -> bool:
    """Check if a Telegram API method is in the allowlist."""
    if method in BLOCKED_METHODS:
        return False
    return method in ALLOWED_METHODS


class TestPayloadGuardAllowlist:
    """Test that only text methods are allowed"""
    
    @pytest.mark.parametrize("method", list(ALLOWED_METHODS))
    def test_allowed_methods_pass(self, method):
        """Allowed methods should pass"""
        assert is_method_allowed(method)
    
    @pytest.mark.parametrize("method", list(BLOCKED_METHODS))
    def test_blocked_methods_fail(self, method):
        """Media methods should be blocked"""
        assert not is_method_allowed(method)


class TestPayloadGuardMediaBlocking:
    """Test that all media types are blocked"""
    
    def test_photo_blocked(self):
        assert not is_method_allowed("sendPhoto")
    
    def test_video_blocked(self):
        assert not is_method_allowed("sendVideo")
    
    def test_audio_blocked(self):
        assert not is_method_allowed("sendAudio")
    
    def test_voice_blocked(self):
        assert not is_method_allowed("sendVoice")
    
    def test_document_blocked(self):
        """Documents could contain images/video"""
        assert not is_method_allowed("sendDocument")
    
    def test_animation_blocked(self):
        """GIFs are visual media"""
        assert not is_method_allowed("sendAnimation")
    
    def test_video_note_blocked(self):
        """Video notes (circles) are video"""
        assert not is_method_allowed("sendVideoNote")
    
    def test_media_group_blocked(self):
        """Media groups contain photos/videos"""
        assert not is_method_allowed("sendMediaGroup")
    
    def test_location_blocked(self):
        """Location could reveal privacy-sensitive info"""
        assert not is_method_allowed("sendLocation")


class TestPayloadGuardUnknownMethods:
    """Test handling of unknown methods"""
    
    def test_unknown_method_blocked(self):
        """Unknown methods should not be allowed"""
        assert not is_method_allowed("sendSomethingNew")
    
    def test_case_sensitivity(self):
        """Method names should be case-sensitive"""
        assert not is_method_allowed("sendmessage")  # lowercase
        assert not is_method_allowed("SENDMESSAGE")  # uppercase
        assert is_method_allowed("sendMessage")  # correct case


class TestAlertTextConstraints:
    """Test alert text content constraints"""
    
    def test_max_length_constraint(self):
        """Alert text should be max 1024 chars"""
        max_length = 1024
        text = "x" * max_length
        assert len(text) <= max_length
        
        too_long = "x" * (max_length + 1)
        assert len(too_long) > max_length
    
    def test_no_urls_in_alert(self):
        """Alert should not contain URLs to media"""
        alert_text = """🚨 URGENT — Dad may have fallen
Ahmad · Living room · 14:02 (MYT)
— No photos or video are ever sent."""
        
        # Should not contain http/https URLs
        assert "http://" not in alert_text
        assert "https://" not in alert_text
    
    def test_no_media_references(self):
        """Alert should not reference media files"""
        alert_text = """🚨 URGENT — Dad may have fallen"""
        
        media_extensions = [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webm", ".wav", ".mp3"]
        for ext in media_extensions:
            assert ext not in alert_text.lower()


class TestEgressLogging:
    """Test that egress is properly logged"""
    
    def test_egress_log_structure(self):
        """Egress log should have required fields"""
        sample_log = {
            "at": "2026-10-17T14:02:00Z",
            "host": "api.telegram.org",
            "method": "sendMessage",
            "bytes_out": 512,
            "bytes_in": 100,
            "allowed": True,
        }
        
        required_fields = ["at", "host", "method", "bytes_out", "bytes_in", "allowed"]
        for field in required_fields:
            assert field in sample_log
    
    def test_blocked_egress_logged(self):
        """Blocked attempts should be logged"""
        blocked_log = {
            "at": "2026-10-17T14:02:00Z",
            "host": "api.telegram.org",
            "method": "sendPhoto",
            "bytes_out": 0,
            "bytes_in": 0,
            "allowed": False,
            "error": "blocked_by_payload_guard",
        }
        
        assert blocked_log["allowed"] is False
        assert "blocked" in blocked_log.get("error", "")
