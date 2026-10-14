from fastapi.testclient import TestClient

from moyo_memory.api import create_app
from moyo_memory.config import Settings
from moyo_memory.models import (
    ExtractionResult,
    FactCandidate,
    FactCategory,
    MemoryOperation,
    Sensitivity,
)
from moyo_memory.semantic import SemanticBackendError


class NameExtractor:
    def extract(self, request):
        return ExtractionResult(
            is_memory_worthy=True,
            candidates=[
                FactCandidate(
                    category=FactCategory.profile,
                    key="name",
                    value="陈晨",
                    normalized_value="陈晨",
                    operation=MemoryOperation.replace,
                    confidence=0.98,
                    evidence="我叫陈晨",
                    sensitivity=Sensitivity.personal,
                )
            ],
        )


class FailingOnceNameExtractor(NameExtractor):
    def __init__(self):
        self.calls = 0

    def extract(self, request):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary extraction failure")
        return super().extract(request)


class FailingSemanticRetriever:
    def search(self, **kwargs):
        raise SemanticBackendError("unavailable")


class FakeChatGenerator:
    def __init__(self):
        self.answer_calls = []
        self.summary_calls = []

    def answer(self, system_prompt, history, user_content):
        self.answer_calls.append((system_prompt, history, user_content))
        return f"回复：{user_content}"

    def summarize(self, existing_summary, messages):
        self.summary_calls.append((existing_summary, messages))
        return "较早的对话摘要"


def test_recall_applies_config_and_passes_intent_to_vector_search(tmp_path):
    class RecordingRetriever:
        def __init__(self):
            self.calls = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            return []

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'recall.db').as_posix()}",
        recall_min_score=1.2,
    )
    retriever = RecordingRetriever()
    app = create_app(settings, extractor=NameExtractor(), semantic_retriever=retriever)
    with TestClient(app) as client:
        client.post("/v1/memories/extract", json={
            "user_id": "u-1", "message_id": "m-1", "content": "我叫陈晨",
        })
        filtered = client.post("/v1/users/u-1/recall", json={"query": "我叫什么名字"})
        assert filtered.json()["hits"] == []
        assert filtered.json()["score_threshold"] == 1.2
        overridden = client.post("/v1/users/u-1/recall", json={
            "query": "我叫什么名字", "min_score": 0,
        })
        assert overridden.json()["hits"][0]["fact"]["value"] == "陈晨"
        client.post("/v1/users/u-1/recall", json={"query": "我有哪些爱好"})
        assert retriever.calls[-1]["categories"] == [FactCategory.preference]
        assert retriever.calls[-1]["user_id"] == "u-1"
        client.post("/v1/users/u-1/recall", json={
            "query": "我有哪些爱好", "auto_category_filter": False,
        })
        assert retriever.calls[-1]["categories"] is None
        assert client.post("/v1/users/u-1/recall", json={"query": "  "}).status_code == 422


def test_ingest_profile_and_delete_endpoints(tmp_path) -> None:
    database_path = (tmp_path / "api.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{database_path}",
    )
    app = create_app(settings, extractor=NameExtractor())

    with TestClient(app) as client:
        ingest = client.post(
            "/v1/memories/extract",
            json={
                "user_id": "u-api",
                "message_id": "m-api",
                "content": "我叫陈晨",
            },
        )
        assert ingest.status_code == 200
        assert ingest.json()["decisions"][0]["action"] == "created"

        profile = client.get("/v1/users/u-api/profile")
        assert profile.status_code == 200
        assert profile.json()["profile"] == {"name": "陈晨"}

        graph = client.get("/v1/users/u-api/knowledge-graph")
        assert graph.status_code == 200
        assert len(graph.json()["nodes"]) == 2
        assert len(graph.json()["edges"]) == 1

        preview = client.get("/graph/u-api")
        assert preview.status_code == 200
        assert "用户知识图谱" in preview.text
        assert "sync-status" in preview.text
        assert "user-switcher" in preview.text
        assert "user-id-select" in preview.text
        assert "/v1/users?limit=500" in preview.text
        assert "setInterval(refreshGraph, 3000)" in preview.text

        chat_preview = client.get("/chat/u-api")
        assert chat_preview.status_code == 200
        assert "Moyo Memory" in chat_preview.text
        assert "conversation-list" in chat_preview.text
        assert "engine-status" in chat_preview.text
        assert "user-switcher" in chat_preview.text
        assert "user-id-select" in chat_preview.text
        assert "/v1/users?limit=500" in chat_preview.text
        assert "setInterval(refreshVectorStatus,5000)" in chat_preview.text

        recall = client.post(
            "/v1/users/u-api/recall",
            json={"query": "用户叫什么名字？", "limit": 3},
        )
        assert recall.status_code == 200
        assert recall.json()["hits"][0]["fact"]["value"] == "陈晨"

        client.post("/v1/users/conversation-only/conversations", json={})
        users = client.get("/v1/users", params={"limit": 1})
        assert users.status_code == 200
        assert users.json()["total"] == 2
        assert len(users.json()["items"]) == 1

        filtered_users = client.get("/v1/users", params={"q": "u-api"})
        assert filtered_users.status_code == 200
        assert filtered_users.json()["total"] == 1
        assert filtered_users.json()["items"][0]["user_id"] == "u-api"
        assert filtered_users.json()["items"][0]["memory_count"] == 1

        deleted = client.delete("/v1/users/u-api/memories")
        assert deleted.status_code == 200
        assert deleted.json()["deleted_relations"] == 0
        assert deleted.json()["deleted_facts"] == 1
        assert deleted.json()["deleted_messages"] == 1


def test_recall_falls_back_when_vector_backend_is_unavailable(tmp_path) -> None:
    database_path = (tmp_path / "fallback.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{database_path}",
    )
    app = create_app(
        settings,
        extractor=NameExtractor(),
        semantic_retriever=FailingSemanticRetriever(),
    )

    with TestClient(app) as client:
        client.post(
            "/v1/memories/extract",
            json={
                "user_id": "u-fallback",
                "message_id": "m-fallback",
                "content": "我叫陈晨",
            },
        )
        response = client.post(
            "/v1/users/u-fallback/recall",
            json={"query": "用户叫什么名字？"},
        )

    assert response.status_code == 200
    assert response.json()["retrieval_mode"] == "lexical_graph"
    assert response.json()["warnings"] == ["semantic_backend_unavailable"]
    assert response.json()["hits"][0]["fact"]["value"] == "陈晨"


def test_conversation_is_idempotent_and_user_scoped(tmp_path) -> None:
    database_path = (tmp_path / "conversation.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{database_path}",
    )
    generator = FakeChatGenerator()
    app = create_app(
        settings,
        extractor=NameExtractor(),
        chat_generator=generator,
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/users/user-a/conversations",
            json={"title": "测试会话"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        first = client.post(
            f"/v1/users/user-a/conversations/{conversation_id}/messages",
            json={"message_id": "client-1", "content": "我叫陈晨"},
        )
        assert first.status_code == 200
        assert first.json()["assistant_message"]["content"] == "回复：我叫陈晨"
        assert first.json()["memory_decisions"][0]["action"] == "created"

        repeated = client.post(
            f"/v1/users/user-a/conversations/{conversation_id}/messages",
            json={"message_id": "client-1", "content": "我叫陈晨"},
        )
        assert repeated.status_code == 200
        assert "duplicate_message_id" in repeated.json()["warnings"]
        assert len(generator.answer_calls) == 1

        history = client.get(
            f"/v1/users/user-a/conversations/{conversation_id}"
        )
        assert history.status_code == 200
        assert [item["role"] for item in history.json()["messages"]] == [
            "user",
            "assistant",
        ]

        assert (
            client.get(f"/v1/users/user-b/conversations/{conversation_id}").status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/users/user-b/conversations/{conversation_id}/messages",
                json={"message_id": "client-2", "content": "读取其他用户"},
            ).status_code
            == 404
        )


def test_duplicate_message_retries_failed_memory_extraction(tmp_path) -> None:
    database_path = (tmp_path / "conversation-retry.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{database_path}",
    )
    extractor = FailingOnceNameExtractor()
    generator = FakeChatGenerator()
    app = create_app(
        settings,
        extractor=extractor,
        chat_generator=generator,
    )

    with TestClient(app) as client:
        conversation_id = client.post(
            "/v1/users/user-retry/conversations", json={}
        ).json()["id"]
        endpoint = (
            f"/v1/users/user-retry/conversations/{conversation_id}/messages"
        )
        payload = {"message_id": "retry-client-message", "content": "我叫陈晨"}

        first = client.post(endpoint, json=payload)
        repeated = client.post(endpoint, json=payload)
        memories = client.get("/v1/users/user-retry/memories")

    assert first.status_code == 200
    assert "memory_extraction_failed" in first.json()["warnings"]
    assert repeated.status_code == 200
    assert repeated.json()["memory_decisions"][0]["action"] == "created"
    assert "duplicate_message_id" in repeated.json()["warnings"]
    assert [fact["value"] for fact in memories.json()] == ["陈晨"]
    assert extractor.calls == 2
    assert len(generator.answer_calls) == 1


def test_conversation_rolls_old_turns_into_summary(tmp_path) -> None:
    database_path = (tmp_path / "summary.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+pysqlite:///{database_path}",
        chat_history_max_turns=1,
        chat_context_max_tokens=4096,
        chat_max_output_tokens=512,
    )
    generator = FakeChatGenerator()
    app = create_app(
        settings,
        extractor=NameExtractor(),
        chat_generator=generator,
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/users/user-summary/conversations", json={}
        ).json()
        conversation_id = created["id"]
        for index in range(3):
            response = client.post(
                f"/v1/users/user-summary/conversations/{conversation_id}/messages",
                json={
                    "message_id": f"message-{index}",
                    "content": f"第{index + 1}轮对话",
                },
            )
            assert response.status_code == 200

        history = client.get(
            f"/v1/users/user-summary/conversations/{conversation_id}"
        ).json()

    assert generator.summary_calls
    assert history["conversation"]["summary"] == "较早的对话摘要"
    assert history["conversation"]["summary_through_sequence"] >= 2
    assert len(generator.answer_calls[-1][1]) <= 2

