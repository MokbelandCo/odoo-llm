"""Prove image/PDF/audio payloads reach OpenAI adapters (MH-1329)."""

import base64

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm.models.llm_media_capabilities import (
    LLM_MEDIA_ROLE_ANALYZE,
    LLM_MEDIA_ROLE_DICTATION,
)
from odoo.addons.llm_openai.utils.openai_capabilities import CHAT_COMPLETIONS
from odoo.addons.llm_openai.utils.openai_responses import OpenAIChatError, messages_to_input

TINY_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
    b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()

TINY_PDF = base64.b64encode(
    b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
).decode()

TINY_WAV = base64.b64encode(
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
).decode()

FAKE_KEY = "sk-test-not-a-real-key-000000"


@tagged("post_install", "-at_install")
class TestOpenAIMediaFormatting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "openai" not in dict(cls.env["llm.provider"]._selection_service()):
            raise cls.failureException("OpenAI provider is not available")
        cls.provider = cls.env["llm.provider"].create(
            {
                "name": "OpenAI Media Format",
                "service": "openai",
                "api_key": FAKE_KEY,
            }
        )
        cls.vision = cls.env["llm.model"].create(
            {
                "name": "gpt-4o",
                "provider_id": cls.provider.id,
                "model_use": "multimodal",
            }
        )
        cls.audio = cls.env["llm.model"].create(
            {
                "name": "gpt-4o-audio-preview",
                "provider_id": cls.provider.id,
                "model_use": "chat",
                "openai_endpoint": "chat_completions",
            }
        )

    def _user_message(self, body, attachments):
        return self.env["mail.message"].create(
            {
                "body": body,
                "attachment_ids": [(6, 0, [att.id for att in attachments])],
                "model": "res.partner",
                "res_id": self.env.user.partner_id.id,
                "subtype_id": self.env.ref("llm.mt_user").id,
            }
        )

    def test_image_and_pdf_parts_for_chat_completions(self):
        image = self.env["ir.attachment"].create(
            {
                "name": "pixel.png",
                "datas": TINY_PNG,
                "mimetype": "image/png",
            }
        )
        pdf = self.env["ir.attachment"].create(
            {
                "name": "doc.pdf",
                "datas": TINY_PDF,
                "mimetype": "application/pdf",
            }
        )
        message = self._user_message("look", [image, pdf])
        formatted = message.openai_format_message(is_multimodal=True, is_audio_model=False)
        self.assertEqual(formatted["role"], "user")
        types = [part["type"] for part in formatted["content"]]
        self.assertIn("text", types)
        self.assertIn("image_url", types)
        self.assertIn("file", types)
        image_part = next(part for part in formatted["content"] if part["type"] == "image_url")
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_responses_conversion_keeps_image_and_pdf_rejects_audio(self):
        cc_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{TINY_PNG}"},
                },
                {
                    "type": "file",
                    "file": {
                        "filename": "doc.pdf",
                        "file_data": f"data:application/pdf;base64,{TINY_PDF}",
                    },
                },
            ],
        }
        converted = messages_to_input([cc_message], "gpt-4o")
        part_types = [part["type"] for item in converted for part in item.get("content", [])]
        self.assertIn("input_text", part_types)
        self.assertIn("input_image", part_types)
        self.assertIn("input_file", part_types)

        with self.assertRaises(OpenAIChatError) as ctx:
            messages_to_input(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": TINY_WAV, "format": "wav"},
                            }
                        ],
                    }
                ],
                "gpt-4o",
            )
        self.assertEqual(ctx.exception.details.get("code"), "unsupported_attachment")
        self.assertIn("audio", str(ctx.exception).lower())

    def test_analyze_audio_builds_input_audio_dictation_skipped(self):
        analyze = self.env["ir.attachment"].create(
            {
                "name": "analyze.wav",
                "datas": TINY_WAV,
                "mimetype": "audio/wav",
                "description": LLM_MEDIA_ROLE_ANALYZE,
            }
        )
        dictation = self.env["ir.attachment"].create(
            {
                "name": "dictation.wav",
                "datas": TINY_WAV,
                "mimetype": "audio/wav",
                "description": LLM_MEDIA_ROLE_DICTATION,
            }
        )
        message = self._user_message("tone?", [analyze, dictation])
        formatted = message.openai_format_message(
            is_multimodal=False, is_audio_model=True
        )
        audio_parts = [
            part for part in formatted["content"] if part["type"] == "input_audio"
        ]
        self.assertEqual(len(audio_parts), 1)
        self.assertEqual(audio_parts[0]["input_audio"]["format"], "wav")

    def test_audio_model_media_capabilities_prefer_chat_completions(self):
        caps = self.audio.get_media_capabilities()
        self.assertTrue(caps["direct_audio_chat"])
        self.assertEqual(caps["chat_endpoint"], CHAT_COMPLETIONS)
        self.assertFalse(caps["image"])

        vision_caps = self.vision.get_media_capabilities()
        self.assertTrue(vision_caps["image"])
        self.assertTrue(vision_caps["pdf"])
        self.assertFalse(vision_caps["direct_audio_chat"])
