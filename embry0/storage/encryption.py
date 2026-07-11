"""Secrets encryption provider — Protocol + Fernet implementation.

FernetSecretsProvider derives a Fernet key from an arbitrary password string using
PBKDF2-HMAC-SHA256, allowing the caller to use a plain secret_key without worrying
about Fernet's exact 32-byte URL-safe base64 key format.

Usage::

    provider = FernetSecretsProvider(secret_key="my-secret-password")
    ciphertext = await provider.encrypt("api-key-value")
    plaintext  = await provider.decrypt(ciphertext)
"""

import base64
import hashlib
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken

# Salt is fixed/public — security comes from the secret_key, not the salt.
# PBKDF2 is used only to expand/normalize the key to the 32-byte size Fernet needs.
# Deliberately not renamed: changing this value rotates all stored secrets.
_PBKDF2_SALT = b"legion-fernet-v1"
_PBKDF2_ITERATIONS = 390_000  # OWASP-recommended minimum for PBKDF2-HMAC-SHA256


@runtime_checkable
class SecretsProvider(Protocol):
    """Protocol for encrypting and decrypting secret strings at rest."""

    async def encrypt(self, plaintext: str) -> str:
        """Encrypt *plaintext* and return an opaque ciphertext string."""
        ...

    async def decrypt(self, ciphertext: str) -> str:
        """Decrypt *ciphertext* and return the original plaintext.

        Raises:
            cryptography.fernet.InvalidToken: if the ciphertext is invalid or the
                wrong key is used.
        """
        ...


class FernetSecretsProvider:
    """Fernet-backed SecretsProvider.

    The *secret_key* is an arbitrary UTF-8 string.  It is deterministically
    stretched to a 32-byte URL-safe base64 key via PBKDF2-HMAC-SHA256 so that
    Fernet can accept it.

    Fernet tokens are URL-safe base64-encoded and include a random 128-bit IV, so
    two calls to ``encrypt()`` with the same plaintext produce different ciphertexts.
    """

    def __init__(self, secret_key: str) -> None:
        derived = hashlib.pbkdf2_hmac(
            hash_name="sha256",
            password=secret_key.encode(),
            salt=_PBKDF2_SALT,
            iterations=_PBKDF2_ITERATIONS,
            dklen=32,
        )
        fernet_key = base64.urlsafe_b64encode(derived)
        self._fernet = Fernet(fernet_key)

    async def encrypt(self, plaintext: str) -> str:
        """Encrypt *plaintext* and return a Fernet token as a str."""
        token: bytes = self._fernet.encrypt(plaintext.encode())
        return token.decode()

    async def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet token and return the original plaintext.

        Raises:
            cryptography.fernet.InvalidToken: on bad token or wrong key.
        """
        try:
            plaintext_bytes: bytes = self._fernet.decrypt(ciphertext.encode())
        except InvalidToken:
            raise
        return plaintext_bytes.decode()
