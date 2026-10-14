from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from moyo_memory.models import (
    FactCategory,
    FactView,
    RecallRequest,
    RelationView,
    SemanticMatch,
)
from moyo_memory.retrieval import infer_query_categories, recall_memories


def fact(identifier, key, value, category=FactCategory.profile):
    now = datetime.now(timezone.utc)
    return FactView(
        id=identifier, user_id="u-1", category=category, key=key,
        value=value, normalized_value=value, confidence=0.96,
        evidence=value, source_message_id="m-1", sensitivity="personal",
        status="active", valid_from=None, valid_to=None,
        created_at=now, updated_at=now,
    )


def relation(source, target):
    now = datetime.now(timezone.utc)
    return RelationView(
        id=f"{source}-{target}", user_id="u-1", source_fact_id=source,
        target_fact_id=target, predicate="related_to", confidence=0.95,
        evidence="explicit relationship", source_message_id="m-1",
        status="active", created_at=now, updated_at=now,
    )


@pytest.mark.parametrize(("query", "expected"), [
    ("我叫什么名字", [FactCategory.profile]),
    ("我都有什么爱好", [FactCategory.preference]),
    ("我最近喜欢什么", [FactCategory.preference]),
    ("去年发生了什么事情", [FactCategory.event]),
    ("我的工作和爱好是什么", [FactCategory.profile, FactCategory.preference]),
    ("有什么适合我的户外运动", []),
])
def test_category_intent_is_conservative(query, expected):
    assert infer_query_categories(query) == expected


def test_hobbies_exclude_unrelated_profile_and_event():
    facts = [
        fact("h", "hobby", "徒步", FactCategory.preference),
        fact("n", "name", "林墨"),
        fact("e", "job_change", "加入星海科技", FactCategory.event),
    ]
    result = recall_memories(
        "u-1", RecallRequest(query="我都有什么爱好"), facts, [],
        semantic_matches=[SemanticMatch(fact_id=item.id, score=0.6) for item in facts],
        semantic_available=True,
    )
    assert [hit.fact.id for hit in result.hits] == ["h"]
    assert result.applied_categories == [FactCategory.preference]


def test_explicit_category_overrides_automatic_inference():
    result = recall_memories(
        "u-1", RecallRequest(query="我有什么爱好", categories=[FactCategory.event]),
        [fact("e", "job_change", "加入公司", FactCategory.event)], [],
        semantic_matches=[SemanticMatch(fact_id="e", score=0.9)],
        semantic_available=True,
    )
    assert result.applied_categories == [FactCategory.event]
    assert [hit.fact.id for hit in result.hits] == ["e"]


def test_relative_threshold_removes_weak_tail_and_related_metadata():
    facts = [fact("a", "custom", "紫色"), fact("b", "custom", "蓝色")]
    result = recall_memories(
        "u-1", RecallRequest(query="紫色", relative_score_threshold=0.95),
        facts, [relation("a", "b")],
        semantic_matches=[SemanticMatch(fact_id="b", score=0.26)],
        semantic_available=True,
    )
    assert [hit.fact.id for hit in result.hits] == ["a"]
    assert result.hits[0].related == []
    assert "蓝色" not in result.context


def test_tiny_keyword_overlap_does_not_get_full_relevance():
    result = recall_memories(
        "u-1", RecallRequest(query="苹果公司的股票走势怎么样"),
        [fact("a", "custom", "我买了苹果汁")], [],
    )
    assert result.hits == []
    assert result.context == ""


def test_absolute_threshold_can_return_no_results():
    result = recall_memories(
        "u-1", RecallRequest(query="户外运动建议"),
        [fact("a", "custom", "杭州")], [],
        semantic_matches=[SemanticMatch(fact_id="a", score=0.26)],
        semantic_available=True,
    )
    assert result.hits == []


def test_graph_propagation_is_order_independent(monkeypatch):
    monkeypatch.setattr("moyo_memory.retrieval._recency_score", lambda _: 1.0)
    facts = [fact("a", "custom", "甲"), fact("b", "custom", "乙"),
             fact("c", "custom", "丙")]
    matches = [SemanticMatch(fact_id="a", score=0.9),
               SemanticMatch(fact_id="b", score=0.5)]
    links = [relation("a", "b"), relation("b", "c")]
    request = RecallRequest(query="测试", min_score=0, relative_score_threshold=0)
    results = [recall_memories(
        "u-1", request, facts, ordered_links,
        semantic_matches=matches, semantic_available=True,
    ) for ordered_links in (links, list(reversed(links)))]
    assert results[0].model_dump() == results[1].model_dump()
    c = next(hit for hit in results[0].hits if hit.fact.id == "c")
    # Only b's original score propagates to c, not its boost from a.
    assert c.scores.graph == pytest.approx(0.1317, abs=0.0001)


def test_blank_query_is_rejected():
    with pytest.raises(ValidationError):
        RecallRequest(query="   ")


def test_work_question_does_not_include_user_name():
    facts = [fact("n", "name", "林墨"), fact("o", "occupation", "设计师"),
             fact("e", "employer", "星海科技")]
    result = recall_memories(
        "u-1", RecallRequest(query="我做什么工作"), facts, [],
        semantic_matches=[SemanticMatch(fact_id=item.id, score=0.9) for item in facts],
        semantic_available=True,
    )
    assert {hit.fact.id for hit in result.hits} == {"o", "e"}


def test_pet_name_is_not_confused_with_user_name():
    facts = [fact("n", "name", "林墨"), fact("p", "pet_name", "小白")]
    result = recall_memories(
        "u-1", RecallRequest(query="我养的宠物叫什么名字"), facts, [],
        semantic_matches=[SemanticMatch(fact_id=item.id, score=0.8) for item in facts],
        semantic_available=True,
    )
    assert [hit.fact.id for hit in result.hits] == ["p"]
    without_pet = recall_memories(
        "u-1", RecallRequest(query="我养的宠物叫什么名字"), facts[:1], [],
        semantic_matches=[SemanticMatch(fact_id="n", score=0.8)],
        semantic_available=True,
    )
    assert without_pet.hits == []
