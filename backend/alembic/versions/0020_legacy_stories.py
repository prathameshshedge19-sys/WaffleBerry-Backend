"""Add L18 Story narrative artifacts and provenance."""

from alembic import op
import sqlalchemy as sa

revision = "0020_legacy_stories"
down_revision = "0019_legacy_timeline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stories",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("narrative_perspective", sa.String(32), nullable=False), sa.Column("visibility", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="active"), sa.Column("current_version_id", sa.String(36)),
        sa.Column("staleness_state", sa.String(16), nullable=False, server_default="current"), sa.Column("staleness_reason", sa.String(80)),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id"], ["legacies.id"], ondelete="CASCADE"), sa.UniqueConstraint("legacy_id", "id", name="uq_stories_legacy_id_id"),
        sa.CheckConstraint("scope IN ('full_biography','childhood','education','career','family','relationship','place','event','custom')", name="ck_stories_scope"),
        sa.CheckConstraint("narrative_perspective IN ('legacy_first_person','biography_third_person')", name="ck_stories_perspective"),
        sa.CheckConstraint("visibility IN ('draft','published','archived')", name="ck_stories_visibility"), sa.CheckConstraint("lifecycle_state IN ('active','deleted')", name="ck_stories_lifecycle"), sa.CheckConstraint("staleness_state IN ('current','stale','needs_review')", name="ck_stories_staleness"), sa.CheckConstraint("length(trim(title)) > 0 AND length(title) <= 255", name="ck_stories_title_bound"),
    )
    op.create_index("ix_stories_legacy_visibility", "stories", ["legacy_id", "visibility", "updated_at"])

    op.create_table(
        "story_versions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("story_id", sa.String(36), nullable=False), sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"), sa.Column("generation_request_key", sa.String(128)), sa.Column("provider_model", sa.String(120)), sa.Column("policy_version", sa.String(64), nullable=False, server_default="l18-grounded-v1"), sa.Column("input_snapshot", sa.JSON()), sa.Column("audit_summary", sa.JSON()), sa.Column("failure_code", sa.String(64)), sa.Column("generation_attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0"), sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id", "story_id"], ["stories.legacy_id", "stories.id"], ondelete="CASCADE", name="fk_story_versions_story_scope"), sa.UniqueConstraint("legacy_id", "id", name="uq_story_versions_legacy_id_id"), sa.UniqueConstraint("legacy_id", "story_id", "version_number", name="uq_story_versions_number"), sa.UniqueConstraint("legacy_id", "story_id", "generation_request_key", name="uq_story_versions_request"),
        sa.CheckConstraint("status IN ('draft','generating','audit_failed','ready','accepted','superseded','failed')", name="ck_story_versions_status"), sa.CheckConstraint("version_number >= 1", name="ck_story_versions_number"), sa.CheckConstraint("generation_attempts >= 0 AND generation_attempts <= 3", name="ck_story_versions_attempts"),
    )
    op.create_index("ix_story_versions_generation", "story_versions", ["legacy_id", "status", "updated_at"])

    op.create_table(
        "story_chapters",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("story_version_id", sa.String(36), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("narrative_text", sa.Text(), nullable=False, server_default=""), sa.Column("generation_status", sa.String(16), nullable=False, server_default="draft"), sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0"), sa.Column("audit_summary", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id", "story_version_id"], ["story_versions.legacy_id", "story_versions.id"], ondelete="CASCADE", name="fk_story_chapters_version_scope"), sa.UniqueConstraint("legacy_id", "id", name="uq_story_chapters_legacy_id_id"), sa.UniqueConstraint("legacy_id", "story_version_id", "ordinal", name="uq_story_chapters_ordinal"),
        sa.CheckConstraint("length(trim(title)) > 0 AND length(title) <= 255", name="ck_story_chapters_title_bound"), sa.CheckConstraint("length(narrative_text) <= 12000", name="ck_story_chapters_text_bound"), sa.CheckConstraint("generation_status IN ('draft','generating','audit_failed','ready','accepted','superseded','failed')", name="ck_story_chapters_status"),
    )
    op.create_index("ix_story_chapters_version_order", "story_chapters", ["legacy_id", "story_version_id", "ordinal"])

    op.create_table(
        "story_support_links",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("legacy_id", sa.Integer(), nullable=False), sa.Column("story_version_id", sa.String(36), nullable=False), sa.Column("chapter_id", sa.String(36), nullable=False), sa.Column("support_kind", sa.String(24), nullable=False), sa.Column("memory_id", sa.Integer()), sa.Column("life_event_id", sa.String(36)), sa.Column("evidence_id", sa.String(36)), sa.Column("support_state", sa.String(16), nullable=False, server_default="available"), sa.Column("source_updated_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["legacy_id", "story_version_id"], ["story_versions.legacy_id", "story_versions.id"], ondelete="CASCADE", name="fk_story_support_version_scope"), sa.ForeignKeyConstraint(["legacy_id", "chapter_id"], ["story_chapters.legacy_id", "story_chapters.id"], ondelete="CASCADE", name="fk_story_support_chapter_scope"), sa.ForeignKeyConstraint(["legacy_id", "memory_id"], ["memories.legacy_id", "memories.id"], ondelete="CASCADE", name="fk_story_support_memory_scope"), sa.ForeignKeyConstraint(["legacy_id", "life_event_id"], ["life_events.legacy_id", "life_events.id"], ondelete="CASCADE", name="fk_story_support_event_scope"), sa.ForeignKeyConstraint(["legacy_id", "evidence_id"], ["source_evidence.legacy_id", "source_evidence.id"], ondelete="RESTRICT", name="fk_story_support_evidence_scope"),
        sa.CheckConstraint("support_kind IN ('memory','timeline_event','source_evidence')", name="ck_story_support_kind"), sa.CheckConstraint("support_state IN ('available','stale','unavailable','removed')", name="ck_story_support_state"), sa.CheckConstraint("(CASE WHEN memory_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN life_event_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) = 1", name="ck_story_support_one_target"), sa.UniqueConstraint("legacy_id", "chapter_id", "support_kind", "memory_id", "life_event_id", "evidence_id", name="uq_story_support_target"),
    )
    op.create_index("ix_story_support_target", "story_support_links", ["legacy_id", "memory_id", "life_event_id", "evidence_id"])


def downgrade():
    op.drop_table("story_support_links")
    op.drop_table("story_chapters")
    op.drop_table("story_versions")
    op.drop_table("stories")
