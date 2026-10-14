from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from moyo_memory.db import MemoryRepository
from moyo_memory.extractor import FactExtractor
from moyo_memory.models import (
    FactCandidate,
    FactCategory,
    IngestRequest,
    MemoryDecision,
    MemoryOperation,
    RelationCandidate,
    RejectedCandidate,
    Sensitivity,
)


class MemoryState(TypedDict, total=False):
    request: IngestRequest
    memory_worthy: bool
    candidates: list[FactCandidate]
    relations: list[RelationCandidate]
    accepted: list[FactCandidate]
    accepted_relations: list[RelationCandidate]
    rejected: list[RejectedCandidate]
    decisions: list[MemoryDecision]


def build_memory_graph(
    extractor: FactExtractor,
    repository: MemoryRepository,
    *, min_confidence: float,
):
    def extract_facts(state: MemoryState) -> MemoryState:
        request = state["request"]
        result = extractor.extract(request)
        return {
            "memory_worthy": result.is_memory_worthy,
            "candidates": result.candidates,
            "relations": result.relations,
        }

    def route_after_extraction(state: MemoryState) -> str:
        if not state.get("memory_worthy") or not state.get("candidates"):
            return "finish"
        return "policy"

    def apply_policy(state: MemoryState) -> MemoryState:
        request = state["request"]
        policy = repository.get_memory_policy(request.user_id)
        accepted: list[FactCandidate] = []
        accepted_relations: list[RelationCandidate] = []
        rejected: list[RejectedCandidate] = []
        for candidate in state.get("candidates", []):
            if (
                not policy.enabled
                and candidate.operation != MemoryOperation.retract
            ):
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="用户已关闭长期记忆")
                )
            elif candidate.category not in policy.allowed_categories:
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="用户策略禁止保存该类记忆")
                )
            elif candidate.key in policy.blocked_keys:
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="用户策略禁止保存该字段")
                )
            elif not candidate.explicit:
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="不是用户明确表达的信息")
                )
            elif candidate.confidence < min_confidence:
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="提取置信度低于阈值")
                )
            elif candidate.sensitivity == Sensitivity.restricted:
                rejected.append(
                    RejectedCandidate(key=candidate.key, reason="禁止保存的高风险敏感信息")
                )
            else:
                if (
                    candidate.operation != MemoryOperation.retract
                    and candidate.category in (FactCategory.preference, FactCategory.event)
                ):
                    candidate = candidate.model_copy(
                        update={"operation": MemoryOperation.append}
                    )
                accepted.append(candidate)
        for relation in state.get("relations", []):
            relation_key = (
                f"{relation.subject_key}:{relation.predicate}:{relation.object_key}"
            )
            if not policy.enabled:
                rejected.append(
                    RejectedCandidate(key=relation_key, reason="用户已关闭长期记忆")
                )
            elif (
                relation.subject_key in policy.blocked_keys
                or relation.object_key in policy.blocked_keys
            ):
                rejected.append(
                    RejectedCandidate(key=relation_key, reason="关系包含被禁止的字段")
                )
            elif not relation.explicit:
                rejected.append(
                    RejectedCandidate(key=relation_key, reason="不是用户明确表达的关系")
                )
            elif relation.confidence < min_confidence:
                rejected.append(
                    RejectedCandidate(key=relation_key, reason="关系置信度低于阈值")
                )
            else:
                accepted_relations.append(relation)
        return {
            "accepted": accepted,
            "accepted_relations": accepted_relations,
            "rejected": rejected,
        }

    def route_after_policy(state: MemoryState) -> str:
        return "persist" if state.get("accepted") else "finish"

    def persist(state: MemoryState) -> MemoryState:
        request = state["request"]
        decisions = repository.apply_candidates(
            request.user_id, request.message_id, state.get("accepted", [])
        )
        decisions.extend(
            repository.apply_relations(
                request.user_id,
                request.message_id,
                state.get("accepted_relations", []),
            )
        )
        return {"decisions": decisions}

    def finish(state: MemoryState) -> MemoryState:
        return {
            "decisions": state.get("decisions", []),
            "rejected": state.get("rejected", []),
        }

    graph = StateGraph(MemoryState)
    graph.add_node("extract", extract_facts)
    graph.add_node("policy", apply_policy)
    graph.add_node("persist", persist)
    graph.add_node("finish", finish)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges(
        "extract", route_after_extraction, {"policy": "policy", "finish": "finish"}
    )
    graph.add_conditional_edges(
        "policy", route_after_policy, {"persist": "persist", "finish": "finish"}
    )
    graph.add_edge("persist", "finish")
    graph.add_edge("finish", END)
    compiled = graph.compile()

    class ReliableMemoryGraph:
        def invoke(self, state: MemoryState) -> MemoryState:
            request = state["request"]
            repository.save_message(request)
            repository.begin_memory_extraction(request.user_id, request.message_id)
            try:
                result = compiled.invoke(state)
            except Exception as exc:
                repository.finish_memory_extraction(
                    request.user_id,
                    request.message_id,
                    succeeded=False,
                    error_code=type(exc).__name__,
                )
                raise
            repository.finish_memory_extraction(
                request.user_id,
                request.message_id,
                succeeded=True,
            )
            return result

    return ReliableMemoryGraph()

