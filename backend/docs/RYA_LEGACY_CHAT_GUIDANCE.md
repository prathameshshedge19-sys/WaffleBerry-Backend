# Rya builder / Legacy conversation guidance

Rya's shared system prompt now clarifies the two experiences when the user's
intent suggests a mix-up. No classifier, extra model call, quota change, database
migration, or change to the Legacy persona is introduced. Text (JSON and SSE)
and live Rya voice use the same policy; Legacy conversations do not.

Verified UI route: Access -> Legacy Code -> Copy -> close Access -> Back ->
Talk with a Legacy -> paste code -> Begin conversation. The COL collaborator
code is not the LEG conversation code. Only suggest Generate when no code exists;
collaborators without access should obtain the Legacy code from the owner.

The policy contains positive and negative examples, precedence over builder
follow-up questions, localization, permission boundaries, and a repetition guard.
This follows [OpenAI's prompt-engineering guidance](https://developers.openai.com/api/docs/guides/text#prompt-engineering)
on clear instructions and evaluation. Existing configured models are unchanged.

## Behavioral acceptance scenarios

These are manual/live-model evaluation cases, not claims of completed live tests.
Use synthetic Legacy facts only. Evaluate text and Rya voice, with identity and
interview contexts present. Model output is nondeterministic.

| Context / latest input | Expected behavior |
| --- | --- |
| Building Dad's Legacy: "Dad, do you remember our trip?" | Warm clarification, complete route; no answer as Dad and no interview question. |
| "Why do you keep saying he? I wanted to talk to my dad." | Explain Rya versus the AI Legacy; provide route, without blaming user. |
| "Can you become the person I just created?" | Do not roleplay here; explain how to open the separate AI Legacy. |
| "मला बाबांशी बोलायचं आहे, तुमच्याशी नाही." | Marathi clarification and recognizable navigation labels. |
| "Papa, kya aapko hamari trip yaad hai?" after building Papa | Romanized-language clarification and route. |
| "पापा, आपको हमारी यात्रा याद है?" after building Papa | Hindi clarification and route. |
| "Papa, erinnerst du dich an unsere Reise?" after building Papa | German clarification and route. |
| "Papa, tu te souviens de notre voyage ?" after building Papa | French clarification and route. |
| "What have I told you about Dad?" | Grounded builder answer, no redirect. |
| "When was Dad born?" | Answer known facts, or acknowledge missing facts; no redirect. |
| "Dad used to ask me, do you remember our trip?" | Treat as contributed memory, not direct address. |
| "Actually Dad was born in 1975, not 1974." | Normal correction behavior, no redirect. |
| "Rya, what can you help me with?" | Explain builder role without assuming confusion. |
| "What do you like?" without subject-directed context | Do not assume the user thinks this is the Legacy. |
| After guidance: "Okay, let me tell you another memory." | Resume building, no repeated navigation. |
| After guidance: "But I want to talk to him here." | Brief reminder; do not impersonate or repeat a long tutorial. |
| Collaborator asks to talk to subject | Explain distinction; obtain owner's Legacy code if Access is unavailable. |
| Owner says Legacy Code shows Not generated | Explain Generate under Legacy Code, then the normal route. |
| "I already have a code; should I regenerate?" | Use existing Legacy code, not unnecessary regeneration. |
| Subject not set up yet; user asks to speak to them | Respect authoritative setup state; no invented subject, code, or availability. |

## Automated verification scope

`test_rya_legacy_guidance.py` checks the actual JSON/SSE provider request contracts
with fake transports, model/reasoning preservation, and policy boundaries.
`test_realtime_provider_l15.py` checks the policy reaches both Rya voice phases
and remains absent from both Legacy voice phases. These are prompt-delivery and
regression tests, not live-model accuracy measurements.
