from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FactCategory(str, Enum):
    profile = "profile"
    preference = "preference"
    event = "event"


class MemoryOperation(str, Enum):
    replace = "replace"
    append = "append"
    retract = "retract"


class Sensitivity(str, Enum):
    normal = "normal"
    personal = "personal"
    sensitive = "sensitive"
    restricted = "restricted"


class FactStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    retracted = "retracted"


class ChatRole(str, Enum):
    user = "user"
    assistant = "assistant"


class FactCandidate(BaseModel):
    category: FactCategory
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    normalized_value: str = Field(min_length=1, max_length=2000)
    operation: MemoryOperation
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=2000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.personal
    explicit: bool = True

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")

    @field_validator("normalized_value")
    @classmethod
    def clean_normalized_value(cls, value: str) -> str:
        return value.strip().casefold()

    @model_validator(mode="after")
    def validate_validity(self):
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be later than valid_from")
        return self


class RelationCandidate(BaseModel):
    subject_key: str = Field(min_length=1, max_length=100)
    subject_normalized_value: str = Field(min_length=1, max_length=2000)
    predicate: str = Field(min_length=1, max_length=100)
    object_key: str = Field(min_length=1, max_length=100)
    object_normalized_value: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=2000)
    explicit: bool = True

    @field_validator("subject_key", "object_key", "predicate")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")

    @field_validator("subject_normalized_value", "object_normalized_value")
    @classmethod
    def normalize_endpoint_value(cls, value: str) -> str:
        return value.strip().casefold()


class ExtractionResult(BaseModel):
    is_memory_worthy: bool
    candidates: list[FactCandidate] = Field(default_factory=list)
    relations: list[RelationCandidate] = Field(default_factory=list)


class IngestRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)
    occurred_at: datetime = Field(default_factory=utc_now)


class RejectedCandidate(BaseModel):
    key: str
    reason: str


class MemoryDecision(BaseModel):
    action: str
    fact_id: str | None = None
    relation_id: str | None = None
    key: str
    detail: str


class IngestResponse(BaseModel):
    message_id: str
    memory_worthy: bool
    decisions: list[MemoryDecision] = Field(default_factory=list)
    rejected: list[RejectedCandidate] = Field(default_factory=list)


class FactView(BaseModel):
    id: str
    user_id: str
    category: FactCategory
    key: str
    value: str
    normalized_value: str
    confidence: float
    evidence: str
    source_message_id: str
    sensitivity: Sensitivity
    status: FactStatus
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryReviewState(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class ManagedFactView(FactView):
    review_state: MemoryReviewState = MemoryReviewState.confirmed


class MemoryReviewRequest(BaseModel):
    state: MemoryReviewState

    @model_validator(mode="after")
    def validate_terminal_state(self):
        if self.state == MemoryReviewState.pending:
            raise ValueError("review state must be confirmed or rejected")
        return self


class MemoryCorrectionRequest(BaseModel):
    value: str = Field(min_length=1, max_length=2000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_validity(self):
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be later than valid_from")
        return self


class MemoryPolicy(BaseModel):
    enabled: bool = True
    allowed_categories: list[FactCategory] = Field(
        default_factory=lambda: list(FactCategory)
    )
    blocked_keys: list[str] = Field(default_factory=list)
    require_confirmation_sensitive: bool = True

    @field_validator("blocked_keys")
    @classmethod
    def normalize_blocked_keys(cls, values: list[str]) -> list[str]:
        return sorted(
            {
                value.strip().casefold().replace(" ", "_")
                for value in values
                if value.strip()
            }
        )


class AuditEventView(BaseModel):
    id: str
    user_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str | None
    detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryStatsResponse(BaseModel):
    active: int = 0
    pending_confirmation: int = 0
    expired: int = 0
    superseded: int = 0
    retracted: int = 0
    vector_outbox: dict[str, int] = Field(default_factory=dict)


class RelationView(BaseModel):
    id: str
    user_id: str
    source_fact_id: str
    target_fact_id: str
    predicate: str
    confidence: float
    evidence: str
    source_message_id: str
    status: FactStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GraphNode(BaseModel):
    id: str
    label: str
    node_type: str
    key: str | None = None
    confidence: float | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    edge_type: str
    confidence: float | None = None
    evidence: str | None = None


class KnowledgeGraphResponse(BaseModel):
    user_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class RecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=50)
    categories: list[FactCategory] | None = None
    auto_category_filter: bool = True
    include_graph_neighbors: bool = True
    use_semantic: bool = True
    semantic_min_score: float = Field(default=0.25, ge=0, le=1)
    min_score: float = Field(default=0.35, ge=0, le=2)
    relative_score_threshold: float = Field(default=0.55, ge=0, le=1)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be blank")
        return value.strip()


class RecallRelation(BaseModel):
    predicate: str
    direction: str
    fact: FactView
    confidence: float


class RecallScores(BaseModel):
    lexical: float = 0
    semantic: float = 0
    confidence: float = 0
    recency: float = 0
    graph: float = 0
    total: float = 0


class RecallHit(BaseModel):
    fact: FactView
    score: float
    scores: RecallScores = Field(default_factory=RecallScores)
    match_reasons: list[str]
    related: list[RecallRelation] = Field(default_factory=list)


class RecallResponse(BaseModel):
    user_id: str
    query: str
    hits: list[RecallHit]
    context: str
    retrieval_mode: str = "lexical_graph"
    warnings: list[str] = Field(default_factory=list)
    applied_categories: list[FactCategory] = Field(default_factory=list)
    applied_keys: list[str] = Field(default_factory=list)
    score_threshold: float = 0


class SemanticMatch(BaseModel):
    fact_id: str
    score: float = Field(ge=-1, le=1)


class ProfileResponse(BaseModel):
    user_id: str
    profile: dict[str, Any]
    preferences: dict[str, list[str]]
    events: list[FactView]


class UserSummary(BaseModel):
    user_id: str
    memory_count: int
    conversation_count: int
    last_activity_at: datetime


class UserListResponse(BaseModel):
    items: list[UserSummary]
    total: int
    limit: int
    offset: int


class DeleteResponse(BaseModel):
    user_id: str
    deleted_relations: int
    deleted_facts: int
    deleted_messages: int
    deleted_conversations: int = 0
    deleted_chat_messages: int = 0


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationView(BaseModel):
    id: str
    user_id: str
    title: str | None
    summary: str | None
    summary_through_sequence: int
    last_sequence: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessageView(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    role: ChatRole
    content: str
    sequence: int
    client_message_id: str | None
    reply_to_message_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationHistoryResponse(BaseModel):
    conversation: ConversationView
    messages: list[ConversationMessageView]


class ChatRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)


class ChatResponse(BaseModel):
    conversation_id: str
    user_message: ConversationMessageView
    assistant_message: ConversationMessageView
    memory_context: str
    retrieval_mode: str
    warnings: list[str] = Field(default_factory=list)
    memory_decisions: list[MemoryDecision] = Field(default_factory=list)
    memory_rejected: list[RejectedCandidate] = Field(default_factory=list)

