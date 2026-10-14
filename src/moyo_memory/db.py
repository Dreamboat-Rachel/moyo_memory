from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    text,
    union_all,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from moyo_memory.models import (
    ChatRole,
    ConversationMessageView,
    ConversationView,
    FactCandidate,
    FactStatus,
    FactView,
    GraphEdge,
    GraphNode,
    IngestRequest,
    KnowledgeGraphResponse,
    ManagedFactView,
    MemoryDecision,
    MemoryOperation,
    MemoryPolicy,
    MemoryReviewState,
    MemoryStatsResponse,
    RelationCandidate,
    RelationView,
    RecallRequest,
    RecallResponse,
    SemanticMatch,
    Sensitivity,
    AuditEventView,
    UserSummary,
)
from moyo_memory.retrieval import recall_memories


class MessageConflictError(ValueError):
    """Raised when a message id is reused with different content."""


@dataclass(frozen=True)
class VectorOutboxItem:
    id: str
    fact_id: str
    user_id: str
    operation: str
    attempts: int


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class MessageRecord(Base):
    __tablename__ = "messages"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class MemoryExtractionRecord(Base):
    __tablename__ = "memory_extractions"
    __table_args__ = (Index("ix_memory_extraction_status", "user_id", "status"),)

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


class ConversationRecord(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversation_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )

    def to_view(self) -> ConversationView:
        return ConversationView(
            id=self.id,
            user_id=self.user_id,
            title=self.title,
            summary=self.summary,
            summary_through_sequence=self.summary_through_sequence,
            last_sequence=self.last_sequence,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_message_sequence"
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_conversation_client_message",
        ),
        Index(
            "ix_conversation_message_user",
            "user_id",
            "conversation_id",
            "sequence",
        ),
        Index("ix_conversation_message_reply", "reply_to_message_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )

    def to_view(self) -> ConversationMessageView:
        return ConversationMessageView(
            id=self.id,
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=self.role,
            content=self.content,
            sequence=self.sequence,
            client_message_id=self.client_message_id,
            reply_to_message_id=self.reply_to_message_id,
            created_at=self.created_at,
        )


class MemoryFactRecord(Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("ix_memory_user_status", "user_id", "status"),
        Index("ix_memory_user_key", "user_id", "category", "fact_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )

    def to_view(self) -> FactView:
        return FactView(
            id=self.id,
            user_id=self.user_id,
            category=self.category,
            key=self.fact_key,
            value=self.value,
            normalized_value=self.normalized_value,
            confidence=self.confidence,
            evidence=self.evidence,
            source_message_id=self.source_message_id,
            sensitivity=self.sensitivity,
            status=self.status,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class KnowledgeRelationRecord(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        Index("ix_relation_user_status", "user_id", "status"),
        Index("ix_relation_source", "source_fact_id"),
        Index("ix_relation_target", "target_fact_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_fact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_fact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predicate: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )

    def to_view(self) -> RelationView:
        return RelationView(
            id=self.id,
            user_id=self.user_id,
            source_fact_id=self.source_fact_id,
            target_fact_id=self.target_fact_id,
            predicate=self.predicate,
            confidence=self.confidence,
            evidence=self.evidence,
            source_message_id=self.source_message_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class VectorIndexOutboxRecord(Base):
    __tablename__ = "vector_index_outbox"
    __table_args__ = (
        Index("ix_vector_outbox_ready", "status", "available_at"),
        Index("ix_vector_outbox_fact", "fact_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


class MemoryReviewRecord(Base):
    __tablename__ = "memory_reviews"
    __table_args__ = (Index("ix_memory_review_user_state", "user_id", "state"),)

    fact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MemoryOperation.append.value
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


class MemoryPolicyRecord(Base):
    __tablename__ = "memory_policies"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allowed_categories: Mapped[str] = mapped_column(Text, nullable=False)
    blocked_keys: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    require_confirmation_sensitive: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


class AuditLogRecord(Base):
    __tablename__ = "memory_audit_logs"
    __table_args__ = (
        Index("ix_memory_audit_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )

    def to_view(self) -> AuditEventView:
        return AuditEventView.model_validate(self)


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    kwargs = {"pool_pre_ping": True, "echo": echo}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update({"pool_recycle": 1800})
    return create_engine(database_url, **kwargs)


class MemoryRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            with session.begin():
                yield session

    def ping(self) -> None:
        with self.session_factory() as session:
            session.execute(text("SELECT 1"))

    def get_memory_policy(self, user_id: str) -> MemoryPolicy:
        with self.session_factory() as session:
            record = session.get(MemoryPolicyRecord, user_id)
        if record is None:
            return MemoryPolicy()
        return MemoryPolicy(
            enabled=bool(record.enabled),
            allowed_categories=json.loads(record.allowed_categories),
            blocked_keys=json.loads(record.blocked_keys),
            require_confirmation_sensitive=bool(
                record.require_confirmation_sensitive
            ),
        )

    def set_memory_policy(self, user_id: str, policy: MemoryPolicy) -> MemoryPolicy:
        with self.session() as session:
            record = session.get(MemoryPolicyRecord, user_id)
            values = {
                "enabled": int(policy.enabled),
                "allowed_categories": json.dumps(
                    [category.value for category in policy.allowed_categories]
                ),
                "blocked_keys": json.dumps(policy.blocked_keys, ensure_ascii=False),
                "require_confirmation_sensitive": int(
                    policy.require_confirmation_sensitive
                ),
            }
            if record is None:
                session.add(MemoryPolicyRecord(user_id=user_id, **values))
            else:
                for key, value in values.items():
                    setattr(record, key, value)
                record.updated_at = now_utc()
        return policy

    def add_audit_event(
        self,
        *,
        user_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        detail: str | None = None,
    ) -> AuditEventView:
        with self.session() as session:
            record = AuditLogRecord(
                id=str(uuid4()),
                user_id=user_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail[:500] if detail else None,
            )
            session.add(record)
            session.flush()
            return record.to_view()

    def list_audit_events(
        self, user_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEventView]:
        statement = (
            select(AuditLogRecord)
            .where(AuditLogRecord.user_id == user_id)
            .order_by(AuditLogRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        with self.session_factory() as session:
            return [row.to_view() for row in session.scalars(statement)]

    def memory_stats(self, user_id: str | None = None) -> MemoryStatsResponse:
        filters = []
        if user_id is not None:
            filters.append(MemoryFactRecord.user_id == user_id)
        current_time = now_utc()
        with self.session_factory() as session:
            status_rows = session.execute(
                select(MemoryFactRecord.status, func.count(MemoryFactRecord.id))
                .where(*filters)
                .group_by(MemoryFactRecord.status)
            )
            statuses = {status: int(count) for status, count in status_rows}
            review_filters = [MemoryReviewRecord.state == MemoryReviewState.pending.value]
            if user_id is not None:
                review_filters.append(MemoryReviewRecord.user_id == user_id)
            pending = int(
                session.scalar(
                    select(func.count(MemoryReviewRecord.fact_id)).where(
                        *review_filters
                    )
                )
                or 0
            )
            active_filters = [
                MemoryFactRecord.status == FactStatus.active.value,
                (MemoryFactRecord.valid_to.is_(None))
                | (MemoryFactRecord.valid_to > current_time),
                (MemoryReviewRecord.state.is_(None))
                | (MemoryReviewRecord.state == MemoryReviewState.confirmed.value),
                *filters,
            ]
            active = int(
                session.scalar(
                    select(func.count(MemoryFactRecord.id))
                    .outerjoin(
                        MemoryReviewRecord,
                        (MemoryReviewRecord.fact_id == MemoryFactRecord.id)
                        & (MemoryReviewRecord.user_id == MemoryFactRecord.user_id),
                    )
                    .where(*active_filters)
                )
                or 0
            )
            expired_filters = [
                MemoryFactRecord.status == FactStatus.active.value,
                MemoryFactRecord.valid_to.is_not(None),
                MemoryFactRecord.valid_to <= current_time,
                (MemoryReviewRecord.state.is_(None))
                | (MemoryReviewRecord.state == MemoryReviewState.confirmed.value),
                *filters,
            ]
            expired = int(
                session.scalar(
                    select(func.count(MemoryFactRecord.id))
                    .outerjoin(
                        MemoryReviewRecord,
                        (MemoryReviewRecord.fact_id == MemoryFactRecord.id)
                        & (MemoryReviewRecord.user_id == MemoryFactRecord.user_id),
                    )
                    .where(*expired_filters)
                )
                or 0
            )
        return MemoryStatsResponse(
            active=active,
            pending_confirmation=pending,
            expired=expired,
            superseded=statuses.get(FactStatus.superseded.value, 0),
            retracted=statuses.get(FactStatus.retracted.value, 0),
            vector_outbox=self.vector_outbox_counts(user_id),
        )

    def list_users(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[UserSummary], int]:
        activity = union_all(
            select(
                MessageRecord.user_id.label("user_id"),
                MessageRecord.created_at.label("activity_at"),
            ),
            select(
                ConversationRecord.user_id.label("user_id"),
                ConversationRecord.updated_at.label("activity_at"),
            ),
            select(
                MemoryFactRecord.user_id.label("user_id"),
                MemoryFactRecord.updated_at.label("activity_at"),
            ),
        ).subquery("user_activity")
        users = (
            select(
                activity.c.user_id,
                func.max(activity.c.activity_at).label("last_activity_at"),
            )
            .group_by(activity.c.user_id)
            .subquery("users")
        )
        fact_counts = (
            select(
                MemoryFactRecord.user_id,
                func.count(MemoryFactRecord.id).label("memory_count"),
            )
            .where(MemoryFactRecord.status == FactStatus.active.value)
            .group_by(MemoryFactRecord.user_id)
            .subquery("fact_counts")
        )
        conversation_counts = (
            select(
                ConversationRecord.user_id,
                func.count(ConversationRecord.id).label("conversation_count"),
            )
            .group_by(ConversationRecord.user_id)
            .subquery("conversation_counts")
        )
        filters = []
        clean_query = query.strip() if query else ""
        if clean_query:
            filters.append(users.c.user_id.contains(clean_query, autoescape=True))

        count_statement = select(func.count()).select_from(users)
        if filters:
            count_statement = count_statement.where(*filters)

        statement = (
            select(
                users.c.user_id,
                func.coalesce(fact_counts.c.memory_count, 0).label("memory_count"),
                func.coalesce(
                    conversation_counts.c.conversation_count, 0
                ).label("conversation_count"),
                users.c.last_activity_at,
            )
            .outerjoin(fact_counts, fact_counts.c.user_id == users.c.user_id)
            .outerjoin(
                conversation_counts,
                conversation_counts.c.user_id == users.c.user_id,
            )
        )
        if filters:
            statement = statement.where(*filters)
        statement = statement.order_by(
            users.c.last_activity_at.desc(), users.c.user_id.asc()
        ).limit(limit).offset(offset)

        with self.session_factory() as session:
            total = int(session.scalar(count_statement) or 0)
            rows = session.execute(statement).all()
        return (
            [
                UserSummary(
                    user_id=row.user_id,
                    memory_count=int(row.memory_count),
                    conversation_count=int(row.conversation_count),
                    last_activity_at=row.last_activity_at,
                )
                for row in rows
            ],
            total,
        )

    def save_message(self, request: IngestRequest) -> None:
        with self.session() as session:
            existing = session.get(MessageRecord, (request.user_id, request.message_id))
            if existing is None:
                session.add(
                    MessageRecord(
                        user_id=request.user_id,
                        message_id=request.message_id,
                        content=request.content,
                        occurred_at=request.occurred_at,
                    )
                )
            elif existing.content != request.content:
                raise MessageConflictError(
                    "同一 user_id 下的 message_id 已被另一条消息使用"
                )

    def begin_memory_extraction(self, user_id: str, message_id: str) -> None:
        with self.session() as session:
            record = session.get(MemoryExtractionRecord, (user_id, message_id))
            if record is None:
                session.add(
                    MemoryExtractionRecord(
                        user_id=user_id,
                        message_id=message_id,
                        status="processing",
                        attempts=1,
                    )
                )
                return
            record.status = "processing"
            record.attempts += 1
            record.last_error = None
            record.updated_at = now_utc()

    def finish_memory_extraction(
        self,
        user_id: str,
        message_id: str,
        *,
        succeeded: bool,
        error_code: str | None = None,
    ) -> None:
        with self.session() as session:
            record = session.get(MemoryExtractionRecord, (user_id, message_id))
            if record is None:
                record = MemoryExtractionRecord(
                    user_id=user_id,
                    message_id=message_id,
                    status="succeeded" if succeeded else "failed",
                    attempts=1,
                )
                session.add(record)
            else:
                record.status = "succeeded" if succeeded else "failed"
            record.last_error = None if succeeded else (error_code or "unknown_error")[:200]
            record.updated_at = now_utc()

    def memory_extraction_succeeded(self, user_id: str, message_id: str) -> bool:
        with self.session_factory() as session:
            record = session.get(MemoryExtractionRecord, (user_id, message_id))
            return record is not None and record.status == "succeeded"

    def create_conversation(
        self, user_id: str, title: str | None = None
    ) -> ConversationView:
        clean_title = title.strip() if title and title.strip() else None
        with self.session() as session:
            conversation = ConversationRecord(
                id=str(uuid4()),
                user_id=user_id,
                title=clean_title,
            )
            session.add(conversation)
            session.flush()
            return conversation.to_view()

    def list_conversations(self, user_id: str) -> list[ConversationView]:
        query = (
            select(ConversationRecord)
            .where(ConversationRecord.user_id == user_id)
            .order_by(ConversationRecord.updated_at.desc())
        )
        with self.session_factory() as session:
            return [row.to_view() for row in session.scalars(query)]

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> ConversationView | None:
        query = select(ConversationRecord).where(
            ConversationRecord.id == conversation_id,
            ConversationRecord.user_id == user_id,
        )
        with self.session_factory() as session:
            conversation = session.scalar(query)
            return conversation.to_view() if conversation is not None else None

    def list_conversation_messages(
        self,
        user_id: str,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        before_sequence: int | None = None,
        limit: int | None = 100,
    ) -> list[ConversationMessageView]:
        query = select(ConversationMessageRecord).where(
            ConversationMessageRecord.user_id == user_id,
            ConversationMessageRecord.conversation_id == conversation_id,
            ConversationMessageRecord.sequence > after_sequence,
        )
        if before_sequence is not None:
            query = query.where(
                ConversationMessageRecord.sequence < before_sequence
            )
        query = query.order_by(ConversationMessageRecord.sequence.desc())
        if limit is not None:
            query = query.limit(limit)
        with self.session_factory() as session:
            rows = list(session.scalars(query))
        return [row.to_view() for row in reversed(rows)]

    def get_chat_exchange(
        self,
        user_id: str,
        conversation_id: str,
        client_message_id: str,
    ) -> tuple[ConversationMessageView, ConversationMessageView] | None:
        with self.session_factory() as session:
            user_message = session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.user_id == user_id,
                    ConversationMessageRecord.conversation_id == conversation_id,
                    ConversationMessageRecord.client_message_id == client_message_id,
                    ConversationMessageRecord.role == ChatRole.user.value,
                )
            )
            if user_message is None:
                return None
            assistant_message = session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.user_id == user_id,
                    ConversationMessageRecord.conversation_id == conversation_id,
                    ConversationMessageRecord.reply_to_message_id == user_message.id,
                    ConversationMessageRecord.role == ChatRole.assistant.value,
                )
            )
            if assistant_message is None:
                return None
            return user_message.to_view(), assistant_message.to_view()

    def append_chat_exchange(
        self,
        *,
        user_id: str,
        conversation_id: str,
        client_message_id: str,
        user_content: str,
        assistant_content: str,
    ) -> tuple[ConversationMessageView, ConversationMessageView, bool]:
        with self.session() as session:
            conversation = session.scalar(
                select(ConversationRecord)
                .where(
                    ConversationRecord.id == conversation_id,
                    ConversationRecord.user_id == user_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise KeyError("conversation_not_found")

            existing = session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.user_id == user_id,
                    ConversationMessageRecord.conversation_id == conversation_id,
                    ConversationMessageRecord.client_message_id == client_message_id,
                    ConversationMessageRecord.role == ChatRole.user.value,
                )
            )
            if existing is not None:
                if existing.content != user_content:
                    raise ValueError(
                        "同一会话中的 message_id 已被另一条消息使用"
                    )
                assistant = session.scalar(
                    select(ConversationMessageRecord).where(
                        ConversationMessageRecord.reply_to_message_id == existing.id,
                        ConversationMessageRecord.role == ChatRole.assistant.value,
                    )
                )
                if assistant is None:
                    raise RuntimeError("幂等消息缺少对应的助手回复")
                return existing.to_view(), assistant.to_view(), True

            created_at = now_utc()
            user_message = ConversationMessageRecord(
                id=str(uuid4()),
                conversation_id=conversation_id,
                user_id=user_id,
                role=ChatRole.user.value,
                content=user_content,
                sequence=conversation.last_sequence + 1,
                client_message_id=client_message_id,
                created_at=created_at,
            )
            assistant_message = ConversationMessageRecord(
                id=str(uuid4()),
                conversation_id=conversation_id,
                user_id=user_id,
                role=ChatRole.assistant.value,
                content=assistant_content,
                sequence=conversation.last_sequence + 2,
                reply_to_message_id=user_message.id,
                created_at=created_at,
            )
            session.add_all((user_message, assistant_message))
            conversation.last_sequence += 2
            conversation.updated_at = created_at
            session.flush()
            return user_message.to_view(), assistant_message.to_view(), False

    def update_conversation_summary(
        self,
        user_id: str,
        conversation_id: str,
        summary: str,
        through_sequence: int,
    ) -> ConversationView:
        with self.session() as session:
            conversation = session.scalar(
                select(ConversationRecord)
                .where(
                    ConversationRecord.id == conversation_id,
                    ConversationRecord.user_id == user_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise KeyError("conversation_not_found")
            if through_sequence > conversation.summary_through_sequence:
                conversation.summary = summary
                conversation.summary_through_sequence = through_sequence
                conversation.updated_at = now_utc()
            session.flush()
            return conversation.to_view()

    def delete_conversation(self, user_id: str, conversation_id: str) -> tuple[int, int]:
        with self.session() as session:
            messages = session.execute(
                delete(ConversationMessageRecord).where(
                    ConversationMessageRecord.user_id == user_id,
                    ConversationMessageRecord.conversation_id == conversation_id,
                )
            ).rowcount
            conversations = session.execute(
                delete(ConversationRecord).where(
                    ConversationRecord.user_id == user_id,
                    ConversationRecord.id == conversation_id,
                )
            ).rowcount
        return conversations or 0, messages or 0

    def apply_candidates(
        self, user_id: str, message_id: str, candidates: list[FactCandidate]
    ) -> list[MemoryDecision]:
        decisions: list[MemoryDecision] = []
        policy = self.get_memory_policy(user_id)
        with self.session() as session:
            for candidate in candidates:
                current_time = now_utc()
                active = list(
                    session.scalars(
                        select(MemoryFactRecord)
                        .where(
                            MemoryFactRecord.user_id == user_id,
                            MemoryFactRecord.category == candidate.category.value,
                            MemoryFactRecord.fact_key == candidate.key,
                            MemoryFactRecord.status == FactStatus.active.value,
                            (MemoryFactRecord.valid_to.is_(None))
                            | (MemoryFactRecord.valid_to > current_time),
                        )
                        .with_for_update()
                    )
                )
                same = next(
                    (
                        fact
                        for fact in active
                        if fact.normalized_value == candidate.normalized_value
                    ),
                    None,
                )

                if candidate.operation == MemoryOperation.retract:
                    targets = [same] if same is not None else active
                    if not targets:
                        decisions.append(
                            MemoryDecision(
                                action="noop", key=candidate.key, detail="没有可撤回的活跃事实"
                            )
                        )
                        continue
                    for fact in targets:
                        fact.status = FactStatus.retracted.value
                        fact.valid_to = now_utc()
                        fact.updated_at = now_utc()
                        self._deactivate_relations(session, fact.id, FactStatus.retracted)
                        self._enqueue_vector_operation(
                            session, fact.id, user_id, "delete"
                        )
                    decisions.append(
                        MemoryDecision(
                            action="retracted",
                            key=candidate.key,
                            detail=f"已撤回 {len(targets)} 条事实",
                        )
                    )
                    continue

                if same is not None:
                    same_review = session.get(MemoryReviewRecord, same.id)
                    changed = candidate.confidence > same.confidence
                    if changed:
                        same.confidence = candidate.confidence
                        same.evidence = candidate.evidence
                        same.source_message_id = message_id
                    if candidate.valid_from is not None:
                        same.valid_from = candidate.valid_from
                        changed = True
                    if candidate.valid_to is not None:
                        same.valid_to = candidate.valid_to
                        changed = True
                    if changed:
                        same.updated_at = current_time
                        if not (
                            same_review is not None
                            and same_review.state == MemoryReviewState.pending.value
                        ):
                            self._enqueue_vector_operation(
                                session, same.id, user_id, "upsert"
                            )
                    decisions.append(
                        MemoryDecision(
                            action="duplicate",
                            fact_id=same.id,
                            key=candidate.key,
                            detail="相同事实已经存在",
                        )
                    )
                    continue

                pending_confirmation = (
                    candidate.sensitivity == Sensitivity.sensitive
                    and policy.require_confirmation_sensitive
                )
                stale_replacement = False
                if (
                    candidate.operation == MemoryOperation.replace
                    and candidate.valid_from is not None
                ):
                    newer_starts = [
                        fact.valid_from
                        for fact in active
                        if fact.valid_from is not None
                        and aware_utc(fact.valid_from)
                        > aware_utc(candidate.valid_from)
                    ]
                    stale_replacement = bool(newer_starts)

                if (
                    candidate.operation == MemoryOperation.replace
                    and not pending_confirmation
                    and not stale_replacement
                ):
                    for fact in active:
                        fact.status = FactStatus.superseded.value
                        fact.valid_to = candidate.valid_from or current_time
                        fact.updated_at = current_time
                        self._deactivate_relations(session, fact.id, FactStatus.superseded)
                        self._enqueue_vector_operation(
                            session, fact.id, user_id, "delete"
                        )

                fact = MemoryFactRecord(
                    id=str(uuid4()),
                    user_id=user_id,
                    category=candidate.category.value,
                    fact_key=candidate.key,
                    value=candidate.value,
                    normalized_value=candidate.normalized_value,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    source_message_id=message_id,
                    sensitivity=candidate.sensitivity.value,
                    status=(
                        FactStatus.superseded.value
                        if stale_replacement
                        else FactStatus.active.value
                    ),
                    valid_from=candidate.valid_from,
                    valid_to=(
                        min(newer_starts)
                        if stale_replacement
                        else candidate.valid_to
                    ),
                )
                session.add(fact)
                session.flush()
                if pending_confirmation:
                    session.add(
                        MemoryReviewRecord(
                            fact_id=fact.id,
                            user_id=user_id,
                            state=MemoryReviewState.pending.value,
                            operation=candidate.operation.value,
                        )
                    )
                    action = "pending_confirmation"
                    detail = "敏感记忆等待用户确认"
                elif stale_replacement:
                    action = "historical"
                    detail = "已保存为历史事实，未覆盖更新的当前事实"
                else:
                    self._enqueue_vector_operation(
                        session, fact.id, user_id, "upsert"
                    )
                if not pending_confirmation and not stale_replacement and (
                    candidate.operation == MemoryOperation.replace and active
                ):
                    action = "replaced"
                    detail = f"已替换 {len(active)} 条旧事实"
                elif not pending_confirmation and not stale_replacement and active:
                    action = "appended"
                    detail = "已追加事实"
                elif not pending_confirmation and not stale_replacement:
                    action = "created"
                    detail = "已新增事实"
                decisions.append(
                    MemoryDecision(
                        action=action,
                        fact_id=fact.id,
                        key=candidate.key,
                        detail=detail,
                    )
                )
        return decisions

    @staticmethod
    def _enqueue_vector_operation(
        session: Session,
        fact_id: str,
        user_id: str,
        operation: str,
    ) -> None:
        existing = session.scalar(
            select(VectorIndexOutboxRecord)
            .where(
                VectorIndexOutboxRecord.fact_id == fact_id,
                VectorIndexOutboxRecord.status.in_(("pending", "failed")),
            )
            .order_by(VectorIndexOutboxRecord.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            existing.operation = operation
            existing.status = "pending"
            existing.attempts = 0
            existing.available_at = now_utc()
            existing.last_error = None
            existing.updated_at = now_utc()
            return
        session.add(
            VectorIndexOutboxRecord(
                id=str(uuid4()),
                fact_id=fact_id,
                user_id=user_id,
                operation=operation,
                status="pending",
            )
        )

    @staticmethod
    def _deactivate_relations(
        session: Session, fact_id: str, status: FactStatus
    ) -> None:
        relations = session.scalars(
            select(KnowledgeRelationRecord).where(
                KnowledgeRelationRecord.status == FactStatus.active.value,
                (KnowledgeRelationRecord.source_fact_id == fact_id)
                | (KnowledgeRelationRecord.target_fact_id == fact_id),
            )
        )
        for relation in relations:
            relation.status = status.value
            relation.updated_at = now_utc()

    def apply_relations(
        self, user_id: str, message_id: str, candidates: list[RelationCandidate]
    ) -> list[MemoryDecision]:
        decisions: list[MemoryDecision] = []
        with self.session() as session:
            active_facts = list(
                session.scalars(
                    select(MemoryFactRecord)
                    .outerjoin(
                        MemoryReviewRecord,
                        MemoryReviewRecord.fact_id == MemoryFactRecord.id,
                    )
                    .where(
                        MemoryFactRecord.user_id == user_id,
                        MemoryFactRecord.status == FactStatus.active.value,
                        (MemoryFactRecord.valid_to.is_(None))
                        | (MemoryFactRecord.valid_to > now_utc()),
                        (MemoryReviewRecord.state.is_(None))
                        | (
                            MemoryReviewRecord.state
                            == MemoryReviewState.confirmed.value
                        ),
                    )
                )
            )
            fact_lookup = {
                (fact.fact_key, fact.normalized_value): fact for fact in active_facts
            }
            for candidate in candidates:
                source = fact_lookup.get(
                    (candidate.subject_key, candidate.subject_normalized_value)
                )
                target = fact_lookup.get(
                    (candidate.object_key, candidate.object_normalized_value)
                )
                relation_key = (
                    f"{candidate.subject_key}:{candidate.predicate}:{candidate.object_key}"
                )
                if source is None or target is None:
                    decisions.append(
                        MemoryDecision(
                            action="noop",
                            key=relation_key,
                            detail="关系端点不存在或已失效",
                        )
                    )
                    continue
                existing = session.scalar(
                    select(KnowledgeRelationRecord).where(
                        KnowledgeRelationRecord.user_id == user_id,
                        KnowledgeRelationRecord.source_fact_id == source.id,
                        KnowledgeRelationRecord.target_fact_id == target.id,
                        KnowledgeRelationRecord.predicate == candidate.predicate,
                        KnowledgeRelationRecord.status == FactStatus.active.value,
                    )
                )
                if existing is not None:
                    if candidate.confidence > existing.confidence:
                        existing.confidence = candidate.confidence
                        existing.evidence = candidate.evidence
                        existing.source_message_id = message_id
                        existing.updated_at = now_utc()
                    decisions.append(
                        MemoryDecision(
                            action="duplicate",
                            relation_id=existing.id,
                            key=relation_key,
                            detail="相同关系已经存在",
                        )
                    )
                    continue
                relation = KnowledgeRelationRecord(
                    id=str(uuid4()),
                    user_id=user_id,
                    source_fact_id=source.id,
                    target_fact_id=target.id,
                    predicate=candidate.predicate,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    source_message_id=message_id,
                    status=FactStatus.active.value,
                )
                session.add(relation)
                session.flush()
                decisions.append(
                    MemoryDecision(
                        action="created",
                        relation_id=relation.id,
                        key=relation_key,
                        detail="已新增知识关系",
                    )
                )
        return decisions

    def list_facts(self, user_id: str, *, include_history: bool = False) -> list[FactView]:
        now = now_utc()
        query = select(MemoryFactRecord).outerjoin(
            MemoryReviewRecord,
            (MemoryReviewRecord.fact_id == MemoryFactRecord.id)
            & (MemoryReviewRecord.user_id == MemoryFactRecord.user_id),
        ).where(MemoryFactRecord.user_id == user_id)
        if not include_history:
            query = query.where(
                MemoryFactRecord.status == FactStatus.active.value,
                (MemoryFactRecord.valid_to.is_(None))
                | (MemoryFactRecord.valid_to > now),
                (MemoryReviewRecord.state.is_(None))
                | (MemoryReviewRecord.state == MemoryReviewState.confirmed.value),
            )
        query = query.order_by(MemoryFactRecord.created_at.asc())
        with self.session_factory() as session:
            return [row.to_view() for row in session.scalars(query)]

    def get_fact(self, user_id: str, fact_id: str) -> FactView | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(MemoryFactRecord).where(
                    MemoryFactRecord.user_id == user_id,
                    MemoryFactRecord.id == fact_id,
                )
            )
            return record.to_view() if record else None

    def list_managed_facts(
        self,
        user_id: str,
        *,
        include_history: bool = False,
        include_rejected: bool = False,
    ) -> list[ManagedFactView]:
        now = now_utc()
        query = select(MemoryFactRecord, MemoryReviewRecord.state).outerjoin(
            MemoryReviewRecord,
            (MemoryReviewRecord.fact_id == MemoryFactRecord.id)
            & (MemoryReviewRecord.user_id == MemoryFactRecord.user_id),
        ).where(MemoryFactRecord.user_id == user_id)
        if not include_history:
            query = query.where(
                MemoryFactRecord.status == FactStatus.active.value,
                (MemoryFactRecord.valid_to.is_(None))
                | (MemoryFactRecord.valid_to > now),
                (MemoryReviewRecord.state.is_(None))
                | (MemoryReviewRecord.state != MemoryReviewState.rejected.value),
            )
        elif not include_rejected:
            query = query.where(
                (MemoryReviewRecord.state.is_(None))
                | (MemoryReviewRecord.state != MemoryReviewState.rejected.value)
            )
        query = query.order_by(MemoryFactRecord.created_at.desc())
        with self.session_factory() as session:
            return [
                ManagedFactView(
                    **record.to_view().model_dump(),
                    review_state=review_state or MemoryReviewState.confirmed,
                )
                for record, review_state in session.execute(query)
            ]

    def update_fact_review(
        self, user_id: str, fact_id: str, state: MemoryReviewState
    ) -> FactView | None:
        with self.session() as session:
            fact = session.scalar(
                select(MemoryFactRecord)
                .where(
                    MemoryFactRecord.user_id == user_id,
                    MemoryFactRecord.id == fact_id,
                )
                .with_for_update()
            )
            if fact is None:
                return None
            review = session.get(MemoryReviewRecord, fact_id)
            if review is None:
                review = MemoryReviewRecord(
                    fact_id=fact_id,
                    user_id=user_id,
                    state=state.value,
                    reviewed_at=now_utc(),
                )
                session.add(review)
            else:
                if review.state == MemoryReviewState.rejected.value:
                    return None
                review.state = state.value
                review.reviewed_at = now_utc()
                review.updated_at = now_utc()
            fact.status = (
                FactStatus.retracted.value
                if state == MemoryReviewState.rejected
                else FactStatus.active.value
            )
            if state == MemoryReviewState.rejected:
                fact.valid_to = now_utc()
                self._deactivate_relations(session, fact.id, FactStatus.retracted)
                self._enqueue_vector_operation(session, fact.id, user_id, "delete")
            else:
                if review.operation == MemoryOperation.replace.value:
                    others = session.scalars(
                        select(MemoryFactRecord)
                        .where(
                            MemoryFactRecord.user_id == user_id,
                            MemoryFactRecord.category == fact.category,
                            MemoryFactRecord.fact_key == fact.fact_key,
                            MemoryFactRecord.id != fact.id,
                            MemoryFactRecord.status == FactStatus.active.value,
                        )
                        .with_for_update()
                    )
                    for other in others:
                        other.status = FactStatus.superseded.value
                        other.valid_to = fact.valid_from or now_utc()
                        other.updated_at = now_utc()
                        self._deactivate_relations(
                            session, other.id, FactStatus.superseded
                        )
                        self._enqueue_vector_operation(
                            session, other.id, user_id, "delete"
                        )
                self._enqueue_vector_operation(session, fact.id, user_id, "upsert")
            fact.updated_at = now_utc()
            return fact.to_view()

    def correct_fact(
        self,
        user_id: str,
        fact_id: str,
        *,
        value: str,
        normalized_value: str,
        valid_from: datetime | None,
        valid_to: datetime | None,
        actor_id: str,
    ) -> FactView | None:
        with self.session() as session:
            old = session.scalar(
                select(MemoryFactRecord)
                .where(
                    MemoryFactRecord.user_id == user_id,
                    MemoryFactRecord.id == fact_id,
                    MemoryFactRecord.status == FactStatus.active.value,
                )
                .with_for_update()
            )
            if old is None:
                return None
            replacement = MemoryFactRecord(
                id=str(uuid4()),
                user_id=user_id,
                category=old.category,
                fact_key=old.fact_key,
                value=value,
                normalized_value=normalized_value,
                confidence=old.confidence,
                evidence=old.evidence,
                source_message_id=old.source_message_id,
                sensitivity=old.sensitivity,
                status=FactStatus.active.value,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            old.status = FactStatus.superseded.value
            old.valid_to = valid_from or now_utc()
            old.updated_at = now_utc()
            self._deactivate_relations(session, old.id, FactStatus.superseded)
            self._enqueue_vector_operation(session, old.id, user_id, "delete")
            session.add(replacement)
            session.flush()
            session.add(
                MemoryReviewRecord(
                    fact_id=replacement.id,
                    user_id=user_id,
                    state=MemoryReviewState.confirmed.value,
                    reviewed_at=now_utc(),
                )
            )
            self._enqueue_vector_operation(session, replacement.id, user_id, "upsert")
            session.add(
                AuditLogRecord(
                    id=str(uuid4()),
                    user_id=user_id,
                    actor_id=actor_id,
                    action="correct",
                    resource_type="memory_fact",
                    resource_id=replacement.id,
                    detail=f"Corrected fact {fact_id}; previous value retained in history",
                )
            )
            return replacement.to_view()

    def delete_fact(
        self, user_id: str, fact_id: str, *, actor_id: str
    ) -> bool:
        with self.session() as session:
            fact = session.scalar(
                select(MemoryFactRecord)
                .where(
                    MemoryFactRecord.user_id == user_id,
                    MemoryFactRecord.id == fact_id,
                )
                .with_for_update()
            )
            if fact is None:
                return False
            fact.status = FactStatus.retracted.value
            fact.valid_to = now_utc()
            fact.updated_at = now_utc()
            self._deactivate_relations(session, fact.id, FactStatus.retracted)
            self._enqueue_vector_operation(session, fact.id, user_id, "delete")
            review = session.get(MemoryReviewRecord, fact_id)
            if review is not None:
                review.state = MemoryReviewState.rejected.value
                review.reviewed_at = now_utc()
                review.updated_at = now_utc()
            session.add(
                AuditLogRecord(
                    id=str(uuid4()), user_id=user_id, actor_id=actor_id,
                    action="delete", resource_type="memory_fact",
                    resource_id=fact_id, detail="Memory fact deleted by user action",
                )
            )
        return True

    def list_relations(
        self, user_id: str, *, include_history: bool = False
    ) -> list[RelationView]:
        query = select(KnowledgeRelationRecord).where(
            KnowledgeRelationRecord.user_id == user_id
        )
        if not include_history:
            query = query.where(
                KnowledgeRelationRecord.status == FactStatus.active.value
            )
        query = query.order_by(KnowledgeRelationRecord.created_at.asc())
        with self.session_factory() as session:
            return [row.to_view() for row in session.scalars(query)]

    def get_knowledge_graph(self, user_id: str) -> KnowledgeGraphResponse:
        facts = self.list_facts(user_id)
        relations = self.list_relations(user_id)
        user_node_id = f"user:{user_id}"
        nodes = [GraphNode(id=user_node_id, label=user_id, node_type="user")]
        edges: list[GraphEdge] = []
        for fact in facts:
            nodes.append(
                GraphNode(
                    id=fact.id,
                    label=fact.value,
                    node_type=fact.category.value,
                    key=fact.key,
                    confidence=fact.confidence,
                )
            )
            edges.append(
                GraphEdge(
                    id=f"owns:{fact.id}",
                    source=user_node_id,
                    target=fact.id,
                    label=fact.key,
                    edge_type="memory",
                    confidence=fact.confidence,
                    evidence=fact.evidence,
                )
            )
        active_ids = {fact.id for fact in facts}
        for relation in relations:
            if (
                relation.source_fact_id not in active_ids
                or relation.target_fact_id not in active_ids
            ):
                continue
            edges.append(
                GraphEdge(
                    id=relation.id,
                    source=relation.source_fact_id,
                    target=relation.target_fact_id,
                    label=relation.predicate,
                    edge_type="relation",
                    confidence=relation.confidence,
                    evidence=relation.evidence,
                )
            )
        return KnowledgeGraphResponse(user_id=user_id, nodes=nodes, edges=edges)

    def recall(
        self,
        user_id: str,
        request: RecallRequest,
        *,
        semantic_matches: list[SemanticMatch] | None = None,
        semantic_available: bool = False,
        warnings: list[str] | None = None,
    ) -> RecallResponse:
        return recall_memories(
            user_id,
            request,
            self.list_facts(user_id),
            self.list_relations(user_id),
            semantic_matches=semantic_matches,
            semantic_available=semantic_available,
            warnings=warnings,
        )

    def enqueue_active_facts(self, user_id: str | None = None) -> int:
        query = select(MemoryFactRecord).outerjoin(
            MemoryReviewRecord,
            MemoryReviewRecord.fact_id == MemoryFactRecord.id,
        ).where(
            MemoryFactRecord.status == FactStatus.active.value,
            (MemoryFactRecord.valid_to.is_(None))
            | (MemoryFactRecord.valid_to > now_utc()),
            (MemoryReviewRecord.state.is_(None))
            | (MemoryReviewRecord.state == MemoryReviewState.confirmed.value),
        )
        if user_id is not None:
            query = query.where(MemoryFactRecord.user_id == user_id)
        with self.session() as session:
            facts = list(session.scalars(query))
            for fact in facts:
                self._enqueue_vector_operation(
                    session, fact.id, fact.user_id, "upsert"
                )
        return len(facts)

    def claim_vector_operations(
        self, *, batch_size: int, max_attempts: int
    ) -> list[VectorOutboxItem]:
        with self.session() as session:
            rows = list(
                session.scalars(
                    select(VectorIndexOutboxRecord)
                    .where(
                        VectorIndexOutboxRecord.status.in_(("pending", "failed")),
                        VectorIndexOutboxRecord.available_at <= now_utc(),
                        VectorIndexOutboxRecord.attempts < max_attempts,
                    )
                    .order_by(VectorIndexOutboxRecord.created_at.asc())
                    .limit(batch_size)
                    .with_for_update()
                )
            )
            for row in rows:
                row.status = "processing"
                row.attempts += 1
                row.updated_at = now_utc()
            return [
                VectorOutboxItem(
                    id=row.id,
                    fact_id=row.fact_id,
                    user_id=row.user_id,
                    operation=row.operation,
                    attempts=row.attempts,
                )
                for row in rows
            ]

    def get_active_facts_by_ids(self, fact_ids: list[str]) -> dict[str, FactView]:
        if not fact_ids:
            return {}
        query = select(MemoryFactRecord).outerjoin(
            MemoryReviewRecord,
            MemoryReviewRecord.fact_id == MemoryFactRecord.id,
        ).where(
            MemoryFactRecord.id.in_(fact_ids),
            MemoryFactRecord.status == FactStatus.active.value,
            (MemoryFactRecord.valid_to.is_(None))
            | (MemoryFactRecord.valid_to > now_utc()),
            (MemoryReviewRecord.state.is_(None))
            | (MemoryReviewRecord.state == MemoryReviewState.confirmed.value),
        )
        with self.session_factory() as session:
            return {row.id: row.to_view() for row in session.scalars(query)}

    def complete_vector_operations(self, operation_ids: list[str]) -> None:
        if not operation_ids:
            return
        with self.session() as session:
            rows = session.scalars(
                select(VectorIndexOutboxRecord).where(
                    VectorIndexOutboxRecord.id.in_(operation_ids)
                )
            )
            for row in rows:
                row.status = "completed"
                row.last_error = None
                row.updated_at = now_utc()

    def fail_vector_operations(
        self,
        operation_ids: list[str],
        error: str,
        *,
        delay_seconds: int,
    ) -> None:
        if not operation_ids:
            return
        with self.session() as session:
            rows = session.scalars(
                select(VectorIndexOutboxRecord).where(
                    VectorIndexOutboxRecord.id.in_(operation_ids)
                )
            )
            for row in rows:
                row.status = "failed"
                row.last_error = error[:2000]
                row.available_at = now_utc() + timedelta(seconds=delay_seconds)
                row.updated_at = now_utc()

    def recover_stale_vector_operations(self, *, stale_after_seconds: int = 600) -> int:
        cutoff = now_utc() - timedelta(seconds=stale_after_seconds)
        recovered = 0
        with self.session() as session:
            rows = session.scalars(
                select(VectorIndexOutboxRecord).where(
                    VectorIndexOutboxRecord.status == "processing",
                    VectorIndexOutboxRecord.updated_at < cutoff,
                )
            )
            for row in rows:
                row.status = "failed"
                row.available_at = now_utc()
                row.last_error = "worker lease expired"
                row.updated_at = now_utc()
                recovered += 1
        return recovered

    def vector_outbox_counts(self, user_id: str | None = None) -> dict[str, int]:
        filters = []
        if user_id is not None:
            filters.append(VectorIndexOutboxRecord.user_id == user_id)
        with self.session_factory() as session:
            rows = session.execute(
                select(VectorIndexOutboxRecord.status, text("COUNT(*)"))
                .where(*filters)
                .group_by(VectorIndexOutboxRecord.status)
            )
            return {status: int(count) for status, count in rows}

    def delete_user(self, user_id: str) -> tuple[int, int, int, int, int]:
        with self.session() as session:
            facts_to_delete = list(
                session.scalars(
                    select(MemoryFactRecord).where(
                        MemoryFactRecord.user_id == user_id
                    )
                )
            )
            for fact in facts_to_delete:
                self._enqueue_vector_operation(
                    session, fact.id, user_id, "delete"
                )
            relations = session.execute(
                delete(KnowledgeRelationRecord).where(
                    KnowledgeRelationRecord.user_id == user_id
                )
            ).rowcount
            session.execute(
                delete(MemoryReviewRecord).where(
                    MemoryReviewRecord.user_id == user_id
                )
            )
            facts = session.execute(
                delete(MemoryFactRecord).where(MemoryFactRecord.user_id == user_id)
            ).rowcount
            messages = session.execute(
                delete(MessageRecord).where(MessageRecord.user_id == user_id)
            ).rowcount
            session.execute(
                delete(MemoryExtractionRecord).where(
                    MemoryExtractionRecord.user_id == user_id
                )
            )
            chat_messages = session.execute(
                delete(ConversationMessageRecord).where(
                    ConversationMessageRecord.user_id == user_id
                )
            ).rowcount
            conversations = session.execute(
                delete(ConversationRecord).where(ConversationRecord.user_id == user_id)
            ).rowcount
            session.execute(
                delete(MemoryPolicyRecord).where(
                    MemoryPolicyRecord.user_id == user_id
                )
            )
            session.execute(
                delete(AuditLogRecord).where(AuditLogRecord.user_id == user_id)
            )
        return (
            relations or 0,
            facts or 0,
            messages or 0,
            conversations or 0,
            chat_messages or 0,
        )

