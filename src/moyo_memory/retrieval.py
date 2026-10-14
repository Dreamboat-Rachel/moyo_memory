import re
from collections import defaultdict
from datetime import datetime, timezone

from moyo_memory.models import (
    FactCategory,
    FactView,
    RecallHit,
    RecallRelation,
    RecallRequest,
    RecallResponse,
    RecallScores,
    RelationView,
    SemanticMatch,
)


FIELD_ALIASES: dict[str, set[str]] = {
    "name": {"姓名", "名字", "叫什么", "称呼"},
    "gender": {"性别", "男女"},
    "birth_date": {"生日", "出生", "出生日期", "出生年月"},
    "city": {"城市", "住址", "居住", "住在哪里", "所在地", "地点"},
    "occupation": {"职业", "工作", "做什么", "职位"},
    "employer": {"公司", "单位", "雇主", "在哪里工作"},
    "hobby": {"爱好", "兴趣", "喜欢", "平时做什么"},
    "favorite_food": {"喜欢吃", "食物", "口味", "美食"},
    "job_change": {"换工作", "入职", "加入公司", "工作经历"},
}

RELATION_LABELS = {
    "works_at": "任职于",
    "located_in": "位于",
    "joined": "加入",
    "happened_at": "发生于",
    "caused_by": "源于",
    "related_to": "相关",
}

FIELD_CATEGORIES = {
    "name": FactCategory.profile,
    "gender": FactCategory.profile,
    "birth_date": FactCategory.profile,
    "city": FactCategory.profile,
    "occupation": FactCategory.profile,
    "employer": FactCategory.profile,
    "hobby": FactCategory.preference,
    "favorite_food": FactCategory.preference,
    "job_change": FactCategory.event,
}

EVENT_ALIASES = {"事件", "经历", "发生", "入职", "搬家", "换工作"}


def infer_query_categories(query: str) -> list[FactCategory]:
    normalized = query.casefold().strip()
    inferred: set[FactCategory] = set()
    for key, aliases in FIELD_ALIASES.items():
        if key in normalized or any(alias in normalized for alias in aliases):
            category = FIELD_CATEGORIES.get(key)
            if category is not None:
                inferred.add(category)
    if any(alias in normalized for alias in EVENT_ALIASES):
        inferred.add(FactCategory.event)
    return [category for category in FactCategory if category in inferred]


def infer_profile_keys(query: str) -> list[str]:
    """Narrow clear profile questions without applying a name to the wrong subject."""
    normalized = query.casefold().strip()
    if any(alias in normalized for alias in FIELD_ALIASES["name"]):
        if any(subject in normalized for subject in ("宠物", "狗", "猫")):
            return ["pet_name", "dog_name", "cat_name", "pet", "dog", "cat"]
        if any(subject in normalized for subject in ("公司", "单位", "雇主")):
            return ["employer"]
        if any(subject in normalized for subject in ("对象", "伴侣", "爱人")):
            return ["partner_name", "spouse_name"]
        if any(subject in normalized for subject in ("朋友", "父亲", "母亲", "孩子")):
            return ["friend_name", "father_name", "mother_name", "child_name"]
    keys = set()
    for key, aliases in FIELD_ALIASES.items():
        if FIELD_CATEGORIES.get(key) != FactCategory.profile:
            continue
        if key in normalized or any(alias in normalized for alias in aliases):
            keys.add(key)
    if keys & {"occupation", "employer"}:
        keys.update({"occupation", "employer", "city"})
    return sorted(keys)


def _terms(text: str) -> set[str]:
    normalized = text.casefold().strip()
    terms = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for chunk in chinese_chunks:
        terms.add(chunk)
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return terms


def _score_fact(query: str, query_terms: set[str], fact: FactView) -> tuple[float, list[str]]:
    value = fact.normalized_value.casefold()
    evidence = fact.evidence.casefold()
    key = fact.key.casefold()
    searchable = f"{key} {value} {evidence}"
    score = 0.0
    reasons: list[str] = []

    if query in value:
        score += 5.0
        reasons.append("值精确匹配")
    elif value and value in query:
        score += 4.0
        reasons.append("问题包含该事实")
    if query in evidence:
        score += 2.5
        reasons.append("原始证据匹配")
    if key in query:
        score += 2.0
        reasons.append("字段名匹配")

    aliases = FIELD_ALIASES.get(fact.key, set())
    matched_aliases = [alias for alias in aliases if alias in query]
    if matched_aliases:
        score += 3.0
        reasons.append(f"字段语义匹配：{matched_aliases[0]}")

    fact_terms = _terms(searchable)
    overlap = query_terms & fact_terms
    if overlap:
        coverage = len(overlap) / max(len(query_terms), 1)
        score += min(3.0, len(overlap) * 0.45 + coverage * 1.5)
        reasons.append("关键词匹配")

    if fact.category == FactCategory.event and any(
        word in query for word in ("事件", "经历", "发生", "过去", "最近")
    ):
        score += 1.0
        reasons.append("事件类型匹配")
    return score, list(dict.fromkeys(reasons))


def _recency_score(created_at: datetime) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - created_at).total_seconds() / 86400, 0)
    return 1 / (1 + age_days / 365)


def recall_memories(
    user_id: str,
    request: RecallRequest,
    facts: list[FactView],
    relations: list[RelationView],
    *,
    semantic_matches: list[SemanticMatch] | None = None,
    semantic_available: bool = False,
    warnings: list[str] | None = None,
) -> RecallResponse:
    query = request.query.casefold().strip()
    query_terms = _terms(query)
    applied_categories = request.categories
    if applied_categories is None and request.auto_category_filter:
        inferred_categories = infer_query_categories(query)
        applied_categories = inferred_categories or None
    allowed = set(applied_categories) if applied_categories else None
    applied_keys = []
    if (
        request.categories is None
        and request.auto_category_filter
        and applied_categories == [FactCategory.profile]
    ):
        applied_keys = infer_profile_keys(query)
    facts = [
        fact for fact in facts
        if (allowed is None or fact.category in allowed)
        and (not applied_keys or fact.key in applied_keys)
    ]
    fact_by_id = {fact.id: fact for fact in facts}

    lexical_raw: dict[str, float] = {}
    reasons_by_fact: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        score, reasons = _score_fact(query, query_terms, fact)
        if score > 0:
            lexical_raw[fact.id] = score
            reasons_by_fact[fact.id].extend(reasons)

    # A tiny overlap must not become a perfect match just because it is the best one.
    max_lexical = max(6.0, max(lexical_raw.values(), default=0.0))
    semantic_scores: dict[str, float] = {}
    for match in semantic_matches or []:
        if match.fact_id not in fact_by_id or match.score < request.semantic_min_score:
            continue
        semantic_scores[match.fact_id] = max(0.0, min(1.0, match.score))
        reasons_by_fact[match.fact_id].append(
            f"向量语义匹配：{match.score:.3f}"
        )

    candidate_ids = set(lexical_raw) | set(semantic_scores)
    scored: dict[str, RecallScores] = {}
    if semantic_available:
        lexical_weight, semantic_weight = 0.35, 0.50
        confidence_weight, recency_weight = 0.10, 0.05
    else:
        lexical_weight, semantic_weight = 0.75, 0.0
        confidence_weight, recency_weight = 0.20, 0.05
    for fact_id in sorted(candidate_ids):
        fact = fact_by_id[fact_id]
        lexical = lexical_raw.get(fact_id, 0.0) / max_lexical
        semantic = semantic_scores.get(fact_id, 0.0)
        confidence = fact.confidence
        recency = _recency_score(fact.created_at)
        total = (
            lexical * lexical_weight
            + semantic * semantic_weight
            + confidence * confidence_weight
            + recency * recency_weight
        )
        scored[fact_id] = RecallScores(
            lexical=round(lexical, 4),
            semantic=round(semantic, 4),
            confidence=round(confidence, 4),
            recency=round(recency, 4),
            total=round(total, 4),
        )

    relations_by_fact: dict[str, list[tuple[RelationView, str, str]]] = defaultdict(list)
    for relation in sorted(relations, key=lambda item: item.id):
        if relation.source_fact_id in fact_by_id and relation.target_fact_id in fact_by_id:
            relations_by_fact[relation.source_fact_id].append(
                (relation, "outgoing", relation.target_fact_id)
            )
            relations_by_fact[relation.target_fact_id].append(
                (relation, "incoming", relation.source_fact_id)
            )

    strongest_direct_score = max((score.total for score in scored.values()), default=0.0)
    score_threshold = max(
        request.min_score,
        strongest_direct_score * request.relative_score_threshold,
    )
    if request.include_graph_neighbors:
        # Snapshot numeric scores so graph boosts never feed back into propagation.
        seed_scores = {fact_id: score.total for fact_id, score in scored.items()}
        for fact_id, seed_score in seed_scores.items():
            if seed_score < score_threshold:
                continue
            for relation, _, neighbor_id in relations_by_fact.get(fact_id, []):
                if neighbor_id not in fact_by_id:
                    continue
                propagated = seed_score * 0.35 * relation.confidence
                current = scored.get(neighbor_id)
                if current is None:
                    neighbor = fact_by_id[neighbor_id]
                    current = RecallScores(
                        confidence=round(neighbor.confidence, 4),
                        recency=round(_recency_score(neighbor.created_at), 4),
                    )
                    scored[neighbor_id] = current
                if propagated > current.graph:
                    current.graph = round(propagated, 4)
                    current.total = round(
                        current.lexical * lexical_weight
                        + current.semantic * semantic_weight
                        + current.confidence * confidence_weight
                        + current.recency * recency_weight
                        + current.graph,
                        4,
                    )
                    reasons_by_fact[neighbor_id].append(
                        f"通过关系 {relation.predicate} 扩展"
                    )

    ranked = sorted(scored.items(), key=lambda item: (-item[1].total, item[0]))
    ranked = [item for item in ranked if item[1].total >= score_threshold]
    hits: list[RecallHit] = []
    selected_ids = {fact_id for fact_id, _ in ranked[: request.limit]}
    for fact_id, score_parts in ranked[: request.limit]:
        related: list[RecallRelation] = []
        if request.include_graph_neighbors:
            for relation, direction, neighbor_id in relations_by_fact.get(fact_id, []):
                neighbor = fact_by_id.get(neighbor_id)
                if neighbor is None or neighbor_id not in selected_ids:
                    continue
                related.append(
                    RecallRelation(
                        predicate=relation.predicate,
                        direction=direction,
                        fact=neighbor,
                        confidence=relation.confidence,
                    )
                )
        hits.append(
            RecallHit(
                fact=fact_by_id[fact_id],
                score=score_parts.total,
                scores=score_parts,
                match_reasons=list(dict.fromkeys(reasons_by_fact[fact_id])),
                related=related,
            )
        )

    context_lines: list[str] = []
    for hit in hits:
        fact = hit.fact
        context_lines.append(f"- {fact.key}: {fact.value}")
        for relation in hit.related:
            if relation.fact.id not in selected_ids:
                continue
            label = RELATION_LABELS.get(relation.predicate, relation.predicate)
            if relation.direction == "outgoing":
                context_lines.append(f"  - {label} -> {relation.fact.value}")
    return RecallResponse(
        user_id=user_id,
        query=request.query,
        hits=hits,
        context="\n".join(context_lines),
        retrieval_mode=(
            "hybrid_vector_graph" if semantic_available else "lexical_graph"
        ),
        warnings=warnings or [],
        applied_categories=applied_categories or [],
        applied_keys=applied_keys,
        score_threshold=round(score_threshold, 4),
    )
