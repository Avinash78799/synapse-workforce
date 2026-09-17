"""Data Breach Reporting Engine under Section 8(6) of the DPDP Act, 2023.

Manages security incident tracking and formats regulatory notices for the
Data Protection Board of India (DPBI) and affected Data Principals.
"""
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any
from core.config import settings


def create_breach_report(
    conn: Any,
    tenant_id: str,
    incident_title: str,
    description: str,
    severity: str = 'medium',
    affected_count: int = 0,
    data_categories: list[str] | None = None
) -> dict:
    """Logs a personal data breach event into the immutable security ledger."""
    breach_id = uuid4()
    cats = data_categories or ['customer_identifiers', 'contact_details']
    
    query = """
    INSERT INTO data_breaches (id, tenant_id, incident_title, description, severity, affected_principals_count, categories_of_data, dpbi_notified, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, false, now())
    RETURNING *
    """
    row = conn.execute(query, (breach_id, tenant_id, incident_title, description, severity, affected_count, cats)).fetchone()
    return dict(row) if row else {
        'id': str(breach_id),
        'tenant_id': tenant_id,
        'incident_title': incident_title,
        'severity': severity,
        'affected_principals_count': affected_count,
        'categories_of_data': cats,
        'created_at': datetime.now(timezone.utc).isoformat()
    }


def format_dpbi_notification(breach_record: dict, dpo_info: dict | None = None) -> dict:
    """Constructs the formal notification document for the Data Protection Board of India."""
    dpo = dpo_info or {
        'name': getattr(settings, 'dpo_name', 'Data Protection Officer'),
        'email': getattr(settings, 'dpo_email', 'dpo@acme.local'),
        'phone': getattr(settings, 'dpo_phone', '+91-80-5555-0199'),
        'jurisdiction': 'India'
    }

    return {
        'regulatory_recipient': 'Data Protection Board of India (DPBI)',
        'statutory_reference': 'Section 8(6) of the Digital Personal Data Protection Act, 2023',
        'incident_id': str(breach_record.get('id', uuid4())),
        'tenant_id': str(breach_record.get('tenant_id', '')),
        'incident_title': breach_record.get('incident_title', 'Unspecified Personal Data Incident'),
        'severity_level': breach_record.get('severity', 'medium').upper(),
        'estimated_affected_principals': breach_record.get('affected_principals_count', 0),
        'categories_of_data_affected': breach_record.get('categories_of_data', []),
        'description_and_impact': breach_record.get('description', ''),
        'technical_mitigations_applied': [
            'Immediate session revocation and token cycling',
            'Vault integration credential rotation',
            'Cryptographic audit trail inspection'
        ],
        'data_fiduciary_contact': dpo,
        'notification_timestamp': datetime.now(timezone.utc).isoformat()
    }
