"""Session signing key: generated securely on first run and persisted.

- 256 bits from the OS CSPRNG (``secrets``).
- Written atomically: the key goes to a private temp file (mode 0600 from the
  moment it exists), is fsynced, then hard-linked into place. ``link`` never
  overwrites, so if two processes start at once exactly one key wins and both
  use it. Readers never see a partial file.
- An existing key that is empty/short is rejected loudly instead of being
  used (an empty key would let anyone forge sessions).
"""
import logging
import os
import secrets
import stat
from pathlib import Path

KEY_FILE = "secret_key"
KEY_BYTES = 32
MIN_LENGTH = 32  # characters, for keys supplied via the environment

log = logging.getLogger(__name__)


class SecretKeyError(RuntimeError):
    pass


def load_or_create(data_dir: Path, env_value: str | None = None) -> str:
    if env_value:
        if len(env_value) < MIN_LENGTH:
            raise SecretKeyError(
                f"SECRET_KEY is too short ({len(env_value)} chars; need {MIN_LENGTH}+). "
                "Unset it to have one generated, or use: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return env_value

    path = Path(data_dir) / KEY_FILE
    if not path.exists() and create_private_file(path, secrets.token_hex(KEY_BYTES)):
        log.info("Generated new secret key at %s", path)
    return _read(path)


def create_private_file(path: Path, content: str) -> bool:
    """Atomically create ``path`` (mode 0600) with ``content`` unless it exists.

    Returns True if this call created it, False if another process already had.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)  # atomic and never overwrites
            created = True
        except FileExistsError:
            created = False
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(path.parent)
    return created


def _read(path: Path) -> str:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("Tightening permissions on %s (was %o)", path, stat.S_IMODE(mode))
        path.chmod(0o600)
    key = path.read_text().strip()
    if len(key) < KEY_BYTES * 2:
        raise SecretKeyError(
            f"{path} is empty or corrupted. Restore it from backup, or delete it to "
            "generate a new key (this signs everyone out)."
        )
    return key


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
