"""Bounded, read-only Shopify and Zendesk clients; no runtime mutation surface."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import httpx

SHOPIFY_VERSION = '2026-07'
MAX_RESPONSE = 1_048_576


class VendorError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def hostname(vendor, account):
    if vendor not in ('shopify','zendesk') or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', account):
        raise ValueError('Use a single lowercase vendor account subdomain, not a URL')
    return account + ('.myshopify.com' if vendor == 'shopify' else '.zendesk.com')


def resource_number(value):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('Use the positive numeric vendor resource ID')
    return value


def _request(vendor, account, token, path, query=None):
    host = hostname(vendor, account)
    if not isinstance(token, str) or not token or len(token)>8192 or any(c.isspace() for c in token):
        raise VendorError('credential_unavailable')
    headers = {'Accept':'application/json'}
    if vendor == 'shopify':
        headers['X-Shopify-Access-Token'] = token
    else:
        headers['Authorization'] = 'Bearer '+token
    try:
        with httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False) as client:
            with client.stream('POST' if query else 'GET', 'https://'+host+path, headers=headers,
                               **({'json':query} if query else {})) as response:
                if response.status_code in (401,403): raise VendorError('credential_or_scope_denied')
                if response.status_code == 404: raise VendorError('resource_not_found')
                if response.status_code == 429: raise VendorError('vendor_rate_limited')
                if not 200 <= response.status_code < 300: raise VendorError('vendor_unavailable')
                if vendor == 'shopify' and response.headers.get('x-shopify-api-version') != SHOPIFY_VERSION:
                    raise VendorError('api_version_mismatch')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw)+len(chunk)>MAX_RESPONSE: raise VendorError('response_too_large')
                    raw.extend(chunk)
                data = json.loads(raw)
                if not isinstance(data,dict): raise VendorError('invalid_vendor_response')
                return data
    except VendorError:
        raise
    except (httpx.HTTPError,ValueError,TypeError):
        raise VendorError('vendor_unavailable') from None


def _graphql(account, token, query, variables=None):
    result = _request('shopify',account,token,f'/admin/api/{SHOPIFY_VERSION}/graphql.json',
                      {'query':query,'variables':variables or {}})
    if result.get('errors'):
        raise VendorError('graphql_error')
    if not isinstance(result.get('data'),dict): raise VendorError('invalid_vendor_response')
    return result['data']


def check_account(vendor, account, token):
    if vendor == 'shopify':
        data = _graphql(account,token,'query WorkforceAccount { shop { id myshopifyDomain } currentAppInstallation { accessScopes { handle } } }')
        try:
            if data['shop']['myshopifyDomain'] != hostname(vendor,account):
                raise VendorError('vendor_account_mismatch')
            scopes = {s['handle'] for s in data['currentAppInstallation']['accessScopes']}
            if not scopes.intersection({'read_orders','write_orders'}):
                raise VendorError('orders_scope_missing')
        except (KeyError,TypeError):
            raise VendorError('invalid_vendor_response') from None
        return {'account_read':True,'orders_scope':True,'api_version':SHOPIFY_VERSION,'write_actions_enabled':False}
    data = _request(vendor,account,token,'/api/v2/users/me.json')
    user = data.get('user')
    if not isinstance(user,dict) or type(user.get('id')) is not int or user.get('role') not in ('agent','admin'):
        raise VendorError('agent_account_required')
    return {'account_read':True,'agent_role':user['role'],'write_actions_enabled':False}


def read_resource(vendor, account, token, resource_id):
    number = resource_number(resource_id)
    if vendor == 'shopify':
        expected = 'gid://shopify/Order/'+number
        data = _graphql(account,token,'''query WorkforceOrder($id: ID!) {
          order(id: $id) { id name displayFinancialStatus displayFulfillmentStatus
            totalPriceSet { shopMoney { amount currencyCode } }
            customer { id }
            transactions { id kind status test amountSet { shopMoney { amount currencyCode } } }
          }
        }''',{'id':expected})
        order=data.get('order')
        if order is None: raise VendorError('resource_not_found')
        if not isinstance(order,dict) or order.get('id')!=expected:
            raise VendorError('resource_binding_mismatch')
        try:
            money=order['totalPriceSet']['shopMoney']
            amount=str(money['amount']);currency=money['currencyCode']
            if not Decimal(amount).is_finite() or Decimal(amount)<0 or not re.fullmatch('[A-Z]{3}',currency):
                raise ValueError()
            customer=order.get('customer')
            transactions=order['transactions']
            if not isinstance(transactions,list) or len(transactions)>100:
                raise ValueError()
            # Keep the vendor's decimal representation; do not assume every currency has two decimals.
            safe_transactions=[]
            for tx in transactions:
                tx_money=tx['amountSet']['shopMoney']
                tx_amount=str(tx_money['amount']);tx_currency=tx_money['currencyCode']
                if not Decimal(tx_amount).is_finite() or Decimal(tx_amount)<0 or not re.fullmatch('[A-Z]{3}',tx_currency):raise ValueError()
                safe_transactions.append({'id':tx['id'],'kind':tx['kind'],'status':tx['status'],
                                          'test':tx['test'],'amount':tx_amount,'currency':tx_currency})
            result={'id':expected,'name':str(order['name'])[:120],'financial_status':order['displayFinancialStatus'],
                    'fulfillment_status':order['displayFulfillmentStatus'],'amount':amount,'currency':currency,
                    'customer_id':customer.get('id') if isinstance(customer,dict) else None,'transactions':safe_transactions}
        except (KeyError,TypeError,ValueError,InvalidOperation):
            raise VendorError('invalid_vendor_response') from None
    else:
        data = _request(vendor,account,token,'/api/v2/tickets/'+number+'.json')
        ticket=data.get('ticket')
        if not isinstance(ticket,dict) or type(ticket.get('id')) is not int or ticket['id']!=int(number):
            raise VendorError('resource_binding_mismatch')
        if ticket.get('status') not in {'new','open','pending','hold','solved','closed'}:
            raise VendorError('invalid_vendor_response')
        # No contact details, message body or attachments are copied into the platform.
        result={k:ticket.get(k) for k in ('id','status','requester_id','organization_id','updated_at')}
    return {'vendor':vendor,'account':hostname(vendor,account),'mode':'vendor_read_only',
            'observed_at':datetime.now(timezone.utc).isoformat(),'resource':result,
            'workflow_execution_enabled':False}
