"""Unit tests for FernetSecretsProvider."""

import pytest

from legion.storage.encryption import FernetSecretsProvider


@pytest.mark.asyncio
async def test_fernet_round_trip():
    p = FernetSecretsProvider(secret_key="test-key-123")
    ct = await p.encrypt("my-secret-value")
    assert ct != "my-secret-value"
    assert await p.decrypt(ct) == "my-secret-value"


@pytest.mark.asyncio
async def test_fernet_wrong_key_raises():
    from cryptography.fernet import InvalidToken

    p1 = FernetSecretsProvider(secret_key="key-a")
    p2 = FernetSecretsProvider(secret_key="key-b")
    ct = await p1.encrypt("x")
    with pytest.raises(InvalidToken):
        await p2.decrypt(ct)


@pytest.mark.asyncio
async def test_fernet_deterministic_key_nondeterministic_ciphertext():
    """Same secret_key -> same Fernet key, but ciphertexts differ per call (IV randomness)."""
    p = FernetSecretsProvider(secret_key="same-key")
    a = await p.encrypt("hello")
    b = await p.encrypt("hello")
    assert a != b
    assert await p.decrypt(a) == "hello"
    assert await p.decrypt(b) == "hello"
