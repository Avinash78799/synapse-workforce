import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from core.config import settings
from core.models.pool import MultiModelPool

settings = replace(settings,
    groq_input_usd_per_million=1,
    groq_output_usd_per_million=2,
    google_input_usd_per_million=1,
    google_output_usd_per_million=2,
    openrouter_input_usd_per_million=0,
    openrouter_output_usd_per_million=0
)


@pytest.fixture(autouse=True)
def isolated_cooldowns():
    MultiModelPool._cooldowns.clear()
    yield
    MultiModelPool._cooldowns.clear()


def test_support_agent_speed_tier_prioritizes_groq(monkeypatch):
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_test',
        google_api_key='AIzaSy_test',
        openrouter_api_key='sk-or-test'))

    pool = MultiModelPool()
    providers = pool.get_providers(role='support', model_tier='speed')
    assert providers[0].name == 'groq'
    assert providers[1].name == 'google'
    assert providers[2].name == 'openrouter'


def test_operations_agent_reasoning_tier_prioritizes_gemini(monkeypatch):
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_test',
        google_api_key='AIzaSy_test',
        openrouter_api_key='sk-or-test'))

    pool = MultiModelPool()
    providers = pool.get_providers(role='operations', model_tier='reasoning')
    assert providers[0].name == 'google'
    assert providers[1].name == 'groq'
    assert providers[2].name == 'openrouter'


def test_auditor_agent_scale_tier_prioritizes_openrouter(monkeypatch):
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_test',
        google_api_key='AIzaSy_test',
        openrouter_api_key='sk-or-test'))

    pool = MultiModelPool()
    providers = pool.get_providers(role='auditor', model_tier='broad_scale')
    assert providers[0].name == 'openrouter'
    assert providers[1].name == 'google'
    assert providers[2].name == 'groq'


def test_operations_agent_dispatches_google_gemini_first(monkeypatch):
    tenant = str(uuid4())
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_valid_key',
        google_api_key='AIzaSy_valid_key',
        openrouter_api_key='sk-or-valid_key'))

    mock_redis = SimpleNamespace(eval=lambda *a, **k: 1)
    monkeypatch.setattr('core.models.pool.Redis', SimpleNamespace(from_url=lambda *_: mock_redis))

    dispatched_urls = []

    def mock_post(url, **kwargs):
        dispatched_urls.append(url)
        content = json.dumps({
            'intent': 'daily_operations',
            'summary': 'Processed deeply via Gemini',
            'tenant_id': tenant,
            'agent_scope': 'operations',
            'target_entity_id': None,
            'risk_classification': 'medium',
            'vector_search_query': 'shipments'
        })
        body = {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 20}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr('core.models.pool.httpx.post', mock_post)

    pool = MultiModelPool()
    result = pool.understand('operations', "Inspect delayed shipments and inventory", tenant, model_tier='reasoning')

    assert result.suggestion.intent == 'daily_operations'
    assert 'Gemini' in result.suggestion.summary
    assert len(dispatched_urls) == 1
    assert 'generativelanguage.googleapis.com' in dispatched_urls[0]


def test_failover_from_gemini_to_groq_when_gemini_cooling(monkeypatch):
    tenant = str(uuid4())
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_valid_key',
        google_api_key='AIzaSy_valid_key',
        openrouter_api_key='sk-or-valid_key'))

    mock_redis = SimpleNamespace(eval=lambda *a, **k: 1)
    monkeypatch.setattr('core.models.pool.Redis', SimpleNamespace(from_url=lambda *_: mock_redis))

    pool = MultiModelPool()
    pool.set_cooldown('google', duration_seconds=60)

    dispatched_urls = []

    def mock_post(url, **kwargs):
        dispatched_urls.append(url)
        content = json.dumps({
            'intent': 'daily_operations',
            'summary': 'Processed fallback via Groq',
            'tenant_id': tenant,
            'agent_scope': 'operations',
            'target_entity_id': None,
            'risk_classification': 'low',
            'vector_search_query': 'orders'
        })
        body = {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 20}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr('core.models.pool.httpx.post', mock_post)

    result = pool.understand('operations', "Inspect delayed shipments", tenant, model_tier='reasoning')

    assert 'Groq' in result.suggestion.summary
    assert len(dispatched_urls) == 1
    assert 'groq.com' in dispatched_urls[0]
