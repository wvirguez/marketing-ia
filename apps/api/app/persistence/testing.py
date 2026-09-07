"""Test-database safety guard (BACKEND-03 §12).

Any code path that is about to run destructive operations against a
database (drop tables, truncate, ``alembic downgrade``, etc.) must call
``assert_safe_test_database_url`` first. It fails closed: anything that
does not clearly look like a disposable, local, test-scoped database is
rejected, rather than trusting the *name* of the environment variable
(``TEST_DATABASE_URL``) alone — a variable name is not a safeguard by
itself, since nothing stops it from being set to a real database by
mistake.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_REQUIRED_SUBSTRING = "_test"
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a URL destined for destructive test operations does
    not look like a disposable, local, test-scoped database."""


def assert_safe_test_database_url(database_url: str) -> None:
    """Fails closed unless the URL both:

    1. names a database containing ``_test`` (case-insensitive) — so a
       plain ``impulso`` or ``postgres`` database is refused even if some
       caller mislabels it as "the test database"; and
    2. points at a local host — so a correctly-named database on a
       remote/shared server is still refused, since a name match alone
       is not proof of who else might be relying on that instance.

    Raises ``UnsafeTestDatabaseError`` with a message safe to log (it
    never includes credentials — only the host and database name, which
    is exactly what a developer needs to see to fix a misconfiguration).
    """
    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    host = parsed.hostname or ""

    if _REQUIRED_SUBSTRING not in database_name.lower():
        raise UnsafeTestDatabaseError(
            f"Refusing to run destructive test operations: database name "
            f"{database_name!r} does not contain {_REQUIRED_SUBSTRING!r}. "
            "Point TEST_DATABASE_URL at a database whose name makes it "
            "unambiguous that it is disposable test data."
        )

    if host.lower() not in _ALLOWED_HOSTS:
        raise UnsafeTestDatabaseError(
            f"Refusing to run destructive test operations against host "
            f"{host!r}: only {sorted(_ALLOWED_HOSTS)} are treated as safe, "
            "local, disposable test targets."
        )
