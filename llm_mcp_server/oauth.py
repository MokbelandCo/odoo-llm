"""OAuth 2.1 helpers for the MCP resource server and authorization server."""

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

from odoo import fields

_logger = logging.getLogger(__name__)

MCP_OAUTH_SCOPE = "mcp:tools"
DEFAULT_ACCESS_TOKEN_SECONDS = 3600
DEFAULT_REFRESH_TOKEN_SECONDS = 30 * 24 * 3600
DEFAULT_AUTH_CODE_SECONDS = 600
MAX_REDIRECT_URI_LENGTH = 2048


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def hash_secret(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def secrets_match(stored_hash, provided):
    if not stored_hash or provided is None:
        return False
    return hmac.compare_digest(stored_hash, hash_secret(provided))


def pkce_challenge_s256(code_verifier):
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(code_verifier, code_challenge, method="S256"):
    if not code_verifier or not code_challenge:
        return False
    method = (method or "S256").upper()
    if method == "S256":
        return hmac.compare_digest(pkce_challenge_s256(code_verifier), code_challenge)
    if method == "PLAIN":
        return hmac.compare_digest(code_verifier, code_challenge)
    return False


def canonical_resource_uri(url):
    """RFC 8707 / MCP canonical URI: lowercase scheme+host, no fragment, no trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    path = parsed.path.rstrip("/") or ""
    netloc = parsed.netloc.lower()
    scheme = parsed.scheme.lower()
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def resource_uris_match(left, right):
    return canonical_resource_uri(left) == canonical_resource_uri(right)


def is_safe_redirect_uri(uri):
    if not uri or len(uri) > MAX_REDIRECT_URI_LENGTH:
        return False
    parsed = urlparse(uri)
    if parsed.fragment:
        return False
    if parsed.scheme == "https":
        return bool(parsed.netloc)
    if parsed.scheme == "http":
        hostname = (parsed.hostname or "").lower()
        return hostname in ("localhost", "127.0.0.1", "::1")
    return False


def expiry_datetime(seconds):
    return fields.Datetime.now() + timedelta(seconds=seconds)


def is_expired(when):
    if not when:
        return True
    return fields.Datetime.now() >= when


def parse_basic_client_auth(authorization_header):
    if not authorization_header:
        return None, None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None, None
    try:
        decoded = base64.b64decode(parts[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None, None
    if ":" not in decoded:
        return None, None
    client_id, client_secret = decoded.split(":", 1)
    return client_id, client_secret


def www_authenticate_header(resource_metadata_url, error=None, description=None):
    parts = ['Bearer realm="mcp"', f'resource_metadata="{resource_metadata_url}"']
    if error:
        parts.append(f'error="{error}"')
    if description:
        escaped = description.replace('"', "'")
        parts.append(f'error_description="{escaped}"')
    return ", ".join(parts)
