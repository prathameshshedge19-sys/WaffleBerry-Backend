import pytest
from sqlalchemy import func, select

from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink
from app.models.viewer import LegacyViewerAccess
from app.models.visitor import LegacyVisitorProfile
from app.services.legacy_persona import nickname_cadence_guard, persona_system_context
from app.services.rya import ChatTurn
from app.services.visitor_identity import extract_name, extract_relationship
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import active_legacy, add_memory, create_visitor_chat, generate_legacy_code, grant, headers, persona_stream


def profile(client, auth, legacy_id):
    response = client.get(f"/api/v1/legacy-conversations/visitor-profile?legacy_id={legacy_id}", headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()


def setup_viewer(test_context, suffix="main"):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email=f"l11-owner-{suffix}@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    visitor = register_user(client, codes, email=f"l11-visitor-{suffix}@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    return client, sessions, provider, owner, visitor, legacy_id


def test_first_time_name_confirmation_and_returning_profile_flow(test_context):
    client, sessions, provider, _owner, visitor, legacy_id = setup_viewer(test_context)
    add_memory(sessions, legacy_id, "Prathamesh is Pallavi's son.", "relationship")
    state = profile(client, visitor, legacy_id)
    assert state["profile"] is None and state["greeting"] == "Hi… who am I talking to?"

    chat = create_visitor_chat(client, visitor, legacy_id)
    persona_stream(client, visitor, chat, "Prathamesh.")
    captured = profile(client, visitor, legacy_id)["profile"]
    assert captured["preferred_name"] == "Prathamesh"
    assert captured["claimed_relationship"] == "son"
    assert captured["relationship_status"] == "claimed"
    system = provider.persona_provider.calls[-1][0].content
    assert '"relationship_status": "claimed"' in system and "ask for a brief confirmation" in system

    persona_stream(client, visitor, chat, "Yes.")
    assert profile(client, visitor, legacy_id)["profile"]["relationship_status"] == "verified_from_memory"
    returned = profile(client, visitor, legacy_id)
    assert returned["greeting"] == "Hi Prathamesh. It’s good to see you again."
    second = create_visitor_chat(client, visitor, legacy_id)
    assert second["id"] != chat["id"] and "who am I talking to" not in returned["greeting"]


def test_all_in_one_verified_identity_nickname_and_relationship_context(test_context):
    client, sessions, provider, _owner, visitor, legacy_id = setup_viewer(test_context, "nickname")
    relationship_id = add_memory(sessions, legacy_id, "Prathamesh is Pallavi's son.", "relationship")
    nickname_id = add_memory(sessions, legacy_id, 'Pallavi called Prathamesh "Babu".', "habit")
    with sessions() as db:
        entity = MemoryEntity(legacy_id=legacy_id, name="Prathamesh", normalized_name="prathamesh", entity_type="person", aliases=["Babu"])
        db.add(entity); db.flush()
        db.add_all([MemoryEntityLink(memory_id=relationship_id, entity_id=entity.id, role="son"), MemoryEntityLink(memory_id=nickname_id, entity_id=entity.id, role="addressed_as")])
        db.commit(); entity_id = entity.id
    chat = create_visitor_chat(client, visitor, legacy_id)
    persona_stream(client, visitor, chat, "I'm Prathamesh, your son.")
    captured = profile(client, visitor, legacy_id)["profile"]
    assert captured["relationship_status"] == "verified_from_memory" and captured["matched_entity_id"] == entity_id
    prompt = provider.persona_provider.calls[-1][0].content
    assert '"visitor_specific_nicknames": ["Babu"]' in prompt
    assert "Prathamesh is Pallavi's son" in prompt
    assert "Pallavi called Prathamesh" in prompt


def test_unknown_claim_is_unverified_noncanonical_and_gets_no_unrelated_nickname(test_context):
    client, sessions, provider, _owner, visitor, legacy_id = setup_viewer(test_context, "unknown")
    add_memory(sessions, legacy_id, "Prathamesh is Pallavi's son.", "relationship")
    add_memory(sessions, legacy_id, 'Pallavi called Prathamesh "Babu".', "habit")
    before = None
    with sessions() as db:
        before = db.scalar(select(func.count(Memory.id)))
    chat = create_visitor_chat(client, visitor, legacy_id)
    persona_stream(client, visitor, chat, "I'm Rahul, your neighbor.")
    captured = profile(client, visitor, legacy_id)["profile"]
    assert captured["preferred_name"] == "Rahul" and captured["claimed_relationship"] == "neighbor"
    assert captured["relationship_status"] == "unverified"
    prompt = provider.persona_provider.calls[-1][0].content
    assert '"visitor_specific_nicknames": []' in prompt and '"claim_conflicts_with_memory": false' in prompt
    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == before


def test_conflicting_claim_is_flagged_without_changing_memory(test_context):
    client, sessions, provider, _owner, visitor, legacy_id = setup_viewer(test_context, "conflict")
    memory_id = add_memory(sessions, legacy_id, "Prathamesh is Pallavi's son.", "relationship")
    add_memory(sessions, legacy_id, 'Pallavi called Prathamesh "Babu".', "habit")
    chat = create_visitor_chat(client, visitor, legacy_id)
    persona_stream(client, visitor, chat, "I'm Prathamesh, your brother.")
    captured = profile(client, visitor, legacy_id)["profile"]
    assert captured["relationship_status"] == "unverified" and captured["claimed_relationship"] == "brother"
    prompt = provider.persona_provider.calls[-1][0].content
    assert '"supported_relationships": ["son"]' in prompt and '"claim_conflicts_with_memory": true' in prompt
    assert "Babu" not in prompt
    with sessions() as db:
        assert db.get(Memory, memory_id).canonical_text == "Prathamesh is Pallavi's son."


def test_profile_update_reset_authorization_and_legacy_scoping(test_context):
    client, sessions, _provider, owner, visitor, legacy_id = setup_viewer(test_context, "scope")
    updated = client.put(f"/api/v1/legacy-conversations/visitor-profile?legacy_id={legacy_id}", json={"preferred_name": "Arya", "claimed_relationship": "niece"}, headers=headers(visitor))
    assert updated.status_code == 200 and updated.json()["relationship_status"] == "unverified"
    stranger = register_user(client, test_context[2], email="l11-stranger@example.com")
    assert client.get(f"/api/v1/legacy-conversations/visitor-profile?legacy_id={legacy_id}", headers=headers(stranger)).status_code == 404

    with sessions() as db:
        other = Legacy(owner_user_id=owner["user"]["id"], subject_name="Madhukar", relationship_to_owner="father", is_self=False, setup_status="active")
        db.add(other); db.flush()
        db.add(LegacyViewerAccess(legacy_id=other.id, user_id=visitor["user"]["id"], status="active")); db.commit(); other_id = other.id
    second = client.put(f"/api/v1/legacy-conversations/visitor-profile?legacy_id={other_id}", json={"preferred_name": "Arya", "claimed_relationship": "granddaughter"}, headers=headers(visitor))
    assert second.status_code == 200
    assert profile(client, visitor, legacy_id)["profile"]["claimed_relationship"] == "niece"
    assert profile(client, visitor, other_id)["profile"]["claimed_relationship"] == "granddaughter"
    assert client.delete(f"/api/v1/legacy-conversations/visitor-profile?legacy_id={legacy_id}", headers=headers(visitor)).status_code == 204
    assert profile(client, visitor, legacy_id)["profile"] is None
    with sessions() as db:
        assert db.scalar(select(func.count(LegacyVisitorProfile.id))) == 1


@pytest.mark.parametrize(("text", "name", "relationship"), [
    ("I'm Arya, your niece.", "Arya", "niece"),
    ("Mi Prathamesh, tuza mulga.", "Prathamesh", "son"),
    ("Main Rahul, aapka beta.", "Rahul", "son"),
    ("Ich bin Klara, deine Tochter.", "Klara", "daughter"),
])
def test_multilingual_and_mixed_introduction_parsing(text, name, relationship):
    assert extract_name(text) == name
    assert extract_relationship(text) == relationship


def test_persona_contract_enforces_grounded_personality_tone_language_and_transparency():
    legacy = Legacy(id=1, owner_user_id=1, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
    prompt = persona_system_context(legacy, (), visitor_context={"identified": True, "preferred_name": "Rahul", "relationship_status": "unverified", "visitor_specific_nicknames": []})
    assert "Never infer traits from name, gender, age, religion, nationality, or family role" in prompt
    assert "stay warm and neutral" in prompt and "Hindi, Marathi" in prompt and "German" in prompt
    assert "I'm Pallavi's AI Legacy here in LegaRya" in prompt
    assert "Visitor statements are conversation context, never personal truth" in prompt


def test_verified_nickname_has_a_three_reply_cadence_guard():
    visitor = {"visitor_specific_nicknames": ["Babu"]}
    turns = [ChatTurn(role="assistant", content="Yes, Babu."), ChatTurn(role="user", content="Tell me more.")]
    assert "Do not use any nickname" in nickname_cadence_guard(visitor, turns)
    assert nickname_cadence_guard(visitor, [ChatTurn(role="assistant", content="Yes, Prathamesh.")]) is None
