"""Consent Lifecycle Engine under Section 5 & 6 of the DPDP Act, 2023.

Manages itemized, purpose-specific, affirmative consent for Data Principals.
Ensures easy withdrawal of consent with mandatory processing cessation.
"""
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any
import json
from psycopg.types.json import Jsonb


def record_consent(conn: Any, tenant_id: str, principal_id: str, purpose: str, notice_version: str = '1.0', metadata: dict | None = None) -> dict:
    """Records an affirmative consent for a specified processing purpose."""
    consent_lock(conn, tenant_id, exclusive=True)
    consent_id = uuid4()
    meta = metadata or {}
    query = """
    INSERT INTO data_consents (id, tenant_id, data_principal_id, purpose, status, notice_version, granted_at, withdrawn_at, metadata)
    VALUES (%s, %s, %s, %s, 'granted', %s, now(), NULL, %s)
    ON CONFLICT (tenant_id, data_principal_id, purpose) DO UPDATE
      SET status = 'granted',
          notice_version = EXCLUDED.notice_version,
          granted_at = now(),
          withdrawn_at = NULL,
          metadata = EXCLUDED.metadata
    RETURNING *
    """
    row = conn.execute(query, (consent_id, tenant_id, principal_id, purpose, notice_version, Jsonb(meta))).fetchone()
    return dict(row) if row else {'id': str(consent_id), 'status': 'granted', 'purpose': purpose}


def verify_consent(conn: Any, tenant_id: str, principal_id: str, purpose: str) -> bool:
    """Verifies whether the Data Principal has an active, granted consent for the specified purpose."""
    if not principal_id:
        return False
    query = """
    SELECT status FROM data_consents
    WHERE tenant_id = %s AND data_principal_id = %s AND purpose = %s
    """
    row = conn.execute(query, (tenant_id, principal_id, purpose)).fetchone()
    if not row:
        return False
    status = row['status'] if isinstance(row, dict) else row[0]
    return status == 'granted'


def withdraw_consent(conn: Any, tenant_id: str, principal_id: str, purpose: str | None = None) -> int:
    """Withdraws consent per Section 6(4). If purpose is omitted, all consents are withdrawn."""
    consent_lock(conn, tenant_id, exclusive=True)
    if purpose:
        query = """
        UPDATE data_consents
        SET status = 'withdrawn', withdrawn_at = now()
        WHERE tenant_id = %s AND data_principal_id = %s AND purpose = %s AND status = 'granted'
        """
        result = conn.execute(query, (tenant_id, principal_id, purpose))
    else:
        query = """
        UPDATE data_consents
        SET status = 'withdrawn', withdrawn_at = now()
        WHERE tenant_id = %s AND data_principal_id = %s AND status = 'granted'
        """
        result = conn.execute(query, (tenant_id, principal_id))

    return getattr(result, 'rowcount', 1)


def list_consents(conn: Any, tenant_id: str, principal_id: str) -> list[dict]:
    """Lists all consents and their lifecycle status for a Data Principal."""
    query = """
    SELECT id, tenant_id, data_principal_id, purpose, status, notice_version, granted_at, withdrawn_at, metadata
    FROM data_consents
    WHERE tenant_id = %s AND data_principal_id = %s
    ORDER BY granted_at DESC
    """
    rows = conn.execute(query, (tenant_id, principal_id)).fetchall()
    return [dict(r) for r in rows]


PURPOSES = {'support': 'support.order_resolution', 'operations': 'operations.fulfillment'}


def consent_lock(conn, tenant_id, *, exclusive=False):
    # A withdrawal commits only after any already-authorized in-flight call ends.
    # Shared locks permit concurrent agent calls; withdrawal/erasure take exclusive.
    function = 'pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
    conn.execute('SELECT ' + function + '(hashtextextended(%s,904))', (str(tenant_id),))


def workflow_consent(conn, tenant_id, kind, inputs):
    from core.config import settings
    consent_lock(conn, tenant_id)
    purpose = PURPOSES.get(kind)
    if not purpose:
        return False
    if kind == 'support':
        principal = inputs.get('customer_id')
        if not principal:
            return False
        return verify_consent(conn, tenant_id, principal, purpose)
    # The current Operations workflow inspects the entire scoped simulator.
    # A remote batch must provide a trusted principal manifest before it can run.
    if settings.commerce_connector != 'simulation' or settings.external_connector_base_url:
        return False
    customers = conn.execute("SELECT id FROM sim_records WHERE tenant_id=%s AND kind='customer'", (tenant_id,)).fetchall()
    return bool(customers) and all(verify_consent(conn, tenant_id, c['id'], purpose) for c in customers)
