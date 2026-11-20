"""Media capability contract and unsupported-attachment gating (MH-1329)."""

import base64

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_media_capabilities import (
    LLM_MEDIA_ROLE_ANALYZE,
    LLM_MEDIA_ROLE_DICTATION,
    mark_attachment_media_role,
)


TINY_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
    b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@tagged("post_install", "-at_install")
class TestMediaCapabilities(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        services = cls.env["llm.provider"]._selection_service()
        if not services:
            raise cls.failureException("No LLM provider service is available")
        cls.provider = cls.env["llm.provider"].create(
            {"name": "Media Caps Provider", "service": services[0][0]}
        )
        cls.chat_model = cls.env["llm.model"].create(
            {
                "name": "media-chat",
                "provider_id": cls.provider.id,
                "model_use": "chat",
            }
        )
        cls.multi_model = cls.env["llm.model"].create(
            {
                "name": "media-multi",
                "provider_id": cls.provider.id,
                "model_use": "multimodal",
            }
        )

    def test_default_media_capabilities_follow_model_use(self):
        chat = self.chat_model.get_media_capabilities()
        self.assertFalse(chat["image"])
        self.assertFalse(chat["pdf"])
        self.assertTrue(chat["text_file"])
        self.assertFalse(chat["direct_audio_chat"])
        self.assertFalse(chat["realtime_voice"])

        multi = self.multi_model.get_media_capabilities()
        self.assertTrue(multi["image"])
        self.assertTrue(multi["pdf"])

    def test_mark_attachment_media_role_is_exclusive(self):
        description = mark_attachment_media_role("note", "dictation")
        self.assertIn(LLM_MEDIA_ROLE_DICTATION, description)
        switched = mark_attachment_media_role(description, "analyze")
        self.assertIn(LLM_MEDIA_ROLE_ANALYZE, switched)
        self.assertNotIn(LLM_MEDIA_ROLE_DICTATION, switched)

    def test_unsupported_attachments_use_fine_grained_capabilities(self):
        attachment = self.env["ir.attachment"].create(
            {
                "name": "pixel.png",
                "datas": TINY_PNG,
                "mimetype": "image/png",
            }
        )
        message = self.env["mail.message"].create(
            {
                "body": "see image",
                "attachment_ids": [(4, attachment.id)],
                "model": "res.partner",
                "res_id": self.env.user.partner_id.id,
            }
        )
        unsupported = message._get_unsupported_attachments(
            provider_service="openai",
            media_capabilities={
                "image": False,
                "pdf": False,
                "text_file": True,
                "direct_audio_chat": False,
            },
        )
        self.assertEqual(len(unsupported), 1)
        self.assertIn("images", unsupported[0]["reason"].lower())

        supported = message._get_unsupported_attachments(
            provider_service="openai",
            media_capabilities={
                "image": True,
                "pdf": True,
                "text_file": True,
                "direct_audio_chat": False,
            },
        )
        self.assertFalse(supported)

    def test_dictation_audio_is_not_unsupported(self):
        # Minimal WAV header (empty data chunk) — format detection only.
        wav = base64.b64encode(
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
            b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "note.wav",
                "datas": wav,
                "mimetype": "audio/wav",
                "description": LLM_MEDIA_ROLE_DICTATION,
            }
        )
        message = self.env["mail.message"].create(
            {
                "body": "spoken",
                "attachment_ids": [(4, attachment.id)],
                "model": "res.partner",
                "res_id": self.env.user.partner_id.id,
            }
        )
        unsupported = message._get_unsupported_attachments(
            provider_service="anthropic",
            media_capabilities={
                "image": False,
                "pdf": False,
                "text_file": True,
                "direct_audio_chat": False,
                "chat_endpoint": None,
            },
        )
        self.assertFalse(unsupported)

    def test_raw_audio_rejected_for_anthropic_and_responses(self):
        wav = base64.b64encode(
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
            b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "raw.wav",
                "datas": wav,
                "mimetype": "audio/wav",
                "description": LLM_MEDIA_ROLE_ANALYZE,
            }
        )
        message = self.env["mail.message"].create(
            {
                "body": "",
                "attachment_ids": [(4, attachment.id)],
                "model": "res.partner",
                "res_id": self.env.user.partner_id.id,
            }
        )
        anthropic = message._get_unsupported_attachments(
            provider_service="anthropic",
            media_capabilities={
                "direct_audio_chat": False,
                "chat_endpoint": None,
            },
        )
        self.assertTrue(anthropic)
        self.assertIn("dictation", anthropic[0]["reason"].lower())

        responses = message._get_unsupported_attachments(
            provider_service="openai",
            media_capabilities={
                "direct_audio_chat": False,
                "chat_endpoint": "responses",
            },
        )
        self.assertTrue(responses)
        self.assertIn("responses", responses[0]["reason"].lower())
