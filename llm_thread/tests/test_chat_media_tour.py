from unittest.mock import patch

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestChatMediaTour(HttpCase):
    """Full-screen and popup composer media affordances (MH-1329)."""

    def _ensure_chat_setup(self):
        admin = self.env.ref("base.user_admin")
        env = self.env(user=admin)
        services = dict(env["llm.provider"]._selection_service())
        chat_service = "openai" if "openai" in services else next(iter(services))
        provider = env["llm.provider"].create(
            {
                "name": "Chat Media Tour Provider",
                "service": chat_service,
                "api_key": "sk-test-not-a-real-key-000000" if chat_service == "openai" else False,
            }
        )
        model = env["llm.model"].create(
            {
                "name": "chat-media-tour-model",
                "provider_id": provider.id,
                "model_use": "multimodal",
                "default": True,
            }
        )
        if chat_service == "openai":
            env["llm.model"].create(
                {
                    "name": "gpt-transcribe",
                    "provider_id": provider.id,
                    "model_use": "transcription",
                }
            )
        env["llm.thread"].search([("user_id", "=", admin.id)]).unlink()
        env["llm.thread"].create(
            {
                "name": "Media Tour Thread",
                "provider_id": provider.id,
                "model_id": model.id,
                "user_id": admin.id,
            }
        )
        return env

    def test_chat_media_fullscreen_tour(self):
        self._ensure_chat_setup()

        def fake_transcribe(self_model, audio, **kwargs):
            return {
                "text": "dictated tour text",
                "language": "en",
                "provider_request_id": "tour_req",
            }

        with patch.object(type(self.env["llm.model"]), "transcribe", fake_transcribe):
            self.start_tour("/web", "llm_thread_chat_media_tour", login="admin")

    def test_chat_media_popup_tour(self):
        self._ensure_chat_setup()
        self.start_tour("/web", "llm_thread_chat_media_popup_tour", login="admin")
