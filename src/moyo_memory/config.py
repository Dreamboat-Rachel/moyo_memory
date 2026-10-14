from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Moyo Memory"
    database_url: str = (
        "mysql+pymysql://moyo:moyo_password@127.0.0.1:3306/"
        "moyo_memory?charset=utf8mb4"
    )
    model_api_key: str = ""
    model_name: str = "qwen3.8-max"
    model_base_url: str | None = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    model_enable_thinking: bool = True
    model_timeout_seconds: float = Field(default=60, gt=0, le=300)
    memory_extraction_max_attempts: int = Field(default=3, ge=1, le=10)
    memory_min_confidence: float = Field(default=0.72, ge=0, le=1)
    recall_min_score: float = Field(default=0.35, ge=0, le=2)
    recall_relative_score_threshold: float = Field(default=0.55, ge=0, le=1)
    recall_auto_category_filter: bool = True
    rerank_enabled: bool = False
    rerank_model: str = "qwen3.7-text-rerank"
    rerank_endpoint: str = (
        "https://maas.qianwenaiapi.com/api/v1/services/"
        "rerank/text-rerank/text-rerank"
    )
    rerank_api_key: str = ""
    rerank_min_score: float = Field(default=0.2, ge=0, le=1)
    rerank_max_candidates: int = Field(default=24, ge=1, le=100)
    rerank_timeout_seconds: float = Field(default=20, gt=0, le=120)
    sql_echo: bool = False
    auth_enabled: bool = False
    auth_admin_key: str = ""
    semantic_recall_enabled: bool = False
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_base_url: str | None = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    embedding_dimensions: int = Field(default=1024, gt=0, le=65536)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=300)
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "moyo_memory_facts_v1"
    qdrant_timeout_seconds: float = Field(default=3, gt=0, le=60)
    semantic_failure_threshold: int = Field(default=3, ge=1, le=100)
    semantic_cooldown_seconds: float = Field(default=30, gt=0, le=600)
    vector_index_batch_size: int = Field(default=64, ge=1, le=1000)
    vector_index_max_attempts: int = Field(default=8, ge=1, le=100)
    chat_history_max_turns: int = Field(default=12, ge=1, le=100)
    chat_context_max_tokens: int = Field(default=16000, ge=2048, le=1000000)
    chat_max_output_tokens: int = Field(default=2048, ge=128, le=32768)
    chat_temperature: float = Field(default=0.3, ge=0, le=2)
    chat_summary_enabled: bool = True

    @property
    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.model_api_key

    @property
    def resolved_rerank_api_key(self) -> str:
        return self.rerank_api_key or self.model_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

