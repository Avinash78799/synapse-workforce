"""Create the local webhook master key once; preserve existing configuration."""
from pathlib import Path
import secrets


def configure():
    folder = Path(__file__).resolve().parents[1] / '.secrets'
    folder.mkdir(exist_ok=True)
    folder.chmod(0o700)
    path = folder / 'webhook_signing_key'
    try:
        with path.open('x') as handle:
            handle.write(secrets.token_urlsafe(48))
        path.chmod(0o644)  # Unprivileged container reads it; parent directory is owner-only.
    except FileExistsError:
        pass
    print('Webhook signing key configured; existing key preserved. Keep it outside SQL and source control.')


if __name__ == '__main__':
    configure()
