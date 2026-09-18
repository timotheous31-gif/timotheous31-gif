"""Request and response shapes for two-factor authentication.

One rule shapes every model here: **the secret leaves exactly once.** It appears
in :class:`MfaEnrollmentRead`, which is the response to starting enrolment and
nothing else. No other model carries it, so no other route can return it by
accident — not ``/auth/me``, not the status endpoint, not an error body.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PasswordConfirmation(BaseModel):
    """Re-authentication for a change to the account's own security settings.

    Required even though the caller is already signed in. An unlocked browser
    somebody walked up to is the threat: without this, it is enough to turn the
    second factor off.
    """

    password: str = Field(min_length=1, max_length=1024)


class MfaEnrollmentRead(BaseModel):
    """The one response that carries the secret.

    ``otpauth_uri`` is rendered as a QR code by the client. It contains the
    secret, which is why this is the only model with either field: an image
    generated server-side would put the same secret through another encoder and
    another cache for no benefit.
    """

    secret: str
    otpauth_uri: str
    #: Nothing is required of the account until a code confirms this.
    confirmed: bool = False


class MfaConfirm(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class MfaEnabledRead(BaseModel):
    """What comes back when the factor becomes real: the recovery codes, once."""

    enabled: bool = True
    #: Plaintext here and nowhere else. Only Argon2 verifiers are stored.
    recovery_codes: list[str]


class MfaChallenge(BaseModel):
    """A sign-in challenge answer: an authenticator code, or a recovery code."""

    code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=32)


class MfaStatusRead(BaseModel):
    """Whether the factor is on. Deliberately carries no secret material."""

    enabled: bool
    confirmed_at: datetime | None = None
    recovery_codes_remaining: int = 0
