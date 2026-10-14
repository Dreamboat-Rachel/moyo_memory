from moyo_memory.config import Settings
import pytest

from moyo_memory.extractor import (
    ExtractionLanguageMismatch,
    LLMFactExtractor,
    validate_extraction_language,
)
from moyo_memory.models import (
    ExtractionResult,
    FactCandidate,
    FactCategory,
    IngestRequest,
    MemoryOperation,
)

from datetime import datetime, timezone


class FakeChatModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def with_structured_output(self, schema):
        self.schema = schema
        return self


def test_qwen_compatible_model_configuration(monkeypatch) -> None:
    captured = {}

    def build_model(**kwargs):
        captured.update(kwargs)
        return FakeChatModel(**kwargs)

    monkeypatch.setattr("moyo_memory.extractor.ChatOpenAI", build_model)
    settings = Settings(_env_file=None, model_api_key="test-key")

    LLMFactExtractor(settings)

    assert captured["model"] == "qwen3.8-max"
    assert captured["base_url"] == (
        "https://maas.qianwenaiapi.com/compatible-mode/v1"
    )
    assert captured["extra_body"] == {"enable_thinking": True}
    assert captured["timeout"] == 60
    assert captured["max_retries"] == 0


def test_memory_extraction_retries_transient_failure(monkeypatch) -> None:
    extractor = object.__new__(LLMFactExtractor)
    extractor.max_attempts = 3
    calls = 0

    class FlakyStructuredModel:
        def invoke(self, messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary failure")
            return ExtractionResult(is_memory_worthy=False)

    extractor.structured_model = FlakyStructuredModel()
    request = IngestRequest(
        user_id="u-retry",
        message_id="m-retry",
        content="你好",
        occurred_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )

    result = extractor.extract(request)

    assert result.is_memory_worthy is False
    assert calls == 2


def extracted_hobby(value: str, normalized_value: str, evidence: str):
    return ExtractionResult(
        is_memory_worthy=True,
        candidates=[
            FactCandidate(
                category=FactCategory.preference,
                key="hobby",
                value=value,
                normalized_value=normalized_value,
                operation=MemoryOperation.append,
                confidence=0.95,
                evidence=evidence,
            )
        ],
    )


def test_rejects_translation_of_chinese_memory_value() -> None:
    with pytest.raises(ExtractionLanguageMismatch):
        validate_extraction_language(
            extracted_hobby("playing cards", "playing_cards", "玩牌"),
            "我喜欢玩牌",
        )


def test_accepts_same_language_normalization_and_original_english_name() -> None:
    chinese = extracted_hobby("玩牌", "打牌", "玩牌")
    assert validate_extraction_language(chinese, "我喜欢玩牌") is chinese

    english_name = ExtractionResult(
        is_memory_worthy=True,
        candidates=[
            FactCandidate(
                category=FactCategory.profile,
                key="name",
                value="Alice",
                normalized_value="alice",
                operation=MemoryOperation.replace,
                confidence=0.99,
                evidence="Alice",
            )
        ],
    )
    assert validate_extraction_language(english_name, "我叫 Alice") is english_name


def test_language_mismatch_retries_with_correction_prompt() -> None:
    extractor = object.__new__(LLMFactExtractor)
    extractor.max_attempts = 3
    calls = []

    class TranslatingOnceModel:
        def invoke(self, messages):
            calls.append(messages.copy())
            if len(calls) == 1:
                return extracted_hobby("playing cards", "playing_cards", "玩牌")
            return extracted_hobby("玩牌", "打牌", "玩牌")

    extractor.structured_model = TranslatingOnceModel()
    request = IngestRequest(
        user_id="u-language",
        message_id="m-language",
        content="我喜欢玩牌",
        occurred_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )

    result = extractor.extract(request)

    assert result.candidates[0].value == "玩牌"
    assert len(calls) == 2
    assert "保持 evidence 的原始语言" in calls[1][-1].content

