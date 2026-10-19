"""Шифрование токенов ботов карго (ТЗ, раздел 10)."""
import hashlib

from cryptography.fernet import Fernet

from cargo_platform.config import config

_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        if not config.fernet_key:
            raise RuntimeError("FERNET_KEY не задан. Создайте: python -m cargo_platform.tools.genkey")
        _fernet = Fernet(config.fernet_key.encode())
    return _fernet


def encrypt(text: str) -> str:
    return _f().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _f().decrypt(token.encode()).decode()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
