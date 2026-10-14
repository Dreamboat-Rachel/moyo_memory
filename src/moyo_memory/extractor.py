import logging
import re
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from moyo_memory.config import Settings
from moyo_memory.models import ExtractionResult, IngestRequest


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是用户长期记忆提取器。只提取用户明确陈述、未来对话确实有用的信息。

允许的 category：
- profile：姓名、用户明确表达的性别、生日、所在地、职业、家庭关系等。
- preference：爱好、喜欢或不喜欢的事物、沟通习惯。
- event：毕业、结婚、搬家、换工作等有长期意义的事件。

规则：
1. 不根据姓名、语气或常识推断性别、年龄、住址等信息，推断内容 explicit=false。
2. evidence 必须是消息中的短原文。value 是展示给用户的事实值，必须保持用户原文语言，
   禁止翻译或音译；normalized_value 用相同语言生成稳定、简洁的规范表达，也禁止翻译。
   例如“喜欢玩牌”应输出 value="玩牌"、normalized_value="打牌"，不能输出
   value="playing cards" 或 normalized_value="playing_cards"。只有 key 和 predicate 使用英文。
3. 单值资料及明确变更用 replace；可同时存在的爱好和事件用 append；用户要求忘记、否认或撤回时用 retract。
4. key 使用简洁 snake_case 英文，例如 name、gender、birth_date、city、occupation、hobby、favorite_food、job_change。
5. 密码、验证码、访问令牌、私钥、完整银行卡号、完整身份证件号码标记为 restricted；不得把它们改写到 evidence 之外。
6. 医疗、宗教、政治、性取向等标记为 sensitive；生日、性别、地址等标记为 personal；普通爱好可标记 normal。
7. 寒暄、临时指令和无长期价值内容的 is_memory_worthy=false。
8. 日期不确定时不要编造。相对时间只有能结合消息时间可靠确定时才填写 valid_from；
   用户明确说明信息的结束或过期时间时填写 valid_to。
9. relations 只表达两个事实间由原文明确支持的关系。端点用对应事实的 key 和 normalized_value。
10. predicate 使用稳定的 snake_case 英文，例如 located_in、happened_at、caused_by、
    works_at、related_to。只有同一消息或历史记忆中确实存在两个端点时才提取关系。
"""

LANGUAGE_RETRY_PROMPT = """上次结果把事实值翻译成了另一种语言，违反了规则。
重新提取时，value 和 normalized_value 必须保持 evidence 的原始语言；只允许 key 和
predicate 使用英文。不要解释，只返回符合结构的提取结果。"""

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


class ExtractionLanguageMismatch(ValueError):
    """Raised when a model translates a user-facing memory value."""


def validate_extraction_language(
    result: ExtractionResult, source_content: str
) -> ExtractionResult:
    source_folded = source_content.casefold()
    for candidate in result.candidates:
        evidence_has_cjk = bool(_CJK_RE.search(candidate.evidence))
        evidence_has_latin = bool(_LATIN_RE.search(candidate.evidence))
        if evidence_has_cjk == evidence_has_latin:
            continue
        for field_value in (candidate.value, candidate.normalized_value):
            appears_in_source = field_value.casefold() in source_folded
            value_has_cjk = bool(_CJK_RE.search(field_value))
            value_has_latin = bool(_LATIN_RE.search(field_value))
            translated_from_chinese = (
                evidence_has_cjk
                and value_has_latin
                and not value_has_cjk
                and not appears_in_source
            )
            translated_from_latin = (
                evidence_has_latin
                and value_has_cjk
                and not value_has_latin
                and not appears_in_source
            )
            if translated_from_chinese or translated_from_latin:
                raise ExtractionLanguageMismatch(
                    f"memory value language differs from evidence for key {candidate.key}"
                )
    return result


class ModelNotConfigured(RuntimeError):
    pass


class FactExtractor(Protocol):
    def extract(self, request: IngestRequest) -> ExtractionResult: ...


class DisabledExtractor:
    def extract(self, request: IngestRequest) -> ExtractionResult:
        raise ModelNotConfigured("未配置 MODEL_API_KEY，无法调用信息提取模型")


class LLMFactExtractor:
    def __init__(self, settings: Settings):
        model = ChatOpenAI(
            model=settings.model_name,
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            temperature=0,
            extra_body={"enable_thinking": settings.model_enable_thinking},
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        )
        self.structured_model = model.with_structured_output(ExtractionResult)
        self.max_attempts = settings.memory_extraction_max_attempts

    def extract(self, request: IngestRequest) -> ExtractionResult:
        prompt = (
            f"消息时间：{request.occurred_at.isoformat()}\n"
            f"用户消息：{request.content}"
        )
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self.structured_model.invoke(messages)
                validated = ExtractionResult.model_validate(result)
                return validate_extraction_language(validated, request.content)
            except Exception as exc:
                if attempt >= self.max_attempts:
                    raise
                if isinstance(exc, ExtractionLanguageMismatch):
                    messages.append(SystemMessage(content=LANGUAGE_RETRY_PROMPT))
                logger.warning(
                    "Memory extraction attempt %s/%s failed; retrying",
                    attempt,
                    self.max_attempts,
                    exc_info=True,
                )
        raise RuntimeError("memory extraction attempts exhausted")


def create_extractor(settings: Settings) -> FactExtractor:
    if not settings.model_api_key:
        return DisabledExtractor()
    return LLMFactExtractor(settings)

