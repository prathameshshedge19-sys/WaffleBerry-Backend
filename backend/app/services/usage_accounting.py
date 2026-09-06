"""Best-effort usage hooks; no ledger, cost estimates, SQL or request retries."""
from contextvars import ContextVar
from dataclasses import asdict, fields
import hashlib
import logging
import math
import re
from typing import Protocol
from uuid import uuid4

from app.config import get_settings
from app.schemas.observability import ProviderUsage, UsageEvent, UsageValue
from app.services import turn_observability as obs

_call = ContextVar('provider_usage_call', default=None)


class UsageSink(Protocol):
    """record must enqueue without blocking; downstream export owns its own queue."""
    def record(self, event: UsageEvent) -> None: ...


class StructuredUsageSink:
    def record(self, event):
        obs.sink.emit({'event': 'provider_usage', 'correlation': event.correlation,
            'dimensions': {'provider_kind': event.kind, 'outcome': event.outcome},
            'usage': asdict(event.usage), 'event_id': event.event_id, 'attempt_id': event.attempt_id,
            'provider_request_hash': event.provider_request_hash,
            'cache_hit': event.cache_hit, 'request_count': event.request_count,
            'request_count_status': event.request_count_status}, logging.INFO)


class BufferedUsageSink(obs.BufferedLogSink):
    """Optional adapter for a slow external consumer. Requests only enqueue.

    Queue overflow drops telemetry; export failures get one safe warning and no
    automatic retry. This adapter never shares an assistant-message transaction.
    """
    def __init__(self, consumer: UsageSink, capacity=256):
        super().__init__(capacity)
        self.consumer = consumer

    def record(self, event):
        self.emit(event, logging.INFO)

    def _run(self):
        while True:
            event, _ = self.queue.get()
            try:
                self.consumer.record(event)
            except Exception:
                self.dropped += 1
                obs.emit('telemetry_failure', level=logging.WARNING)
            finally:
                self.queue.task_done()


sink: UsageSink = StructuredUsageSink()


def clean_usage(usage):
    values = {}
    for descriptor in fields(ProviderUsage):
        if descriptor.name == 'model': continue
        value = getattr(usage, descriptor.name, None)
        if (isinstance(value, UsageValue) and value.status in {'measured', 'estimated'}
                and type(value.value) in {int, float} and math.isfinite(value.value) and 0 <= value.value < 1e12
                and (descriptor.name == 'duration_seconds' or type(value.value) is int)):
            values[descriptor.name] = value
    settings = get_settings()
    configured = {settings.ai_model, settings.memory_extraction_model, settings.memory_embedding_model,
                  settings.voice_transcription_model, settings.voice_tts_model,
                  settings.realtime_model, settings.realtime_transcription_model}
    model = usage.model
    if isinstance(model, str) and model in configured and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model) and not model.startswith('sk-'):
        values['model'] = model
    return ProviderUsage(**values)


class RequestUsage:
    def __init__(self, kind, *, cache_hit=False, outcome=None):
        self.outcome_override = outcome if outcome in {'completed', 'failed', 'interrupted'} else None
        self.kind, self.cache_hit = kind, cache_hit
        self.usage = ProviderUsage()
        self.request_hash = None
        self.token = None
        self.attempt_id = None
        self.recorded = False

    @obs.passive
    def __enter__(self):
        if not obs.enabled: return self
        self.attempt_id = str(uuid4())
        self.token = _call.set(self)
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if self.token is not None: _call.reset(self.token)
            if self.attempt_id is None or self.recorded: return False
            self.recorded = True
            category = obs.error_category(error)
            outcome = 'interrupted' if category == 'cancelled' else 'failed' if error else 'completed'
            if self.outcome_override is not None and error is None:
                outcome = self.outcome_override
            event_id = hashlib.sha256((self.kind + ':' + self.request_hash).encode()).hexdigest() if self.request_hash else self.attempt_id
            event = UsageEvent(event_id, self.attempt_id, self.kind, outcome,
                               clean_usage(self.usage), obs.correlation() or {}, self.request_hash,
                               self.cache_hit, 0 if self.cache_hit else 1)
            if self.kind in {'text', 'embedding', 'stt', 'tts', 'web', 'realtime'}: sink.record(event)
        except Exception:
            obs.emit('telemetry_failure', level=logging.WARNING)
        return False


@obs.passive
def report_usage(usage: ProviderUsage, *, provider_request_id=None):
    """Provider-neutral hook; may run before a provider subsequently fails/cancels.

    One event per actual invocation; repeated terminal usage snapshots replace
    that invocation's snapshot, not add fake requests. Request IDs are hashed.
    """
    current = _call.get()
    if current and isinstance(usage, ProviderUsage):
        current.usage = clean_usage(usage)
        if isinstance(provider_request_id, str) and 0 < len(provider_request_id) <= 256:
            current.request_hash = hashlib.sha256(provider_request_id.encode()).hexdigest()


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


@obs.passive
def capture_response(response, *, model=None):
    """Read only known SDK usage attributes, never dump a response or its content."""
    usage = _field(response, 'usage')
    if usage is None: return
    def measured(value):
        return UsageValue(value, 'measured') if type(value) in {int, float} else UsageValue()
    input_tokens = _field(usage, 'input_tokens')
    if input_tokens is None: input_tokens = _field(usage, 'prompt_tokens')  # embeddings
    report_usage(ProviderUsage(
        input_tokens=measured(input_tokens), output_tokens=measured(_field(usage, 'output_tokens')),
        cached_input_tokens=measured(_field(_field(usage, 'input_tokens_details'), 'cached_tokens')),
        duration_seconds=measured(_field(usage, 'seconds')),
        audio_input_tokens=measured(_field(_field(usage, 'input_token_details'), 'audio_tokens')),
        model=model,
    ), provider_request_id=_field(response, 'id'))


async def invoke(kind, function, *args, **kwargs):
    with RequestUsage(kind):
        return await function(*args, **kwargs)


class MemoryUsageProvider:
    """Transparent delegation; configuration and return values remain unchanged."""
    def __init__(self, provider): self.provider = provider
    def __getattr__(self, name): return getattr(self.provider, name)
    async def analyze(self, *args, **kwargs): return await invoke('text', self.provider.analyze, *args, **kwargs)
    async def embed(self, *args, **kwargs): return await invoke('embedding', self.provider.embed, *args, **kwargs)
    async def canonicalize_edit(self, *args, **kwargs): return await invoke('text', self.provider.canonicalize_edit, *args, **kwargs)


def wrap_memory(provider):
    return provider if isinstance(provider, MemoryUsageProvider) else MemoryUsageProvider(provider)


@obs.passive
def cache_hit():
    with RequestUsage('tts', cache_hit=True): pass


async def respond(provider, turns):
    observation = obs._current.get()
    if observation: obs.timing('provider_start_ms', observation.accepted_at)
    start = obs.now()
    try:
        with obs.stage('provider_generation'):
            result = await invoke('text', provider.respond, turns)
        obs.timing('provider_first_result_ms', start)
        return result
    except BaseException as error:
        obs.failed(obs.error_category(error, 'provider_generation')); raise


async def stream(provider, turns):
    observation = obs._current.get()
    if observation: obs.timing('provider_start_ms', observation.accepted_at)
    first = True
    started = obs.now()
    iterator = provider.stream(turns)
    try:
        with obs.stage('provider_generation'), RequestUsage('text'):
            try:
                async for delta in iterator:
                    if delta:
                        if first:
                            first = False
                            obs.timing('provider_time_to_first_delta', started)
                            if observation: obs.timing('time_to_first_text_delta', observation.accepted_at)
                        if observation: observation.delta_count += 1
                    yield delta
            finally:
                close = getattr(iterator, 'aclose', None)
                if close is not None: await close()
    except BaseException as error:
        obs.failed(obs.error_category(error, 'provider_generation')); raise
