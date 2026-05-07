from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VAULT_PATH = Path.home() / ".local" / "share" / "secret" / "vault.enc"
SESSION_PATH = Path.home() / ".cache" / "secret" / "session.json"
SESSION_TTL_S = 15 * 60


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def vault_exists() -> bool:
    return VAULT_PATH.exists()


def load_records(password: str) -> list[dict[str, str]]:
    """Raises InvalidToken if the password is wrong."""
    raw = json.loads(VAULT_PATH.read_text())
    salt = base64.b64decode(raw["salt"])
    plaintext = Fernet(_derive_key(password, salt)).decrypt(raw["data"].encode())
    return json.loads(plaintext)


def save_records(password: str, records: list[dict[str, str]]) -> None:
    if vault_exists():
        raw = json.loads(VAULT_PATH.read_text())
        salt = base64.b64decode(raw["salt"])
    else:
        salt = os.urandom(16)
        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = _derive_key(password, salt)
    encrypted = Fernet(key).encrypt(json.dumps(records).encode()).decode()
    VAULT_PATH.write_text(json.dumps({
        "salt": base64.b64encode(salt).decode(),
        "data": encrypted,
    }))


def save_session(password: str) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps({
        "password": password,
        "expires_at": int(time.time()) + SESSION_TTL_S,
    }))
    SESSION_PATH.chmod(0o600)


def load_session() -> str | None:
    if not SESSION_PATH.exists():
        return None
    try:
        payload = json.loads(SESSION_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or time.time() >= payload.get("expires_at", 0):
        clear_session()
        return None
    password = payload.get("password")
    return password if isinstance(password, str) else None


def clear_session() -> None:
    SESSION_PATH.unlink(missing_ok=True)
