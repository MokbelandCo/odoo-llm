"""Publish the record-retriever domain, offset and order contract.

Diebec's installed ``llm_tool`` is ``18.0.4.1.1``. The earlier ``18.0.1.1.0``
script is below that version, so Odoo never runs it on this database. This
``18.0.4.2.0`` script is the one that runs there. It is still below
``19.0.1.1.0``, so a later upgrade to 19 runs that script too. Both scripts
call the same refresh, which updates the existing noupdate tool row and clears
a stored input schema that does not mention offset.
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
