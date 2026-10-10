import os
import json
import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from app.config import settings

logger = logging.getLogger("llm_factory")

SETTINGS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "settings.json"))


def get_dynamic_settings() -> dict:
    """Reads dynamic settings from settings.json if present, falling back to app config."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
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

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        res = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
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
        res = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
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

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            if hasattr(chunk, "message") and isinstance(chunk.message.content, list):
                texts = [
                    b.get("text", "")
                    for b in chunk.message.content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    chunk.message.content = "".join(texts)
            yield chunk


def get_llm(model: str = None, **kwargs):
    """Returns a Vertex AI Gemini LLM instance via ``langchain-google-genai``.

    Uses Application Default Credentials (ADC) — no API keys needed.
    """
    actual_model = model or get_dynamic_settings().get("model") or settings.llm_model

    # Apply timeout from settings if not explicitly provided
    if "request_timeout" not in kwargs and "timeout" not in kwargs:
        kwargs["request_timeout"] = settings.llm_timeout

    logger.debug(
        f"Creating Vertex AI LLM: model={actual_model}, "
        f"project={settings.vertex_project_id}, location={settings.vertex_location}"
    )
    return _NormalizedChatGoogleGenerativeAI(
        model=actual_model,
        vertexai=True,
        project=settings.vertex_project_id,
        location=settings.vertex_location,
        **kwargs,
    )
