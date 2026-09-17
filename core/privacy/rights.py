"""Data Principal Rights Execution Engine under Sections 11–14 of DPDP Act, 2023.

Implements:
1. Right to Access Information (Section 11): Data Subject Access Request (DSAR) summary.
2. Right to Correction and Erasure (Section 12): Right to be Forgotten and data rectification.
3. Statutory SLA Tracking & Compliance Receipts.
"""
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from typing import Any
from psycopg.types.json import Jsonb
from core.security.pii import anonymize_identifier


def export_data_principal_summary(conn: Any, tenant_id: str, principal_id: str) -> dict:
    """Compiles an itemized summary of personal data processed, per Section 11 of the DPDP Act."""
    request_id = uuid4()
    
    # 1. Fetch Consents
    consents = conn.execute(
        "SELECT purpose, status, notice_version, granted_at, withdrawn_at FROM data_consents WHERE tenant_id=%s AND data_principal_id=%s",
        (tenant_id, principal_id)
    ).fetchall()

    # 2. Fetch Customer / Order records
    records = conn.execute(
        "SELECT kind, id, data FROM sim_records WHERE tenant_id=%s AND (id=%s OR data->>'customer_id'=%s)",
        (tenant_id, principal_id, principal_id)
    ).fetchall()

    # 3. Third-Party Data Processors & Cross-Border Disclosure (Mandatory under Section 11(1)(b))
    processors = [
        {
            'processor_name': 'Groq Inc. (USA)',
            'role': 'AI Inference Sub-processor (Low-latency plan extraction)',
            'safeguard': 'PII Redaction pre-flight; no persistent customer training',
            'country': 'United States'
        },
        {
            'processor_name': 'Google Cloud / Google AI (USA)',
            'role': 'AI Inference Sub-processor (Deep reasoning failover)',
            'safeguard': 'Enterprise API tier; prompt anonymization',
            'country': 'United States'
        },
        {
            'processor_name': 'OpenRouter Inc. (USA)',
            'role': 'AI Inference Sub-processor (Tertiary failover pool)',
            'safeguard': 'Anonymized context; zero data retention headers',
            'country': 'United States'
        }
    ]

    summary = {
        'data_principal_id': principal_id,
        'tenant_id': tenant_id,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'consents': [dict(c) for c in consents],
        'sim_records_count': len(records),
        'records_summary': [
            {'kind': r['kind'], 'id': r['id']} for r in records
        ],
        'sub_processors': processors,
        'statutory_notice': 'Generated pursuant to Section 11 of the Digital Personal Data Protection Act, 2023.'
    }

    # Record completed privacy request
    conn.execute(
        """
        INSERT INTO privacy_requests (id, tenant_id, data_principal_id, request_type, status, sla_due_at, details, result_summary, created_at, completed_at)
        VALUES (%s, %s, %s, 'access_summary', 'completed', now() + interval '30 days', %s, 'DSAR export successfully compiled', now(), now())
        """,
        (request_id, tenant_id, principal_id, Jsonb(summary))
    )

    return summary


def execute_erasure(conn: Any, tenant_id: str, principal_id: str) -> dict:
    """Executes the Right to Erasure / Right to be Forgotten under Section 12(3) of DPDP Act.

    Anonymizes customer identifiers and personal details while preserving necessary financial/tax transaction ledger hashes.
    """
    request_id = uuid4()
    anon_tag = anonymize_identifier(principal_id)

    # 1. Withdraw all consents
    conn.execute(
        "UPDATE data_consents SET status='withdrawn', withdrawn_at=now() WHERE tenant_id=%s AND data_principal_id=%s AND status='granted'",
        (tenant_id, principal_id)
    )

    # 2. Anonymize customer data in simulation records
    records_updated = 0
    records = conn.execute(
        "SELECT kind, id, data FROM sim_records WHERE tenant_id=%s AND (id=%s OR data->>'customer_id'=%s)",
        (tenant_id, principal_id, principal_id)
    ).fetchall()

    for r in records:
        data = dict(r['data'])
        if 'name' in data:
            data['name'] = f'ANONYMIZED_DPDP_{anon_tag}'
        if 'email' in data:
            data['email'] = f'{anon_tag}@erased.privacy.local'
        if 'phone' in data:
            data['phone'] = '[ERASED_PER_DPDP]'
        if 'address' in data:
            data['address'] = '[ERASED]'
        if 'customer_id' in data:
            data['customer_id'] = anon_tag

        conn.execute(
            "UPDATE sim_records SET data=%s WHERE tenant_id=%s AND kind=%s AND id=%s",
            (Jsonb(data), tenant_id, r['kind'], r['id'])
        )
        records_updated += 1

    # 3. Clean up customer memory items
    memory_scrubbed = 0
    res = conn.execute(
        "DELETE FROM memory_items WHERE tenant_id=%s AND (details->>'customer_id'=%s OR details->>'entity_id'=%s)",
        (tenant_id, principal_id, principal_id)
    )
    if hasattr(res, 'rowcount'):
        memory_scrubbed = res.rowcount

    result_summary = f'Erased personal data for {principal_id}. Anonymized {records_updated} records and purged {memory_scrubbed} agent memory items.'

    # 4. Record completed privacy request
    conn.execute(
        """
        INSERT INTO privacy_requests (id, tenant_id, data_principal_id, request_type, status, sla_due_at, details, result_summary, created_at, completed_at)
        VALUES (%s, %s, %s, 'erasure', 'completed', now() + interval '30 days', %s, %s, now(), now())
        """,
        (request_id, tenant_id, principal_id, Jsonb({'records_updated': records_updated, 'memory_purged': memory_scrubbed}), result_summary)
    )

    return {
        'request_id': str(request_id),
        'data_principal_id': principal_id,
        'anonymized_reference': anon_tag,
        'records_anonymized': records_updated,
        'memory_purged': memory_scrubbed,
        'status': 'completed',
        'statutory_notice': 'Personal data erased pursuant to Section 12(3) of the Digital Personal Data Protection Act, 2023.'
    }
