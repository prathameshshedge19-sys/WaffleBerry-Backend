from sqlalchemy import func, select

from app.models.conversation import Conversation, Message
from app.api.routes.conversations import derive_conversation_title
from app.services.rya import RYA_SYSTEM_PROMPT
from tests.conftest import register_user


def _headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_authenticated_chat_persists_both_messages_and_context(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes)
    conversation = client.post("/api/v1/conversations", json={"title": "A thoughtful chat"}, headers=_headers(auth))
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Who are you?"}, headers=_headers(auth))
    assert response.status_code == 201
    assert response.json()["rya_message"]["content"].startswith("I am Rya")
    assert [turn.role for turn in provider.calls[0] if turn.role != "system"] == ["user"]

    second = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Can you go deeper?"}, headers=_headers(auth))
    assert second.status_code == 201
    assert [turn.role for turn in provider.calls[1] if turn.role != "system"] == ["user", "assistant", "user"]

    messages = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(auth))
    assert [item["role"] for item in messages.json()] == ["user", "assistant", "user", "assistant"]
    with sessions() as db:
        assert db.scalar(select(func.count(Conversation.id))) == 1
        assert db.scalar(select(func.count(Message.id))) == 4


def test_conversation_listing_and_user_isolation(test_context):
    client, sessions, codes, _provider = test_context
    first = register_user(client, codes, email="first@example.com", name="First")
    created = client.post("/api/v1/conversations", json={}, headers=_headers(first))
    conversation_id = created.json()["id"]
    legacy_id = created.json()["legacy_id"]
    second = register_user(client, codes, email="second@example.com", name="Second")

    assert client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=_headers(second)).status_code == 404
    assert client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(second)).status_code == 404
    assert client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Not mine"}, headers=_headers(second)).status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count(Conversation.id))) == 1


def test_rya_identity_and_language_contract_are_explicit():
    assert "You are Rya" in RYA_SYSTEM_PROMPT
    assert "AI companion inside Legarya" in RYA_SYSTEM_PROMPT
    assert "Never identify yourself as ChatGPT" in RYA_SYSTEM_PROMPT
    assert "user's current conversational language" in RYA_SYSTEM_PROMPT
    assert "relevant long-term memory context" in RYA_SYSTEM_PROMPT


def test_provider_failure_rolls_back_user_message(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes)
    conversation_id = client.post("/api/v1/conversations", json={}, headers=_headers(auth)).json()["id"]

    async def fail(_messages):
        raise RuntimeError("provider down")

    provider.respond = fail
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Hello"}, headers=_headers(auth))
    assert response.status_code == 503
    with sessions() as db:
        assert db.scalar(select(func.count(Message.id))) == 0


def test_first_message_derives_concise_local_title(test_context):
    client, _sessions, codes, _provider = test_context
    auth = register_user(client, codes)
    conversation = client.post(
        "/api/v1/conversations", json={}, headers=_headers(auth)
    ).json()
    conversation_id = conversation["id"]
    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Tell me about German accusative."},
        headers=_headers(auth),
    )
    conversations = client.get(
        f"/api/v1/conversations?legacy_id={conversation['legacy_id']}", headers=_headers(auth)
    ).json()
    assert conversations[0]["title"] == "German accusative"
    assert derive_conversation_title("I'm confused about changing my job.") == "Changing careers"


def test_delete_conversation_requires_ownership_and_cascades(test_context):
    client, sessions, codes, _provider = test_context
    first = register_user(client, codes, email="delete-owner@example.com", name="Owner")
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=_headers(first)
    ).json()["id"]
    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "A temporary thread"},
        headers=_headers(first),
    )
    second = register_user(client, codes, email="delete-other@example.com", name="Other")
    assert client.delete(
        f"/api/v1/conversations/{conversation_id}", headers=_headers(second)
    ).status_code == 404
    assert client.delete(
        f"/api/v1/conversations/{conversation_id}", headers=_headers(first)
    ).status_code == 204
    with sessions() as db:
        assert db.scalar(select(func.count(Conversation.id)).where(Conversation.id == conversation_id)) == 0
        assert db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation_id)) == 0


def test_rename_conversation_is_trimmed_validated_and_owner_scoped(test_context):
    client, _sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="rename-owner@example.com", name="Owner")
    conversation = client.post(
        "/api/v1/conversations", json={}, headers=_headers(owner)
    ).json()
    conversation_id = conversation["id"]
    other = register_user(client, codes, email="rename-other@example.com", name="Other")

    forbidden = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "Not mine"},
        headers=_headers(other),
    )
    assert forbidden.status_code == 404

    blank = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "   "},
        headers=_headers(owner),
    )
    assert blank.status_code == 422

    renamed = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "  A meaningful memory  "},
        headers=_headers(owner),
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "A meaningful memory"
    deleted = client.delete(
        f"/api/v1/conversations/{conversation_id}", headers=_headers(owner)
    )
    assert deleted.status_code == 204
    assert client.get(
        f"/api/v1/conversations?legacy_id={conversation['legacy_id']}", headers=_headers(owner)
    ).json() == []


def test_multiple_conversations_keep_distinct_histories_and_newest_first(test_context):
    client, _sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="workspace@example.com", name="Workspace")

    chat_a = client.post(
        "/api/v1/conversations", json={}, headers=_headers(auth)
    ).json()
    client.post(
        f"/api/v1/conversations/{chat_a['id']}/messages",
        json={"content": "Help me plan my week."},
        headers=_headers(auth),
    )
    chat_b = client.post(
        "/api/v1/conversations", json={}, headers=_headers(auth)
    ).json()
    client.post(
        f"/api/v1/conversations/{chat_b['id']}/messages",
        json={"content": "Tell me about German accusative."},
        headers=_headers(auth),
    )

    listed = client.get(
        f"/api/v1/conversations?legacy_id={chat_a['legacy_id']}", headers=_headers(auth)
    ).json()
    assert [item["id"] for item in listed] == [chat_b["id"], chat_a["id"]]
    history_a = client.get(
        f"/api/v1/conversations/{chat_a['id']}/messages", headers=_headers(auth)
    ).json()
    history_b = client.get(
        f"/api/v1/conversations/{chat_b['id']}/messages", headers=_headers(auth)
    ).json()
    assert history_a[0]["content"] == "Help me plan my week."
    assert history_b[0]["content"] == "Tell me about German accusative."


def test_stream_endpoint_emits_ordered_deltas_and_persists_one_assistant(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="stream@example.com", name="Stream")
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=_headers(auth)
    ).json()["id"]

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "Stream a response"},
        headers=_headers(auth),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.index('"delta": "I am "') < response.text.index('"delta": "Rya. "')
    assert "event: start" in response.text
    assert "event: done" in response.text
    assert "event: error" not in response.text
    assert [turn.role for turn in provider.calls[0] if turn.role != "system"] == ["user"]
    with sessions() as db:
        stored = db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).all()
        assert [message.role.value for message in stored] == ["user", "assistant"]
        assert stored[1].content == "I am Rya. I can help you think this through."


def test_stream_context_authentication_and_ownership(test_context):
    client, _sessions, codes, provider = test_context
    owner = register_user(client, codes, email="stream-owner@example.com", name="Owner")
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=_headers(owner)
    ).json()["id"]

    assert client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "No token"},
    ).status_code == 401

    other = register_user(client, codes, email="stream-other@example.com", name="Other")
    assert client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "Not mine"},
        headers=_headers(other),
    ).status_code == 404

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "First turn"},
        headers=_headers(owner),
    )
    second = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "Second turn"},
        headers=_headers(owner),
    )
    assert first.status_code == second.status_code == 200
    assert [turn.role for turn in provider.calls[1] if turn.role != "system"] == ["user", "assistant", "user"]


def test_interrupted_stream_keeps_user_message_without_false_assistant(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="stream-failure@example.com", name="Failure")
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=_headers(auth)
    ).json()["id"]
    provider.stream_error_after = 1

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "Please begin"},
        headers=_headers(auth),
    )

    assert response.status_code == 200
    assert "event: delta" in response.text
    assert "event: error" in response.text
    assert "event: done" not in response.text
    with sessions() as db:
        stored = db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).all()
        assert [message.role.value for message in stored] == ["user"]
