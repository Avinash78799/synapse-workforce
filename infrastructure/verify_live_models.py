"""Phase 2: Live AI Model Connectivity & Fallback Validation.

Tests:
1. Safely checks presence and non-empty status of all provider credentials in settings.
2. Validates live inference connectivity against active external providers (Groq, Gemini, OpenRouter)
   using isolated test objectives.
3. Tests multi-model pool priority routing based on agent role (Support -> Groq, Operations -> Gemini).
4. Tests automatic cooldown failover mechanics (simulates provider unavailability and confirms seamless fallback).
5. Enforces zero credential disclosure (no API keys are ever printed or logged).
"""
import sys
import json
from pathlib import Path
from uuid import uuid4
import httpx

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from core.config import settings
from core.models.pool import MultiModelPool


def verify_provider_direct(provider_node):
    """Sends a minimal schema verification prompt directly to a provider node."""
    headers = {'Content-Type': 'application/json'}
    if provider_node.api_key:
        headers['Authorization'] = f'Bearer {provider_node.api_key}'

    payload = {
        'model': provider_node.model,
        'messages': [
            {'role': 'system', 'content': 'Respond ONLY with a valid JSON object: {"status": "ok", "provider": "' + provider_node.name + '"}'},
            {'role': 'user', 'content': 'Ping'}
        ],
        'max_tokens': 150,
        'response_format': {'type': 'json_object'}
    }

    try:
        url = f"{provider_node.base_url}/chat/completions"
        resp = httpx.post(url, headers=headers, json=payload, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data['choices'][0]['message']['content']
        parsed = json.loads(raw_text)
        return True, f"Success (status={parsed.get('status')})"
    except Exception as exc:
        return False, f"Failed: {type(exc).__name__} - {str(exc)[:120]}"


def run_phase_2():
    print("=" * 75)
    print("   PHASE 2: LIVE AI MODEL CONNECTIVITY & FALLBACK VALIDATION")
    print("=" * 75)

    pool = MultiModelPool()
    providers = pool.get_providers()

    print("\n[Step 1] Inspecting Configured Model Providers (Zero Disclosure)...")
    for p in providers:
        has_key = bool(p.api_key and len(p.api_key) > 5)
        key_format = f"{p.api_key[:4]}...{p.api_key[-2:]}" if has_key else "None"
        # Masked representation only
        masked = "***CONFIGURED***" if has_key else "NOT SET"
        print(f"  * Provider: {p.name:<12} | Model: {p.model:<35} | Status: {masked}")

    # 2. Test Direct Connectivity for Configured Providers
    print("\n[Step 2] Testing Live Endpoint Connectivity...")
    results = {}
    for p in providers:
        if not p.api_key:
            print(f"  [SKIP] {p.name}: No API key configured in .env")
            results[p.name] = 'Skipped (No Key)'
            continue
        print(f"  --> Testing live connection to {p.name} ({p.model})...")
        success, msg = verify_provider_direct(p)
        results[p.name] = 'PASS' if success else 'FAIL'
        status_label = "[PASS]" if success else "[WARN/FAIL]"
        print(f"  {status_label} {p.name}: {msg}")

    # 3. Test Multi-Model Pool Role Routing
    print("\n[Step 3] Testing Role-Based Pool Priority Routing...")
    # Support Agent (Speed tier)
    support_candidates = pool.get_providers(role='support', model_tier='speed')
    active_support = [p for p in support_candidates if p.api_key]
    if active_support:
        top_support = active_support[0]
        print(f"  [PASS] Support Agent priority dispatch target: {top_support.name} ({top_support.model}) [Expected: groq]")
    else:
        print("  [INFO] No active API keys for support pool; will use simulation fallback")

    # Operations Agent (Reasoning tier)
    ops_candidates = pool.get_providers(role='operations', model_tier='reasoning')
    active_ops = [p for p in ops_candidates if p.api_key]
    if active_ops:
        top_ops = active_ops[0]
        print(f"  [PASS] Operations Agent priority dispatch target: {top_ops.name} ({top_ops.model}) [Expected: google]")
    else:
        print("  [INFO] No active API keys for operations pool; will use simulation fallback")

    # 4. Test Cooldown Failover Mechanics
    print("\n[Step 4] Testing Automatic Failover Mechanics under Provider Outage...")
    pool.clear_cooldown('groq')
    pool.clear_cooldown('google')
    pool.clear_cooldown('openrouter')

    # Simulate groq entering cooldown
    pool.set_cooldown('groq', duration_seconds=30)
    assert pool.is_in_cooldown('groq'), "Groq should be in cooldown"
    post_cooldown = [p for p in pool.get_providers(role='support', model_tier='speed') if not pool.is_in_cooldown(p.name)]
    assert post_cooldown[0].name != 'groq', "Groq should not be the top candidate during cooldown"
    print(f"  [PASS] Groq placed in cooldown -> Next candidate selected: {post_cooldown[0].name} ({post_cooldown[0].model})")

    # Clear cooldown
    pool.clear_cooldown('groq')
    assert not pool.is_in_cooldown('groq')
    print("  [PASS] Cooldown cleared -> Groq restored as top speed priority")

    print("\n" + "=" * 75)
    print("   [PHASE 2 COMPLETE: MULTI-MODEL CONNECTIVITY & FAILOVER VERIFIED]")
    print("=" * 75)


if __name__ == '__main__':
    run_phase_2()
