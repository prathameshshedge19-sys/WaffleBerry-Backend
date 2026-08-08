# Legacy Settings

Phase 6.6.5 adds a focused owner-scoped identity update:

```text
PATCH /api/v1/legacies/{legacy_id}
```

The authenticated owner may supply `display_name` and/or `relationship`, plus
the required `expected_updated_at` concurrency token. Both text values are
trimmed, remain Unicode-safe, and follow the existing database limits of 255
and 100 characters. At least one editable field must be supplied.

The request rejects unknown fields. `legacy_id`, `owner_user_id`,
`client_correlation_id`, status, timestamps, relationships, and aggregate data
cannot be mass-assigned. Status remains read-only because the only alternative
is `archived`, which belongs to Phase 6.8 lifecycle management.

Missing and non-owned Legacies return the same neutral 404. A timestamp that
does not match the current record returns `409 legacy_changed`. Real changes
use an atomic owner-and-timestamp-guarded update so a racing request cannot
silently overwrite a newer value. A no-op returns
the current projection without committing or advancing `updated_at`. A real
change commits atomically and explicitly advances `updated_at`.

The response contains only `legacy_id`, `display_name`, `relationship`,
`status`, `created_at`, and `updated_at`. Archive, restore, deletion, export,
transfer, sharing, and collaboration remain outside this endpoint and Phase.
