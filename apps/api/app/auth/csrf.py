"""CSRF protection (synchronizer-token pattern), because authentication
uses a cookie.

SameSite=Lax on its own is a real mitigation but not a complete CSRF
architecture — it does not cover cross-site *same-site-navigable*
requests some browsers still allow, and older/misconfigured clients may
not enforce it at all. So: a per-session, server-issued secret
(``AuthSession.csrf_secret``) must also be presented back by the client
in a custom header on every authenticated state-changing request. An
attacker forging a cross-site request has the victim's cookie sent
automatically by the browser, but has no way to read the victim's CSRF
secret (same-origin policy blocks reading the ``/csrf`` response from
another origin), so they cannot supply a matching header value.

GET/HEAD/OPTIONS never require this (see ``app/auth/dependencies.py``);
every authenticated POST/PUT/PATCH/DELETE does. Register/login are
exempt from *this* check specifically because no session exists yet to
bind a CSRF secret to — see the module docstring in
``app/auth/service.py`` for that separate, documented pre-auth policy.
"""

from __future__ import annotations

CSRF_HEADER_NAME = "X-CSRF-Token"
