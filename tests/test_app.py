import re

from app.db import connect, current_version, pending_migrations

from .conftest import login, make_image, register, upload


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
    for path in ("/", "/artists", "/search?q=x"):
        assert client.get(path).status_code == 200
    assert client.get("/studio/").status_code == 302


def test_first_user_is_admin_and_registration(client, app):
    register(client)
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT is_admin FROM users WHERE username='alice'").fetchone()[0] == 1
    client.post("/logout")
    register(client, "bob")
    with app.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT is_admin FROM users WHERE username='bob'").fetchone()[0] == 0


def test_registration_can_be_closed(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "t",
                      "CSRF_DISABLED": True, "ALLOW_REGISTRATION": False})
    c = app.test_client()
    assert c.get("/register").status_code == 200  # first user is always allowed
    register(c)
    c.post("/logout")
    assert c.get("/register").status_code == 404


def test_login_logout(client):
    register(client)
    client.post("/logout")
    assert login(client, password="wrong-password").status_code == 200
    assert client.get("/studio/").status_code == 302
    assert login(client).status_code == 302
    assert client.get("/studio/").status_code == 200


def test_upload_publish_unpublish_flow(client):
    register(client)
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
    register(client)
    upload(client)
    aid = artwork_id(client)
    client.post("/logout")
    register(client, "mallory")
    assert client.post(f"/studio/art/{aid}/publish", data={"publish": "0"}).status_code == 403
    assert client.post(f"/studio/art/{aid}/delete").status_code == 403
    assert client.get(f"/studio/art/{aid}/edit").status_code == 403


def test_edit_and_delete(client, app):
    register(client)
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
    register(client)
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
    register(client)
    import io
    r = client.post("/studio/upload", data={"images": (io.BytesIO(b"<?php evil ?>"), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert "valid image" in r.get_data(as_text=True)


def test_like_toggle(client):
    register(client)
    upload(client)
    aid = artwork_id(client)
    anon = client.application.test_client()
    assert anon.post(f"/art/{aid}/like").json == {"liked": True, "count": 1}
    assert anon.post(f"/art/{aid}/like").json == {"liked": False, "count": 0}


def test_view_counter_counts_once_per_session(client):
    register(client)
    upload(client)
    aid = artwork_id(client)
    anon = client.application.test_client()
    anon.get(f"/art/{aid}")
    anon.get(f"/art/{aid}")
    client.get(f"/art/{aid}")  # owner views don't count
    html = anon.get(f"/art/{aid}").get_data(as_text=True)
    assert "1 view<" in html


def test_admin_can_disable_user(client):
    register(client)  # admin
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
    register(client)
    other = client.application.test_client()
    login(other)
    assert other.get("/studio/").status_code == 200
    client.post("/account", data={"action": "password", "current": "correct-horse-battery",
                                  "new": "a-new-long-password", "new2": "a-new-long-password"})
    assert client.get("/studio/").status_code == 200
    assert other.get("/studio/").status_code == 302


def test_open_redirect_blocked(client):
    register(client)
    client.post("/logout")
    r = client.post("/login?next=//evil.example", data={"username": "alice", "password": "correct-horse-battery"})
    assert "evil.example" not in r.headers["Location"]


def test_login_rate_limited(client):
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
    register(client)
    client.post("/studio/upload", data={"images": (buf, "p.jpg")}, content_type="multipart/form-data")
    for f in os.listdir(app.config["UPLOAD_DIR"]):
        assert b"SecretCamera" not in open(os.path.join(app.config["UPLOAD_DIR"], f), "rb").read()


def test_infinite_scroll_partial(client):
    register(client)
    upload(client)
    r = client.get("/?partial=1")
    assert r.status_code == 200
    assert "<html" not in r.get_data(as_text=True)
    assert "card" in r.get_data(as_text=True)
