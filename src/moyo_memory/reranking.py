import math
from typing import Protocol

import httpx

from moyo_memory.config import Settings
from moyo_memory.models import RecallHit, RecallResponse
from moyo_memory.retrieval import RELATION_LABELS


class RerankerError(RuntimeError):
    pass


class RecallReranker(Protocol):
    def rerank(self, query: str, hits: list[RecallHit], limit: int) -> list[str]: ...


class QwenTextReranker:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.model = settings.rerank_model
        self.endpoint = settings.rerank_endpoint
        self.api_key = settings.resolved_rerank_api_key
        self.min_score = settings.rerank_min_score
        self.client = client or httpx.Client(timeout=settings.rerank_timeout_seconds)

    @staticmethod
    def _document(hit: RecallHit) -> str:
        related = "；".join(
            f"{relation.predicate}:{relation.fact.value}" for relation in hit.related
        )
        parts = [
            f"类别：{hit.fact.category.value}",
            f"字段：{hit.fact.key}",
            f"事实：{hit.fact.value}",
            f"证据：{hit.fact.evidence}",
        ]
        if related:
            parts.append(f"关联：{related}")
        return "\n".join(parts)

    def rerank(self, query: str, hits: list[RecallHit], limit: int) -> list[str]:
        if not hits:
            return []
        documents = [self._document(hit) for hit in hits]
        try:
            response = self.client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": {"query": query, "documents": documents},
                    "parameters": {
                        "top_n": min(limit, len(documents)),
                        "return_documents": False,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = payload["output"]["results"]
            if not isinstance(results, list):
                raise ValueError("rerank results must be a list")

            scored_indices: list[tuple[float, int]] = []
            seen: set[int] = set()
            for item in results:
                index = item.get("index")
                score = item.get("relevance_score")
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or index < 0
                    or index >= len(hits)
                    or index in seen
                    or not isinstance(score, (int, float))
                    or isinstance(score, bool)
                    or not math.isfinite(float(score))
                ):
                    raise ValueError("invalid rerank result item")
                seen.add(index)
                if float(score) >= self.min_score:
                    scored_indices.append((float(score), index))
            scored_indices.sort(key=lambda item: (-item[0], item[1]))
            return [hits[index].fact.id for _, index in scored_indices[:limit]]
        except Exception as exc:
            raise RerankerError("recall reranker unavailable") from exc


def apply_reranking(
    response: RecallResponse, ordered_ids: list[str], limit: int
) -> RecallResponse:
    by_id = {hit.fact.id: hit for hit in response.hits}
    hits = [by_id[fact_id] for fact_id in ordered_ids if fact_id in by_id][:limit]
    selected_ids = {hit.fact.id for hit in hits}
    context_lines = []
    for hit in hits:
        context_lines.append(f"- {hit.fact.key}: {hit.fact.value}")
        for relation in hit.related:
            if relation.fact.id not in selected_ids or relation.direction != "outgoing":
                continue
            label = RELATION_LABELS.get(relation.predicate, relation.predicate)
            context_lines.append(f"  - {label} -> {relation.fact.value}")
    return response.model_copy(
        update={
            "hits": hits,
            "context": "\n".join(context_lines),
            "retrieval_mode": f"{response.retrieval_mode}_reranked",
        }
    )


def truncate_recall_response(
    response: RecallResponse,
    limit: int,
    *,
    warning: str | None = None,
) -> RecallResponse:
    """Trim a candidate-pool response while keeping its context consistent."""
    ordered_ids = [hit.fact.id for hit in response.hits[:limit]]
    trimmed = apply_reranking(response, ordered_ids, limit)
    warnings = response.warnings if warning is None else [*response.warnings, warning]
    return trimmed.model_copy(
        update={
            "retrieval_mode": response.retrieval_mode,
            "warnings": warnings,
        }
    )


def create_reranker(settings: Settings) -> RecallReranker | None:
    if not settings.rerank_enabled:
        return None
    if not settings.resolved_rerank_api_key:
        raise ValueError(
            "RERANK_ENABLED=true requires RERANK_API_KEY or MODEL_API_KEY"
        )
    return QwenTextReranker(settings)
