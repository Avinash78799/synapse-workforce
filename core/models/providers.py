"""Small provider boundaries. Live model suggests intent; code authorizes actions."""
import hashlib
import math
import re
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from redis import Redis
from uuid import UUID
from dataclasses import dataclass
from typing import Literal, Protocol
import httpx
from pydantic import BaseModel, ConfigDict
from core.config import settings
from core.security.secrets import VaultSecrets
from core.models.prompts import METADATA_EXTRACTOR
from core.security.pii import redact_pii


class Suggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['daily_operations','resolve_ticket','unsupported']
    summary: str
    tenant_id: UUID
    agent_scope: Literal['operations','support']
    target_entity_id: str | None = None
    risk_classification: Literal['low','medium','high'] = 'medium'
    vector_search_query: str


@dataclass
class ModelResult:
    suggestion: Suggestion
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class LLMProvider(Protocol):
    def understand(self, kind: str, objective: str, tenant_id: str, entity_id: str | None = None) -> ModelResult: ...


class SimulationProvider:
    """No LLM, no invented model scores. Reproducible workflow intent for V1 tests."""
    def understand(self, kind, objective, tenant_id, entity_id=None):
        intent = 'daily_operations' if kind == 'operations' else 'resolve_ticket'
        query='payments orders inventory shipments' if kind=='operations' else 'duplicate payment refund policy'
        return ModelResult(Suggestion(intent=intent, summary='Execute the configured simulation workflow.',
            tenant_id=tenant_id,agent_scope=kind,target_entity_id=entity_id,vector_search_query=query), cost_usd=0)


class ChatCompletionsProvider:
    def understand(self, kind, objective, tenant_id, entity_id=None):
        base_url = settings.llm_base_url.strip()
        if not (base_url.startswith('https://') or base_url.startswith('http://')) or not settings.llm_model:
            raise RuntimeError('Live model requires an HTTP(S) endpoint and an explicit model')
        input_price = settings.input_usd_per_million if settings.input_usd_per_million > 0 else 2.50
        output_price = settings.output_usd_per_million if settings.output_usd_per_million > 0 else 10.00
        budget = settings.daily_model_budget_usd
        if not math.isfinite(budget) or budget <= 0:
            raise RuntimeError('Positive model budget required')
        system = METADATA_EXTRACTOR+'\nSERVER ENVELOPE:\n'+json.dumps({'tenant_id':str(tenant_id),'agent_scope':kind,'target_entity_id':entity_id})
        sanitized_objective = redact_pii(objective)
        objective = sanitized_objective.encode()[:2500].decode(errors='ignore')
        # Conservative byte-count reservation plus chat framing allowance. Use a
        # matching tokenizer / provider usage cap before supporting other tokenizers.
        upper_input=len(system.encode())+len(objective.encode())+500
        if upper_input>settings.max_run_tokens:
            raise RuntimeError('Input is too large for the configured model budget')
        reserve=int((Decimal(upper_input)*Decimal(str(input_price))+
                     Decimal(500)*Decimal(str(output_price))).to_integral_value(rounding=ROUND_CEILING))
        redis=Redis.from_url(settings.redis_url)
        budget_key=f"workforce:model-budget:{tenant_id}:{datetime.now(timezone.utc).date()}"
        permitted=redis.eval("local n=tonumber(redis.call('GET',KEYS[1]) or '0'); if n+tonumber(ARGV[1])>tonumber(ARGV[2]) then return 0 end; redis.call('INCRBY',KEYS[1],ARGV[1]); redis.call('EXPIRE',KEYS[1],172800); return 1",1,budget_key,reserve,int(budget*1_000_000))
        if not permitted:
            raise RuntimeError('Tenant daily model budget exhausted')
        # Retain the reservation after ambiguous errors; a crash or unknown usage
        # cannot replenish spend capacity and accidentally permit more requests.
        key = VaultSecrets().read(settings.llm_secret_ref)
        try:
            response = httpx.post(base_url.rstrip('/') + '/chat/completions',
                headers={'Authorization': f'Bearer {key}'}, timeout=30,
                json={'model':settings.llm_model,'messages':[{'role':'system','content':system},
                    {'role':'user','content':objective[:4000]}], 'max_tokens':500,
                    'response_format':{'type':'json_object'}})
            response.raise_for_status()
            body = response.json()
            raw_content = body['choices'][0]['message']['content'].strip()
            # Strip markdown code blocks if the model outputs ```json ... ```
            if raw_content.startswith('```'):
                raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content, flags=re.IGNORECASE)
                raw_content = re.sub(r'\s*```$', '', raw_content)
            suggestion = Suggestion.model_validate_json(raw_content.strip())
            if str(suggestion.tenant_id)!=str(tenant_id) or suggestion.agent_scope!=kind or suggestion.target_entity_id!=entity_id:
                raise ValueError('Model attempted to change the authenticated scope')
            usage = body.get('usage',{})
            if 'prompt_tokens' not in usage or 'completion_tokens' not in usage:
                raise ValueError('Provider usage accounting is required')
            if any(type(usage[k]) is not int or usage[k]<0 for k in ('prompt_tokens','completion_tokens')):
                raise ValueError('Invalid provider token counters')
            if usage['prompt_tokens']>upper_input or usage['completion_tokens']>500:
                raise ValueError('Provider exceeded the reserved token contract')
            actual=(usage['prompt_tokens']*input_price+usage['completion_tokens']*output_price)/1_000_000
            # Keep the conservative reservation until end of day. Display actual
            # priced usage, but do not release capacity after an uncertain request.
            return ModelResult(suggestion,usage['prompt_tokens'],usage['completion_tokens'],round(actual,6))
        except Exception:
            # Never log provider bodies/headers, which may include sensitive content.
            raise RuntimeError('Model request failed or returned an invalid plan') from None


def get_model():
    if settings.model_provider == 'simulation':
        return SimulationProvider()
    if settings.model_provider in ('multi', 'pool', 'adaptive', 'auto'):
        from core.models.pool import MultiModelPool
        return MultiModelPool()
    if settings.model_provider in ('chat_completions', 'astra', 'openai', 'groq', 'google', 'gemini', 'openrouter'):
        return ChatCompletionsProvider()
    raise RuntimeError('Unknown model provider; refusing to silently fall back')


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class SimulationEmbeddings:
    """64-dimension hashed lexical vectors; not a semantic model or benchmark."""
    def embed(self, text):
        vector = [0.0] * 64
        for word in re.findall(r'\w+', text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:2],'big') % 64] += 1 if digest[2] % 2 else -1
        norm = math.sqrt(sum(x*x for x in vector)) or 1
        return [round(x/norm,8) for x in vector]


class ExternalEmbeddingProvider:
    """Connects to an external OpenAI/Astra-compatible embedding endpoint."""
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.embedding_base_url or settings.llm_base_url or '').rstrip('/')
        self.model = model or settings.embedding_model or 'text-embedding-3-small'
        self.api_key = api_key or settings.llm_api_key

    def embed(self, text: str) -> list[float]:
        if not self.base_url or not self.api_key or not settings.embedding_model:
            raise RuntimeError('External embeddings require an endpoint, explicit model and key')
        try:
            resp = httpx.post(f'{self.base_url}/embeddings', headers={'Authorization': f'Bearer {self.api_key}'},
                              json={'input': redact_pii(text)[:8000], 'model': self.model, 'dimensions': 64}, timeout=15)
            resp.raise_for_status()
            vector = resp.json()['data'][0]['embedding']
            if not isinstance(vector, list) or len(vector) != 64 or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in vector) or not any(vector):
                raise ValueError('Embedding must contain 64 finite values and have nonzero norm')
            return vector
        except Exception:
            raise RuntimeError('External embedding failed or does not match the 64-dimensional index; no fallback used') from None


def get_embedding_provider():
    if settings.embedding_provider in ('external', 'astra', 'openai'):
        return ExternalEmbeddingProvider()
    if settings.embedding_provider == 'simulation':
        return SimulationEmbeddings()
    raise RuntimeError('Unknown embedding provider')


class VoiceProvider(Protocol):
    """Future interface only. V1 does not offer voice."""
    def transcribe(self, audio: bytes) -> str: ...
    def synthesize(self, text: str) -> bytes: ...
