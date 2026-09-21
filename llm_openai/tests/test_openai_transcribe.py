"""OpenAI speech-to-text adapter tests.

Known-text WAV fixtures are generated from the phrases in ``audio/manifest.json``
and sent through ``llm.model.transcribe()``. The SDK is mocked so the assertion
is on request construction plus normalized transcript text.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

AUDIO_DIR = Path(__file__).resolve().parent / "audio"
MANIFEST = json.loads((AUDIO_DIR / "manifest.json").read_text(encoding="utf-8"))


def _load_fixture(name):
    return (AUDIO_DIR / name).read_bytes(), MANIFEST[name]


@tagged("post_install", "-at_install", "llm_openai")
class TestOpenAITranscribe(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env["llm.provider"].create(
            {
                "name": "OpenAI STT Test",
                "service": "openai",
                "api_key": "sk-test-not-used",
            }
        )
        self.model = self.env["llm.model"].create(
            {
                "name": "whisper-1",
                "provider_id": self.provider.id,
                "model_use": "transcription",
            }
        )
        self.calls = []
        self.client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=self._fake_create)
            )
        )
        self.patch(
            type(self.env["llm.provider"]),
            "openai_get_client",
            lambda record: self.client,
        )

    def _fake_create(self, **kwargs):
        self.calls.append(kwargs)
        filename, audio_bytes, content_type = kwargs["file"]
        expected = None
        for name, meta in MANIFEST.items():
            fixture = (AUDIO_DIR / name).read_bytes()
            if audio_bytes == fixture:
                expected = meta["text"]
                break
        if expected is None:
            expected = "unrecognized fixture"
        return SimpleNamespace(
            text=expected,
            language="english",
            duration=1.6,
            id="tr_test_1",
            _request_id="req_test_1",
            model_dump=lambda: {
                "text": expected,
                "language": "english",
                "duration": 1.6,
                "id": "tr_test_1",
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 1.6,
                        "text": expected,
                        "avg_logprob": -0.1,
                        "speaker": None,
                    }
                ],
            },
        )

    def test_known_wav_bytes_are_sent_and_transcript_matches(self):
        audio, meta = _load_fixture("right_eye_eighteen.wav")
        result = self.model.transcribe(
            audio,
            filename="right_eye_eighteen.wav",
            content_type=meta["content_type"],
            language="en",
            timestamps=True,
        )
        self.assertEqual(len(self.calls), 1)
        filename, sent, content_type = self.calls[0]["file"]
        self.assertEqual(sent, audio)
        self.assertEqual(filename, "right_eye_eighteen.wav")
        self.assertEqual(content_type, "audio/wav")
        self.assertEqual(self.calls[0]["model"], "whisper-1")
        self.assertEqual(self.calls[0]["language"], "en")
        self.assertEqual(self.calls[0]["response_format"], "verbose_json")
        self.assertEqual(result["text"], meta["text"])
        self.assertEqual(result["segments"][0]["text"], meta["text"])
        self.assertEqual(result["duration_ms"], 1600)
        self.assertEqual(result["provider_request_id"], "tr_test_1")
        self.assertEqual(result["model_version"], "whisper-1")

    def test_second_known_phrase_is_independent(self):
        audio, meta = _load_fixture("left_eye_sixteen.wav")
        result = self.model.transcribe(
            audio,
            filename="left_eye_sixteen.wav",
            content_type=meta["content_type"],
        )
        self.assertEqual(result["text"], meta["text"])
        self.assertEqual(self.calls[0]["file"][1], audio)

    def test_whisper_and_gpt_transcribe_are_classified_as_transcription(self):
        parsed = self.provider._openai_parse_model(SimpleNamespace(
            id="gpt-4o-mini-transcribe",
            model_dump=lambda: {"id": "gpt-4o-mini-transcribe"},
        ))
        self.assertEqual(parsed["details"]["capabilities"], ["transcription"])
        self.assertEqual(
            self.provider._determine_model_use("whisper-1", ["chat"]),
            "transcription",
        )
        vision = self.provider._openai_parse_model(SimpleNamespace(
            id="gpt-4o",
            model_dump=lambda: {"id": "gpt-4o"},
        ))
        self.assertIn("multimodal", vision["details"]["capabilities"])
        self.assertNotEqual(
            self.provider._determine_model_use("gpt-4o", ["chat", "multimodal"]),
            "transcription",
        )

    def test_unsupported_format_is_rejected_before_the_sdk(self):
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(b"not-audio", filename="note.txt")
        self.assertEqual(error.exception.code, "unsupported_format")
        self.assertFalse(self.calls)

    def test_authentication_error_is_normalized(self):
        from odoo.addons.llm_openai.models import openai_provider as openai_mod

        class FakeAuthError(Exception):
            pass

        self.patch(openai_mod, "AuthenticationError", FakeAuthError)

        def boom(**kwargs):
            self.calls.append(kwargs)
            raise FakeAuthError("invalid api key sk-live-secret")

        self.client.audio.transcriptions.create = boom
        audio, _meta = _load_fixture("right_eye_eighteen.wav")
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(audio, filename="right_eye_eighteen.wav")
        self.assertEqual(error.exception.code, "authentication")
        self.assertNotIn("sk-live-secret", str(error.exception))

    def test_provider_failure_does_not_leak_transcript(self):
        def boom(**kwargs):
            raise RuntimeError("transcript=The right eye is eighteen. key=sk-live")

        self.client.audio.transcriptions.create = boom
        audio, meta = _load_fixture("right_eye_eighteen.wav")
        with self.assertRaises(LLMTranscriptionError) as error:
            self.model.transcribe(audio, filename="right_eye_eighteen.wav")
        self.assertEqual(error.exception.code, "provider_failure")
        self.assertNotIn(meta["text"], str(error.exception))
        self.assertNotIn("sk-live", str(error.exception))
