# Legacy Persona identity and speaking style

Phase 7.2 extends the Phase 7.1 Persona prompt with a deterministic,
non-persisted `PersonaProfile`. The profile is regenerated for each Legacy
conversation from all current approved Memory titles and summaries. It is
separate from factual relevance ranking: the existing retrieval, ranking,
grounding budget, prompt boundary, and provenance pipeline remains unchanged.
No table, column, migration, model field, or frontend contract was added.

The extractor is intentionally conservative. It recognizes short quoted text
only when the same approved Memory contains an explicit greeting, nickname, or
recurring-expression cue. Examples include a Memory stating that the person
“greeted family with,” “called her son,” had a “favourite saying,” or “often
said” a quoted phrase. Straight and curly double quotes and curly typographic
single quotes preserve Unicode and multilingual expressions without treating
ordinary apostrophes as quotation boundaries.

Tone markers use a small allowlist of explicit statements such as “spoke
warmly,” “tone was formal,” “spoke directly,” “dry sense of humour,” or “loved
telling stories.” The system does not infer personality from occupations,
events, preferences, relationships, sentiment, or writing length. It never
derives religion, politics, humour, habits, likes, dislikes, family behavior,
or cultural wording unless an approved Memory explicitly provides the relevant
style evidence.

Profile construction sorts evidence by Memory ID, deduplicates values
case-insensitively, preserves original spelling and Unicode, and caps each field
at five items. The resulting JSON contains greetings, nicknames, recurring
expressions, and explicit tone markers. It is enclosed in an untrusted-data
boundary. Persona instructions say to reuse evidence naturally and sparingly,
not force it into every reply, and never extrapolate new traits or phrases.

When no approved style evidence exists, the profile is empty and the Persona
uses the Phase 7.1 fallback: warm, natural, emotionally stable, and concise.
The prompt explicitly forbids inventing nicknames, catchphrases, humour,
personality traits, or cultural expressions. First-person identity,
unknown-memory behavior, prompt-injection resistance, and explicit-only AI
disclosure remain unchanged.

Current limitations: approved Memory summaries may normalize rather than fully
preserve original speech, so the foundation deliberately prefers silence over
speculative style. Raw Story messages are not used because they are not approved
Memory records. The profile is rebuilt synchronously and is suitable for the
current development-scale dataset; caching would require careful invalidation
after review changes. Speaking-style quality evaluation, stronger factual
fidelity, conversation continuity, Persona quality work, and final validation
remain Phases 7.3–7.6. Voice, fine-tuning, embeddings, and emotion modeling are
out of scope.
