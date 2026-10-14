from datetime import datetime, timezone

import pytest

from moyo_memory.db import MessageConflictError, MemoryRepository, create_db_engine
from moyo_memory.graph import build_memory_graph
from moyo_memory.models import (
    ExtractionResult,
    FactCandidate,
    FactCategory,
    FactStatus,
    IngestRequest,
    MemoryOperation,
    RelationCandidate,
    RecallRequest,
    Sensitivity,
)


class FakeExtractor:
    def __init__(self, results: list[ExtractionResult]):
        self.results = iter(results)

    def extract(self, request: IngestRequest) -> ExtractionResult:
        return next(self.results)


def candidate(
    key: str,
    value: str,
    *,
    category: FactCategory = FactCategory.profile,
    operation: MemoryOperation = MemoryOperation.replace,
    sensitivity: Sensitivity = Sensitivity.personal,
    explicit: bool = True,
    confidence: float = 0.95,
) -> FactCandidate:
    return FactCandidate(
        category=category,
        key=key,
        value=value,
        normalized_value=value,
        operation=operation,
        confidence=confidence,
        evidence=value,
        sensitivity=sensitivity,
        explicit=explicit,
    )


@pytest.fixture
def repository() -> MemoryRepository:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    repo = MemoryRepository(engine)
    repo.create_schema()
    return repo


def request(message_id: str, content: str = "test") -> IngestRequest:
    return IngestRequest(
        user_id="u-1",
        message_id=message_id,
        content=content,
        occurred_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )


def test_replace_preserves_history(repository: MemoryRepository) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(is_memory_worthy=True, candidates=[candidate("city", "北京")]),
            ExtractionResult(is_memory_worthy=True, candidates=[candidate("city", "杭州")]),
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("m-1")})
    result = graph.invoke({"request": request("m-2")})

    assert result["decisions"][0].action == "replaced"
    current = repository.list_facts("u-1")
    history = repository.list_facts("u-1", include_history=True)
    assert [fact.value for fact in current] == ["杭州"]
    assert len(history) == 2
    assert history[0].status == FactStatus.superseded


def test_append_deduplicates_active_fact(repository: MemoryRepository) -> None:
    hobby = candidate(
        "hobby",
        "徒步",
        category=FactCategory.preference,
        operation=MemoryOperation.append,
        sensitivity=Sensitivity.normal,
    )
    extractor = FakeExtractor(
        [
            ExtractionResult(is_memory_worthy=True, candidates=[hobby]),
            ExtractionResult(is_memory_worthy=True, candidates=[hobby]),
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("m-1")})
    result = graph.invoke({"request": request("m-2")})

    assert result["decisions"][0].action == "duplicate"
    assert len(repository.list_facts("u-1")) == 1


def test_preference_replace_is_normalized_to_append(
    repository: MemoryRepository,
) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate(
                        "hobby",
                        "骑行",
                        category=FactCategory.preference,
                        operation=MemoryOperation.append,
                        sensitivity=Sensitivity.normal,
                    )
                ],
            ),
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate(
                        "hobby",
                        "橄榄球",
                        category=FactCategory.preference,
                        operation=MemoryOperation.replace,
                        sensitivity=Sensitivity.normal,
                    )
                ],
            ),
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("hobby-1")})
    result = graph.invoke({"request": request("hobby-2")})

    assert result["decisions"][0].action == "appended"
    assert {fact.value for fact in repository.list_facts("u-1")} == {
        "骑行",
        "橄榄球",
    }


def test_policy_rejects_inferred_and_restricted_data(repository: MemoryRepository) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate("gender", "男", explicit=False),
                    candidate("password", "secret", sensitivity=Sensitivity.restricted),
                ],
            )
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    result = graph.invoke({"request": request("m-1")})

    assert len(result["rejected"]) == 2
    assert repository.list_facts("u-1") == []


def test_retract_deactivates_fact(repository: MemoryRepository) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(is_memory_worthy=True, candidates=[candidate("name", "陈晨")]),
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate("name", "陈晨", operation=MemoryOperation.retract)
                ],
            ),
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("m-1")})
    result = graph.invoke({"request": request("m-2")})

    assert result["decisions"][0].action == "retracted"
    assert repository.list_facts("u-1") == []
    history = repository.list_facts("u-1", include_history=True)
    assert history[0].status == FactStatus.retracted


def test_message_id_cannot_be_reused_for_different_content(
    repository: MemoryRepository,
) -> None:
    extractor = FakeExtractor(
        [ExtractionResult(is_memory_worthy=False), ExtractionResult(is_memory_worthy=False)]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("same-id", "第一条消息")})

    with pytest.raises(MessageConflictError):
        graph.invoke({"request": request("same-id", "另一条消息")})


def test_explicit_relation_appears_in_knowledge_graph(
    repository: MemoryRepository,
) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate(
                        "job_change",
                        "加入星海科技",
                        category=FactCategory.event,
                        operation=MemoryOperation.append,
                    ),
                    candidate("city", "杭州"),
                ],
                relations=[
                    RelationCandidate(
                        subject_key="job_change",
                        subject_normalized_value="加入星海科技",
                        predicate="happened_at",
                        object_key="city",
                        object_normalized_value="杭州",
                        confidence=0.93,
                        evidence="我在杭州加入了星海科技",
                    )
                ],
            )
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)

    graph.invoke({"request": request("relation-1")})
    knowledge_graph = repository.get_knowledge_graph("u-1")

    assert len(knowledge_graph.nodes) == 3
    relation_edges = [
        edge for edge in knowledge_graph.edges if edge.edge_type == "relation"
    ]
    assert len(relation_edges) == 1
    assert relation_edges[0].label == "happened_at"


def test_replacing_fact_deactivates_connected_relation(
    repository: MemoryRepository,
) -> None:
    first = ExtractionResult(
        is_memory_worthy=True,
        candidates=[
            candidate("occupation", "工程师"),
            candidate("city", "北京"),
        ],
        relations=[
            RelationCandidate(
                subject_key="occupation",
                subject_normalized_value="工程师",
                predicate="located_in",
                object_key="city",
                object_normalized_value="北京",
                confidence=0.9,
                evidence="我在北京做工程师",
            )
        ],
    )
    second = ExtractionResult(
        is_memory_worthy=True,
        candidates=[candidate("city", "杭州")],
    )
    graph = build_memory_graph(
        FakeExtractor([first, second]), repository, min_confidence=0.72
    )

    graph.invoke({"request": request("relation-1")})
    graph.invoke({"request": request("relation-2")})

    assert repository.list_relations("u-1") == []
    history = repository.list_relations("u-1", include_history=True)
    assert len(history) == 1
    assert history[0].status == FactStatus.superseded


def test_recall_is_scoped_and_expands_graph_neighbors(
    repository: MemoryRepository,
) -> None:
    extractor = FakeExtractor(
        [
            ExtractionResult(
                is_memory_worthy=True,
                candidates=[
                    candidate("occupation", "产品设计师"),
                    candidate("employer", "星海科技"),
                    candidate("city", "杭州"),
                ],
                relations=[
                    RelationCandidate(
                        subject_key="occupation",
                        subject_normalized_value="产品设计师",
                        predicate="works_at",
                        object_key="employer",
                        object_normalized_value="星海科技",
                        confidence=0.94,
                        evidence="我在星海科技做产品设计师",
                    ),
                    RelationCandidate(
                        subject_key="occupation",
                        subject_normalized_value="产品设计师",
                        predicate="located_in",
                        object_key="city",
                        object_normalized_value="杭州",
                        confidence=0.92,
                        evidence="我在杭州工作",
                    ),
                ],
            )
        ]
    )
    graph = build_memory_graph(extractor, repository, min_confidence=0.72)
    graph.invoke({"request": request("recall-1")})

    recalled = repository.recall(
        "u-1",
        RecallRequest(query="他在哪里工作，做什么职位？", limit=5),
    )

    values = {hit.fact.value for hit in recalled.hits}
    assert "产品设计师" in values
    assert "杭州" in values
    assert "星海科技" in values
    assert "任职于 -> 星海科技" in recalled.context
    assert repository.recall(
        "another-user", RecallRequest(query="在哪里工作")
    ).hits == []


