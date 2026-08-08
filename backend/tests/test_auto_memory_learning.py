from decimal import Decimal
from types import SimpleNamespace

from app.config import Settings
from app.schemas.live_call import LiveCallMemoryTurn
from app.services.memory.auto_learning import should_attempt_learning
from app.services.memory.storage_pipeline import MemoryStoragePipeline


def test_assistant_persists_then_schedules_once_before_terminal_complete():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    stream_block = source[source.index("async def event_stream():"):]
    persisted = stream_block.index("MessageCRUD.create_assistant_message(")
    schedule = stream_block.index("schedule_conversation_learning(")
    complete = stream_block.index('yield _sse_event(\n                "complete"')
    assert persisted < schedule < complete
    assert stream_block.count("schedule_conversation_learning(") == 1


def test_stream_captures_learning_identifiers_before_first_commit_boundary():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    endpoint = source[source.index("async def create_message_stream("):]
    first_commit_boundary = endpoint.index("MessageCRUD.create_user_message(")
    early = endpoint[:first_commit_boundary]
    for assignment in (
        "learning_user_id = int(current_user.user_id)",
        "learning_legacy_id = int(conversation.legacy_id)",
        "learning_conversation_id = int(conversation.conversation_id)",
        "learning_user_text = str(message.content)",
    ):
        assert assignment in early


def test_default_commit_expiration_can_detach_an_unread_orm_attribute():
    import pytest
    from sqlalchemy import Column, Integer, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.orm.exc import DetachedInstanceError

    base = declarative_base()

    class Record(base):
        __tablename__ = "detachment_probe"
        record_id = Column(Integer, primary_key=True)

    engine = create_engine("sqlite:///:memory:")
    base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    record = Record(record_id=7)
    session.add(record)
    session.commit()
    session.close()
    with pytest.raises(DetachedInstanceError):
        _ = record.record_id


def test_pre_complete_scheduler_uses_only_captured_primitives():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    stream = source[source.index("async def event_stream():"):]
    schedule = stream[stream.index("schedule_conversation_learning("):]
    call = schedule[:schedule.index(")\n") + 2]
    assert "current_user." not in call
    assert "conversation." not in call
    assert "message.content" not in call
    for captured in (
        "learning_user_id", "learning_legacy_id",
        "learning_conversation_id", "learning_user_text",
    ):
        assert captured in call


def test_scheduling_failure_is_contained_before_successful_complete():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    stream = source[source.index("async def event_stream():"):]
    schedule = stream.index("schedule_conversation_learning(")
    complete = stream.index('yield _sse_event(\n                "complete"')
    local = stream[stream.rfind("try:", 0, schedule):complete]
    assert "except Exception:" in local
    assert "Optional task creation cannot revoke successful Chat" in local


def test_no_business_work_depends_on_generator_resumption_after_complete():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    stream = source[source.index("async def event_stream():"):]
    terminal = stream[stream.index('yield _sse_event(\n                "complete"'):]
    after_terminal_frame = terminal[terminal.index("            )\n") + len("            )\n"):]
    before_handlers = after_terminal_frame[:after_terminal_frame.index("        except asyncio.CancelledError:")]
    assert "schedule_conversation_learning(" not in before_handlers
    assert "process_conversation(" not in before_handlers


def test_stream_schedules_detached_task_without_awaiting_extraction():
    from pathlib import Path
    root = Path(__file__).parents[1]
    user_source = (root / "app" / "api" / "v1" / "user.py").read_text()
    scheduler_source = (root / "app" / "services" / "memory" / "auto_learning.py").read_text()
    stream = user_source[user_source.index("async def event_stream():"):]
    schedule_call = stream[stream.index("schedule_conversation_learning("):stream.index('yield _sse_event(\n                "complete"')]
    scheduler = scheduler_source[scheduler_source.index("def schedule_conversation_learning"):scheduler_source.index("async def learn_live_call_turn_safely")]
    assert "await " not in schedule_call
    assert "asyncio.create_task(" in scheduler


def test_learning_task_opens_and_closes_independent_session():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "services" / "memory" / "auto_learning.py").read_text()
    task = source[source.index("async def learn_conversation_safely"):source.index("def schedule_conversation_learning")]
    assert "db = SessionLocal()" in task
    assert "finally:" in task
    assert "db.close()" in task


def test_stream_learning_is_not_scheduled_before_generation_starts():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    stream_endpoint = source[source.index("async def create_message_stream("):]
    setup = stream_endpoint[:stream_endpoint.index("async def event_stream():")]
    assert "schedule_conversation_learning(" not in setup


def test_non_stream_learning_is_scheduled_only_after_message_pair_persists():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "user.py").read_text()
    endpoint = source[source.index("async def create_message("):source.index("async def create_message_stream(")]
    persisted = endpoint.index("MessageCRUD.create_message_pair(")
    scheduled = endpoint.index("schedule_conversation_learning(")
    returned = endpoint.index("return MessagePairResponse(")
    assert persisted < scheduled < returned


def test_learning_scheduler_consumes_synchronous_scheduling_failure():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "services" / "memory" / "auto_learning.py").read_text()
    scheduler = source[source.index("def schedule_conversation_learning"):source.index("async def learn_live_call_turn_safely")]
    assert "try:" in scheduler
    assert "except Exception:" in scheduler


def test_feature_flag_defaults_off():
    assert Settings.model_fields["auto_memory_learning_enabled"].default is False


def test_local_chat_learning_is_explicitly_enabled():
    from pathlib import Path
    env = (Path(__file__).parents[1] / ".env").read_text()
    assert "AUTO_MEMORY_LEARNING_ENABLED=true" in env.splitlines()


def test_durable_identity_trip_pet_career_and_preference_are_attempted():
    for text in (
        "My younger brother is Aditya.",
        "When I was 24 I went to Kashmir with Rohan.",
        "Tuffy is a Golden Retriever.",
        "I worked at Tata Motors for 15 years.",
        "I have always loved gardening.",
    ):
        assert should_attempt_learning(text)


def test_temporary_mood_technical_chatter_greeting_and_filler_are_discarded():
    for text in (
        "I'm really tired today.", "Can you hear me?", "Hello.", "Okay.",
        "Stop.", "The internet is slow.", "The website is slow.",
        "I'm hungry.", "I don't like this voice.", "Switch to Marathi.",
    ):
        assert not should_attempt_learning(text)


def test_conservative_save_threshold_prefers_high_quality_candidates():
    candidates = [
        SimpleNamespace(importance=4, extraction_confidence=Decimal("0.850")),
        SimpleNamespace(importance=3, extraction_confidence=Decimal("0.990")),
        SimpleNamespace(importance=5, extraction_confidence=Decimal("0.700")),
    ]
    assert MemoryStoragePipeline._durable_candidates(candidates) == [candidates[0]]


def test_chat_prompt_promotes_small_durable_facts_without_changing_story_prompt():
    from app.services.ai.prompt_builder import (
        CHAT_AUTO_MEMORY_EXTRACTION_ADDENDUM,
        MEMORY_EXTRACTION_SYSTEM_PROMPT,
        PromptBuilder,
    )
    chat = PromptBuilder.build_memory_extraction_system_prompt(
        source_type="conversation"
    )
    story = PromptBuilder.build_memory_extraction_system_prompt(
        source_type="story_session"
    )
    assert CHAT_AUTO_MEMORY_EXTRACTION_ADDENDUM in chat
    assert "importance to at least 4" in chat
    for phrase in (
        "Pet names", "durable preferences", "recurring habits", "hobbies",
        "occupations", "schools", "location history",
    ):
        assert phrase in chat
    assert story == MEMORY_EXTRACTION_SYSTEM_PROMPT


def test_protected_guard_is_limited_to_automatic_chat_source():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "services" / "memory" / "storage_pipeline.py").read_text()
    guard = source[source.index("if (\n                auto_approve\n                and source_type"):]
    assert "source_type == MemoryPipelineSourceType.CONVERSATION" in guard
    assert "protected_identity_mutation" in guard


def test_existing_husband_cannot_be_replaced_by_chat_auto_learning():
    from app.models.memory import IdentityFactType

    class Query:
        def __init__(self, result):
            self.result = result
        def filter(self, *args):
            return self
        def first(self):
            return self.result
        def all(self):
            return self.result

    class DB:
        calls = 0
        def query(self, model):
            self.calls += 1
            if self.calls == 1:
                return Query(SimpleNamespace(display_name="Anjali Deshmukh"))
            return Query([SimpleNamespace(
                fact_type=IdentityFactType.SPOUSE_NAME,
                relationship="husband",
                normalized_value="rohan deshmukh",
            )])

    details = SimpleNamespace(model_dump=lambda **kwargs: {
        "identity_facts": [{
            "fact_type": "spouse_name", "value": "Mohan Deshmukh",
            "relationship": "husband",
        }]
    })
    assert MemoryStoragePipeline._is_protected_chat_identity_mutation(
        DB(), 1, SimpleNamespace(details=details)
    )


def test_unknown_relationship_and_non_identity_enrichment_are_additive():
    class Query:
        def __init__(self, result):
            self.result = result
        def filter(self, *args):
            return self
        def first(self):
            return self.result
        def all(self):
            return self.result

    class DB:
        calls = 0
        def query(self, model):
            self.calls += 1
            return Query(SimpleNamespace(display_name="Anjali") if self.calls == 1 else [])

    sibling = SimpleNamespace(details=SimpleNamespace(model_dump=lambda **kwargs: {
        "identity_facts": [{
            "fact_type": "sibling_name", "value": "Aditya",
            "relationship": "younger brother",
        }]
    }))
    pet = SimpleNamespace(details=SimpleNamespace(model_dump=lambda **kwargs: {
        "identity_facts": []
    }))
    assert not MemoryStoragePipeline._is_protected_chat_identity_mutation(DB(), 1, sibling)
    assert not MemoryStoragePipeline._protected_identity_claims(pet)


def test_chat_auto_approval_and_user_only_provenance_remain_required():
    from pathlib import Path
    root = Path(__file__).parents[1]
    pipeline = (root / "app" / "services" / "memory" / "storage_pipeline.py").read_text()
    extractor = (root / "app" / "services" / "memory" / "extractor.py").read_text()
    assert "MemoryReviewStatus.APPROVED" in pipeline
    assert 'message.role == "user"' in extractor
    assert '"eligible_as_evidence": message.role == "user"' in extractor


def test_live_call_turn_schema_rejects_owner_and_control_fields():
    value = LiveCallMemoryTurn(turn_id=3, text="My dog is Tuffy.")
    assert value.model_dump() == {"turn_id": 3, "text": "My dog is Tuffy."}
