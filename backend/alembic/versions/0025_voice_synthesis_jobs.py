"""L21.4 bind synthesis jobs to authoritative text and authorization context.

Revision ID: 0025_voice_synthesis_jobs
Revises: 0024_voice_profiles
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_voice_synthesis_jobs"
down_revision = "0024_voice_profiles"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("voice_jobs") as batch:
        for name, value in (
            ("purpose", sa.String(16)),
            ("authoritative_text", sa.Text()),
            ("authoritative_text_digest", sa.String(64)),
            ("model_manifest_digest", sa.String(64)),
            ("inference_config_digest", sa.String(64)),
            ("requested_by_user_id", sa.Integer()),
            ("conversation_id", sa.Integer()),
            ("message_id", sa.Integer()),
        ):
            batch.add_column(sa.Column(name, value, nullable=True))
        batch.create_check_constraint(
            "ck_voice_jobs_synthesis_payload",
            "kind <> 'synthesize' OR (purpose IN ('preview','message') AND "
            "authoritative_text IS NOT NULL AND length(authoritative_text) BETWEEN 1 AND 4096 AND "
            "length(authoritative_text_digest) = 64 AND length(model_manifest_digest) = 64 AND "
            "length(inference_config_digest) = 64 AND requested_by_user_id IS NOT NULL)",
        )
        batch.create_check_constraint("ck_voice_jobs_message_context",
            "kind <> 'synthesize' OR purpose <> 'message' OR (conversation_id IS NOT NULL AND message_id IS NOT NULL)")
        batch.create_index("ix_voice_jobs_requester", ["requested_by_user_id", "id"])


def downgrade():
    with op.batch_alter_table("voice_jobs") as batch:
        batch.drop_index("ix_voice_jobs_requester")
        batch.drop_constraint("ck_voice_jobs_message_context", type_="check")
        batch.drop_constraint("ck_voice_jobs_synthesis_payload", type_="check")
        for name in (
            "message_id", "conversation_id", "requested_by_user_id",
            "inference_config_digest", "model_manifest_digest",
            "authoritative_text_digest", "authoritative_text", "purpose",
        ):
            batch.drop_column(name)
