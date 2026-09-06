"""Phase E privacy, fail-open behavior, deterministic timing and usage identity."""
import asyncio
from dataclasses import asdict
import json
import logging
import statistics
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.models.conversation import Message
from app.models.turn import ConversationTurn
from app.models.viewer import LegacyViewerAccess
from app.schemas.observability import DIMENSIONS, ERRORS, ProviderUsage, UsageValue
from app.services import turn_observability as obs, usage_accounting as usage
from app.services.rya import RyaProviderError
from app.services.legacy_persona import LegacyPersonaProviderError
from tests.test_conversation_turns_l14 import seed, send, canonical_snapshot
from tests.test_conversation_tools_l14 import setup, call, bind, snapshot, MEMORY, STYLE, RELATIONSHIP, WEB, FRESH


class Capture:
    def __init__(self): self.events = []
    def emit(self, event, level): self.events.append((event, level))
    def record(self, event): self.events.append(event)


@pytest.fixture
def capture(monkeypatch):
    logs, accounting = Capture(), Capture()
    monkeypatch.setattr(obs, 'sink', logs)
    monkeypatch.setattr(usage, 'sink', accounting)
    return logs, accounting


def logs_json(logs): return json.dumps([record for record, _ in logs.events])


class FakeClock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


def test_distinct_stages_and_first_delta_use_fake_clock(monkeypatch, capture):
    clock = FakeClock(); monkeypatch.setattr(obs, 'clock', clock)
    observation = obs.TurnObservation()
    class Provider:
        async def stream(self, turns):
            clock.advance(.020); yield 'PRIVATE first'
            clock.advance(.030); yield 'PRIVATE second'
    async def run():
        with obs.bound(observation):
            obs.accepted(42, 'legacy', 'voice')
            clock.advance(.010)
            with obs.stage('preparation'): clock.advance(.015)
            result = [value async for value in usage.stream(Provider(), [])]
            assert result == ['PRIVATE first', 'PRIVATE second']
            with obs.stage('assistant_persistence'): clock.advance(.005)
            obs.durable()
            with obs.stage('post_turn_effects'): clock.advance(.007)
            obs.summarize(observation)
    asyncio.run(run())
    assert observation.timings == pytest.approx({
        'accepted_to_preparation_ms': 10, 'preparation': 15, 'provider_start_ms': 25,
        'provider_time_to_first_delta': 20, 'time_to_first_text_delta': 45,
        'provider_generation': 50, 'assistant_persistence': 5, 'time_to_assistant_durable': 80,
        'post_turn_effects': 7, 'time_to_durable_completion': 87})
    assert observation.delta_count == 2
    assert 'PRIVATE' not in logs_json(capture[0])
    assert len(capture[1].events) == 1


def test_nonstreaming_first_result_and_no_invented_delta(monkeypatch, capture):
    clock = FakeClock(); monkeypatch.setattr(obs, 'clock', clock)
    observation = obs.TurnObservation()
    class Provider:
        async def respond(self, turns): clock.advance(.040); return 'private answer'
    with obs.bound(observation):
        obs.accepted(1, 'rya', 'text')
        assert asyncio.run(usage.respond(Provider(), [])) == 'private answer'
    assert observation.timings['provider_first_result_ms'] == pytest.approx(40)
    assert 'time_to_first_text_delta' not in observation.timings
    assert capture[1].events[0].usage.input_tokens.status == 'unavailable'


@pytest.mark.parametrize('role', ['owner', 'collaborator', 'viewer'])
@pytest.mark.parametrize('streaming', [False, True])
def test_turn_logs_are_content_free_and_stages_are_present(test_context, capture, role, streaming):
    client, sessions, provider, cid, _, headers, _ = seed(test_context, role)
    before = canonical_snapshot(sessions)
    response = send(client, cid, headers, role, 'What flowers do you like? PRIVATE_user_7482', streaming)
    assert response.status_code == (200 if streaming else 201)
    records = [record for record, _ in capture[0].events]
    summary = next(record for record in records if record['event'] == 'turn_completed')
    for stage in ['preparation', 'history_loading', 'classification', 'provider_generation', 'assistant_persistence', 'time_to_durable_completion']:
        assert stage in summary
    assert summary['dimensions']['role'] == role
    assert bool('time_to_first_text_delta' in summary) == streaming
    assert summary['correlation']['conversation_turn_id'] > 0
    assert all(set(record['dimensions']) <= set(DIMENSIONS) for record in records)
    content = logs_json(capture[0]) + json.dumps([asdict(event) for event in capture[1].events])
    for secret in ['PRIVATE_user_7482', 'Pallavi', 'Alex', 'jasmine', 'Bearer', headers['Authorization'], 'I am Rya', 'I can answer from']:
        assert secret not in content
    assert 'user_id' not in content and 'legacy_id' not in content and 'conversation_id' not in content
    if role == 'viewer': assert before == canonical_snapshot(sessions)
    else: assert 'memory_effect' in summary and 'activity_effect' in summary and 'post_turn_effects' in summary


@pytest.mark.parametrize('role', ['owner', 'viewer'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('broken', ['logging', 'usage', 'clock'])
def test_telemetry_failure_never_breaks_chat(test_context, monkeypatch, role, streaming, broken):
    client, sessions, _, cid, _, headers, _ = seed(test_context, role)
    def fail(*args, **kwargs): raise RuntimeError('SECRET_sink_credentials')
    if broken == 'logging': monkeypatch.setattr(obs, 'sink', SimpleNamespace(emit=fail))
    elif broken == 'usage': monkeypatch.setattr(usage, 'sink', SimpleNamespace(record=fail))
    else: monkeypatch.setattr(obs, 'clock', fail)
    response = send(client, cid, headers, role, 'What flowers do you like?', streaming)
    assert response.status_code == (200 if streaming else 201)
    with sessions() as db:
        turn = db.scalar(select(ConversationTurn))
        assert turn.state == 'completed' and db.get(Message, turn.assistant_message_id)


def test_completed_replay_does_not_account_a_new_provider_request(test_context, capture):
    client, _, _, cid, _, headers, _ = seed(test_context)
    response = send(client, cid, headers, 'owner', 'Explain photosynthesis.', False, client_turn_id='same-key')
    assert response.status_code == 201
    before = list(capture[1].events)
    replay = send(client, cid, headers, 'owner', 'Explain photosynthesis.', False, client_turn_id='same-key')
    assert replay.status_code == 201 and replay.json() == response.json()
    assert capture[1].events == before
    replay_record = next(record for record, _ in capture[0].events if record['event'] == 'turn_replayed')
    original_record = next(record for record, _ in capture[0].events if record['event'] == 'turn_completed')
    assert replay_record['correlation']['conversation_turn_id'] == original_record['correlation']['conversation_turn_id']
    assert 'generation_attempt_id' not in replay_record['correlation']


def test_usage_snapshots_identity_and_legitimate_retries(capture):
    measured = ProviderUsage(input_tokens=UsageValue(12, 'measured'), output_tokens=UsageValue(7, 'measured'), model='test-model')
    for request in ['request-A', 'request-A', 'request-B']:
        with usage.RequestUsage('text'):
            usage.report_usage(measured, provider_request_id=request)
            usage.report_usage(measured, provider_request_id=request)  # repeated terminal snapshot
    events = capture[1].events
    assert len(events) == 3 and len({event.attempt_id for event in events}) == 3
    assert events[0].event_id == events[1].event_id != events[2].event_id
    assert all(event.usage.input_tokens == UsageValue(12, 'measured') for event in events)
    assert 'request-A' not in json.dumps([asdict(event) for event in events])


@pytest.mark.parametrize('interrupted', [False, True])
def test_failed_and_interrupted_work_preserves_supplied_usage(capture, interrupted):
    async def provider():
        usage.report_usage(ProviderUsage(output_tokens=UsageValue(9, 'measured')))
        if interrupted: raise asyncio.CancelledError()
        raise RuntimeError('PRIVATE provider response')
    with pytest.raises(asyncio.CancelledError if interrupted else RuntimeError):
        asyncio.run(usage.invoke('text', provider))
    event = capture[1].events[0]
    assert event.outcome == ('interrupted' if interrupted else 'failed')
    assert event.usage.output_tokens.value == 9


def test_usage_reported_during_generator_close_is_retained(capture):
    class Provider:
        async def stream(self, turns):
            try:
                yield 'private text'
            finally:
                usage.report_usage(ProviderUsage(output_tokens=UsageValue(3, 'measured')))
    async def run():
        iterator = usage.stream(Provider(), [])
        assert await anext(iterator) == 'private text'
        await iterator.aclose()
    asyncio.run(run())
    assert len(capture[1].events) == 1
    assert capture[1].events[0].outcome == 'interrupted'
    assert capture[1].events[0].usage.output_tokens.value == 3


def test_usage_values_default_to_unavailable_and_are_sanitized(capture):
    with usage.RequestUsage('tts'):
        usage.report_usage(ProviderUsage(input_tokens=UsageValue(-1, 'measured'),
            output_tokens=UsageValue(float('nan'), 'measured'), characters=UsageValue(7, 'estimated'), model='sk-SECRET'))
    value = capture[1].events[0].usage
    assert value.input_tokens == value.output_tokens == UsageValue()
    assert value.characters == UsageValue(7, 'estimated') and value.model is None


@pytest.mark.parametrize('raw,expected', [
    ({'input_tokens': 12, 'output_tokens': 3, 'input_tokens_details': {'cached_tokens': 4}}, {'input_tokens': 12, 'output_tokens': 3, 'cached_input_tokens': 4}),
    ({'prompt_tokens': 6}, {'input_tokens': 6}),
    ({'seconds': 2.4}, {'duration_seconds': 2.4}),
    ({'input_tokens': 7, 'input_token_details': {'audio_tokens': 6}}, {'input_tokens': 7, 'audio_input_tokens': 6}),
])
def test_provider_usage_extraction_reads_only_allowlisted_metadata(capture, raw, expected):
    with usage.RequestUsage('text'):
        usage.capture_response(SimpleNamespace(id='PRIVATE_response_id', usage=raw, output_text='PRIVATE_prompt_and_text'), model='test-model')
    event = capture[1].events[0]
    for name, value in expected.items(): assert getattr(event.usage, name) == UsageValue(value, 'measured')
    assert 'PRIVATE' not in json.dumps(asdict(event))


@pytest.mark.parametrize('error,stage,expected', [
    (HTTPException(403, 'PRIVATE'), None, 'authorization_denied'),
    (HTTPException(409, 'PRIVATE'), None, 'turn_conflict'),
    (TimeoutError('PRIVATE'), None, 'provider_timeout'),
    (RyaProviderError('rya_provider_connection'), None, 'provider_connection'),
    (SQLAlchemyError('PRIVATE SQL'), None, 'persistence_failed'),
    (RuntimeError('PRIVATE'), 'memory_retrieval', 'memory_retrieval_failed'),
    (RuntimeError('PRIVATE'), 'post_turn_effects', 'post_processing_failed'),
    (RuntimeError('PRIVATE'), 'current_information', 'current_info_unavailable'),
    (RuntimeError('PRIVATE'), None, 'unknown_internal'),
])
def test_safe_taxonomy(error, stage, expected):
    assert obs.error_category(error, stage) == expected and expected in ERRORS


def test_arbitrary_metric_dimensions_and_correlation_payloads_are_discarded(capture):
    observation = obs.TurnObservation(request_id='PRIVATE_email@example.com', session_id='PRIVATE_bearer')
    with obs.bound(observation):
        obs.metadata(user_id=99, legacy_id=1, role='PRIVATE_owner', route='private query', mode='legacy')
        obs.emit('turn_completed', category='PRIVATE_exception', values={'memory_content': 'PRIVATE_secret'},
                 dimensions={'email': 'PRIVATE_email'})
    assert 'PRIVATE' not in logs_json(capture[0])
    assert capture[0].events[0][0]['dimensions'] == {'mode': 'legacy'}
    assert capture[0].events[0][0]['correlation'] == {}


def test_tool_telemetry_is_bounded_and_read_only_with_revocation(test_context, capture, monkeypatch):
    sessions, provider, context, tools, _ = setup(test_context)
    fresh = bind(sessions, context.actor.conversation_id, 3, 'viewer', FRESH)
    before = snapshot(sessions)
    for current, name, args in [(context, MEMORY, {'query': 'jasmine PRIVATE_query'}), (context, STYLE, {}),
                               (context, RELATIONSHIP, {}), (fresh, WEB, {'query': FRESH})]:
        assert call(tools, current, name, args)['ok']
    assert snapshot(sessions) == before
    assert 'PRIVATE_query' not in logs_json(capture[0]) and 'jasmine' not in logs_json(capture[0])
    finished = [r for r, _ in capture[0].events if r['event'] == 'tool_finished']
    assert {r['dimensions']['tool_name'] for r in finished} == {MEMORY, STYLE, RELATIONSHIP, WEB}
    assert all(r['duration_ms'] >= 0 for r in finished)
    with sessions.begin() as db: db.scalar(select(LegacyViewerAccess)).status = 'revoked'
    assert not call(tools, context, MEMORY, {'query': 'jasmine'})['ok']
    assert any(r.get('error_category') == 'access_changed' for r, _ in capture[0].events)
    monkeypatch.setattr(obs, 'sink', SimpleNamespace(emit=lambda *a: (_ for _ in ()).throw(RuntimeError('PRIVATE'))))
    assert not call(tools, context, MEMORY, {'query': 'jasmine'})['ok']


def test_tts_cache_hit_is_zero_requests_and_no_guessed_usage(test_context, capture):
    client, _, provider, _, _, headers, _ = seed(test_context)
    first = client.post('/api/v1/voice/preview', json={'voice': 'marin'}, headers=headers)
    second = client.post('/api/v1/voice/preview', json={'voice': 'marin'}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.headers['x-voice-cache'] == 'miss' and second.headers['x-voice-cache'] == 'hit'
    values = [e for e in capture[1].events if e.kind == 'tts']
    assert [e.request_count for e in values] == [1, 0]
    assert [e.cache_hit for e in values] == [False, True]
    assert len(provider.voice_provider.synthesis_calls) == 1
    assert all(e.usage.duration_seconds.status == 'unavailable' for e in values)


def test_bootstrap_sql_exception_is_not_logged(test_context, capture, monkeypatch):
    client, _, _, _, _, headers, _ = seed(test_context)
    def broken(*args): raise SQLAlchemyError('PRIVATE bound password and SQL')
    monkeypatch.setattr('app.api.routes.legacies.pending_or_new_legacy', broken)
    response = client.post('/api/v1/legacies/setup/bootstrap', headers=headers)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'legacy_setup_bootstrap_failed'
    assert 'PRIVATE' not in logs_json(capture[0])
    assert capture[0].events[-1][0]['error_category'] == 'persistence_failed'


def test_slow_or_broken_logging_consumer_does_not_block_producer(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def broken(*args, **kwargs):
        entered.set(); release.wait(2); raise RuntimeError('PRIVATE logger payload')
    monkeypatch.setattr(obs.logger, 'log', broken)
    sink = obs.BufferedLogSink(capacity=2)
    sink.emit({'event': 'turn_completed'}, logging.INFO)
    assert entered.wait(1)
    try:
        # Consumer remains blocked; producers never wait for queue space/export.
        for _ in range(20): sink.emit({'event': 'turn_completed'}, logging.INFO)
        assert sink.dropped > 0 and sink.queue.qsize() <= 2
    finally:
        release.set(); sink.queue.join()


@pytest.mark.parametrize('role', ['owner', 'collaborator', 'viewer'])
def test_no_extra_sql_or_provider_calls_when_observability_enabled(test_context, monkeypatch, capture, role):
    client, sessions, provider, cid, _, headers, _ = seed(test_context, role)
    counts = []
    engine = sessions.kw['bind']
    for enabled in [False, True]:
        monkeypatch.setattr(obs, 'enabled', enabled)
        statements = []
        def count(*args): statements.append(args[2].split()[0])
        event.listen(engine, 'before_cursor_execute', count)
        generation = provider.persona_provider if role == 'viewer' else provider
        before = (len(generation.calls), len(provider.memory_provider.analysis_calls), len(provider.memory_provider.embedding_calls))
        try: assert send(client, cid, headers, role, 'Explain photosynthesis.', False).status_code == 201
        finally: event.remove(engine, 'before_cursor_execute', count)
        after = (len(generation.calls), len(provider.memory_provider.analysis_calls), len(provider.memory_provider.embedding_calls))
        counts.append((statements, tuple(a-b for a,b in zip(after,before))))
    assert counts[0] == counts[1]


@pytest.mark.parametrize('role', ['owner', 'collaborator', 'viewer'])
def test_fake_provider_delay_survives_route_instrumentation(test_context, monkeypatch, capture, role):
    client, _, provider, cid, _, headers, _ = seed(test_context, role)
    clock = FakeClock(); monkeypatch.setattr(obs, 'clock', clock)
    selected = provider.persona_provider if role == 'viewer' else provider
    original = selected.respond
    async def delayed(turns): clock.advance(.025); return await original(turns)
    monkeypatch.setattr(selected, 'respond', delayed)
    assert send(client, cid, headers, role, 'Explain photosynthesis.', False).status_code == 201
    summary = next(r for r, _ in capture[0].events if r['event'] == 'turn_completed')
    assert summary['provider_generation'] == summary['provider_first_result_ms'] == 25


def test_tool_duration_with_fake_clock(test_context, monkeypatch, capture):
    _, provider, context, tools, _ = setup(test_context)
    clock = FakeClock(); monkeypatch.setattr(obs, 'clock', clock)
    original = provider.memory_provider.embed
    async def delayed(values): clock.advance(.007); return await original(values)
    monkeypatch.setattr(provider.memory_provider, 'embed', delayed)
    assert call(tools, context, MEMORY, {'query': 'jasmine'})['ok']
    record = next(r for r, _ in capture[0].events if r['event'] == 'tool_finished')
    assert record['duration_ms'] == 7


def test_streaming_never_emits_one_log_per_delta(capture):
    class Provider:
        async def stream(self, turns):
            for _ in range(100): yield 'PRIVATE_delta'
    async def run(): return [value async for value in usage.stream(Provider(), [])]
    assert len(asyncio.run(run())) == 100
    assert len(capture[1].events) == 1
    assert len(capture[0].events) == 1  # Only generation-stage completion.


@pytest.mark.parametrize('persona', [False, True])
def test_existing_text_provider_exposes_measured_usage_without_extra_calls(capture, persona):
    from app.config import get_settings
    from app.services.rya import OpenAIRyaProvider
    from app.services.legacy_persona import OpenAILegacyPersonaProvider
    provider = object.__new__(OpenAILegacyPersonaProvider if persona else OpenAIRyaProvider)
    provider.settings = get_settings()
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text='Same public answer', id='response-123',
                               usage=SimpleNamespace(input_tokens=20, output_tokens=4, input_tokens_details=None))
    provider.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    assert asyncio.run(usage.respond(provider, [])) == 'Same public answer'
    assert len(calls) == len(capture[1].events) == 1
    assert capture[1].events[0].usage.input_tokens == UsageValue(20, 'measured')
    assert capture[1].events[0].usage.output_tokens == UsageValue(4, 'measured')


def test_slow_usage_export_is_buffered_and_fail_open(monkeypatch, capture):
    entered, release = threading.Event(), threading.Event()
    def broken(event): entered.set(); release.wait(2); raise RuntimeError('PRIVATE database export')
    sink = usage.BufferedUsageSink(SimpleNamespace(record=broken), capacity=2)
    monkeypatch.setattr(usage, 'sink', sink)
    with usage.RequestUsage('text'): pass
    assert entered.wait(1)
    try:
        for _ in range(20):
            with usage.RequestUsage('text'): pass
        assert sink.dropped > 0
    finally:
        release.set(); sink.queue.join()
    assert 'PRIVATE' not in logs_json(capture[0])


def test_stt_usage_failure_does_not_change_transcript_or_guess_client_duration(test_context, monkeypatch, capture):
    client, _, provider, _, _, headers, _ = seed(test_context)
    provider.voice_provider.transcription = 'PRIVATE transcript'
    response = client.post('/api/v1/voice/transcribe', files={'audio': ('recording.webm', b'PRIVATE_AUDIO', 'audio/webm')},
                           data={'duration_ms': 2400}, headers=headers)
    assert response.status_code == 200 and response.json()['text'] == 'PRIVATE transcript'
    assert capture[1].events[-1].kind == 'stt'
    assert capture[1].events[-1].usage.duration_seconds.status == 'unavailable'
    assert 'PRIVATE' not in logs_json(capture[0])
    def fail(event): raise RuntimeError('PRIVATE sink error')
    monkeypatch.setattr(usage, 'sink', SimpleNamespace(record=fail))
    assert client.post('/api/v1/voice/transcribe', files={'audio': ('recording.webm', b'audio', 'audio/webm')},
                       headers=headers).status_code == 200


@pytest.mark.parametrize('terminal', ['response.completed', 'response.incomplete'])
def test_existing_stream_provider_reports_returned_terminal_usage(capture, terminal):
    from app.config import get_settings
    from app.services.rya import OpenAIRyaProvider
    provider = object.__new__(OpenAIRyaProvider); provider.settings = get_settings()
    class Events:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def __aiter__(self):
            async def values():
                yield SimpleNamespace(type='response.output_text.delta', delta='PRIVATE generated text')
                yield SimpleNamespace(type=terminal, response=SimpleNamespace(id='PRIVATE_response',
                    usage=SimpleNamespace(input_tokens=8, output_tokens=2)))
            return values()
    provider.client = SimpleNamespace(responses=SimpleNamespace(stream=lambda **kwargs: Events()))
    async def run(): return [value async for value in usage.stream(provider, [])]
    if terminal == 'response.incomplete':
        with pytest.raises(RyaProviderError): asyncio.run(run())
    else: assert asyncio.run(run()) == ['PRIVATE generated text']
    event = capture[1].events[-1]
    assert event.usage.output_tokens == UsageValue(2, 'measured')
    assert event.outcome == ('failed' if terminal == 'response.incomplete' else 'completed')
    assert 'PRIVATE' not in json.dumps(asdict(event))


def test_personality_invalidation_is_timed_without_changing_effects(test_context, capture):
    from tests.test_turn_lifecycle_l14 import configure_memory, CONTENT
    client, sessions, provider, cid, _, headers, _ = seed(test_context)
    configure_memory(provider)
    assert send(client, cid, headers, 'owner', CONTENT, False).status_code == 201
    summary = next(r for r, _ in capture[0].events if r['event'] == 'turn_completed')
    assert summary['personality_invalidation'] >= 0
    assert summary['memory_effect'] >= summary['personality_invalidation']
    assert CONTENT not in logs_json(capture[0])


def test_cancelled_tool_reports_safe_duration_without_losing_cancellation(test_context, monkeypatch, capture):
    _, provider, context, tools, _ = setup(test_context)
    async def cancelled(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(provider.memory_provider, 'embed', cancelled)
    with pytest.raises(asyncio.CancelledError): call(tools, context, MEMORY, {'query': 'jasmine'})
    tool = next(r for r, _ in capture[0].events if r['event'] == 'tool_finished')
    assert tool['dimensions']['outcome'] == 'interrupted' and tool['error_category'] == 'cancelled'
    assert capture[1].events[-1].outcome == 'interrupted'


def test_memory_provider_wrapping_is_idempotent(capture):
    from tests.conftest import FakeMemoryProvider
    provider = FakeMemoryProvider()
    wrapped = usage.wrap_memory(provider)
    assert usage.wrap_memory(wrapped) is wrapped
    asyncio.run(usage.wrap_memory(wrapped).embed(['query']))
    assert len(capture[1].events) == len(provider.embedding_calls) == 1


def test_default_logging_outputs_safe_structured_json(monkeypatch, caplog):
    sink = obs.BufferedLogSink()
    monkeypatch.setattr(obs, 'sink', sink)
    caplog.set_level(logging.INFO, logger=obs.logger.name)
    with obs.bound(obs.TurnObservation()):
        obs.emit('turn_completed', level=logging.INFO, dimensions={'mode': 'rya', 'raw_query': 'PRIVATE_secret'},
                 values={'preparation': 12, 'user_text': 'PRIVATE_secret'})
    sink.queue.join()
    record = next(r for r in caplog.records if r.name == obs.logger.name)
    assert json.loads(record.message) == record.telemetry
    assert 'PRIVATE_secret' not in caplog.text and 'raw_query' not in caplog.text


def test_durable_timing_excludes_later_stream_delivery_delay(monkeypatch, capture):
    clock = FakeClock(); monkeypatch.setattr(obs, 'clock', clock)
    observation = obs.TurnObservation()
    with obs.bound(observation):
        obs.accepted(1, 'rya', 'text')
        clock.advance(.010); obs.durable()
        with obs.stage('post_turn_effects'): clock.advance(.005)
        clock.advance(1)  # E.g. client backpressure after the done event exists.
        obs.summarize(observation)
    assert observation.timings['time_to_assistant_durable'] == pytest.approx(10)
    assert observation.timings['time_to_durable_completion'] == pytest.approx(15)


def distribution(values):
    ordered = sorted(values)
    return {'p50_ms': round(statistics.median(values), 4), 'p95_ms': round(ordered[int(.95 * (len(ordered)-1))], 4),
            'p99_ms': round(ordered[int(.99 * (len(ordered)-1))], 4)}


@pytest.mark.parametrize('scenario', ['owner', 'collaborator', 'visitor', 'fresh'])
def test_local_turn_latency_distributions(test_context, monkeypatch, capture, scenario):
    """100 paired local samples, no external AI; print with pytest -s -k distributions."""
    role = 'viewer' if scenario in {'visitor', 'fresh'} else scenario
    client, _, _, cid, _, headers, _ = seed(test_context, role)
    content = FRESH if scenario == 'fresh' else 'What flowers do you like?'
    durations, stages = {False: [], True: []}, {}
    for index in range(105):
        for enabled in [False, True]:
            monkeypatch.setattr(obs, 'enabled', enabled)
            capture[0].events.clear(); capture[1].events.clear()
            started = time.perf_counter()
            response = send(client, cid, headers, role, content, False)
            ended = time.perf_counter()
            assert response.status_code == 201
            if index >= 5:
                durations[enabled].append((ended-started)*1000)
                if enabled:
                    summary = next(r for r, _ in capture[0].events if r['event'] == 'turn_completed')
                    for key in ['preparation', 'classification', 'memory_retrieval', 'personality_selection',
                                'relationship_context', 'current_information', 'provider_generation',
                                'assistant_persistence', 'post_turn_effects', 'time_to_durable_completion']:
                        if key in summary: stages.setdefault(key, []).append(summary[key])
    baseline, measured = distribution(durations[False]), distribution(durations[True])
    # Coarse regression bound derives from this run's uninstrumented p95, not a
    # hard millisecond deadline. Call/SQL-count parity is the stronger gate.
    assert measured['p50_ms'] <= 2 * baseline['p95_ms']
    print('L14_E_BENCHMARK ' + json.dumps({'scenario': scenario, 'pairs': 100, 'disabled': baseline,
        'enabled': measured, 'median_overhead_ms': round(statistics.median([a-b for a,b in zip(durations[True],durations[False])]),4),
        'stages': {key: distribution(values) for key,values in stages.items()}}))


@pytest.mark.parametrize('name', [MEMORY, STYLE, RELATIONSHIP])
def test_local_tool_latency_distributions(test_context, monkeypatch, capture, name):
    _, _, context, tools, _ = setup(test_context)
    args = {'query': 'jasmine'} if name == MEMORY else {}
    durations = {False: [], True: []}
    for index in range(105):
        for enabled in [False, True]:
            monkeypatch.setattr(obs, 'enabled', enabled)
            capture[0].events.clear(); capture[1].events.clear()
            started = time.perf_counter()
            assert call(tools, context, name, args)['ok']
            elapsed = (time.perf_counter()-started)*1000
            if index >= 5: durations[enabled].append(elapsed)
    baseline, measured = distribution(durations[False]), distribution(durations[True])
    assert measured['p50_ms'] <= 2 * baseline['p95_ms']
    print('L14_E_BENCHMARK ' + json.dumps({'scenario': name, 'pairs': 100, 'disabled': baseline,
        'enabled': measured, 'median_overhead_ms': round(statistics.median([a-b for a,b in zip(durations[True],durations[False])]),4)}))
