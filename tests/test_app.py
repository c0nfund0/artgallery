import re

from app.db import connect, current_version, pending_migrations

from .conftest import login, make_image, register, setup_admin, upload


def artwork_id(client):
    html = client.get("/studio/").get_data(as_text=True)
    return int(re.search(r"/studio/art/(\d+)/edit", html).group(1))


def test_migrations_applied(app):
    conn = connect(app.config["DATABASE"])
    assert current_version(conn) >= 1
    assert pending_migrations(conn) == []


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json["status"] == "ok"


def test_public_pages_without_login(client):
    setup_admin(client)
    client.post("/logout")
    for path in ("/", "/artists", "/search?q=x"):
        assert client.get(path).status_code == 200
    assert client.get("/studio/").status_code == 302


def test_fresh_install_has_no_users_and_redirects_to_setup(client, app):
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    for path in ("/", "/login", "/register", "/artists", "/studio/"):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/setup")
    assert client.get("/healthz").status_code == 200
    assert client.get("/setup").status_code == 200


def test_setup_requires_correct_code(client, app):
    r = client.post("/setup", data={"setup_token": "wrong", "site_name": "X", "username": "eve",
                                    "password": "long-enough-pass", "password2": "long-enough-pass"})
    assert r.status_code == 200 and "Setup code is incorrect" in r.get_data(as_text=True)
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_setup_creates_admin_then_closes(client, app):
    import os
    assert setup_admin(client).status_code == 302
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT is_admin FROM users WHERE username='alice'").fetchone()[0] == 1
    assert not os.path.exists(os.path.join(app.config["DATA_DIR"], "setup_token"))
    assert client.get("/setup").status_code == 404
    assert "Test Gallery" in client.get("/").get_data(as_text=True)
    # Later sign-ups are regular artists, never admins.
    client.post("/logout")
    register(client, "bob")
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT is_admin FROM users WHERE username='bob'").fetchone()[0] == 0


def test_registration_toggle(client):
    setup_admin(client, allow_registration=False)
    client.post("/logout")
    assert client.get("/register").status_code == 404
    login(client)
    client.post("/admin/settings", data={"site_name": "Renamed", "allow_registration": "1"})
    client.post("/logout")
    assert client.get("/register").status_code == 200
    assert "Renamed" in client.get("/").get_data(as_text=True)


def test_state_persists_across_restart(tmp_path):
    """Everything lives in DATA_DIR: a new app instance on the same dir sees it all."""
    from app import create_app
    cfg = {"TESTING": True, "DATA_DIR": str(tmp_path), "CSRF_DISABLED": True}
    c1 = create_app(cfg).test_client()
    setup_admin(c1)
    upload(c1)
    key = (tmp_path / "secret_key").read_text()
    c2 = create_app(cfg).test_client()  # "restart"
    assert (tmp_path / "secret_key").read_text() == key
    assert "Sunrise" in c2.get("/").get_data(as_text=True)
    assert login(c2).status_code == 302
    assert list((tmp_path / "uploads").glob("*_full.webp"))


def test_login_logout(client):
    setup_admin(client)
    client.post("/logout")
    assert login(client, password="wrong-password").status_code == 200
    assert client.get("/studio/").status_code == 302
    assert login(client).status_code == 302
    assert client.get("/studio/").status_code == 200


def test_upload_publish_unpublish_flow(client):
    setup_admin(client)
    r = upload(client, publish=False)
    assert r.status_code == 302
    aid = artwork_id(client)
    art_page = f"/art/{aid}"

    # Draft: owner sees it; anonymous visitors get 404 for page and image.
    assert client.get(art_page).status_code == 200
    html = client.get(art_page).get_data(as_text=True)
    media = re.search(r'/media/[\w-]+/thumb\.webp', client.get("/studio/").get_data(as_text=True)).group(0)
    anon = client.application.test_client()
    assert anon.get(art_page).status_code == 404
    assert anon.get(media).status_code == 404
    assert "Draft" in html or "draft" in html

    client.post(f"/studio/art/{aid}/publish", data={"publish": "1"})
    assert anon.get(art_page).status_code == 200
    assert anon.get(media).status_code == 200
    assert anon.get(media).mimetype == "image/webp"
    assert "Sunrise" in anon.get("/").get_data(as_text=True)
    assert "Sunrise" in anon.get("/search?tag=nordic").get_data(as_text=True)
    assert "Sunrise" in anon.get("/search?q=sunr").get_data(as_text=True)

    client.post(f"/studio/art/{aid}/publish", data={"publish": "0"})
    assert anon.get(art_page).status_code == 404


def test_other_users_cannot_manage(client, app):
    setup_admin(client)
    upload(client)
    aid = artwork_id(client)
    client.post("/logout")
    register(client, "mallory")
    assert client.post(f"/studio/art/{aid}/publish", data={"publish": "0"}).status_code == 403
    assert client.post(f"/studio/art/{aid}/delete").status_code == 403
    assert client.get(f"/studio/art/{aid}/edit").status_code == 403


def test_edit_and_delete(client, app):
    setup_admin(client)
    upload(client)
    aid = artwork_id(client)
    r = client.post(f"/studio/art/{aid}/edit", data={"title": "Dusk", "tags": "evening", "medium": "Ink", "year": "2020"})
    assert r.status_code == 302
    assert "Dusk" in client.get(f"/art/{aid}").get_data(as_text=True)
    client.post(f"/studio/art/{aid}/delete")
    assert client.get(f"/art/{aid}").status_code == 404
    import os
    assert os.listdir(app.config["UPLOAD_DIR"]) == []


def test_bulk_actions(client):
    setup_admin(client)
    upload(client, publish=False)
    upload(client, title="Second", publish=False)
    html = client.get("/studio/").get_data(as_text=True)
    ids = re.findall(r'name="ids" value="(\d+)"', html)
    r = client.post("/studio/bulk", data={"action": "publish", "ids": ids}, follow_redirects=True)
    assert "2 artworks published" in r.get_data(as_text=True)
    anon = client.application.test_client()
    for i in ids:
        assert anon.get(f"/art/{i}").status_code == 200


def test_rejects_non_image(client):
    setup_admin(client)
    import io
    r = client.post("/studio/upload", data={"images": (io.BytesIO(b"<?php evil ?>"), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert "valid image" in r.get_data(as_text=True)


def test_like_toggle(client):
    setup_admin(client)
    upload(client)
    aid = artwork_id(client)
    anon = client.application.test_client()
    assert anon.post(f"/art/{aid}/like").json == {"liked": True, "count": 1}
    assert anon.post(f"/art/{aid}/like").json == {"liked": False, "count": 0}


def test_view_counter_counts_once_per_session(client):
    setup_admin(client)
    upload(client)
    aid = artwork_id(client)
    anon = client.application.test_client()
    anon.get(f"/art/{aid}")
    anon.get(f"/art/{aid}")
    client.get(f"/art/{aid}")  # owner views don't count
    html = anon.get(f"/art/{aid}").get_data(as_text=True)
    assert "1 view<" in html


def test_admin_can_disable_user(client):
    setup_admin(client)  # admin
    client.post("/logout")
    register(client, "bob")
    upload(client, title="Bobs work")
    aid = artwork_id(client)
    client.post("/logout")
    login(client)
    with client.application.app_context():
        from app.db import get_db
        bob_id = get_db().execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
    client.post(f"/admin/users/{bob_id}", data={"action": "toggle_active"})
    anon = client.application.test_client()
    assert anon.get(f"/art/{aid}").status_code == 404
    assert "Bobs work" not in anon.get("/").get_data(as_text=True)


def test_password_change_invalidates_other_sessions(client):
    setup_admin(client)
    other = client.application.test_client()
    login(other)
    assert other.get("/studio/").status_code == 200
    client.post("/account", data={"action": "password", "current": "correct-horse-battery",
                                  "new": "a-new-long-password", "new2": "a-new-long-password"})
    assert client.get("/studio/").status_code == 200
    assert other.get("/studio/").status_code == 302


def test_open_redirect_blocked(client):
    setup_admin(client)
    client.post("/logout")
    r = client.post("/login?next=//evil.example", data={"username": "alice", "password": "correct-horse-battery"})
    assert "evil.example" not in r.headers["Location"]


def test_login_rate_limited(client):
    setup_admin(client)
    client.post("/logout")
    from app.security import login_limiter
    login_limiter.reset()
    for _ in range(10):
        login(client, password="nope-nope-nope")
    assert login(client, password="nope-nope-nope").status_code == 429


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_exif_is_stripped(client, app):
    import io, os
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (200, 200), "blue")
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    img.save(buf, "JPEG", exif=exif)
    buf.seek(0)
    setup_admin(client)
    client.post("/studio/upload", data={"images": (buf, "p.jpg")}, content_type="multipart/form-data")
    for f in os.listdir(app.config["UPLOAD_DIR"]):
        assert b"SecretCamera" not in open(os.path.join(app.config["UPLOAD_DIR"], f), "rb").read()


def test_infinite_scroll_partial(client):
    setup_admin(client)
    upload(client)
    r = client.get("/?partial=1")
    assert r.status_code == 200
    assert "<html" not in r.get_data(as_text=True)
    assert "card" in r.get_data(as_text=True)
