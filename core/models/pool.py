"""Multi-model adaptive routing pool: Groq (fastest), Google Gemini, and OpenRouter.

Features:
- Fastest-first prioritization (Groq prioritized during low/normal traffic).
- Automatic cycling failover on 429 rate limits, quota limits, or server errors.
- Dynamic provider cooldown tracking.
- Merged usage and token cost accounting.
"""
import time
import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timezone
from redis import Redis
import httpx
from core.config import settings
from core.models.prompts import METADATA_EXTRACTOR
from core.models.providers import Suggestion, ModelResult
from core.security.pii import redact_pii


@dataclass
class ProviderNode:
    name: str
    base_url: str
    model: str
    api_key: str
    priority: int  # 1 = highest / fastest
    input_usd_per_million: float
    output_usd_per_million: float


class MultiModelPool:
    """Adaptive model pool managing automatic failover and latency-optimal routing."""

    def __init__(self):
        self._cooldowns: dict[str, float] = {}

    def get_providers(self) -> list[ProviderNode]:
        """Returns configured providers ordered by priority (fastest first)."""
        nodes = []
        # Groq (Priority 1 - ultra-low latency inference ~300 t/s)
        groq_key = settings.groq_api_key or (settings.llm_api_key if 'gsk_' in settings.llm_api_key else '')
        nodes.append(ProviderNode(
            name='groq',
            base_url=settings.groq_base_url.rstrip('/'),
            model=settings.groq_model,
            api_key=groq_key,
            priority=1,
            input_usd_per_million=0.59,
            output_usd_per_million=0.79
        ))

        # Google Gemini (Priority 2 - state-of-the-art reasoning, 1,500 req/day free)
        google_key = settings.google_api_key or (settings.llm_api_key if 'AIzaSy' in settings.llm_api_key else '')
        nodes.append(ProviderNode(
            name='google',
            base_url=settings.google_base_url.rstrip('/'),
            model=settings.google_model,
            api_key=google_key,
            priority=2,
            input_usd_per_million=0.10,
            output_usd_per_million=0.40
        ))

        # OpenRouter (Priority 3 - broad free fallback pool)
        openrouter_key = settings.openrouter_api_key or (settings.llm_api_key if 'sk-or-' in settings.llm_api_key else '')
        nodes.append(ProviderNode(
            name='openrouter',
            base_url=settings.openrouter_base_url.rstrip('/'),
            model=settings.openrouter_model,
            api_key=openrouter_key,
            priority=3,
            input_usd_per_million=0.00,
            output_usd_per_million=0.00
        ))

        # Also support general custom LLM configuration if set and not already in nodes
        if settings.llm_base_url and settings.llm_model:
            nodes.append(ProviderNode(
                name='custom_llm',
                base_url=settings.llm_base_url.rstrip('/'),
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                priority=4,
                input_usd_per_million=settings.input_usd_per_million or 2.50,
                output_usd_per_million=settings.output_usd_per_million or 10.00
            ))

        return sorted(nodes, key=lambda p: p.priority)

    def is_in_cooldown(self, provider_name: str) -> bool:
        """Checks whether a provider is currently cooling down from a rate-limit."""
        return time.time() < self._cooldowns.get(provider_name, 0.0)

    def set_cooldown(self, provider_name: str, duration_seconds: int | None = None) -> None:
        """Puts a provider into cooldown after rate-limit or error."""
        sec = duration_seconds or settings.model_cooldown_seconds
        self._cooldowns[provider_name] = time.time() + sec

    def clear_cooldown(self, provider_name: str) -> None:
        self._cooldowns.pop(provider_name, None)

    def extract_valid_json(self, raw: str) -> str:
        s = raw.strip()
        if s.startswith('```'):
            s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s*```$', '', s).strip()
        try:
            json.loads(s)
            return s
        except Exception:
            pass
        # Handle double opening braces e.g. {\n{ ... }
        if s.startswith(('{{', '{\n{', '{\r\n{')):
            s2 = re.sub(r'^\{\s*\{', '{', s)
            s2 = re.sub(r'\}\s*\}$', '}', s2)
            try:
                json.loads(s2)
                return s2
            except Exception:
                pass
        # Extract balanced JSON substring
        for start in range(len(s)):
            if s[start] == '{':
                for end in range(len(s), start + 1, -1):
                    if s[end - 1] == '}':
                        candidate = s[start:end]
                        try:
                            json.loads(candidate)
                            return candidate
                        except Exception:
                            continue
        return s

    def understand(self, kind: str, objective: str, tenant_id: str, entity_id: str | None = None) -> ModelResult:
        system = METADATA_EXTRACTOR + '\nSERVER ENVELOPE:\n' + json.dumps({
            'tenant_id': str(tenant_id),
            'agent_scope': kind,
            'target_entity_id': entity_id
        })
        sanitized_objective = redact_pii(objective)
        trimmed_objective = sanitized_objective.encode()[:2500].decode(errors='ignore')
        upper_input = len(system.encode()) + len(trimmed_objective.encode()) + 500
        if upper_input > settings.max_run_tokens:
            raise RuntimeError('Input is too large for the configured model budget')

        # Conservative reservation across providers
        max_in_price = Decimal('2.50')
        max_out_price = Decimal('10.00')
        reserve = int((Decimal(upper_input) * max_in_price + Decimal(500) * max_out_price).to_integral_value(rounding=ROUND_CEILING))

        budget_key = f"workforce:model-budget:{tenant_id}:{datetime.now(timezone.utc).date()}"
        try:
            redis = Redis.from_url(settings.redis_url)
            permitted = redis.eval(
                "local n=tonumber(redis.call('GET',KEYS[1]) or '0'); if n+tonumber(ARGV[1])>tonumber(ARGV[2]) then return 0 end; redis.call('INCRBY',KEYS[1],ARGV[1]); redis.call('EXPIRE',KEYS[1],172800); return 1",
                1, budget_key, reserve, int(settings.daily_model_budget_usd * 1_000_000)
            )
            if not permitted:
                raise RuntimeError('Tenant daily model budget exhausted')
        except Exception as exc:
            if 'budget exhausted' in str(exc):
                raise

        providers = self.get_providers()
        # Find active providers with an API key
        available = [p for p in providers if p.api_key or p.base_url]
        if not available:
            # If no provider keys configured, use the full list allowing mock/proxy
            available = providers

        # Filter out those in cooldown; if all are cooling down, attempt all to break deadlock
        healthy = [p for p in available if not self.is_in_cooldown(p.name)]
        candidates = healthy if healthy else available

        errors = []
        for provider in candidates:
            try:
                result = self._dispatch(provider, system, trimmed_objective, tenant_id, kind, entity_id, upper_input)
                self.clear_cooldown(provider.name)
                return result
            except Exception as e:
                # Put provider in cooldown on rate limit, quota, timeout, or server error
                self.set_cooldown(provider.name)
                errors.append(f"{provider.name}: {str(e)[:100]}")

        # If we reached here, candidates failed; try any remaining providers
        remaining = [p for p in available if p not in candidates]
        for provider in remaining:
            try:
                result = self._dispatch(provider, system, trimmed_objective, tenant_id, kind, entity_id, upper_input)
                self.clear_cooldown(provider.name)
                return result
            except Exception as e:
                self.set_cooldown(provider.name)
                errors.append(f"{provider.name}: {str(e)[:100]}")

        raise RuntimeError(f"All model providers in pool failed or were rate-limited: {'; '.join(errors)}")

    def _dispatch(self, provider: ProviderNode, system: str, objective: str, tenant_id: str, kind: str, entity_id: str | None, upper_input: int) -> ModelResult:
        headers = {'Content-Type': 'application/json'}
        if provider.api_key:
            headers['Authorization'] = f'Bearer {provider.api_key}'

        payload = {
            'model': provider.model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': objective[:4000]}
            ],
            'max_tokens': 1000,
            'response_format': {'type': 'json_object'}
        }

        response = httpx.post(
            f"{provider.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=25.0
        )
        response.raise_for_status()
        body = response.json()
        raw_content = (body['choices'][0]['message'].get('content') or '').strip()
        cleaned_json = self.extract_valid_json(raw_content)

        suggestion = Suggestion.model_validate_json(cleaned_json)
        if str(suggestion.tenant_id) != str(tenant_id) or suggestion.agent_scope != kind or suggestion.target_entity_id != entity_id:
            raise ValueError('Model attempted to change the authenticated scope')

        usage = body.get('usage', {})
        prompt_tokens = usage.get('prompt_tokens', upper_input // 4)
        completion_tokens = usage.get('completion_tokens', len(raw_content) // 4)

        if prompt_tokens > upper_input or completion_tokens > 1000:
            raise ValueError('Provider exceeded the reserved token contract')

        actual_cost = (prompt_tokens * provider.input_usd_per_million + completion_tokens * provider.output_usd_per_million) / 1_000_000
        return ModelResult(suggestion, prompt_tokens, completion_tokens, round(actual_cost, 6))
