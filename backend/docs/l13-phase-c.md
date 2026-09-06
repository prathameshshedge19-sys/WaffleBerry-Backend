# L13 Phase C: read-only persona styling

Only `persona_system_context` and its call in visitor `_turns` are integrated.
Conversation creation, request validation, persistence, stream generation, auth,
identity matching, routing, and voice remain unchanged. No frontend edits.

The selector checks the current projection's generation and versions, reads active
evidence, validates the full manifest, source spans, scope, and the semantics of
individual observation references. This last check recognizes evidence locally;
it does not rebuild or publish a profile. No provider call or worker scheduling
is added. A second freshness read catches mutations racing the evidence load.
An already assembled/in-flight response cannot be retroactively revoked.

The selector runs under `no_autoflush` and a connection-level savepoint so a
derived-table SQL error can roll back without poisoning the PostgreSQL request
transaction. Only SELECTs and savepoint control statements are issued. Profile
validation/read failure returns no L13 style and retains the L12 prompt exactly.
Existing visitor message/identity writes are not prohibited; canonical memory and
personality rows remain unchanged by visitor turns.

A valid L13 profile suppresses the coarse `derived_persona_profile` intelligence
field, including when no observations match this turn. The remaining factual and
route fields are not changed. A bounded style-only block follows the original
prompt. It contains predefined phrasing directions, never raw trait descriptions,
memory IDs, manifests, or personal-fact claims. Values can affect wording only
when relevant to the current question, not establish beliefs or opinions.

Named/family contexts require existing L11 verified status plus currently active
explicit relationship support for the visitor. Claims alone are insufficient.
Unsupported relationship patterns are conservatively omitted; identity matching
itself is not modified. Setting/time/situation qualifiers must match the current
question. Same-context contradictions are suppressed. Context specificity wins
over a broad trait in the same dimension. Humor is suppressed for recognized
distress/sensitive contexts; this lexical rule is not a general safety classifier.

At most five phrasing cues and one optional verbatim expression are included.
Signature wording is revalidated, current language must fit, and a recorded
situational or relationship context must match. Any preserved expression or
existing visitor nickname in either of the two most recent assistant replies
suppresses the next expression. Existing nickname rules remain in the prompt.
No back-translation or inferred familiarity is introduced. Tentative teasing
evidence remains ineligible; this phase does not change Phase B confidence rules.

The extra block is capped at 1500 UTF-8 bytes (roughly 500 tokens using a bytes/3
estimate, not an exact tokenizer count). Tests report actual bytes and local SQLite
selector timing; neither is a production PostgreSQL latency measurement. The
selector adds bounded projection reads plus active-evidence/entity reads; it does
not serialize those records into the additional prompt block.

`tests/real_provider_smoke_l13_phase_c.py` makes at most five sequential real calls,
each bounded to 40 seconds, with ephemeral in-memory SQLite data. It stops on the
first provider failure and prints only disposable responses or a failure class.
It uses existing model settings and never prints keys or raw provider errors.
It does not migrate, backfill, or modify any local/production application database.

Automated fake-provider tests establish prompt/routing and persistence contracts,
not genuine model quality. The real responses require review for unsupported
memories, modest style differences, and relationship restraint. No model settings,
deployment, commits, or frontend dashboard are part of this phase.
