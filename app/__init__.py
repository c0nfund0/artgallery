"""Art gallery application factory."""
import os
from pathlib import Path

from flask import Flask, render_template, request, url_for

from . import db, keys, security


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)

    data_dir = Path(os.environ.get("DATA_DIR", Path(app.root_path).parent / "data"))
    if test_config and "DATA_DIR" in test_config:
        data_dir = Path(test_config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)

    app.config.update(
        DATA_DIR=str(data_dir),
        DATABASE=str(data_dir / "gallery.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        BACKUP_DIR=str(data_dir / "backups"),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
        # Refuse uploads when the data disk has less than this free, so a full
        # disk never breaks the database (which lives on the same volume).
        MIN_FREE_MB=int(os.environ.get("MIN_FREE_MB", "512")),
        PER_PAGE=24,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = keys.load_or_create(data_dir, os.environ.get("SECRET_KEY"))

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    # Honour X-Forwarded-* headers when running behind a reverse proxy.
    if os.environ.get("BEHIND_PROXY", "false").lower() == "true":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    security.init_app(app)

    from . import admin, auth, gallery, models, setup, studio

    app.jinja_env.globals["popular_tags"] = models.popular_tags

    app.register_blueprint(auth.bp)
    app.register_blueprint(gallery.bp)
    app.register_blueprint(studio.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(setup.bp)
    setup.announce_if_needed(app)

    @app.template_global()
    def page_url(page: int) -> str:
        view_args = request.view_args or {}
        # Drop query keys that would collide with url_for's own arguments
        # (``_external``...) or with the route's path arguments.
        args = {
            k: v for k, v in request.args.to_dict().items()
            if not k.startswith("_") and k not in view_args and k != "partial"
        }
        args["page"] = page
        return url_for(request.endpoint, **view_args, **args)

    @app.template_filter()
    def human_date(value: str | None) -> str:
        if not value:
            return ""
        from datetime import datetime

        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%-d %B %Y")

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("errors/error.html", code=400, message=e.description or "Bad request."), 400

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/error.html", code=403, message="You don't have access to this page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/error.html", code=404, message="This page has wandered off the wall."), 404

    @app.errorhandler(413)
    def too_large(_e):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return render_template("errors/error.html", code=413, message=f"That file is too large (max {mb} MB)."), 413

    @app.errorhandler(429)
    def too_many(_e):
        return render_template("errors/error.html", code=429, message="Too many attempts. Please wait a moment."), 429

    @app.get("/healthz")
    def healthz():
        db.get_db().execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": db.current_version(db.get_db())}

    return app
