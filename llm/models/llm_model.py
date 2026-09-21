from odoo import api, fields, models


class LLMModel(models.Model):
    _name = "llm.model"
    _description = "LLM Model"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True)
    provider_id = fields.Many2one("llm.provider", required=True, ondelete="cascade")
    publisher_id = fields.Many2one(
        "llm.publisher",
        string="Publisher",
        ondelete="restrict",
        tracking=True,
        help="The organization or entity that published this model",
    )

    model_use = fields.Selection(
        selection="_get_available_model_usages",
        required=True,
        default="chat",
    )
    default = fields.Boolean(default=False)
    active = fields.Boolean(default=True)

    # Model details
    details = fields.Json()
    model_info = fields.Json()
    parameters = fields.Text()
    template = fields.Text()

    @api.model
    def _get_available_model_usages(self):
        return [
            ("embedding", "Embedding"),
            ("completion", "Completion"),
            ("chat", "Chat"),
            ("multimodal", "Multimodal"),
            ("generation", "Generic binary generation"),
            ("image_generation", "Image Generation"),
            ("transcription", "Speech to Text"),
        ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.default:
                # Ensure only one default per provider/use combo
                self.search(
                    [
                        ("provider_id", "=", record.provider_id.id),
                        ("model_use", "=", record.model_use),
                        ("default", "=", True),
                        ("id", "!=", record.id),
                    ]
                ).write({"default": False})
        return records

    def chat(self, messages, stream=False, **kwargs):
        """Send chat messages using this model"""
        return self.provider_id.chat(messages, model=self, stream=stream, **kwargs)

    def embedding(self, texts):
        """Generate embeddings using this model"""
        return self.provider_id.embedding(texts, model=self)

    def generate(self, input_data, stream=False, **kwargs):
        """Generate content using this model

        Args:
            input_data: Input data for generation (could be text, prompt, or structured data)
            stream: Whether to stream the response
            **kwargs: Additional provider-specific parameters

        Returns:
            Generated content from the provider
        """
        return self.provider_id.generate(
            input_data, model=self, stream=stream, **kwargs
        )

    def transcribe(
        self,
        audio,
        filename=None,
        content_type=None,
        language=None,
        prompt=None,
        stream=False,
        timestamps=True,
        diarization=False,
        **kwargs,
    ):
        """Transcribe in-memory audio using this speech-to-text model.

        Args:
            audio: Audio bytes or a seekable byte stream. Storage references
                such as media keys must never be passed here.
            filename: Optional file name hint for the provider.
            content_type: Optional MIME type of the audio payload.
            language: Optional BCP-47 / ISO-639 language hint.
            prompt: Optional vocabulary / domain hint.
            stream: Request streaming when the provider supports it.
            timestamps: Request timestamped segments when available.
            diarization: Request speaker labels when the model supports them.
            **kwargs: Additional provider-specific parameters.

        Returns:
            dict: Normalized transcription result (text, language, duration_ms,
            timestamped segments, provider_request_id, model_version).
        """
        self.ensure_one()
        return self.provider_id.transcribe(
            audio,
            model=self,
            filename=filename,
            content_type=content_type,
            language=language,
            prompt=prompt,
            stream=stream,
            timestamps=timestamps,
            diarization=diarization,
            **kwargs,
        )

    def action_open_fetch_this_model_wizard(self):
        self.ensure_one()
        # Call the provider's action_fetch_models with context for specific model
        return self.provider_id.with_context(
            default_model_to_fetch=self.name
        ).action_fetch_models()
