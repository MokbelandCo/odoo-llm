import base64
import contextlib
import json
import logging

import emoji
import markdown2
from markupsafe import Markup
from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.llm.models.llm_media_capabilities import mark_attachment_media_role
from odoo.addons.llm.models.llm_transcription import LLMTranscriptionError
from odoo.addons.llm.models.mail_message import (
    AUDIO_MIMETYPES,
    IMAGE_MIMETYPES,
    PDF_MIMETYPES,
    TEXT_MIMETYPES,
)

_logger = logging.getLogger(__name__)

CHAT_MODEL_USES = ("chat", "multimodal")
# Soft ceiling for chat attachments when the model does not advertise a limit.
DEFAULT_CHAT_ATTACHMENT_MAX_BYTES = 32 * 1024 * 1024


class RelatedRecordProxy:
    """
    A proxy object that provides clean access to related record fields in Jinja templates.
    Usage in templates: {{ related_record.get_field('field_name', 'default_value') }}
    When called directly, returns JSON with model name, id, and display name.
    """

    def __init__(self, record):
        self._record = record

    def get_field(self, field_name, default=""):
        """
        Get a field value from the related record.

        Args:
            field_name (str): The field name to access
            default: Default value if field doesn't exist or is empty

        Returns:
            The field value, or default if not available
        """
        if not self._record:
            return default

        try:
            if hasattr(self._record, field_name):
                value = getattr(self._record, field_name)

                # Handle different field types
                if value is None:
                    return default
                if isinstance(value, bool):
                    return value  # Keep as boolean for Jinja
                if hasattr(value, "name"):  # Many2one field
                    return value.name
                if hasattr(value, "mapped"):  # Many2many/One2many field
                    return value.mapped("name")
                return value
            _logger.debug(
                "Field '%s' not found on record %s",
                field_name,
                self._record,
            )
            return default

        except Exception as e:
            _logger.error(
                "Error getting field '%s' from record: %s",
                field_name,
                e,
            )
            return default

    def __getattr__(self, name):
        """Allow direct attribute access as fallback"""
        return self.get_field(name)

    def __bool__(self):
        """Return True if we have a record"""
        return bool(self._record)

    def __str__(self):
        """When called by itself, return JSON of model name, id, and display name"""
        if not self._record:
            return json.dumps({"model": None, "id": None, "display_name": None})

        return json.dumps(
            {
                "model": self._record._name,
                "id": self._record.id,
                "display_name": getattr(
                    self._record,
                    "display_name",
                    str(self._record),
                ),
            },
        )

    def __repr__(self):
        """Same as __str__ for consistency"""
        return self.__str__()


class LLMThread(models.Model):
    _name = "llm.thread"
    _description = "LLM Chat Thread"
    _inherit = ["mail.thread"]
    _order = "write_date DESC"

    name = fields.Char(
        string="Title",
        required=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        default=lambda self: self.env.user,
        required=True,
        ondelete="restrict",
    )
    provider_id = fields.Many2one(
        "llm.provider",
        string="Provider",
        required=True,
        ondelete="restrict",
    )
    model_id = fields.Many2one(
        "llm.model",
        string="Model",
        required=True,
        domain="[('provider_id', '=', provider_id), ('model_use', 'in', ['chat', 'multimodal'])]",
        ondelete="restrict",
    )
    active = fields.Boolean(default=True)

    # Updated fields for related record reference
    model = fields.Char(
        string="Related Document Model",
        help="Technical name of the related model",
    )
    res_id = fields.Many2oneReference(
        string="Related Document ID",
        model_field="model",
        help="ID of the related record",
    )

    tool_ids = fields.Many2many(
        "llm.tool",
        string="Available Tools",
        default=lambda self: self.env["llm.tool"]._get_default_optional_tools(),
        help="Optional tools selected for this thread. New threads start with "
        "the active Default tools. Always Include tools that match this "
        "thread's user are added to every request and do not need to be "
        "selected here.",
    )

    attachment_ids = fields.Many2many(
        "ir.attachment",
        string="All Thread Attachments",
        compute="_compute_attachment_ids",
        store=True,
        help="All attachments from all messages in this thread",
    )

    attachment_count = fields.Integer(
        string="Thread Attachments",
        compute="_compute_attachment_count",
        store=True,
        help="Total number of attachments in this thread",
    )

    @api.model
    def _resolve_thread_chat_model(self, provider, model=None):
        """Pick a chat-capable model for a thread.

        Embedding/completion models cannot be used with chat/completions.
        Prefer a marked default, then multimodal (GPT chat models), then chat.
        """
        if model and model.model_use in CHAT_MODEL_USES:
            return model
        if not provider:
            return model
        models = provider.model_ids.filtered(
            lambda rec: rec.model_use in CHAT_MODEL_USES
        )
        if not models:
            return model
        default_model = models.filtered("default")[:1]
        if default_model:
            return default_model
        multimodal = models.filtered(lambda rec: rec.model_use == "multimodal")[:1]
        if multimodal:
            return multimodal
        return models[:1]

    @api.model_create_multi
    def create(self, vals_list):
        """Set default title if not provided"""
        needs_unique_name = []

        for vals in vals_list:
            provider = (
                self.env["llm.provider"].browse(vals["provider_id"])
                if vals.get("provider_id")
                else self.env["llm.provider"]
            )
            model = (
                self.env["llm.model"].browse(vals["model_id"])
                if vals.get("model_id")
                else self.env["llm.model"]
            )
            resolved = self._resolve_thread_chat_model(provider, model)
            if resolved:
                vals["provider_id"] = resolved.provider_id.id
                vals["model_id"] = resolved.id

            if not vals.get("name"):
                # If linked to a record, use its display name
                if vals.get("model") and vals.get("res_id"):
                    try:
                        record = self.env[vals["model"]].browse(vals["res_id"])
                        if record.exists():
                            vals["name"] = f"AI Chat - {record.display_name}"
                        else:
                            # Record doesn't exist, use technical format
                            vals["name"] = f"AI Chat - {vals['model']}#{vals['res_id']}"
                    except Exception:
                        # Model doesn't exist or access error, use technical format
                        vals["name"] = f"AI Chat - {vals['model']}#{vals['res_id']}"
                else:
                    # Generic name - will add unique ID after creation
                    vals["name"] = "New Chat"
                    needs_unique_name.append(True)
            else:
                needs_unique_name.append(False)

        records = super().create(vals_list)

        # Update generic thread names to include unique ID
        for record, needs_update in zip(records, needs_unique_name):
            if needs_update:
                record.name = f"New Chat #{record.id}"

        return records

    # ============================================================================
    # TOOL SELECTION
    # ============================================================================

    def _get_tool_audience_user(self):
        """User whose type selects Always Include tools for this thread.

        Automation runs own the thread as their execution user, so that user
        is used here. Portal conversations override this: they execute as an
        internal service user, and the visitor is the audience.
        """
        self.ensure_one()
        return self.user_id or self.env.user

    def _get_always_include_tools(self):
        """Active Always Include tools that apply to this thread's audience user."""
        self.ensure_one()
        return self.env["llm.tool"]._get_always_include_tools(
            self._get_tool_audience_user()
        )

    def _get_effective_tools(self):
        """Tools offered to the provider and allowed to run for this thread.

        Active Always Include tools for the audience user, plus the active
        tools selected in ``tool_ids``. Inactive tools never qualify, whatever
        ids are stored. The tool search runs as the current user, so llm.tool
        record rules apply. Resolved on every request, so flag changes reach
        open threads.
        """
        self.ensure_one()
        selected = self.tool_ids.filtered("active")
        return selected | self._get_always_include_tools()

    @api.depends("message_ids.attachment_ids")
    def _compute_attachment_ids(self):
        """Compute all attachments from all messages in this thread."""
        for thread in self:
            # Get all attachments from all messages in this thread
            all_attachments = thread.message_ids.mapped("attachment_ids")
            thread.attachment_ids = [(6, 0, all_attachments.ids)]

    @api.depends("attachment_ids")
    def _compute_attachment_count(self):
        """Compute the total number of attachments in this thread."""
        for thread in self:
            thread.attachment_count = len(thread.attachment_ids)

    # ============================================================================
    # MESSAGE POST OVERRIDES - Clean integration with mail.thread
    # ============================================================================

    @api.returns("mail.message", lambda value: value.id)
    def message_post(
        self,
        *,
        llm_role=None,
        message_type="comment",
        body_json=None,
        is_error=False,
        **kwargs,
    ):
        """Override to handle LLM-specific message types and metadata.

        Args:
            llm_role (str): The LLM role ('user', 'assistant', 'tool', 'system')
                           If provided, will automatically set the appropriate subtype
            body_json (dict): JSON body for tool calls - will be set after message creation
            is_error (bool): If True, marks message as error (excluded from LLM context)
        """

        # Convert LLM role to subtype_xmlid if provided
        if llm_role:
            _, role_to_id = self.env["mail.message"].get_llm_roles()
            if llm_role in role_to_id:
                # Get the xmlid from the role
                subtype_xmlid = f"llm.mt_{llm_role}"
                kwargs["subtype_xmlid"] = subtype_xmlid

        # Handle LLM-specific subtypes and email_from generation
        if not kwargs.get("author_id") and not kwargs.get("email_from"):
            kwargs["email_from"] = self._get_llm_email_from(
                kwargs.get("subtype_xmlid"),
                kwargs.get("author_id"),
                llm_role,
            )

        # Convert markdown to HTML if needed (only for assistant messages)
        # User messages should be plain text, tool messages use body_json
        if kwargs.get("body") and llm_role == "assistant":
            kwargs["body"] = self._process_llm_body(kwargs["body"])

        # Create the message using standard mail.thread flow (without body_json)
        message = super().message_post(message_type=message_type, **kwargs)

        # Set additional fields after message creation
        write_vals = {}
        if body_json:
            write_vals["body_json"] = body_json
        if is_error:
            write_vals["is_error"] = True
        if write_vals:
            message.write(write_vals)

        return message

    def _get_llm_email_from(self, subtype_xmlid, author_id, llm_role=None):
        """Generate appropriate email_from for LLM messages."""
        if author_id:
            return None  # Let standard flow handle it

        provider_name = self.provider_id.name
        model_name = self.model_id.name

        if subtype_xmlid == "llm.mt_tool" or llm_role == "tool":
            return f"Tool <tool@{provider_name.lower().replace(' ', '')}.ai>"
        if subtype_xmlid == "llm.mt_assistant" or llm_role == "assistant":
            return f"{model_name} <ai@{provider_name.lower().replace(' ', '')}.ai>"

        return None

    def _process_llm_body(self, body):
        """Process body content for LLM messages (markdown to HTML conversion).

        Skips processing if body is already Markup (pre-formatted HTML).
        """
        if not body or isinstance(body, Markup):
            return body
        return markdown2.markdown(emoji.demojize(body))

    # ============================================================================
    # STREAMING MESSAGE CREATION
    # ============================================================================

    def message_post_from_stream(
        self,
        stream,
        llm_role,
        placeholder_text="…",
        **kwargs,
    ):
        """Create and update a message from a streaming response.

        Args:
            stream: Generator yielding chunks of response data
            llm_role (str): The LLM role ('user', 'assistant', 'tool', 'system')
            placeholder_text (str): Text to show while streaming

        Returns:
            message: The created/updated message record
        """
        message = None
        accumulated_content = ""

        for chunk in stream:
            # Initialize message on first content
            if message is None and chunk.get("content"):
                message = self.message_post(
                    body=placeholder_text,
                    llm_role=llm_role,
                    author_id=False,
                    **kwargs,
                )
                yield {"type": "message_create", "message": message.to_store_format()}

            # Handle content streaming
            if chunk.get("content"):
                accumulated_content += chunk["content"]
                message.write({"body": self._process_llm_body(accumulated_content)})
                yield {"type": "message_chunk", "message": message.to_store_format()}

            # Handle errors
            if chunk.get("error"):
                yield {"type": "error", "error": chunk["error"]}
                return message

        # Final update for assistant message
        if message and accumulated_content:
            message.write({"body": self._process_llm_body(accumulated_content)})
            yield {"type": "message_update", "message": message.to_store_format()}

        return message

    # ============================================================================
    # GENERATION FLOW - Refactored to use message_post with roles
    # ============================================================================

    def generate(self, user_message_body=None, attachment_ids=None, **kwargs):
        """Main generation method with PostgreSQL advisory locking.

        Args:
            user_message_body: Optional message body. If not provided, will use
                              the latest message in the thread to start generation.
            attachment_ids: Optional list of ir.attachment IDs to attach to user message.
        """
        self.ensure_one()
        self._ensure_chat_capable_model()

        with self._generation_lock():
            last_message = False
            if user_message_body or attachment_ids:
                validated_ids = self._validate_chat_attachments(attachment_ids or [])
                post_kwargs = {
                    "body": user_message_body or "",
                    "llm_role": "user",
                    "author_id": self.env.user.partner_id.id,
                }
                if validated_ids:
                    post_kwargs["attachment_ids"] = validated_ids
                last_message = self.message_post(**post_kwargs)
                yield {
                    "type": "message_create",
                    "message": last_message.to_store_format(),
                }

                # Check for unsupported attachments in the new message
                if last_message.attachment_ids:
                    unsupported = self._check_unsupported_attachments(last_message)
                    if unsupported:
                        # Mark the user message as error (excluded from LLM context)
                        last_message.write({"is_error": True})
                        # Show warning and return - don't call LLM
                        yield from self._handle_unsupported_attachments(unsupported)
                        return last_message

            # Always validate the full context (covers mid-thread model switches).
            context_unsupported = self._check_unsupported_attachments()
            if context_unsupported:
                yield from self._handle_unsupported_attachments(context_unsupported)
                return last_message

            last_message = yield from self.generate_messages(last_message)
            return last_message

    def _ensure_chat_capable_model(self):
        """Refuse embedding/completion models before calling a chat API."""
        self.ensure_one()
        if self.model_id.model_use in CHAT_MODEL_USES:
            return
        raise UserError(
            _(
                "The selected model '%(model)s' is a %(use)s model and cannot be used for chat. "
                "Choose a chat or multimodal model (for example gpt-4o or gpt-5)."
            )
            % {
                "model": self.model_id.display_name,
                "use": self.model_id.model_use,
            }
        )

    def _get_context_messages(self, limit=25):
        """Get recent LLM messages that will be sent as context.

        This is used to validate attachments in the ENTIRE context before
        sending to the LLM, not just the new message.

        Note: Error messages (is_error=True) are excluded from context.

        Args:
            limit: Maximum number of messages to retrieve (default: 25)

        Returns:
            mail.message recordset of recent LLM messages
        """
        self.ensure_one()
        domain = [
            ("model", "=", self._name),
            ("res_id", "=", self.id),
            ("llm_role", "!=", False),
            ("is_error", "=", False),  # Exclude error messages from context
        ]
        return self.env["mail.message"].search(
            domain,
            order="create_date DESC, id DESC",
            limit=limit,
        )

    def _check_unsupported_attachments(self, message=None):
        """Check a message for unsupported attachments.

        Args:
            message: Specific message to check. If None, checks entire context.

        Returns:
            List of unsupported attachments or empty list
        """
        self.ensure_one()

        if message:
            # Check only the specific message
            messages_to_check = message
        else:
            # Check all context messages (for model switch scenarios)
            context_messages = self._get_context_messages()
            messages_to_check = context_messages.filtered(
                lambda m: m.attachment_ids,
            )

        if not messages_to_check:
            return []

        provider_service = self.provider_id.service
        media_capabilities = self.model_id.get_media_capabilities()
        is_multimodal = bool(
            media_capabilities.get("image") or media_capabilities.get("pdf")
        )

        return messages_to_check._get_unsupported_attachments(
            provider_service=provider_service,
            is_multimodal=is_multimodal,
            media_capabilities=media_capabilities,
        )

    def _validate_chat_attachments(self, attachment_ids):
        """Validate ownership, access and size before attaching files to a thread.

        The stream URL only carries attachment ids, never file bytes. Reject
        foreign or inaccessible attachments before ``message_post``.
        """
        self.ensure_one()
        if not attachment_ids:
            return []
        ids = []
        for value in attachment_ids:
            try:
                att_id = int(value)
            except (TypeError, ValueError):
                continue
            if att_id and att_id not in ids:
                ids.append(att_id)
        if not ids:
            return []

        Attachment = self.env["ir.attachment"]
        try:
            attachments = Attachment.browse(ids).exists()
            # Force an ACL check while we still hold user rights.
            attachments.check_access_rights("read")
            attachments.check_access_rule("read")
        except AccessError as err:
            raise UserError(
                _("You do not have access to one or more attached files.")
            ) from err

        if len(attachments) != len(ids):
            raise UserError(_("One or more attachments could not be found."))

        media = self.model_id.get_media_capabilities()
        max_by_type = {
            "image": media.get("max_image_bytes") or DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
            "pdf": media.get("max_pdf_bytes") or DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
            "text": media.get("max_text_bytes") or DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
            "audio": media.get("max_audio_bytes") or DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
            "other": DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
        }

        for att in attachments:
            # Ownership / scope: creator, already linked to this thread, or
            # linked to the message/thread the user can already read.
            owned = att.create_uid == self.env.user
            linked_here = att.res_model == self._name and att.res_id == self.id
            if not owned and not linked_here and not self.env.user.has_group(
                "base.group_system"
            ):
                raise UserError(
                    _(
                        "Attachment '%(name)s' does not belong to this chat. "
                        "Re-upload the file from the composer."
                    )
                    % {"name": att.name or att.id}
                )

            mimetype = att.mimetype or ""
            if mimetype in IMAGE_MIMETYPES:
                kind = "image"
            elif mimetype in PDF_MIMETYPES:
                kind = "pdf"
            elif mimetype in TEXT_MIMETYPES:
                kind = "text"
            elif mimetype in AUDIO_MIMETYPES:
                kind = "audio"
            else:
                kind = "other"
            size = att.file_size or 0
            if not size and att.datas:
                try:
                    size = len(base64.b64decode(att.datas))
                except (TypeError, ValueError):
                    size = 0
            limit = max_by_type[kind]
            if size and size > limit:
                raise UserError(
                    _(
                        "Attachment '%(name)s' is too large (%(size)s bytes). "
                        "Maximum for %(kind)s files is %(limit)s bytes."
                    )
                    % {
                        "name": att.name or att.id,
                        "size": size,
                        "kind": kind,
                        "limit": limit,
                    }
                )

            # Bind unlinked uploads to this thread so later turns keep ACL scope.
            if not att.res_model or not att.res_id:
                att.write({"res_model": self._name, "res_id": self.id})

        return attachments.ids

    def get_composer_media_capabilities(self):
        """JSON-safe media capability contract for the active thread model."""
        self.ensure_one()
        media = self.model_id.get_media_capabilities()
        stt = self._get_batch_transcription_model()
        media["batch_stt"] = bool(stt)
        media["batch_stt_model_id"] = stt.id if stt else False
        media["batch_stt_model_name"] = stt.name if stt else False
        return media

    def _get_batch_transcription_model(self):
        """Pick a configured batch STT model for chat dictation.

        Prefer a model on the same provider (OpenAI gpt-transcribe family when
        available), then any active batch transcription model.
        """
        self.ensure_one()
        Model = self.env["llm.model"]
        domain = [
            ("active", "=", True),
            ("model_use", "=", "transcription"),
            ("supports_batch_transcription", "=", True),
        ]
        preferred_names = (
            "gpt-transcribe",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "whisper-1",
        )
        same_provider = Model.search(
            domain + [("provider_id", "=", self.provider_id.id)],
            order="default desc, id asc",
        )
        for name in preferred_names:
            match = same_provider.filtered(lambda m, n=name: (m.name or "").lower() == n)
            if match:
                return match[:1]
        if same_provider:
            return same_provider[:1]
        return Model.search(domain, order="default desc, id asc", limit=1)

    def transcribe_voice_note(self, attachment_id, language=None, keep_recording=True):
        """Transcribe a recorded voice note for chat dictation.

        Returns editable transcript text plus metadata. Does not auto-send.
        The original recording may be kept as a dictation-source attachment
        for playback/audit; it is never sent as chat-model audio input.
        """
        self.ensure_one()
        attachment = self.env["ir.attachment"].browse(int(attachment_id)).exists()
        if not attachment:
            raise UserError(_("Voice recording not found."))
        try:
            attachment.check_access_rights("read")
            attachment.check_access_rule("read")
        except AccessError as err:
            raise UserError(_("You do not have access to this recording.")) from err

        stt_model = self._get_batch_transcription_model()
        if not stt_model:
            raise UserError(
                _(
                    "No speech-to-text model is configured. Ask an administrator to "
                    "enable a batch transcription model (for example gpt-transcribe)."
                )
            )

        if not attachment.datas:
            raise UserError(_("The recording is empty."))
        try:
            audio_bytes = base64.b64decode(attachment.datas)
        except (TypeError, ValueError) as err:
            raise UserError(_("The recording could not be read.")) from err
        if not audio_bytes:
            raise UserError(_("The recording is empty."))

        filename = attachment.name or "voice-note.webm"
        content_type = attachment.mimetype or "audio/webm"
        try:
            result = stt_model.transcribe(
                audio_bytes,
                filename=filename,
                content_type=content_type,
                language=language,
            )
        except LLMTranscriptionError as err:
            # Never include audio bytes in the error payload.
            raise UserError(
                _("Speech-to-text failed: %(message)s")
                % {"message": err.message or err.code}
            ) from err

        transcript = (result.get("text") or "").strip()
        if keep_recording:
            attachment.write(
                {
                    "description": mark_attachment_media_role(
                        attachment.description, "dictation"
                    ),
                    "res_model": self._name,
                    "res_id": self.id,
                }
            )
        else:
            attachment.unlink()
            attachment = self.env["ir.attachment"]

        return {
            "text": transcript,
            "language": result.get("language") or language or False,
            "model": stt_model.name,
            "provider_request_id": result.get("provider_request_id") or False,
            "attachment_id": attachment.id if attachment else False,
            "duration_ms": result.get("duration_ms") or False,
        }

    def mark_attachment_analyze_audio(self, attachment_id):
        """Mark an uploaded audio clip for direct analysis (not dictation).

        Only allowed when the active model/endpoint supports direct audio chat.
        """
        self.ensure_one()
        media = self.model_id.get_media_capabilities()
        if not media.get("direct_audio_chat"):
            raise UserError(
                _(
                    "The selected model does not support direct audio analysis. "
                    "Use voice dictation instead."
                )
            )
        if media.get("chat_endpoint") == "responses":
            raise UserError(
                _(
                    "Audio analysis is not available on the Responses endpoint. "
                    "Choose an audio-capable Chat Completions model, or use dictation."
                )
            )
        attachment = self.env["ir.attachment"].browse(int(attachment_id)).exists()
        if not attachment:
            raise UserError(_("Audio attachment not found."))
        self._validate_chat_attachments([attachment.id])
        attachment.write(
            {
                "description": mark_attachment_media_role(
                    attachment.description, "analyze"
                ),
            }
        )
        return {
            "attachment_id": attachment.id,
            "media_role": "analyze",
            "tools_with_direct_audio": bool(media.get("tools_with_direct_audio")),
        }

    def _handle_unsupported_attachments(self, unsupported):
        """Create an info message for unsupported attachments.

        The message has is_error=True so it's excluded from LLM context,
        allowing the conversation to continue normally.

        Args:
            unsupported: List of dicts with name, mimetype, reason

        Yields:
            Message events for the info message
        """
        # Build file list items
        file_items = "".join(
            f"<li><strong>{att['name']}</strong>: {att['reason']}</li>"
            for att in unsupported
        )

        # Build HTML message
        error_html = (
            f"<p>⚠️ <strong>{_('Unsupported file(s)')}</strong></p>"
            f"<ul>{file_items}</ul>"
            f"<p><em>{_('These files will be skipped.')}</em></p>"
        )

        # Post as info message (excluded from LLM context)
        error_message = self.message_post(
            body=Markup(error_html),
            llm_role="assistant",
            author_id=False,
            is_error=True,
        )
        yield {
            "type": "message_create",
            "message": error_message.to_store_format(),
        }
        return error_message

    def _post_error_message(self, error, title=None):
        """Post an error message to the thread (excluded from LLM context).

        This allows users to see API errors directly in the chat instead of
        only in server logs.

        Args:
            error: The exception or error string
            title: Optional title for the error message

        Returns:
            tuple: (error_message, event_dict) for yielding to the client
        """
        title = title or _("Error")
        error_str = str(error)

        # Build HTML error message
        error_html = (
            f"<p>❌ <strong>{title}</strong></p><p><code>{error_str}</code></p>"
        )

        error_message = self.message_post(
            body=Markup(error_html),
            llm_role="assistant",
            author_id=False,
            is_error=True,
        )

        event = {
            "type": "message_create",
            "message": error_message.to_store_format(),
        }
        return error_message, event

    def generate_messages(self, last_message=None):
        """Generate messages - to be overridden by llm_assistant module."""
        raise UserError(
            _("Please install the llm_assistant module for actual AI generation."),
        )

    def get_context(self, base_context=None):
        context = {
            **(base_context or {}),
            "thread_id": self.id,
        }
        # Guard clause: skip if model or res_id not set
        if not self.model or not self.res_id:
            return context

        try:
            related_record = self.env[self.model].browse(self.res_id)
            if related_record:
                context["related_record"] = RelatedRecordProxy(related_record)
                context["related_model"] = self.model
                context["related_res_id"] = self.res_id
            else:
                context["related_record"] = None
                context["related_model"] = None
                context["related_res_id"] = None
        except Exception as e:
            _logger.warning(
                "Error accessing related record %s,%s: %s",
                self.model,
                self.res_id,
                e,
            )

        return context

    # ============================================================================
    # POSTGRESQL ADVISORY LOCK IMPLEMENTATION
    # ============================================================================

    def _acquire_thread_lock(self):
        """Acquire PostgreSQL advisory lock for this thread."""
        self.ensure_one()

        try:
            query = "SELECT pg_try_advisory_lock(%s)"
            self.env.cr.execute(query, (self.id,))
            result = self.env.cr.fetchone()

            if not result or not result[0]:
                raise UserError(
                    _(
                        "This conversation is currently generating a response. "
                        "Please wait for it to complete before sending another message.",
                    ),
                )

            _logger.info(f"Acquired advisory lock for thread {self.id}")

        except UserError:
            raise
        except OperationalError as e:
            _logger.error("Database error acquiring lock for thread %s: %s", self.id, e)
            raise UserError(
                _(
                    "Unable to process your request due to a system conflict. "
                    "Please wait a moment and try again.",
                ),
            ) from e
        except Exception as e:
            _logger.error(
                "Unexpected error acquiring lock for thread %s: %s",
                self.id,
                e,
            )
            raise UserError(
                _(
                    "Your request could not be processed. Please refresh the page and try again.",
                ),
            ) from e

    def _release_thread_lock(self):
        """Release PostgreSQL advisory lock for this thread."""
        self.ensure_one()

        try:
            query = "SELECT pg_advisory_unlock(%s)"
            self.env.cr.execute(query, (self.id,))
            result = self.env.cr.fetchone()

            success = result and result[0]
            if success:
                _logger.info(f"Released advisory lock for thread {self.id}")
            else:
                _logger.warning(f"Advisory lock for thread {self.id} was not held")

            return success

        except Exception as e:
            _logger.error(f"Error releasing lock for thread {self.id}: {e}")
            return False

    @contextlib.contextmanager
    def _generation_lock(self):
        """Context manager for thread generation with automatic lock cleanup."""
        self.ensure_one()

        self._acquire_thread_lock()

        try:
            _logger.info(f"Starting locked generation for thread {self.id}")
            yield self

        finally:
            released = self._release_thread_lock()
            if released:
                _logger.info(f"Finished locked generation for thread {self.id}")
            else:
                _logger.warning(f"Lock release failed for thread {self.id}")

    # ============================================================================
    # ODOO HOOKS AND CLEANUP
    # ============================================================================

    # ============================================================================
    # STORE INTEGRATION - For mail.store compatibility
    # ============================================================================

    def _llm_thread_format(self):
        """Format llm.thread records for the Odoo 17 mail JS store."""
        self.ensure_one()
        data = {
            "id": self.id,
            "model": "llm.thread",
            "name": self.name,
            "write_date": self.write_date,
            "type": "llm_chat",
            "res_model": self.model or False,
            "res_id": self.res_id or False,
            "provider_id": False,
            "model_id": False,
            "tool_ids": [],
        }
        if self.provider_id:
            data["provider_id"] = {
                "id": self.provider_id.id,
                "name": self.provider_id.name,
            }
        if self.model_id:
            data["model_id"] = {
                "id": self.model_id.id,
                "name": self.model_id.name,
            }
        if self.tool_ids:
            data["tool_ids"] = [
                {"id": tool.id, "name": tool.name} for tool in self.tool_ids
            ]
        return data

    @api.ondelete(at_uninstall=False)
    def _unlink_llm_thread(self):
        unlink_ids = [record.id for record in self]
        self.env["bus.bus"]._sendone(
            self.env.user.partner_id,
            "llm.thread/delete",
            {"ids": unlink_ids},
        )
