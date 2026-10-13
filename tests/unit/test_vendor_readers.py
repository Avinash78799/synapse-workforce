import json
import httpx
import pytest
from core.connectors import vendor_readers as v
REAL_CLIENT=httpx.Client


def transport(monkeypatch, handler):
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return REAL_CLIENT(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(v.httpx,'Client',client)


@pytest.mark.parametrize('account',['localhost:80','evil.example','foo/../bar','https://foo','foo@evil','-abc','abc-','a'*64])
def test_host_injection_rejected(account):
    with pytest.raises(ValueError):v.hostname('shopify',account)


@pytest.mark.parametrize('status,code',[(302,'vendor_unavailable'),(401,'credential_or_scope_denied'),(403,'credential_or_scope_denied'),(404,'resource_not_found'),(429,'vendor_rate_limited'),(500,'vendor_unavailable')])
def test_vendor_errors_never_expose_response(monkeypatch,status,code):
    transport(monkeypatch,lambda request:httpx.Response(status,text='secret-and-customer-data'))
    with pytest.raises(v.VendorError,match='^'+code+'$'):v.check_account('zendesk','acme','test-token')


def test_shopify_checks_account_scope_version_and_graphql_errors(monkeypatch):
    body={'data':{'shop':{'id':'gid://shopify/Shop/1','myshopifyDomain':'acme.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'}]}}}
    version=v.SHOPIFY_VERSION
    def handler(req):
        assert req.url.host=='acme.myshopify.com' and req.url.path==f'/admin/api/{v.SHOPIFY_VERSION}/graphql.json'
        assert req.headers['X-Shopify-Access-Token']=='test-token'
        assert 'mutation' not in json.loads(req.content)['query']
        return httpx.Response(200,json=body,headers={'X-Shopify-API-Version':version})
    transport(monkeypatch,handler)
    assert v.check_account('shopify','acme','test-token')['write_actions_enabled'] is False
    body['data']['shop']['myshopifyDomain']='other.myshopify.com'
    with pytest.raises(v.VendorError,match='vendor_account_mismatch'):v.check_account('shopify','acme','test-token')
    body['data']['shop']['myshopifyDomain']='acme.myshopify.com'
    body['data']['currentAppInstallation']['accessScopes']=[]
    with pytest.raises(v.VendorError,match='orders_scope_missing'):v.check_account('shopify','acme','test-token')
    body['errors']=[{'message':'sensitive vendor diagnostic'}]
    with pytest.raises(v.VendorError,match='graphql_error'):v.check_account('shopify','acme','test-token')
    version='2027-01'
    with pytest.raises(v.VendorError,match='api_version_mismatch'):v.check_account('shopify','acme','test-token')


def test_ticket_read_minimizes_data_and_binds_resource(monkeypatch):
    ticket={'id':42,'status':'open','requester_id':7,'organization_id':None,'updated_at':'2026-09-21T00:00:00Z','description':'private','email':'private@example.com'}
    def handler(req):
        assert req.method=='GET' and req.url.path=='/api/v2/tickets/42.json'
        assert req.headers['Authorization']=='Bearer test-token'
        return httpx.Response(200,json={'ticket':ticket})
    transport(monkeypatch,handler)
    result=v.read_resource('zendesk','acme','test-token','42')
    assert result['workflow_execution_enabled'] is False and 'private' not in json.dumps(result)
    ticket['id']=43
    with pytest.raises(v.VendorError,match='resource_binding_mismatch'):v.read_resource('zendesk','acme','test-token','42')


def test_bounded_response_and_network_failure(monkeypatch):
    transport(monkeypatch,lambda req:httpx.Response(200,content=b' '*(v.MAX_RESPONSE+1)))
    with pytest.raises(v.VendorError,match='response_too_large'):v.check_account('zendesk','acme','test-token')
    def fail(req):raise httpx.ReadTimeout('sensitive URL/token')
    transport(monkeypatch,fail)
    with pytest.raises(v.VendorError,match='^vendor_unavailable$'):v.check_account('zendesk','acme','test-token')


def test_shopify_money_preserves_currency_precision(monkeypatch):
    money={'amount':'123.456','currencyCode':'KWD'}
    order={'id':'gid://shopify/Order/42','name':'#42','displayFinancialStatus':'PAID','displayFulfillmentStatus':'UNFULFILLED','totalPriceSet':{'shopMoney':money},'customer':None,'transactions':[]}
    transport(monkeypatch,lambda req:httpx.Response(200,json={'data':{'order':order}},headers={'X-Shopify-API-Version':v.SHOPIFY_VERSION}))
    result=v.read_resource('shopify','acme','test-token','42')['resource']
    assert result['amount']=='123.456' and result['currency']=='KWD'
    money['amount']='NaN'
    with pytest.raises(v.VendorError,match='invalid_vendor_response'):v.read_resource('shopify','acme','test-token','42')
