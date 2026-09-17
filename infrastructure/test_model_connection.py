"""Utility script to verify live model connections (Groq, Google Gemini, OpenRouter, Astra).

Usage:
    python infrastructure/test_model_connection.py
"""
import sys
import time
from uuid import uuid4
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from core.config import settings
from core.models.pool import MultiModelPool, ProviderNode
from core.models.providers import ChatCompletionsProvider, get_model


def test_provider(node: ProviderNode) -> bool:
    print(f"\n--- Testing Provider: {node.name.upper()} ---")
    print(f"  Endpoint: {node.base_url}")
    print(f"  Model:    {node.model}")
    if not node.api_key:
        print(f"  Status:   [SKIPPED] No API key configured ({node.name.upper()}_API_KEY is empty in .env)")
        return False

    masked_key = node.api_key[:6] + "..." + node.api_key[-4:] if len(node.api_key) > 10 else "***"
    print(f"  Key:      {masked_key}")

    pool = MultiModelPool()
    from core.models.prompts import METADATA_EXTRACTOR
    import json
    sample_tenant = str(uuid4())
    system = METADATA_EXTRACTOR + '\nSERVER ENVELOPE:\n' + json.dumps({
        'tenant_id': sample_tenant,
        'agent_scope': 'support',
        'target_entity_id': 'ORD-1002'
    })
    objective = "Customer Emma reported duplicate payment on order ORD-1002 of INR 75,000."

    start_time = time.time()
    try:
        result = pool._dispatch(
            node,
            system=system,
            objective=objective,
            tenant_id=sample_tenant,
            kind='support',
            entity_id='ORD-1002',
            upper_input=1000
        )
        elapsed_ms = int((time.time() - start_time) * 1000)
        print(f"  Status:   [SUCCESS] Received valid structured response in {elapsed_ms}ms")
        print(f"  Intent:   {result.suggestion.intent}")
        print(f"  Summary:  {result.suggestion.summary}")
        print(f"  Tokens:   {result.input_tokens} prompt + {result.output_tokens} completion")
        print(f"  Est Cost: ${result.cost_usd:.6f}")
        return True
    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        print(f"  Status:   [FAILED] (after {elapsed_ms}ms)")
        print(f"  Error:    {str(exc)}")
        return False


def main():
    print("=" * 60)
    print("AI Agent Workforce Platform - Model Connection Diagnostic")
    print("=" * 60)
    print(f"Current MODEL_PROVIDER setting: {settings.model_provider}")

    pool = MultiModelPool()
    providers = pool.get_providers()

    configured_count = sum(1 for p in providers if p.api_key)
    print(f"Configured Providers with keys: {configured_count} of {len(providers)}")

    if configured_count == 0:
        print("\n[!] No live API keys found in .env yet.")
        print("To connect live models, set any of the following in .env:")
        print("  - GROQ_API_KEY=gsk_... (Fastest, ~300 t/s)")
        print("  - GOOGLE_API_KEY=AIzaSy... (Gemini 2.0 Flash)")
        print("  - OPENROUTER_API_KEY=sk-or-v1-... (Llama 3.3 Free)")
        print("  - LLM_API_KEY=sk-... (Astra / OpenAI)")
        return

    results = {}
    for p in providers:
        results[p.name] = test_provider(p)

    print("\n" + "=" * 60)
    print("Summary of Connection Tests:")
    for name, success in results.items():
        status = "PASSED" if success else ("FAILED" if pool.get_providers() else "SKIPPED")
        print(f"  - {name.capitalize():12}: {status}")
    print("=" * 60)


if __name__ == '__main__':
    main()
