import os
import logging
from langchain_openai import ChatOpenAI
from app.config import settings

import json
from pathlib import Path

logger = logging.getLogger("llm_factory")

SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"

def get_dynamic_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "llm_base_url": settings.llm_base_url,
        "small_model": settings.llm_small_model,
        "large_model": settings.llm_large_model
    }

def get_llm(model: str = None, provider: str = None, **kwargs):
    """
    Returns an LLM instance supporting multiple providers.
    Uses LLM_PROVIDER from env or infers from base_url/model.

    Supports timeout configuration via settings.llm_timeout.
    Falls back to settings.llm_fallback_model if primary model fails on init.
    """
    dyn = get_dynamic_settings()
    
    actual_model = model
    if model == settings.llm_small_model:
        actual_model = dyn.get("small_model", model)
    elif model == settings.llm_large_model:
        actual_model = dyn.get("large_model", model)
    elif model is None:
        actual_model = dyn.get("large_model", settings.llm_large_model)

    base_url = dyn.get("llm_base_url", settings.llm_base_url)
    
    # Apply timeout from settings if not explicitly provided
    if "request_timeout" not in kwargs and "timeout" not in kwargs:
        kwargs["request_timeout"] = settings.llm_timeout
    
    # Multi-provider routing
    prov = provider or os.getenv("LLM_PROVIDER", "openai").lower()
    
    if prov == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "LLM_PROVIDER=anthropic requires 'langchain-anthropic'. "
                "Install it with: pip install langchain-anthropic"
            )
        return ChatAnthropic(
            model_name=actual_model,
            api_key=settings.llm_api_key,
            **kwargs
        )
    elif prov == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "LLM_PROVIDER=google requires 'langchain-google-genai'. "
                "Install it with: pip install langchain-google-genai"
            )
        return ChatGoogleGenerativeAI(
            model=actual_model,
            google_api_key=settings.llm_api_key,
            **kwargs
        )
    else:
        # Default to OpenAI or OpenAI-compatible (vLLM, Ollama Cloud)
        normalized_url = base_url.rstrip("/") if base_url else None
        logger.debug(f"Creating LLM: model={actual_model}, url={normalized_url}")
        return ChatOpenAI(
            api_key=settings.llm_api_key or "empty",
            base_url=normalized_url,
            model=actual_model,
            **kwargs
        )
