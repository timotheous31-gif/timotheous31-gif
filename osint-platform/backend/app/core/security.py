"""Password hashing, session tokens and CSRF tokens.

Everything cryptographic in this platform lives here, and all of it is a thin
call into a library that somebody qualified wrote. Nothing in this module
invents a construction: passwords go to Argon2id via ``argon2-cffi``, tokens
come from :mod:`secrets`, comparisons go through :func:`hmac.compare_digest`,
and the CSRF token is an HMAC-SHA256 of the session id.

Four decisions worth stating, because each is a place where the obvious cheaper
option is wrong.

**Argon2id, not bcrypt or PBKDF2.** A memory-hard KDF is what makes an offline
attack on a stolen password table expensive on the hardware an attacker
actually has. The parameters are the library's own current defaults rather than
numbers picked here, and :func:`needs_rehash` lets a deployment move to stronger
parameters on next login without a password reset.

**A wrong password and an unknown account cost the same.** :func:`verify_password`
is always called — against a real verifier when the account exists and against a
fixed dummy when it does not — so response time does not answer "does this
address have an account here".

**Session tokens are opaque and stored hashed.** There is no signed, decodable
token: the database is the authority on whether a session is live, so logging
out actually ends it. Reading the ``user_sessions`` table yields SHA-256 digests
and nothing replayable.

**CSRF is a real token, not a claim about SameSite.** ``SameSite=Lax`` stops the
common cross-site form post, but it is one browser's policy, it does not cover a
same-site subdomain or a permissive intermediary, and it is not something to
stake a customer's investigation data on. The token here is derived from the
session, so it cannot be forged by an attacker who cannot read the session.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.logging import get_logger

log = get_logger(__name__)

#: One hasher for the process. Parameters are argon2-cffi's current defaults,
#: deliberately not hand-tuned here — the library tracks the guidance.
_hasher = PasswordHasher()

#: A pre-computed verifier for an address that does not exist, so an unknown
#: account costs the same Argon2 work as a known one. Computed once at import
#: rather than per request, which would be the expensive mistake.
_DUMMY_HASH = _hasher.hash("a password nobody has: " + secrets.token_hex(16))

#: Long enough that a passphrase is the natural choice and short enough that a
#: password manager's output is never rejected. No composition rules: they push
#: people toward "Password1!" and buy nothing.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

#: Bytes of entropy in a session token. 32 bytes is 256 bits.
TOKEN_BYTES = 32


class PasswordPolicyError(ValueError):
    """A password that cannot be accepted, with a reason a human can act on."""


def normalize_password(password: str) -> str:
    """NFKC-normalise so the same typed passphrase always hashes the same.

    Two visually identical strings can differ in their Unicode encoding — a
    composed 'é' against an 'e' plus a combining accent. Without normalising,
    a password typed on one keyboard layout can fail to verify when typed on
    another, which reads to the user as "the system is broken".
    """
    return unicodedata.normalize("NFKC", password)


def check_password_policy(password: str) -> None:
    """Raise :class:`PasswordPolicyError` if ``password`` is unusable."""
    normalized = normalize_password(password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters. "
            f"A passphrase of a few unrelated words is easier to remember and "
            f"stronger than a short password with substitutions."
        )
    if len(normalized) > MAX_PASSWORD_LENGTH:
        # Not a strength rule: an unbounded input is a denial-of-service vector
        # against a deliberately expensive hash.
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def hash_password(password: str) -> str:
    """Return an Argon2id verifier. The password itself is never retained."""
    check_password_policy(password)
    return _hasher.hash(normalize_password(password))


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check ``password``, in constant work whether or not the account exists.

    ``password_hash`` of ``None`` means "no such account". The dummy verifier is
    still exercised, so the caller can answer with one uniform message and one
    uniform latency.
    """
    candidate = password_hash or _DUMMY_HASH
    try:
        _hasher.verify(candidate, normalize_password(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True when a stored verifier uses weaker parameters than current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:  # pragma: no cover - corrupt row
        return True


def new_session_token() -> str:
    """A fresh opaque session token. Never stored; only its digest is."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    """The stored form of a session token.

    SHA-256 rather than Argon2 on purpose: this value has full machine entropy,
    so there is no dictionary to grind, and an authentication check runs on every
    request. A memory-hard hash here would buy nothing and cost every call.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_id: str, secret: str) -> str:
    """A CSRF token bound to one session.

    Derived rather than random so the server holds no extra state, and keyed by
    the deployment secret so it cannot be produced by an attacker who can guess
    a session id but cannot read the secret.
    """
    return hmac.new(
        secret.encode("utf-8"), f"csrf:{session_id}".encode(), hashlib.sha256
    ).hexdigest()


def tokens_match(left: str | None, right: str | None) -> bool:
    """Constant-time comparison that tolerates ``None``."""
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)
