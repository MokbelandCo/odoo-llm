from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "llm_openai")
class TestOpenAIModelFormTour(HttpCase):
    """The OpenAI section of the model form shows capabilities and saves settings."""

    def test_openai_model_form_tour(self):
        provider = self.env["llm.provider"].create({
            "name": "OpenAI Form Tour",
            "service": "openai",
            "api_key": "sk-test-not-used",
        })
        model = self.env["llm.model"].create({
            "name": "gpt-5-mini",
            "provider_id": provider.id,
            "model_use": "multimodal",
        })
        self.start_tour(
            "/web#model=llm.model&view_type=form&id=%d" % model.id,
            "llm_openai_model_form_tour",
            login="admin",
        )
        model.invalidate_recordset()
        self.assertEqual(model.openai_reasoning_effort, "low")
        self.assertEqual(model.openai_max_output_tokens, 1500)
