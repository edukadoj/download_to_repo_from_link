#!/usr/bin/env python3
# ==============================================================================
# crypto_utils.py – Version 1.0.0
# ==============================================================================
# General‑purpose encryption/decryption helpers.
#
# Uses the same XOR‑with‑SHA256‑keystream algorithm employed by the remote
# control system.  Every function accepts an optional `encryption_key` argument;
# if omitted, the key is taken from the environment variable `KEY`.
#
# Functions:
#   encrypt_string(plain_text, encryption_key=None) -> str
#   decode_string(encoded_str, encryption_key=None) -> str or None
#
# These functions are completely independent of the rest of the project and
# can be reused in any workflow that needs symmetric, key‑based text encoding.
# ==============================================================================

import base64
import hashlib
import os
from typing import Optional


def _xor_with_keystream(key_bytes: bytes, data_bytes: bytes) -> bytes:
    """XOR every byte of data_bytes with a hash‑derived keystream."""
    result = bytearray()
    for i, b in enumerate(data_bytes):
        h = hashlib.sha256(key_bytes + str(i).encode()).digest()
        result.append(b ^ h[0])
    return bytes(result)


def encrypt_string(plain_text: str, encryption_key: Optional[str] = None) -> str:
    """
    Encrypt a plain‑text string and return a Base64‑encoded representation.
    If no `encryption_key` is supplied, the environment variable `KEY` is used.
    """
    if encryption_key is None:
        encryption_key = os.environ.get("KEY", "")
    key_bytes = encryption_key.encode("utf-8")
    plain_bytes = plain_text.encode("utf-8")
    cipher = _xor_with_keystream(key_bytes, plain_bytes)
    return base64.b64encode(cipher).decode()


def decode_string(encoded_str: str, encryption_key: Optional[str] = None) -> Optional[str]:
    """
    Decode a Base64‑encoded ciphertext back to the original plain text.
    Returns `None` if decoding fails.

    If no `encryption_key` is supplied, the environment variable `KEY` is used.
    """
    try:
        raw = base64.b64decode(encoded_str)
    except Exception:
        return None

    if encryption_key is None:
        encryption_key = os.environ.get("KEY", "")
    key_bytes = encryption_key.encode("utf-8")
    try:
        decoded = _xor_with_keystream(key_bytes, raw)
        return decoded.decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Self‑test (run only when executed directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test = "Hello, world!"
    enc = encrypt_string(test, "secret")
    dec = decode_string(enc, "secret")
    assert dec == test, "Round‑trip failed"
    print("crypto_utils self‑test passed.")
