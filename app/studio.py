"""Logged-in artist area: upload, edit, publish/unpublish, delete."""
import datetime as dt
import shutil
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import models
from .db import get_db
from .images import ImageError, delete_renditions, process_upload
from .security import client_ip, login_required, safe_local_url, upload_limiter

bp = Blueprint("studio", __name__, url_prefix="/studio")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _owned_artwork(artwork_id: int):
    art = models.get_artwork(artwork_id)
    if not art:
        abort(404)
    if not models.can_manage(g.user, art):
        abort(403)
    return art


def _parse_meta(form) -> tuple[dict, list[str]]:
    errors = []
    title = form.get("title", "").strip()[:120]
    year_raw = form.get("year", "").strip()
    year = None
    if year_raw:
        try:
            year = int(year_raw)
            if not -10000 <= year <= dt.date.today().year + 1:
                raise ValueError
        except ValueError:
            errors.append("Year must be a valid year.")
    meta = {
        "title": title,
        "description": form.get("description", "").strip()[:5000],
        "medium": form.get("medium", "").strip()[:60],
        "year": year,
        "tags": models.normalize_tags(form.get("tags", "")),
    }
    return meta, errors


def _set_published(artwork_id: int, published: bool) -> None:
    get_db().execute(
        """UPDATE artworks SET is_published = ?, updated_at = ?,
               published_at = CASE WHEN ? AND published_at IS NULL THEN ? ELSE published_at END
           WHERE id = ?""",
        (int(published), _now(), int(published), _now(), artwork_id),
    )


@bp.get("/")
@login_required
def dashboard():
    status = request.args.get("status", "all")
    db = get_db()
    where = "a.user_id = ?"
    if status == "published":
        where += " AND a.is_published = 1"
    elif status == "drafts":
        where += " AND a.is_published = 0"
    rows = db.execute(
        f"{models.ARTWORK_SELECT} WHERE {where} ORDER BY a.created_at DESC, a.id DESC", (g.user["id"],)
    ).fetchall()
    stats = db.execute(
        """SELECT COUNT(*) AS total, COALESCE(SUM(is_published), 0) AS published,
                  COALESCE(SUM(views), 0) AS views,
                  (SELECT COUNT(*) FROM likes l JOIN artworks x ON x.id = l.artwork_id WHERE x.user_id = :u) AS likes
           FROM artworks WHERE user_id = :u""",
        {"u": g.user["id"]},
    ).fetchone()
    return render_template(
        "studio/dashboard.html", artworks=rows, stats=stats, status=status,
        tags=models.tags_for([r["id"] for r in rows]),
    )


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    errors = []
    if request.method == "POST":
        files = [f for f in request.files.getlist("images") if f and f.filename]
        meta, errors = _parse_meta(request.form)
        publish = request.form.get("publish") == "1"
        if not files:
            errors.append("Choose at least one image.")
        if len(files) > 20:
            errors.append("Upload at most 20 images at a time.")
        if not errors and not _storage_available():
            errors.append("The gallery's storage is full. Uploads are paused until space is freed.")
        if not errors:
            db = get_db()
            created = []
            for i, f in enumerate(files):
                if not upload_limiter.hit(f"{g.user['id']}:{client_ip()}"):
                    errors.append("Upload limit reached. Try again later.")
                    break
                try:
                    info = process_upload(f.stream, current_app.config["UPLOAD_DIR"])
                except ImageError as e:
                    errors.append(f"{f.filename}: {e}")
                    continue
                title = meta["title"] or Path(f.filename).stem.replace("_", " ").replace("-", " ").strip().title()[:120] or "Untitled"
                if meta["title"] and len(files) > 1:
                    title = f"{meta['title']} ({i + 1})"
                cur = db.execute(
                    """INSERT INTO artworks (user_id, title, description, medium, year,
                                             image_key, width, height, color)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (g.user["id"], title, meta["description"], meta["medium"], meta["year"],
                     info["image_key"], info["width"], info["height"], info["color"]),
                )
                models.set_tags(cur.lastrowid, meta["tags"])
                if publish:
                    _set_published(cur.lastrowid, True)
                created.append(cur.lastrowid)
            db.commit()
            if created:
                state = "published" if publish else "saved as draft"
                flash(f"{len(created)} artwork{'s' if len(created) > 1 else ''} {state}.", "success")
                for e in errors:
                    flash(e, "error")
                if len(created) == 1:
                    return redirect(url_for("studio.edit", artwork_id=created[0]))
                return redirect(url_for("studio.dashboard"))
    return render_template("studio/upload.html", errors=errors, form=request.form)


@bp.route("/art/<int:artwork_id>/edit", methods=["GET", "POST"])
@login_required
def edit(artwork_id):
    art = _owned_artwork(artwork_id)
    errors = []
    if request.method == "POST":
        meta, errors = _parse_meta(request.form)
        if not meta["title"]:
            errors.append("Title is required.")
        if not errors:
            db = get_db()
            db.execute(
                """UPDATE artworks SET title = ?, description = ?, medium = ?, year = ?, updated_at = ?
                   WHERE id = ?""",
                (meta["title"], meta["description"], meta["medium"], meta["year"], _now(), artwork_id),
            )
            models.set_tags(artwork_id, meta["tags"])
            if g.user["is_admin"]:
                db.execute(
                    "UPDATE artworks SET is_featured = ? WHERE id = ?",
                    (int(request.form.get("featured") == "1"), artwork_id),
                )
            db.commit()
            flash("Changes saved.", "success")
            return redirect(url_for("studio.edit", artwork_id=artwork_id))
    tags = models.tags_for([artwork_id])[artwork_id]
    return render_template("studio/edit.html", art=art, tags=tags, errors=errors)


@bp.post("/art/<int:artwork_id>/publish")
@login_required
def publish(artwork_id):
    art = _owned_artwork(artwork_id)
    publish = request.form.get("publish", "1" if not art["is_published"] else "0") == "1"
    _set_published(artwork_id, publish)
    get_db().commit()
    flash(f"“{art['title']}” is now {'published' if publish else 'unpublished'}.", "success")
    return redirect(_back())


@bp.post("/bulk")
@login_required
def bulk():
    action = request.form.get("action")
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    if action not in ("publish", "unpublish", "delete") or not ids:
        flash("Select some artworks and an action.", "info")
        return redirect(_back())
    db = get_db()
    done = 0
    for aid in ids:
        art = models.get_artwork(aid)
        if not art or not models.can_manage(g.user, art):
            continue
        if action == "delete":
            _delete(art)
        else:
            _set_published(aid, action == "publish")
        done += 1
    db.commit()
    flash(f"{done} artwork{'s' if done != 1 else ''} {action}{'d' if action.endswith('e') else 'ed'}.", "success")
    return redirect(_back())


@bp.post("/art/<int:artwork_id>/delete")
@login_required
def delete(artwork_id):
    art = _owned_artwork(artwork_id)
    _delete(art)
    get_db().commit()
    flash(f"“{art['title']}” was deleted.", "success")
    return redirect(url_for("studio.dashboard"))


def _storage_available() -> bool:
    free_mb = shutil.disk_usage(current_app.config["UPLOAD_DIR"]).free // (1024 * 1024)
    return free_mb >= current_app.config["MIN_FREE_MB"]


def _delete(art):
    get_db().execute("DELETE FROM artworks WHERE id = ?", (art["id"],))
    get_db().execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM artwork_tags)")
    delete_renditions(art["image_key"], current_app.config["UPLOAD_DIR"])


def _back() -> str:
    return safe_local_url(request.form.get("next"), url_for("studio.dashboard"))
