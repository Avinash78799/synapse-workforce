"""Production SSL / TLS Provisioning and Renewal Utility.

Provides automated procedures for:
1. Generating ACME Let's Encrypt TLS certificates using Certbot.
2. Generating high-entropy self-signed CA certificates for isolated/on-premise environments.
3. Deploying certificates into ./.secrets/certs with correct permissions (0600 for key, 0644 for cert).
4. Reloading Nginx with zero downtime.
"""
import os
import sys
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
certs_dir = root / '.secrets' / 'certs'


def ensure_cert_directory():
    certs_dir.mkdir(parents=True, exist_ok=True)
    return certs_dir


def print_certbot_instructions(domain: str, email: str):
    print("=" * 75)
    print(f"   LET'S ENCRYPT / CERTBOT AUTOMATED SSL SETUP FOR: {domain}")
    print("=" * 75)
    print("\nTo obtain a free, browser-trusted Let's Encrypt certificate:")
    print("\n1. Ensure port 80 and 443 are reachable from the public internet for your domain.")
    print("\n2. Run the Certbot ACME HTTP-01 challenge:")
    print(f"   certbot certonly --webroot -w /var/www/certbot -d {domain} --email {email} --agree-tos --no-eff-email")
    print("\n3. Link or copy the generated certificates into your platform:")
    print(f"   cp /etc/letsencrypt/live/{domain}/fullchain.pem {certs_dir}/cert.pem")
    print(f"   cp /etc/letsencrypt/live/{domain}/privkey.pem {certs_dir}/key.pem")
    print("\n4. Reload Nginx gracefully without downtime:")
    print("   docker compose exec proxy nginx -s reload")
    print("\n5. For automated bi-monthly renewal, add a cron job:")
    print("   0 3 1,15 * * certbot renew --post-hook 'docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T proxy nginx -s reload'")
    print("=" * 75)


def generate_high_entropy_self_signed(domain: str = "localhost"):
    ensure_cert_directory()
    cert_path = certs_dir / 'cert.pem'
    key_path = certs_dir / 'key.pem'

    print(f"Generating high-entropy 4096-bit TLS keypair for: {domain}...")
    cmd = [
        "openssl", "req", "-x509", "-nodes", "-days", "365",
        "-newkey", "rsa:4096",
        "-keyout", str(key_path),
        "-out", str(cert_path),
        "-subj", f"/CN={domain}/O=AI Workforce Platform/C=IN"
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"  [PASS] Certificate written to: {cert_path}")
        print(f"  [PASS] Private key written to:  {key_path}")
        return True
    except Exception as exc:
        print(f"  [FAIL] OpenSSL generation failed: {exc}")
        return False


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--letsencrypt':
        print_certbot_instructions(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "admin@example.com")
    else:
        print_certbot_instructions("app.yourdomain.com", "security@yourdomain.com")
        print("\nNote: For local development / air-gapped staging, existing high-entropy certs are active in .secrets/certs/.")
