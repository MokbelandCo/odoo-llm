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
    # The static provider matrix says what a model *should* accept. Whether the
    # provider actually opens a realtime transcription session for it is only
    # known once one was requested. A rejected model is never offered for live
    # planning again until an administrator re-verifies it.
    live_verification_state = fields.Selection(
        [
            ("unverified", "Not Verified"),
            ("verified", "Verified With Provider"),
            ("rejected", "Rejected By Provider"),
        ],
        string="Live Verification",
        default="unverified",
        readonly=True,
        copy=False,
        help="Result of the last real live-transcription session request for this model.",
    )
    live_verification_at = fields.Datetime(string="Live Verified At", readonly=True, copy=False)
    live_verification_transports = fields.Char(
        string="Verified Live Transports",
        readonly=True,
        copy=False,
        help="Comma separated transports the provider accepted (webrtc, websocket).",
    )
    live_verification_details = fields.Json(
        string="Live Verification Details",
        readonly=True,
        copy=False,
        help="Structured, PHI-free telemetry of the last verification (status, provider error code/type/param).",
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
        from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

        self._validate_transcription_mode(live=True)
        if self.live_verification_state == "rejected":
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s was rejected by its provider for live transcription." % self.name,
                details={
                    "operation": "live_credentials",
                    "model": self.name,
                    "transport": transport,
                    "provider_error_code": "model_rejected",
                    "retryable_class": "permanent",
                },
            )
        if transport == "webrtc" and not self.supports_live_webrtc:
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support WebRTC live transcription." % self.name,
                details={
                    "operation": "live_credentials",
                    "model": self.name,
                    "transport": transport,
                    "provider_error_code": "transport_not_supported",
                    "retryable_class": "permanent",
                },
            )
        if transport == "websocket" and not self.supports_live_websocket:
            raise LLMTranscriptionError(
                "unsupported_capability",
                "Model %s does not support WebSocket live transcription." % self.name,
                details={
                    "operation": "live_credentials",
                    "model": self.name,
                    "transport": transport,
                    "provider_error_code": "transport_not_supported",
                    "retryable_class": "permanent",
                },
            )
        try:
            result = self.provider_id.transcribe_live_credentials(
                model=self, transport=transport, **kwargs
            )
        except LLMTranscriptionError as error:
            # Every real session request is a verification of the model: a
            # capability refusal takes it out of live planning right away.
            self.live_verification_record_failure(error, transport=transport)
            raise
        self.live_verification_record_success(transport)
        return result

    def supports_live_transport(self, transport):
        """True when this STT model can drive ``transport`` (webrtc/websocket).

        A model the provider rejected for realtime transcription is not offered
        for any transport, whatever the static capability matrix says.
        """
        self.ensure_one()
        if not self.supports_live_transcription:
            return False
        if self.live_verification_state == "rejected":
            return False
        if transport == "webrtc":
            return bool(self.supports_live_webrtc)
        if transport == "websocket":
            return bool(self.supports_live_websocket)
        return False

    def write(self, vals):
        result = super().write(vals)
        if {"name", "provider_id", "model_use"} & set(vals):
            # A different model or provider is a different contract: what was
            # verified no longer says anything about it.
            super().write({
                "live_verification_state": "unverified",
                "live_verification_at": False,
                "live_verification_transports": False,
                "live_verification_details": False,
            })
        return result

    # ------------------------------------------------------------------
    # Live verification against the real provider
    # ------------------------------------------------------------------
    def _live_verification_transports(self):
        self.ensure_one()
        transports = []
        if self.supports_live_webrtc:
            transports.append("webrtc")
        if self.supports_live_websocket:
            transports.append("websocket")
        return transports

    def live_verification_record_success(self, transport):
        """A real provider session was opened for ``transport``."""
        self.ensure_one()
        verified = [
            item for item in (self.live_verification_transports or "").split(",") if item
        ]
        if transport and transport not in verified:
            verified.append(transport)
        self.sudo().write({
            "live_verification_state": "verified",
            "live_verification_at": fields.Datetime.now(),
            "live_verification_transports": ",".join(verified),
            "live_verification_details": {
                "operation": "live_credentials",
                "outcome": "accepted",
                "transport": transport,
            },
        })
        return True

    def live_verification_record_failure(self, error, transport=None):
        """Record a provider refusal. Only capability refusals reject the model.

        Authentication or throttling says nothing about the model, so the state
        stays as it was and only the telemetry is kept.
        """
        self.ensure_one()
        from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

        details = {"operation": "live_credentials", "transport": transport}
        code = None
        if isinstance(error, LLMTranscriptionError):
            code = error.code
            details.update(error.details or {})
            details["code"] = code
            details["retryable"] = error.retryable
        else:
            details["code"] = "provider_failure"
        values = {"live_verification_details": details}
        if code == "unsupported_capability":
            values.update({
                "live_verification_state": "rejected",
                "live_verification_at": fields.Datetime.now(),
                "live_verification_transports": False,
            })
        self.sudo().write(values)
        return values.get("live_verification_state") == "rejected"

    def action_verify_live_transcription(self):
        """Open a real ephemeral provider session per transport and record the outcome.

        Nothing is streamed: creating the short-lived session credential is the
        provider's contract check. The result drives ``supports_live_transport``
        so a model the provider refuses cannot be planned for live capture.
        """
        from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError

        results = []
        for model in self:
            if model.model_use != "transcription" or not model.supports_live_transcription:
                results.append((model, "skipped", None))
                continue
            transports = model._live_verification_transports()
            if not transports:
                results.append((model, "skipped", None))
                continue
            outcome = "verified"
            last_error = None
            # Start from a clean slate so a model that was rejected earlier can
            # be re-verified after the provider (or its matrix) changed.
            model.sudo().write({
                "live_verification_state": "unverified",
                "live_verification_transports": False,
            })
            for transport in transports:
                try:
                    # The model method records the outcome itself; the savepoint
                    # only protects the transaction from an unexpected SQL error,
                    # so the outcome is recorded again after a rollback.
                    with self.env.cr.savepoint():
                        model.transcribe_live_credentials(transport=transport)
                except LLMTranscriptionError as error:
                    last_error = error
                    rejected = model.live_verification_record_failure(error, transport=transport)
                    outcome = "rejected" if rejected else "error"
                    break
                except Exception as error:  # noqa: BLE001 - verification must report, not crash
                    last_error = error
                    model.live_verification_record_failure(error, transport=transport)
                    outcome = "error"
                    break
                model.live_verification_record_success(transport)
            results.append((model, outcome, last_error))
        verified = [model.name for model, outcome, _error in results if outcome == "verified"]
        rejected = [model.name for model, outcome, _error in results if outcome == "rejected"]
        errored = [
            "%s (%s)" % (model.name, getattr(error, "code", None) or "provider_failure")
            for model, outcome, error in results if outcome == "error"
        ]
        parts = []
        if verified:
            parts.append(_("Verified: %s") % ", ".join(verified))
        if rejected:
            parts.append(_("Rejected by the provider: %s") % ", ".join(rejected))
        if errored:
            parts.append(_("Could not verify: %s") % ", ".join(errored))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Live Transcription Verification"),
                "message": "\n".join(parts) or _("No live-capable transcription model selected."),
                "type": "success" if verified and not rejected and not errored else "warning",
                "sticky": bool(rejected or errored),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_open_fetch_this_model_wizard(self):
        self.ensure_one()
        # Call the provider's action_fetch_models with context for specific model
        return self.provider_id.with_context(
            default_model_to_fetch=self.name
        ).action_fetch_models()
