"""Add L17 Legacy timeline structures."""

from alembic import op
import sqlalchemy as sa

revision = "0019_legacy_timeline"
down_revision = "0018_media_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("source_evidence") as batch:
        batch.create_unique_constraint("uq_source_evidence_legacy_id_id", ["legacy_id", "id"])
    with op.batch_alter_table("memory_entities") as batch:
        batch.create_unique_constraint("uq_memory_entities_legacy_id_id", ["legacy_id", "id"])

    op.create_table(
        "life_events",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("admission_key", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("event_type", sa.String(40), nullable=False, server_default="other"),
        sa.Column("date_start", sa.Date()), sa.Column("date_end", sa.Date()), sa.Column("sort_date", sa.Date()),
        sa.Column("date_precision", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("is_approximate", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("date_label", sa.String(255)), sa.Column("sequence_hint", sa.Integer()), sa.Column("place_label", sa.String(255)),
        sa.Column("confidence", sa.Float()), sa.Column("origin", sa.String(32), nullable=False, server_default="canonical_structured"),
        sa.Column("review_state", sa.String(16), nullable=False, server_default="approved"), sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("conflict_json", sa.JSON()), sa.Column("conflict_resolved_at", sa.DateTime(timezone=True)), sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id"], ["legacies.id"], ondelete="CASCADE"), sa.UniqueConstraint("legacy_id", "id", name="uq_life_events_legacy_id_id"), sa.UniqueConstraint("legacy_id", "admission_key", name="uq_life_events_admission_key"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_life_events_title_not_blank"), sa.CheckConstraint("date_end IS NULL OR date_start IS NULL OR date_end >= date_start", name="ck_life_events_date_order"),
        sa.CheckConstraint("date_precision IN ('day','month','year','range','life_period','unknown')", name="ck_life_events_precision"), sa.CheckConstraint("origin IN ('canonical_structured','human_created','source_supported','ai_proposed')", name="ck_life_events_origin"),
        sa.CheckConstraint("review_state IN ('approved','needs_review','conflict','superseded','archived')", name="ck_life_events_review"), sa.CheckConstraint("lifecycle_state IN ('active','deleted')", name="ck_life_events_lifecycle"), sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_life_events_confidence"),
    )
    op.create_index("ix_life_events_legacy_sort", "life_events", ["legacy_id", "lifecycle_state", "sort_date", "sequence_hint", "id"])
    op.create_index("ix_life_events_legacy_review", "life_events", ["legacy_id", "review_state", "updated_at"])

    op.create_table(
        "life_event_memories",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("event_id", sa.String(36), nullable=False), sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("link_role", sa.String(32), nullable=False, server_default="additional_support"), sa.Column("link_state", sa.String(16), nullable=False, server_default="active"), sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("source_memory_updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["legacy_id", "event_id"], ["life_events.legacy_id", "life_events.id"], ondelete="CASCADE", name="fk_life_event_memories_event_scope"), sa.ForeignKeyConstraint(["legacy_id", "memory_id"], ["memories.legacy_id", "memories.id"], ondelete="CASCADE", name="fk_life_event_memories_memory_scope"), sa.UniqueConstraint("legacy_id", "event_id", "memory_id", "link_role", name="uq_life_event_memory_role"),
    )
    op.create_index("ix_life_event_memories_memory", "life_event_memories", ["legacy_id", "memory_id", "link_state"])

    op.create_table(
        "life_event_evidence",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("event_id", sa.String(36), nullable=False), sa.Column("evidence_id", sa.String(36), nullable=False), sa.Column("link_state", sa.String(16), nullable=False, server_default="available"), sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id", "event_id"], ["life_events.legacy_id", "life_events.id"], ondelete="CASCADE", name="fk_life_event_evidence_event_scope"), sa.ForeignKeyConstraint(["legacy_id", "evidence_id"], ["source_evidence.legacy_id", "source_evidence.id"], ondelete="RESTRICT", name="fk_life_event_evidence_evidence_scope"), sa.UniqueConstraint("legacy_id", "event_id", "evidence_id", name="uq_life_event_evidence"),
    )
    op.create_index("ix_life_event_evidence_evidence", "life_event_evidence", ["legacy_id", "evidence_id", "link_state"])

    op.create_table(
        "life_event_entities",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("event_id", sa.String(36), nullable=False), sa.Column("entity_id", sa.Integer(), nullable=False), sa.Column("role", sa.String(40), nullable=False, server_default="mentioned"),
        sa.ForeignKeyConstraint(["legacy_id", "event_id"], ["life_events.legacy_id", "life_events.id"], ondelete="CASCADE", name="fk_life_event_entities_event_scope"), sa.ForeignKeyConstraint(["legacy_id", "entity_id"], ["memory_entities.legacy_id", "memory_entities.id"], ondelete="CASCADE", name="fk_life_event_entities_entity_scope"), sa.UniqueConstraint("legacy_id", "event_id", "entity_id", "role", name="uq_life_event_entity_role"),
    )
    op.create_index("ix_life_event_entities_entity", "life_event_entities", ["legacy_id", "entity_id"])


def downgrade():
    op.drop_table("life_event_entities")
    op.drop_table("life_event_evidence")
    op.drop_table("life_event_memories")
    op.drop_table("life_events")
    with op.batch_alter_table("memory_entities") as batch:
        batch.drop_constraint("uq_memory_entities_legacy_id_id", type_="unique")
    with op.batch_alter_table("source_evidence") as batch:
        batch.drop_constraint("uq_source_evidence_legacy_id_id", type_="unique")
