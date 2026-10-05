"""Validated process configuration shared by HTTP, CLI, and composition roots."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Positive = Annotated[int, Field(gt=0)]
BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"
DEVELOPMENT_OWNER = "00000000-0000-4000-8000-000000000001"


class Settings(BaseModel):
    """Validate configuration without mutating the environment or logging secrets.

    ``load`` merges defaults, a dotenv file, exported values, and explicit
    overrides in that order. Secrets are intentionally absent from run config.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)

    dr4a_env: Literal["development", "production"] = "development"
    dr4a_auth_required: bool = False
    database_url: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    bocha_api_key: SecretStr = SecretStr("")
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    llm_model: str = "deepseek-flash"
    llm_revision: str = "unconfigured"
    llm_local: bool = False
    milvus_uri: str = "http://localhost:19530"
    minio_endpoint: str = "localhost:9000"
    minio_secure: bool = False
    minio_bucket: str = "deepresearch"
    bge_m3_model_path: str = "BAAI/bge-m3"
    bge_reranker_model_path: str = "BAAI/bge-reranker-v2-m3"
    embedding_revision: str = "unconfigured"
    reranker_revision: str = "unconfigured"
    parser_version: str = "unconfigured"
    chunker_version: str = "mono-v1"
    index_version: str = "dr4a_chunks_v1"
    cors_allow_origins: list[str] = Field(default_factory=list)
    run_deadline_s: Positive = 1800
    run_search_calls: Positive = 60
    run_fetch_calls: Positive = 30
    run_llm_calls: Positive = 60
    run_tokens: Positive = 120000
    run_terminal_reserved_calls: Positive = 8
    run_terminal_reserved_tokens: Positive = 12000
    run_rework_rounds: Annotated[int, Field(ge=0, le=3)] = 3
    citation_depth: Annotated[int, Field(ge=0, le=2)] = 2
    gap_queries_per_spec: Annotated[int, Field(ge=0, le=2)] = 2
    clarify_rounds: Annotated[int, Field(ge=0, le=3)] = 3
    llm_timeout_s: Positive = 60
    search_timeout_s: Positive = 20
    fetch_timeout_s: Positive = 45
    embedding_timeout_s: Positive = 120
    rerank_timeout_s: Positive = 60
    vector_timeout_s: Positive = 15
    content_timeout_s: Positive = 30
    parser_timeout_s: Positive = 600
    sandbox_timeout_s: Positive = 30
    search_concurrency: Positive = 4
    fetch_concurrency: Positive = 2
    llm_concurrency: Positive = 2
    local_inference_concurrency: Positive = 1
    embedding_batch_size: Positive = 16
    pipeline_concurrency: Positive = 2
    owner_run_concurrency: Positive = 1
    owner_queue_limit: Positive = 20
    queue_timeout_s: Positive = 1800
    ingestion_concurrency: Positive = 2
    kb_ingestion_concurrency: Positive = 1
    job_deadline_s: Positive = 1200
    job_max_attempts: Positive = 3
    lease_s: Positive = 90
    heartbeat_s: Positive = 20
    scan_s: Positive = 5
    shutdown_s: Positive = 30
    sse_heartbeat_s: Positive = 15
    sse_queue_size: Positive = 256
    upload_max_bytes: Positive = 50 * 1024 * 1024
    parser_max_pages: Positive = 500
    parser_max_chunks: Positive = 10000
    sandbox_backend: Literal["docker", "isolated_worker"] = "docker"

    @classmethod
    def load(
        cls,
        *,
        env_file: Path = BACKEND_ENV,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> Settings:
        """Load only recognized environment names and validate explicit overrides."""
        exported = os.environ if environ is None else environ
        file_values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
        values: dict[str, object] = {}
        for source in (file_values, exported):
            for key, value in source.items():
                name = key.lower()
                if name in cls.model_fields and value is not None:
                    values[name] = value
        for key, value in (overrides or {}).items():
            values[key.lower()] = value
        if values.get("dr4a_env") == "production":
            values.setdefault("dr4a_auth_required", True)
            values.setdefault("sandbox_backend", "isolated_worker")
        return cls.model_validate(values)

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        """Accept JSON lists or comma-separated dotenv origin lists."""
        if isinstance(value, str):
            if value.strip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_allow_origins")
    @classmethod
    def validate_origins(cls, value: list[str]) -> list[str]:
        """Reject wildcard credentials and non-origin URLs."""
        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
        return list(dict.fromkeys(value))

    @field_validator("anthropic_base_url", "milvus_uri")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        """Keep embedded credentials and query secrets out of public configuration."""
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("service URL must be HTTP(S) without embedded credentials")
        return value.rstrip("/")

    @field_validator(
        "llm_model", "llm_revision", "embedding_revision", "reranker_revision",
        "parser_version", "chunker_version", "index_version", "minio_bucket",
        "bge_m3_model_path", "bge_reranker_model_path",
    )
    @classmethod
    def nonempty(cls, value: str) -> str:
        """Reject blank identifiers rather than silently using a different profile."""
        if not value.strip():
            raise ValueError("configuration identifier cannot be empty")
        return value.strip()

    @field_validator("minio_endpoint")
    @classmethod
    def validate_object_endpoint(cls, value: str) -> str:
        """Use a host:port endpoint; TLS is a separate explicit setting."""
        parsed = urlsplit("//" + value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.path:
            raise ValueError("MinIO endpoint must be a host and optional port")
        if parsed.query or parsed.fragment or "://" in value:
            raise ValueError("MinIO endpoint cannot include a scheme or query")
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> Settings:
        """Fail closed in production and reject inconsistent budgets or leases."""
        if self.run_terminal_reserved_calls >= self.run_llm_calls:
            raise ValueError("LLM budget must exceed the terminal reserve")
        if self.run_terminal_reserved_tokens >= self.run_tokens:
            raise ValueError("token budget must exceed the terminal reserve")
        if self.heartbeat_s >= self.lease_s or self.scan_s >= self.lease_s:
            raise ValueError("heartbeat and scan intervals must be shorter than the lease")
        if self.dr4a_env == "production":
            if not self.dr4a_auth_required:
                raise ValueError("production requires authentication")
            required = (
                "database_url", "jwt_secret", "minio_access_key", "minio_secret_key",
            )
            for name in required:
                value = getattr(self, name).get_secret_value()
                if not value or value.lower() in {"change-me", "your-deepseek-api-key"}:
                    raise ValueError(f"production requires configured {name}")
            if len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("production JWT secret must have at least 32 characters")
            if not self.llm_local and not self.anthropic_api_key.get_secret_value():
                raise ValueError("remote LLM requires configured credentials")
            if self.sandbox_backend != "isolated_worker":
                raise ValueError("production requires an isolated worker, not a Docker socket")
        return self

    def run_config_snapshot(
        self,
        *,
        categories: list[str] | None = None,
        knowledge_base_ids: list[str] | None = None,
        private_only: bool = False,
    ) -> dict:
        """Return fixed non-secret inputs; capability checks reject unconfigured versions."""
        operations = ("comparison_matrix", "pairwise_delta", "plot", "statistic", "aggregation")
        agents = ("clarify", "plan", "research", "analyze", "write", "review")
        return {
            "versions": {
                "llm_provider": "local" if self.llm_local else "anthropic_compatible",
                "llm_model": self.llm_model,
                "llm_revision": self.llm_revision,
                "prompt_versions": {agent: "mono-v1" for agent in agents},
                "template_versions": {operation: "mono-v1" for operation in operations},
                "parser_version": self.parser_version,
                "chunker_version": self.chunker_version,
                "embedding_version": self.embedding_revision,
                "reranker_version": self.reranker_revision,
                "index_version": self.index_version,
            },
            "source_policy": {
                "categories": categories if categories is not None else ["papers", "web"],
                "knowledge_base_ids": knowledge_base_ids or [],
                "private_only": private_only,
            },
            "limits": {
                "deadline_s": self.run_deadline_s,
                "search_calls": self.run_search_calls,
                "fetch_calls": self.run_fetch_calls,
                "llm_calls": self.run_llm_calls,
                "tokens": self.run_tokens,
                "terminal_reserved_calls": self.run_terminal_reserved_calls,
                "terminal_reserved_tokens": self.run_terminal_reserved_tokens,
                "rework_rounds": self.run_rework_rounds,
                "citation_depth": self.citation_depth,
                "gap_queries_per_spec": self.gap_queries_per_spec,
            },
            "timeouts_s": {
                name: getattr(self, f"{name}_timeout_s")
                for name in (
                    "llm", "search", "fetch", "embedding", "rerank", "content", "parser",
                    "sandbox",
                )
            } | {"vector": self.vector_timeout_s},
            "concurrency": {
                "search": self.search_concurrency,
                "fetch": self.fetch_concurrency,
                "llm": self.llm_concurrency,
                "local_inference": self.local_inference_concurrency,
            },
        }
