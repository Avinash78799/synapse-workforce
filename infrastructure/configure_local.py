"""One-time, no-overwrite local setup. Generates passwords without cloud accounts."""
from pathlib import Path
import secrets

root=Path(__file__).resolve().parents[1]
env=root/'.env'
if env.exists():
    raise SystemExit('.env already exists; left unchanged.')
passwords={key:secrets.token_urlsafe(24) for key in ['POSTGRES_PASSWORD','APP_DB_PASSWORD','AUTH_DB_PASSWORD','DISPATCH_DB_PASSWORD','SEED_PASSWORD']}
env.write_text('\n'.join(f'{k}={v}' for k,v in passwords.items())+'\nWEB_ORIGIN=https://localhost\nSECURE_COOKIES=true\nMODEL_PROVIDER=simulation\nLLM_BASE_URL=\nLLM_MODEL=\nLLM_API_KEY=\nASTRA_API_KEY=\nLLM_SECRET_REF=platform/llm\nLLM_INPUT_USD_PER_MILLION=0\nLLM_OUTPUT_USD_PER_MILLION=0\nDAILY_MODEL_BUDGET_USD=5\nEMBEDDING_PROVIDER=simulation\nEMBEDDING_BASE_URL=\nEMBEDDING_MODEL=text-embedding-3-small\nCOMMERCE_CONNECTOR=simulation\nPAYMENT_CONNECTOR=simulation\nHELPDESK_CONNECTOR=simulation\nEXTERNAL_CONNECTOR_BASE_URL=\nEXTERNAL_CONNECTOR_SECRET=\nVAULT_ADDR=http://vault:8200\n')
env.chmod(0o600)
secret_dir=root/'.secrets'
secret_dir.mkdir(exist_ok=True)
(secret_dir/'vault_token').write_text(secrets.token_urlsafe(32))
(secret_dir/'vault_token').chmod(0o644)  # container's unprivileged user must read the bind-mounted file
secret_dir.chmod(0o700)
from configure_audit import configure
configure()
from generate_ssl_certs import generate_ssl_certificates
generate_ssl_certificates()
print('Local settings generated. The sign-in account is owner@acme.local.')
print('Your password is SEED_PASSWORD in .env. Keep this file private.')
