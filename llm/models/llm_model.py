from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    # Batch vs live STT are distinct contracts. ``model_use=transcription``
    # never implies live/realtime support by itself.
    supports_batch_transcription = fields.Boolean(
        string="Batch / File Transcription",
        compute="_compute_transcription_modes",
        store=True,
        readonly=False,
        help="Complete independently decodable audio file → final transcript.",
    )
    supports_live_transcription = fields.Boolean(
        string="Live / Realtime Transcription",
        compute="_compute_transcription_modes",
        store=True,
        readonly=False,
        help="Persistent realtime session with partial and final events.",
    )
    supports_live_webrtc = fields.Boolean(
        string="Live WebRTC Transport",
        compute="_compute_transcription_modes",
        store=True,
        readonly=False,
        help="Browser clients can stream live audio to this model over WebRTC.",
    )
    supports_live_websocket = fields.Boolean(
        string="Live WebSocket Transport",
        compute="_compute_transcription_modes",
        store=True,
        readonly=False,
        help="IoT/local agents can stream live audio to this model over WebSocket.",
    )

    @api.depends("model_use", "name", "provider_id", "provider_id.service")
    def _compute_transcription_modes(self):
        for model in self:
            modes = model.provider_id._transcription_modes_for_model(model) if model.provider_id else {
                "batch": False,
                "live": False,
                "webrtc": False,
                "websocket": False,
            }
            if model.model_use != "transcription":
                model.supports_batch_transcription = False
                model.supports_live_transcription = False
                model.supports_live_webrtc = False
                model.supports_live_websocket = False
            else:
                live = bool(modes.get("live"))
                model.supports_batch_transcription = bool(modes.get("batch"))
                model.supports_live_transcription = live
                model.supports_live_webrtc = bool(modes["webrtc"]) if "webrtc" in modes else live
                model.supports_live_websocket = (
                    bool(modes["websocket"]) if "websocket" in modes else live
                )

    @api.constrains(
        "supports_batch_transcription",
        "supports_live_transcription",
        "model_use",
        "name",
        "provider_id",
    )
    def _check_transcription_modes(self):
        """Administrators may disable a mode; they cannot enable one the provider rejects."""
        for model in self:
            if model.model_use != "transcription" or not model.provider_id:
                continue
            method = getattr(
                model.provider_id,
                "%s_transcription_modes" % model.provider_id.service,
                None,
            )
            if not method:
                continue
            modes = method(model) or {}
            if model.supports_live_transcription and not modes.get("live"):
                raise ValidationError(
                    _("Model %s is not accepted for live/realtime transcription.")
                    % model.name
                )
            if model.supports_batch_transcription and not modes.get("batch"):
                raise ValidationError(
                    _("Model %s is not accepted for batch/file transcription.")
                    % model.name
                )

    def _validate_transcription_mode(self, live=False):
        self.ensure_one()
        from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

        if self.model_use != "transcription":
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s is not a speech-to-text model." % self.name,
            )
        if live and not self.supports_live_transcription:
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support live transcription." % self.name,
            )
        if not live and not self.supports_batch_transcription:
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support batch transcription." % self.name,
            )

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
        self._validate_transcription_mode(live=False)
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

    def transcribe_live_open(self, **kwargs):
        """Open a provider-neutral live transcription session."""
        self.ensure_one()
        self._validate_transcription_mode(live=True)
        return self.provider_id.transcribe_live_open(model=self, **kwargs)

    def transcribe_live_append(self, handle, audio, **kwargs):
        """Append audio frames to an open live transcription session."""
        self.ensure_one()
        self._validate_transcription_mode(live=True)
        return self.provider_id.transcribe_live_append(
            handle, audio, model=self, **kwargs
        )

    def transcribe_live_commit(self, handle, **kwargs):
        """Finalize the current utterance and return normalized events."""
        self.ensure_one()
        self._validate_transcription_mode(live=True)
        return self.provider_id.transcribe_live_commit(handle, model=self, **kwargs)

    def transcribe_live_events(self, handle, **kwargs):
        """Drain pending partial/final events without closing the session."""
        self.ensure_one()
        self._validate_transcription_mode(live=True)
        return self.provider_id.transcribe_live_events(handle, model=self, **kwargs)

    def transcribe_live_close(self, handle, **kwargs):
        """Close a live transcription session and release provider resources."""
        self.ensure_one()
        return self.provider_id.transcribe_live_close(handle, model=self, **kwargs)

    def transcribe_live_credentials(self, transport="webrtc", **kwargs):
        """Issue short-lived client credentials for a direct provider live session.

        The capture source connects to the provider. Odoo never receives the
        live audio and never returns the long-lived provider API key.
        """
        self.ensure_one()
        self._validate_transcription_mode(live=True)
        if transport == "webrtc" and not self.supports_live_webrtc:
            from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support WebRTC live transcription." % self.name,
            )
        if transport == "websocket" and not self.supports_live_websocket:
            from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support WebSocket live transcription." % self.name,
            )
        return self.provider_id.transcribe_live_credentials(
            model=self, transport=transport, **kwargs
        )

    def supports_live_transport(self, transport):
        """True when this STT model can drive ``transport`` (webrtc/websocket)."""
        self.ensure_one()
        if not self.supports_live_transcription:
            return False
        if transport == "webrtc":
            return bool(self.supports_live_webrtc)
        if transport == "websocket":
            return bool(self.supports_live_websocket)
        return False

    def action_open_fetch_this_model_wizard(self):
        self.ensure_one()
        # Call the provider's action_fetch_models with context for specific model
        return self.provider_id.with_context(
            default_model_to_fetch=self.name
        ).action_fetch_models()
