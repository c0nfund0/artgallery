"""Admin: manage users and all artworks."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import models
from .db import get_db
from .security import admin_required

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


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    if user_id == g.user["id"]:
        flash("You can't change your own admin status or deactivate yourself.", "error")
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
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("admin.index"))
