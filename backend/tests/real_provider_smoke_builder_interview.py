"""Disposable real-provider quality smoke for progressive builder interviewing."""
import asyncio
import json
import re
from types import SimpleNamespace

from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryEntityCandidate, progressive_interviewing
from app.services.rya import ChatTurn, OpenAIRyaProvider


def analysis(text, category, *, person=None, role="mentioned", story_key=None):
    entities = [] if not person else [MemoryEntityCandidate(name=person, entity_type="person", role=role)]
    return MemoryAnalysis(source_language="english", normalized_query=text,
        memories=[MemoryCandidate(canonical_text=text, category=category, confidence=.98, entities=entities, story_key=story_key)])


def memory(text, category, *, person=None, role="mentioned", story_key=None):
    links = [] if not person else [SimpleNamespace(role=role, entity=SimpleNamespace(name=person))]
    return SimpleNamespace(canonical_text=text, category=category, entity_links=links, story_key=story_key)


def question_from(answer):
    matches = re.findall(r"[^.!?]*\?", answer)
    return matches[-1].strip() if matches else None


async def run_sequence(provider, turns, active, cases):
    results = []
    for user_text, item, stored in cases:
        context = [*turns, SimpleNamespace(role="user", content=user_text)]
        planner = progressive_interviewing(item, active, context, subject_name="Pallavi", contributor_role="owner")
        request = [ChatTurn(role="system", content="Pallavi's Legacy is active and fully set up. Never re-onboard. The speaker is Prathamesh, her son and the owner-builder."),
                   ChatTurn(role="system", content=planner)]
        request.extend(ChatTurn(role=turn.role, content=turn.content) for turn in turns[-12:])
        request.append(ChatTurn(role="user", content=user_text))
        answer = await provider.respond(request)
        results.append({"user": user_text, "rya": answer, "followup": question_from(answer)})
        turns.extend((SimpleNamespace(role="user", content=user_text), SimpleNamespace(role="assistant", content=answer)))
        active.append(stored)
    return results


async def main():
    provider = OpenAIRyaProvider()
    family = await run_sequence(provider, [], [], [
        ("your sons name is Prathamesh", analysis("Prathamesh is Pallavi's son.", "relationship", person="Prathamesh", role="son"), memory("Prathamesh is Pallavi's son.", "relationship", person="Prathamesh", role="son")),
        ("and Husband is Kiran Shedge", analysis("Kiran Shedge is Pallavi's husband.", "relationship", person="Kiran Shedge", role="husband"), memory("Kiran Shedge is Pallavi's husband.", "relationship", person="Kiran Shedge", role="husband")),
        ("i am her son creating for her", analysis("Prathamesh is Pallavi's son.", "relationship", person="Prathamesh", role="son"), memory("Prathamesh is creating Pallavi's Legacy as her son.", "relationship", person="Prathamesh", role="son")),
    ])
    natural = await run_sequence(provider, [], [], [
        ("She used to make tea every evening.", analysis("Pallavi made tea every evening.", "habit", story_key="evening-tea"), memory("Pallavi made tea every evening.", "habit", story_key="evening-tea")),
        ("We all sat on the balcony.", analysis("The family sat on the balcony for evening tea.", "family_story", story_key="evening-tea"), memory("The family sat on the balcony for evening tea.", "family_story", story_key="evening-tea")),
        ("My dad would joke a lot.", analysis("Kiran joked during family tea.", "family_story", person="Kiran Shedge", role="husband", story_key="evening-tea"), memory("Kiran joked during family tea.", "family_story", person="Kiran Shedge", role="husband", story_key="evening-tea")),
        ("He always did impressions of people from television.", analysis("Kiran did television impressions.", "habit", person="Kiran Shedge", role="husband", story_key="evening-tea"), memory("Kiran did television impressions.", "habit", person="Kiran Shedge", role="husband", story_key="evening-tea")),
        ("Pallavi laughed so hard she sometimes spilled her tea.", analysis("Pallavi laughed at Kiran's impressions and sometimes spilled tea.", "family_story", person="Kiran Shedge", role="husband", story_key="evening-tea"), memory("Pallavi laughed at Kiran's impressions and sometimes spilled tea.", "family_story", person="Kiran Shedge", role="husband", story_key="evening-tea")),
        ("Even the neighbors could hear her laughing.", analysis("Neighbors could hear Pallavi laughing.", "family_story", story_key="evening-tea"), memory("Neighbors could hear Pallavi laughing.", "family_story", story_key="evening-tea")),
        ("It felt especially cozy during the monsoon.", analysis("The family tea ritual felt cozy during monsoon.", "family_story", story_key="evening-tea"), memory("The family tea ritual felt cozy during monsoon.", "family_story", story_key="evening-tea")),
        ("She would make onion pakoras when it rained.", analysis("Pallavi made onion pakoras during rainy tea evenings.", "tradition", story_key="evening-tea"), memory("Pallavi made onion pakoras during rainy tea evenings.", "tradition", story_key="evening-tea")),
        ("Her mother taught her the recipe.", analysis("Pallavi learned the pakora recipe from her mother.", "relationship", person="Pallavi's mother", role="mother", story_key="evening-tea"), memory("Pallavi learned the pakora recipe from her mother.", "relationship", person="Pallavi's mother", role="mother", story_key="evening-tea")),
        ("Now we make them for our children on rainy evenings.", analysis("The family continues Pallavi's rainy-evening pakora tradition.", "tradition", story_key="evening-tea"), memory("The family continues Pallavi's rainy-evening pakora tradition.", "tradition", story_key="evening-tea")),
    ])
    print(json.dumps({"family_sequence": family, "ten_turn_conversation": natural}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
