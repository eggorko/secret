from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VAULT_PATH = Path.home() / ".local" / "share" / "secret" / "vault.enc"
SESSION_PATH = Path.home() / ".cache" / "secret" / "session.json"
DEBUG_DUMP_PATH = Path.home() / ".cache" / "secret" / "records-debug.json"
SESSION_TTL_S = 15 * 60
VAULT_VERSION = 2
RecordPayload = dict[str, str | None]
LegacyRecordPayload = dict[str, str | None]


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


def load_records(password: str) -> list[RecordPayload]:
    """Raises InvalidToken if the password is wrong."""
    raw = json.loads(VAULT_PATH.read_text())
    plaintext = _decrypt_vault_payload(password, raw)
    records = json.loads(plaintext)
    if _needs_secret_migration(raw, records):
        save_records(password, records)
        return load_records(password)
    return records


def save_records(password: str, records: list[RecordPayload | LegacyRecordPayload]) -> None:
    if vault_exists():
        raw = json.loads(VAULT_PATH.read_text())
        outer_salt = base64.b64decode(raw["salt"])
        inner_salt = (
            base64.b64decode(raw["secret_salt"])
            if "secret_salt" in raw
            else os.urandom(16)
        )
    else:
        outer_salt = os.urandom(16)
        inner_salt = os.urandom(16)
        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)

    normalized_records = _normalize_records_for_save(password, records, inner_salt)
    key = _derive_key(password, outer_salt)
    encrypted = Fernet(key).encrypt(json.dumps(normalized_records).encode()).decode()
    VAULT_PATH.write_text(json.dumps({
        "version": VAULT_VERSION,
        "salt": base64.b64encode(outer_salt).decode(),
        "secret_salt": base64.b64encode(inner_salt).decode(),
        "data": encrypted,
    }))


def encrypt_secret(password: str, value: str) -> str:
    raw = _load_raw_vault()
    if raw is None:
        raise RuntimeError("Vault must be created before encrypting secrets.")
    salt = base64.b64decode(raw.get("secret_salt", raw["salt"]))
    return Fernet(_derive_key(password, salt)).encrypt(value.encode()).decode()


def decrypt_secret(password: str, token: str) -> str:
    raw = json.loads(VAULT_PATH.read_text())
    salt = base64.b64decode(raw.get("secret_salt", raw["salt"]))
    plaintext = Fernet(_derive_key(password, salt)).decrypt(token.encode())
    return plaintext.decode()


def dump_records(records: list[RecordPayload]) -> Path:
    DEBUG_DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEBUG_DUMP_PATH.write_text(json.dumps({
        "dumped_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }, indent=2))
    DEBUG_DUMP_PATH.chmod(0o600)
    return DEBUG_DUMP_PATH


def _load_raw_vault() -> dict[str, object] | None:
    if not vault_exists():
        return None
    return json.loads(VAULT_PATH.read_text())


def _decrypt_vault_payload(password: str, raw: dict[str, object]) -> bytes:
    salt = base64.b64decode(raw["salt"])
    return Fernet(_derive_key(password, salt)).decrypt(str(raw["data"]).encode())


def _needs_secret_migration(raw: dict[str, object], records: object) -> bool:
    if not isinstance(records, list):
        return False
    return raw.get("version") != VAULT_VERSION or any(
        isinstance(record, dict)
        and ("value" in record or "type" not in record)
        for record in records
    )


def _normalize_records_for_save(
    password: str,
    records: list[RecordPayload | LegacyRecordPayload],
    inner_salt: bytes,
) -> list[RecordPayload]:
    normalized_records: list[RecordPayload] = []
    for record in records:
        if "secret" in record:
            normalized_records.append(_normalize_record(record))
        elif isinstance(record.get("value"), str):
            normalized_records.append(_normalize_record({
                **record,
                "secret": _encrypt_secret_with_salt(password, inner_salt, record["value"]),
            }))
    return normalized_records


def _normalize_record(record: RecordPayload) -> RecordPayload:
    return {
        "name": record["name"],
        "type": record.get("type") or "SimpleCredentials",
        "url": record.get("url") or None,
        "login": record.get("login") or None,
        "secret": record["secret"],
    }


def _encrypt_secret_with_salt(password: str, salt: bytes, value: str) -> str:
    return Fernet(_derive_key(password, salt)).encrypt(value.encode()).decode()


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
