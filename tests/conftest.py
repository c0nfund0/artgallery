import io

import pytest
from PIL import Image

from app import create_app
from app.security import login_limiter, upload_limiter


@pytest.fixture
def app(tmp_path):
    login_limiter.reset()
    upload_limiter.reset()
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test", "CSRF_DISABLED": True})
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def make_image(fmt="PNG", size=(320, 200), color=(180, 70, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    buf.seek(0)
    return buf


def setup_admin(client, username="alice", password="correct-horse-battery", allow_registration=True):
    """Complete first-run setup; the setup code is read from the data dir as an operator would from the log."""
    from pathlib import Path

    token = (Path(client.application.config["DATA_DIR"]) / "setup_token").read_text().strip()
    data = {"setup_token": token, "site_name": "Test Gallery", "username": username,
            "display_name": username.title(), "password": password, "password2": password}
    if allow_registration:
        data["allow_registration"] = "1"
    return client.post("/setup", data=data)


def register(client, username="alice", password="correct-horse-battery"):
    return client.post(
        "/register",
        data={"username": username, "display_name": username.title(), "password": password, "password2": password},
    )


def login(client, username="alice", password="correct-horse-battery"):
    return client.post("/login", data={"username": username, "password": password})


def upload(client, title="Sunrise", publish=True, tags="light, nordic"):
    data = {"images": (make_image(), "sunrise.png"), "title": title, "medium": "Oil on canvas", "year": "2024", "tags": tags}
    if publish:
        data["publish"] = "1"
    return client.post("/studio/upload", data=data, content_type="multipart/form-data")
