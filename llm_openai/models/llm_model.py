from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..utils.openai_capabilities import apply_probe, lookup, summary


class LLMModel(models.Model):
    _inherit = "llm.model"

    openai_endpoint = fields.Selection(
        [
            ("auto", "Automatic"),
            ("responses", "Responses API"),
            ("chat_completions", "Chat Completions"),
        ],
        string="OpenAI Endpoint",
        default="auto",
        help="Automatic keeps Chat Completions for GPT-4 class models and uses the "
        "Responses API for reasoning models, Responses-only models and tools that "
        "require it. A forced endpoint the model cannot serve raises an error.",
    )
    openai_reasoning_effort = fields.Selection(
        [
            ("none", "None"),
            ("minimal", "Minimal"),
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("xhigh", "Extra High"),
            ("max", "Max"),
        ],
        string="Reasoning Effort",
        help="Sent as reasoning.effort (Responses) or reasoning_effort (Chat "
        "Completions). Empty uses the model default. Ignored for non-reasoning models.",
    )
    openai_max_output_tokens = fields.Integer(
        string="Max Output Tokens",
        help="Sent as max_output_tokens (Responses) or max_completion_tokens (Chat "
        "Completions). Reasoning tokens count toward this limit. 0 uses the model default.",
    )
    openai_verbosity = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")],
        string="Verbosity",
        help="text.verbosity for GPT-5 and later models. Ignored by models that do not support it.",
    )
    openai_probe_result = fields.Json(
        string="OpenAI Probe Result",
        readonly=True,
        copy=False,
        help="What the provider accepted the last time the capability probe ran: "
        "endpoints, tools, image input and error codes only.",
    )
    is_openai_routed = fields.Boolean(
        compute="_compute_openai_capability_summary",
        help="The provider is the OpenAI API, so per-model capability routing applies.",
    )
    openai_capability_summary = fields.Text(
        string="OpenAI Capabilities",
        compute="_compute_openai_capability_summary",
    )

    @api.model
    def _get_available_model_usages(self):
        return super()._get_available_model_usages() + [
            ("unverified", "Unverified (probe required)"),
            ("unsupported", "Not usable in Odoo"),
        ]

    @api.depends("name", "provider_id.service", "provider_id.api_base", "openai_probe_result")
    def _compute_openai_capability_summary(self):
        for model in self:
            routed = bool(
                model.provider_id and model.provider_id._openai_uses_capability_routing()
            )
            model.is_openai_routed = routed
            model.openai_capability_summary = (
                summary(model._openai_capabilities()) if routed and model.name else False
            )

    def write(self, vals):
        if vals.get("model_use") != "unverified":
            return super().write(vals)
        # Re-importing a model resets it to unverified; a model the probe
        # already verified keeps its chat use.
        verified = self.filtered(
            lambda m: m.model_use in ("chat", "multimodal")
            and m.openai_probe_result
            and m._openai_capabilities().chat_capable
            and m._openai_capabilities().verified
        )
        result = super(LLMModel, self - verified).write(vals) if self - verified else True
        if verified:
            super(LLMModel, verified).write({k: v for k, v in vals.items() if k != "model_use"})
        return result

    def _openai_capabilities(self):
        self.ensure_one()
        return apply_probe(lookup(self.name), self.openai_probe_result)

    def action_openai_probe_capabilities(self):
        """Send minimal live requests and record what OpenAI accepted."""
        lines = []
        for model in self:
            if not model.provider_id._openai_uses_capability_routing():
                raise UserError(_("Model %s is not served by the OpenAI API.") % model.name)
            result = model.provider_id.openai_probe_model(model)
            model.sudo().write({"openai_probe_result": result})
            capabilities = model._openai_capabilities()
            if capabilities.chat_capable and model.model_use in ("unverified", "chat", "multimodal"):
                model.model_use = "multimodal" if capabilities.vision else "chat"
            accepted = [
                endpoint
                for endpoint, checks in (result.get("endpoints") or {}).items()
                if checks.get("text")
            ]
            lines.append(
                _("%(model)s: %(endpoints)s")
                % {
                    "model": model.name,
                    "endpoints": ", ".join(accepted) if accepted else _("no chat endpoint accepted"),
                }
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("OpenAI Capability Probe"),
                "message": "\n".join(lines),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
