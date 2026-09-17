"""Generates development/staging self-signed TLS/SSL X.509 certificates.

Creates:
- .secrets/certs/cert.pem (X.509 Certificate with SAN)
- .secrets/certs/key.pem (RSA Private Key)

Configured with Subject Alternative Names (SAN) for:
- localhost, *.localhost, host.docker.internal, 127.0.0.1, ::1
"""
import os
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_ssl_certificates(
    cert_dir: Path | None = None,
    domain: str = "localhost",
    days_valid: int = 365
) -> tuple[Path, Path]:
    """Generates an RSA-2048 self-signed TLS certificate with SAN for HTTPS."""
    if cert_dir is None:
        cert_dir = Path(__file__).resolve().parents[1] / ".secrets" / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)

    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"

    print(f"Generating TLS/SSL Private Key and X.509 Certificate in: {cert_dir}")

    # 1. Generate Private Key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )

    # 2. Build Subject & Issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Karnataka"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Bengaluru"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AI Agent Workforce"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Platform Security"),
        x509.NameAttribute(NameOID.COMMON_NAME, domain),
    ])

    # 3. Build Subject Alternative Names (SAN)
    san_list = [
        x509.DNSName("localhost"),
        x509.DNSName("*.localhost"),
        x509.DNSName("host.docker.internal"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    if domain != "localhost":
        san_list.append(x509.DNSName(domain))

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False
            ),
            critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False
        )
        .sign(key, hashes.SHA256())
    )

    # 4. Write Private Key (PEM format)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    key_path.write_bytes(key_pem)

    # 5. Write Certificate (PEM format)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(cert_pem)

    # Set file permissions if on Unix/POSIX
    try:
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
    except Exception:
        pass

    print(f" [OK] Certificate written to: {cert_path}")
    print(f" [OK] Private Key written to: {key_path}")
    return cert_path, key_path


if __name__ == "__main__":
    generate_ssl_certificates()
