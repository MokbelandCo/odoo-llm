"""Core speech-to-text contract: dispatch, normalization and error mapping."""

import io

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError


@tagged("post_install", "-at_install", "llm")
class TestTranscriptionContract(TransactionCase):
    def setUp(self):
        super().setUp()
        provider_model = type(self.env["llm.provider"])
        original_services = provider_model._get_available_services

        def _services(record):
            return original_services(record) + [
                ("stt_test", "STT Test"),
                ("no_stt", "No STT"),
            ]

        self.patch(provider_model, "_get_available_services", _services)
        self.audio = b"RIFF" + b"\x00" * 64
        self.provider = self.env["llm.provider"].create(
            {
                "name": "STT Test Provider",
                "service": "stt_test",
            }
        )
        self.model = self.env["llm.model"].create(
            {
                "name": "stt-test-model",
                "provider_id": self.provider.id,
                "model_use": "transcription",
            }
        )
        self.unsupported = self.env["llm.provider"].create(
            {
                "name": "No STT Provider",
                "service": "no_stt",
            }
        )
        self.unsupported_model = self.env["llm.model"].create(
            {
                "name": "chat-only-model",
                "provider_id": self.unsupported.id,
                "model_use": "chat",
            }
        )
        self.unsupported_stt_model = self.env["llm.model"].create(
            {
                "name": "orphan-stt-model",
                "provider_id": self.unsupported.id,
                "model_use": "transcription",
            }
        )

    def _patch_transcribe(self, implementation):
        provider_model = type(self.env["llm.provider"])
        if not hasattr(provider_model, "stt_test_transcribe"):
            provider_model.stt_test_transcribe = lambda record, *args, **kwargs: None
        self.patch(provider_model, "stt_test_transcribe", implementation)

    def test_model_use_includes_transcription(self):
        usages = dict(self.env["llm.model"]._get_available_model_usages())
        self.assertEqual(usages["transcription"], "Speech to Text")

    def test_determine_model_use_from_transcription_capability(self):
        self.assertEqual(
            self.provider._determine_model_use(
                "vendor-stt",
                ["transcription"],
            ),
            "transcription",
        )
        self.assertEqual(
            self.provider._determine_model_use("gpt-4o", ["chat"]),
            "chat",
        )

    def test_transcribe_dispatches_bytes_not_storage_references(self):
        captured = {}

        def stt_test_transcribe(
            record,
            audio,
            model=None,
            filename=None,
            content_type=None,
            **kwargs,
        ):
            captured["audio"] = audio
            captured["model"] = model
            captured["filename"] = filename
            captured["content_type"] = content_type
            captured["kwargs"] = kwargs
            return {
                "text": "hello world",
                "language": "en",
                "duration_ms": 1200,
                "segments": [
                    {
                        "text": "hello world",
                        "start_ms": 0,
                        "end_ms": 1200,
                        "speaker": "clinician",
                        "confidence": 0.91,
                        "alternatives": [{"text": "hello word", "confidence": 0.2}],
                    }
                ],
                "provider_request_id": "req-1",
                "model_version": "stt-test-model",
                "native_object": object(),
            }

        self._patch_transcribe(stt_test_transcribe)
        result = self.model.transcribe(
            self.audio,
            filename="chunk.wav",
            content_type="audio/wav",
            language="en",
        )
        self.assertEqual(captured["audio"], self.audio)
        self.assertEqual(captured["model"], self.model)
        self.assertEqual(captured["filename"], "chunk.wav")
        self.assertNotIn("media_key", captured["kwargs"])
        self.assertEqual(result["text"], "hello world")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["duration_ms"], 1200)
        self.assertEqual(result["provider_request_id"], "req-1")
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["speaker"], "clinician")
        self.assertNotIn("native_object", result)

    def test_transcribe_accepts_seekable_stream(self):
        def stt_test_transcribe(record, audio, model=None, **kwargs):
            self.assertEqual(audio, self.audio)
            return {"text": "from stream", "segments": []}

        self._patch_transcribe(stt_test_transcribe)
        stream = io.BytesIO(self.audio)
        result = self.model.transcribe(stream)
        self.assertEqual(result["text"], "from stream")
        self.assertEqual(stream.tell(), 0, "a seekable stream must be rewindable")

    def test_empty_audio_is_rejected_before_provider_dispatch(self):
        called = []

        def stt_test_transcribe(record, audio, model=None, **kwargs):
            called.append(True)
            raise AssertionError("empty audio must not reach the adapter")

        self._patch_transcribe(stt_test_transcribe)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(b"")
        self.assertEqual(error.exception.code, "empty_audio")
        self.assertFalse(called)

    def test_storage_reference_strings_are_invalid_audio(self):
        called = []

        def stt_test_transcribe(record, audio, model=None, **kwargs):
            called.append(audio)
            return {"text": "leaked"}

        self._patch_transcribe(stt_test_transcribe)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe("attachment:12")
        self.assertEqual(error.exception.code, "invalid_audio")
        self.assertFalse(called)
        self.assertNotIn("attachment:12", str(error.exception))

    def test_chat_model_is_not_a_transcription_model(self):
        with self.assertRaises(LLMTranscriptionError) as error:
            self.unsupported_model.transcribe(self.audio)
        self.assertEqual(error.exception.code, "unsupported_capability")

    def test_unsupported_provider_fails_without_adapter_call(self):
        with self.assertRaises(LLMTranscriptionError) as error:
            self.unsupported_stt_model.transcribe(self.audio)
        self.assertEqual(error.exception.code, "unsupported_capability")
        self.assertNotIn("RIFF", str(error.exception))

    def test_provider_failure_is_sanitized(self):
        def stt_test_transcribe(record, audio, model=None, **kwargs):
            raise RuntimeError("transcript=secret-phi api_key=sk-live")

        self._patch_transcribe(stt_test_transcribe)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(self.audio)
        self.assertEqual(error.exception.code, "provider_failure")
        self.assertNotIn("secret-phi", str(error.exception))
        self.assertNotIn("sk-live", str(error.exception))

    def test_adapter_error_codes_are_preserved(self):
        def stt_test_transcribe(record, audio, model=None, **kwargs):
            raise LLMTranscriptionError(
                "throttling",
                "The speech-to-text provider is rate limiting requests.",
            )

        self._patch_transcribe(stt_test_transcribe)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(self.audio)
        self.assertEqual(error.exception.code, "throttling")
