"""Art gallery application factory."""
import os
import secrets
from pathlib import Path

from flask import Flask, render_template, request, url_for

from . import db, security


def _load_secret_key(data_dir: Path) -> str:
    """Use SECRET_KEY from env, or generate one and persist it in the data dir."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_file = data_dir / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


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
        SITE_NAME=os.environ.get("SITE_NAME", "Atelier"),
        ALLOW_REGISTRATION=os.environ.get("ALLOW_REGISTRATION", "true").lower() == "true",
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
        PER_PAGE=24,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    )
    if test_config:
        app.config.update(test_config)
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or _load_secret_key(data_dir)

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    # Honour X-Forwarded-* headers when running behind a reverse proxy.
    if os.environ.get("BEHIND_PROXY", "false").lower() == "true":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    security.init_app(app)

    from . import admin, auth, gallery, models, studio

    app.jinja_env.globals["popular_tags"] = models.popular_tags

    app.register_blueprint(auth.bp)
    app.register_blueprint(gallery.bp)
    app.register_blueprint(studio.bp)
    app.register_blueprint(admin.bp)

    @app.template_global()
    def page_url(page: int) -> str:
        args = request.args.to_dict()
        args.pop("partial", None)
        args["page"] = page
        return url_for(request.endpoint, **(request.view_args or {}), **args)

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
