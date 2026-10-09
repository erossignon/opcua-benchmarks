# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
#    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

"""Generate temporary self-signed identities for encrypted benchmark workers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path


def require_certificate_tool() -> None:
    """Check certificate-generation dependencies before starting a matrix."""
    try:
        import cryptography.x509
    except ImportError as error:
        raise RuntimeError("Certificate generation requires pip install -r requirements.txt") from error


def create_certificate(app_uri: str, common_name: str, key_path: Path, certificate_path: Path) -> None:
    """Write a DER RSA-2048 key and SHA-256 certificate valid for one year."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.UniformResourceIdentifier(app_uri),
            x509.DNSName("localhost"),
            x509.IPAddress(ip_address("127.0.0.1")),
        ]), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True,
            key_encipherment=True, data_encipherment=True,
            key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH,
        ]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))


_PEER_ROLE = {"client": "server", "server": "client"}


def secure_arguments(
    role: str,
    material: Mapping[str, str],
    policy: str = "Basic256Sha256",
    trusts: str | None = None,
) -> list[str]:
    """Return security flags and certificate paths for a worker; trust its peer by default."""
    trusts = trusts or _PEER_ROLE.get(role, "")
    if not trusts:
        raise ValueError(f"role {role!r} has no default peer to trust; name one with trusts=")
    return [
        "--security",
        policy,
        "--certificate",
        material[f"{role}_cert"],
        "--private-key",
        material[f"{role}_key"],
        "--trust-certificate",
        material[f"{trusts}_cert"],
    ]


def certificate_material(directory: Path, roles: Sequence[str] = ("client", "server")) -> dict[str, str]:
    """Generate one identity per role and return paths keyed by role_key and role_cert."""
    material: dict[str, str] = {}
    for role in roles:
        key_path = directory / f"{role}-key.der"
        certificate_path = directory / f"{role}-cert.der"
        create_certificate(f"urn:o6:benchmark:{role}", f"o6 benchmark {role}", key_path, certificate_path)
        material[f"{role}_key"] = str(key_path)
        material[f"{role}_cert"] = str(certificate_path)
    return material
