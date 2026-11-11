"""Publish the record-retriever domain, offset and order contract.

The tool row is noupdate, so an upgrade would otherwise keep a description
and a stored input schema that predate offset/order. Clearing a stale schema
makes discovery rebuild it from the method signature. The same XML id is
updated in place. This is the Odoo 18 counterpart of the 17.0.4.4.0 and
19.0.1.1.0 migrations; the 19.0.1.1.0 step is idempotent when an 18.0.1.1.0
database is later upgraded to 19.
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
