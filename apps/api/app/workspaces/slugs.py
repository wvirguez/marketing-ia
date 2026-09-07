"""Slug generation for Workspace.

A slug is derived from the workspace name plus a short random suffix —
the suffix exists purely to make collisions negligible without a
retry-on-conflict loop; it is not meant to be memorable on its own.
"""

from __future__ import annotations

import re
import secrets

_SLUG_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def generate_workspace_slug(name: str) -> str:
    base = _NON_SLUG_CHARS.sub("-", name.strip().lower()).strip("-") or "workspace"
    suffix = "".join(secrets.choice(_SLUG_SUFFIX_ALPHABET) for _ in range(6))
    return f"{base}-{suffix}"
