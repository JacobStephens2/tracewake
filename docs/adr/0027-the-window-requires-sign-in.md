---
status: accepted
---

# The window requires sign-in

The window used to ship no authentication of its own. It bound to loopback
and left the rest to the reverse proxy. That was enough while the Journal
stayed on a private host. It is not enough once the window faces a public
URL and people other than the operator are invited to read it.

The window now requires a session. An unauthenticated request to any page
303-redirects to sign-in; a valid email and password return the visitor to
the page they asked for; sign-out deletes the session row. There is no
registration page. The first admin is seeded by `web/seed-admin.py` at
deploy. `/healthz` stays open for the reverse proxy and systemd.

The loopback bind and the reverse proxy remain. They were never the session.
They are still the network shape: uvicorn listens on 127.0.0.1, TLS
terminates in front of it, and `__Host-` cookies with `Secure`, `HttpOnly`
and `SameSite=Lax` ride the public https origin. An environment toggle
(`WINDOW_COOKIE_SECURE=0`) relaxes only `Secure`, and only so the Attended
Preview can set a cookie on plain-HTTP loopback.

Passwords are argon2id with the stock pinned hasher (RFC 9106's second
recommended option). Session tokens are 256-bit, hashed at rest, rotated on
every sign-in, and expired on idle (30 minutes) and absolutely (12 hours) in
the read query. State-changing POSTs carry a synchronizer CSRF token stored
on the session row.

Roles (`admin`, `reader`) are a column on the account. This ADR does not
gate on them; the roles ticket does. The Operator remains exactly one.
Window Accounts are viewers of an Instance, not Operators.

This reverses the "window ships no authentication of its own" acceptance
criterion. The binding half of that criterion is unchanged.
