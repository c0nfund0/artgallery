"""SQLite access and automatic, versioned schema migrations.

Migrations live in app/migrations/NNN_description.sql and are applied in order
on startup. Each migration runs in a transaction; a backup of the database is
taken before any pending migration is applied, so upgrades are safe to automate.
"""
import shutil
import sqlite3
import time
from pathlib import Path

import click
from flask import current_app, g

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def pending_migrations(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    version = current_version(conn)
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        num = int(path.name.split("_", 1)[0])
        if num > version:
            found.append((num, path))
    return found


def migrate(db_path: str, logger=None) -> int:
    """Apply pending migrations. Returns the number applied."""
    conn = connect(db_path)
    try:
        pending = pending_migrations(conn)
        if not pending:
            return 0
        if current_version(conn) > 0:
            backup = f"{db_path}.bak-v{current_version(conn)}-{int(time.time())}"
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(db_path, backup)
            if logger:
                logger.info("Database backed up to %s", backup)
        for num, path in pending:
            sql = path.read_text()
            # executescript commits implicitly, so wrap explicitly for atomicity.
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {num};\nCOMMIT;")
            if logger:
                logger.info("Applied migration %s", path.name)
        return len(pending)
    finally:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    migrate(app.config["DATABASE"], app.logger)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(migrate_command)


@click.command("migrate")
def migrate_command():
    """Apply pending database migrations."""
    n = migrate(current_app.config["DATABASE"])
    click.echo(f"Applied {n} migration(s).")


@click.command("create-admin")
@click.argument("username")
@click.password_option()
def create_admin_command(username, password):
    """Create an admin user (or promote an existing one)."""
    from werkzeug.security import generate_password_hash

    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username.lower(),)).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET is_admin = 1, password_hash = ? WHERE id = ?",
            (generate_password_hash(password), row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, is_admin) VALUES (?, ?, ?, 1)",
            (username.lower(), username, generate_password_hash(password)),
        )
    conn.commit()
    click.echo(f"Admin '{username}' ready.")
