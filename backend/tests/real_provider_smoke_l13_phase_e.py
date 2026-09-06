"""Four independent, bounded builder quality checks; no application DB writes."""

import asyncio
import json
from types import SimpleNamespace

from app.config import get_settings
from app.services.memory import MemoryAnalysis, MemoryCandidate, progressive_interviewing
from app.services.rya import ChatTurn, OpenAIRyaProvider


async def main():
    if not get_settings().openai_api_key:
        print(json.dumps({"status": "blocked", "reason": "provider_key_unavailable"}))
        return
    phrase = "\u0905\u0917\u0902 \u092c\u093e\u0908"
    cases = [
        ("studies", "My mother was very strict about studies.", "Pallavi was very strict about studies.", "personality"),
        ("humor", "She always made everyone laugh.", "Pallavi always made everyone laugh.", "habit"),
        ("expression", f'She used to say "{phrase}" whenever she was surprised.', f'Pallavi said "{phrase}" when surprised.', "habit"),
        ("emotional", "I still remember how she sat with me all night when I was sick.", "Pallavi sat with Prathamesh all night when he was sick.", "habit"),
    ]
    provider = OpenAIRyaProvider()
    for index, (label, user_text, canonical, category) in enumerate(cases, 1):
        analysis = MemoryAnalysis(source_language="english", normalized_query=canonical, memories=[MemoryCandidate(canonical_text=canonical, category=category, confidence=.98)])
        context = [SimpleNamespace(role="user", content=user_text)]
        guidance = progressive_interviewing(analysis, (), context, subject_name="Pallavi", contributor_role="owner")
        turns = [
            ChatTurn(role="system", content="You are Rya, helping Prathamesh preserve his mother Pallavi's Legacy. Setup is complete. Do not re-onboard. Treat contributions as evidence, without inventing motives, roles, emotions or memories."),
            ChatTurn(role="system", content=guidance), ChatTurn(role="user", content=user_text),
        ]
        try:
            answer = await asyncio.wait_for(provider.respond(turns), timeout=40)
        except Exception as exc:
            print(json.dumps({"status": "blocked", "reason": type(exc).__name__, "attempted_calls": index}))
            return
        print(json.dumps({"case": label, "response": answer, "question_marks": answer.count("?")}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
