from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Moyo Memory"
    app_env: Literal["development", "production"] = "development"
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
    auth_user_signing_key: str = ""
    auth_signature_ttl_seconds: int = Field(default=300, ge=30, le=3600)
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

    @model_validator(mode="after")
    def validate_production_settings(self):
        if self.app_env != "production":
            return self
        if not self.auth_enabled:
            raise ValueError("APP_ENV=production requires AUTH_ENABLED=true")
        if len(self.auth_admin_key) < 32:
            raise ValueError("production requires AUTH_ADMIN_KEY with at least 32 characters")
        if len(self.auth_user_signing_key) < 32:
            raise ValueError(
                "production requires AUTH_USER_SIGNING_KEY with at least 32 characters"
            )
        if self.database_url.startswith("sqlite"):
            raise ValueError("production requires a MySQL DATABASE_URL")
        if self.sql_echo:
            raise ValueError("SQL_ECHO must be false in production")
        if not self.model_api_key:
            raise ValueError("production requires MODEL_API_KEY")
        secret_values = {
            "AUTH_ADMIN_KEY": self.auth_admin_key,
            "AUTH_USER_SIGNING_KEY": self.auth_user_signing_key,
            "MODEL_API_KEY": self.model_api_key,
            "QDRANT_API_KEY": self.qdrant_api_key,
        }
        placeholder_markers = ("replace-with", "change-me", "placeholder", "your-key")
        for name, value in secret_values.items():
            if value and any(marker in value.casefold() for marker in placeholder_markers):
                raise ValueError(f"production {name} still contains a placeholder")
        if "moyo_password" in self.database_url or "root_password" in self.database_url:
            raise ValueError("production DATABASE_URL still uses a development password")
        if self.semantic_recall_enabled:
            if not self.resolved_embedding_api_key:
                raise ValueError("semantic recall requires EMBEDDING_API_KEY or MODEL_API_KEY")
            if not self.qdrant_api_key:
                raise ValueError("semantic recall requires QDRANT_API_KEY in production")
        if self.rerank_enabled and not self.resolved_rerank_api_key:
            raise ValueError("reranking requires RERANK_API_KEY or MODEL_API_KEY")
        return self

    @property
    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.model_api_key

    @property
    def resolved_rerank_api_key(self) -> str:
        return self.rerank_api_key or self.model_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

