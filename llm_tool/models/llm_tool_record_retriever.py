import json
import logging
from typing import Annotated, Any, Optional

from pydantic import Field

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.osv.expression import TERM_OPERATORS, normalize_domain

from odoo.addons.llm_tool.odoo_domain import OdooDomain, normalize_domain_json

_logger = logging.getLogger(__name__)

RECORD_RETRIEVER_DESCRIPTION = """Retrieve records from an Odoo model using the caller's access rights.

Arguments:
- model: technical model name.
- domain: JSON Odoo domain in prefix notation. Conditions are ["field", "operator", value]. Implicit AND applies when operators are omitted. Prefix "&", "|" and "!" may be nested. Values may be strings, numbers, booleans, null, or lists (for "in" and "not in", including an empty list). "any" and "not any" take a nested domain. Dotted relational fields are allowed. This is an ORM domain, not a Python expression.
- fields: list of field-name strings. Omit or pass [] to read the model's default fields.
- limit: maximum number of rows. Default 100. Must be an integer >= 0.
- offset: number of rows to skip. Default 0. Must be an integer >= 0.
- order: optional ORM order string such as "name asc, id asc". Omit it to keep the model order. Multiple fields and asc/desc are supported. For reliable paging over unchanged data, end the order with a unique key such as id.

The result is a JSON list of records. Record rules, field permissions and company restrictions of the current user apply. Invalid domains, fields, operators, value types, orders and negative pagination values raise an error."""


class LLMToolRecordRetriever(models.Model):
    _inherit = "llm.tool"

    @api.model
    def _get_available_implementations(self):
        implementations = super()._get_available_implementations()
        return implementations + [("odoo_record_retriever", "Odoo Record Retriever")]

    @api.model
    def _refresh_record_retriever_contract(self):
        """Publish the domain/offset/order contract on the existing tool record.

        Clears a stored schema that does not yet mention offset so discovery
        regenerates it from the method signature. The XML id record is updated
        in place and no second tool is created.
        """
        tools = self.search([("implementation", "=", "odoo_record_retriever")])
        for tool in tools:
            values = {}
            if "offset" not in (tool.description or ""):
                values["description"] = RECORD_RETRIEVER_DESCRIPTION
            schema = tool.input_schema or ""
            if schema and "offset" not in schema:
                values["input_schema"] = False
            if values:
                tool.write(values)
        return True

    def odoo_record_retriever_execute(
        self,
        model: str,
        domain: OdooDomain = [],  # noqa: B006
        fields: list[str] = [],  # noqa: B006
        limit: Annotated[
            int,
            Field(
                ge=0,
                description="Maximum number of records. Default 100. Must be >= 0.",
            ),
        ] = 100,
        offset: Annotated[
            int,
            Field(
                ge=0,
                description="Number of records to skip. Default 0. Must be >= 0.",
            ),
        ] = 0,
        order: Annotated[
            Optional[str],
            Field(
                description=(
                    "ORM order string such as 'name asc, id asc'. "
                    "Omit to keep the model order. End with a unique key such as id "
                    "so paging over unchanged data does not skip or repeat rows."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Execute the Odoo Record Retriever tool

        Parameters:
            model: The Odoo model to retrieve records from
            domain: JSON Odoo domain in prefix notation. Supports implicit AND,
                "&", "|", "!", nested combinations, dotted fields, scalars, null,
                and lists for in/not in. Not a Python expression.
            fields: List of field names to retrieve. Empty reads the default fields.
            limit: Maximum number of records to retrieve. Default 100. Must be >= 0.
            offset: Number of records to skip. Default 0. Must be >= 0.
            order: ORM order string. Omit to keep the model order. End with a
                unique key such as id for stable paging.
        """
        _logger.info(
            "Executing Odoo Record Retriever with: model=%s, domain=%s, fields=%s, "
            "limit=%s, offset=%s, order=%s",
            model,
            domain,
            fields,
            limit,
            offset,
            order,
        )
        domain = self._validate_retriever_domain(domain)
        fields = self._validate_retriever_fields(fields)
        limit = self._validate_retriever_pagination("limit", limit)
        offset = self._validate_retriever_pagination("offset", offset)
        model_obj = self.env[model]
        order = self._validate_retriever_order(model_obj, order)

        search_kwargs = {"domain": domain, "limit": limit, "offset": offset}
        if order:
            search_kwargs["order"] = order

        # Selected fields use search_read. An omitted or empty list keeps the
        # historical search() + read() result, which is the model's default fields.
        if fields:
            result = model_obj.search_read(fields=fields, **search_kwargs)
        else:
            records = model_obj.search(**search_kwargs)
            result = records.read()

        return json.loads(json.dumps(result, default=str))

    @api.model
    def _validate_retriever_domain(self, domain):
        """Copy the JSON domain, then ask the ORM to parse the expression."""
        try:
            if isinstance(domain, OdooDomain):
                normalized = list(domain)
            else:
                normalized = normalize_domain_json(domain)
            self._validate_domain_terms(normalized)
            normalize_domain(normalized)
        except (AssertionError, TypeError, ValueError) as error:
            raise ValueError("Invalid domain: %s" % error) from error
        return normalized

    @api.model
    def _validate_domain_terms(self, domain):
        """Reject operators and value shapes the installed ORM does not accept.

        The accepted operators and value shapes match llm_tool 17.0.4.4.0 so a
        database upgraded from 17 to 19 keeps the same call.
        """
        allowed = set(TERM_OPERATORS) | {"<>"}
        for token in domain:
            if isinstance(token, str):
                continue
            _field_name, operator, value = token
            if operator not in allowed:
                raise ValueError(
                    "Invalid operator %r in domain term %r. Supported operators: %s."
                    % (operator, token, ", ".join(sorted(allowed)))
                )
            if operator in ("in", "not in") and not isinstance(value, list):
                raise ValueError(
                    "Operator %r requires a list value in term %r, got %s."
                    % (operator, token, type(value).__name__)
                )
            if operator in ("any", "not any"):
                if not isinstance(value, list):
                    raise ValueError(
                        "Operator %r requires a nested domain in term %r."
                        % (operator, token)
                    )
                self._validate_domain_terms(value)

    @api.model
    def _validate_retriever_fields(self, fields):
        if fields is None:
            return []
        if isinstance(fields, str) or not isinstance(fields, (list, tuple)):
            raise ValueError("fields must be a list of field name strings.")
        if not all(isinstance(name, str) and name for name in fields):
            raise ValueError("fields must be a list of field name strings.")
        return list(fields)

    @api.model
    def _validate_retriever_pagination(self, name, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("%s must be an integer >= 0." % name)
        return value

    @api.model
    def _validate_retriever_order(self, model_obj, order):
        """Reject a malformed clause. None keeps the model ``_order``."""
        if order is None:
            return None
        if not isinstance(order, str) or not order.strip():
            raise UserError(
                _(
                    "Invalid order %r. Use a comma-separated ORM order such as "
                    "'name asc, id asc'. Omit order to keep the model order. End the "
                    "clause with a unique key such as id so paging over unchanged data "
                    "does not skip or repeat rows."
                )
                % (order,)
            )
        try:
            model_obj._check_qorder(order)
        except UserError as error:
            raise UserError(
                _(
                    "%s End the order with a unique key such as id "
                    "(for example 'name asc, id asc') so paging over unchanged data "
                    "does not skip or repeat rows."
                )
                % (error,)
            ) from error
        return order
