"""Chat attachment ownership, size and context validation (MH-1329)."""

import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.llm.models.llm_media_capabilities import LLM_MEDIA_ROLE_DICTATION

TINY_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
    b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

TINY_WAV = base64.b64encode(
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


@tagged("post_install", "-at_install")
class TestChatAttachments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        services = cls.env["llm.provider"]._selection_service()
        if not services:
            raise cls.failureException("No LLM provider service is available")
        cls.provider = cls.env["llm.provider"].create(
            {"name": "Chat Attach Provider", "service": services[0][0]}
        )
        cls.chat_model = cls.env["llm.model"].create(
            {
                "name": "attach-chat",
                "provider_id": cls.provider.id,
                "model_use": "chat",
            }
        )
        cls.multi_model = cls.env["llm.model"].create(
            {
                "name": "attach-multi",
                "provider_id": cls.provider.id,
                "model_use": "multimodal",
            }
        )
        # Prefer an OpenAI provider so gpt-transcribe is accepted by the
        # provider transcription-mode matrix; fall back to patching modes.
        openai_services = [
            service for service, _label in services if service == "openai"
        ]
        if openai_services:
            cls.stt_provider = cls.env["llm.provider"].create(
                {
                    "name": "Chat Attach STT Provider",
                    "service": "openai",
                    "api_key": "sk-test-not-a-real-key-000000",
                }
            )
        else:
            cls.stt_provider = cls.provider
        cls.stt_model = cls.env["llm.model"].create(
            {
                "name": "gpt-transcribe",
                "provider_id": cls.stt_provider.id,
                "model_use": "transcription",
            }
        )
        if not cls.stt_model.supports_batch_transcription:
            # Non-OpenAI providers may not advertise STT modes; force the flag
            # for dictation selection tests only.
            cls.stt_model.with_context(tracking_disable=True).write(
                {"supports_batch_transcription": True}
            )
        cls.user = new_test_user(
            cls.env, login="llm_attach_user", groups="base.group_user"
        )
        cls.other = new_test_user(
            cls.env, login="llm_attach_other", groups="base.group_user"
        )

    def _thread(self, env=None, model=None):
        env = env or self.env
        return env["llm.thread"].create(
            {
                "name": "Attachment thread",
                "provider_id": self.provider.id,
                "model_id": (model or self.multi_model).id,
            }
        )

    def test_validate_rejects_foreign_attachment(self):
        thread = self._thread(env=self.env(user=self.user))
        foreign = self.env(user=self.other)["ir.attachment"].create(
            {
                "name": "secret.png",
                "datas": TINY_PNG,
                "mimetype": "image/png",
            }
        )
        with self.assertRaises(UserError):
            thread._validate_chat_attachments([foreign.id])

    def test_validate_accepts_owned_attachment_and_binds_thread(self):
        thread = self._thread(env=self.env(user=self.user))
        attachment = self.env(user=self.user)["ir.attachment"].create(
            {
                "name": "mine.png",
                "datas": TINY_PNG,
                "mimetype": "image/png",
            }
        )
        ids = thread._validate_chat_attachments([attachment.id])
        self.assertEqual(ids, [attachment.id])
        attachment.invalidate_recordset()
        self.assertEqual(attachment.res_model, "llm.thread")
        self.assertEqual(attachment.res_id, thread.id)

    def test_context_check_after_model_switch(self):
        thread = self._thread(model=self.multi_model)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "vision.png",
                "datas": TINY_PNG,
                "mimetype": "image/png",
                "res_model": "llm.thread",
                "res_id": thread.id,
            }
        )
        thread.message_post(
            body="with image",
            llm_role="user",
            attachment_ids=[attachment.id],
        )
        self.assertFalse(thread._check_unsupported_attachments())
        thread.model_id = self.chat_model
        unsupported = thread._check_unsupported_attachments()
        self.assertTrue(unsupported)
        self.assertIn("images", unsupported[0]["reason"].lower())

    def test_composer_media_capabilities_include_batch_stt(self):
        thread = self._thread()
        caps = thread.get_composer_media_capabilities()
        self.assertTrue(caps["batch_stt"])
        self.assertEqual(caps["batch_stt_model_name"], "gpt-transcribe")

    def test_transcribe_voice_note_marks_dictation_and_returns_text(self):
        thread = self._thread(env=self.env(user=self.user))
        attachment = self.env(user=self.user)["ir.attachment"].create(
            {
                "name": "voice.wav",
                "datas": TINY_WAV,
                "mimetype": "audio/wav",
                "res_model": "llm.thread",
                "res_id": thread.id,
            }
        )

        def fake_transcribe(self_model, audio, **kwargs):
            self.assertTrue(audio)
            return {
                "text": "hello from the mic",
                "language": "en",
                "provider_request_id": "req_test",
                "duration_ms": 1200,
            }

        with patch.object(
            type(self.env["llm.model"]), "transcribe", fake_transcribe
        ):
            result = thread.transcribe_voice_note(attachment.id)

        self.assertEqual(result["text"], "hello from the mic")
        self.assertEqual(result["attachment_id"], attachment.id)
        attachment.invalidate_recordset()
        self.assertIn(LLM_MEDIA_ROLE_DICTATION, attachment.description or "")

    def test_mark_analyze_audio_requires_direct_audio_capability(self):
        thread = self._thread(model=self.chat_model)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "tone.wav",
                "datas": TINY_WAV,
                "mimetype": "audio/wav",
            }
        )
        with self.assertRaises(UserError):
            thread.mark_attachment_analyze_audio(attachment.id)
