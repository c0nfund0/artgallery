"""Site settings persisted in the database, with defaults."""
from flask import g

from .db import get_db

DEFAULTS = {
    "site_name": "Atelier",
    "allow_registration": "0",
}


def all_settings() -> dict:
    if "settings" not in g:
        rows = get_db().execute("SELECT key, value FROM settings").fetchall()
        g.settings = {**DEFAULTS, **{r["key"]: r["value"] for r in rows}}
    return g.settings


def get(key: str) -> str:
    return all_settings()[key]


def set_many(values: dict) -> None:
    db = get_db()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    g.pop("settings", None)
