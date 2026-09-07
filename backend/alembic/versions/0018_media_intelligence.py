"""Add L16 evidence, candidate review and canonical provenance tables."""

from alembic import op
import sqlalchemy as sa


revision = "0018_media_intelligence"
down_revision = "0017_media_sources"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("memories") as batch:
        batch.create_unique_constraint("uq_memories_legacy_id_id", ["legacy_id", "id"])
    with op.batch_alter_table("media_artifacts") as batch:
        batch.create_unique_constraint("uq_media_artifacts_identity_scope", ["legacy_id", "source_id", "generation", "id"])
    with op.batch_alter_table("media_processing_jobs") as batch:
        batch.create_unique_constraint("uq_media_jobs_identity_scope", ["legacy_id", "source_id", "generation", "id"])

    op.create_table(
        "source_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("artifact_id", sa.String(36)),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("locator_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("language", sa.String(80)),
        sa.Column("confidence", sa.Float()),
        sa.Column("origin_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("asserted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "source_id", "id", name="uq_source_evidence_id_scope"),
        sa.UniqueConstraint("legacy_id", "source_id", "generation", "stable_key", name="uq_source_evidence_stable"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], ondelete="RESTRICT", name="fk_source_evidence_source_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "generation", "job_id"], ["media_processing_jobs.legacy_id", "media_processing_jobs.source_id", "media_processing_jobs.generation", "media_processing_jobs.id"], ondelete="RESTRICT", name="fk_source_evidence_job_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "generation", "artifact_id"], ["media_artifacts.legacy_id", "media_artifacts.source_id", "media_artifacts.generation", "media_artifacts.id"], ondelete="RESTRICT", name="fk_source_evidence_artifact_scope"),
        sa.CheckConstraint("length(text) <= 4000", name="ck_source_evidence_text_bound"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_source_evidence_confidence"),
    )
    op.create_index("ix_source_evidence_source", "source_evidence", ["legacy_id", "source_id", "generation"])

    op.create_table(
        "source_memory_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("proposal_json", sa.JSON(), nullable=False),
        sa.Column("review_draft_json", sa.JSON()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("review_action", sa.String(24)),
        sa.Column("review_request_key", sa.String(36)),
        sa.Column("review_digest", sa.String(64)),
        sa.Column("canonical_memory_id", sa.Integer()),
        sa.Column("promotion_outcome", sa.String(24)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "source_id", "generation", "stable_key", name="uq_source_candidates_stable"),
        sa.UniqueConstraint("legacy_id", "source_id", "id", name="uq_source_candidates_id_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], ondelete="RESTRICT", name="fk_source_candidates_source_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "generation", "job_id"], ["media_processing_jobs.legacy_id", "media_processing_jobs.source_id", "media_processing_jobs.generation", "media_processing_jobs.id"], ondelete="RESTRICT", name="fk_source_candidates_job_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "canonical_memory_id"], ["memories.legacy_id", "memories.id"], ondelete="SET NULL", name="fk_source_candidates_memory_scope"),
        sa.CheckConstraint("version >= 1", name="ck_source_candidates_version"),
        sa.CheckConstraint("length(CAST(proposal_json AS VARCHAR)) <= 16384", name="ck_source_candidates_proposal_bound"),
    )
    op.create_index("ix_source_candidates_review", "source_memory_candidates", ["legacy_id", "review_state", "created_at"])

    op.create_table(
        "source_candidate_evidence",
        sa.Column("legacy_id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), primary_key=True),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "candidate_id"], ["source_memory_candidates.legacy_id", "source_memory_candidates.source_id", "source_memory_candidates.id"], ondelete="CASCADE", name="fk_candidate_evidence_candidate_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "evidence_id"], ["source_evidence.legacy_id", "source_evidence.source_id", "source_evidence.id"], ondelete="CASCADE", name="fk_candidate_evidence_evidence_scope"),
    )
    op.create_index("ix_source_candidate_evidence_candidate", "source_candidate_evidence", ["candidate_id"])

    op.create_table(
        "memory_source_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("approved_text_sha256", sa.String(64), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("support_state", sa.String(16), nullable=False, server_default="approved"),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], ondelete="RESTRICT", name="fk_memory_source_links_source_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "candidate_id"], ["source_memory_candidates.legacy_id", "source_memory_candidates.source_id", "source_memory_candidates.id"], ondelete="RESTRICT", name="fk_memory_source_links_candidate_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id", "evidence_id"], ["source_evidence.legacy_id", "source_evidence.source_id", "source_evidence.id"], ondelete="RESTRICT", name="fk_memory_source_links_evidence_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "memory_id"], ["memories.legacy_id", "memories.id"], ondelete="RESTRICT", name="fk_memory_source_links_memory_scope"),
        sa.UniqueConstraint("legacy_id", "memory_id", "candidate_id", "evidence_id", name="uq_memory_source_link_support"),
    )
    op.create_index("ix_memory_source_links_memory", "memory_source_links", ["legacy_id", "memory_id", "support_state"])


def downgrade():
    op.drop_table("memory_source_links")
    op.drop_table("source_candidate_evidence")
    op.drop_table("source_memory_candidates")
    op.drop_table("source_evidence")
    with op.batch_alter_table("memories") as batch:
        batch.drop_constraint("uq_memories_legacy_id_id", type_="unique")
    with op.batch_alter_table("media_processing_jobs") as batch:
        batch.drop_constraint("uq_media_jobs_identity_scope", type_="unique")
    with op.batch_alter_table("media_artifacts") as batch:
        batch.drop_constraint("uq_media_artifacts_identity_scope", type_="unique")
