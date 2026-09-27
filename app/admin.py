"""Admin: manage users and all artworks."""
import secrets

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import models
from . import settings as site_settings
from .db import get_db
from .images import delete_renditions
from .auth import USERNAME_RE
from .security import admin_required, safe_local_url

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/")
@admin_required
def index():
    db = get_db()
    users = db.execute(
        """SELECT u.*, COUNT(a.id) AS n, COALESCE(SUM(a.is_published), 0) AS published
           FROM users u LEFT JOIN artworks a ON a.user_id = u.id
           GROUP BY u.id ORDER BY u.created_at"""
    ).fetchall()
    q = request.args.get("q", "").strip()[:100]
    page = max(1, request.args.get("page", 1, type=int) or 1)
    artworks, total = models.search_artworks(q=q, published_only=False, page=page, per_page=50)
    return render_template(
        "admin/index.html", users=users, artworks=artworks, total=total, q=q, page=page,
    )


@bp.post("/settings")
@admin_required
def update_settings():
    site_name = request.form.get("site_name", "").strip()[:60]
    if not site_name:
        flash("Gallery name is required.", "error")
        return redirect(url_for("admin.index"))
    site_settings.set_many({
        "site_name": site_name,
        "allow_registration": "1" if request.form.get("allow_registration") == "1" else "0",
    })
    get_db().commit()
    flash("Settings saved.", "success")
    return redirect(url_for("admin.index"))


def _temporary_password() -> str:
    # Easy to read aloud or type from a message; 5 x 4 chars from a 32-symbol
    # alphabet is ~100 bits. Must be changed on first sign-in anyway.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(5))


@bp.post("/users")
@admin_required
def create_user():
    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()[:60] or username
    if not USERNAME_RE.match(username):
        flash("Username must be 3–30 characters: lowercase letters, numbers, underscore.", "error")
        return redirect(url_for("admin.index"))
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        flash("That username is taken.", "error")
        return redirect(url_for("admin.index"))
    password = _temporary_password()
    db.execute(
        """INSERT INTO users (username, display_name, password_hash, is_admin, must_change_password)
           VALUES (?, ?, ?, ?, 1)""",
        (username, display_name, generate_password_hash(password), int(request.form.get("is_admin") == "1")),
    )
    db.commit()
    return render_template("admin/credentials.html", username=username, display_name=display_name,
                           password=password, created=True)


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    if user_id == g.user["id"]:
        flash("You can't change, deactivate or delete your own account.", "error")
        return redirect(url_for("admin.index"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    action = request.form.get("action")
    if action == "toggle_admin":
        db.execute("UPDATE users SET is_admin = 1 - is_admin WHERE id = ?", (user_id,))
    elif action == "toggle_active":
        db.execute("UPDATE users SET is_active = 1 - is_active WHERE id = ?", (user_id,))
    elif action == "reset_password":
        password = _temporary_password()
        # Changing the hash also signs the user out everywhere (see security.load_user).
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
        db.commit()
        return render_template("admin/credentials.html", username=user["username"],
                               display_name=user["display_name"], password=password, created=False)
    elif action == "delete":
        keys = [r["image_key"] for r in db.execute(
            "SELECT image_key FROM artworks WHERE user_id = ?", (user_id,)
        ).fetchall()]
        # Artworks, tags links and likes cascade from the user row.
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM artwork_tags)")
        db.commit()
        for key in keys:
            delete_renditions(key, current_app.config["UPLOAD_DIR"])
        flash(f"Deleted {user['display_name']} and {len(keys)} artwork{'s' if len(keys) != 1 else ''}.", "success")
        return redirect(url_for("admin.index"))
    else:
        abort(400)
    db.commit()
    flash(f"Updated {user['display_name']}.", "success")
    return redirect(url_for("admin.index"))


@bp.post("/art/<int:artwork_id>/feature")
@admin_required
def feature(artwork_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM artworks WHERE id = ?", (artwork_id,)).fetchone():
        abort(404)
    db.execute("UPDATE artworks SET is_featured = 1 - is_featured WHERE id = ?", (artwork_id,))
    db.commit()
    return redirect(safe_local_url(request.form.get("next"), url_for("admin.index")))
