"""
Application configuration loaded from environment variables.
"""
import os
import re
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, model_validator
from pathlib import Path
from urllib.parse import quote

# backend/ directory — root for resolving relative paths like knowledge_base
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Explicitly distinguishes development from non-debug hosted environments.
    app_environment: str = Field(default="development", alias="APP_ENVIRONMENT")

    # --- Database (Supabase) ---
    supabase_url: str = Field(..., alias="SUPABASE_URL")       # required, validated below
    supabase_key: str = Field(..., alias="SUPABASE_KEY")       # required, min 10 chars
    database_password: str = Field(default="", alias="DATABASE_PASSWORD")  # falls back to supabase_key

    # --- LLM (Vertex AI) ---
    llm_model: str = Field(default="gemini-3.8-flash", alias="LLM_MODEL")
    llm_timeout: int = Field(default=120, alias="LLM_TIMEOUT")  # seconds
    vertex_project_id: str = Field(default="bytebazaar-dj", alias="VERTEX_PROJECT_ID")
    vertex_location: str = Field(default="global", alias="VERTEX_LOCATION")

    # --- Pinecone ---
    pinecone_api_key: str = Field(default="", alias="PINECONE_API_KEY")
    pinecone_index_name: str = Field(default="knowledge-base", alias="PINECONE_INDEX_NAME")

    # --- Security & Auth ---
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")
    agent_secret: str = Field(default="", alias="AGENT_SECRET")
    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expiration_minutes: int = Field(default=1440, alias="JWT_EXPIRATION_MINUTES")
    approval_signing_secret: str = Field(default="", alias="APPROVAL_SIGNING_SECRET")

    # --- Redis ---
    upstash_redis_url: str = Field(default="", alias="UPSTASH_REDIS_URL")
    upstash_redis_token: str = Field(default="", alias="UPSTASH_REDIS_TOKEN")

    # --- MCP Servers ---
    mcp_db_port: int = 8001
    mcp_fs_port: int = 8002
    mcp_web_port: int = 8003

    # --- FastAPI ---
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8002, alias="API_PORT")  #review: conflicts with mcp_fs_port=8002 above

    # --- CORS ---
    cors_origins: str = Field(
        default="http://localhost:3003,http://127.0.0.1:3003,http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
        alias="CORS_ORIGINS",
    )

    # --- Knowledge Base ---
    knowledge_base_dir: str = "./knowledge_base"

    # --- Outbox Worker (Slice 3.3 / Prompt 12) ---
    outbox_worker_enabled: bool = Field(default=True, alias="OUTBOX_WORKER_ENABLED")
    outbox_worker_mode: str = Field(default="in_process", alias="OUTBOX_WORKER_MODE")
    outbox_poll_interval_seconds: float = Field(default=5.0, alias="OUTBOX_POLL_INTERVAL_SECONDS")
    outbox_batch_size: int = Field(default=10, alias="OUTBOX_BATCH_SIZE")
    outbox_stale_threshold_seconds: float = Field(default=300.0, alias="OUTBOX_STALE_THRESHOLD_SECONDS")
    outbox_recovery_interval_seconds: float = Field(default=60.0, alias="OUTBOX_RECOVERY_INTERVAL_SECONDS")
    outbox_max_attempts: int = Field(default=5, alias="OUTBOX_MAX_ATTEMPTS")

    # --- Outbox Reconciliation (Slice 3.7.4) ---
    reconciliation_enabled: bool = Field(default=True, alias="RECONCILIATION_ENABLED")
    reconciliation_interval_seconds: int = Field(default=3600, alias="RECONCILIATION_INTERVAL_SECONDS")
    reconciliation_startup_pages: int = Field(default=5, alias="RECONCILIATION_STARTUP_PAGES")
    reconciliation_horizon_days: float = Field(default=7.0, alias="RECONCILIATION_HORIZON_DAYS")
    reconciliation_batch_size: int = Field(default=100, alias="RECONCILIATION_BATCH_SIZE")

    # --- Email ---
    gmail_address: str = Field(default="jdivy7151@gmail.com", alias="GMAIL_ADDRESS")  #review: hardcoded personal email in defaults
    gmail_app_password: str = Field(default="", alias="GMAIL_APP_PASSWORD")
    support_team_email: str = Field(default="support@supportteam.com", alias="SUPPORT_TEAM_EMAIL")
    tech_team_email: str = Field(default="tech@supportteam.com", alias="TECH_TEAM_EMAIL")
    from_email: str = Field(default="jdivy7151@gmail.com", alias="FROM_EMAIL")  #review: same hardcoded email
    email_test_recipient: str | None = Field(default="pdfallen0@gmail.com", alias="EMAIL_TEST_RECIPIENT")  #review: test recipient should be None in prod

    # --- Logging & Debug ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug_mode: bool = Field(default=False, alias="DEBUG_MODE")

    # --- WebSocket ---
    ws_heartbeat_interval: int = Field(default=30, alias="WS_HEARTBEAT_INTERVAL")
    ws_max_connections_per_session: int = Field(default=5, alias="WS_MAX_CONNECTIONS_PER_SESSION")
    ws_message_rate_limit: int = Field(default=20, alias="WS_MESSAGE_RATE_LIMIT")  # messages per minute

    # --- LangGraph ---
    langgraph_timeout: int = Field(default=120, alias="LANGGRAPH_TIMEOUT")  # seconds
    agent_architecture_mode: str = Field(default="legacy", alias="AGENT_ARCHITECTURE_MODE")

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
        if not self.database_password:
            raise ValueError("DATABASE_PASSWORD is required for direct PostgreSQL connections; SUPABASE_KEY is not a database password")
        host = f"db.{self._supabase_host}.supabase.co"
        password = quote(self.database_password, safe="")
        return f"postgresql+asyncpg://postgres:{password}@{host}:5432/postgres"

    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection string for SQLAlchemy (psycopg2)."""
        if not self.database_password:
            raise ValueError("DATABASE_PASSWORD is required for direct PostgreSQL connections; SUPABASE_KEY is not a database password")
        host = f"db.{self._supabase_host}.supabase.co"
        password = quote(self.database_password, safe="")
        return f"postgresql+psycopg2://postgres:{password}@{host}:5432/postgres"

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

    @field_validator("agent_architecture_mode")
    @classmethod
    def validate_agent_architecture_mode(cls, v: str) -> str:
        """Keep architecture rollouts explicit and safely reversible."""
        valid = {"legacy", "shadow", "v2_product", "v2_product_order", "v2_core", "v2_all"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(
                f"AGENT_ARCHITECTURE_MODE must be one of {sorted(valid)}"
            )
        return normalized

    @field_validator("outbox_worker_mode")
    @classmethod
    def validate_outbox_worker_mode(cls, v: str) -> str:
        """Validate outbox execution mode: in_process, standalone, or disabled."""
        valid = {"in_process", "standalone", "disabled"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(f"OUTBOX_WORKER_MODE must be one of {sorted(valid)}")
        return normalized

    @field_validator("app_environment")
    @classmethod
    def normalize_app_environment(cls, v: str) -> str:
        normalized = v.strip().lower()
        valid = {"development", "test", "staging", "production"}
        if normalized not in valid:
            raise ValueError(f"APP_ENVIRONMENT must be one of {sorted(valid)}")
        return normalized

    @field_validator("jwt_secret")
    @classmethod
    def normalize_jwt_secret(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def validate_auth_security_settings(self) -> "Settings":
        """Allow an explicit development key only in debug mode; require a strong key otherwise."""
        if self.app_environment in {"staging", "production"} and self.debug_mode:
            raise ValueError("DEBUG_MODE must be false when APP_ENVIRONMENT is staging or production")

        if self.app_environment == "production" and not self.database_password:
            raise ValueError("DATABASE_PASSWORD must be configured when APP_ENVIRONMENT=production")

        if not self.jwt_secret:
            if self.debug_mode:
                self.jwt_secret = "dev-secret-key"
            else:
                raise ValueError("JWT_SECRET must be set unless DEBUG_MODE=true")

        if not self.debug_mode:
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters outside DEBUG_MODE")
            lowered_secret = self.jwt_secret.lower()
            if lowered_secret in {"dev-secret-key", "your-secure-jwt-secret-key-change-me"} or "change-me" in lowered_secret:
                raise ValueError("JWT_SECRET must not use a known development/example placeholder")

            # Static bearer-style secrets are optional when agents use JWTs, but
            # any configured value must not be a guessable demo credential.
            for name, secret in (
                ("AGENT_SECRET", self.agent_secret),
                ("ADMIN_API_KEY", self.admin_api_key),
            ):
                if secret and (
                    len(secret) < 32
                    or secret.lower() in {"test-agent-secret", "admin", "changeme"}
                    or "replace-with" in secret.lower()
                    or "change-me" in secret.lower()
                ):
                    raise ValueError(
                        f"{name} must be at least 32 characters and must not use a demo/example value outside DEBUG_MODE"
                    )
        return self

    model_config = {
        "env_file": str(_BACKEND_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
