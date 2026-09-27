import io
import re

import pytest
from PIL import Image

from app import create_app
from app.security import like_limiter, safe_local_url

from .conftest import setup_admin, upload


@pytest.mark.parametrize("target", [
    "https://evil.example/x", "//evil.example", "/\\evil.example", "/\\\\evil.example",
    "javascript:alert(1)", "", None, "/x\r\nSet-Cookie: a=b", "relative/path",
])
def test_unsafe_redirect_targets_use_fallback(target):
    assert safe_local_url(target, "/fallback") == "/fallback"


@pytest.mark.parametrize("target", ["/", "/studio/", "/art/3?x=1#frag", "/search?q=a%20b"])
def test_safe_redirect_targets_pass(target):
    assert safe_local_url(target, "/fallback") == target


def test_page_url_survives_hostile_query_params(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "CSRF_DISABLED": True, "PER_PAGE": 1})
    c = app.test_client()
    setup_admin(c)
    upload(c)
    upload(c, title="Second")
    c.post("/logout")
    for path in ("/artist/alice?username=zzz", "/?_scheme=javascript", "/?_external=1",
                 "/search?q=s&_anchor=x&partial=0", "/artist/alice?page=abc"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert 'data-load-more href="' in r.get_data(as_text=True)


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf


def test_pixel_bomb_rejected_before_decode(client):
    setup_admin(client)
    r = client.post("/studio/upload", data={"images": (_png(7000, 7000), "bomb.png")},
                    content_type="multipart/form-data")  # 49 MP, ~150 KB on disk
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "too large (7000" in body


def test_large_but_allowed_image_is_downscaled(client, app):
    import os
    setup_admin(client)
    r = client.post("/studio/upload", data={"images": (_png(6000, 4000), "big.png")},
                    content_type="multipart/form-data")  # 24 MP
    assert r.status_code == 302
    key = re.search(r"/media/([\w-]+)/", client.get("/studio/").get_data(as_text=True)).group(1)
    sizes = {n: Image.open(os.path.join(app.config["UPLOAD_DIR"], f"{key}_{n}.webp")).size
             for n in ("full", "medium", "thumb")}
    assert sizes == {"full": (2400, 1600), "medium": (1200, 800), "thumb": (640, 427)}
    assert "6000 × 4000" in client.get(f"/art/{key and 1}").get_data(as_text=True)


def test_tiff_and_bmp_no_longer_accepted(client):
    setup_admin(client)
    for fmt, name in (("TIFF", "x.tif"), ("BMP", "x.bmp")):
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, fmt)
        buf.seek(0)
        r = client.post("/studio/upload", data={"images": (buf, name)}, content_type="multipart/form-data")
        assert "Unsupported image format" in r.get_data(as_text=True)


def test_upload_refused_when_disk_nearly_full(client, monkeypatch):
    import shutil
    setup_admin(client)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 90, 10 * 1024 * 1024))
    r = client.post("/studio/upload", data={"images": (_png(300, 200), "a.png")}, content_type="multipart/form-data")
    assert "storage is full" in r.get_data(as_text=True)


def test_likes_are_rate_limited(client):
    setup_admin(client)
    upload(client)
    like_limiter.reset()
    anon = client.application.test_client()
    codes = [anon.post("/art/1/like").status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_media_key_cannot_traverse(client):
    setup_admin(client)
    for key in ("..", "%2e%2e", "..%2fsecret_key", "x/../../secret_key"):
        assert client.get(f"/media/{key}/full.webp").status_code == 404
