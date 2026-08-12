"""
Application configuration loaded from environment variables.
"""
import os
import re
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from pathlib import Path

# backend/ directory — the project root for all relative paths
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # --- Database ---
    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_key: str = Field(..., alias="SUPABASE_KEY")
    database_password: str = Field(default="", alias="DATABASE_PASSWORD")

    # --- Cloud LLM ---
    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/", alias="LLM_BASE_URL")
    llm_small_model: str = Field(default="gpt-oss:20b-cloud", alias="LLM_SMALL_MODEL")
    llm_large_model: str = Field(default="gpt-oss:120b-cloud", alias="LLM_LARGE_MODEL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")

    # --- Pinecone ---
    pinecone_api_key: str = Field(default="", alias="PINECONE_API_KEY")
    pinecone_index_name: str = Field(default="knowledge-base", alias="PINECONE_INDEX_NAME")
    embedding_model: str = Field(default="all-MiniLM-L6-v2", alias="EMBEDDING_MODEL")

    # --- Redis ---
    upstash_redis_url: str = Field(default="", alias="UPSTASH_REDIS_URL")
    upstash_redis_token: str = Field(default="", alias="UPSTASH_REDIS_TOKEN")

    # --- MCP Servers ---
    mcp_db_port: int = 8001
    mcp_fs_port: int = 8002
    mcp_web_port: int = 8003

    # --- FastAPI ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- CORS ---
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )

    # --- Knowledge Base ---
    knowledge_base_dir: str = "./knowledge_base"

    # --- Email ---
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    support_team_email: str = Field(default="support@projectbestie.com", alias="SUPPORT_TEAM_EMAIL")
    tech_team_email: str = Field(default="tech@projectbestie.com", alias="TECH_TEAM_EMAIL")
    from_email: str = Field(default="noreply@projectbestie.com", alias="FROM_EMAIL")

    # --- Logging & Debug ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug_mode: bool = Field(default=False, alias="DEBUG_MODE")

    # --- WebSocket ---
    ws_heartbeat_interval: int = Field(default=30, alias="WS_HEARTBEAT_INTERVAL")
    ws_max_connections_per_session: int = Field(default=5, alias="WS_MAX_CONNECTIONS_PER_SESSION")
    ws_message_rate_limit: int = Field(default=20, alias="WS_MESSAGE_RATE_LIMIT")  # messages per minute

    # --- LangGraph ---
    langgraph_timeout: int = Field(default=60, alias="LANGGRAPH_TIMEOUT")  # seconds

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def knowledge_base_path(self) -> Path:
        """Resolve knowledge_base_dir relative to backend/ root."""
        p = Path(self.knowledge_base_dir)
        return p if p.is_absolute() else (_BACKEND_ROOT / p).resolve()

    @property
    def _supabase_host(self) -> str:
        """Extract the host portion from supabase_url (e.g. tesfafbkhbleipxbpcpk)."""
        match = re.search(r"https://([^.]+)\.supabase\.co", self.supabase_url)
        return match.group(1) if match else "localhost"

    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection string for SQLAlchemy (asyncpg)."""
        host = f"db.{self._supabase_host}.supabase.co"
        pw = self.database_password or self.supabase_key
        return f"postgresql+asyncpg://postgres:{pw}@{host}:5432/postgres"

    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection string for SQLAlchemy (psycopg2)."""
        host = f"db.{self._supabase_host}.supabase.co"
        pw = self.database_password or self.supabase_key
        return f"postgresql+psycopg2://postgres:{pw}@{host}:5432/postgres"

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, v):
        if not v or not v.startswith("http"):
            raise ValueError("SUPABASE_URL must be a valid URL starting with http(s)://")
        return v

    @field_validator("supabase_key")
    @classmethod
    def validate_supabase_key(cls, v):
        if not v or len(v) < 10:
            raise ValueError("SUPABASE_KEY must be a non-empty API key")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v):
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}")
        return v.upper()

    model_config = {
        "env_file": str(_BACKEND_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
