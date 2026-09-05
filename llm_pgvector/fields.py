import logging

import numpy as np
from pgvector import Vector
from pgvector.psycopg2 import register_vector

from odoo import fields
from odoo.fields import Default
from odoo.tools import sql

_logger = logging.getLogger(__name__)


class PgVector(fields.Field):
    """PgVector field for Odoo, using pgvector extension for PostgreSQL.

    This field stores vector embeddings in PostgreSQL using the pgvector extension.

    :param int dimension: Optional dimension of the vector. If provided, the column
                          will be created with the specified dimension constraint.
    """

    type = "pgvector"
    column_type = ("vector", "vector")
    dimension = None

    def __init__(self, string=Default, dimension=Default, **kwargs):
        super().__init__(string=string, dimension=dimension, **kwargs)

    def convert_to_column(self, value, record, values=None, validate=True):
        """Convert Python value to database format using pgvector.Vector."""
        if value is None:
            return None

        try:
            return Vector._to_db(value, self.dimension)
        except (ValueError, TypeError) as e:
            _logger.warning("Error converting vector: %s. Returning NULL.", e)
            return None

    def convert_to_cache(self, value, record, validate=True):
        """Convert database value to cache format."""
        if value is None:
            return None

        if isinstance(value, (list, np.ndarray)):
            return value

        try:
            return Vector._from_db(value)
        except (ValueError, TypeError) as e:
            _logger.warning("Error converting vector from DB: %s. Returning None.", e)
            return None

    def update_db_column(self, model, column):
        """Create or update a PostgreSQL vector column (Odoo 17 has no Field.create_column)."""
        register_vector(model._cr._cnx)
        dim_spec = f"({self.dimension})" if self.dimension else ""
        coltype = f"vector{dim_spec}"
        if not column:
            sql.create_column(
                model._cr, model._table, self.name, coltype, self.string
            )
            return
        if column["udt_name"] == "vector":
            return
        if column["is_nullable"] == "NO":
            sql.drop_not_null(model._cr, model._table, self.name)
        sql.convert_column(model._cr, model._table, self.name, coltype)
