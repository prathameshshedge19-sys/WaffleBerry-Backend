"""Phase 9.14 hybrid multilingual semantic retrieval tests."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.models.memory import MemoryType
from app.schemas.memory import ApprovedMemoryRetrievalItem
from app.services.memory.embedding import (
    EmbeddingProvider,
    EmbeddingProviderError,
    MemoryEmbeddingService,
)
from app.services.memory.multilingual_retrieval import retrieval_tokens
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker


MARATHI_MEMORY = (
    "\u092e\u0940 \u092e\u093e\u0927\u0935\u0940 \u0936\u093e\u0933\u0947\u0924 \u0926\u0939\u093e\u0935\u0940\u092a\u0930\u094d\u092f\u0902\u0924 \u0936\u093f\u0915\u0932\u0947. "
    "\u0928\u0902\u0924\u0930 \u092e\u0940 KJ Somaiya College \u092e\u0927\u094d\u092f\u0947 \u0936\u093f\u0915\u0932\u0947."
)


class FakeMultilingualProvider(EmbeddingProvider):
    """Deterministic stand-in for a multilingual model; never uses a network."""

    model = "fake-multilingual"
    version = "test-v1"
    dimensions = 3

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.casefold()
            if any(term in lowered for term in (
                "work", "job", "\u0928\u094c\u0915\u0930\u0940", "\u0915\u093e\u092e",
            )):
                vectors.append([0.0, 1.0, 0.0])
            elif any(term in lowered for term in (
                "school", "college", "study", "shale", "shik",
                "\u0936\u093e\u0933", "\u0936\u093f\u0915", "\u0915\u0949\u0932\u0947\u091c", "\u092a\u0922\u093c",
            )):
                vectors.append([1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class NoDatabaseWrites:
    def rollback(self):
        raise AssertionError("compatible test embeddings must not write")


class FailingProvider(FakeMultilingualProvider):
    def embed(self, texts):
        raise EmbeddingProviderError("synthetic failure")


class RollbackOnlyDatabase:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *_args):
        return self

    def one(self):
        return self.row


class WritableDatabase(RollbackOnlyDatabase):
    def __init__(self, row):
        super().__init__()
        self.row = row
        self.commits = 0

    def query(self, _model):
        return FakeQuery(self.row)

    def commit(self):
        self.commits += 1


class MultilingualMemoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 6, tzinfo=timezone.utc)
        self.provider = FakeMultilingualProvider()
        self.semantic = MemoryEmbeddingService(self.provider, threshold=0.35)
        self.ranker = MemoryRelevanceRanker()

    def item(self, memory_id, title, summary):
        vector = self.provider.embed([f"{title}\n{summary}\nstory"])[0]
        return ApprovedMemoryRetrievalItem(
            memory_id=memory_id, memory_type=MemoryType.ATOMIC,
            category="story", title=title, summary=summary, importance=3,
            extraction_confidence=Decimal("0.8"), created_at=self.now,
            updated_at=self.now, embedding=vector,
            embedding_model=self.provider.model,
            embedding_version=self.provider.version,
            embedding_dimensions=self.provider.dimensions,
            embedded_at=self.now,
        )

    def rank(self, memories, query):
        semantic = self.semantic.score(NoDatabaseWrites(), memories, query)
        self.assertTrue(semantic.route_used)
        return self.ranker.rank(memories, query, semantic_scores=semantic.scores)

    def test_marathi_memory_retrieved_across_required_query_modes(self):
        memory = self.item(1, "\u0936\u093f\u0915\u094d\u0937\u0923", MARATHI_MEMORY)
        queries = (
            "What school did you attend?", "Which college did you study at?",
            "Tu kuthlya shalet shiklis?",
            "\u0924\u0942 \u0915\u0941\u0920\u0932\u094d\u092f\u093e \u0936\u093e\u0933\u0947\u0924 \u0936\u093f\u0915\u0932\u0940\u0938?",
            "\u0924\u0941\u092e\u0928\u0947 \u0915\u094c\u0928 \u0938\u0947 \u0915\u0949\u0932\u0947\u091c \u092e\u0947\u0902 \u092a\u0922\u093c\u093e\u0908 \u0915\u0940?",
        )
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual([item.memory_id for item in self.rank([memory], query)], [1])

    def test_english_memory_retrieved_by_marathi_and_hindi(self):
        memory = self.item(1, "Education", "I studied at Madhavi School and KJ Somaiya College.")
        for query in (
            "\u0924\u0942 \u0915\u0941\u0920\u0932\u094d\u092f\u093e \u0936\u093e\u0933\u0947\u0924 \u0936\u093f\u0915\u0932\u0940\u0938?",
            "\u0924\u0941\u092e\u0928\u0947 \u0915\u0939\u093e\u0901 \u092a\u0922\u093c\u093e\u0908 \u0915\u0940?",
        ):
            self.assertEqual([item.memory_id for item in self.rank([memory], query)], [1])

    def test_hindi_work_memory_retrieved_by_english(self):
        memory = self.item(1, "\u0928\u094c\u0915\u0930\u0940", "\u092e\u0948\u0902\u0928\u0947 \u092e\u0941\u0902\u092c\u0908 \u092e\u0947\u0902 \u0936\u093f\u0915\u094d\u0937\u0915 \u0915\u0947 \u0930\u0942\u092a \u092e\u0947\u0902 \u0915\u093e\u092e \u0915\u093f\u092f\u093e.")
        unrelated = self.item(2, "\u092f\u093e\u0924\u094d\u0930\u093e", "\u092e\u0948\u0902 \u091c\u092f\u092a\u0941\u0930 \u0917\u092f\u093e \u0925\u093e.")
        self.assertEqual(
            [item.memory_id for item in self.rank(
                [memory, unrelated], "What work did you do?"
            )],
            [1],
        )

    def test_unicode_tokens_keep_devanagari_combining_marks(self):
        text = (
            "KJ Somaiya \u0936\u093e\u0933\u0947\u0924 \u0936\u093f\u0915\u094d\u0937\u0923 \u092e\u0941\u0902\u092c\u0908 \u0936\u093f\u0915\u094d\u0937\u0915 "
            "\u0928\u094c\u0915\u0930\u0940 \u092a\u0922\u093c\u093e\u0908 1998"
        )
        self.assertEqual(
            retrieval_tokens(text),
            [
                "kj", "somaiya", "\u0936\u093e\u0933\u0947\u0924", "\u0936\u093f\u0915\u094d\u0937\u0923", "\u092e\u0941\u0902\u092c\u0908",
                "\u0936\u093f\u0915\u094d\u0937\u0915", "\u0928\u094c\u0915\u0930\u0940", "\u092a\u0922\u093c\u093e\u0908", "1998",
            ],
        )

    def test_semantic_and_lexical_candidates_fuse_without_duplicates(self):
        education = self.item(1, "\u0936\u093e\u0933\u093e", MARATHI_MEMORY)
        unrelated = self.item(2, "Garden", "Jasmine bloomed near the house.")
        result = self.rank([education, unrelated], "Which school did you attend?")
        self.assertEqual([item.memory_id for item in result], [1])

    def test_canonical_facts_are_unchanged_and_aliases_are_not_generated(self):
        summary = f"{MARATHI_MEMORY} \u0915\u0926\u093e\u091a\u093f\u0924 1998 \u092e\u0927\u094d\u092f\u0947 \u0928\u093e\u0939\u0940."
        memory = self.item(1, "\u0936\u093f\u0915\u094d\u0937\u0923", summary)
        ranked = self.rank([memory], "Which college did you study at?")[0]
        self.assertEqual(ranked.summary, summary)
        self.assertIn("KJ Somaiya", ranked.summary)
        self.assertIn("1998", ranked.summary)
        tokens = retrieval_tokens("KJ Somaiya \u0936\u093e\u0933\u0947\u0924 1998")
        self.assertEqual(tokens, ["kj", "somaiya", "\u0936\u093e\u0933\u0947\u0924", "1998"])

    def test_provider_failure_falls_back_without_false_semantic_candidates(self):
        database = RollbackOnlyDatabase()
        service = MemoryEmbeddingService(FailingProvider(), threshold=0.35)
        memory = self.item(1, "School", "I studied in Pune.")
        result = service.score(database, [memory], "Where did you study?")
        self.assertFalse(result.route_used)
        self.assertEqual(result.scores, {})
        self.assertTrue(database.rolled_back)

    def test_embedding_version_mismatch_is_rebuilt_idempotently(self):
        memory = self.item(1, "Education", "I studied at Madhavi School.")
        memory.embedding_version = "old-version"
        row = SimpleNamespace()
        database = WritableDatabase(row)
        first = self.semantic.score(database, [memory], "Which school?")
        self.assertTrue(first.route_used)
        self.assertEqual(memory.embedding_version, self.provider.version)
        self.assertEqual(row.embedding_dimensions, self.provider.dimensions)
        self.assertEqual(database.commits, 1)
        second = self.semantic.score(database, [memory], "Which school?")
        self.assertTrue(second.route_used)
        self.assertEqual(database.commits, 1)


if __name__ == "__main__":
    unittest.main()
