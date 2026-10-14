import json
import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RecallEvaluationCase:
    user_id: str
    query: str
    relevant_fact_ids: tuple[str, ...]
    expect_empty: bool = False


def load_evaluation_cases(path: Path) -> list[RecallEvaluationCase]:
    cases: list[RecallEvaluationCase] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
            cases.append(
                RecallEvaluationCase(
                    user_id=str(item["user_id"]),
                    query=str(item["query"]),
                    relevant_fact_ids=tuple(
                        str(value) for value in item.get("relevant_fact_ids", [])
                    ),
                    expect_empty=bool(item.get("expect_empty", False)),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid evaluation case on line {line_number}") from exc
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    return cases


def evaluate_rankings(
    results: Iterable[tuple[RecallEvaluationCase, list[str]]], *, k: int
) -> dict[str, Any]:
    if k < 1:
        raise ValueError("k must be at least 1")
    answered_count = 0
    no_answer_count = 0
    false_positive_count = 0
    recall_sum = 0.0
    precision_sum = 0.0
    reciprocal_rank_sum = 0.0
    details: list[dict[str, Any]] = []

    for case, ranking in results:
        top_ids = ranking[:k]
        relevant = set(case.relevant_fact_ids)
        is_no_answer = case.expect_empty or not relevant
        if is_no_answer:
            no_answer_count += 1
            false_positive = bool(top_ids)
            false_positive_count += int(false_positive)
            details.append(
                {
                    "user_id": case.user_id,
                    "query": case.query,
                    "expected": [],
                    "returned": top_ids,
                    "no_answer_false_positive": false_positive,
                }
            )
            continue

        answered_count += 1
        matched = relevant.intersection(top_ids)
        recall = len(matched) / len(relevant)
        precision = len(matched) / k
        reciprocal_rank = next(
            (1 / rank for rank, fact_id in enumerate(top_ids, 1) if fact_id in relevant),
            0.0,
        )
        recall_sum += recall
        precision_sum += precision
        reciprocal_rank_sum += reciprocal_rank
        details.append(
            {
                "user_id": case.user_id,
                "query": case.query,
                "expected": sorted(relevant),
                "returned": top_ids,
                "recall": round(recall, 4),
                "precision": round(precision, 4),
                "reciprocal_rank": round(reciprocal_rank, 4),
            }
        )

    total = answered_count + no_answer_count
    return {
        "k": k,
        "case_count": total,
        "answered_case_count": answered_count,
        "no_answer_case_count": no_answer_count,
        "recall_at_k": round(recall_sum / answered_count, 4) if answered_count else 0,
        "precision_at_k": (
            round(precision_sum / answered_count, 4) if answered_count else 0
        ),
        "mrr": (
            round(reciprocal_rank_sum / answered_count, 4) if answered_count else 0
        ),
        "no_answer_false_positive_rate": (
            round(false_positive_count / no_answer_count, 4) if no_answer_count else 0
        ),
        "details": details,
    }


def recall_from_api(base_url: str, user_id: str, query: str, k: int) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/v1/users/{quote(user_id, safe='')}/recall"
    request = Request(
        endpoint,
        data=json.dumps({"query": query, "limit": k}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-User-ID": user_id},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Recall request failed for user {user_id}: {exc}") from exc
    return [item["fact"]["id"] for item in payload.get("hits", [])]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Moyo recall against JSONL cases")
    parser.add_argument("cases", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("-k", type=int, default=8)
    arguments = parser.parse_args()
    cases = load_evaluation_cases(arguments.cases)
    results = [
        (
            case,
            recall_from_api(arguments.base_url, case.user_id, case.query, arguments.k),
        )
        for case in cases
    ]
    print(json.dumps(evaluate_rankings(results, k=arguments.k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
