from contextlib import asynccontextmanager
import logging
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from moyo_memory.chat import (
    ChatGenerationError,
    ChatGenerator,
    ConversationService,
    create_chat_generator,
)
from moyo_memory.chat_preview import render_chat_preview
from moyo_memory.config import Settings, get_settings
from moyo_memory.db import MessageConflictError, MemoryRepository, create_db_engine
from moyo_memory.extractor import FactExtractor, ModelNotConfigured, create_extractor
from moyo_memory.graph import build_memory_graph
from moyo_memory.graph_preview import render_graph_preview
from moyo_memory.retrieval import infer_query_categories
from moyo_memory.models import (
    ChatRequest,
    ChatResponse,
    ConversationCreateRequest,
    ConversationHistoryResponse,
    ConversationView,
    DeleteResponse,
    FactCategory,
    FactView,
    IngestRequest,
    IngestResponse,
    KnowledgeGraphResponse,
    ManagedFactView,
    MemoryCorrectionRequest,
    MemoryPolicy,
    MemoryReviewRequest,
    MemoryStatsResponse,
    ProfileResponse,
    RecallRequest,
    RecallResponse,
    UserListResponse,
    AuditEventView,
)
from moyo_memory.reranking import (
    RecallReranker,
    RerankerError,
    apply_reranking,
    create_reranker,
    truncate_recall_response,
)
from moyo_memory.semantic import (
    SemanticBackendError,
    SemanticRecallService,
    create_semantic_recall_service,
)
from moyo_memory.security import verify_user_identity


logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    extractor: FactExtractor | None = None,
    semantic_retriever: SemanticRecallService | None = None,
    chat_generator: ChatGenerator | None = None,
    reranker: RecallReranker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url, echo=settings.sql_echo)
    repository = MemoryRepository(engine)
    extractor = extractor or create_extractor(settings)
    graph = build_memory_graph(
        extractor, repository, min_confidence=settings.memory_min_confidence
    )
    semantic_retriever = semantic_retriever or create_semantic_recall_service(settings)
    chat_generator = chat_generator or create_chat_generator(settings)
    reranker = reranker or create_reranker(settings)
    conversation_service = ConversationService(repository, chat_generator, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.app_env == "development":
            repository.create_schema()
        yield
        engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None if settings.app_env == "production" else "/redoc",
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    application.state.repository = repository
    application.state.memory_graph = graph
    application.state.semantic_retriever = semantic_retriever
    application.state.conversation_service = conversation_service
    application.state.reranker = reranker

    def admin_key_matches(value: str | None) -> bool:
        if not value or not settings.auth_admin_key:
            return False
        try:
            return secrets.compare_digest(value, settings.auth_admin_key)
        except TypeError:
            return False

    def authorize_user(
        user_id: str,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        x_user_timestamp: str | None = Header(default=None, alias="X-User-Timestamp"),
        x_user_signature: str | None = Header(default=None, alias="X-User-Signature"),
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ) -> str:
        if not settings.auth_enabled:
            return user_id
        if admin_key_matches(x_admin_key):
            return "admin"
        signed_identity_valid = (
            not settings.auth_user_signing_key
            or verify_user_identity(
                user_id,
                x_user_timestamp,
                x_user_signature,
                settings.auth_user_signing_key,
                ttl_seconds=settings.auth_signature_ttl_seconds,
            )
        )
        if (
            x_user_id
            and secrets.compare_digest(
                x_user_id.encode("utf-8"), user_id.encode("utf-8")
            )
            and signed_identity_valid
        ):
            return x_user_id
        raise HTTPException(status_code=403, detail="无权访问该用户的数据")

    def authorize_admin(
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ) -> str:
        if not settings.auth_enabled:
            return "local-admin"
        if admin_key_matches(x_admin_key):
            return "admin"
        raise HTTPException(status_code=403, detail="需要管理员权限")

    def recall_for_user(user_id: str, request: RecallRequest) -> RecallResponse:
        requested_limit = request.limit
        updates = {}
        if "min_score" not in request.model_fields_set:
            updates["min_score"] = settings.recall_min_score
        if "relative_score_threshold" not in request.model_fields_set:
            updates["relative_score_threshold"] = (
                settings.recall_relative_score_threshold
            )
        if "auto_category_filter" not in request.model_fields_set:
            updates["auto_category_filter"] = settings.recall_auto_category_filter
        request = request.model_copy(update=updates)
        if reranker is not None:
            request = request.model_copy(
                update={
                    "limit": min(
                        max(requested_limit * 3, requested_limit),
                        settings.rerank_max_candidates,
                    )
                }
            )
        search_categories = request.categories
        if search_categories is None and request.auto_category_filter:
            search_categories = infer_query_categories(request.query) or None
        semantic_matches = []
        semantic_available = False
        warnings: list[str] = []
        if request.use_semantic and semantic_retriever is not None:
            try:
                semantic_matches = semantic_retriever.search(
                    user_id=user_id,
                    query=request.query,
                    limit=max(request.limit * 4, 20),
                    categories=search_categories,
                )
                semantic_available = True
            except SemanticBackendError:
                logger.warning(
                    "Semantic recall unavailable; using lexical fallback",
                    exc_info=True,
                )
                warnings.append("semantic_backend_unavailable")
        elif request.use_semantic:
            warnings.append("semantic_recall_disabled")
        response = repository.recall(
            user_id,
            request,
            semantic_matches=semantic_matches,
            semantic_available=semantic_available,
            warnings=warnings,
        )
        if reranker is None or not response.hits:
            return truncate_recall_response(response, requested_limit)
        try:
            ordered_ids = reranker.rerank(
                request.query, response.hits, requested_limit
            )
            return apply_reranking(response, ordered_ids, requested_limit)
        except RerankerError:
            logger.warning("Recall reranker unavailable; using fused rank", exc_info=True)
            return truncate_recall_response(
                response,
                requested_limit,
                warning="reranker_unavailable",
            )

    @application.get("/health")
    def health() -> dict[str, str]:
        try:
            repository.ping()
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="数据库不可用") from exc
        return {
            "status": "ok",
            "semantic_recall": "enabled" if semantic_retriever else "disabled",
        }

    @application.get("/health/ready")
    def readiness() -> dict[str, object]:
        try:
            repository.ping()
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="数据库不可用") from exc
        dependencies = {"database": "ok"}
        checker = getattr(semantic_retriever, "healthcheck", None)
        if checker is not None:
            try:
                checker()
            except Exception as exc:
                logger.warning("Semantic backend readiness check failed", exc_info=True)
                raise HTTPException(status_code=503, detail="向量检索服务不可用") from exc
            dependencies["semantic_backend"] = "ok"
        elif semantic_retriever is not None:
            dependencies["semantic_backend"] = "configured_unchecked"
        else:
            dependencies["semantic_backend"] = "disabled"
        return {"status": "ready", "dependencies": dependencies}

    @application.get("/health/vector-index")
    def vector_index_health(
        _: str = Depends(authorize_admin),
    ) -> dict[str, object]:
        return {
            "semantic_recall_enabled": semantic_retriever is not None,
            "outbox": repository.vector_outbox_counts(),
        }

    @application.post("/v1/memories/extract", response_model=IngestResponse)
    def extract_memory(
        request: IngestRequest,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        x_user_timestamp: str | None = Header(default=None, alias="X-User-Timestamp"),
        x_user_signature: str | None = Header(default=None, alias="X-User-Signature"),
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ) -> IngestResponse:
        actor_id = authorize_user(
            request.user_id,
            x_user_id=x_user_id,
            x_user_timestamp=x_user_timestamp,
            x_user_signature=x_user_signature,
            x_admin_key=x_admin_key,
        )
        try:
            state = graph.invoke({"request": request})
        except ModelNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except MessageConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="数据库操作失败") from exc
        response = IngestResponse(
            message_id=request.message_id,
            memory_worthy=state.get("memory_worthy", False),
            decisions=state.get("decisions", []),
            rejected=state.get("rejected", []),
        )
        repository.add_audit_event(
            user_id=request.user_id,
            actor_id=actor_id,
            action="extract",
            resource_type="message",
            resource_id=request.message_id,
            detail=(
                f"decisions={len(response.decisions)};rejected={len(response.rejected)}"
            ),
        )
        return response

    @application.get("/v1/users", response_model=UserListResponse)
    def list_users(
        q: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: str = Depends(authorize_admin),
    ) -> UserListResponse:
        users, total = repository.list_users(
            query=q,
            limit=limit,
            offset=offset,
        )
        return UserListResponse(
            items=users,
            total=total,
            limit=limit,
            offset=offset,
        )

    @application.get("/v1/users/{user_id}/memories", response_model=list[FactView])
    def list_memories(
        user_id: str,
        include_history: bool = Query(default=False),
        _: str = Depends(authorize_user),
    ) -> list[FactView]:
        return repository.list_facts(user_id, include_history=include_history)

    @application.get("/v1/users/{user_id}/profile", response_model=ProfileResponse)
    def get_profile(
        user_id: str, _: str = Depends(authorize_user)
    ) -> ProfileResponse:
        facts = repository.list_facts(user_id)
        profile: dict[str, str] = {}
        preferences: dict[str, list[str]] = {}
        events: list[FactView] = []
        for fact in facts:
            if fact.category == FactCategory.profile:
                profile[fact.key] = fact.value
            elif fact.category == FactCategory.preference:
                preferences.setdefault(fact.key, []).append(fact.value)
            else:
                events.append(fact)
        return ProfileResponse(
            user_id=user_id,
            profile=profile,
            preferences=preferences,
            events=events,
        )

    @application.get(
        "/v1/users/{user_id}/knowledge-graph", response_model=KnowledgeGraphResponse
    )
    def get_knowledge_graph(
        user_id: str, _: str = Depends(authorize_user)
    ) -> KnowledgeGraphResponse:
        return repository.get_knowledge_graph(user_id)

    @application.post(
        "/v1/users/{user_id}/recall", response_model=RecallResponse
    )
    def recall_user_knowledge(
        user_id: str,
        request: RecallRequest,
        _: str = Depends(authorize_user),
    ) -> RecallResponse:
        return recall_for_user(user_id, request)

    @application.post(
        "/v1/users/{user_id}/conversations",
        response_model=ConversationView,
        status_code=201,
    )
    def create_conversation(
        user_id: str,
        request: ConversationCreateRequest,
        _: str = Depends(authorize_user),
    ) -> ConversationView:
        return repository.create_conversation(user_id, request.title)

    @application.get(
        "/v1/users/{user_id}/conversations",
        response_model=list[ConversationView],
    )
    def list_conversations(
        user_id: str, _: str = Depends(authorize_user)
    ) -> list[ConversationView]:
        return repository.list_conversations(user_id)

    @application.get(
        "/v1/users/{user_id}/conversations/{conversation_id}",
        response_model=ConversationHistoryResponse,
    )
    def get_conversation_history(
        user_id: str,
        conversation_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        before_sequence: int | None = Query(default=None, ge=1),
        _: str = Depends(authorize_user),
    ) -> ConversationHistoryResponse:
        conversation = repository.get_conversation(user_id, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = repository.list_conversation_messages(
            user_id,
            conversation_id,
            before_sequence=before_sequence,
            limit=limit,
        )
        return ConversationHistoryResponse(
            conversation=conversation,
            messages=messages,
        )

    @application.post(
        "/v1/users/{user_id}/conversations/{conversation_id}/messages",
        response_model=ChatResponse,
    )
    def send_conversation_message(
        user_id: str,
        conversation_id: str,
        request: ChatRequest,
        actor_id: str = Depends(authorize_user),
    ) -> ChatResponse:
        recall = recall_for_user(
            user_id,
            RecallRequest(query=request.content, limit=8),
        )
        try:
            result = conversation_service.send_message(
                user_id,
                conversation_id,
                request,
                recall,
                lambda ingest_request: graph.invoke({"request": ingest_request}),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ModelNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ChatGenerationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if result.memory_decisions or result.memory_rejected:
            repository.add_audit_event(
                user_id=user_id,
                actor_id=actor_id,
                action="extract",
                resource_type="conversation_message",
                resource_id=request.message_id,
                detail=(
                    f"decisions={len(result.memory_decisions)};"
                    f"rejected={len(result.memory_rejected)}"
                ),
            )
        return ChatResponse(
            conversation_id=conversation_id,
            user_message=result.user_message,
            assistant_message=result.assistant_message,
            memory_context=recall.context,
            retrieval_mode=recall.retrieval_mode,
            warnings=[*recall.warnings, *result.warnings],
            memory_decisions=result.memory_decisions,
            memory_rejected=result.memory_rejected,
        )

    @application.delete(
        "/v1/users/{user_id}/conversations/{conversation_id}"
    )
    def delete_conversation(
        user_id: str,
        conversation_id: str,
        actor_id: str = Depends(authorize_user),
    ) -> dict[str, int]:
        conversations, messages = repository.delete_conversation(
            user_id, conversation_id
        )
        if conversations == 0:
            raise HTTPException(status_code=404, detail="会话不存在")
        repository.add_audit_event(
            user_id=user_id,
            actor_id=actor_id,
            action="delete",
            resource_type="conversation",
            resource_id=conversation_id,
            detail=f"messages={messages}",
        )
        return {
            "deleted_conversations": conversations,
            "deleted_messages": messages,
        }

    @application.get("/graph/{user_id}", response_class=HTMLResponse)
    def knowledge_graph_preview(
        user_id: str, _: str = Depends(authorize_user)
    ) -> HTMLResponse:
        return HTMLResponse(render_graph_preview(user_id))

    @application.get("/chat/{user_id}", response_class=HTMLResponse)
    def chat_preview(
        user_id: str, _: str = Depends(authorize_user)
    ) -> HTMLResponse:
        return HTMLResponse(render_chat_preview(user_id))

    @application.delete("/v1/users/{user_id}/memories", response_model=DeleteResponse)
    def delete_memories(
        user_id: str, actor_id: str = Depends(authorize_user)
    ) -> DeleteResponse:
        relations, facts, messages, conversations, chat_messages = (
            repository.delete_user(user_id)
        )
        response = DeleteResponse(
            user_id=user_id,
            deleted_relations=relations,
            deleted_facts=facts,
            deleted_messages=messages,
            deleted_conversations=conversations,
            deleted_chat_messages=chat_messages,
        )
        repository.add_audit_event(
            user_id=user_id,
            actor_id=actor_id,
            action="delete_all",
            resource_type="user_memory",
            resource_id=user_id,
            detail=(
                f"facts={facts};relations={relations};messages={messages};"
                f"conversations={conversations};chat_messages={chat_messages}"
            ),
        )
        return response

    @application.get(
        "/v1/users/{user_id}/memory-items",
        response_model=list[ManagedFactView],
    )
    def list_memory_items(
        user_id: str,
        include_history: bool = Query(default=False),
        include_rejected: bool = Query(default=False),
        _: str = Depends(authorize_user),
    ) -> list[ManagedFactView]:
        return repository.list_managed_facts(
            user_id,
            include_history=include_history,
            include_rejected=include_rejected,
        )

    @application.get(
        "/v1/users/{user_id}/memory-policy", response_model=MemoryPolicy
    )
    def get_memory_policy(
        user_id: str, _: str = Depends(authorize_user)
    ) -> MemoryPolicy:
        return repository.get_memory_policy(user_id)

    @application.put(
        "/v1/users/{user_id}/memory-policy", response_model=MemoryPolicy
    )
    def update_memory_policy(
        user_id: str,
        policy: MemoryPolicy,
        actor_id: str = Depends(authorize_user),
    ) -> MemoryPolicy:
        result = repository.set_memory_policy(user_id, policy)
        repository.add_audit_event(
            user_id=user_id,
            actor_id=actor_id,
            action="update",
            resource_type="memory_policy",
            resource_id=user_id,
            detail=(
                f"enabled={policy.enabled};categories={len(policy.allowed_categories)};"
                f"blocked_keys={len(policy.blocked_keys)};"
                f"confirm_sensitive={policy.require_confirmation_sensitive}"
            ),
        )
        return result

    @application.post(
        "/v1/users/{user_id}/memories/{fact_id}/review",
        response_model=ManagedFactView,
    )
    def review_memory(
        user_id: str,
        fact_id: str,
        request: MemoryReviewRequest,
        actor_id: str = Depends(authorize_user),
    ) -> ManagedFactView:
        fact = repository.update_fact_review(user_id, fact_id, request.state)
        if fact is None:
            raise HTTPException(status_code=404, detail="记忆不存在或已完成审核")
        repository.add_audit_event(
            user_id=user_id,
            actor_id=actor_id,
            action=request.state.value,
            resource_type="memory_fact",
            resource_id=fact_id,
            detail="Sensitive memory review completed",
        )
        return ManagedFactView(
            **fact.model_dump(), review_state=request.state
        )

    @application.patch(
        "/v1/users/{user_id}/memories/{fact_id}", response_model=FactView
    )
    def correct_memory(
        user_id: str,
        fact_id: str,
        request: MemoryCorrectionRequest,
        actor_id: str = Depends(authorize_user),
    ) -> FactView:
        fact = repository.correct_fact(
            user_id,
            fact_id,
            value=request.value,
            normalized_value=request.value.casefold(),
            valid_from=request.valid_from,
            valid_to=request.valid_to,
            actor_id=actor_id,
        )
        if fact is None:
            raise HTTPException(status_code=404, detail="活跃记忆不存在")
        return fact

    @application.delete("/v1/users/{user_id}/memories/{fact_id}")
    def delete_memory_item(
        user_id: str,
        fact_id: str,
        actor_id: str = Depends(authorize_user),
    ) -> dict[str, bool]:
        if not repository.delete_fact(user_id, fact_id, actor_id=actor_id):
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"deleted": True}

    @application.get(
        "/v1/users/{user_id}/memory-audit", response_model=list[AuditEventView]
    )
    def list_memory_audit(
        user_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: str = Depends(authorize_user),
    ) -> list[AuditEventView]:
        return repository.list_audit_events(user_id, limit=limit, offset=offset)

    @application.get(
        "/v1/users/{user_id}/memory-stats", response_model=MemoryStatsResponse
    )
    def get_memory_stats(
        user_id: str, _: str = Depends(authorize_user)
    ) -> MemoryStatsResponse:
        return repository.memory_stats(user_id)

    @application.get("/v1/users/{user_id}/memory-index-status")
    def get_memory_index_status(
        user_id: str, _: str = Depends(authorize_user)
    ) -> dict[str, object]:
        return {
            "semantic_recall_enabled": semantic_retriever is not None,
            "outbox": repository.vector_outbox_counts(user_id),
        }

    @application.get("/health/metrics", response_model=MemoryStatsResponse)
    def memory_metrics(_: str = Depends(authorize_admin)) -> MemoryStatsResponse:
        return repository.memory_stats()

    return application


app = create_app()

