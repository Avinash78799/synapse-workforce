import psycopg
from tests.integration.test_client_foundation import business


def test_client_owner_cannot_read_or_write_internal_notes(client,database):
    tenant=str(database['tenant'])
    assert client.get('/api/internal/onboarding/access').json()=={'allowed':False}
    assert client.get('/api/internal/onboarding/workspaces').status_code==403
    assert client.get('/api/internal/onboarding/'+tenant).status_code==403
    assert client.put('/api/internal/onboarding/'+tenant,json={'version':0,'items':{},'notes':'private'}).status_code==403


def test_founder_notes_conflict_and_audit_redaction(client,database):
    with psycopg.connect(database['url']) as admin:
        admin.execute('INSERT INTO auth_private.platform_founders(user_id) VALUES(%s)',(database['owner'],))
    tenant=str(database['tenant']);url='/api/internal/onboarding/'+tenant
    assert client.get(url).json()['checklist']['version']==0
    body={'version':0,'items':{'discovery':{'status':'done','date':'2026-09-26','note':'private-interview-note'}},'notes':'private-founder-note'}
    assert client.put(url,json=body).status_code==200
    assert client.put(url,json=body).status_code==409
    assert client.get(url).json()['checklist']['notes']=='private-founder-note'
    audit=client.get('/api/audit').text
    assert 'private-founder-note' not in audit and 'private-interview-note' not in audit
    other,_=business(email='onboarding-other@example.test')
    assert other.get(url).status_code==403


def test_trust_data_tenant_isolated(client,database):
    from core.runtime.audit import audit
    with database['scoped'](database['tenant'],database['support']) as conn:
        audit(conn,database['tenant'],database['support'],'vendor.registered','pending_credentials',details={'private':'do-not-expose'})
    value=client.get('/api/trust')
    assert value.status_code==200 and 'do-not-expose' not in value.text
    other,_=business(email='trust-other@example.test')
    assert other.get('/api/trust').json()['actions']==[]
