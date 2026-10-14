import json
from datetime import datetime, timezone

import httpx
import pytest

from moyo_memory.config import Settings
from moyo_memory.models import (
    FactCategory,
    FactStatus,
    FactView,
    RecallHit,
    Sensitivity,
)
from moyo_memory.reranking import (
    QwenTextReranker,
    RerankerError,
    create_reranker,
)


def make_hit(fact_id: str, value: str) -> RecallHit:
    now = datetime.now(timezone.utc)
    return RecallHit(
        fact=FactView(
            id=fact_id,
            user_id="u-1",
            category=FactCategory.preference,
            key="hobby",
            value=value,
            normalized_value=value,
            confidence=0.95,
            evidence=f"用户说喜欢{value}",
            source_message_id="m-1",
            sensitivity=Sensitivity.personal,
            status=FactStatus.active,
            valid_from=None,
            valid_to=None,
            created_at=now,
            updated_at=now,
        ),
        score=0.8,
        match_reasons=["语义相似"],
    )


def make_reranker(handler, **settings_overrides) -> QwenTextReranker:
    settings = Settings(
        _env_file=None,
        rerank_enabled=True,
        model_api_key="shared-key",
        rerank_endpoint="https://example.test/rerank",
        **settings_overrides,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return QwenTextReranker(settings, client=client)


def test_reranker_sends_dedicated_api_request_and_sorts_by_score() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.72},
                        {"index": 2, "relevance_score": 0.96},
                        {"index": 1, "relevance_score": 0.81},
                    ]
                }
            },
        )

    reranker = make_reranker(handler)
    hits = [make_hit("f-1", "徒步"), make_hit("f-2", "摄影"), make_hit("f-3", "跑步")]

    assert reranker.rerank("我有什么爱好", hits, 2) == ["f-3", "f-2"]
    assert captured["authorization"] == "Bearer shared-key"
    assert captured["payload"]["model"] == "qwen3.7-text-rerank"
    assert captured["payload"]["input"]["query"] == "我有什么爱好"
    assert len(captured["payload"]["input"]["documents"]) == 3
    assert "事实：徒步" in captured["payload"]["input"]["documents"][0]
    assert captured["payload"]["parameters"] == {
        "top_n": 2,
        "return_documents": False,
    }


def test_reranker_filters_results_below_minimum_score() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.19},
                        {"index": 1, "relevance_score": 0.20},
                    ]
                }
            },
        )

    hits = [make_hit("f-1", "徒步"), make_hit("f-2", "摄影")]
    assert make_reranker(handler).rerank("爱好", hits, 2) == ["f-2"]


def test_reranker_does_not_call_api_for_empty_candidates() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("empty candidates must not call rerank API")

    assert make_reranker(handler).rerank("爱好", [], 3) == []


@pytest.mark.parametrize(
    "item",
    [
        {"index": 2, "relevance_score": 0.9},
        {"index": 0, "relevance_score": "0.9"},
        {"index": 0, "relevance_score": float("nan")},
    ],
)
def test_reranker_rejects_invalid_results(item) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"results": [item]}})

    with pytest.raises(RerankerError):
        make_reranker(handler).rerank("爱好", [make_hit("f-1", "徒步")], 1)


def test_reranker_wraps_http_failures() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "temporarily unavailable"})

    with pytest.raises(RerankerError):
        make_reranker(handler).rerank("爱好", [make_hit("f-1", "徒步")], 1)


def test_create_reranker_uses_dedicated_implementation_and_key_fallback() -> None:
    disabled = Settings(_env_file=None, rerank_enabled=False)
    assert create_reranker(disabled) is None

    enabled = Settings(
        _env_file=None,
        rerank_enabled=True,
        model_api_key="shared-key",
        rerank_api_key="",
    )
    reranker = create_reranker(enabled)
    assert isinstance(reranker, QwenTextReranker)
    assert reranker.api_key == "shared-key"


def test_create_reranker_requires_an_api_key() -> None:
    settings = Settings(
        _env_file=None,
        rerank_enabled=True,
        model_api_key="",
        rerank_api_key="",
    )
    with pytest.raises(ValueError, match="RERANK_API_KEY or MODEL_API_KEY"):
        create_reranker(settings)
