import shutil

from app import db


def test_new_migration_applied_with_backup(tmp_path, monkeypatch):
    mig = tmp_path / "migrations"
    shutil.copytree(db.MIGRATIONS_DIR, mig)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    path = str(tmp_path / "g.db")
    assert db.migrate(path) >= 1
    assert db.migrate(path) == 0  # idempotent

    (mig / "999_add_column.sql").write_text("ALTER TABLE artworks ADD COLUMN location TEXT;")
    assert db.migrate(path) == 1
    conn = db.connect(path)
    assert db.current_version(conn) == 999
    assert "location" in [r[1] for r in conn.execute("PRAGMA table_info(artworks)")]
    assert list((tmp_path / "backups").glob("g.db.v*.bak")), "a backup is taken before migrating"


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    mig = tmp_path / "migrations"
    shutil.copytree(db.MIGRATIONS_DIR, mig)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    path = str(tmp_path / "g.db")
    db.migrate(path)
    before = db.current_version(db.connect(path))
    (mig / "998_broken.sql").write_text("CREATE TABLE ok_table (id INTEGER);\nTHIS IS NOT SQL;")
    try:
        db.migrate(path)
    except Exception:
        pass
    conn = db.connect(path)
    assert db.current_version(conn) == before
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='ok_table'").fetchone() is None
