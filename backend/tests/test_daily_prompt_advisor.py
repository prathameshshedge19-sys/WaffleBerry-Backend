from sqlalchemy import func, select

from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message, MessageRole
from app.models.memory import Memory
from app.models.progress import BuilderActivity, DailyPrompt
from app.services.memory import MemoryAnalysis, MemoryCandidate
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import active_legacy, generate_legacy_code, grant, headers


def _journey(client, auth, legacy_id):
    response = client.get(f"/api/v1/progress/{legacy_id}?timezone=Europe/Berlin", headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()


def _start(client, auth, legacy_id, prompt_id):
    return client.post(
        "/api/v1/conversations/from-daily-prompt",
        json={"legacy_id": legacy_id, "prompt_id": prompt_id},
        headers=headers(auth),
    )


def test_another_question_only_swaps_prompt_without_builder_activity(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="daily-skip-owner@example.com")
    original_chat = active_legacy(client, sessions, owner)
    first = _journey(client, owner, original_chat["legacy_id"])["daily_prompt"]

    with sessions() as db:
        conversation_count = db.scalar(select(func.count(Conversation.id)))

    response = client.post(
        f"/api/v1/progress/{original_chat['legacy_id']}/daily-prompt/{first['id']}/skip?timezone=Europe/Berlin",
        headers=headers(owner),
    )
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["id"] != first["id"] and second["prompt_text"] != first["prompt_text"]

    with sessions() as db:
        assert db.scalar(select(func.count(Conversation.id))) == conversation_count
        assert db.scalar(select(func.count(BuilderActivity.id))) == 0
        assert db.scalar(select(func.count(Memory.id))) == 0


def test_daily_question_is_idempotently_seeded_as_rya_then_answered_by_user(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="daily-start-owner@example.com")
    original_chat = active_legacy(client, sessions, owner)
    legacy_id = original_chat["legacy_id"]
    prompt = _journey(client, owner, legacy_id)["daily_prompt"]

    first = _start(client, owner, legacy_id, prompt["id"])
    second = _start(client, owner, legacy_id, prompt["id"])
    assert first.status_code == second.status_code == 201
    seeded = first.json()
    assert second.json()["conversation"]["id"] == seeded["conversation"]["id"]
    assert seeded["conversation"]["legacy_id"] == legacy_id
    assert seeded["rya_message"]["role"] == "assistant"
    assert seeded["rya_message"]["content"] == prompt["prompt_text"]

    with sessions() as db:
        conversation = db.get(Conversation, seeded["conversation"]["id"])
        messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)).all())
        assert conversation.source_daily_prompt_id == prompt["id"]
        assert [(message.role, message.content) for message in messages] == [(MessageRole.ASSISTANT, prompt["prompt_text"])]
        assert db.scalar(select(func.count(Memory.id))) == 0
        assert db.scalar(select(func.count(BuilderActivity.id))) == 0
        assert db.get(DailyPrompt, prompt["id"]).status == "pending"

    answer = "Pallavi was curious as a child and often dismantled toys to learn how they worked."
    provider.memory_provider.analyses[answer] = MemoryAnalysis(
        source_language="english",
        normalized_query=answer,
        memories=[MemoryCandidate(
            canonical_text="Pallavi was curious as a child and dismantled toys to understand them.",
            category="childhood",
            confidence=.98,
        )],
    )
    response = client.post(
        f"/api/v1/conversations/{seeded['conversation']['id']}/messages/stream?legacy_id={legacy_id}&timezone=Europe/Berlin",
        json={"content": answer},
        headers=headers(owner),
    )
    assert response.status_code == 200, response.text

    history = client.get(
        f"/api/v1/conversations/{seeded['conversation']['id']}/messages?legacy_id={legacy_id}",
        headers=headers(owner),
    ).json()
    assert [message["role"] for message in history] == ["assistant", "user", "assistant"]
    assert history[0]["content"] == prompt["prompt_text"] and history[1]["content"] == answer
    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == 1
        assert db.scalar(select(func.count(BuilderActivity.id))) == 1
        assert db.get(DailyPrompt, prompt["id"]).status == "answered"


def test_owner_and_collaborator_get_private_prompt_conversations_but_visitor_cannot_start(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="daily-role-owner@example.com")
    owner_chat = active_legacy(client, sessions, owner)
    legacy_id = owner_chat["legacy_id"]
    collaborator = register_user(client, codes, email="daily-role-collaborator@example.com")
    with sessions() as db:
        db.add(LegacyCollaborator(
            legacy_id=legacy_id,
            user_id=collaborator["user"]["id"],
            added_by_user_id=owner["user"]["id"],
        ))
        db.commit()

    prompt = _journey(client, collaborator, legacy_id)["daily_prompt"]
    owner_seed = _start(client, owner, legacy_id, prompt["id"])
    collaborator_seed = _start(client, collaborator, legacy_id, prompt["id"])
    assert owner_seed.status_code == collaborator_seed.status_code == 201
    assert owner_seed.json()["conversation"]["id"] != collaborator_seed.json()["conversation"]["id"]
    collaborator_conversations = client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=headers(collaborator)).json()
    assert [item["id"] for item in collaborator_conversations] == [collaborator_seed.json()["conversation"]["id"]]

    visitor = register_user(client, codes, email="daily-role-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    denied = _start(client, visitor, legacy_id, prompt["id"])
    assert denied.status_code == 404

