import hashlib
import os
import secrets


def hash_password(password: str) -> str:
    """Хэширует пароль с использованием PBKDF2-HMAC-SHA256 и случайной соли."""
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        100_000,
    )
    return f"{salt}${dk.hex()}"


def verify_password(password: str, hashed: str | None) -> bool:
    """Проверяет соответствие пароля хэшу."""
    if not hashed or "$" not in hashed:
        return False
    try:
        salt, dk_hex = hashed.split("$", 1)
        expected_dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            100_000,
        )
        return secrets.compare_digest(dk_hex, expected_dk.hex())
    except Exception:  # noqa: BLE001
        return False
