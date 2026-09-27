from app import create_app


def test_post_without_csrf_rejected(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "t"})
    c = app.test_client()
    r = c.post("/login", data={"username": "a", "password": "b"})
    assert r.status_code == 400


def test_post_with_csrf_accepted(tmp_path):
    import re
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "t"})
    c = app.test_client()
    html = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    r = c.post("/login", data={"username": "a", "password": "b", "csrf_token": token})
    assert r.status_code == 200
