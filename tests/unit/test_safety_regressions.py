"""Regression tests for fail-closed model, connector and embedding boundaries."""
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from core.models import pool as model_pool, providers
from core.connectors.http_adapter import HttpConnector, ConnectorUnavailable
from core.connectors import registry


@pytest.fixture(autouse=True)
def clear_cooldowns():
    model_pool.MultiModelPool._cooldowns.clear()
    yield
    model_pool.MultiModelPool._cooldowns.clear()


def configured(monkeypatch, eval_fn):
    monkeypatch.setattr(model_pool,'settings',replace(model_pool.settings,groq_api_key='fake',google_api_key='fake',openrouter_api_key='fake',daily_model_budget_usd=5,groq_input_usd_per_million=1,groq_output_usd_per_million=2,google_input_usd_per_million=1,google_output_usd_per_million=2,openrouter_input_usd_per_million=0,openrouter_output_usd_per_million=0))
    monkeypatch.setattr(model_pool,'Redis',SimpleNamespace(from_url=lambda *_:SimpleNamespace(eval=eval_fn)))
    return model_pool.MultiModelPool()


def test_redis_failure_blocks_all_model_dispatch(monkeypatch):
    def broken(*a): raise ConnectionError('offline')
    pool=configured(monkeypatch,broken)
    monkeypatch.setattr(pool,'_dispatch',lambda *a:pytest.fail('Model was dispatched without budget'))
    with pytest.raises(RuntimeError,match='budget service unavailable'): pool.understand('operations','Run daily operations',str(uuid4()))


def test_reserve_each_fallback_and_stop_when_budget_exhausted(monkeypatch):
    reservations=[]; calls=[]
    def reserve(*a):
        reservations.append(a)
        return int(len(reservations)==1)
    pool=configured(monkeypatch,reserve)
    def dispatch(*a): calls.append(a[0].name); raise TimeoutError()
    monkeypatch.setattr(pool,'_dispatch',dispatch)
    with pytest.raises(RuntimeError,match='exhausted'): pool.understand('operations','Run daily operations',str(uuid4()))
    assert calls==['groq'] and len(reservations)==2
    assert reservations[0][-2] >= 1000 * pool.get_providers()[0].output_usd_per_million


def test_cooling_providers_not_retried(monkeypatch):
    pool=configured(monkeypatch,lambda *a:pytest.fail('Should not reserve during cooldown'))
    for provider in pool.get_providers(): pool.set_cooldown(provider.name)
    with pytest.raises(RuntimeError,match='cooldown'): pool.understand('operations','Run',str(uuid4()))


@pytest.mark.parametrize('usage',[{}, {'prompt_tokens':-1,'completion_tokens':2},{'prompt_tokens':True,'completion_tokens':2},{'prompt_tokens':1,'completion_tokens':1001}])
def test_invalid_usage_rejected(monkeypatch,usage):
    tenant=str(uuid4()); pool=model_pool.MultiModelPool()
    body={'choices':[{'message':{'content':json.dumps({'intent':'daily_operations','summary':'ok','tenant_id':tenant,'agent_scope':'operations','vector_search_query':'orders'})}}], 'usage':usage}
    monkeypatch.setattr(model_pool.httpx,'post',lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:body))
    with pytest.raises(ValueError): pool._dispatch(pool.get_providers()[0],'system','objective',tenant,'operations',None,1000)


class ScopedConnection:
    def execute(self,*a): return SimpleNamespace(fetchone=lambda:{'tenant_id':'tenant','agent_id':'agent'})


@pytest.mark.parametrize('operation',['investigate','verify','reconcile','execute'])
def test_external_outage_never_uses_simulation(monkeypatch,operation):
    from simulations.ecommerce import store
    for name in ['investigate','verify','reconcile','execute']:
        monkeypatch.setattr(store,name,lambda *a:pytest.fail('Simulation fallback used'))
    monkeypatch.setattr(httpx,'post',lambda *a,**k:(_ for _ in ()).throw(httpx.ReadTimeout('unknown')))
    connector=HttpConnector('https://bridge.example.test','test-secret')
    args={'investigate':(ScopedConnection(),{}), 'verify':(ScopedConnection(),'payment.refund',{}),
          'reconcile':(ScopedConnection(),'payment.refund',{},'key'),
          'execute':(ScopedConnection(),'tenant','agent','payment.refund',{},'key')}[operation]
    with pytest.raises(ConnectorUnavailable): getattr(connector,operation)(*args)


@pytest.mark.parametrize('response',[{'confirmed':False},{'confirmed':False,'status':'pending'},{'verified':'false'}])
def test_ambiguous_evidence_never_becomes_success(monkeypatch,response):
    connector=HttpConnector('https://bridge.example.test','secret')
    monkeypatch.setattr(connector,'_request',lambda *a:response)
    with pytest.raises(ConnectorUnavailable):
        if 'verified' in response: connector.verify(None,'payment.refund',{})
        else: connector.reconcile(None,'payment.refund',{},'key')


def test_unknown_connector_fails_closed(monkeypatch):
    monkeypatch.setattr(registry,'settings',replace(registry.settings,payment_connector='typo'))
    with pytest.raises(RuntimeError,match='Unknown'): registry.get_connector('payment.refund')


@pytest.mark.parametrize('vector',[[1.0]*1536,[float('nan')]*64,[0.0]*64])
def test_external_embedding_rejects_wrong_representation(monkeypatch,vector):
    monkeypatch.setattr(providers,'settings',replace(providers.settings,embedding_model='explicit-model'))
    monkeypatch.setattr(httpx,'post',lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'embedding':vector}]}))
    with pytest.raises(RuntimeError,match='no fallback'): providers.ExternalEmbeddingProvider('https://embedding.example.test',api_key='fake').embed('test')


def test_external_embedding_redacts_input(monkeypatch):
    monkeypatch.setattr(providers,'settings',replace(providers.settings,embedding_model='explicit-model'))
    def post(*a,**k):
        assert 'person@example.com' not in k['json']['input']
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'embedding':[1.0]*64}]})
    monkeypatch.setattr(httpx,'post',post)
    assert len(providers.ExternalEmbeddingProvider('https://embedding.example.test',api_key='fake').embed('person@example.com'))==64


def test_missing_price_schedule_blocks_dispatch(monkeypatch):
    pool=configured(monkeypatch,lambda *a:pytest.fail('Reservation without a price schedule'))
    monkeypatch.setattr(model_pool,'settings',replace(model_pool.settings,groq_input_usd_per_million=-1))
    monkeypatch.setattr(pool,'_dispatch',lambda *a:pytest.fail('Unpriced dispatch'))
    with pytest.raises(RuntimeError,match='prices'): pool.understand('operations','Run',str(uuid4()))
