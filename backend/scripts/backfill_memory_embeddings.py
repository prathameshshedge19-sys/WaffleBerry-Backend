"""Idempotently backfill approved memories with versioned embeddings."""

import argparse
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError
from app.config import get_settings
from app.db import SessionLocal
from app.models.memory import Memory, MemoryReviewStatus
from app.services.memory.embedding import EmbeddingProviderError
from app.services.memory.embedding_registry import create_embedding_provider
from app.services.memory.multilingual_retrieval import normalize_embedding_text


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--after-memory-id", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.batch_size > 512:
        raise SystemExit("--batch-size must be between 1 and 512")
    settings = get_settings()
    provider = create_embedding_provider(settings)
    db = SessionLocal()
    processed = failed = 0
    cursor = args.after_memory_id
    try:
        while True:
            rows = (
                db.query(Memory)
                .filter(
                    Memory.memory_id > cursor,
                    Memory.review_status == MemoryReviewStatus.APPROVED,
                )
                .order_by(Memory.memory_id.asc())
                .limit(args.batch_size)
                .all()
            )
            if not rows:
                break
            cursor = rows[-1].memory_id
            stale = [
                row for row in rows
                if row.embedding_model != provider.model
                or row.embedding_version != provider.version
                or row.embedding_dimensions != provider.dimensions
                or not row.embedding
            ]
            if not stale:
                continue
            texts = [
                normalize_embedding_text(
                    "\n".join((row.title, row.summary, row.category))
                )
                for row in stale
            ]
            try:
                vectors = provider.embed(texts)
                if len(vectors) != len(stale):
                    raise ValueError("embedding count mismatch")
                now = datetime.now(timezone.utc)
                for row, vector in zip(stale, vectors):
                    if len(vector) != provider.dimensions:
                        raise ValueError("incompatible dimensions")
                    row.embedding = vector
                    row.embedding_model = provider.model
                    row.embedding_version = provider.version
                    row.embedding_dimensions = provider.dimensions
                    row.embedded_at = now
                db.commit()
                processed += len(stale)
            except (EmbeddingProviderError, SQLAlchemyError, TypeError, ValueError):
                db.rollback()
                failed += len(stale)
            print(
                f"cursor={cursor} embedded={processed} failed={failed}"
            )
    finally:
        db.close()
    print(f"complete embedded={processed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
