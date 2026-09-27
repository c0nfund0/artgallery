import re
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .security import client_ip, login_limiter, login_required, password_version, registration_open

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")
# Pre-computed hash so failed lookups take as long as real ones (no user enumeration by timing).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _safe_next(target: str | None) -> str:
    if target:
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("studio.dashboard")


def validate_new_account(form) -> tuple[str, str, str, list[str]]:
    """Shared by registration and first-run setup."""
    errors = []
    username = form.get("username", "").strip().lower()
    display_name = form.get("display_name", "").strip()[:60] or username
    password = form.get("password", "")
    if not USERNAME_RE.match(username):
        errors.append("Username must be 3–30 characters: lowercase letters, numbers, underscore.")
    if len(password) < 10:
        errors.append("Password must be at least 10 characters.")
    if password != form.get("password2", ""):
        errors.append("Passwords do not match.")
    return username, display_name, password, errors


def start_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["pwv"] = password_version(user)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("studio.dashboard"))
    error = None
    if request.method == "POST":
        if not login_limiter.hit(client_ip()):
            abort(429)
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            start_session(user)
            flash(f"Welcome back, {user['display_name']}.", "success")
            return redirect(_safe_next(request.args.get("next")))
        if not user:
            check_password_hash(_DUMMY_HASH, password)
        error = "Incorrect username or password."
    return render_template("auth/login.html", error=error)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("studio.dashboard"))
    if not registration_open():
        abort(404)
    errors, form = [], request.form
    if request.method == "POST":
        if not login_limiter.hit(client_ip()):
            abort(429)
        username, display_name, password, errors = validate_new_account(form)
        db = get_db()
        if not errors and db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            errors.append("That username is taken.")
        if not errors:
            cur = db.execute(
                "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?)",
                (username, display_name, generate_password_hash(password)),
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
            start_session(user)
            flash("Your studio is ready. Upload your first piece!", "success")
            return redirect(url_for("studio.dashboard"))
    return render_template("auth/register.html", errors=errors, form=form)


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("gallery.index"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    db = get_db()
    errors = []
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            display_name = request.form.get("display_name", "").strip()[:60]
            bio = request.form.get("bio", "").strip()[:1000]
            website = request.form.get("website", "").strip()[:200]
            if website and not re.match(r"^https?://", website):
                website = "https://" + website
            if not display_name:
                errors.append("Display name is required.")
            else:
                db.execute(
                    "UPDATE users SET display_name = ?, bio = ?, website = ? WHERE id = ?",
                    (display_name, bio, website, g.user["id"]),
                )
                db.commit()
                flash("Profile updated.", "success")
                return redirect(url_for("auth.account"))
        elif action == "password":
            if not check_password_hash(g.user["password_hash"], request.form.get("current", "")):
                errors.append("Current password is incorrect.")
            new = request.form.get("new", "")
            if len(new) < 10:
                errors.append("New password must be at least 10 characters.")
            if new != request.form.get("new2", ""):
                errors.append("New passwords do not match.")
            if not errors:
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), g.user["id"]),
                )
                db.commit()
                user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
                start_session(user)  # other sessions are invalidated
                flash("Password changed. Other sessions have been signed out.", "success")
                return redirect(url_for("auth.account"))
    return render_template("auth/account.html", errors=errors)
