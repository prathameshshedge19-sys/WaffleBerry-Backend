"""L21.5 bind preserved Live speech jobs to the authoritative realtime turn.

Revision ID: 0026_voice_live_synthesis
Revises: 0025_voice_synthesis_jobs
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_voice_live_synthesis"
down_revision = "0025_voice_synthesis_jobs"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("voice_jobs") as batch:
        batch.drop_constraint("ck_voice_jobs_synthesis_payload", type_="check")
        batch.add_column(sa.Column("realtime_turn_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("realtime_claim_token", sa.String(36), nullable=True))
        batch.create_check_constraint(
            "ck_voice_jobs_synthesis_payload",
            "kind <> 'synthesize' OR (purpose IN ('preview','message','live') AND "
            "authoritative_text IS NOT NULL AND length(authoritative_text) BETWEEN 1 AND 4096 AND "
            "length(authoritative_text_digest) = 64 AND length(model_manifest_digest) = 64 AND "
            "length(inference_config_digest) = 64 AND requested_by_user_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_voice_jobs_live_context",
            "kind <> 'synthesize' OR purpose <> 'live' OR (conversation_id IS NOT NULL AND "
            "realtime_turn_id IS NOT NULL AND realtime_claim_token IS NOT NULL)",
        )
        batch.create_index("ix_voice_jobs_realtime_turn", ["realtime_turn_id", "id"])


def downgrade():
    # L21.4 cannot represent a live job's claim or purpose. Refuse before DDL
    # rather than discard private-asset purge ownership or partially alter SQLite.
    if op.get_context().as_sql:
        if op.get_bind().dialect.name != "postgresql":
            raise RuntimeError("The 0026 downgrade requires an online database or PostgreSQL SQL output.")
        op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM voice_jobs WHERE purpose = 'live') THEN "
            "RAISE EXCEPTION 'Cannot downgrade 0026 while live voice jobs exist'; END IF; END $$;")
    elif op.get_bind().execute(sa.text("SELECT 1 FROM voice_jobs WHERE purpose = 'live' LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade 0026 while live voice jobs exist; retain this revision until their authorized retention/purge cleanup is complete.")
    with op.batch_alter_table("voice_jobs") as batch:
        batch.drop_index("ix_voice_jobs_realtime_turn")
        batch.drop_constraint("ck_voice_jobs_live_context", type_="check")
        batch.drop_constraint("ck_voice_jobs_synthesis_payload", type_="check")
        batch.create_check_constraint(
            "ck_voice_jobs_synthesis_payload",
            "kind <> 'synthesize' OR (purpose IN ('preview','message') AND "
            "authoritative_text IS NOT NULL AND length(authoritative_text) BETWEEN 1 AND 4096 AND "
            "length(authoritative_text_digest) = 64 AND length(model_manifest_digest) = 64 AND "
            "length(inference_config_digest) = 64 AND requested_by_user_id IS NOT NULL)",
        )
        batch.drop_column("realtime_claim_token")
        batch.drop_column("realtime_turn_id")
