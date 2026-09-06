"""Cadence inspects the newest two assistant replies in either explicit order."""

import pytest

from app.services import persona_turns
from app.models.legacy import Legacy
from app.services.personality_style import normalize_personality_history, select_personality_style
from app.services.rya import ChatTurn
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import create_visitor_chat, generate_legacy_code, grant, persona_stream
from tests.test_personality_style_l13 import prepared


CASES = [
    pytest.param([], True, id="empty"),
    pytest.param(["A normal reply"], True, id="one-unused"),
    pytest.param(["Oh my, a surprise"], False, id="one-used"),
    pytest.param(["Old reply", "Previous reply", "Oh my, a surprise"], False, id="newest-used"),
    pytest.param(["Old reply", "Oh my, a surprise", "Newest reply"], False, id="previous-used"),
    pytest.param(["Oh my, a surprise", "Previous reply", "Newest reply"], True, id="third-only"),
    pytest.param(["Oh my, a surprise", "Third reply", "Previous reply", "Newest reply"], True, id="fourth-only"),
    pytest.param(["Old reply", "Oh my, previous", "Oh my, newest"], False, id="both-recent-used"),
]


@pytest.mark.parametrize("history_order", ["newest_first", "chronological"])
@pytest.mark.parametrize("replies,eligible", CASES)
def test_cadence_uses_two_most_recent_assistants_without_mutation(test_context, history_order, replies, eligible):
    _, sessions, _, _, _, legacy_id, _ = prepared(test_context, [('Pallavi often said "Oh my" when surprised.', "habit")])
    chronological = []
    for reply in replies:
        # A visitor using the expression must never count as assistant reuse.
        chronological.extend([ChatTurn(role="user", content="Oh my"), ChatTurn(role="assistant", content=reply)])
    history = list(reversed(chronological)) if history_order == "newest_first" else list(chronological)
    before = list(history)
    normalized = normalize_personality_history(history, history_order=history_order)
    assert normalized == tuple(chronological)
    assert history == before
    assert all(original is unchanged for original, unchanged in zip(history, before))
    with sessions() as db:
        selected = select_personality_style(
            db, db.get(Legacy, legacy_id), "What an unexpected surprise!", (),
            {"current_turn_language": "english"}, history, history_order=history_order,
        )
    assert selected is not None
    assert (selected.expression == "Oh my") is eligible
    assert history == before


@pytest.mark.parametrize("used_position", [0, 1, 2, 3], ids=["newest", "previous", "third", "fourth"])
def test_routed_newest_first_history_checks_newest_and_previous(test_context, monkeypatch, used_position):
    client, sessions, codes, provider, owner, legacy_id, _ = prepared(test_context, [('Pallavi often said "Oh my" when surprised.', "habit")])
    visitor = register_user(client, codes, email="cadence-route-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    chat = create_visitor_chat(client, visitor, legacy_id)
    # Build real persisted history through the normal visitor stream endpoint.
    for index in range(4):
        question = f"History seed {index}"
        provider.persona_provider.responses[question] = "Oh my, what a surprise." if index == 3 - used_position else f"Ordinary response {index}."
        response = persona_stream(client, visitor, chat, question)
        assert "event: error" not in response.text

    original_selector = persona_turns.select_personality_style
    captures = []

    def capture(db, legacy, question, memories, identity, recent, **kwargs):
        before = tuple(recent)
        assert kwargs["history_order"] == "newest_first"
        raw_assistants = [row.id for row in before if row.role == "assistant"]
        assert len(raw_assistants) == 4
        assert raw_assistants == sorted(raw_assistants, reverse=True)
        normalized = normalize_personality_history(recent, history_order=kwargs["history_order"])
        inspected = [row.id for row in normalized if row.role == "assistant"][-2:]
        assert inspected == list(reversed(raw_assistants[:2]))
        selected = original_selector(db, legacy, question, memories, identity, recent, **kwargs)
        assert tuple(recent) == before
        captures.append(selected)
        return selected

    monkeypatch.setattr(persona_turns, "select_personality_style", capture)
    response = persona_stream(client, visitor, chat, "What an unexpected surprise!")
    assert "event: error" not in response.text
    assert len(captures) == 1 and captures[0] is not None
    assert (captures[0].expression == "Oh my") is (used_position >= 2)
    prompt = provider.persona_provider.calls[-1][0].content
    assert ('"optional_original_expression":"Oh my"' in prompt) is (used_position >= 2)


def test_user_only_history_does_not_suppress_expression(test_context):
    _, sessions, _, _, _, legacy_id, _ = prepared(test_context, [('Pallavi often said "Oh my" when surprised.', "habit")])
    with sessions() as db:
        selected = select_personality_style(db, db.get(Legacy, legacy_id), "What an unexpected surprise!", (),
            {"current_turn_language": "english"}, [ChatTurn(role="user", content="Oh my")], history_order="newest_first")
    assert selected.expression == "Oh my"


def test_unknown_order_is_rejected_without_mutating_history():
    history = [ChatTurn(role="assistant", content="Hello")]
    before = list(history)
    with pytest.raises(ValueError, match="Unsupported personality history order"):
        normalize_personality_history(history, history_order="guess")
    assert history == before
