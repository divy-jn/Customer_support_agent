"""Build provider-specific chat models and track observed provider health."""
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_google_genai import ChatGoogleGenerativeAI
from app.config import settings

logger = logging.getLogger("llm_factory")

SETTINGS_FILE = settings.llm_settings_path
_HEALTH_TTL = timedelta(minutes=5)
_provider_success: dict[tuple[str, str | None, str | None], str] = {}
_bedrock_success: dict[tuple[str, str | None], str] = {}
MODEL_CHOICES = {
    "bedrock": ("deepseek.v3.2", "qwen.qwen3-next-80b-a3b", "mistral.ministral-3-14b-instruct"),
    "vertex": ("gemini-2.5-flash", "gemini-3.8-flash"),
}


def resolve_model(model: str | None = None) -> str:
    if model:
        return model
    configured = get_dynamic_settings().get("model")
    return (configured.strip() if isinstance(configured, str) else "") or settings.llm_model


def model_for_telemetry(llm) -> str:
    model = getattr(llm, "model_id", None) or getattr(llm, "model", None)
    return model.removeprefix("models/") if isinstance(model, str) else resolve_model()


def last_provider_success(model: str, project: str | None, location: str | None) -> str | None:
    observed_at = _provider_success.get((model.removeprefix("models/"), project, location))
    return _recent_success(observed_at)


def last_bedrock_success(model: str, region: str) -> str | None:
    return _recent_success(_bedrock_success.get((model, region)))


def _recent_success(observed_at: str | None) -> str | None:
    if observed_at and datetime.now(timezone.utc) - datetime.fromisoformat(observed_at) < _HEALTH_TTL:
        return observed_at
    return None


def get_dynamic_settings() -> dict:
    """Reads dynamic settings from settings.json if present, falling back to app config."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("provider", settings.llm_provider) == settings.llm_provider:
                    model = data.get("model")
                    # Gemini-only files predate provider-scoped overrides.
                    legacy_gemini = (
                        settings.llm_provider == "bedrock"
                        and "provider" not in data
                        and isinstance(model, str)
                        and model.strip().startswith("gemini-")
                    )
                    if not legacy_gemini:
                        return data
        except Exception:
            pass  #review: silently swallows JSON parse errors — consider logging
    return {
        "model": settings.llm_model,
    }


class _NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """Ensures AIMessage.content is consistently a string for Gemini 3+ models.

    Gemini 3+ returns content as a list of typed blocks (text, signature, etc.)
    instead of a plain string. Every downstream agent parser in this codebase
    expects ``response.content`` to be ``str``, so we normalise here at the
    lowest layer.
    """

    def _record_provider_success(self) -> None:
        key = (self.model.removeprefix("models/"), self.project, self.location)
        _provider_success[key] = datetime.now(timezone.utc).isoformat()

    def _record_provider_failure(self) -> None:
        _provider_success.pop((self.model.removeprefix("models/"), self.project, self.location), None)

    # Gemini 3+ returns content as list of typed blocks; normalize to plain str
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            res = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception:
            self._record_provider_failure()
            raise
        self._record_provider_success()
        for gen in res.generations:
            if hasattr(gen, "message") and isinstance(gen.message.content, list):
                texts = [
                    b.get("text", "")
                    for b in gen.message.content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    gen.message.content = "".join(texts)
        return res

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            res = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception:
            self._record_provider_failure()
            raise
        self._record_provider_success()
        for gen in res.generations:
            if hasattr(gen, "message") and isinstance(gen.message.content, list):
                texts = [
                    b.get("text", "")
                    for b in gen.message.content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    gen.message.content = "".join(texts)
        return res

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        observed_response = False
        try:
            for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                if not observed_response:
                    self._record_provider_success()
                    observed_response = True
                yield chunk
        except Exception:
            self._record_provider_failure()
            raise

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        observed_response = False
        try:
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                if not observed_response:
                    self._record_provider_success()
                    observed_response = True
                if hasattr(chunk, "message") and isinstance(chunk.message.content, list):
                    texts = [
                        b.get("text", "")
                        for b in chunk.message.content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if texts:
                        chunk.message.content = "".join(texts)
                yield chunk
        except Exception:
            self._record_provider_failure()
            raise


def _normalize_bedrock_text(message) -> None:
    if isinstance(message.content, list):
        message.content = "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in message.content
            if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")
        )


class _ObservedBedrockConverse(ChatBedrockConverse):
    """Observe network calls and keep text responses compatible with the agents."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        key = (self.model_id, self.region_name)
        try:
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception:
            _bedrock_success.pop(key, None)
            raise
        _bedrock_success[key] = datetime.now(timezone.utc).isoformat()
        for generation in result.generations:
            _normalize_bedrock_text(generation.message)
        return result

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        key = (self.model_id, self.region_name)
        observed = False
        try:
            for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                if not observed:
                    _bedrock_success[key] = datetime.now(timezone.utc).isoformat()
                    observed = True
                yield chunk
        except Exception:
            _bedrock_success.pop(key, None)
            raise


def get_llm(model: str | None = None, **kwargs):
    """Build the selected provider with its effective model and request timeout."""
    actual_model = resolve_model(model)
    if settings.llm_provider == "bedrock":
        timeout = kwargs.pop("timeout", None)
        timeout = kwargs.pop("request_timeout", None) or timeout or settings.llm_timeout
        kwargs.setdefault("config", Config(
            connect_timeout=min(timeout, 10),
            read_timeout=timeout,
            retries={"total_max_attempts": 1},
        ))
        return _ObservedBedrockConverse(
            model=actual_model,
            region_name=settings.aws_region,
            **kwargs,
        )

    if "request_timeout" not in kwargs and "timeout" not in kwargs:
        kwargs["request_timeout"] = settings.llm_timeout

    return _NormalizedChatGoogleGenerativeAI(
        model=actual_model,
        vertexai=True,
        project=settings.vertex_project_id,
        location=settings.vertex_location,
        **kwargs,
    )
