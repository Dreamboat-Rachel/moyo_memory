import json

from moyo_memory.evaluation import (
    RecallEvaluationCase,
    evaluate_rankings,
    load_evaluation_cases,
)


def test_load_and_evaluate_recall_cases(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "user_id": "u-1",
                        "query": "name",
                        "relevant_fact_ids": ["a", "b"],
                    }
                ),
                json.dumps(
                    {
                        "user_id": "u-1",
                        "query": "weather",
                        "relevant_fact_ids": [],
                        "expect_empty": True,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    cases = load_evaluation_cases(path)
    report = evaluate_rankings(
        [(cases[0], ["x", "a"]), (cases[1], ["z"])],
        k=2,
    )

    assert report["recall_at_k"] == 0.5
    assert report["precision_at_k"] == 0.5
    assert report["mrr"] == 0.5
    assert report["no_answer_false_positive_rate"] == 1.0


def test_no_answer_without_hits_is_not_false_positive() -> None:
    case = RecallEvaluationCase("u-1", "unknown", (), expect_empty=True)
    report = evaluate_rankings([(case, [])], k=3)
    assert report["no_answer_false_positive_rate"] == 0
