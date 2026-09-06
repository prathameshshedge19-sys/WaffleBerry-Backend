"""Passive turn instrumentation. No database access and no content serialization."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import logging
import json
import queue
import threading
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.observability import DIMENSIONS, ERRORS, STAGES

logger = logging.getLogger('app.conversation.telemetry')

clock = time.perf_counter
enabled = True
_current = ContextVar('turn_observation', default=None)


class BufferedLogSink:
    """Bounded nonblocking producer; a slow/broken handler cannot hold up chat."""
    def __init__(self, capacity=1024):
        self.queue = queue.Queue(capacity)
        self.thread = None
        self.lock = threading.Lock()
        self.dropped = 0

    def emit(self, event, level):
        if self.thread is None:
            with self.lock:
                if self.thread is None:
                    self.thread = threading.Thread(target=self._run, daemon=True, name='conversation-telemetry')
                    self.thread.start()
        try:
            self.queue.put_nowait((event, level))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        # Uvicorn configures only its own loggers. Wait until delivery starts
        # so application startup can install its own handlers first. Supply a
        # message-only fallback for this namespace; never configure root.
        try:
            if not logger.hasHandlers() and logger.level == logging.NOTSET:
                logger.addHandler(logging.StreamHandler())
                logger.setLevel(logging.INFO)
                logger.propagate = False
        except Exception:
            pass  # Logging setup must be as passive as event delivery.
        while True:
            event, level = self.queue.get()
            try:
                # JSON remains useful with existing plain message formatters;
                # structured exporters may consume LogRecord.telemetry directly.
                logger.log(level, json.dumps(event, separators=(',', ':'), allow_nan=False), extra={'telemetry': event})
            except Exception:
                self.dropped += 1  # Never retry a broken exporter or log its exception.
            finally:
                self.queue.task_done()


sink = BufferedLogSink()


def passive(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            return None
    return guarded


@passive
def now():
    return clock() if enabled else None


def elapsed(start, end=None):
    end = now() if end is None else end
    return max(0.0, (end - start) * 1000) if start is not None and end is not None else None


@passive
def error_category(error, stage=None):
    # Never serialize str/repr, HTTP details or an unchecked provider kind.
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)): return 'cancelled'
    cause = getattr(error, '__cause__', None)
    if isinstance(error, TimeoutError) or isinstance(cause, TimeoutError) or type(cause).__name__ == 'APITimeoutError':
        return 'provider_timeout'
    if isinstance(error, SQLAlchemyError): return 'persistence_failed'
    if isinstance(error, HTTPException):
        if error.status_code in {401, 403, 404}: return 'authorization_denied'
        if error.status_code == 409: return 'turn_conflict'
    kind = getattr(error, 'kind', None)
    if kind in {'rya_provider_connection', 'legacy_persona_connection', 'web_search_connection', 'voice_provider_connection'}:
        return 'provider_connection'
    if kind in {'rya_provider_timeout', 'legacy_persona_timeout', 'voice_provider_timeout'}:
        return 'provider_timeout'
    if stage == 'memory_retrieval': return 'memory_retrieval_failed'
    if stage == 'current_information': return 'current_info_unavailable'
    if stage in {'post_turn_effects', 'memory_effect', 'activity_effect'}: return 'post_processing_failed'
    if stage in {'provider_generation', 'voice_provider'}: return 'provider_failed'
    return 'unknown_internal'


@dataclass
class TurnObservation:
    request_id: str = field(default_factory=lambda: str(uuid4()))
    conversation_turn_id: int | None = None
    generation_attempt_id: str | None = None
    session_id: str | None = None  # Reserved for a server-generated future UUID.
    dimensions: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    started: float | None = field(default_factory=now)
    accepted_at: float | None = None
    durable: bool = False
    failure: str | None = None
    delta_count: int = 0


@passive
def correlation():
    observation = _current.get()
    if observation is None: return {}
    result = {}
    for key in ('request_id', 'generation_attempt_id', 'session_id'):
        value = getattr(observation, key)
        if value is not None:
            try: result[key] = str(UUID(value))
            except (ValueError, TypeError, AttributeError): pass
    value = observation.conversation_turn_id
    if type(value) is int and value > 0: result['conversation_turn_id'] = value
    return result


@passive
def emit(event, *, level=logging.DEBUG, duration_ms=None, category=None, dimensions=None, values=None):
    if not enabled: return
    if event not in {'stage_finished', 'turn_completed', 'turn_failed', 'turn_interrupted', 'turn_replayed',
                     'component_degraded', 'provider_usage', 'telemetry_failure', 'tool_finished'}: return
    observation = _current.get()
    merged = dict(observation.dimensions) if observation else {}
    merged.update(dimensions or {})
    labels = {key: value for key, value in merged.items() if key in DIMENSIONS and value in DIMENSIONS[key]}
    record = {'event': event, 'dimensions': labels, 'correlation': correlation() or {}}
    if duration_ms is not None and type(duration_ms) in {int, float} and 0 <= duration_ms < 1e12:
        record['duration_ms'] = round(duration_ms, 4)
    if category in ERRORS: record['error_category'] = category
    # No arbitrary values API: only fixed stage timings/counters can be exported.
    for key, value in (values or {}).items():
        if key in STAGES | {'accepted_to_preparation_ms', 'provider_start_ms', 'time_to_first_text_delta',
                            'provider_first_result_ms', 'provider_time_to_first_delta', 'time_to_assistant_durable',
                            'time_to_durable_completion', 'delta_count', 'tool_calls'}:
            if type(value) in {int, float} and 0 <= value < 1e12: record[key] = round(value, 4)
    sink.emit(record, level)


@passive
def metadata(**values):
    observation = _current.get()
    if observation:
        observation.dimensions.update({key: value for key, value in values.items()
                                       if key in DIMENSIONS and value in DIMENSIONS[key]})


@passive
def accepted(turn_id, mode, input_mode):
    observation = _current.get()
    if observation:
        observation.conversation_turn_id = turn_id
        observation.generation_attempt_id = str(uuid4())
        observation.accepted_at = now()
        metadata(mode=mode, input_mode=input_mode)


@passive
def replayed(turn_id, mode, input_mode):
    observation = _current.get()
    if observation:
        observation.conversation_turn_id = turn_id
        metadata(mode=mode, input_mode=input_mode, outcome='replayed')


@passive
def timing(key, start):
    observation = _current.get()
    value = elapsed(start)
    if observation and value is not None: observation.timings[key] = value


@passive
def durable():
    observation = _current.get()
    if observation:
        observation.durable = True
        timing('time_to_assistant_durable', observation.accepted_at)
        timing('time_to_durable_completion', observation.accepted_at)


@passive
def failed(category):
    observation = _current.get()
    if observation and (not observation.failure or category == 'cancelled'):
        observation.failure = category if category in ERRORS else 'unknown_internal'


@passive
def degraded(category):
    emit('component_degraded', level=logging.WARNING, category=category)


class Stage:
    def __init__(self, name): self.name, self.started = name, None
    def __enter__(self):
        self.started = now()
        if self.name == 'preparation':
            observation = _current.get()
            if observation: timing('accepted_to_preparation_ms', observation.accepted_at)
        return self
    def __exit__(self, kind, error, traceback):
        try:
            duration = elapsed(self.started)
            observation = _current.get()
            if observation and duration is not None and self.name in STAGES:
                observation.timings[self.name] = observation.timings.get(self.name, 0) + duration
            emit('stage_finished', duration_ms=duration,
                 category=error_category(error, self.name) if error else None,
                 values={self.name: duration} if duration is not None else {})
            if error and self.name in {'assistant_persistence', 'post_turn_effects'}:
                failed(error_category(error, self.name))
            if observation and self.name == 'post_turn_effects':
                if error:
                    observation.timings.pop('time_to_durable_completion', None)
                elif observation.durable:
                    timing('time_to_durable_completion', observation.accepted_at)
        except Exception: pass
        return False


def stage(name): return Stage(name)


def timed(name):
    def decorate(function):
        if asyncio.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with stage(name): return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with stage(name): return function(*args, **kwargs)
        return synchronous
    return decorate


@contextmanager
def bound(observation):
    token = _current.set(observation)
    try: yield
    finally: _current.reset(token)


@passive
def summarize(observation):
    if observation is None: return
    outcome = 'completed' if observation.durable else 'interrupted' if observation.failure == 'cancelled' else 'failed'
    if observation.dimensions.get('outcome') == 'replayed': outcome = 'replayed'
    metadata(outcome=outcome)
    emit('turn_' + outcome, level=logging.INFO if outcome in {'completed', 'replayed'} and not observation.failure else logging.ERROR,
         duration_ms=elapsed(observation.accepted_at or observation.started), category=observation.failure,
         values={**observation.timings, 'delta_count': observation.delta_count})


def observe_turn(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        observation = passive(TurnObservation)()
        with bound(observation):
            try: response = await function(*args, **kwargs)
            except BaseException as error:
                failed(error_category(error)); summarize(observation); raise
            if not isinstance(response, StreamingResponse):
                summarize(observation)
                return response
        original = response.body_iterator
        async def stream():
            with bound(observation):
                try:
                    async for value in original: yield value
                except BaseException as error:
                    failed(error_category(error)); raise
                finally:
                    try: await original.aclose()
                    finally: summarize(observation)
        response.body_iterator = stream()
        return response
    return wrapped


def observe_tool(function):
    @wraps(function)
    async def wrapped(self, context, name, arguments):
        observation = _current.get() or passive(TurnObservation)()
        with bound(observation):
            try:
                actor = getattr(context, 'actor', None)
                if actor is not None:
                    metadata(mode=actor.mode, role=actor.role, input_mode=actor.input_mode)
                    if observation: observation.conversation_turn_id = context.turn_id
            except Exception: pass
            start = now()
            try:
                if observation: observation.timings['tool_calls'] = observation.timings.get('tool_calls', 0) + 1
            except Exception: pass
            try:
                result = await function(self, context, name, arguments)
            except BaseException as error:
                category = error_category(error)
                emit('tool_finished', duration_ms=elapsed(start), category=category,
                     dimensions={'tool_name': name if isinstance(name, str) and name in DIMENSIONS['tool_name'] else 'unknown',
                                 'outcome': 'interrupted' if category == 'cancelled' else 'failed'})
                raise
            # The result itself is never passed to a sink. Only its fixed code.
            try:
                code = result.for_turn(context).get('error', {}).get('code')
                category = {'tool_scope_invalid': 'scope_invalid', 'tool_data_unavailable': 'memory_retrieval_failed',
                    'tool_current_info_unavailable': 'current_info_unavailable', 'tool_internal_error': 'unknown_internal',
                    'tool_not_allowed': 'tool_not_allowed', 'tool_invalid_arguments': 'tool_invalid_arguments'}.get(code)
                emit('tool_finished', duration_ms=elapsed(start), category=category,
                     dimensions={'tool_name': name if isinstance(name, str) and name in DIMENSIONS['tool_name'] else 'unknown',
                                 'outcome': 'failed' if code else 'completed'})
            except Exception: pass
            return result
    return wrapped
