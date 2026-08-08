"""Run a local, provider-free Phase 9.14 retrieval smoke evaluation."""

from datetime import datetime, timezone
from decimal import Decimal

from app.config import get_settings
from app.models.memory import MemoryType
from app.schemas.memory import ApprovedMemoryRetrievalItem
from app.services.memory.embedding import cosine_similarity, memory_embedding_text
from app.services.memory.multilingual_retrieval import detect_query_language_mode
from app.services.memory.embedding_registry import create_embedding_provider
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker


def memory(memory_id: int, title: str, summary: str):
    now = datetime.now(timezone.utc)
    return ApprovedMemoryRetrievalItem(
        memory_id=memory_id,
        memory_type=MemoryType.ATOMIC,
        category="story",
        title=title,
        summary=summary,
        importance=3,
        extraction_confidence=Decimal("0.8"),
        created_at=now,
        updated_at=now,
    )


def main() -> int:
    memories = [
        memory(
            1,
            "\u0936\u093f\u0915\u094d\u0937\u0923",
            "\u092e\u0940 \u092e\u093e\u0927\u0935\u0940 \u0936\u093e\u0933\u0947\u0924 \u0936\u093f\u0915\u0932\u0947. "
            "\u0928\u0902\u0924\u0930 KJ Somaiya College \u092e\u0927\u094d\u092f\u0947 \u0936\u093f\u0915\u0932\u0947.",
        ),
        memory(2, "Garden", "Jasmine bloomed near the house."),
        memory(3, "\u092e\u0942\u0933\u0917\u093e\u0935", "\u092e\u093e\u091d\u0947 \u092e\u0942\u0933\u0917\u093e\u0935 \u092a\u0941\u0923\u0947 \u0906\u0939\u0947."),
        memory(4, "\u0915\u0941\u091f\u0941\u0902\u092c", "\u092e\u093e\u091d\u0940 \u0906\u0908 \u0906\u0928\u0902\u0926\u0940 \u092f\u093e \u0928\u093e\u0935\u093e\u0928\u0947 \u0913\u0933\u0916\u0932\u0940 \u091c\u093e\u092f\u091a\u0940."),
        memory(5, "\u0928\u094c\u0915\u0930\u0940", "\u092e\u0948\u0902\u0928\u0947 \u0926\u093f\u0932\u094d\u0932\u0940 \u092e\u0947\u0902 \u0936\u093f\u0915\u094d\u0937\u0915 \u0915\u0947 \u0930\u0942\u092a \u092e\u0947\u0902 \u0915\u093e\u092e \u0915\u093f\u092f\u093e."),
        memory(6, "\u0906\u0935\u0921\u0924\u0947 \u091c\u0947\u0935\u0923", "\u092e\u0932\u093e \u092a\u094b\u0939\u0947 \u0906\u0935\u0921\u0924\u093e\u0924."),
        memory(7, "\u0932\u0917\u094d\u0928", "\u0906\u092e\u091a\u0947 \u0932\u0917\u094d\u0928 \u0915\u0926\u093e\u091a\u093f\u0924 1998 \u092e\u0927\u094d\u092f\u0947 \u091d\u093e\u0932\u0947; 1997 \u092e\u0927\u094d\u092f\u0947 \u0928\u093e\u0939\u0940."),
    ]
    cases = (
        ("What school did you attend?", 1),
        ("Which college did you study at?", 1),
        ("Tu kuthlya shalet shiklis?", 1),
        ("\u0924\u0942 \u0915\u0941\u0920\u0932\u094d\u092f\u093e \u0936\u093e\u0933\u0947\u0924 \u0936\u093f\u0915\u0932\u0940\u0938?", 1),
        ("\u0924\u0941\u092e\u0928\u0947 \u0915\u094c\u0928 \u0938\u0947 \u0915\u0949\u0932\u0947\u091c \u092e\u0947\u0902 \u092a\u0922\u093c\u093e\u0908 \u0915\u0940?", 1),
        ("What flowers grew in the garden?", 2),
        ("What was your hometown?", 3),
        ("What was your mother's name?", 4),
        ("What work did you do?", 5),
        ("What food did you like?", 6),
        ("What year was your marriage?", 7),
    )
    ranker = MemoryRelevanceRanker()
    provider = create_embedding_provider(get_settings())
    vectors = provider.embed([memory_embedding_text(item) for item in memories])
    failures = 0
    for query, expected in cases:
        query_vector = provider.embed([query])[0]
        semantic_scores = {
            item.memory_id: score
            for item, vector in zip(memories, vectors)
            if (score := cosine_similarity(query_vector, vector)) >= 0.35
        }
        ranked = ranker.rank(memories, query, semantic_scores=semantic_scores)
        expected_rank = next(
            (index for index, item in enumerate(ranked, start=1) if item.memory_id == expected),
            None,
        )
        actual = ranked[0].memory_id if ranked else None
        passed = expected_rank == 1
        failures += not passed
        score = ranked[0].relevance_score if ranked else 0
        print(
            f"mode={detect_query_language_mode(query):<24} "
            f"expected={expected} actual={actual} rank={expected_rank} score={score:.3f} "
            f"result={'PASS' if passed else 'FAIL'}"
        )
    print(f"summary: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
