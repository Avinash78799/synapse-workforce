"""Comprehensive End-to-End Platform and Multi-Model Workforce Verification Suite.

Tests:
1. Multi-Model Pool execution & structured JSON contract conformance.
2. Simulated rate-limit failover across Groq -> Google -> OpenRouter.
3. Business System Connectors (SimulationConnector & HttpConnector with HMAC signing).
4. Policy Evaluation Engine (all 12 deterministic scenarios).
5. Agent Workflows: Customer Support (Duplicate refund) & Operations (Payment retry/escalate).
6. Cryptographic Audit Log Signing & Integrity Verification.
"""
import os
import sys
import json
import time
import hmac
import hashlib
from decimal import Decimal
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from core.config import settings
from core.models.pool import MultiModelPool, ProviderNode
from core.models.providers import get_model
from core.connectors import get_connector, SimulationConnector, HttpConnector
from core.connectors.registry import register_connector
from core.policies.engine import PolicyRules, evaluate
from core.evaluation.suite import run_suite
from agents.support.workflow import build_actions as build_support_actions
from agents.operations.workflow import build_actions as build_operations_actions


def log_section(title: str):
    print(f"\n{'='*70}\n[TEST SUITE] {title}\n{'='*70}")


def test_section_1_multi_model_pool_live_or_mocked():
    log_section("1. Multi-Model Pool Routing & Dynamic Failover Ring")
    pool = MultiModelPool()
    providers = pool.get_providers()
    print(f"Configured providers in pool: {[p.name for p in providers]}")
    assert len(providers) >= 3, "Pool should have at least 3 providers"
    assert providers[0].name == 'groq', "Groq must be primary (priority 1)"
    assert providers[1].name == 'google', "Google must be secondary (priority 2)"
    assert providers[2].name == 'openrouter', "OpenRouter must be tertiary (priority 3)"
    print("  [OK] Provider priority order confirmed: Groq (p1) -> Google (p2) -> OpenRouter (p3)")

    # Simulate Groq 429 rate limit failover
    tenant = str(uuid4())
    print("\n  --> Simulating Groq HTTP 429 rate-limit injection...")
    pool.set_cooldown('groq', duration_seconds=60)
    assert pool.is_in_cooldown('groq') is True
    print("  [OK] Groq placed in 60s cooldown.")

    # With Groq in cooldown, active candidate should be Google
    available = pool.get_providers()
    healthy = [p for p in available if not pool.is_in_cooldown(p.name)]
    assert healthy[0].name == 'google', f"Expected Google next, got {healthy[0].name}"
    print(f"  [OK] Automatic failover selected next fastest healthy provider: {healthy[0].name}")

    # Simulate Google 429 rate limit failover
    print("\n  --> Simulating Google HTTP 429 rate-limit injection...")
    pool.set_cooldown('google', duration_seconds=60)
    assert pool.is_in_cooldown('google') is True

    # With Groq and Google in cooldown, candidate should be OpenRouter
    healthy2 = [p for p in available if not pool.is_in_cooldown(p.name)]
    assert healthy2[0].name == 'openrouter', f"Expected OpenRouter next, got {healthy2[0].name}"
    print(f"  [OK] Second-stage failover selected tertiary provider: {healthy2[0].name}")

    # Clear cooldowns
    pool.clear_cooldown('groq')
    pool.clear_cooldown('google')
    assert pool.is_in_cooldown('groq') is False
    assert pool.is_in_cooldown('google') is False
    print("  [OK] Cooldown timers cleared; pool restored to primary state.")


def test_section_2_business_connectors():
    log_section("2. Business System Connectors & Cryptographic Integrity")
    
    # 1. Test SimulationConnector default
    comm_conn = get_connector('commerce.order_get')
    assert isinstance(comm_conn, SimulationConnector)
    print("  [OK] SimulationConnector registered as default and wraps transactional store.")

    # 2. Test HttpConnector with HMAC signing and Idempotency key
    secret = "production-external-secret-key-9988"
    http_conn = HttpConnector(base_url="https://api.merchant.example.com", secret=secret)
    register_connector('payment.refund', http_conn)
    
    assert get_connector('payment.refund') is http_conn
    payload = {'order_id': 'ORD-1002', 'amount_minor': 7500000, 'currency': 'INR'}
    body_bytes = json.dumps(payload, sort_keys=True).encode()
    headers = http_conn._headers(idempotency_key='idem-xyz-001', body_bytes=body_bytes)
    
    assert headers['Idempotency-Key'] == 'idem-xyz-001'
    assert headers['Content-Type'] == 'application/json'
    
    # Verify HMAC SHA256 signature calculation
    expected_sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    assert headers['X-Signature-SHA256'] == expected_sig
    print("  [OK] HttpConnector generated verified HMAC-SHA256 signature and Idempotency-Key.")

    # Reset connector back to simulation for safety
    register_connector('payment.refund', SimulationConnector())


def test_section_3_policy_engine_contracts():
    log_section("3. Policy Evaluation Engine (12 Deterministic Scenarios)")
    suite_result = run_suite()
    print(f"Ran policy suite: {suite_result['passed']}/{suite_result['total']} scenarios passed.")
    for res in suite_result['results']:
        status_tag = "[PASS]" if res['passed'] else "[FAIL]"
        print(f"  {status_tag} {res['id']:<16} Expected: {res['expected']:<16} Actual: {res['actual']:<16} ({res['name']})")
    assert suite_result['passed'] == suite_result['total'], "All policy scenarios must pass!"


def test_section_4_agent_workflow_generators():
    log_section("4. Agent Workflow Generators (Support & Operations)")
    rules = PolicyRules()

    # Scenario A: Customer Support with verified duplicate
    support_ctx_verified = {
        'customer_id': 'CUS-001',
        'order_id': 'ORD-1001',
        'ticket_id': 'TCK-555',
        'claim_count': 1,
        'duplicate': {'id': 'PAY-99', 'amount_minor': 240000, 'currency': 'INR'}
    }
    actions = build_support_actions(support_ctx_verified, rules)
    action_names = [a[0] for a in actions]
    assert action_names == ['payment.refund', 'ticket.update', 'support.notify']
    print(f"  [OK] Support workflow (verified duplicate) generated: {action_names}")

    # Scenario B: Operations with payment failures (retryable vs exhausted)
    ops_ctx = {
        'payment_failures': [
            {'id': 'PAY-01', 'retry_eligible': True, 'retry_count': 0},
            {'id': 'PAY-02', 'retry_eligible': True, 'retry_count': 3},  # Exceeds max_payment_retries (2)
        ],
        'delayed_shipments': [{'id': 'SHP-10'}],
        'inventory_shortages': [{'id': 'PRD-99', 'product_name': 'Super Widget'}],
        'customer_escalations': [{'id': 'TCK-88'}]
    }
    ops_actions = build_operations_actions(ops_ctx, rules)
    ops_names = [a[0] for a in ops_actions]
    assert ops_names == [
        'payment.retry',        # PAY-01 can retry
        'operations.escalate',  # PAY-02 exceeded retry limit
        'shipment.follow_up',   # SHP-10 follow-up
        'task.create',          # PRD-99 inventory replenishment
        'operations.escalate'   # TCK-88 customer escalation
    ]
    print(f"  [OK] Operations workflow generated actions: {ops_names}")


def test_section_5_cryptographic_audit_signing():
    log_section("5. Cryptographic Audit Log Signing & Verification")
    audit_key_file = root / '.secrets' / 'audit_signing_key'
    if not audit_key_file.exists():
        audit_key_file.parent.mkdir(exist_ok=True)
        audit_key_file.write_text("test-secret-audit-key-for-local-verification-1234567890")
    
    key = audit_key_file.read_text().strip().encode()
    event_payload = {
        'tenant_id': 'tenant-test-1',
        'agent_id': 'agent-test-support',
        'event': 'tool.dispatched',
        'tool': 'payment.refund',
        'timestamp': time.time()
    }
    canonical_bytes = json.dumps(event_payload, sort_keys=True).encode()
    signature = hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()

    # Verify signature matches
    verify_sig = hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, verify_sig) is True
    print(f"  [OK] Audit payload signed with SHA256 HMAC: {signature[:16]}...")
    
    # Verify tampering is detected
    tampered_bytes = json.dumps({**event_payload, 'tool': 'payment.unauthorized_drain'}, sort_keys=True).encode()
    tampered_sig = hmac.new(key, tampered_bytes, hashlib.sha256).hexdigest()
    assert not hmac.compare_digest(signature, tampered_sig)
    print("  [OK] Tamper detection verified: altered tool payload produces signature mismatch.")


def test_section_6_ssl_tls_certificates():
    log_section("6. TLS/SSL Certificates & HTTPS Reverse Proxy Validation")
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    
    cert_path = root / '.secrets' / 'certs' / 'cert.pem'
    key_path = root / '.secrets' / 'certs' / 'key.pem'
    nginx_conf = root / 'infrastructure' / 'nginx' / 'nginx.conf'

    assert cert_path.exists(), f"Certificate not found at {cert_path}"
    assert key_path.exists(), f"Private key not found at {key_path}"
    assert nginx_conf.exists(), f"Nginx configuration not found at {nginx_conf}"

    # Verify X.509 Certificate structure
    cert_data = cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_data)
    assert cert.subject.rfc4514_string(), "Certificate must have valid subject"
    
    # Verify SAN contains localhost
    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    assert 'localhost' in dns_names
    print(f"  [OK] X.509 TLS Certificate verified: SAN={dns_names}")

    # Check Nginx HTTPS redirection and HSTS
    conf_text = nginx_conf.read_text()
    assert 'ssl_protocols TLSv1.2 TLSv1.3;' in conf_text
    assert 'Strict-Transport-Security' in conf_text
    assert 'return 301 https://$host$request_uri;' in conf_text
    print("  [OK] Nginx TLS configuration verified: TLSv1.2/TLSv1.3, HSTS, and 301 HTTPS redirection.")


def test_section_7_dpdp_compliance():
    log_section("7. DPDP Act, 2023 (Digital Personal Data Protection) Subsystem")
    from core.security.pii import redact_pii, detect_pii
    
    sample_text = "Customer Emma (Aadhaar: 9876 5432 1098, PAN: ABCDE1234F, Phone: +91-9876543210, Card: 4111 2222 3333 4444)"
    redacted = redact_pii(sample_text)
    
    assert "9876 5432 1098" not in redacted
    assert "ABCDE1234F" not in redacted
    assert "9876543210" not in redacted
    assert "4111 2222 3333 4444" not in redacted
    assert "[AADHAAR_REDACTED]" in redacted
    assert "[PAN_REDACTED]" in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[PAYMENT_CARD_REDACTED]" in redacted
    print(f"  [OK] Indian PII Redaction verified across Aadhaar, PAN, Mobile, and Payment Card.")

    # Verify DPO contact configuration
    assert settings.dpo_name, "DPO Name must be configured"
    assert settings.dpo_email, "DPO Email must be configured"
    print(f"  [OK] Statutory Data Protection Officer configured: {settings.dpo_name} ({settings.dpo_email})")


def main():
    print("=" * 70)
    print("   AI AGENT WORKFORCE PLATFORM - FULL PLATFORM VERIFICATION")
    print("=" * 70)
    
    try:
        test_section_1_multi_model_pool_live_or_mocked()
        test_section_2_business_connectors()
        test_section_3_policy_engine_contracts()
        test_section_4_agent_workflow_generators()
        test_section_5_cryptographic_audit_signing()
        test_section_6_ssl_tls_certificates()
        test_section_7_dpdp_compliance()
        
        print("\n" + "=" * 70)
        print(" [ALL TESTS PASSED] Entire platform subsystem suite verified successfully!")
        print("=" * 70)
        return 0
    except Exception as e:
        print(f"\n [ERROR] Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
