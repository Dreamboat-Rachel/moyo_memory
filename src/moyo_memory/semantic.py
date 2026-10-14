from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Protocol

from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient, models

from moyo_memory.config import Settings
from moyo_memory.models import FactCategory, FactView, SemanticMatch


class SemanticBackendError(RuntimeError):
    """Raised when the embedding or vector backend cannot serve a request."""


@dataclass(frozen=True)
class VectorDocument:
    fact_id: str
    user_id: str
    category: str
    key: str
    value: str
    evidence: str
    confidence: float
    vector: list[float]


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class VectorIndex(Protocol):
    def upsert(self, documents: list[VectorDocument]) -> None: ...

    def delete(self, fact_ids: list[str]) -> None: ...

    def search(
        self,
        *,
        user_id: str,
        vector: list[float],
        limit: int,
        categories: list[FactCategory] | None,
    ) -> list[SemanticMatch]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings):
        if not settings.resolved_embedding_api_key:
            raise ValueError(
                "SEMANTIC_RECALL_ENABLED=true 时必须配置 EMBEDDING_API_KEY "
                "或 MODEL_API_KEY"
            )
        self.client = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.resolved_embedding_api_key,
            base_url=settings.embedding_base_url,
            dimensions=settings.embedding_dimensions,
            request_timeout=settings.embedding_timeout_seconds,
            max_retries=1,
            check_embedding_ctx_length=False,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.client.embed_query(text)


class QdrantVectorIndex:
    def __init__(
        self,
        settings: Settings,
        *,
        client: QdrantClient | None = None,
    ):
        self.collection_name = settings.qdrant_collection
        self.dimensions = settings.embedding_dimensions
        self.client = client or QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=settings.qdrant_timeout_seconds,
        )
        self._collection_ready = False

    def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dimensions,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                ),
                on_disk_payload=True,
            )
            for field_name in ("user_id", "category", "status"):
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
        else:
            collection = self.client.get_collection(self.collection_name)
            vectors = collection.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is not None and size != self.dimensions:
                raise ValueError(
                    f"Qdrant collection dimension is {size}, expected {self.dimensions}"
                )
        self._collection_ready = True

    def upsert(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        self._ensure_collection()
        points = [
            models.PointStruct(
                id=document.fact_id,
                vector=document.vector,
                payload={
                    "user_id": document.user_id,
                    "category": document.category,
                    "key": document.key,
                    "value": document.value,
                    "evidence": document.evidence,
                    "confidence": document.confidence,
                    "status": "active",
                },
            )
            for document in documents
        ]
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    def delete(self, fact_ids: list[str]) -> None:
        if not fact_ids:
            return
        self._ensure_collection()
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=fact_ids),
            wait=True,
        )

    def search(
        self,
        *,
        user_id: str,
        vector: list[float],
        limit: int,
        categories: list[FactCategory] | None,
    ) -> list[SemanticMatch]:
        self._ensure_collection()
        conditions: list[models.Condition] = [
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id)
            ),
            models.FieldCondition(
                key="status", match=models.MatchValue(value="active")
            ),
        ]
        if categories:
            conditions.append(
                models.FieldCondition(
                    key="category",
                    match=models.MatchAny(any=[category.value for category in categories]),
                )
            )
        result = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=conditions),
            limit=limit,
            with_payload=False,
            with_vectors=False,
        )
        return [
            SemanticMatch(fact_id=str(point.id), score=float(point.score))
            for point in result.points
        ]


class SemanticRecallService:
    def __init__(
        self,
        embeddings: EmbeddingProvider,
        index: VectorIndex,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30,
    ):
        self.embeddings = embeddings
        self.index = index
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._open_until = 0.0
        self._lock = Lock()

    def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        categories: list[FactCategory] | None,
    ) -> list[SemanticMatch]:
        with self._lock:
            if monotonic() < self._open_until:
                raise SemanticBackendError("semantic recall circuit is open")
        try:
            vector = self.embeddings.embed_query(query)
            matches = self.index.search(
                user_id=user_id,
                vector=vector,
                limit=limit,
                categories=categories,
            )
        except Exception as exc:
            with self._lock:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._open_until = monotonic() + self.cooldown_seconds
            raise SemanticBackendError("semantic recall backend unavailable") from exc
        with self._lock:
            self._failure_count = 0
            self._open_until = 0.0
        return matches


def fact_embedding_text(fact: FactView) -> str:
    return (
        f"类别: {fact.category.value}\n"
        f"字段: {fact.key}\n"
        f"内容: {fact.value}\n"
        f"证据: {fact.evidence}"
    )


def create_semantic_components(
    settings: Settings,
) -> tuple[EmbeddingProvider, VectorIndex]:
    return OpenAIEmbeddingProvider(settings), QdrantVectorIndex(settings)


def create_semantic_recall_service(settings: Settings) -> SemanticRecallService | None:
    if not settings.semantic_recall_enabled:
        return None
    embeddings, index = create_semantic_components(settings)
    return SemanticRecallService(
        embeddings,
        index,
        failure_threshold=settings.semantic_failure_threshold,
        cooldown_seconds=settings.semantic_cooldown_seconds,
    )
