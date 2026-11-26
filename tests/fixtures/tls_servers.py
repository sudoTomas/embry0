"""A TLS model server on 127.0.0.1 whose certificate holds one name, signed by a CA of the test's own.

``tls_server`` makes every httpx client created after it trust that CA instead of the public ones, the way a
deployment's client trusts a public CA: through ``certifi.where()``, which httpx reads when it builds a client's
TLS context. Each request the server answers is kept in ``seen`` with its ``Host`` and its ``Authorization``, and
each TLS handshake's server name in ``handshakes``, so a test can tell which key reached the server and how many
connections it took.
"""

from __future__ import annotations

import datetime as dt
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import certifi
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass
class TlsServer:
    """The running server: its port, and each request it answered."""

    port: int
    seen: list[dict[str, Any]] = field(default_factory=list)
    handshakes: list[str | None] = field(default_factory=list)


def _certificates(tmp: Path, name: str) -> tuple[Path, Path, Path]:
    """A CA, and a leaf certificate for ``name`` alone that it signed, with the leaf's key: their PEM files."""
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    paths = tmp / "ca.pem", tmp / "leaf.pem", tmp / "key.pem"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return paths


async def tls_server(aiohttp_server: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> TlsServer:
    """A server on 127.0.0.1 whose certificate holds ``name`` alone, answering every path with a chat completion;
    every httpx client built after this call trusts its CA."""
    ca, leaf, key = _certificates(tmp_path, name)
    monkeypatch.setattr(certifi, "where", lambda: str(ca))
    seen: list[dict[str, Any]] = []
    handshakes: list[str | None] = []

    async def answer(request: web.Request) -> web.Response:
        seen.append({"host": request.headers.get("Host"), "authorization": request.headers.get("Authorization")})
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": "{}"}}], "data": []})

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(leaf, key)
    context.sni_callback = lambda _sock, sni, _ctx: handshakes.append(sni)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", answer)
    server = await aiohttp_server(app, ssl=context)
    return TlsServer(port=server.port, seen=seen, handshakes=handshakes)
