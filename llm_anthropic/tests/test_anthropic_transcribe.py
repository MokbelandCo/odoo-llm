"""Anthropic has no native STT API; the adapter must fail closed."""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError


@tagged("post_install", "-at_install", "llm_anthropic")
class TestAnthropicTranscribe(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env["llm.provider"].create(
            {
                "name": "Anthropic STT Test",
                "service": "anthropic",
                "api_key": "sk-ant-test-not-used",
            }
        )
        self.chat_model = self.env["llm.model"].create(
            {
                "name": "claude-sonnet-4-5",
                "provider_id": self.provider.id,
                "model_use": "chat",
            }
        )
        self.forced_stt_model = self.env["llm.model"].create(
            {
                "name": "claude-forced-stt",
                "provider_id": self.provider.id,
                "model_use": "transcription",
            }
        )
        self.patch(
            type(self.env["llm.provider"]),
            "anthropic_get_client",
            lambda record: (_ for _ in ()).throw(
                AssertionError("Anthropic client must not be constructed for STT")
            ),
        )

    def test_anthropic_models_are_not_classified_as_transcription(self):
        parsed = self.provider._anthropic_parse_model(
            type("M", (), {"id": "claude-opus-4-5", "display_name": "Opus", "created_at": ""})()
        )
        self.assertNotIn("transcription", parsed["details"]["capabilities"])
        self.assertEqual(
            self.provider._determine_model_use(
                "claude-opus-4-5",
                parsed["details"]["capabilities"],
            ),
            "multimodal",
        )

    def test_transcribe_fails_unsupported_without_a_provider_request(self):
        audio = b"RIFF" + b"\x00" * 64
        with self.assertRaises(LLMTranscriptionError) as error:
            self.forced_stt_model.transcribe(audio, filename="chunk.wav")
        self.assertEqual(error.exception.code, "unsupported_capability")
        self.assertNotIn("RIFF", str(error.exception))

    def test_chat_is_never_used_as_speech_to_text(self):
        called = []

        def boom(*args, **kwargs):
            called.append(True)
            raise AssertionError("Claude chat must not be used as transcription")

        self.patch(type(self.env["llm.provider"]), "anthropic_chat", boom)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.forced_stt_model.transcribe(b"RIFF" + b"\x00" * 64)
        self.assertEqual(error.exception.code, "unsupported_capability")
        self.assertFalse(called)

    def test_anthropic_live_is_unsupported(self):
        from odoo.exceptions import ValidationError

        self.assertFalse(self.forced_stt_model.supports_live_transcription)
        self.assertFalse(self.forced_stt_model.supports_batch_transcription)
        with self.assertRaises(ValidationError):
            self.forced_stt_model.supports_live_transcription = True
        with self.assertRaises(LLMTranscriptionError) as error:
            self.forced_stt_model.transcribe_live_open()
        self.assertEqual(error.exception.code, "unsupported_capability")
        self.assertFalse(
            self.provider.anthropic_transcription_modes(self.forced_stt_model)["batch"]
        )
        self.assertFalse(
            self.provider.anthropic_transcription_modes(self.forced_stt_model)["live"]
        )
        with self.assertRaises(LLMTranscriptionError) as cred_error:
            self.forced_stt_model.transcribe_live_credentials(transport="webrtc")
        self.assertEqual(cred_error.exception.code, "unsupported_capability")
