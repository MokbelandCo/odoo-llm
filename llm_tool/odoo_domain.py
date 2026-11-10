"""JSON Odoo domains for LLM tool signatures.

Odoo 17 domains are prefix lists: implicit AND, the tokens ``&``, ``|`` and ``!``,
and 3-item conditions ``[field, operator, value]``. Values may be scalars, lists
(``in`` / ``not in``), or a nested domain (``any`` / ``not any``). This type keeps
those operand types intact. It does not evaluate Python expressions.
"""

from pydantic_core import core_schema

_LOGICAL_OPERATORS = frozenset({"&", "|", "!"})

_SCALAR_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "boolean"},
        {"type": "number"},
        {"type": "null"},
    ]
}

ODOO_DOMAIN_JSON_SCHEMA = {
    "type": "array",
    "description": (
        "Odoo domain in JSON prefix notation. Items are logical operators "
        "('&', '|', '!') or conditions ['field', 'operator', value]. "
        "value is a scalar, a list (in/not in), or a nested domain (any/not any). "
        "Dotted relational fields are allowed. This is not a Python expression."
    ),
    "items": {
        "anyOf": [
            {"type": "string", "enum": ["&", "|", "!"]},
            {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "prefixItems": [
                    {
                        "type": "string",
                        "description": "Field name. Dotted paths such as parent_id.name are allowed.",
                    },
                    {"type": "string", "description": "ORM operator."},
                    {
                        "description": (
                            "Operand. Scalars, null, lists for in/not in, "
                            "or a nested domain for any/not any."
                        ),
                        "anyOf": [
                            _SCALAR_SCHEMA,
                            {"type": "array", "items": _SCALAR_SCHEMA},
                            {"type": "array"},
                        ],
                    },
                ],
            },
        ]
    },
}


def _is_scalar(value):
    return value is None or isinstance(value, (str, bool, int, float))


def _looks_like_domain(value):
    """A nested domain contains at least one condition list.

    A flat list of scalars is an ``in`` / ``not in`` operand, including the
    empty list. Logical-operator strings alone are not enough, so a list such
    as ``["&", "team"]`` stays a list of strings.
    """
    return any(isinstance(item, (list, tuple)) for item in value)


def _normalize_value(value):
    if _is_scalar(value):
        return value
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        if _looks_like_domain(value):
            return normalize_domain_json(value)
        return [_normalize_value(item) for item in value]
    raise ValueError(
        "Unsupported domain value %r (%s). Use JSON scalars, null, lists, "
        "or a nested domain. Python expressions are not accepted."
        % (value, type(value).__name__)
    )


def _normalize_token(token):
    if isinstance(token, str):
        return token
    if isinstance(token, tuple):
        token = list(token)
    if isinstance(token, list):
        if (
            len(token) != 3
            or not isinstance(token[0], str)
            or not isinstance(token[1], str)
        ):
            raise ValueError(
                "Invalid domain term %r. Expected [field, operator, value] "
                "or a logical operator '&', '|' or '!'."
                % (token,)
            )
        return [token[0], token[1], _normalize_value(token[2])]
    raise ValueError(
        "Invalid domain token %r. Expected a logical operator or a condition list."
        % (token,)
    )


def normalize_domain_json(domain):
    """Return a JSON-shaped copy of ``domain`` with operand types preserved."""
    if isinstance(domain, tuple):
        domain = list(domain)
    if not isinstance(domain, list):
        raise ValueError(
            "domain must be a list in Odoo prefix notation, not %s."
            % type(domain).__name__
        )
    return [_normalize_token(token) for token in domain]


class OdooDomain(list):
    """Pydantic-compatible Odoo domain that does not coerce operand scalars."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.no_info_plain_validator_function(
            lambda value: cls(normalize_domain_json(value)),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: list(value),
                info_arg=False,
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        return dict(ODOO_DOMAIN_JSON_SCHEMA)


# Re-exported so callers can tell a documented operator from an arbitrary string.
LOGICAL_OPERATORS = _LOGICAL_OPERATORS
