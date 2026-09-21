"""Core speech-to-text contract: dispatch, normalization and error mapping."""

import io

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError


def _minimal_wav(pcm_bytes=b"\x00" * 64):
    """Independently decodable 16-bit mono WAV for contract tests."""
    data_size = len(pcm_bytes)
    return (
        b"RIFF"
        + (36 + data_size).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
        + pcm_bytes
    )


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
        self.audio = _minimal_wav()
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
        self.assertTrue(error.exception.retryable)
        self.assertEqual(error.exception.http_status, 429)

    def test_truncated_webm_is_rejected_before_the_adapter(self):
        called = []

        def stt_test_transcribe(record, audio, model=None, **kwargs):
            called.append(True)
            return {"text": "should not run"}

        self._patch_transcribe(stt_test_transcribe)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(
                b"\x00" * 40,
                filename="chunk.webm",
                content_type="audio/webm",
            )
        self.assertEqual(error.exception.code, "invalid_audio")
        self.assertFalse(error.exception.retryable)
        self.assertEqual(error.exception.action, "replace_chunk")
        self.assertFalse(called)

    def test_mime_mismatch_is_unsupported_format(self):
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(
                self.audio,
                filename="chunk.webm",
                content_type="audio/webm",
            )
        self.assertEqual(error.exception.code, "unsupported_format")
        self.assertFalse(error.exception.retryable)

    def test_live_is_not_inferred_from_transcription_use(self):
        self.assertTrue(self.model.supports_batch_transcription)
        self.assertFalse(self.model.supports_live_transcription)
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe_live_open()
        self.assertEqual(error.exception.code, "unsupported_capability")

    def test_live_session_lifecycle_is_provider_neutral(self):
        provider_model = type(self.env["llm.provider"])
        opened = {}

        def stt_test_transcribe_live_open(record, model=None, **kwargs):
            opened["model"] = model
            return {"handle": "live-1", "transport": "test"}

        def stt_test_transcribe_live_append(record, handle, audio, model=None, **kwargs):
            return {"handle": handle, "buffered_bytes": len(audio)}

        def stt_test_transcribe_live_commit(record, handle, model=None, **kwargs):
            return {
                "handle": handle,
                "events": [{
                    "kind": "final",
                    "text": "hello",
                    "start_ms": 0,
                    "end_ms": 500,
                }],
            }

        def stt_test_transcribe_live_events(record, handle, model=None, **kwargs):
            return {"handle": handle, "events": []}

        def stt_test_transcribe_live_close(record, handle, model=None, **kwargs):
            return {"handle": handle, "closed": True}

        for name, impl in (
            ("stt_test_transcribe_live_open", stt_test_transcribe_live_open),
            ("stt_test_transcribe_live_append", stt_test_transcribe_live_append),
            ("stt_test_transcribe_live_commit", stt_test_transcribe_live_commit),
            ("stt_test_transcribe_live_events", stt_test_transcribe_live_events),
            ("stt_test_transcribe_live_close", stt_test_transcribe_live_close),
        ):
            if not hasattr(provider_model, name):
                setattr(provider_model, name, lambda *args, **kwargs: None)
            self.patch(provider_model, name, impl)

        self.model.supports_live_transcription = True
        opened_result = self.model.transcribe_live_open()
        self.assertEqual(opened_result["handle"], "live-1")
        appended = self.model.transcribe_live_append("live-1", self.audio)
        self.assertEqual(appended["buffered_bytes"], len(self.audio))
        committed = self.model.transcribe_live_commit("live-1")
        self.assertEqual(committed["events"][0]["kind"], "final")
        self.assertEqual(committed["events"][0]["text"], "hello")
        self.assertTrue(committed["events"][0]["replaceable"] is False)
        closed = self.model.transcribe_live_close("live-1")
        self.assertTrue(closed["closed"])
        self.assertEqual(opened["model"], self.model)

