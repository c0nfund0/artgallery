import multiprocessing as mp
import os
import re
import stat

import pytest

from app import create_app, keys


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_generated_on_first_run_and_persisted(tmp_path):
    k1 = keys.load_or_create(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{64}", k1)  # 256 bits
    assert mode(tmp_path / "secret_key") == 0o600
    assert keys.load_or_create(tmp_path) == k1
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]  # no temp files left


def test_private_even_with_permissive_umask(tmp_path):
    old = os.umask(0)
    try:
        keys.load_or_create(tmp_path)
    finally:
        os.umask(old)
    assert mode(tmp_path / "secret_key") == 0o600


def test_keys_differ_between_installs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    assert keys.load_or_create(a) != keys.load_or_create(b)


def _worker(path, q):
    q.put(keys.load_or_create(path))


def test_concurrent_first_run_agrees_on_one_key(tmp_path):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(tmp_path, q)) for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    results = {q.get(timeout=5) for _ in procs}
    assert len(results) == 1
    assert (tmp_path / "secret_key").read_text().strip() in results
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]


def test_corrupted_key_file_refuses_to_start(tmp_path):
    (tmp_path / "secret_key").write_text("")
    with pytest.raises(keys.SecretKeyError):
        keys.load_or_create(tmp_path)
    with pytest.raises(keys.SecretKeyError):
        create_app({"DATA_DIR": str(tmp_path)})


def test_loose_permissions_are_tightened(tmp_path):
    keys.load_or_create(tmp_path)
    os.chmod(tmp_path / "secret_key", 0o644)
    keys.load_or_create(tmp_path)
    assert mode(tmp_path / "secret_key") == 0o600


def test_env_key_must_be_strong(tmp_path):
    with pytest.raises(keys.SecretKeyError):
        keys.load_or_create(tmp_path, "secret")
    strong = "x" * 40
    assert keys.load_or_create(tmp_path, strong) == strong
    assert not (tmp_path / "secret_key").exists()


def test_app_uses_persisted_key_across_restarts(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    k1 = create_app({"DATA_DIR": str(tmp_path)}).secret_key
    k2 = create_app({"DATA_DIR": str(tmp_path)}).secret_key
    assert k1 == k2 == (tmp_path / "secret_key").read_text().strip()


def test_setup_token_file_is_private(tmp_path, monkeypatch):
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    create_app({"DATA_DIR": str(tmp_path)})
    assert mode(tmp_path / "setup_token") == 0o600
