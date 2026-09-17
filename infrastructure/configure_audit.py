"""Create an audit key once, including upgrades of an existing local installation."""
from pathlib import Path
import secrets


def configure():
    folder=Path(__file__).resolve().parents[1]/'.secrets'
    folder.mkdir(exist_ok=True)
    folder.chmod(0o700)
    path=folder/'audit_signing_key'
    if not path.exists():
        with path.open('x') as f:
            f.write(secrets.token_urlsafe(48))
        path.chmod(0o644)  # Read by the unprivileged container; parent is owner-only.
    print('Audit key is configured. Keep it outside the database and preserve it during upgrades.')


if __name__=='__main__':
    configure()
