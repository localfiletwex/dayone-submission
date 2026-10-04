"""Key derivation and image-blob encryption (AES-256-GCM)."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


def derive_key(pin: str, salt: bytes) -> bytes:
    """Derive the 256-bit database key from the device PIN."""
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(pin.encode("utf-8"))


def load_or_create_salt(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(16)
    path.write_bytes(salt)
    return salt


def encrypt_blob(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt_blob(key: bytes, blob: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)
