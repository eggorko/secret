from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VAULT_PATH = Path.home() / ".local" / "share" / "secret" / "vault.enc"


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
