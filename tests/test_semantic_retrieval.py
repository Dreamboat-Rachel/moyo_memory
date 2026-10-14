from qdrant_client import QdrantClient

from moyo_memory.config import Settings
from moyo_memory.db import MemoryRepository, create_db_engine
from moyo_memory.index_worker import VectorIndexWorker
from moyo_memory.models import (
    FactCandidate,
    FactCategory,
    MemoryOperation,
    RecallRequest,
    SemanticMatch,
    Sensitivity,
)
from moyo_memory.semantic import QdrantVectorIndex, VectorDocument


class FakeEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), 1.0] for index, _ in enumerate(texts, start=1)]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0]


class FakeVectorIndex:
    def __init__(self):
        self.documents: list[VectorDocument] = []
        self.deleted: list[str] = []

    def upsert(self, documents: list[VectorDocument]) -> None:
        self.documents.extend(documents)

    def delete(self, fact_ids: list[str]) -> None:
        self.deleted.extend(fact_ids)

    def search(self, **kwargs):
        return []


def make_repository() -> MemoryRepository:
    repository = MemoryRepository(create_db_engine("sqlite+pysqlite:///:memory:"))
    repository.create_schema()
    return repository


def make_fact(value: str) -> FactCandidate:
    return FactCandidate(
        category=FactCategory.preference,
        key="hobby",
        value=value,
        normalized_value=value,
        operation=MemoryOperation.append,
        confidence=0.95,
        evidence=f"我喜欢{value}",
        sensitivity=Sensitivity.normal,
    )


def test_vector_outbox_is_processed_after_fact_commit() -> None:
    repository = make_repository()
    decision = repository.apply_candidates("u-1", "m-1", [make_fact("徒步")])[0]
    index = FakeVectorIndex()
    worker = VectorIndexWorker(repository, FakeEmbeddings(), index)

    assert repository.vector_outbox_counts() == {"pending": 1}
    assert worker.process_once() == 1

    assert index.documents[0].fact_id == decision.fact_id
    assert index.documents[0].user_id == "u-1"
    assert repository.vector_outbox_counts() == {"completed": 1}


def test_vector_outbox_deletes_retracted_fact() -> None:
    repository = make_repository()
    created = repository.apply_candidates("u-1", "m-1", [make_fact("徒步")])[0]
    repository.apply_candidates(
        "u-1",
        "m-2",
        [
            FactCandidate(
                **{
                    **make_fact("徒步").model_dump(),
                    "operation": MemoryOperation.retract,
                }
            )
        ],
    )
    index = FakeVectorIndex()
    worker = VectorIndexWorker(repository, FakeEmbeddings(), index)

    assert worker.process_once() == 1
    assert index.documents == []
    assert index.deleted == [created.fact_id]


def test_semantic_match_recalls_fact_without_keyword_overlap() -> None:
    repository = make_repository()
    decision = repository.apply_candidates("u-1", "m-1", [make_fact("徒步")])[0]

    result = repository.recall(
        "u-1",
        RecallRequest(query="有哪些适合我的户外运动？", limit=5),
        semantic_matches=[SemanticMatch(fact_id=decision.fact_id, score=0.91)],
        semantic_available=True,
    )

    assert result.retrieval_mode == "hybrid_vector_graph"
    assert result.hits[0].fact.value == "徒步"
    assert result.hits[0].scores.semantic == 0.91
    assert "向量语义匹配" in result.hits[0].match_reasons[0]


def test_qdrant_index_filters_results_by_user_id() -> None:
    settings = Settings(
        _env_file=None,
        embedding_dimensions=2,
        qdrant_collection="test_facts",
    )
    index = QdrantVectorIndex(
        settings,
        client=QdrantClient(location=":memory:"),
    )
    documents = [
        VectorDocument(
            fact_id="11111111-1111-1111-1111-111111111111",
            user_id="u-1",
            category="preference",
            key="hobby",
            value="徒步",
            evidence="我喜欢徒步",
            confidence=0.95,
            vector=[1.0, 0.0],
        ),
        VectorDocument(
            fact_id="22222222-2222-2222-2222-222222222222",
            user_id="u-2",
            category="preference",
            key="hobby",
            value="摄影",
            evidence="我喜欢摄影",
            confidence=0.95,
            vector=[1.0, 0.0],
        ),
    ]
    index.upsert(documents)

    matches = index.search(
        user_id="u-1",
        vector=[1.0, 0.0],
        limit=10,
        categories=None,
    )

    assert [match.fact_id for match in matches] == [documents[0].fact_id]
    index.delete([documents[0].fact_id])
    assert index.search(
        user_id="u-1",
        vector=[1.0, 0.0],
        limit=10,
        categories=None,
    ) == []
