"""Reclassify OpenAI models with the explicit capability registry.

Before 17.0.1.12.0 every OpenAI model id that was not an embedding or
transcription model was imported as ``chat``, including image, speech,
realtime and moderation models. Those records are moved to ``unsupported``
(or ``unverified`` when the id is unknown) so they stop appearing in chat
model pickers. Records are not archived or deleted.
"""

import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.llm_openai.utils.openai_capabilities import lookup

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    providers = env["llm.provider"].search([("service", "=", "openai")])
    providers = providers.filtered(lambda p: p._openai_uses_capability_routing())
    models = env["llm.model"].search([
        ("provider_id", "in", providers.ids),
        ("model_use", "in", ("chat", "multimodal")),
    ])
    changed = {}
    for model in models:
        capabilities = lookup(model.name)
        if capabilities.chat_capable and capabilities.verified:
            target = "multimodal" if capabilities.vision else "chat"
        elif capabilities.chat_capable or capabilities.kind == "unknown":
            target = "unverified"
        else:
            target = "unsupported"
        if target != model.model_use:
            changed.setdefault(target, []).append(model.name)
            model.write({"model_use": target})
    for target, names in changed.items():
        _logger.info("llm_openai: %d OpenAI models set to %s: %s", len(names), target, ", ".join(names))
