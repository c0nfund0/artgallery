"""Cross-cutting security: current user, CSRF, headers, rate limiting."""
import functools
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import abort, g, redirect, request, session, url_for
from markupsafe import Markup

VISITOR_COOKIE = "visitor"


def init_app(app):
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid is not None:
            from .db import get_db

            user = get_db().execute(
                "SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)
            ).fetchone()
            # Invalidate sessions whose password changed since login.
            if user and hmac.compare_digest(session.get("pwv", ""), password_version(user)):
                g.user = user
            else:
                session.clear()

    @app.before_request
    def check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if app.config.get("TESTING") and app.config.get("CSRF_DISABLED"):
                return
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            if not sent or not hmac.compare_digest(sent, csrf_token()):
                abort(400, "Invalid or missing CSRF token")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.context_processor
    def inject():
        return {
            "csrf_field": lambda: Markup(
                f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
            ),
            "csrf_token": csrf_token,
            "site_name": app.config["SITE_NAME"],
            "allow_registration": registration_open(),
        }


def password_version(user) -> str:
    """Short fingerprint of the password hash; changes when the password does."""
    return user["password_hash"][-16:]


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def registration_open() -> bool:
    from flask import current_app

    from .db import get_db

    if current_app.config["ALLOW_REGISTRATION"]:
        return True
    # Always allow the very first account (becomes admin).
    return get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


class RateLimiter:
    """Simple in-process sliding-window limiter (per worker)."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self.lock:
            self.hits.clear()


login_limiter = RateLimiter(limit=10, window=15 * 60)
upload_limiter = RateLimiter(limit=60, window=60 * 60)


def client_ip() -> str:
    return request.remote_addr or "unknown"
