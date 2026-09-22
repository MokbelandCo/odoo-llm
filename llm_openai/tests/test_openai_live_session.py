"""mh-767: OpenAI realtime transcription session creation and live model validation.

``POST /realtime/client_secrets`` is mocked at the HTTP layer so every provider
answer shape is exercised: acceptance, capability refusal for a model, bad key,
throttling, outage, and a secret bound to the wrong session type. The last class
is opt-in and talks to the real API.
"""

import json
import os

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self._text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _error_body(code, message, param=None, error_type="invalid_request_error"):
    return {"error": {"type": error_type, "code": code, "param": param, "message": message}}


@tagged("post_install", "-at_install", "llm_openai")
class TestOpenAILiveSession(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env["llm.provider"].create({
            "name": "OpenAI Live Test",
            "service": "openai",
            "api_key": "sk-test-never-returned",
        })
        self.posted = []
        self.responses = []

        def fake_post(url, json=None, headers=None, timeout=None):
            self.posted.append({"url": url, "json": json, "headers": headers})
            if not self.responses:
                raise AssertionError("unexpected POST %s" % url)
            answer = self.responses.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        import requests

        self.patch(requests, "post", fake_post)

    def _expect_error(self, call):
        """Run ``call`` and return the ``LLMTranscriptionError`` it raised.

        Odoo's ``assertRaises`` wraps the block in a savepoint that is rolled
        back on exception, which would undo the verification the model records
        *while* refusing. Production code has no such savepoint.
        """
        try:
            call()
        except LLMTranscriptionError as error:
            return error
        self.fail("LLMTranscriptionError was not raised")

    def _model(self, name):
        return self.env["llm.model"].create({
            "name": name,
            "provider_id": self.provider.id,
            "model_use": "transcription",
        })

    def _accepted(self, model_name, session_type="transcription"):
        return FakeResponse(200, {
            "value": "ek_live_secret",
            "expires_at": 1893456000,
            "session": {
                "id": "sess_ok",
                "object": "realtime.transcription_session",
                "type": session_type,
                "audio": {"input": {"transcription": {"model": model_name}}},
            },
        })

    # ------------------------------------------------------------------
    # Static matrix
    # ------------------------------------------------------------------
    def test_diarize_model_is_batch_only(self):
        diarize = self._model("gpt-4o-transcribe-diarize")
        self.assertTrue(diarize.supports_batch_transcription)
        self.assertFalse(diarize.supports_live_transcription,
                         "the realtime API refuses the diarize model; it must not be planned live")
        self.assertFalse(diarize.supports_live_webrtc)
        caught = self._expect_error(lambda: diarize.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "unsupported_capability")
        self.assertFalse(self.posted, "a model outside the live matrix never reaches the provider")

    def test_every_live_model_in_the_matrix_is_verified_against_the_contract(self):
        names = sorted(type(self.provider).OPENAI_LIVE_TRANSCRIBE_MODELS)
        self.assertNotIn("gpt-4o-transcribe-diarize", names)
        self.assertNotIn("whisper-1", names)
        for name in names:
            model = self._model(name)
            self.assertTrue(model.supports_live_transcription, name)
            self.responses.append(self._accepted(name))
            self.responses.append(self._accepted(name))
            model.action_verify_live_transcription()
            self.assertEqual(model.live_verification_state, "verified", name)
            self.assertEqual(model.live_verification_transports, "webrtc,websocket", name)
            body = self.posted[-1]["json"]["session"]
            self.assertEqual(body["type"], "transcription")
            self.assertEqual(body["audio"]["input"]["transcription"]["model"], name)
            self.assertEqual(body["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})

    # ------------------------------------------------------------------
    # Provider answers
    # ------------------------------------------------------------------
    def test_accepted_session_records_verification_and_returns_no_api_key(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(self._accepted("gpt-4o-transcribe"))
        creds = model.transcribe_live_credentials(transport="webrtc", language="en_US")
        self.assertEqual(creds["token"], "ek_live_secret")
        self.assertNotIn("sk-test-never-returned", json.dumps(creds))
        self.assertEqual(model.live_verification_state, "verified")
        self.assertEqual(model.live_verification_transports, "webrtc")
        self.assertEqual(model.live_verification_details["outcome"], "accepted")
        self.assertEqual(self.posted[0]["headers"]["Authorization"], "Bearer sk-test-never-returned")

    def test_model_refusal_rejects_the_model_with_structured_telemetry(self):
        model = self._model("gpt-4o-mini-transcribe")
        self.responses.append(FakeResponse(
            400,
            _error_body(
                "invalid_value",
                "Invalid value: 'gpt-4o-mini-transcribe'. Supported values are: ... sk-should-be-redacted-abcdef",
                param="session.audio.input.transcription.model",
            ),
            headers={"x-request-id": "req_400"},
        ))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        error = caught
        self.assertEqual(error.code, "unsupported_capability")
        self.assertFalse(error.retryable)
        self.assertEqual(error.details["http_status"], 400)
        self.assertEqual(error.details["provider_error_code"], "invalid_value")
        self.assertEqual(error.details["provider_error_param"], "session.audio.input.transcription.model")
        self.assertEqual(error.details["request_id"], "req_400")
        self.assertEqual(error.details["retryable_class"], "permanent")
        self.assertIn("[redacted]", error.details["provider_message"])
        self.assertNotIn("sk-should-be-redacted", json.dumps(error.to_dict()))
        self.assertEqual(model.live_verification_state, "rejected")
        self.assertFalse(model.supports_live_transport("webrtc"),
                         "a rejected model must not be offered for live planning")
        self.assertFalse(model.supports_live_transport("websocket"))
        # The rejection is sticky: no second provider round trip.
        again = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(again.details["provider_error_code"], "model_rejected")
        self.assertEqual(len(self.posted), 1)

    def test_authentication_failure_keeps_the_model_unverified(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(
            401, _error_body("invalid_api_key", "Incorrect API key provided: sk-live-abcdefgh1234"),
        ))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "authentication")
        self.assertEqual(caught.details["http_status"], 401)
        self.assertNotIn("sk-live-abcdefgh1234", json.dumps(caught.details))
        self.assertEqual(model.live_verification_state, "unverified",
                         "a bad key says nothing about the model")
        self.assertTrue(model.supports_live_transport("webrtc"))
        self.assertEqual(model.live_verification_details["code"], "authentication")

    def test_a_key_the_provider_already_masked_is_still_redacted(self):
        # OpenAI echoes the key back as ``sk-smok****...agao``. The prefix and
        # suffix it keeps are fragments of the real key and must not be stored.
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(
            401, _error_body(
                "invalid_api_key",
                "Incorrect API key provided: sk-smok**************************************agao. "
                "You can find your API key at https://platform.openai.com/account/api-keys.",
            ),
        ))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        dumped = json.dumps(caught.to_dict()) + json.dumps(model.live_verification_details)
        self.assertNotIn("sk-smok", dumped)
        self.assertNotIn("agao", dumped)
        self.assertIn("Incorrect API key provided: [redacted]", caught.details["provider_message"])

    def test_throttling_is_retryable_and_carries_retry_after(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(
            429, _error_body("rate_limit_exceeded", "Rate limit reached", error_type="requests"),
            headers={"Retry-After": "3"},
        ))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="websocket"))
        self.assertEqual(caught.code, "throttling")
        self.assertTrue(caught.retryable)
        self.assertEqual(caught.retry_after_ms, 3000)
        self.assertEqual(caught.details["retryable_class"], "transient")
        self.assertEqual(model.live_verification_state, "unverified")

    def test_outage_is_a_retryable_provider_failure(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(503, None, text="<html>bad gateway</html>"))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "provider_failure")
        self.assertTrue(caught.retryable)
        self.assertEqual(caught.details["http_status"], 503)
        self.assertEqual(model.live_verification_state, "unverified")

    def test_network_failure_is_a_retryable_provider_failure(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(ConnectionError("dns failure"))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "provider_failure")
        self.assertTrue(caught.retryable)
        self.assertEqual(caught.details["provider_error_type"], "ConnectionError")

    def test_other_bad_request_is_permanent_but_does_not_reject_the_model(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(
            400, _error_body("invalid_value", "Invalid value for expires_after.seconds",
                             param="expires_after.seconds"),
        ))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "provider_failure")
        self.assertFalse(caught.retryable)
        self.assertEqual(model.live_verification_state, "unverified")

    def test_secret_bound_to_a_conversation_session_is_refused(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(self._accepted("gpt-4o-transcribe", session_type="realtime"))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "unsupported_capability")
        self.assertEqual(caught.details["provider_error_code"], "session_type_mismatch")
        self.assertEqual(model.live_verification_state, "rejected")

    def test_missing_secret_is_a_provider_failure(self):
        model = self._model("gpt-4o-transcribe")
        self.responses.append(FakeResponse(200, {"expires_at": 1, "session": {"id": "x"}}))
        caught = self._expect_error(lambda: model.transcribe_live_credentials(transport="webrtc"))
        self.assertEqual(caught.code, "provider_failure")
        self.assertEqual(caught.details["provider_error_code"], "missing_client_secret")

    # ------------------------------------------------------------------
    # Verification action
    # ------------------------------------------------------------------
    def test_verify_action_marks_rejected_models_and_re_verifies_after_change(self):
        good = self._model("gpt-4o-transcribe")
        bad = self._model("gpt-live-transcribe")
        self.responses.extend([
            self._accepted("gpt-4o-transcribe"),
            self._accepted("gpt-4o-transcribe"),
            FakeResponse(400, _error_body(
                "model_not_found", "The model does not exist",
                param="session.audio.input.transcription.model")),
        ])
        action = (good | bad).action_verify_live_transcription()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("gpt-4o-transcribe", action["params"]["message"])
        self.assertIn("gpt-live-transcribe", action["params"]["message"])
        self.assertEqual(good.live_verification_state, "verified")
        self.assertEqual(bad.live_verification_state, "rejected")
        self.assertEqual(action["params"]["next"]["tag"], "soft_reload")
        # Renaming the record is a different contract: verification is reset.
        bad.name = "gpt-4o-mini-transcribe"
        self.assertEqual(bad.live_verification_state, "unverified")
        self.assertTrue(bad.supports_live_transport("webrtc"))
        # Re-verifying a previously rejected model that the provider now accepts.
        self.responses.extend([
            self._accepted("gpt-4o-mini-transcribe"),
            self._accepted("gpt-4o-mini-transcribe"),
        ])
        bad.action_verify_live_transcription()
        self.assertEqual(bad.live_verification_state, "verified")

    def test_verify_action_skips_batch_only_models(self):
        whisper = self._model("whisper-1")
        action = whisper.action_verify_live_transcription()
        self.assertFalse(self.posted)
        self.assertIn("No live-capable", action["params"]["message"])


@tagged("post_install", "-at_install", "llm_openai_live_integration")
class TestOpenAILiveSessionIntegration(TransactionCase):
    """Real ``/realtime/client_secrets`` round trips. Opt-in.

    Run with ``OPENAI_API_KEY`` set and ``LLM_OPENAI_LIVE_INTEGRATION=1``::

        odoo --test-enable --test-tags llm_openai_live_integration ...

    A session credential is created per (model, transport) and discarded; no
    audio is streamed and the ``ek_`` value never leaves the test process.
    """

    def setUp(self):
        super().setUp()
        if os.environ.get("LLM_OPENAI_LIVE_INTEGRATION") != "1" or not os.environ.get("OPENAI_API_KEY"):
            self.skipTest("set LLM_OPENAI_LIVE_INTEGRATION=1 and OPENAI_API_KEY to run")
        self.provider = self.env["llm.provider"].create({
            "name": "OpenAI Live Integration",
            "service": "openai",
            "api_key": os.environ["OPENAI_API_KEY"],
        })

    def test_every_configured_live_model_opens_a_transcription_session(self):
        outcomes = {}
        for name in sorted(type(self.provider).OPENAI_LIVE_TRANSCRIBE_MODELS):
            model = self.env["llm.model"].create({
                "name": name,
                "provider_id": self.provider.id,
                "model_use": "transcription",
            })
            model.action_verify_live_transcription()
            outcomes[name] = (model.live_verification_state, model.live_verification_details)
        for name, (state, details) in outcomes.items():
            self.assertIn(state, ("verified", "rejected"), (name, details))
            self.assertNotIn("ek_", json.dumps(details))
        self.assertTrue(
            any(state == "verified" for state, _details in outcomes.values()),
            "at least one live transcription model must be accepted: %s" % outcomes,
        )

    def test_diarize_model_is_refused_by_the_provider(self):
        diarize = self.env["llm.model"].create({
            "name": "gpt-4o-transcribe-diarize",
            "provider_id": self.provider.id,
            "model_use": "transcription",
        })
        with self.assertRaises(LLMTranscriptionError) as caught:
            self.provider.transcribe_live_credentials(model=diarize, transport="webrtc")
        self.assertEqual(caught.exception.code, "unsupported_capability")
