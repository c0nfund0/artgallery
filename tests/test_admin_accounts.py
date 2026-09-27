import re

from .conftest import login, setup_admin


def create(client, username="helene", **extra):
    r = client.post("/admin/users", data={"username": username, "display_name": "Helene S", **extra})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    return re.search(r"Temporary password</dt><dd><code>([^<]+)</code>", html).group(1)


def test_admin_creates_account_with_forced_password_change(client, app):
    setup_admin(client, allow_registration=False)
    temp = create(client)
    assert re.fullmatch(r"([a-z0-9]{4}-){4}[a-z0-9]{4}", temp)
    client.post("/logout")

    assert login(client, "helene", "wrong-password-xx").status_code == 200  # rejected
    assert login(client, "helene", temp).status_code == 302
    # Everything except the account page redirects until the password is changed.
    for path in ("/studio/", "/studio/upload", "/"):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/account"), path
    assert client.get("/account").status_code == 200

    r = client.post("/account", data={"action": "password", "current": temp,
                                      "new": "my-own-long-password", "new2": "my-own-long-password"})
    assert r.status_code == 302
    assert client.get("/studio/").status_code == 200
    assert login(client.application.test_client(), "helene", temp).status_code == 200  # temp no longer works
    with app.app_context():
        from app.db import get_db
        row = get_db().execute("SELECT is_admin, must_change_password FROM users WHERE username='helene'").fetchone()
        assert (row["is_admin"], row["must_change_password"]) == (0, 0)


def test_admin_can_create_admin_and_validation(client):
    setup_admin(client)
    create(client, "second", is_admin="1")
    r = client.post("/admin/users", data={"username": "Bad Name!"}, follow_redirects=True)
    assert "Username must be" in r.get_data(as_text=True)
    r = client.post("/admin/users", data={"username": "second"}, follow_redirects=True)
    assert "taken" in r.get_data(as_text=True)


def test_non_admin_cannot_create_accounts(client):
    setup_admin(client)
    temp = create(client, "artist")
    client.post("/logout")
    login(client, "artist", temp)
    client.post("/account", data={"action": "password", "current": temp, "new": "artist-long-pass", "new2": "artist-long-pass"})
    assert client.post("/admin/users", data={"username": "sneaky"}).status_code == 403
    assert client.get("/admin/").status_code == 403


def test_reset_password_signs_user_out_everywhere(client, app):
    setup_admin(client)
    temp = create(client, "artist")
    other = app.test_client()
    login(other, "artist", temp)
    other.post("/account", data={"action": "password", "current": temp, "new": "artist-long-pass", "new2": "artist-long-pass"})
    assert other.get("/studio/").status_code == 200
    with app.app_context():
        from app.db import get_db
        uid = get_db().execute("SELECT id FROM users WHERE username='artist'").fetchone()[0]
    r = client.post(f"/admin/users/{uid}", data={"action": "reset_password"})
    new_temp = re.search(r"Temporary password</dt><dd><code>([^<]+)</code>", r.get_data(as_text=True)).group(1)
    assert other.get("/studio/").status_code == 302  # old session dead
    assert login(other, "artist", new_temp).status_code == 302


def test_admin_deletes_user_and_their_artworks(client, app):
    import os
    from .conftest import upload
    setup_admin(client)
    temp = create(client, "artist")
    artist = app.test_client()
    login(artist, "artist", temp)
    artist.post("/account", data={"action": "password", "current": temp, "new": "artist-long-pass", "new2": "artist-long-pass"})
    upload(artist, tags="onlytag")
    upload(artist, title="Second", publish=False)
    assert len(os.listdir(app.config["UPLOAD_DIR"])) == 6
    with app.app_context():
        from app.db import get_db
        uid = get_db().execute("SELECT id FROM users WHERE username='artist'").fetchone()[0]

    r = client.post(f"/admin/users/{uid}", data={"action": "delete"}, follow_redirects=True)
    assert "Deleted Helene S and 2 artworks" in r.get_data(as_text=True)
    assert os.listdir(app.config["UPLOAD_DIR"]) == []
    with app.app_context():
        from app.db import get_db
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artworks").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM tags WHERE name='onlytag'").fetchone()[0] == 0
    assert artist.get("/studio/").status_code == 302  # session gone
    assert login(artist, "artist", "artist-long-pass").status_code == 200  # can't sign in
    assert client.get("/artist/artist").status_code == 404


def test_admin_cannot_delete_self(client, app):
    setup_admin(client)
    r = client.post("/admin/users/1", data={"action": "delete"}, follow_redirects=True)
    assert "your own account" in r.get_data(as_text=True)
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
