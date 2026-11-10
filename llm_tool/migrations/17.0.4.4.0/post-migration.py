"""Publish the record-retriever domain, offset and order contract.

The tool row is noupdate, so an upgrade would otherwise keep a description
and a stored input schema that predate offset/order. Clearing a stale schema
makes discovery rebuild it from the method signature. The same XML id is
updated in place.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["llm.tool"]._refresh_record_retriever_contract()
    _logger.info(
        "Refreshed odoo_record_retriever description and cleared a stale input schema"
    )
