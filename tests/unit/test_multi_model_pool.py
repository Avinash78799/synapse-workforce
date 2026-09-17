"""Tests for multi-model adaptive routing pool: Groq, Google Gemini, OpenRouter."""
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
import httpx
from core.config import settings
from core.models.pool import MultiModelPool, ProviderNode
from core.models.providers import get_model

# Explicit test price schedule; not a production price claim.
settings = replace(settings, groq_input_usd_per_million=1, groq_output_usd_per_million=2, google_input_usd_per_million=1, google_output_usd_per_million=2, openrouter_input_usd_per_million=0, openrouter_output_usd_per_million=0)


@pytest.fixture(autouse=True)
def isolated_cooldowns():
    MultiModelPool._cooldowns.clear()
    yield
    MultiModelPool._cooldowns.clear()


def test_multi_model_pool_priority_fastest_first(monkeypatch):
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_test_1',
        google_api_key='AIzaSy_test_2',
        openrouter_api_key='sk-or-test_3'))

    pool = MultiModelPool()
    providers = pool.get_providers()
    assert len(providers) >= 3
    assert providers[0].name == 'groq'
    assert providers[0].priority == 1
    assert providers[1].name == 'google'
    assert providers[1].priority == 2
    assert providers[2].name == 'openrouter'
    assert providers[2].priority == 3


def test_get_model_resolves_multi_provider(monkeypatch):
    monkeypatch.setattr('core.models.providers.settings', replace(settings, model_provider='multi'))
    assert isinstance(get_model(), MultiModelPool)

    monkeypatch.setattr('core.models.providers.settings', replace(settings, model_provider='pool'))
    assert isinstance(get_model(), MultiModelPool)

    monkeypatch.setattr('core.models.providers.settings', replace(settings, model_provider='auto'))
    assert isinstance(get_model(), MultiModelPool)


def test_fastest_provider_groq_dispatched_on_normal_traffic(monkeypatch):
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
            'summary': 'Processed fast via Groq',
            'tenant_id': tenant,
            'agent_scope': 'operations',
            'target_entity_id': None,
            'risk_classification': 'low',
            'vector_search_query': 'orders'
        })
        body = {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 20}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr('core.models.pool.httpx.post', mock_post)

    pool = MultiModelPool()
    result = pool.understand('operations', "Run today's operations", tenant)

    assert result.suggestion.intent == 'daily_operations'
    assert 'Groq' in result.suggestion.summary
    assert len(dispatched_urls) == 1
    assert 'groq.com' in dispatched_urls[0]


def test_automatic_failover_from_groq_to_google_on_rate_limit(monkeypatch):
    tenant = str(uuid4())
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_key',
        google_api_key='AIzaSy_key',
        openrouter_api_key='sk-or-key',
        model_cooldown_seconds=60))

    mock_redis = SimpleNamespace(eval=lambda *a, **k: 1)
    monkeypatch.setattr('core.models.pool.Redis', SimpleNamespace(from_url=lambda *_: mock_redis))

    dispatched_urls = []

    def mock_post(url, **kwargs):
        dispatched_urls.append(url)
        if 'groq.com' in url:
            # Simulate HTTP 429 Rate Limit on Groq
            req = httpx.Request('POST', url)
            resp = httpx.Response(429, request=req, text='Rate limit exceeded')
            raise httpx.HTTPStatusError('429 Rate Limit', request=req, response=resp)

        # Google Gemini succeeds
        content = json.dumps({
            'intent': 'daily_operations',
            'summary': 'Processed successfully on Google Gemini failover',
            'tenant_id': tenant,
            'agent_scope': 'operations',
            'target_entity_id': None,
            'risk_classification': 'low',
            'vector_search_query': 'orders'
        })
        body = {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 80, 'completion_tokens': 30}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr('core.models.pool.httpx.post', mock_post)

    pool = MultiModelPool()
    result = pool.understand('operations', "Run today's operations", tenant)

    # Verified Groq was tried first, failed with 429, and Google was called immediately
    assert len(dispatched_urls) == 2
    assert 'groq.com' in dispatched_urls[0]
    assert 'generativelanguage.googleapis.com' in dispatched_urls[1]
    assert result.suggestion.summary == 'Processed successfully on Google Gemini failover'

    # Verified Groq is now in cooldown
    assert pool.is_in_cooldown('groq') is True
    assert pool.is_in_cooldown('google') is False


def test_second_stage_failover_to_openrouter(monkeypatch):
    tenant = str(uuid4())
    monkeypatch.setattr('core.models.pool.settings', replace(settings,
        groq_api_key='gsk_key',
        google_api_key='AIzaSy_key',
        openrouter_api_key='sk-or-key'))

    mock_redis = SimpleNamespace(eval=lambda *a, **k: 1)
    monkeypatch.setattr('core.models.pool.Redis', SimpleNamespace(from_url=lambda *_: mock_redis))

    dispatched_urls = []

    def mock_post(url, **kwargs):
        dispatched_urls.append(url)
        if 'groq.com' in url or 'googleapis.com' in url:
            req = httpx.Request('POST', url)
            resp = httpx.Response(503, request=req, text='Service unavailable')
            raise httpx.HTTPStatusError('503 Service Unavailable', request=req, response=resp)

        # OpenRouter succeeds
        content = json.dumps({
            'intent': 'resolve_ticket',
            'summary': 'Resolved via OpenRouter free model',
            'tenant_id': tenant,
            'agent_scope': 'support',
            'target_entity_id': 'ORD-1001',
            'risk_classification': 'low',
            'vector_search_query': 'refund'
        })
        body = {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 95, 'completion_tokens': 35}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr('core.models.pool.httpx.post', mock_post)

    pool = MultiModelPool()
    result = pool.understand('support', 'Refund duplicate', tenant, 'ORD-1001')

    # Both Groq and Google tried, followed by OpenRouter
    assert len(dispatched_urls) == 3
    assert 'groq.com' in dispatched_urls[0]
    assert 'googleapis.com' in dispatched_urls[1]
    assert 'openrouter.ai' in dispatched_urls[2]
    assert result.suggestion.intent == 'resolve_ticket'
    assert result.suggestion.summary == 'Resolved via OpenRouter free model'
