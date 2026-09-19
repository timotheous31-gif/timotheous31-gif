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

**TOTP is RFC 6238 via pyotp, not a hand-rolled HMAC loop.** The construction is
short enough to be tempting to write here and exactly the kind of thing that is
subtly wrong for years — a drift window applied in one direction, a comparison
that is not constant time, a base32 decoder that accepts what an authenticator
app would not. The one thing this module adds on top is replay rejection, which
the RFC names as the verifier's job and which no library can do for us because it
needs the account's own last-used counter.

**Recovery codes are hashed with Argon2, unlike session tokens.** A session token
has full machine entropy, so :func:`token_digest` uses SHA-256 and says why. A
recovery code is typed by a human under stress, so it is shorter — and a shorter
secret in a stolen database is worth grinding. Verification happens rarely and is
rate-limited, so the memory-hard cost is affordable exactly where it is needed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import unicodedata
from datetime import UTC, datetime

import pyotp
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


# ------------------------------------------------------------ TOTP (RFC 6238)

#: Seconds per TOTP step. 30 is the RFC default and what every authenticator app
#: assumes; changing it would silently break enrolment against real apps.
TOTP_PERIOD = 30
#: Digits in a code. Six, for the same reason.
TOTP_DIGITS = 6
#: How many steps either side of now are accepted, to tolerate clock drift
#: between the server and the phone. One step is ±30s, which is the usual
#: compromise: enough for a phone that has never synced, small enough that a code
#: shoulder-surfed from a screen is not usable for long.
TOTP_DRIFT_STEPS = 1
#: Characters in a recovery code, excluding the separating dash.
RECOVERY_CODE_LENGTH = 10
#: How many recovery codes are issued at enrolment.
RECOVERY_CODE_COUNT = 10
#: Alphabet for recovery codes: Crockford-style, without the characters people
#: mistype when reading them off paper (I/L/O/U and the digits they look like).
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


class MfaError(ValueError):
    """A TOTP or recovery-code check that could not be accepted."""


def new_totp_secret() -> str:
    """A fresh base32 TOTP secret.

    Generated by pyotp so it is the length and alphabet every authenticator app
    expects. Returned once, at enrolment, and never again.
    """
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans.

    The URI *contains the secret*, which is why it is returned only during
    enrolment and never afterwards. The QR image is rendered client-side from
    this string: sending a PNG would put the same secret through an extra
    encoder, an extra cache and an extra log line for no benefit.
    """
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD).provisioning_uri(
        name=account, issuer_name=issuer
    )


def current_totp_step(at: float | None = None) -> int:
    """The RFC 6238 counter for ``at`` (default: now).

    Exposed because replay rejection is the verifier's job and needs to record
    which step was spent.
    """
    return int((at if at is not None else time.time()) // TOTP_PERIOD)


def verify_totp(secret: str, code: str, *, last_used_step: int | None = None) -> int:
    """Check ``code`` and return the step it consumed.

    Raises :class:`MfaError` rather than returning a bool, so a caller cannot
    accidentally treat the falsy ``0`` step as a failure.

    ``last_used_step`` is what makes this a verifier rather than a comparison. A
    TOTP code stays valid for its whole window, so without it the same six digits
    — read over a shoulder, captured from a phishing page, replayed from a proxy
    — work again for up to a minute. Any step at or below the last one spent by
    this account is refused even when the arithmetic is correct.
    """
    cleaned = "".join(character for character in (code or "") if character.isdigit())
    if len(cleaned) != TOTP_DIGITS:
        raise MfaError(f"A code is {TOTP_DIGITS} digits.")

    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD)
    now = current_totp_step()
    for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        step = now + offset
        # pyotp compares in constant time and does the base32 decoding.
        moment = datetime.fromtimestamp(step * TOTP_PERIOD, tz=UTC)
        if totp.verify(cleaned, for_time=moment):
            if last_used_step is not None and step <= last_used_step:
                raise MfaError(
                    "That code has already been used. Wait for your authenticator "
                    "app to show the next one."
                )
            return step
    raise MfaError("That code is not valid. Check your authenticator app and try again.")


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh recovery codes, in the form shown to the user once.

    Grouped with a dash purely so a human can read them back off paper; the dash
    is not part of the secret and :func:`normalize_recovery_code` removes it.
    """
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        half = RECOVERY_CODE_LENGTH // 2
        codes.append(f"{raw[:half]}-{raw[half:]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    """A recovery code as it is compared: upper case, no separators.

    Somebody reading a code off paper will type the dash, or not, or a space.
    None of that should be the difference between getting back into an account
    and not.
    """
    return "".join(character for character in (code or "").upper() if character.isalnum())


def hash_recovery_code(code: str) -> str:
    """The stored form of a recovery code. The code itself is never retained."""
    return _hasher.hash(normalize_recovery_code(code))


def verify_recovery_code(code: str, code_hash: str) -> bool:
    """Whether ``code`` matches ``code_hash``."""
    try:
        _hasher.verify(code_hash, normalize_recovery_code(code))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True
