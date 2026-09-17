"""Explicit local simulation bootstrap. Never runs automatically at API startup."""
import os
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid5, NAMESPACE_DNS
import psycopg
from psycopg.types.json import Jsonb
from core.security.passwords import hash_password
from core.policies.engine import PolicyRules
from core.tools.registry import tool_catalog
from core.memory.knowledge import ingest
from simulations.ecommerce.store import put

TENANT = UUID('11111111-1111-4111-8111-111111111111')
OTHER_TENANT = UUID('22222222-2222-4222-8222-222222222222')


def identity(name):
    return uuid5(NAMESPACE_DNS,'workforce.local.'+name)


def seed():
    password = os.environ.get('SEED_PASSWORD')
    if not password or len(password)<12:
        raise RuntimeError('Set SEED_PASSWORD to at least 12 characters')
    with psycopg.connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
        for tenant,name in [(TENANT,'Acme Commerce'),(OTHER_TENANT,'Northstar Demo')]:
            if conn.execute('SELECT 1 FROM tenants WHERE id=%s',(tenant,)).fetchone():
                print(f'{name}: already seeded; left unchanged')
                continue
            conn.execute('INSERT INTO tenants(id,name) VALUES(%s,%s)',(tenant,name))
            for role,display in [('owner','Alex Morgan'),('operator','Jordan Lee'),('finance_approver','Priya Shah'),('viewer','Sam Chen')]:
                email = f'{role}@'+('acme.local' if tenant==TENANT else 'northstar.local')
                conn.execute('INSERT INTO auth_private.users(id,tenant_id,email,name,password_hash,role) VALUES(%s,%s,%s,%s,%s,%s)',
                             (identity(email),tenant,email,display,hash_password(password),role))
            for kind in ['operations','support']:
                agent = identity(str(tenant)+kind)
                conn.execute('INSERT INTO agents(id,tenant_id,kind,name,owner_name) VALUES(%s,%s,%s,%s,%s)',
                             (agent,tenant,kind,'Operations Agent' if kind=='operations' else 'Customer Support Agent','Alex Morgan'))
                rules = PolicyRules().model_dump()
                conn.execute('INSERT INTO agent_policies(tenant_id,agent_id,version,rules,created_by) VALUES(%s,%s,1,%s,%s)',(tenant,agent,Jsonb(rules),'bootstrap'))
                conn.execute('INSERT INTO agent_versions(tenant_id,agent_id,version,definition) VALUES(%s,%s,%s,%s)',
                             (tenant,agent,'1.0.0',Jsonb({'kind':kind,'environment':'simulation','prompt_version':'1.0'})))
                skills = ({'OrderSkill','PaymentSkill','InventorySkill','ShipmentSkill','TaskSkill','ReportingSkill','EscalationSkill'} if kind=='operations'
                          else {'CustomerLookupSkill','OrderLookupSkill','TicketSkill','PaymentSkill','RefundSkill','CommunicationSkill','EscalationSkill','AbuseSignalSkill'})
                for skill in skills:
                    conn.execute('INSERT INTO agent_skills(tenant_id,agent_id,name) VALUES(%s,%s,%s)',(tenant,agent,skill))
                for tool in tool_catalog(kind):
                    conn.execute('INSERT INTO agent_tools(tenant_id,agent_id,name) VALUES(%s,%s,%s)',(tenant,agent,tool.name))
                    conn.execute('INSERT INTO agent_permissions(tenant_id,agent_id,action,allowed) VALUES(%s,%s,%s,true)',(tenant,agent,tool.name))
                conn.execute('INSERT INTO workflow_definitions(tenant_id,agent_id,name,version,definition) VALUES(%s,%s,%s,%s,%s)',
                             (tenant,agent,'daily_operations' if kind=='operations' else 'duplicate_charge','1.0',Jsonb({'verify_every_mutation':True,'steps':'inspect, plan, policy, execute, verify, report'})))
                for memory_kind,content in [('semantic',{'policy_version':1,'currency':'INR','refund_rule':'Verified duplicates only; repeated claims and high values require approval'}),
                                            ('procedural',{'workflow':'daily_operations' if kind=='operations' else 'duplicate_charge','version':'1.0','verify_every_mutation':True})]:
                    conn.execute('INSERT INTO memory_items(id,tenant_id,agent_id,kind,content) VALUES(%s,%s,%s,%s,%s)',
                                 (identity(str(agent)+memory_kind),tenant,agent,memory_kind,Jsonb(content)))
                for integration,status in [('Acme Commerce','simulation'),('Email','not_connected'),('Payments','simulation')]:
                    conn.execute('INSERT INTO integrations(id,tenant_id,agent_id,name,status) VALUES(%s,%s,%s,%s,%s)',
                                 (identity(str(agent)+integration),tenant,agent,integration,status))
                for i in range(1,13):
                    cid,oid,pid = f'CUS-{i:03}',f'ORD-{1000+i}',f'PAY-{i:03}'
                    amount = 7_500_000 if i==2 else 240_000 if i==1 else 150_000
                    history=[{'category':'duplicate_charge','created_at':(datetime.now(timezone.utc)-timedelta(days=day)).isoformat()} for day in [4,9,15]] if i==3 else []
                    put(conn,tenant,agent,'customer',cid,{'name':['Aarav Mehta','Emma Wilson','Riya Kapoor','James Carter'][((i-1)%4)],'email':f'customer{i}@example.test','refund_claim_history':history})
                    put(conn,tenant,agent,'order',oid,{'customer_id':cid,'amount_minor':amount,'currency':'INR','status':'processing'})
                    put(conn,tenant,agent,'payment',pid,{'order_id':oid,'amount_minor':amount,'currency':'INR','status':'failed' if i in [5,6,7] else 'captured',
                        'provider_token':f'sim_tok_{i}','retry_eligible':i!=7,'retry_count':0,'retry_will_succeed':i!=6})
                    if i<=3:
                        put(conn,tenant,agent,'payment',pid+'-DUP',{'order_id':oid,'amount_minor':amount,'currency':'INR','status':'captured','provider_token':f'sim_dup_{i}'})
                    put(conn,tenant,agent,'shipment',f'SHP-{i:03}',{'order_id':oid,'status':'delayed' if i in [4,8] else 'in_transit','carrier':'Acme Logistics'})
                    if i<=4:
                        put(conn,tenant,agent,'ticket',f'TKT-{i:03}',{'customer_id':cid,'order_id':oid,'subject':'Charged twice for my order','category':'duplicate_charge','status':'escalated' if i==4 else 'open','created_at':datetime.now(timezone.utc).isoformat()})
                for i,product_name in enumerate(['Studio headphones','Everyday backpack','Desk lamp','Travel bottle'],1):
                    put(conn,tenant,agent,'product',f'PROD-{i:03}',{'name':product_name})
                    put(conn,tenant,agent,'inventory',f'INV-{i:03}',{'product_id':f'PROD-{i:03}','product_name':product_name,'available':2 if i<=2 else 30,'reorder_point':10})
                put(conn,tenant,agent,'employee','EMP-001',{'name':'Operations manager','role':'operator'})
                ingest(conn,tenant,agent,'Refund policy v1' if kind=='support' else 'Operations SOP v1',
                       'Verified duplicate payments below INR 5000 may be refunded under configured policy. At or above INR 5000 an independent approver is required. Repeated claims also require finance approval. Customer claims alone are not payment evidence.' if kind=='support' else
                       'Inspect orders, failed payments, inventory and delayed shipments. Retry only eligible payments. Request shipment follow-ups, create replenishment tasks and escalate unresolved exceptions. Verify every mutation.')
                print(f'{name}: seeded isolated {kind} workspace')


if __name__=='__main__':
    seed()
