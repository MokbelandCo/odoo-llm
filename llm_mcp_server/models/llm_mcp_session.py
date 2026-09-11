import logging
import uuid

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class LLMMCPSession(models.Model):
    _name = "llm.mcp.session"
    _description = "MCP Session Management"

    _sql_constraints = [
        ("session_id_unique", "UNIQUE(session_id)", "Session ID must be unique")
    ]

    # Required fields
    session_id = fields.Char(required=True, index=True, help="UUID hex format")
    state = fields.Selection(
        [
            ("not_initialized", "Not Initialized"),
            ("initializing", "Initializing"),
            ("initialized", "Initialized"),
        ],
        default="not_initialized",
        required=True,
    )

    user_id = fields.Many2one(
        "res.users",
        index=True,
        help="User associated with session, set when Bearer token is available",
    )

    # Client data
    client_capabilities = fields.Json(
        help="Client capabilities from initialize request"
    )
    client_info = fields.Json(help="Client information (name, version, etc.)")
    protocol_version = fields.Char(help="MCP protocol version requested by client")
    last_method = fields.Char(readonly=True)
    last_request_id = fields.Char(readonly=True)
    last_request_at = fields.Datetime(readonly=True)
    initialized_at = fields.Datetime(readonly=True)
    request_count = fields.Integer(default=0, readonly=True)
    last_user_agent = fields.Char(readonly=True)
    last_accept = fields.Char(readonly=True)
    last_remote_address = fields.Char(readonly=True)
    initialization_diagnostic = fields.Char(
        compute="_compute_initialization_diagnostic"
    )

    @api.depends("state", "last_method")
    def _compute_initialization_diagnostic(self):
        for session in self:
            if session.state == "initialized":
                session.initialization_diagnostic = "Initialization completed."
            elif (
                session.state == "initializing" and session.last_method == "initialize"
            ):
                session.initialization_diagnostic = (
                    "Waiting for the client to send notifications/initialized."
                )
            elif session.state == "initializing":
                session.initialization_diagnostic = "The client continued without completing the initialized notification."
            else:
                session.initialization_diagnostic = "Initialize has not completed."

    @api.model
    def generate_session_id(self):
        """Generate a new session ID in UUID hex format"""
        return uuid.uuid4().hex

    @api.model
    def validate_session_id(self, session_id):
        """Validate session ID format (ASCII 0x21-0x7E only)"""
        if not session_id:
            return False

        # Check if all characters are in ASCII printable range (0x21-0x7E)
        try:
            return all(0x21 <= ord(char) <= 0x7E for char in session_id)
        except (TypeError, ValueError):
            return False

    @api.model
    def get_session(self, session_id):
        """Get existing session by ID and optional user_id"""
        if not session_id:
            return self.browse()

        # Validate session_id format
        if not self.validate_session_id(session_id):
            raise ValidationError(f"Invalid session ID format: {session_id}")

        # Build search domain
        domain = [("session_id", "=", session_id)]

        return self.search(domain, limit=1)

    @api.model
    def create_new_session(self, user_id=None):
        """Create a new session for initialize method (only for stateful mode)"""
        # Always generate a new session_id
        session_id = self.generate_session_id()

        # Create new session (always starts as not_initialized for stateful mode)
        session_vals = {
            "session_id": session_id,
            "state": "not_initialized",
        }
        if user_id:
            session_vals["user_id"] = user_id

        session = self.create(session_vals)

        return session

    def record_request(
        self,
        method,
        request_id=None,
        user_agent=None,
        accept=None,
        remote_address=None,
    ):
        """Store non-secret request metadata to diagnose stalled clients."""
        for session in self:
            session.write(
                {
                    "last_method": method,
                    "last_request_id": (
                        str(request_id) if request_id is not None else False
                    ),
                    "last_request_at": fields.Datetime.now(),
                    "request_count": session.request_count + 1,
                    "last_user_agent": user_agent,
                    "last_accept": accept,
                    "last_remote_address": remote_address,
                }
            )

    def is_method_allowed(self, method):
        """Check if method is allowed in current session state (stateful mode only)"""
        self.ensure_one()

        # Define allowed methods per state for stateful mode
        allowed_methods = {
            "not_initialized": ["initialize", "ping"],
            "initializing": [
                "*"
            ],  # TODO: temporarily allow, because claude desktop is sending both notifications/initialize and tools/list at the similar time and causing race condition
            "initialized": ["*"],  # Allow any method when initialized
        }

        allowed = allowed_methods.get(self.state, [])
        return "*" in allowed or method in allowed

    def transition_to(self, new_state):
        """Transition session to new state with validation"""
        self.ensure_one()

        # Define valid transitions
        valid_transitions = {
            "not_initialized": ["initializing"],
            "initializing": ["initialized"],
            "initialized": [],  # Terminal state
        }

        if new_state not in valid_transitions.get(self.state, []):
            raise ValidationError(
                f"Invalid state transition from '{self.state}' to '{new_state}'"
            )

        values = {"state": new_state}
        if new_state == "initialized":
            values["initialized_at"] = fields.Datetime.now()
        self.write(values)

    def terminate(self):
        """Terminate the session (delete it)"""
        self.ensure_one()
        session_id = self.session_id
        self.unlink()
        _logger.info(f"Terminated MCP session {session_id}")
