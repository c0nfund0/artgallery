"""Cross-cutting security: current user, CSRF, headers, rate limiting."""
import functools
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import abort, g, redirect, request, session, url_for
from markupsafe import Markup

from . import settings as site_settings

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
    def require_setup():
        # Until an admin exists, every page leads to the first-run setup.
        from .setup import setup_needed

        if request.endpoint in ("setup.index", "static", "healthz"):
            return
        if setup_needed():
            return redirect(url_for("setup.index"))

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
            "site_name": site_settings.get("site_name"),
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
    """Public artist sign-up, toggled by admins. The admin itself is created via /setup."""
    return site_settings.get("allow_registration") == "1"


def safe_local_url(target: str | None, fallback: str) -> str:
    """Return ``target`` only if it is a plain path on this site, else ``fallback``.

    Used for every user-controlled redirect (``?next=``, hidden ``next`` fields).
    Rejects absolute URLs, protocol-relative ``//host``, backslashes (which some
    browsers treat as slashes) and control characters.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return fallback
    if "\\" in target or not target.isprintable():
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    return target


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
# Anonymous likes create rows; bound how fast one client can add them.
like_limiter = RateLimiter(limit=30, window=60)


def client_ip() -> str:
    return request.remote_addr or "unknown"
