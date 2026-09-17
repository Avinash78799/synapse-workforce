"""Tests for external sources connectivity: Astra/OpenAI GPT models, embeddings, and connectors."""
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from core.config import settings
from core.models import providers
from core.models.providers import (
    get_model,
    ChatCompletionsProvider,
    SimulationProvider,
    get_embedding_provider,
    SimulationEmbeddings,
    ExternalEmbeddingProvider,
)
from core.memory.knowledge import format_retrieved_context
from core.security.secrets import VaultSecrets
from core.connectors import get_connector, SimulationConnector, HttpConnector
from core.connectors.registry import register_connector


def test_astra_and_openai_model_provider_resolution(monkeypatch):
    monkeypatch.setattr(providers, 'settings', replace(settings, model_provider='astra'))
    assert isinstance(get_model(), ChatCompletionsProvider)

    monkeypatch.setattr(providers, 'settings', replace(settings, model_provider='openai'))
    assert isinstance(get_model(), ChatCompletionsProvider)

    monkeypatch.setattr(providers, 'settings', replace(settings, model_provider='simulation'))
    assert isinstance(get_model(), SimulationProvider)


def test_direct_api_key_resolution_without_vault(monkeypatch):
    monkeypatch.setattr('core.security.secrets.settings', replace(settings, llm_api_key='test-astra-direct-key-12345', app_env='development'))
    resolved = VaultSecrets().read(settings.llm_secret_ref)
    assert resolved == 'test-astra-direct-key-12345'


def test_direct_api_key_resolution_blocked_in_production(monkeypatch):
    monkeypatch.setattr('core.security.secrets.settings', replace(settings, llm_api_key='test-astra-direct-key-12345', app_env='production'))
    with pytest.raises(RuntimeError, match='Secret unavailable from Vault'):
        VaultSecrets().read(settings.llm_secret_ref)


def test_chat_completions_markdown_fence_stripping(monkeypatch):
    tenant = str(uuid4())
    monkeypatch.setattr(providers, 'settings', replace(settings,
        llm_base_url='https://api.astra.example.test/v1',
        llm_model='gpt-model-astra',
        input_usd_per_million=2.5,
        output_usd_per_million=10.0,
        daily_model_budget_usd=5.0))
    monkeypatch.setattr(providers.VaultSecrets, 'read', lambda *_: 'mock-key')

    # Mock Redis budget check
    mock_redis = SimpleNamespace(eval=lambda *a, **k: 1)
    monkeypatch.setattr(providers.Redis, 'from_url', lambda *_: mock_redis)

    # Return markdown code block json
    expected_data = {
        'intent': 'daily_operations',
        'summary': 'Execute scheduled daily operations on orders',
        'tenant_id': tenant,
        'agent_scope': 'operations',
        'target_entity_id': None,
        'risk_classification': 'low',
        'vector_search_query': 'orders payments shipments'
    }
    raw_content = f"```json\n{json.dumps(expected_data, indent=2)}\n```"
    body = {
        'choices': [{'message': {'content': raw_content}}],
        'usage': {'prompt_tokens': 120, 'completion_tokens': 45}
    }
    monkeypatch.setattr(providers.httpx, 'post', lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: body))

    result = ChatCompletionsProvider().understand('operations', "Run today's operations", tenant)
    assert result.suggestion.intent == 'daily_operations'
    assert result.suggestion.summary == 'Execute scheduled daily operations on orders'
    assert result.input_tokens == 120
    assert result.output_tokens == 45
    assert result.cost_usd > 0


def test_embedding_provider_factory(monkeypatch):
    monkeypatch.setattr(providers, 'settings', replace(settings, embedding_provider='simulation'))
    assert isinstance(get_embedding_provider(), SimulationEmbeddings)

    monkeypatch.setattr(providers, 'settings', replace(settings,
        embedding_provider='astra',
        embedding_base_url='https://api.astra.example.test/v1',
        embedding_model='text-embedding-3-small',
        llm_api_key='test-key'))
    assert isinstance(get_embedding_provider(), ExternalEmbeddingProvider)


def test_format_retrieved_context_contract():
    chunks = [
        {'document_id': 'refund_policy_v1.pdf', 'content': 'Refunds are permitted within 30 days.'},
        {'document_id': 'shipping_terms.txt', 'content': 'Delayed shipments can be escalated.'}
    ]
    formatted = format_retrieved_context(chunks)
    assert '[Doc: refund_policy_v1.pdf]' in formatted
    assert 'Refunds are permitted within 30 days.' in formatted
    assert '[Doc: shipping_terms.txt]' in formatted


def test_connector_routing_and_http_headers(monkeypatch):
    # Simulation routing by default
    conn = get_connector('payment.refund')
    assert isinstance(conn, SimulationConnector)

    patched_settings = replace(settings,
        payment_connector='http',
        external_connector_base_url='https://sandbox.payments.example.test',
        external_connector_secret='secret-sig-key')
    monkeypatch.setattr('core.connectors.registry.settings', patched_settings)
    monkeypatch.setattr('core.connectors.http_adapter.settings', patched_settings)

    http_conn = get_connector('payment.refund')
    assert isinstance(http_conn, HttpConnector)

    headers = http_conn._headers('idem-key-99', b'{"amount":100}')
    assert headers['Idempotency-Key'] == 'idem-key-99'
    assert 'X-Signature-SHA256' in headers
    assert len(headers['X-Signature-SHA256']) == 64
