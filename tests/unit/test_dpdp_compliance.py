"""Unit tests for DPDP Act (2023) Compliance:
- Indian PII detection & redaction (Aadhaar, PAN, Mobile, Email, Card)
- Consent lifecycle (grant, verify, withdraw)
- Policy engine consent enforcement
- Data Principal Rights (DSAR summary, Right to Erasure / Right to be Forgotten)
- DPBI breach regulatory notification formatting
"""
import pytest
from uuid import uuid4
from types import SimpleNamespace
from core.security.pii import redact_pii, detect_pii, anonymize_identifier
from core.privacy.consent import record_consent, verify_consent, withdraw_consent, list_consents
from core.privacy.rights import export_data_principal_summary, execute_erasure
from core.privacy.breach import create_breach_report, format_dpbi_notification
from core.policies.engine import PolicyRules, evaluate


# ==============================================================================
# 1. PII Redaction & Detection Tests
# ==============================================================================

def test_indian_aadhaar_redaction():
    text = "Customer UID is 9876 5432 1098 for verification."
    redacted = redact_pii(text)
    assert "9876 5432 1098" not in redacted
    assert "[AADHAAR_REDACTED]" in redacted


def test_indian_pan_card_redaction():
    text = "Tax PAN number is ABCDE1234F for invoice ORD-1002."
    redacted = redact_pii(text)
    assert "ABCDE1234F" not in redacted
    assert "[PAN_REDACTED]" in redacted


def test_indian_mobile_number_redaction():
    samples = [
        "Call me at +91 9876543210 regarding the order",
        "Phone: 09876543210 please update ticket",
        "Contact 9876543210 urgently"
    ]
    for sample in samples:
        redacted = redact_pii(sample)
        assert "9876543210" not in redacted
        assert "[PHONE_REDACTED]" in redacted


def test_email_and_payment_card_redaction():
    text = "Customer email is rahul.sharma@example.in and card is 4111-2222-3333-4444."
    redacted = redact_pii(text)
    assert "rahul.sharma@example.in" not in redacted
    assert "4111-2222-3333-4444" not in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert "[PAYMENT_CARD_REDACTED]" in redacted


def test_detect_pii_counts():
    text = "Aadhaar: 2345 6789 0123, PAN: BZRPK9988Q, Phone: +91 9811223344, Email: user@acme.in"
    counts = detect_pii(text)
    assert counts.get('aadhaar') == 1
    assert counts.get('pan') == 1
    assert counts.get('phone') == 1
    assert counts.get('email') == 1


def test_anonymize_identifier_deterministic():
    id1 = anonymize_identifier('CUS-001')
    id2 = anonymize_identifier('CUS-001')
    id3 = anonymize_identifier('CUS-002')
    assert id1 == id2
    assert id1 != id3
    assert id1.startswith('ANON_')


# ==============================================================================
# 2. Consent Lifecycle & Policy Enforcement Tests
# ==============================================================================

class MockDb:
    def __init__(self):
        self.consents = {}
        self.records = {}
        self.memory = []
        self.requests = []
        self.breaches = []

    def execute(self, query, params=None):
        q = query.strip().upper()
        p = params or ()

        if "INSERT INTO DATA_CONSENTS" in q:
            # (id, tenant_id, data_principal_id, purpose, notice_version, metadata)
            cid, tid, pid, purp, not_ver, meta = p[0], p[1], p[2], p[3], p[4], p[5]
            self.consents[(tid, pid, purp)] = {'id': cid, 'tenant_id': tid, 'data_principal_id': pid,
                                               'purpose': purp, 'status': 'granted', 'notice_version': not_ver}
            return SimpleNamespace(fetchone=lambda: self.consents[(tid, pid, purp)])

        if "SELECT STATUS FROM DATA_CONSENTS" in q:
            tid, pid, purp = p[0], p[1], p[2]
            row = self.consents.get((tid, pid, purp))
            return SimpleNamespace(fetchone=lambda: row)

        if "UPDATE DATA_CONSENTS" in q and "WITHDRAWN" in q:
            tid, pid = p[0], p[1]
            purp = p[2] if len(p) > 2 else None
            count = 0
            for k, v in self.consents.items():
                if k[0] == tid and k[1] == pid and (purp is None or k[2] == purp) and v['status'] == 'granted':
                    v['status'] = 'withdrawn'
                    count += 1
            return SimpleNamespace(rowcount=count)

        if "SELECT ID, TENANT_ID, DATA_PRINCIPAL_ID, PURPOSE" in q:
            tid, pid = p[0], p[1]
            rows = [v for k, v in self.consents.items() if k[0] == tid and k[1] == pid]
            return SimpleNamespace(fetchall=lambda: rows)

        if "SELECT KIND, ID, DATA FROM SIM_RECORDS" in q:
            tid, pid = p[0], p[1]
            rows = [v for k, v in self.records.items() if k[0] == tid and (v['id'] == pid or v['data'].get('customer_id') == pid)]
            return SimpleNamespace(fetchall=lambda: rows)

        if "UPDATE SIM_RECORDS SET DATA=" in q:
            # (data, tenant_id, kind, id)
            data, tid, kind, rid = p[0], p[1], p[2], p[3]
            actual_data = data.obj if hasattr(data, 'obj') else data
            if (tid, rid) in self.records:
                self.records[(tid, rid)]['data'] = actual_data
            return SimpleNamespace(rowcount=1)

        if "DELETE FROM MEMORY_ITEMS" in q:
            tid, pid = p[0], p[1]
            orig = len(self.memory)
            self.memory = [m for m in self.memory if m.get('customer_id') != pid]
            return SimpleNamespace(rowcount=orig - len(self.memory))

        if "INSERT INTO PRIVACY_REQUESTS" in q:
            self.requests.append(p)
            return SimpleNamespace(rowcount=1)

        if "INSERT INTO DATA_BREACHES" in q:
            row = {'id': p[0], 'tenant_id': p[1], 'incident_title': p[2], 'description': p[3], 'severity': p[4],
                   'affected_principals_count': p[5], 'categories_of_data': p[6]}
            self.breaches.append(row)
            return SimpleNamespace(fetchone=lambda: row)

        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)


def test_consent_lifecycle_flow():
    db = MockDb()
    tenant = str(uuid4())
    customer = 'CUS-001'
    purpose = 'support.order_resolution'

    # 1. Unregistered consent should be false
    assert verify_consent(db, tenant, customer, purpose) is False

    # 2. Record affirmative consent
    rec = record_consent(db, tenant, customer, purpose)
    assert rec['status'] == 'granted'
    assert verify_consent(db, tenant, customer, purpose) is True

    # 3. Withdraw consent per Section 6(4)
    withdrawn = withdraw_consent(db, tenant, customer, purpose)
    assert withdrawn >= 1
    assert verify_consent(db, tenant, customer, purpose) is False


def test_policy_engine_blocks_without_dpdp_consent():
    rules = PolicyRules()
    args = {'amount_minor': 240000, 'currency': 'INR', 'duplicate_verified': True, 'claim_count': 1}

    # Allowed when consent is verified
    decision_with_consent = evaluate('payment.refund', args, rules, allowed=True, skill_enabled=True, autonomy=4, mutates=True, consent_granted=True)
    assert decision_with_consent.outcome == 'allow'

    # Denied when consent is missing or withdrawn
    decision_no_consent = evaluate('payment.refund', args, rules, allowed=True, skill_enabled=True, autonomy=4, mutates=True, consent_granted=False)
    assert decision_no_consent.outcome == 'deny'
    assert 'DPDP Act' in decision_no_consent.reason


# ==============================================================================
# 3. Data Principal Rights (DSAR & Erasure) Tests
# ==============================================================================

def test_dsar_summary_export():
    db = MockDb()
    tenant = str(uuid4())
    customer = 'CUS-101'
    record_consent(db, tenant, customer, 'support.order_resolution')

    summary = export_data_principal_summary(db, tenant, customer)
    assert summary['data_principal_id'] == customer
    assert summary['sub_processors'] == []
    assert 'not yet implemented' in summary['processor_disclosure_status']
    assert len(db.requests) == 1




# ==============================================================================
# 4. DPBI Breach Notification Format Test
# ==============================================================================

def test_dpbi_breach_notification_structure():
    db = MockDb()
    tenant = str(uuid4())
    report = create_breach_report(
        db, tenant,
        incident_title="Suspicious API egress anomaly",
        description="Unusual spike in external queries detected; intercepted by rate limiter",
        severity="medium",
        affected_count=12,
        data_categories=["order_identifiers", "masked_phones"]
    )
    notification = format_dpbi_notification(report)
    assert notification['regulatory_recipient'] == 'Data Protection Board of India (DPBI)'
    assert 'Section 8(6)' in notification['statutory_reference']
    assert notification['estimated_affected_principals'] == 12
    assert 'data_fiduciary_contact' in notification
    assert notification['data_fiduciary_contact']['email'] == 'dpo@acme.local'
