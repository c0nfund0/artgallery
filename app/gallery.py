"""Public, no-login-required views."""
import secrets
from math import ceil
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, g, jsonify, make_response, render_template, request,
    send_from_directory, session,
)

from . import models
from .db import get_db
from .images import RENDITIONS
from .security import VISITOR_COOKIE

bp = Blueprint("gallery", __name__)


def _page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _listing(template, extra=None, **filters):
    page = _page()
    per_page = current_app.config["PER_PAGE"]
    sort = request.args.get("sort", "new")
    rows, total = models.search_artworks(page=page, per_page=per_page, sort=sort, **filters)
    ctx = dict(
        artworks=rows,
        tags=models.tags_for([r["id"] for r in rows]),
        page=page,
        pages=max(1, ceil(total / per_page)),
        total=total,
        sort=sort,
    )
    # Infinite scroll fetches just the grid items.
    if request.args.get("partial") == "1":
        return render_template("partials/grid_items.html", **ctx, **(extra or {}))
    ctx.update({k: v for k, v in filters.items() if k != "user_id"})
    return render_template(template, **ctx, **(extra or {}))


@bp.get("/")
def index():
    featured = []
    if request.args.get("partial") != "1" and _page() == 1:
        featured, _ = models.search_artworks(featured=True, per_page=5)
        if not featured:  # fall back to the most appreciated work
            featured, _ = models.search_artworks(sort="popular", per_page=5)
    stats = get_db().execute(
        """SELECT (SELECT COUNT(*) FROM artworks WHERE is_published = 1) AS works,
                  (SELECT COUNT(DISTINCT user_id) FROM artworks WHERE is_published = 1) AS artists"""
    ).fetchone()
    return _listing("gallery/index.html", extra={"featured": featured, "stats": stats})


@bp.get("/search")
def search():
    q = request.args.get("q", "").strip()[:100]
    tag = request.args.get("tag", "").strip()[:40]
    medium = request.args.get("medium", "").strip()[:60]
    return _listing("gallery/search.html", q=q, tag=tag, medium=medium)


@bp.get("/artists")
def artists():
    rows = get_db().execute(
        """SELECT u.username, u.display_name, u.bio, COUNT(a.id) AS n,
                  (SELECT image_key FROM artworks x WHERE x.user_id = u.id AND x.is_published = 1
                   ORDER BY x.is_featured DESC, x.views DESC LIMIT 1) AS cover,
                  (SELECT color FROM artworks x WHERE x.user_id = u.id AND x.is_published = 1
                   ORDER BY x.is_featured DESC, x.views DESC LIMIT 1) AS cover_color
           FROM users u JOIN artworks a ON a.user_id = u.id AND a.is_published = 1
           WHERE u.is_active = 1
           GROUP BY u.id ORDER BY n DESC, u.display_name"""
    ).fetchall()
    return render_template("gallery/artists.html", artists=rows)


@bp.get("/artist/<username>")
def artist(username):
    user = get_db().execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username.lower(),)
    ).fetchone()
    if not user:
        abort(404)
    return _listing("gallery/artist.html", extra={"artist": user}, user_id=user["id"])


@bp.get("/art/<int:artwork_id>")
def artwork(artwork_id):
    art = models.get_artwork(artwork_id)
    if not art or not models.can_view(g.user, art):
        abort(404)
    db = get_db()
    # Count a view once per session per artwork, and not for the owner.
    seen = set(session.get("seen", []))
    if art["is_published"] and artwork_id not in seen and not models.can_manage(g.user, art):
        db.execute("UPDATE artworks SET views = views + 1 WHERE id = ?", (artwork_id,))
        db.commit()
        seen.add(artwork_id)
        session["seen"] = list(seen)[-200:]
    tags = models.tags_for([artwork_id])[artwork_id]
    visitor = request.cookies.get(VISITOR_COOKIE)
    liked = bool(visitor) and db.execute(
        "SELECT 1 FROM likes WHERE artwork_id = ? AND visitor = ?", (artwork_id, visitor)
    ).fetchone() is not None
    more, _ = models.search_artworks(user_id=art["user_id"], per_page=7)
    more = [m for m in more if m["id"] != artwork_id][:6]
    # Prev/next within the artist's published work, for keyboard navigation.
    nav = db.execute(
        """SELECT
             (SELECT id FROM artworks WHERE user_id = :u AND is_published = 1 AND id < :id ORDER BY id DESC LIMIT 1) AS prev,
             (SELECT id FROM artworks WHERE user_id = :u AND is_published = 1 AND id > :id ORDER BY id ASC LIMIT 1) AS next""",
        {"u": art["user_id"], "id": artwork_id},
    ).fetchone()
    return render_template(
        "gallery/artwork.html", art=art, tags=tags, liked=liked, more=more, nav=nav,
        can_manage=models.can_manage(g.user, art),
    )


@bp.post("/art/<int:artwork_id>/like")
def like(artwork_id):
    art = models.get_artwork(artwork_id)
    if not art or not art["is_published"]:
        abort(404)
    visitor = request.cookies.get(VISITOR_COOKIE) or secrets.token_urlsafe(16)
    db = get_db()
    existing = db.execute(
        "SELECT 1 FROM likes WHERE artwork_id = ? AND visitor = ?", (artwork_id, visitor)
    ).fetchone()
    if existing:
        db.execute("DELETE FROM likes WHERE artwork_id = ? AND visitor = ?", (artwork_id, visitor))
    else:
        db.execute("INSERT INTO likes (artwork_id, visitor) VALUES (?, ?)", (artwork_id, visitor))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM likes WHERE artwork_id = ?", (artwork_id,)).fetchone()[0]
    resp = make_response(jsonify(liked=not existing, count=count))
    resp.set_cookie(
        VISITOR_COOKIE, visitor, max_age=60 * 60 * 24 * 365 * 2, httponly=True, samesite="Lax",
        secure=current_app.config["SESSION_COOKIE_SECURE"],
    )
    return resp


@bp.get("/media/<key>/<size>.webp")
def media(key, size):
    if size not in RENDITIONS:
        abort(404)
    art = get_db().execute(
        "SELECT a.user_id, a.is_published, u.is_active AS artist_active FROM artworks a "
        "JOIN users u ON u.id = a.user_id WHERE a.image_key = ?", (key,)
    ).fetchone()
    if not art or not models.can_view(g.user, art):
        abort(404)
    resp = send_from_directory(Path(current_app.config["UPLOAD_DIR"]), f"{key}_{size}.webp", mimetype="image/webp", max_age=0)
    # Keep public caching short so unpublishing takes effect quickly.
    if art["is_published"]:
        resp.cache_control.public = True
        resp.cache_control.max_age = 3600
    else:
        resp.cache_control.private = True
        resp.cache_control.no_store = True
    return resp
