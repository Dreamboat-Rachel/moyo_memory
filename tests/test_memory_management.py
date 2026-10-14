from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from moyo_memory.api import create_app
from moyo_memory.config import Settings
from moyo_memory.db import MemoryRepository, create_db_engine
from moyo_memory.graph import build_memory_graph
from moyo_memory.models import (
    ExtractionResult,
    FactCandidate,
    FactCategory,
    FactStatus,
    IngestRequest,
    MemoryOperation,
    MemoryPolicy,
    MemoryReviewState,
    RecallRequest,
    Sensitivity,
)
from moyo_memory.reranking import RerankerError


class QueueExtractor:
    def __init__(self, results: list[ExtractionResult]):
        self.results = iter(results)

    def extract(self, request: IngestRequest) -> ExtractionResult:
        return next(self.results)


def make_candidate(
    key: str,
    value: str,
    *,
    category: FactCategory = FactCategory.profile,
    sensitivity: Sensitivity = Sensitivity.personal,
    operation: MemoryOperation = MemoryOperation.replace,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> FactCandidate:
    return FactCandidate(
        category=category,
        key=key,
        value=value,
        normalized_value=value,
        operation=operation,
        confidence=0.96,
        evidence="explicit evidence",
        sensitivity=sensitivity,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def make_request(message_id: str, user_id: str = "u-1") -> IngestRequest:
    return IngestRequest(
        user_id=user_id,
        message_id=message_id,
        content="用户明确提供的信息",
        occurred_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def repository() -> MemoryRepository:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    result = MemoryRepository(engine)
    result.create_schema()
    return result


def run_graph(
    repository: MemoryRepository,
    candidates: list[FactCandidate],
    *,
    message_id: str = "m-1",
):
    graph = build_memory_graph(
        QueueExtractor(
            [ExtractionResult(is_memory_worthy=True, candidates=candidates)]
        ),
        repository,
        min_confidence=0.72,
    )
    return graph.invoke({"request": make_request(message_id)})


def test_policy_can_disable_or_filter_memory(repository: MemoryRepository) -> None:
    repository.set_memory_policy(
        "u-1",
        MemoryPolicy(
            enabled=False,
            allowed_categories=list(FactCategory),
            blocked_keys=[],
        ),
    )
    disabled = run_graph(repository, [make_candidate("name", "陈晨")])
    assert disabled["decisions"] == []
    assert disabled["rejected"][0].reason == "用户已关闭长期记忆"

    repository.set_memory_policy(
        "u-1",
        MemoryPolicy(
            enabled=True,
            allowed_categories=[FactCategory.profile],
            blocked_keys=["city"],
        ),
    )
    filtered = run_graph(
        repository,
        [
            make_candidate("city", "杭州"),
            make_candidate("hobby", "徒步", category=FactCategory.preference),
        ],
        message_id="m-2",
    )
    assert {item.reason for item in filtered["rejected"]} == {
        "用户策略禁止保存该字段",
        "用户策略禁止保存该类记忆",
    }
    assert repository.list_facts("u-1") == []


def test_sensitive_fact_is_hidden_until_confirmed(repository: MemoryRepository) -> None:
    result = run_graph(
        repository,
        [make_candidate("birthday", "1990-01-01", sensitivity=Sensitivity.sensitive)],
    )
    fact_id = result["decisions"][0].fact_id
    assert result["decisions"][0].action == "pending_confirmation"
    assert repository.list_facts("u-1") == []
    assert repository.recall("u-1", RecallRequest(query="生日", min_score=0)).hits == []
    assert repository.vector_outbox_counts() == {}
    assert repository.list_managed_facts("u-1")[0].review_state == MemoryReviewState.pending
    assert repository.memory_stats("u-1").active == 0
    assert repository.memory_stats("u-1").pending_confirmation == 1

    confirmed = repository.update_fact_review(
        "u-1", fact_id, MemoryReviewState.confirmed
    )
    assert confirmed is not None
    assert [fact.value for fact in repository.list_facts("u-1")] == ["1990-01-01"]
    assert repository.vector_outbox_counts() == {"pending": 1}
    assert repository.memory_stats("u-1").active == 1


def test_rejecting_sensitive_fact_retracts_it(repository: MemoryRepository) -> None:
    result = run_graph(
        repository,
        [make_candidate("birthday", "1990-01-01", sensitivity=Sensitivity.sensitive)],
    )
    fact_id = result["decisions"][0].fact_id
    rejected = repository.update_fact_review(
        "u-1", fact_id, MemoryReviewState.rejected
    )
    assert rejected is not None
    assert rejected.status == FactStatus.retracted
    assert repository.list_facts("u-1") == []


def test_pending_replace_does_not_hide_current_fact(repository: MemoryRepository) -> None:
    run_graph(repository, [make_candidate("city", "北京")])
    pending = run_graph(
        repository,
        [make_candidate("city", "上海", sensitivity=Sensitivity.sensitive)],
        message_id="m-2",
    )
    assert [fact.value for fact in repository.list_facts("u-1")] == ["北京"]

    repository.update_fact_review(
        "u-1", pending["decisions"][0].fact_id, MemoryReviewState.confirmed
    )
    assert [fact.value for fact in repository.list_facts("u-1")] == ["上海"]


def test_old_and_expired_facts_are_not_current(repository: MemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    run_graph(
        repository,
        [make_candidate("city", "杭州", valid_from=now)],
    )
    historical = run_graph(
        repository,
        [make_candidate("city", "北京", valid_from=now - timedelta(days=365))],
        message_id="m-2",
    )
    assert historical["decisions"][0].action == "historical"
    assert [fact.value for fact in repository.list_facts("u-1")] == ["杭州"]

    run_graph(
        repository,
        [
            make_candidate(
                "membership",
                "旧会员",
                operation=MemoryOperation.append,
                valid_from=now - timedelta(days=10),
                valid_to=now - timedelta(days=1),
            )
        ],
        message_id="m-3",
    )
    assert "旧会员" not in [fact.value for fact in repository.list_facts("u-1")]


def test_correction_and_delete_keep_history(repository: MemoryRepository) -> None:
    created = run_graph(repository, [make_candidate("city", "北京")])
    old_id = created["decisions"][0].fact_id
    corrected = repository.correct_fact(
        "u-1",
        old_id,
        value="杭州",
        normalized_value="杭州",
        valid_from=None,
        valid_to=None,
        actor_id="u-1",
    )
    assert corrected is not None
    assert [fact.value for fact in repository.list_facts("u-1")] == ["杭州"]
    history = repository.list_facts("u-1", include_history=True)
    assert {fact.status for fact in history} == {
        FactStatus.active,
        FactStatus.superseded,
    }
    assert repository.delete_fact("u-1", corrected.id, actor_id="u-1")
    assert repository.list_facts("u-1") == []


def test_vector_outbox_counts_are_user_scoped(repository: MemoryRepository) -> None:
    run_graph(repository, [make_candidate("city", "北京")])
    graph = build_memory_graph(
        QueueExtractor(
            [
                ExtractionResult(
                    is_memory_worthy=True,
                    candidates=[make_candidate("city", "上海")],
                )
            ]
        ),
        repository,
        min_confidence=0.72,
    )
    graph.invoke({"request": make_request("m-other", user_id="u-2")})
    assert repository.vector_outbox_counts("u-1") == {"pending": 1}
    assert repository.vector_outbox_counts("u-2") == {"pending": 1}
    assert repository.vector_outbox_counts() == {"pending": 2}


class TwoFactExtractor:
    def extract(self, request: IngestRequest) -> ExtractionResult:
        return ExtractionResult(
            is_memory_worthy=True,
            candidates=[
                make_candidate("name", "陈晨"),
                make_candidate("city", "杭州", operation=MemoryOperation.append),
            ],
        )


class ReverseReranker:
    def rerank(self, query, hits, limit):
        return [hit.fact.id for hit in reversed(hits)]


class FailingReranker:
    def rerank(self, query, hits, limit):
        raise RerankerError("failed")


def test_auth_management_audit_metrics_and_reranking(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'managed.db').as_posix()}",
        auth_enabled=True,
        auth_admin_key="admin-secret",
        recall_min_score=0,
    )
    app = create_app(settings, extractor=TwoFactExtractor(), reranker=ReverseReranker())
    user_headers = {"X-User-ID": "user-a"}
    with TestClient(app) as client:
        ingested = client.post(
            "/v1/memories/extract",
            headers=user_headers,
            json={"user_id": "user-a", "message_id": "m-1", "content": "资料"},
        )
        assert ingested.status_code == 200
        assert client.get("/v1/users/user-a/memory-items", headers=user_headers).status_code == 200
        assert client.get(
            "/v1/users/user-a/memory-items", headers={"X-User-ID": "user-b"}
        ).status_code == 403
        assert client.get("/v1/users", headers=user_headers).status_code == 403
        assert client.get(
            "/v1/users", headers={"X-Admin-Key": "admin-secret"}
        ).status_code == 200

        recall = client.post(
            "/v1/users/user-a/recall",
            headers=user_headers,
            json={
                "query": "姓名城市陈晨杭州",
                "limit": 1,
                "auto_category_filter": False,
            },
        ).json()
        assert len(recall["hits"]) == 1
        assert recall["context"].count("\n") == 0
        assert recall["retrieval_mode"].endswith("_reranked")

        policy = client.put(
            "/v1/users/user-a/memory-policy",
            headers=user_headers,
            json={"enabled": False},
        )
        assert policy.status_code == 200
        audit = client.get(
            "/v1/users/user-a/memory-audit", headers=user_headers
        ).json()
        assert {item["action"] for item in audit} >= {"extract", "update"}
        assert all("陈晨" not in (item["detail"] or "") for item in audit)
        assert client.get(
            "/health/metrics", headers={"X-Admin-Key": "admin-secret"}
        ).status_code == 200


def test_reranker_failure_rebuilds_trimmed_context(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'fallback.db').as_posix()}",
        recall_min_score=0,
    )
    app = create_app(settings, extractor=TwoFactExtractor(), reranker=FailingReranker())
    with TestClient(app) as client:
        client.post(
            "/v1/memories/extract",
            json={"user_id": "u-1", "message_id": "m-1", "content": "资料"},
        )
        response = client.post(
            "/v1/users/u-1/recall",
            json={
                "query": "姓名城市陈晨杭州",
                "limit": 1,
                "auto_category_filter": False,
            },
        ).json()
    assert len(response["hits"]) == 1
    assert response["context"].count("\n") == 0
    assert "reranker_unavailable" in response["warnings"]

