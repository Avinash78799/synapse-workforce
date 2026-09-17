import os
from pathlib import Path
from dataclasses import dataclass

_env_path = Path(__file__).resolve().parents[1] / '.env'
if _env_path.exists():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'postgresql://wf_app:local-app@localhost:5432/workforce')
    auth_database_url: str = os.getenv('AUTH_DATABASE_URL', 'postgresql://wf_auth:local-auth@localhost:5432/workforce')
    redis_url: str = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    app_env: str = os.getenv('APP_ENV', 'development')
    web_origin: str = os.getenv('WEB_ORIGIN', 'http://localhost:3000')
    secure_cookies: bool = os.getenv('SECURE_COOKIES', 'false') == 'true'
    model_provider: str = os.getenv('MODEL_PROVIDER', 'simulation')
    llm_base_url: str = os.getenv('LLM_BASE_URL', '')
    llm_model: str = os.getenv('LLM_MODEL', '')
    llm_secret_ref: str = os.getenv('LLM_SECRET_REF', 'platform/llm')
    input_usd_per_million: float = float(os.getenv('LLM_INPUT_USD_PER_MILLION','0'))
    output_usd_per_million: float = float(os.getenv('LLM_OUTPUT_USD_PER_MILLION','0'))
    daily_model_budget_usd: float = float(os.getenv('DAILY_MODEL_BUDGET_USD','5'))
    llm_api_key: str = os.getenv('LLM_API_KEY', os.getenv('ASTRA_API_KEY', ''))
    # Multi-model pool settings: Groq (fastest), Google Gemini, OpenRouter
    groq_api_key: str = os.getenv('GROQ_API_KEY', '')
    groq_model: str = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
    groq_base_url: str = os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
    google_api_key: str = os.getenv('GOOGLE_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    google_model: str = os.getenv('GOOGLE_MODEL', 'gemini-2.0-flash')
    google_base_url: str = os.getenv('GOOGLE_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
    openrouter_api_key: str = os.getenv('OPENROUTER_API_KEY', '')
    openrouter_model: str = os.getenv('OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free')
    openrouter_base_url: str = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
    model_cooldown_seconds: int = int(os.getenv('MODEL_COOLDOWN_SECONDS', '60'))
    embedding_provider: str = os.getenv('EMBEDDING_PROVIDER', 'simulation')
    embedding_base_url: str = os.getenv('EMBEDDING_BASE_URL', '')
    embedding_model: str = os.getenv('EMBEDDING_MODEL', '')
    commerce_connector: str = os.getenv('COMMERCE_CONNECTOR', 'simulation')
    payment_connector: str = os.getenv('PAYMENT_CONNECTOR', 'simulation')
    helpdesk_connector: str = os.getenv('HELPDESK_CONNECTOR', 'simulation')
    external_connector_base_url: str = os.getenv('EXTERNAL_CONNECTOR_BASE_URL', '')
    external_connector_secret: str = os.getenv('EXTERNAL_CONNECTOR_SECRET', '')
    vault_addr: str = os.getenv('VAULT_ADDR', 'http://localhost:8200')
    vault_token_file: str = os.getenv('VAULT_TOKEN_FILE', '/run/secrets/vault_token')
    dpo_name: str = os.getenv('DPO_NAME', 'Chief Data Protection Officer')
    dpo_email: str = os.getenv('DPO_EMAIL', 'dpo@acme.local')
    dpo_phone: str = os.getenv('DPO_PHONE', '+91-80-5555-0199')
    privacy_notice_url: str = os.getenv('PRIVACY_NOTICE_URL', 'https://acme.local/privacy')
    max_steps: int = 100
    max_run_tokens: int = 4000
    max_runs_per_hour: int = 30


settings = Settings()
