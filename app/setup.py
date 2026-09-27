"""First-run setup: create the admin account.

No users exist on a fresh install. Until an admin is created, every page
redirects here. To stop a stranger from claiming a freshly started public
server, the form requires a one-time setup code that is printed to the server
log (or preset via the SETUP_TOKEN environment variable).
"""
import hmac
import os
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import settings as site_settings
from .auth import validate_new_account
from .db import connect, get_db
from .keys import create_private_file
from .security import client_ip, login_limiter

bp = Blueprint("setup", __name__)


def _token_file(app) -> Path:
    return Path(app.config["DATA_DIR"]) / "setup_token"


def setup_needed() -> bool:
    app = current_app._get_current_object()
    # Users are never fully deleted, so once setup is done it stays done.
    if app.extensions.get("setup_done"):
        return False
    if get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone():
        app.extensions["setup_done"] = True
        return False
    return True


def setup_token(app) -> str:
    env = os.environ.get("SETUP_TOKEN")
    if env:
        return env
    f = _token_file(app)
    if not f.exists():
        create_private_file(f, "-".join(secrets.token_hex(3) for _ in range(3)))
    return f.read_text().strip()


def announce_if_needed(app) -> None:
    conn = connect(app.config["DATABASE"])
    try:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            _token_file(app).unlink(missing_ok=True)
            return
    finally:
        conn.close()
    token = setup_token(app)
    banner = (
        "\n" + "=" * 64 +
        "\n  FIRST RUN: no admin account exists yet."
        "\n  Open the site in a browser and enter this setup code:"
        f"\n\n      {token}\n" +
        "=" * 64
    )
    print(banner, flush=True)


@bp.route("/setup", methods=["GET", "POST"])
def index():
    if not setup_needed():
        abort(404)
    errors, form = [], request.form
    if request.method == "POST":
        if not login_limiter.hit(client_ip()):
            abort(429)
        sent = form.get("setup_token", "").strip().lower()
        if not hmac.compare_digest(sent, setup_token(current_app).lower()):
            errors.append("Setup code is incorrect. You'll find it in the server log.")
        username, display_name, password, account_errors = validate_new_account(form)
        errors += account_errors
        site_name = form.get("site_name", "").strip()[:60]
        if not site_name:
            errors.append("Gallery name is required.")
        if not errors:
            db = get_db()
            db.execute("BEGIN IMMEDIATE")  # serialise concurrent setup attempts
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                db.rollback()
                abort(404)
            cur = db.execute(
                "INSERT INTO users (username, display_name, password_hash, is_admin) VALUES (?, ?, ?, 1)",
                (username, display_name, generate_password_hash(password)),
            )
            site_settings.set_many({
                "site_name": site_name,
                "allow_registration": "1" if form.get("allow_registration") == "1" else "0",
            })
            db.commit()
            _token_file(current_app).unlink(missing_ok=True)
            current_app.extensions["setup_done"] = True

            from .auth import start_session

            start_session(db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
            flash("Your gallery is ready. Welcome, admin!", "success")
            return redirect(url_for("studio.dashboard"))
    return render_template("setup.html", errors=errors, form=form)
