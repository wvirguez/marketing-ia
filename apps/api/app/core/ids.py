"""Public-ID generation per the BACKEND-01 convention
(`docs/backend/BACKEND-01-DOMAIN-MODEL.md` §3): a short, human-readable,
prefixed identifier — separate from the internal UUID primary key —
that is what actually appears in API responses and URLs.

Format: ``{PREFIX}-{12-character Crockford-base32 suffix}``. Crockford's
alphabet excludes visually ambiguous characters (no `I`, `L`, `O`, `U`),
which matters here because these IDs are meant to be readable/typeable
by a human (e.g. in a support conversation), not just machine-consumed.
"""

from __future__ import annotations

import secrets

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DEFAULT_LENGTH = 12


def generate_public_id(prefix: str, *, length: int = _DEFAULT_LENGTH) -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}-{suffix}"
