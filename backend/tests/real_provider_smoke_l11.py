"""Disposable L11 real-provider review. Run manually; never collected by pytest."""

import asyncio
import json
from types import SimpleNamespace

from app.models.legacy import Legacy
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import OpenAILegacyPersonaProvider, nickname_cadence_guard, persona_system_context
from app.services.rya import ChatTurn
from app.services.visitor_identity import greeting
from app.services.visitor_identity import current_language


def memory(memory_id: int, text: str, category: str):
    return SimpleNamespace(id=memory_id, canonical_text=text, category=category, confidence=.99, story_key=None, entity_links=[])


async def answer(provider, legacy, memories, visitor, history, question):
    route = analyze_legacy_query(question, legacy.subject_name, memories)
    selected = memories if route.needs_memory else ()
    turn_visitor = {**visitor, "current_turn_language": current_language(question)}
    system = persona_system_context(legacy, selected, route, memories, turn_visitor)
    turns = [ChatTurn(role="system", content=system), *history, ChatTurn(role="user", content=question)]
    guard = nickname_cadence_guard(turn_visitor, turns)
    if guard:
        turns.append(ChatTurn(role="system", content=guard))
    response = await provider.respond(turns)
    history.extend((ChatTurn(role="user", content=question), ChatTurn(role="assistant", content=response)))
    return response


async def main():
    provider = OpenAILegacyPersonaProvider()
    legacy = Legacy(id=999999, owner_user_id=999999, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
    memories = (
        memory(1, "Prathamesh is Pallavi's son.", "relationship"),
        memory(2, 'Pallavi calls Prathamesh "Babu".', "habit"),
        memory(3, "Pallavi is affectionate but practical.", "personality"),
        memory(4, "Pallavi often speaks directly and briefly.", "habit"),
        memory(5, "Pallavi used to make tea for Prathamesh in the evening.", "story"),
    )
    verified = {
        "identified": True, "preferred_name": "Prathamesh", "claimed_relationship": "son",
        "relationship_status": "verified_from_memory", "supported_relationships": ["son"],
        "claim_conflicts_with_memory": False, "visitor_specific_nicknames": ["Babu"],
        "visitor_specific_evidence": [{"id": item.id, "text": item.canonical_text} for item in memories[:2]],
    }
    unknown = {
        "identified": True, "preferred_name": "Rahul", "claimed_relationship": "neighbor",
        "relationship_status": "unverified", "supported_relationships": [], "claim_conflicts_with_memory": False,
        "visitor_specific_nicknames": [], "visitor_specific_evidence": [],
    }
    conflict = {
        **verified, "claimed_relationship": "brother", "relationship_status": "unverified",
        "claim_conflicts_with_memory": True, "visitor_specific_nicknames": [],
        "visitor_specific_evidence": [{"id": memories[0].id, "text": memories[0].canonical_text}],
    }
    results = {
        "first_time_greeting": greeting("Pallavi", None),
        "returning_greeting": greeting("Pallavi", SimpleNamespace(preferred_name="Prathamesh", claimed_relationship="son")),
    }
    history = []
    questions = (
        "I'm Prathamesh, your son.",
        "Do you remember our evenings?",
        "Do you remember our Kashmir trip?",
        "What is a mango?",
        "Are you ChatGPT?",
    )
    results["ten_turn_conversation"] = []
    for question in questions:
        response = await answer(provider, legacy, memories, verified, history, question)
        results["ten_turn_conversation"].append({"visitor": question, "legacy": response})
    results["unverified_neighbor"] = await answer(provider, legacy, memories, unknown, [], "I'm Rahul, your neighbor.")
    results["conflicting_brother"] = await answer(provider, legacy, memories, conflict, [], "I'm Prathamesh, your brother.")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
